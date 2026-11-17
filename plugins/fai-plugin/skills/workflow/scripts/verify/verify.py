#!/usr/bin/env python3
"""verify.py — orchestrator for the workflow verify sub-skill.

Runs pass 0 (facts), pass 1 (the existing structural validator), then passes
2 through 6, then the roll-up. Each pass is a subprocess: one that crashes
becomes a PASS_CRASHED blocker naming the pass and its stderr tail, and the
remaining passes still run. A verify run that dies because one detector has a
bug is worse than one that reports the bug.

    python3 verify.py <workflow.json> [--out DIR] [--registry PATH]
                      [--skip PASS[,PASS]] [--only PASS[,PASS]]
                      [--escalate] [--no-screenshot] [--json] [--self-test]

Exit codes follow the CONTRACT: 0 ran clean, 1 blockers found, 2 the
orchestrator itself failed.

Everything the passes cannot do is a note rather than an error — no registry
directory, no Chrome, no network. See `skipped` in the roll-up: "could not
check" must never read as "clean".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    PassOutput,
    finding,
    truncate,
)

#: Pass id -> (script, kind). "structural" is ../validate_workflow.py and is
#: special-cased: it predates this sub-skill, takes a workflow rather than
#: facts, and prints text rather than writing a pass payload.
PASSES: List[Tuple[str, str]] = [
    ("facts", "wf_facts.py"),
    ("structural", "../validate_workflow.py"),
    ("flow", "flow_check.py"),
    ("paths", "paths.py"),
    ("modules", "registry_match.py"),
    ("html", "html_probe.py"),
    ("simplify", "redundancy.py"),
]
PASS_IDS = [p for p, _ in PASSES]

#: Passes that read facts.json. Everything except pass 0 and pass 1.
FACTS_CONSUMERS = {"flow", "paths", "modules", "html", "simplify"}

STRUCT_STEP_RE = re.compile(r"^Step '([^']+)':\s*(.*)$", re.S)


def die(msg: str) -> "None":
    sys.stderr.write("verify: %s\n" % msg)
    raise SystemExit(2)


def run(cmd: List[str], timeout: int) -> Tuple[int, str, str, float]:
    """Run a pass. Never raises for a non-zero exit; a timeout is rc 124."""
    start = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(HERE),
        )
        return proc.returncode, proc.stdout, proc.stderr, time.time() - start
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ds" % timeout, time.time() - start
    except OSError as exc:
        return 2, "", str(exc), time.time() - start


# --------------------------------------------------------------------- pass 1

def parse_structural(stdout: str) -> Tuple[List[dict], int, int]:
    """Turn the structural validator's text into findings.

    Its format, stable since this sub-skill was written:

        ⚠  N warning(s):
           - <text>
        ✗  N error(s):
           - <text>
        ✓  Valid workflow: '<name>' with N steps, 0 errors

    Every error is a blocker: the platform's own save validator rejects the
    workflow, so nothing downstream matters until it is fixed.
    """
    findings: List[dict] = []
    severity: Optional[str] = None
    n_err = n_warn = 0

    for raw in stdout.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if "error(s)" in line and line.lstrip().startswith(("✗", "x", "X")):
            severity = "blocker"
            continue
        if "warning(s)" in line and line.lstrip().startswith(("⚠", "!")):
            severity = "warning"
            continue
        if line.lstrip().startswith("✓"):
            severity = None
            continue
        stripped = line.strip()
        if not stripped.startswith("- ") or severity is None:
            continue
        text = stripped[2:].strip()
        step = None
        match = STRUCT_STEP_RE.match(text)
        if match:
            step, text = match.group(1), match.group(2).strip()
        if severity == "blocker":
            n_err += 1
            title = "Structural error — the platform will reject this"
            fix = ("Fix before anything else: the platform's save validator "
                   "rejects this workflow, so no other finding can be trusted.")
        else:
            n_warn += 1
            title = "Structural warning"
            fix = "See reference/validation_errors.md for the fix."
        findings.append(finding(
            "STRUCTURAL_ERROR" if severity == "blocker" else "STRUCTURAL_WARNING",
            severity, title, text, step=step, fix=fix,
            group_key="STRUCTURAL:%s" % (step or "workflow"),
        ))
    return findings, n_err, n_warn


def write_structural(out_dir: Path, stdout: str, stderr: str, rc: int,
                     secs: float) -> Tuple[int, int]:
    """Wrap pass 1 into a normal pass payload so the roll-up consumes it."""
    findings, n_err, n_warn = parse_structural(stdout)

    if rc not in (0, 1) and not findings:
        # The validator itself broke. Say so rather than reporting clean.
        findings.append(finding(
            "PASS_CRASHED", "blocker",
            "The structural validator failed to run",
            "validate_workflow.py exited %d: %s"
            % (rc, truncate((stderr or stdout).strip(), 400)),
            fix="Run it directly on the workflow to see the failure.",
        ))
        n_err += 1

    out = PassOutput("structural", str(out_dir))
    out.add_all(findings)
    out.set("validator_exit", rc)
    out.set("elapsed_seconds", round(secs, 3))
    out.write()
    return n_err, n_warn


# ------------------------------------------------------------------ the run

def build_cmd(pass_id: str, script: str, workflow: Path, out_dir: Path,
              args: argparse.Namespace) -> List[str]:
    facts = out_dir / "facts.json"
    if pass_id == "facts":
        return [sys.executable, script, str(workflow), "--out", str(out_dir)]
    if pass_id == "structural":
        return [sys.executable, script, str(workflow)]
    cmd = [sys.executable, script, "--facts", str(facts), "--out", str(out_dir)]
    if pass_id == "modules" and args.registry:
        cmd += ["--registry", str(args.registry)]
    if pass_id == "html":
        if args.escalate:
            cmd.append("--escalate")
        if args.no_screenshot:
            cmd.append("--no-screenshot")
    return cmd


def orchestrate(workflow: Path, out_dir: Path, args: argparse.Namespace) -> dict:
    selected = set(PASS_IDS)
    if args.only:
        want = {p.strip() for p in args.only.split(",") if p.strip()}
        unknown = want - set(PASS_IDS)
        if unknown:
            die("unknown pass(es) in --only: %s (known: %s)"
                % (", ".join(sorted(unknown)), ", ".join(PASS_IDS)))
        # facts is not optional: every other pass reads it.
        selected = want | ({"facts"} if want & FACTS_CONSUMERS else set())
    if args.skip:
        drop = {p.strip() for p in args.skip.split(",") if p.strip()}
        unknown = drop - set(PASS_IDS)
        if unknown:
            die("unknown pass(es) in --skip: %s (known: %s)"
                % (", ".join(sorted(unknown)), ", ".join(PASS_IDS)))
        if "facts" in drop and drop != set(PASS_IDS):
            die("cannot --skip facts: every other pass reads facts.json")
        selected -= drop

    results: List[dict] = []
    skipped: List[dict] = []
    blockers = 0
    facts_ok = False
    total_start = time.time()

    for pass_id, script in PASSES:
        if pass_id not in selected:
            skipped.append({"check": pass_id, "scope": "pass",
                            "reason": "not selected on the command line"})
            continue
        if pass_id in FACTS_CONSUMERS and not facts_ok:
            skipped.append({"check": pass_id, "scope": "pass",
                            "reason": "facts.json was not produced, so this "
                                      "pass had nothing to read"})
            continue

        script_path = (HERE / script).resolve()
        if not script_path.is_file():
            skipped.append({"check": pass_id, "scope": "pass",
                            "reason": "%s is missing from the sub-skill" % script})
            continue

        cmd = build_cmd(pass_id, str(script_path), workflow, out_dir, args)
        rc, stdout, stderr, secs = run(cmd, args.timeout)

        if pass_id == "structural":
            n_err, n_warn = write_structural(out_dir, stdout, stderr, rc, secs)
            blockers += n_err
            results.append({"pass": pass_id, "exit": rc, "seconds": round(secs, 3),
                            "blockers": n_err, "warnings": n_warn})
            if not args.quiet:
                print("  %-11s %5.2fs  exit %d" % (pass_id, secs, rc))
            continue

        payload = out_dir / ("facts.json" if pass_id == "facts"
                             else "%s.json" % pass_id)

        if rc == 2 or (rc not in (0, 1) and not payload.is_file()):
            # The pass itself broke. Record it and keep going.
            out = PassOutput(pass_id, str(out_dir))
            out.add(finding(
                "PASS_CRASHED", "blocker",
                "The %s pass failed to run" % pass_id,
                "%s exited %d. stderr: %s"
                % (script, rc, truncate((stderr or stdout).strip(), 500) or "(empty)"),
                fix="Run it directly with the same --facts to reproduce.",
            ))
            out.write()
            blockers += 1
            results.append({"pass": pass_id, "exit": rc, "seconds": round(secs, 3),
                            "crashed": True})
            if not args.quiet:
                print("  %-11s %5.2fs  CRASHED exit %d" % (pass_id, secs, rc))
            continue

        if pass_id == "facts":
            facts_ok = payload.is_file()

        counts = {}
        if payload.is_file():
            try:
                doc = json.loads(payload.read_text())
                counts = doc.get("counts") or {}
                for entry in doc.get("skipped") or []:
                    skipped.append(entry)
            except (OSError, ValueError):
                counts = {}
        blockers += int(counts.get("blockers") or 0)
        results.append({"pass": pass_id, "exit": rc, "seconds": round(secs, 3),
                        **counts})
        if not args.quiet:
            print("  %-11s %5.2fs  exit %d" % (pass_id, secs, rc))

    return {"passes": results, "skipped": skipped, "blockers": blockers,
            "seconds": round(time.time() - total_start, 3)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify.py", description="Verify a workflow end to end.")
    ap.add_argument("workflow", nargs="?", help="path to workflow.json")
    ap.add_argument("--out", help="output directory (default: alongside the workflow)")
    ap.add_argument("--registry", help="module registry root "
                                       "(default: ~/fai/module-registry)")
    ap.add_argument("--skip", help="comma-separated passes to skip")
    ap.add_argument("--only", help="comma-separated passes to run")
    ap.add_argument("--escalate", action="store_true",
                    help="render the full HTML fixture matrix")
    ap.add_argument("--no-screenshot", action="store_true",
                    help="lint and render HTML but capture no images")
    ap.add_argument("--timeout", type=int, default=180,
                    help="per-pass timeout in seconds (default 180)")
    ap.add_argument("--json", action="store_true",
                    help="print the run summary as JSON instead of the report")
    ap.add_argument("--quiet", action="store_true", help="suppress per-pass lines")
    ap.add_argument("--self-test", action="store_true", help="run against the corpus")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test(args)
    if not args.workflow:
        die("a workflow.json path is required (or --self-test)")

    workflow = Path(args.workflow).expanduser()
    if not workflow.is_file():
        die("no such workflow: %s" % workflow)

    out_dir = Path(args.out).expanduser() if args.out else workflow.parent / "verify_out"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        die("cannot create --out %s: %s" % (out_dir, exc))

    if not args.quiet:
        print("verify: %s" % workflow.name)

    run_info = orchestrate(workflow, out_dir, args)

    # Roll-up. It reads whatever payloads exist, so a crashed pass still
    # appears rather than vanishing.
    report = HERE / "report.py"
    rc_report = 0
    if report.is_file():
        cmd = [sys.executable, str(report), "--out", str(out_dir)]
        if args.json:
            cmd.append("--json")
        rc_report, stdout, stderr, _ = run(cmd, args.timeout)
        if stdout:
            print(stdout, end="" if stdout.endswith("\n") else "\n")
        if rc_report == 2:
            sys.stderr.write("verify: the roll-up failed: %s\n"
                             % truncate((stderr or "").strip(), 400))
    else:
        sys.stderr.write("verify: report.py is missing; per-pass JSON is in %s\n"
                         % out_dir)

    if run_info["skipped"] and not args.quiet:
        print("SKIPPED  %d check(s) could not run — see the roll-up"
              % len(run_info["skipped"]))

    (out_dir / "run.json").write_text(
        json.dumps({"schema_version": 1, "workflow": str(workflow),
                    **run_info}, indent=2, sort_keys=True) + "\n")

    if rc_report == 2:
        return 2
    return 1 if run_info["blockers"] else 0


# ------------------------------------------------------------------ self-test

CORPUS = [
    "/Users/andrewjeffers/Documents/Work/contractors/workflows/cna_dua_audit_ads.json",
    "/Users/andrewjeffers/Documents/Work/contractors/workflows/cna_dua_audit_lpl.json",
    "/Users/andrewjeffers/Documents/Work/contractors/workflows/submission_intake.json",
    "/Users/andrewjeffers/Documents/Work/contractors/workflows/quantum_cny.json",
    str(HERE.parent.parent / "reference/examples/submission_intake_es_umbrella.json"),
]


def self_test(args: argparse.Namespace) -> int:
    failures: List[str] = []
    checks = 0

    def check(ok: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        print("  %s  %s" % ("ok " if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    tmp = Path(tempfile.mkdtemp(prefix="verify_selftest_"))
    try:
        for wf_path in CORPUS:
            wf = Path(wf_path)
            if not wf.is_file():
                check(False, "corpus workflow missing: %s" % wf.name)
                continue
            out = tmp / wf.stem
            out.mkdir(parents=True, exist_ok=True)
            start = time.time()
            rc = main([str(wf), "--out", str(out), "--quiet"])
            secs = time.time() - start

            check(rc in (0, 1), "%s: exit %d is 0 or 1" % (wf.stem, rc))
            check((out / "findings.json").is_file(),
                  "%s: findings.json written" % wf.stem)
            check((out / "report.html").is_file(),
                  "%s: report.html written" % wf.stem)
            check((out / "run.json").is_file(), "%s: run.json written" % wf.stem)
            check(secs < 60, "%s: finished in %.1fs (<60s)" % (wf.stem, secs))

            run_doc = json.loads((out / "run.json").read_text())
            crashed = [p["pass"] for p in run_doc["passes"] if p.get("crashed")]
            check(not crashed, "%s: no pass crashed%s"
                  % (wf.stem, (" (%s)" % ", ".join(crashed)) if crashed else ""))

            ran = {p["pass"] for p in run_doc["passes"]}
            check(ran == set(PASS_IDS),
                  "%s: all 7 passes ran (missing: %s)"
                  % (wf.stem, ", ".join(sorted(set(PASS_IDS) - ran)) or "none"))

            findings = json.loads((out / "findings.json").read_text())
            check(isinstance(findings, dict) and "findings" in findings,
                  "%s: findings.json has the expected shape" % wf.stem)

        # --only must be honored and the rest recorded as skipped, not clean.
        out = tmp / "subset"
        out.mkdir(parents=True, exist_ok=True)
        rc = main([CORPUS[1], "--out", str(out), "--quiet", "--only", "facts,flow"])
        check(rc in (0, 1), "partial --only run exits cleanly")
        run_doc = json.loads((out / "run.json").read_text())
        ran = {p["pass"] for p in run_doc["passes"]}
        check(ran == {"facts", "flow"},
              "--only honored (ran: %s)" % ", ".join(sorted(ran)))
        skipped_ids = {s["check"] for s in run_doc["skipped"]}
        check(skipped_ids >= {"structural", "paths", "modules", "html", "simplify"},
              "unselected passes recorded as skipped, not clean (%s)"
              % ", ".join(sorted(skipped_ids)))

        # A crashing pass must not take the run down: the others still run and
        # the crash becomes a blocker rather than an exception.
        out = tmp / "crash"
        out.mkdir(parents=True, exist_ok=True)
        broken = HERE / "_selftest_broken.py"
        real = HERE / "redundancy.py"
        backup = tmp / "redundancy.py.bak"
        try:
            shutil.copy2(str(real), str(backup))
            real.write_text("import sys\nsys.stderr.write('deliberate\\n')\n"
                            "sys.exit(2)\n")
            rc = main([CORPUS[1], "--out", str(out), "--quiet"])
            run_doc = json.loads((out / "run.json").read_text())
            crashed = [p["pass"] for p in run_doc["passes"] if p.get("crashed")]
            ran = {p["pass"] for p in run_doc["passes"]}
            check(crashed == ["simplify"],
                  "a crashing pass is recorded as crashed (%s)" % crashed)
            check("html" in ran and "paths" in ran,
                  "the other passes still ran after a crash")
            check(rc == 1, "a crashed pass makes the run exit 1, not 2")
            payload = out / "simplify.json"
            check(payload.is_file()
                  and "PASS_CRASHED" in payload.read_text(),
                  "the crash is written as a PASS_CRASHED blocker")
        finally:
            if backup.is_file():
                shutil.copy2(str(backup), str(real))
            if broken.is_file():
                broken.unlink()

        # Bad input is exit 2, never exit 1. die() raises SystemExit(2).
        try:
            main(["/nonexistent/workflow.json", "--out", str(tmp / "nope")])
            check(False, "a missing workflow exits 2 (it did not exit at all)")
        except SystemExit as exc:
            check(exc.code == 2,
                  "a missing workflow exits 2, got %r" % (exc.code,))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print("SELF-TEST FAILED — %d of %d checks" % (len(failures), checks))
        for f in failures:
            print("  - %s" % f)
        return 2
    print("SELF-TEST OK — %d checks" % checks)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.stderr.write("verify: interrupted\n")
        sys.exit(2)
