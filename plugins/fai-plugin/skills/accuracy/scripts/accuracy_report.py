#!/usr/bin/env python3
"""Build a field-level accuracy report for an Eval Studio Test Batch.

    read the platform's accuracy report -> reduce to rows -> render HTML

Read-only. It never generates a report, never triggers a test run, never edits
Ground Truth and never mutates a Test Batch. Report generation is a platform
write and stays the user's decision: when no usable report exists this exits
with instructions rather than creating one.

Two sources:

  --dataset / --workflow   PRIMARY. Reads the platform's own accuracy report:
      one summary call plus one call per Sample. Every accuracy percentage in
      the output comes from that payload, so the report agrees with the Eval
      Studio UI. Local scoring config does not apply.

  --from-dir GEN GT        OFFLINE fallback for two folders of JSON with no
      platform access. Scores locally with scoring.py; the numbers will differ
      slightly from the platform's on formatting-only differences.

    accuracy_report.py --account acme --dataset 665f... --out report.html
    accuracy_report.py --account acme --workflow "Intake" --check
    accuracy_report.py --from-dir ./generated ./ground_truth --out report.html

Run with -h for every flag. stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from fai_client import FaiClient, FaiError  # noqa: E402
from fai_accounts import AccountError, default_env, resolve_org_id, resolve_workflow_id  # noqa: E402

import studio_fetch  # noqa: E402
from studio_fetch import ReportNotReady  # noqa: E402
from aggregate import compute, compute_from_api  # noqa: E402
from scoring import ScoringConfig  # noqa: E402

OBJECT_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

# Flags that only mean something on the offline path.
OFFLINE_ONLY_FLAGS = ("config", "exclude_step", "exclude_field", "step_alias",
                      "identity_key", "synonyms", "union_keys")


def fail(message: str, code: int = 1):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


def note(message: str) -> None:
    print(message, file=sys.stderr)


def print_table(headers, rows) -> None:
    if not rows:
        print("(none)")
        return
    cells = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    for row in cells:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def fmt_pct(value) -> str:
    return "—" if value is None else f"{value:.1f}%"


# --------------------------------------------------------------------- config

def load_json_arg(value: str, label: str):
    """Accept inline JSON, @path, or a bare path."""
    text = value
    if value.startswith("@"):
        value = value[1:]
    if not value.lstrip().startswith(("{", "[")):
        path = Path(value).expanduser()
        if not path.exists():
            fail(f"{label}: file not found: {path}")
        text = path.read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        fail(f"{label} is not valid JSON: {e}")


def split_once(value: str, sep: str, label: str, flag: str):
    if sep not in value:
        fail(f"{flag} expects {label!r}, got {value!r}")
    left, _, right = value.rpartition(sep)
    left, right = left.strip(), right.strip()
    if not left or not right:
        fail(f"{flag} expects {label!r}, got {value!r}")
    return left, right


def build_config(args) -> ScoringConfig:
    data = {}
    if args.config:
        data = load_json_arg(args.config, "--config")
        if not isinstance(data, dict):
            fail("--config must be a JSON object of ScoringConfig keys")
    if args.synonyms:
        syn = load_json_arg(args.synonyms, "--synonyms")
        if not isinstance(syn, dict):
            fail('--synonyms must be a JSON object: {"path marker": {"from": "to"}}')
        merged = dict(data.get("synonyms") or {})
        merged.update(syn)
        data["synonyms"] = merged
    try:
        config = ScoringConfig.from_dict(data)
    except (ValueError, TypeError) as e:
        fail(f"--config: {e}")

    for step in args.exclude_step or []:
        config.exclude_steps.add(step)
    for spec in args.exclude_field or []:
        step, fld = split_once(spec, ":", "<step>:<field label>", "--exclude-field")
        config.exclude_fields.add((step, fld))
    for spec in args.step_alias or []:
        src, dst = split_once(spec, "=", "<from>=<to>", "--step-alias")
        config.step_aliases[src] = dst
    for spec in args.identity_key or []:
        scope, fld = split_once(spec, ":", "<step>:<field>", "--identity-key")
        config.identity_overrides[scope] = fld
    if args.union_keys:
        config.intersect_keys_only = False
    return config


# ------------------------------------------------------------ offline loading

def normalize_filename(name: str) -> str:
    s = name.lower()
    s = re.sub(r"_generated\.json$", "", s)
    s = re.sub(r"_gt(\s*\(\d+\))?\.json$", "", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def pair_files(gen_dir: Path, gt_dir: Path):
    """Pair one generated file to one ground-truth file by normalized stem,
    then by substring containment. Only generated files drive the loop."""
    gens = sorted(p for p in gen_dir.iterdir() if p.suffix == ".json")
    gts = sorted(p for p in gt_dir.iterdir() if p.suffix == ".json")
    gt_index = {normalize_filename(p.name): p for p in gts}
    pairs = []
    for gen in gens:
        gn = normalize_filename(gen.name)
        if gn in gt_index:
            pairs.append((gen, gt_index[gn]))
            continue
        found = next((t for tn, t in gt_index.items() if gn in tn or tn in gn), None)
        if found:
            pairs.append((gen, found))
        else:
            note(f"WARN: no Ground Truth pair for {gen.name}")
    return pairs


def load_from_dirs(gen_dir: str, gt_dir: str) -> List[dict]:
    gen_path, gt_path = Path(gen_dir).expanduser(), Path(gt_dir).expanduser()
    for p in (gen_path, gt_path):
        if not p.is_dir():
            fail(f"--from-dir: not a directory: {p}")
    samples = []
    for gen_file, gt_file in pair_files(gen_path, gt_path):
        gen_doc = json.loads(gen_file.read_text())
        gt_doc = json.loads(gt_file.read_text())
        # Display name comes from the generated filename, which by this
        # convention carries the readable label; the doc's own submission_name
        # is often a slug. Fall back to it when the filename yields nothing.
        name = re.sub(r"_generated\.json$", "", gen_file.name, flags=re.IGNORECASE)
        name = re.sub(r"\s+", " ", name.replace("_", " ").replace(".", " ")).strip()
        samples.append({
            "submission_id": gen_doc.get("submission_id", ""),
            "submission_name": name or gen_doc.get("submission_name") or gen_file.stem,
            "generated": gen_doc,
            "ground_truth": gt_doc,
            "gen_file": gen_file.name,
            "gt_file": gt_file.name,
        })
    return samples


# -------------------------------------------------------------- API resolution

def build_client(args) -> FaiClient:
    env = args.env or default_env()
    org_id = args.org_id
    if org_id and not UUID_RE.fullmatch(org_id):
        fail(f"--org-id {org_id!r} is not a UUID")
    if not org_id and args.account:
        org_id = resolve_org_id(args.account, env)
    if not org_id:
        fail("no org context: pass --account <slug> (from ~/.fai/accounts.json) or --org-id <uuid>")
    return FaiClient(env=env, org_id=org_id, skill_name="accuracy")


def resolve_dataset(client: FaiClient, args) -> str:
    if args.dataset:
        if not OBJECT_ID_RE.fullmatch(args.dataset):
            fail(f"--dataset {args.dataset!r} is not a 24-char hex ObjectId "
                 f"(Test Batch IDs come from the /eval/<id> URL or eval-studio list-datasets)")
        return args.dataset
    ref = args.workflow
    workflow_id = ref if UUID_RE.fullmatch(ref or "") else None
    if not workflow_id:
        if not args.account:
            fail(f"workflow {ref!r} is not a UUID; resolving a workflow name needs --account")
        workflow_id = resolve_workflow_id(args.account, ref, args.env or default_env())
    datasets = studio_fetch.resolve_dataset_for_workflow(client, workflow_id)
    if not datasets:
        fail(f"no Test Batch is pinned to workflow {workflow_id}")
    if len(datasets) > 1:
        print(f"{len(datasets)} Test Batches for workflow {workflow_id} — "
              f"re-run with --dataset <id>:")
        print_table(["DATASET_ID", "NAME", "TYPE", "SAMPLES", "WITH_GT"],
                    [[d.get("id"), d.get("name"), d.get("dataset_type"),
                      d.get("submission_count"), d.get("submissions_with_gt")]
                     for d in datasets])
        sys.exit(2)
    ds = datasets[0]
    note(f"Test Batch: {ds.get('name')} ({ds.get('id')})")
    return ds["id"]


# --------------------------------------------------------------------- modes

def run_check_api(cov: dict) -> int:
    print(f"Test Batch: {cov['dataset_name']} ({cov['dataset_id']})")
    print(f"Report status: {cov['status']}"
          + ("  STALE" if cov["is_stale"] else "")
          + ("  PARTIAL" if cov["partial"] else ""))
    print(f"Test run: {cov['run_id']} ({cov['run_status']})")
    cov_txt = "—" if cov["coverage"] is None else f"{cov['coverage']:.3f}"
    print(f"Headline: {fmt_pct(cov['overall_accuracy'])} "
          f"({cov['overall_accuracy_source']}), coverage {cov_txt}")
    print(f"\nScored Samples: {len(cov['samples'])}")
    print_table(["SAMPLE", "SUBMISSION_ID", "ACCURACY"],
                [[s.get("submission_name"), s.get("submission_id"),
                  fmt_pct(s.get("accuracy"))] for s in cov["samples"]])
    print(f"\nScored steps: {len(cov['steps'])}")
    print_table(["STEP", "TYPE", "ACCURACY", "THRESHOLD", "STATUS", "FIELDS", "EVALS"],
                [[s["step"][:44], s["type"], fmt_pct(s["accuracy"]),
                  fmt_pct(s["threshold"]), s["status"], s["scored_fields"],
                  s["evaluations"]] for s in cov["steps"]])
    if cov["steps_without_counts"]:
        print(f"\n! {len(cov['steps_without_counts'])} step(s) emit no counts and cannot move "
              f"the headline: {', '.join(cov['steps_without_counts'])}")
    if cov["missing_gt"]:
        print(f"\n! {len(cov['missing_gt'])} Sample(s) have no Ground Truth and are excluded.")
    if cov["missing_execution"]:
        print(f"! {len(cov['missing_execution'])} Sample(s) have no completed run — rerun them "
              f"to include them.")
    return 0 if cov["samples"] else 1


def run_check_offline(samples: List[dict]) -> int:
    rows = []
    empty = 0
    for s in samples:
        gt = set((s.get("ground_truth") or {}).get("steps") or {})
        gen = set((s.get("generated") or {}).get("steps") or {})
        if not (gt & gen):
            empty += 1
        rows.append([s["submission_name"][:40], len(gt), len(gen), len(gt & gen),
                     ", ".join(sorted(gt - gen))[:40], ", ".join(sorted(gen - gt))[:40]])
    print(f"Samples: {len(samples)}")
    print_table(["SAMPLE", "GT_STEPS", "GEN_STEPS", "SCORED", "ONLY_GT", "ONLY_GEN"], rows)
    if empty:
        print(f"\n! {empty} Sample(s) have no step with both Ground Truth and "
              f"AI-Generated output — they contribute nothing to the report.")
    return 0 if samples else 1


def print_summary(stats: dict) -> None:
    o = stats["overall"]
    plat = stats.get("platform") or {}
    src = o.get("accuracy_source") or plat.get("overall_accuracy_source") or "local"
    print(f"Accuracy {fmt_pct(o['accuracy'])}  (source: {src}"
          + (f", coverage {o['coverage']:.3f}" if o.get("coverage") is not None else "") + ")")
    print(f"Field verdicts: {o['correct']}/{o['total']} agree  ({o['errors']} disagree)")
    tax = stats["error_taxonomy"]
    total_err = sum(tax.values()) or 1
    mix = " · ".join(f"{k.lower()} {v} ({v * 100 // total_err}%)" for k, v in tax.items())
    print(f"Error mix: {mix}")
    print(f"Samples: {o['submissions']}   Steps: {len(stats['block_stats'])}")
    worst = [b for b in stats["block_stats"] if not b.get("no_row_model")][:5]
    if worst:
        print("Weakest steps:")
        print_table(["STEP", "ACCURACY", "DISAGREE", "VERDICTS"],
                    [[b["block"][:44], fmt_pct(b["accuracy"]), b["errors"],
                      f"{b['correct']}/{b['total']}"] for b in worst])


# ----------------------------------------------------------------------- main

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        prog="accuracy_report.py",
        description="Field-level accuracy report for an Eval Studio Test Batch (read-only).")
    ap.add_argument("--env", choices=("prod", "staging", "local"),
                    help="default from ~/.fai/accounts.json defaults.env, else prod")
    ap.add_argument("--account", help="account slug/name/domain from ~/.fai/accounts.json")
    ap.add_argument("--org-id", help="explicit PropelAuth org UUID (wins over --account)")

    src = ap.add_argument_group("source")
    src.add_argument("--dataset", help="Test Batch ID (24-char hex, from the /eval/<id> URL)")
    src.add_argument("--workflow", help="workflow UUID or name; resolved to its Test Batch")
    src.add_argument("--from-dir", nargs=2, metavar=("GEN_DIR", "GT_DIR"),
                     help="offline fallback: two folders of per-Sample JSON docs, scored locally")
    src.add_argument("--scoring-mode", choices=("dataset", "all_fields"), default="dataset",
                     help="which report variant to read (default dataset)")
    src.add_argument("--max-samples", type=int,
                     help="read only the first N Samples (each detail is ~100-500 KB)")

    out = ap.add_argument_group("output")
    out.add_argument("--out", help="HTML output path (default accuracy_report_<date>.html)")
    out.add_argument("--title", help="report title (default '<Test Batch> — Accuracy Report')")
    out.add_argument("--max-rows", type=int, default=250,
                     help="per-Sample budget for AGREEING value rows in the HTML payload. "
                          "Disagreeing rows are ALWAYS included, so this trades context for "
                          "file size, never evidence. Each Sample whose rows were dropped says "
                          "so in its accordion. Default 250; --full removes the budget")
    out.add_argument("--full", action="store_true",
                     help="emit every value row — large file, no row budget")
    out.add_argument("--stats-json", help="also write the raw stats dict to this path")
    out.add_argument("--check", action="store_true",
                     help="print report status, Sample roster and step coverage, then exit")

    cfg = ap.add_argument_group("offline scoring config (--from-dir only)")
    cfg.add_argument("--config", metavar="JSON", help="ScoringConfig as inline JSON, @file, or a path")
    cfg.add_argument("--exclude-step", action="append", metavar="STEP", help="repeatable")
    cfg.add_argument("--exclude-field", action="append", metavar="STEP:FIELD", help="repeatable")
    cfg.add_argument("--step-alias", action="append", metavar="FROM=TO", help="repeatable")
    cfg.add_argument("--identity-key", action="append", metavar="STEP:FIELD",
                     help="force the list-row identity key for a step; repeatable")
    cfg.add_argument("--synonyms", metavar="JSON",
                     help='value equivalences {"path marker": {"from": "to"}}; '
                          "an insurance preset ships in references/insurance_synonyms.json")
    cfg.add_argument("--union-keys", action="store_true",
                     help="score keys present on EITHER side (default: both sides only)")

    args = ap.parse_args(argv)

    truncated_note = ""
    if not (args.dataset or args.workflow or args.from_dir):
        fail("pick a source: --dataset <id>, --workflow <uuid|name>, or --from-dir GEN GT")
    if args.from_dir and (args.dataset or args.workflow):
        fail("--from-dir cannot be combined with --dataset/--workflow")
    if args.max_rows < 0:
        fail("--max-rows must be >= 0")
    if not args.from_dir:
        used = [f"--{f.replace('_', '-')}" for f in OFFLINE_ONLY_FLAGS if getattr(args, f)]
        if used:
            note(f"warn: {', '.join(used)} apply to --from-dir only. The platform did the "
                 f"scoring here; its verdicts and config are read as-is.")

    warnings: List[str] = []
    dataset_id = dataset_name = dataset_url = ""
    config = None

    if args.from_dir:
        config = build_config(args)
        samples = load_from_dirs(*args.from_dir)
        if not samples:
            fail(f"no paired Samples found in {args.from_dir[0]} / {args.from_dir[1]}")
        dataset_name = Path(args.from_dir[0]).expanduser().resolve().name
        workflow_name = ""
        app_url = ""
        if args.check:
            sys.exit(run_check_offline(samples))
        stats = compute(samples, config)
        scoring_source = "local field-level scoring (--from-dir)"
    else:
        client = build_client(args)
        dataset_id = resolve_dataset(client, args)
        summary = studio_fetch.fetch_summary(client, dataset_id, args.scoring_mode)
        warnings = studio_fetch.check_report_ready(summary, dataset_id)
        for w in warnings:
            note(f"warn: {w}")
        if args.check:
            sys.exit(run_check_api(studio_fetch.coverage(summary)))
        dataset_name = summary.get("dataset_name") or dataset_id
        dataset_url = client.dataset_url(dataset_id)
        app_url = client.app_url
        # Optional single call: the Test Batch record names the workflow the
        # report is about, which the footer wants. Never fatal.
        try:
            ds_record = studio_fetch.get_dataset(client, dataset_id)
            workflow_name = (ds_record.get("workflow_name")
                             or ds_record.get("workflow", {}).get("name") or "")
        except (FaiError, AttributeError):
            workflow_name = ""
        if not workflow_name and args.workflow and not UUID_RE.fullmatch(args.workflow):
            workflow_name = args.workflow
        roster = studio_fetch.scored_samples(summary)
        note(f"Reading {len(roster)} scored Sample(s) from the platform's accuracy report ...")
        step_labels = studio_fetch.build_step_labels(summary)
        bundles = studio_fetch.iter_sample_bundles(
            client, dataset_id, summary, args.scoring_mode, args.max_samples, progress=note)
        stats = compute_from_api(summary, bundles, step_labels)
        scoring_source = "platform accuracy report"
        # --max-samples truncates the per-Sample reads, but the summary's headline
        # and per-step aggregates still describe the WHOLE batch. Rendering that
        # without saying so shows a batch-wide score above a partial drill-down.
        if args.max_samples and args.max_samples < len(roster):
            truncated_note = (
                f"PARTIAL REPORT — drill-down covers {args.max_samples} of "
                f"{len(roster)} Samples (--max-samples). The headline and per-step "
                f"accuracy still describe all {len(roster)} Samples, so they do not "
                f"match the Samples shown below. Re-run without --max-samples "
                f"before quoting any number from this report.")
            note(f"warning: {truncated_note}")

    if not stats["per_submission"]:
        fail("no Sample produced any scored field. Run with --check to see the coverage.")

    print_summary(stats)

    stamp = datetime.date.today().isoformat()
    out_path = Path(args.out).expanduser() if args.out else Path(f"accuracy_report_{stamp}.html")
    plat = stats.get("platform") or {}
    meta = {
        "scoring_note_prefix": truncated_note,
        "title": args.title or f"{dataset_name} — Accuracy Report",
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "workflow_name": workflow_name or dataset_name,
        "dataset_url": dataset_url,
        "env": args.env or default_env(),
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": scoring_source,
        "scoring_note": (
            "Accuracy percentages are read from the platform's accuracy report, so they "
            "match the Eval Studio UI. Verdict counts are tallies of those same verdicts."
            if not args.from_dir else
            "Scored locally from two folders of JSON. These numbers are not the platform's "
            "and will differ on formatting-only differences."),
        "warnings": warnings,
        "sample_count": stats["overall"]["submissions"],
        "record_label_fields": list(config.record_label_fields) if config else [],
        "max_rows": None if args.full else args.max_rows,
        "full": bool(args.full),
        "config": config.to_dict() if config else None,
        "platform": {k: v for k, v in plat.items()
                     if k not in ("execution_ids_by_submission", "scoring_config")},
        "scoring_config": plat.get("scoring_config"),
        "execution_ids_by_submission": plat.get("execution_ids_by_submission") or {},
        "app_url": app_url,
        # Deep-link shapes the renderer needs; both are stable app routes.
        "execution_url_template": f"{app_url}/workflow-execution/{{execution_id}}" if app_url else "",
        "dataset_url_template": f"{app_url}/eval/{{dataset_id}}" if app_url else "",
    }

    if args.stats_json:
        stats_path = Path(args.stats_json).expanduser()
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False, default=str) + "\n")
        print(f"stats: {stats_path}")

    try:
        import render  # noqa: PLC0415 — optional at import time by design
        writer = render.write
    except Exception as e:  # ImportError, or a broken render module
        print(f"\nerror: the HTML renderer is not available ({type(e).__name__}: {e}).",
              file=sys.stderr)
        print(f"       Expected {SCRIPT_DIR / 'render.py'} exposing "
              f"write(stats, out_path, meta).", file=sys.stderr)
        if not args.stats_json:
            fallback = out_path.with_suffix(".stats.json")
            fallback.write_text(json.dumps(stats, indent=2, ensure_ascii=False, default=str) + "\n")
            print(f"       Scoring succeeded; stats written to {fallback}", file=sys.stderr)
        sys.exit(3)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer(stats, str(out_path), meta)
    size = out_path.stat().st_size if out_path.exists() else 0
    print(f"report: {out_path}  ({size / 1024:.0f} KB)")
    if dataset_url:
        print(f"test batch: {dataset_url}")


if __name__ == "__main__":
    try:
        main()
    except ReportNotReady as e:
        fail(str(e), code=4)
    except AccountError as e:
        fail(str(e))
    except FaiError as e:
        detail = f" | body: {e.body[:400]}" if e.body else ""
        fail(f"{e}{detail}")
    except KeyboardInterrupt:
        fail("interrupted", code=130)
