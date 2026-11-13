#!/usr/bin/env python3
"""Roll-up for the verify sub-skill: one verdict, one findings.json, one report.html.

    python3 report.py --out <dir> [--json] [--no-html] [--self-test]

Reads whatever pass payloads exist in `<dir>` (facts.json, structural.json,
flow.json, paths.json, modules.json, html.json, simplify.json,
orchestrator.json), groups their findings by root cause, and writes:

    <out>/findings.json   complete and ungrouped, grouping carried as references
    <out>/report.html     self-contained, FurtherAI report palette, light + dark

Why the grouping exists (CONTRACT.md "Findings"; the passes overlap by design):
one defect surfaces from several angles. `Prepared Documents Conservation Check`
in cna_dua_audit_lpl.json draws ORPHAN_STEP and UNUSED_FIELDS from redundancy
and UNCONSUMED_FIELD from flow_check — three lines for one fact, that nothing
downstream reads the step. This module collapses those into one entry that
names the passes that agreed, because independent corroboration is signal and
must stay visible, and keeps every original finding in findings.json.

Two rules this file is built to respect:

- `graph.terminals` and `graph.sinks` answer different questions. `terminals` is
  type-based ("does this workflow deliver through email / docx / summary /
  display") and `sinks` is "steps whose output nothing consumes".
  submission_intake.json and quantum_cny.json have no terminal step and 11 and
  4 real endpoints, because the extraction grid is the deliverable. Graph shape
  is read off sinks; terminals answer only the delivery question; and nothing
  here says or implies that a workflow without a terminal produces nothing.
- The facts file reaches 3.4 MB (CONTRACT.md A5b). Only its `workflow` header
  and `counters` are read, by targeted scan, never `json.load` of the whole
  file — the passes have already summarized everything else.

stdlib only. Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import html as html_lib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import traceback
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from common import (  # noqa: E402  — the pinned shared API, never reimplemented
    SEVERITIES,
    THRESHOLDS,
    finding,
    slug,
    sort_findings,
    table,
    truncate,
)

#: lib/fai_render.py + lib/assets/tokens.css — the same path the accuracy
#: report takes, so this looks like the same product (CONVENTIONS.md "Design
#: tokens"). Optional at import time: a missing asset degrades the report's
#: styling, it must not lose the report.
_PLUGIN_ROOT = _HERE.parents[3]
if str(_PLUGIN_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT / "lib"))
try:
    import fai_render  # noqa: E402
except Exception:  # pragma: no cover — degrade, never fail
    fai_render = None

SCHEMA_VERSION = 1

#: Pass id -> display label. "structural" is `../validate_workflow.py` (pass 1)
#: and "orchestrator" is verify.py's own findings; neither is in
#: common.PASS_NAMES, so neither can use PassOutput — verify.py writes those two
#: payloads directly in the same shape.
PASS_LABELS = (
    ("facts", "Facts"),
    ("structural", "Structural"),
    ("flow", "Data flow"),
    ("paths", "Paths & gates"),
    ("modules", "Modules"),
    ("html", "Dashboard"),
    ("simplify", "Simplification"),
    ("orchestrator", "Orchestrator"),
)
PASS_ORDER = [p for p, _ in PASS_LABELS]
PASS_LABEL = dict(PASS_LABELS)

VERDICT_FAIL = "FAIL"
VERDICT_NOTES = "PASS WITH NOTES"
VERDICT_PASS = "PASS"

# --------------------------------------------------------------------------- #
# Terminal caps. Model context is the scarce resource (CONTRACT.md hard rule 4
# and "Output discipline"): the roll-up is bounded by section AND by a hard
# global line budget, so no corpus workflow can widen it.
# --------------------------------------------------------------------------- #

MAX_BLOCKER_ISSUES = 10
MAX_WARNING_ISSUES = 8
MAX_NOTE_LINES = 12
MAX_DEGRADE_LINES = 5
MAX_TERMINAL_LINES = 120

#: Report-side caps. Same intent as THRESHOLDS but about rendering, not policy.
MAX_HTML_TABLE_ROWS = 200
MAX_EMBED_BYTES = 12 * 1024 * 1024
FACTS_HEAD_BYTES = 512 * 1024
FACTS_TAIL_BYTES = 512 * 1024


# --------------------------------------------------------------------------- #
# Defect classes — the grouping model
# --------------------------------------------------------------------------- #
#
# A class is a set of finding codes that describe ONE underlying defect, plus
# the evidence keys that make two findings in that class different instances of
# it. Codes outside every class group only with themselves, so an unmapped or
# newly added code can never be merged into something it does not belong to.
#
# `why` is printed and rendered verbatim. It is the answer to a reader's "why
# are these one thing?", which is the whole point of grouping.

class DefectClass(object):
    __slots__ = ("id", "label", "why", "codes", "locus")

    def __init__(self, id: str, label: str, why: str,
                 codes: Sequence[str], locus: Sequence[str] = ()) -> None:
        self.id = id
        self.label = label
        self.why = why
        self.codes = frozenset(codes)
        self.locus = tuple(locus)


DEFECT_CLASSES: Tuple[DefectClass, ...] = (
    DefectClass(
        "dead_output", "Step output is never consumed",
        "Each of these findings is the same fact from a different angle: "
        "nothing downstream reads this step's output. One edit fixes all of them.",
        ("UNCONSUMED_FIELD", "UNUSED_FIELDS", "ORPHAN_STEP", "ISLAND_STEP",
         "HOLD_OUTPUT_UNCONSUMED", "HOLD_PARAM_UNCONSUMED",
         "MANUAL_FORM_UNREAD", "MANUAL_FIELD_UNREAD"),
    ),
    DefectClass(
        "broken_wire", "Input mapping does not resolve",
        "These all describe one wire — the same parameter fed from the same "
        "source path. Correcting the path or the type settles every one.",
        ("UNRESOLVED_PATH", "CONTAINER_VALUE_SUFFIX", "CELL_NOT_UNWRAPPED",
         "CELL_WIRED_WHOLE", "TYPE_MISMATCH", "INDEX_MISMATCH",
         "UNVERIFIED_PATH", "DUPLICATE_INPUT_MAPPING", "MISSING_REQUIRED_INPUT"),
        locus=("to_param", "param", "output_attribute", "path", "value_path"),
    ),
    DefectClass(
        "never_runs", "Step never runs",
        "One unreachable step, reported by whichever check noticed it first.",
        ("UNREACHABLE_STEP", "UNREACHABLE_GATE", "UNREACHABLE_IN_CYCLE",
         "BRANCH_ROOT_NOT_STAMPED", "STALE_PARENT_CONDITION",
         "UNREACHABLE_CONDITIONS"),
    ),
    DefectClass(
        "dead_branch", "Branch can never be selected",
        "One decision branch that no input can select. The condition is the "
        "single thing to change.",
        ("DEAD_BRANCH", "DEAD_DEFAULT_BRANCH", "IMPOSSIBLE_CASE",
         "ALWAYS_TRUE_CASE", "DECISION_SELECTS_NOTHING",
         "CONDITION_VARIABLE_DEAD", "EMPTY_CONDITION", "EMPTY_AND_GROUP"),
        locus=("branch", "case", "variable"),
    ),
    DefectClass(
        "code_contract", "Step code does not match its declaration",
        "The step's Python and its declared signature or output schema "
        "disagree; both readings of that one mismatch are shown here.",
        ("CODE_SIGNATURE_MISMATCH", "CODE_RETURN_MISSING", "CODE_RETURN_EXTRA",
         "CODE_ENTRY_MISSING", "CODE_UNPARSEABLE", "HTML_CODE_SYNTAX_ERROR",
         "HTML_WRONG_OUTPUT_KEY"),
    ),
    DefectClass(
        "dashboard_render", "Dashboard does not render",
        "The producer raised or returned nothing usable. Every fixture that "
        "hit the same failure is counted here rather than listed separately.",
        ("HTML_RENDER_ERROR", "HTML_EMPTY_SHELL", "HTML_CODE_UNAVAILABLE",
         "HTML_PARAM_NOT_ACCEPTED", "HTML_RAW_DICT_RENDERED",
         "HTML_PLACEHOLDER_LEAK", "HTML_COMPOSE_VISIBLE"),
    ),
    DefectClass(
        "dashboard_markup", "Dashboard markup is malformed",
        "One broken markup tree, described by each lint that tripped over it.",
        ("HTML_MALFORMED", "HTML_UNCLOSED_TAG", "HTML_STRAY_END_TAG",
         "HTML_NO_DOCTYPE", "HTML_UNCLOSED_OPTIONAL"),
    ),
    DefectClass(
        "dashboard_escaping", "Dashboard interpolates without escaping",
        "Unescaped interpolation in one producer — one helper fixes it.",
        ("HTML_NO_ESCAPING", "HTML_UNESCAPED_INPUT"),
    ),
    DefectClass(
        "dashboard_style", "Dashboard diverges from the dashboard standard",
        "This dashboard was not built on the shared tokens. Each divergence "
        "below is a symptom of that one decision, fixed in one edit.",
        ("HTML_STYLE_TOKENS", "HTML_EMOJI", "HTML_EMOJI_LITERALS",
         "HTML_EXTERNAL_FONT", "HTML_FONT_NO_FALLBACK", "HTML_HARDCODED_DATE"),
    ),
    DefectClass(
        "dashboard_external", "Dashboard reaches the network at render time",
        "One producer fetching a remote resource, seen at lint and at render.",
        ("HTML_EXTERNAL_REQUEST", "HTML_RENDER_NETWORK_ATTEMPT"),
    ),
    DefectClass(
        "dashboard_delivery", "Email delivery is not configured as written",
        "One email step's routing, from whichever angle it was noticed.",
        ("HTML_EMAIL_RECIPIENTS", "HTML_REPLY_TO_ORIGINAL"),
    ),
    DefectClass(
        "no_delivery", "No terminal delivery step on this route",
        "The route ends without an email, docx, summary or display step. "
        "Advisory: the extraction grid or the API is a legitimate deliverable.",
        ("NO_TERMINAL_OUTPUT", "PATH_WITHOUT_TERMINAL",
         "BRANCH_TERMINATES_NOWHERE"),
        locus=("branch", "path"),
    ),
    DefectClass(
        "redundant_extraction", "Near-identical extraction repeated across steps",
        "The duplicated fields and the duplicated steps are the same "
        "observation about the same group of steps.",
        ("DUPLICATE_FIELD", "DUPLICATE_STEP"),
    ),
    DefectClass(
        "schema_size", "Schema or prompt is oversized",
        "One step carrying more than the thresholds allow; each measure of it "
        "points at the same step.",
        ("OVERSIZED_SCHEMA", "DEEP_SCHEMA", "LONG_PROMPT", "CASE_SPRAWL"),
    ),
    DefectClass(
        "operator", "Resolution operator is wrong for this input",
        "One parameter's multi-source operator.",
        ("OPERATOR_MISSING", "OPERATOR_NOT_LOWERCASE", "OPERATOR_ON_SINGLE_SOURCE",
         "OPERATOR_OPERAND_TYPE", "OPERATOR_SCALAR_CONCAT", "OPERATOR_UNKNOWN"),
        locus=("param",),
    ),
    DefectClass(
        "comparison_risk", "Comparison operand is risky",
        "One comparison in one case.",
        ("COMPARISON_TYPE_RISK", "AMBIGUOUS_DATE_LITERAL", "OPERAND_MISSING_VALUE"),
        locus=("branch", "case", "param"),
    ),
    DefectClass(
        "gate_rerun", "Manual gate rerun wiring",
        "One gate's rerun configuration.",
        ("GATE_RERUN_SET", "GATE_RERUN_NO_UPSTREAM"),
    ),
    DefectClass(
        "manual_gate", "Manual step collects or passes nothing",
        "One manual step that stops the run without moving data through it.",
        ("MANUAL_INPUT_NO_FIELDS", "HOLD_NO_PASSTHROUGH"),
    ),
)

_CLASS_OF_CODE: Dict[str, DefectClass] = {}
for _cls in DEFECT_CLASSES:
    for _code in _cls.codes:
        _CLASS_OF_CODE[_code] = _cls

#: Evidence keys that name the step set a workflow-level finding is about.
_STEP_SET_KEYS = ("steps", "matched_steps", "extracting_steps", "producers")

#: Evidence keys that name a non-step subject.
_OTHER_SUBJECT_KEYS = ("module", "module_id", "registry")


# --------------------------------------------------------------------------- #
# Loading pass payloads
# --------------------------------------------------------------------------- #

def _read_json(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    try:
        with open(str(path)) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def facts_header(path: str) -> Dict[str, Any]:
    """The `workflow` block and `counters` from facts.json, without parsing it.

    CONTRACT.md A5b puts the facts file at 3.4 MB for the largest workflow and
    says it never enters model context; this report has no business holding it
    in memory either. `workflow` sits near the head of the payload and
    `counters` near the tail, so a bounded read of each end is enough. A file
    small enough not to matter is parsed normally.
    """
    out: Dict[str, Any] = {"workflow": {}, "counters": {}, "read": "none"}
    try:
        size = os.path.getsize(path)
    except OSError:
        return out

    if size <= FACTS_HEAD_BYTES:
        whole = _read_json(pathlib.Path(path)) or {}
        out["workflow"] = whole.get("workflow") or {}
        out["counters"] = whole.get("counters") or {}
        out["read"] = "whole (%.0f KB)" % (size / 1024.0)
        return out

    decoder = json.JSONDecoder()

    def _grab(blob: str, key: str) -> Optional[Dict[str, Any]]:
        marker = '"%s":' % key
        at = blob.find(marker)
        if at < 0:
            return None
        start = at + len(marker)
        while start < len(blob) and blob[start] in " \t\r\n":
            start += 1
        try:
            value, _ = decoder.raw_decode(blob, start)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    try:
        with open(path) as handle:
            head = handle.read(FACTS_HEAD_BYTES)
            handle.seek(max(0, size - FACTS_TAIL_BYTES))
            tail = handle.read()
    except OSError:
        return out

    out["workflow"] = _grab(head, "workflow") or {}
    out["counters"] = _grab(tail, "counters") or _grab(head, "counters") or {}
    out["read"] = "head+tail of %.1f MB" % (size / 1048576.0)
    return out


def load_pass_payloads(out_dir: str) -> Dict[str, Dict[str, Any]]:
    """Every `<out>/<pass>.json` that exists and parses, keyed by pass id."""
    found: Dict[str, Dict[str, Any]] = {}
    for name in PASS_ORDER:
        path = pathlib.Path(out_dir) / ("%s.json" % name)
        if not path.exists():
            continue
        payload = _read_json(path)
        if payload is None:
            continue
        payload.setdefault("pass", name)
        payload["_path"] = str(path)
        found[name] = payload
    return found


def _pass_findings(payload: Dict[str, Any], pass_id: str) -> List[Dict[str, Any]]:
    rows = []
    for item in payload.get("findings") or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row.setdefault("pass", pass_id)
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #

def _evidence(item: Dict[str, Any]) -> Dict[str, Any]:
    ev = item.get("evidence")
    return ev if isinstance(ev, dict) else {}


def subject_of(item: Dict[str, Any]) -> Tuple[str, str, List[str]]:
    """(kind, key, steps) — who the finding is about.

    A step-scoped finding is about its step. A workflow-level finding that
    names a set of steps is about that set, so DUPLICATE_FIELD over
    {Binder, Policy, Quote} and DUPLICATE_STEP over the same three group
    together. Everything else is about the workflow.
    """
    step = item.get("step")
    if step:
        return ("step", str(step), [str(step)])
    ev = _evidence(item)
    # A named module is a stronger subject than the steps that matched it: two
    # modules matching the same two step names are two findings about two
    # modules, not one finding about the steps.
    for key in _OTHER_SUBJECT_KEYS:
        value = ev.get(key)
        if isinstance(value, str) and value:
            steps = [str(v) for v in (ev.get("matched_steps") or []) if v]
            return (key, value, sorted(steps))
    for key in _STEP_SET_KEYS:
        value = ev.get(key)
        if isinstance(value, (list, tuple)) and value:
            steps = sorted(str(v) for v in value if v)
            if steps:
                return ("steps", " + ".join(steps), steps)
    return ("workflow", "", [])


def _locus(item: Dict[str, Any], cls: Optional[DefectClass]) -> str:
    """Which instance of the class this is. Empty means step-scoped."""
    if cls is None or not cls.locus:
        return ""
    ev = _evidence(item)
    for key in cls.locus:
        value = ev.get(key)
        if isinstance(value, (str, int)) and str(value):
            return "%s=%s" % (key, value)
    return ""


def _severity_rank(severity: str) -> int:
    try:
        return SEVERITIES.index(severity)
    except ValueError:
        return len(SEVERITIES)


def _subject_label(kind: str, key: str, extra_steps: int) -> str:
    if kind == "workflow":
        return "(workflow)"
    if kind == "steps":
        return key
    label = key
    if extra_steps:
        label = "%s (+%d more)" % (key, extra_steps)
    return label


def group_findings(findings: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse findings into issues. Two stages, both deliberate.

    Stage A honours each pass's own `group_key`: the pass already decided what
    counts as one thing inside its own view, and `common.PassOutput` collapses
    on it when printing, so the roll-up must not disagree.

    Stage B merges across passes, and only through DEFECT_CLASSES — a code in
    no class merges with nothing but itself. The merge key is
    (class, subject, locus), so one step's two bad wires stay two issues while
    one step's three descriptions of one dead output become one.

    Nothing is discarded: every issue lists the ids of its members.
    """
    stamped: List[Dict[str, Any]] = []
    for index, item in enumerate(findings):
        row = dict(item)
        row["id"] = "F%03d" % (index + 1)
        stamped.append(row)

    # -- stage A: intra-pass, on the pass's own group_key ------------------- #
    clusters: List[Dict[str, Any]] = []
    by_key: Dict[Tuple[str, str], int] = {}
    for row in stamped:
        pass_id = row.get("pass") or "?"
        group_key = row.get("group_key")
        if group_key:
            slot = by_key.get((pass_id, group_key))
            if slot is not None:
                clusters[slot]["members"].append(row)
                continue
            by_key[(pass_id, group_key)] = len(clusters)
        clusters.append({"members": [row]})

    # -- stage B: cross-pass, on (defect class, subject, locus) ------------- #
    issues: List[Dict[str, Any]] = []
    by_root: Dict[Tuple[str, str, str, str], int] = {}
    for cluster in clusters:
        head = cluster["members"][0]
        code = head.get("code") or "UNKNOWN"
        cls = _CLASS_OF_CODE.get(code)
        kind, key, _steps = subject_of(head)
        root = (cls.id if cls else "code:%s" % code, kind, key, _locus(head, cls))
        slot = by_root.get(root)
        if slot is None:
            by_root[root] = len(issues)
            issues.append({"root": root, "class": cls, "clusters": [cluster]})
        else:
            issues[slot]["clusters"].append(cluster)

    # -- shape each issue --------------------------------------------------- #
    shaped: List[Dict[str, Any]] = []
    for index, issue in enumerate(issues):
        members: List[Dict[str, Any]] = []
        for cluster in issue["clusters"]:
            members.extend(cluster["members"])
        members.sort(key=lambda m: (_severity_rank(m.get("severity", "note")),
                                    m.get("pass") or "", m.get("code") or ""))
        lead = members[0]
        cls = issue["class"]
        kind, key, steps = subject_of(lead)
        distinct_steps = sorted({str(m.get("step")) for m in members if m.get("step")})
        extra = max(0, len(distinct_steps) - 1) if kind == "step" else 0

        codes: List[str] = []
        passes: List[str] = []
        for member in members:
            if member.get("code") and member["code"] not in codes:
                codes.append(member["code"])
            if member.get("pass") and member["pass"] not in passes:
                passes.append(member["pass"])
        passes.sort(key=lambda p: PASS_ORDER.index(p) if p in PASS_ORDER else 99)

        fixes: List[str] = []
        for member in members:
            fix = (member.get("fix") or "").strip()
            if fix and fix not in fixes:
                fixes.append(fix)

        details: List[Dict[str, str]] = []
        for member in members:
            detail = (member.get("detail") or "").strip()
            if not detail:
                continue
            if any(d["detail"] == detail for d in details):
                continue
            details.append({"pass": member.get("pass") or "?",
                            "code": member.get("code") or "?",
                            "detail": detail})

        merged = len(codes) > 1 or len(passes) > 1
        shaped.append({
            "id": "G%03d" % (index + 1),
            "class": cls.id if cls else "code:%s" % (lead.get("code") or "UNKNOWN"),
            "label": cls.label if (cls and merged) else (lead.get("title") or lead.get("code")),
            "why": cls.why if (cls and merged) else None,
            "severity": lead.get("severity", "note"),
            "subject_kind": kind,
            "subject": key,
            "subject_label": _subject_label(kind, key, extra),
            "steps": steps or distinct_steps,
            "locus": issue["root"][3],
            "title": lead.get("title") or lead.get("code"),
            "detail": lead.get("detail") or "",
            "details": details,
            "codes": codes,
            "passes": passes,
            "corroborated": len(passes) > 1,
            "occurrences": len(members),
            "fixes": fixes,
            "finding_ids": [m["id"] for m in members],
        })

    shaped.sort(key=lambda g: (_severity_rank(g["severity"]),
                               -len(g["passes"]), -g["occurrences"],
                               g["class"], g["subject_label"]))
    for index, group in enumerate(shaped):
        group["id"] = "G%03d" % (index + 1)

    lookup = {gid: group["id"] for group in shaped for gid in group["finding_ids"]}
    for row in stamped:
        row["group"] = lookup.get(row["id"])
    return [stamped, shaped]  # type: ignore[return-value]


def counts_of(findings: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    out = {"blockers": 0, "warnings": 0, "notes": 0}
    key = {"blocker": "blockers", "warning": "warnings", "note": "notes"}
    for item in findings:
        slot = key.get(item.get("severity"))
        if slot:
            out[slot] += 1
    return out


def verdict_of(counts: Dict[str, int]) -> str:
    """CONTRACT.md "Findings": any blocker is FAIL; warnings or notes alone are
    PASS WITH NOTES; nothing is PASS."""
    if counts.get("blockers"):
        return VERDICT_FAIL
    if counts.get("warnings") or counts.get("notes"):
        return VERDICT_NOTES
    return VERDICT_PASS


# --------------------------------------------------------------------------- #
# Delivery and graph shape — the terminals/sinks rule
# --------------------------------------------------------------------------- #

def delivery_of(payloads: Dict[str, Dict[str, Any]],
                counters: Dict[str, Any]) -> Dict[str, Any]:
    """How this workflow ends, stated without the terminals/sinks confusion.

    `terminals` answers "does it deliver through an email, docx, summary or
    display step". `sinks` answers "whose output nothing consumes". A workflow
    with no terminal still has endpoints and still produces data — the
    extraction grid is the deliverable — so the wording here never equates the
    two. paths.py's NO_TERMINAL_OUTPUT note says the same thing at finding
    level; this is the same ruling applied to the report's own prose.
    """
    paths = payloads.get("paths") or {}
    graph = paths.get("graph") if isinstance(paths.get("graph"), dict) else {}
    terminals = list(graph.get("terminal_steps") or [])
    sinks = list(graph.get("sinks_not_terminal") or [])
    entries = list(graph.get("entries") or [])
    unreachable = list(graph.get("graph_unreachable") or [])
    known = bool(graph) or "paths" in payloads

    endpoints = len(terminals) + len(sinks)
    if not known:
        headline = "Delivery not assessed — the paths pass did not run."
        shape = ""
    elif terminals:
        headline = "Delivers through %d terminal step%s: %s." % (
            len(terminals), "" if len(terminals) == 1 else "s",
            ", ".join(terminals[:4]) + (", …" if len(terminals) > 4 else ""))
        shape = "%d endpoint%s in the graph (%d terminal, %d sink%s)." % (
            endpoints, "" if endpoints == 1 else "s", len(terminals),
            len(sinks), "" if len(sinks) == 1 else "s")
    else:
        headline = ("No terminal delivery step. The results are the step "
                    "outputs themselves — the extraction grid in the UI, or "
                    "the API. That is a correct shape for an extraction-only "
                    "workflow, not a defect.")
        shape = "%d endpoint%s in the graph, all sinks." % (
            endpoints, "" if endpoints == 1 else "s")
    return {
        "known": known,
        "terminals": terminals,
        "sinks": sinks,
        "entries": entries,
        "graph_unreachable": unreachable,
        "endpoints": endpoints,
        "headline": headline,
        "shape": shape,
        "steps": counters.get("steps"),
    }


# --------------------------------------------------------------------------- #
# The bounded terminal roll-up
# --------------------------------------------------------------------------- #

class _Budget(object):
    """A printer with a hard line budget. Section caps keep the shape sane;
    this keeps the total honest even if a section cap is later loosened."""

    def __init__(self, limit: int = MAX_TERMINAL_LINES) -> None:
        self.limit = limit
        self.lines: List[str] = []
        self.dropped = 0

    def add(self, text: str = "") -> None:
        if len(self.lines) >= self.limit - 1:
            self.dropped += 1
            return
        self.lines.append(text)

    def add_all(self, rows: Iterable[str]) -> None:
        for row in rows:
            self.add(row)

    def render(self) -> str:
        body = list(self.lines)
        if self.dropped:
            body.append("... %d more line(s) suppressed by the terminal budget"
                        % self.dropped)
        return "\n".join(body)


def _issue_line(group: Dict[str, Any], number: int) -> List[str]:
    head = "  %d. %-42s %s" % (number, truncate(group["subject_label"], 42),
                               truncate(group["label"], 62))
    lines = [head]
    if group["corroborated"] or len(group["codes"]) > 1:
        agree = ", ".join("%s/%s" % (m["pass"], m["code"])
                          for m in _member_pairs(group)[:4])
        lines.append("     %d findings agree: %s" % (group["occurrences"], agree))
    elif group["occurrences"] > 1:
        lines.append("     %s x%d" % (group["codes"][0], group["occurrences"]))
    detail = truncate(group["detail"], 150)
    if detail:
        lines.append("     %s" % detail)
    if group["fixes"]:
        lines.append("     fix: %s" % truncate(group["fixes"][0], 150))
    return lines


def _member_pairs(group: Dict[str, Any]) -> List[Dict[str, str]]:
    seen: List[Dict[str, str]] = []
    for detail in group["details"]:
        pair = {"pass": detail["pass"], "code": detail["code"]}
        if pair not in seen:
            seen.append(pair)
    if not seen:
        seen = [{"pass": group["passes"][0] if group["passes"] else "?",
                 "code": group["codes"][0] if group["codes"] else "?"}]
    return seen


def render_terminal(result: Dict[str, Any]) -> str:
    out = _Budget()
    wf = result["workflow"]
    counters = result["counters"]
    counts = result["counts"]
    groups = result["groups"]

    out.add("=== VERIFY · %s ===" % (wf.get("name") or "(unnamed workflow)"))
    out.add("%s — %d blocker%s, %d warning%s, %d note%s" % (
        result["verdict"],
        counts["blockers"], "" if counts["blockers"] == 1 else "s",
        counts["warnings"], "" if counts["warnings"] == 1 else "s",
        counts["notes"], "" if counts["notes"] == 1 else "s"))

    bits = []
    for singular, plural, key in (("step", "steps", "steps"),
                                  ("leaf", "leaves", "schema_leaves"),
                                  ("edge", "edges", "edges"),
                                  ("field", "fields", "fields"),
                                  ("decision", "decisions", "decisions"),
                                  ("hidden", "hidden", "hidden_steps")):
        value = counters.get(key)
        if value is not None:
            bits.append("%s %s" % (value, singular if value == 1 else plural))
    if wf.get("bytes"):
        bits.append("%.2f MB" % (wf["bytes"] / 1048576.0))
    if bits:
        out.add(" · ".join(bits))

    dedupe = result["dedupe"]
    out.add("%d finding%s from %d pass%s collapsed to %d issue%s (%d corroborated)"
            % (dedupe["findings"], "" if dedupe["findings"] == 1 else "s",
               dedupe["passes_ran"], "" if dedupe["passes_ran"] == 1 else "es",
               dedupe["issues"], "" if dedupe["issues"] == 1 else "s",
               dedupe["corroborated"]))
    if result["delivery"]["headline"]:
        out.add(truncate(result["delivery"]["headline"], 200))

    degraded = result["notes"][:MAX_DEGRADE_LINES]
    if degraded:
        out.add("")
        out.add("DEGRADED")
        for note in degraded:
            out.add("  - %s" % truncate(note, 170))
        if len(result["notes"]) > MAX_DEGRADE_LINES:
            out.add("  ... %d more (see findings.json)"
                    % (len(result["notes"]) - MAX_DEGRADE_LINES))

    for severity, cap, heading in (("blocker", MAX_BLOCKER_ISSUES, "BLOCKERS"),
                                   ("warning", MAX_WARNING_ISSUES, "WARNINGS")):
        picked = [g for g in groups if g["severity"] == severity]
        out.add("")
        if not picked:
            out.add("%s  none" % heading)
            continue
        out.add("%s (%d issue%s)" % (heading, len(picked),
                                     "" if len(picked) == 1 else "s"))
        for number, group in enumerate(picked[:cap], 1):
            out.add_all(_issue_line(group, number))
        if len(picked) > cap:
            out.add("  ... %d more (see report.html)" % (len(picked) - cap))

    notes = [g for g in groups if g["severity"] == "note"]
    out.add("")
    if not notes:
        out.add("NOTES  none")
    else:
        out.add("NOTES (%d issue%s)" % (len(notes), "" if len(notes) == 1 else "s"))
        rolled: List[Tuple[str, int, int, str, str]] = []
        index: Dict[str, int] = {}
        for group in notes:
            key = "%s|%s" % (group["class"], "+".join(group["codes"]))
            slot = index.get(key)
            if slot is None:
                index[key] = len(rolled)
                rolled.append(("+".join(group["codes"]), 1, group["occurrences"],
                               group["passes"][0] if group["passes"] else "?",
                               group["subject_label"]))
            else:
                code, issues, occ, pass_id, where = rolled[slot]
                rolled[slot] = (code, issues + 1, occ + group["occurrences"],
                                pass_id, where)
        rolled.sort(key=lambda r: (-r[1], r[0]))
        for code, issues, occ, pass_id, where in rolled[:MAX_NOTE_LINES]:
            scope = where if issues == 1 else "%d step(s)/subject(s)" % issues
            out.add("  %-34s %-9s %s" % (truncate(code, 34), "x%d" % occ,
                                         truncate(scope, 44)))
        if len(rolled) > MAX_NOTE_LINES:
            out.add("  ... %d more note kind(s) (see report.html)"
                    % (len(rolled) - MAX_NOTE_LINES))

    out.add("")
    status = " · ".join(
        "%s %d/%d/%d" % (name, p["counts"]["blockers"], p["counts"]["warnings"],
                         p["counts"]["notes"])
        for name, p in ((row["pass"], row) for row in result["passes"])
        if p.get("ran"))
    if status:
        out.add("PASSES  %s   (blockers/warnings/notes)" % status)
    missing = [row["pass"] for row in result["passes"]
               if not row.get("ran") and row["pass"] != "orchestrator"]
    if missing:
        out.add("SKIPPED %s" % ", ".join(missing))

    out.add("ARTIFACTS")
    if result.get("report_html"):
        out.add("  report    %s" % result["report_html"])
    out.add("  findings  %s" % result["findings_json"])
    shots = result["screenshots"]
    if shots["count"]:
        out.add("  shots     %d image%s, ~%d image tokens if shown"
                % (shots["count"], "" if shots["count"] == 1 else "s",
                   shots["est_tokens"]))
    out.add("SUMMARY verify verdict=%s blockers=%d warnings=%d notes=%d "
            "issues=%d out=%s" % (
                result["verdict"].replace(" ", "_"), counts["blockers"],
                counts["warnings"], counts["notes"], len(groups),
                result["out_dir"]))
    return out.render()


# --------------------------------------------------------------------------- #
# HTML report
# --------------------------------------------------------------------------- #

def esc(value: Any) -> str:
    return html_lib.escape("" if value is None else str(value), quote=True)


_FALLBACK_TOKENS = """
:root{--fai-brand:#fb9608;--fai-action:#425c86;--fai-success:#2b785d;
--fai-success-bg:rgba(54,160,123,.2);--fai-warning:#bc6d06;--fai-warning-bg:#ffe5be;
--fai-error:#b53b18;--fai-error-bg:#fcded7;--fai-accent:#4a6a9b;--fai-accent-bg:#dfe8fb;
--fai-neutral-bg:#ecebe5;--fai-text:#161611;--fai-text-secondary:#6f6d64;
--fai-text-tertiary:#969388;--fai-surface:#fff;--fai-surface-container:#f9f9f9;
--fai-surface-hover:#f4f4f4;--fai-border:#e8e8e8;--fai-radius:8px;--fai-radius-sm:4px;
--fai-radius-lg:12px;--fai-shadow-chip:0 1px .5px .05px rgba(24,24,27,.05);
--fai-font:-apple-system,'Segoe UI',sans-serif}
"""

REPORT_CSS = r"""
<style>
  /* Report-local tints, all derived from the FurtherAI report palette
     (CONVENTIONS.md "Design tokens"). Light values live on bare :root so the
     dark block below only has to restate what changes. */
  :root {
    --rep-tint-error: #fdf1ed;
    --rep-tint-warn:  #fff7e9;
    --rep-tint-ok:    #f0f7f4;
    --rep-neutral:    #f1f0ec;
    --rep-hero-1:     #161611;
    --rep-hero-2:     #1e2531;
    --rep-hero-3:     #425c86;
    --rep-on-dark:    #f7f7f4;
    --rep-shot-mat:   #ffffff;
    --rep-code:       ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  /* The viewer's theme decides. Only the tokens that change are restated. */
  @media (prefers-color-scheme: dark) {
    :root {
      --fai-text: #f1f0ea;
      --fai-text-secondary: #a9a69c;
      --fai-text-tertiary: #8b877f;
      --fai-surface: #1d1d19;
      --fai-surface-container: #141412;
      --fai-surface-hover: #262621;
      --fai-border: #34342d;
      --fai-action: #93a9d1;
      --fai-accent: #93a9d1;
      --fai-accent-bg: rgba(66,92,134,.30);
      --fai-success: #63c39d;
      --fai-success-bg: rgba(43,120,93,.28);
      --fai-warning: #e5a63f;
      --fai-warning-bg: rgba(188,109,6,.28);
      --fai-error: #ea7a60;
      --fai-error-bg: rgba(181,59,24,.28);
      --fai-neutral-bg: #2b2b25;
      --rep-tint-error: rgba(181,59,24,.13);
      --rep-tint-warn:  rgba(251,150,8,.11);
      --rep-tint-ok:    rgba(43,120,93,.13);
      --rep-neutral:    #262621;
      --rep-hero-1:     #0f0f0d;
      --rep-hero-2:     #171d27;
      --rep-hero-3:     #2f4363;
    }
  }
  * { box-sizing: border-box; }
  body { font-family: var(--fai-font); background: var(--fai-surface-container);
         color: var(--fai-text); line-height: 1.5; margin: 0;
         -webkit-font-smoothing: antialiased; }
  .wrap { max-width: 1140px; margin: 0 auto; padding: 22px 18px 70px; }
  a { color: var(--fai-action); }

  .hero { background-image:
      radial-gradient(circle at 92% 14%, rgba(251,150,8,.20), rgba(251,150,8,0) 42%),
      linear-gradient(135deg, var(--rep-hero-1) 0%, var(--rep-hero-2) 58%, var(--rep-hero-3) 100%);
    border: 1px solid rgba(255,255,255,.08); border-radius: 22px; padding: 28px 32px;
    box-shadow: 0 16px 36px rgba(22,22,17,.18); margin-bottom: 16px;
    display: grid; grid-template-columns: 1fr auto; gap: 26px; align-items: center; }
  .hero-title { font-size: 27px; font-weight: 700; color: var(--rep-on-dark);
                margin: 0 0 8px; letter-spacing: -.02em; }
  .hero-meta { font-size: 13px; color: rgba(247,247,244,.70); }
  .hero-note { font-size: 12px; color: rgba(247,247,244,.58); margin-top: 9px; max-width: 700px; }
  .hero-chips { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
  .chip { font-size: 11px; font-weight: 600; padding: 5px 11px;
          border-radius: var(--fai-radius-sm); background: rgba(255,255,255,.09);
          color: rgba(247,247,244,.85); }
  .chip b { color: #fff; }
  .verdict { text-align: right; padding-left: 22px; border-left: 1px solid rgba(255,255,255,.14); }
  .verdict-label { font-size: 10px; font-weight: 600; letter-spacing: .08em;
                   text-transform: uppercase; color: rgba(247,247,244,.55); margin-bottom: 7px; }
  .verdict-value { font-size: 34px; font-weight: 700; line-height: 1.05;
                   letter-spacing: -.02em; color: var(--rep-on-dark); }
  .verdict-value.fail { color: #ff9d80; }
  .verdict-value.ok   { color: #8fe0bd; }
  .verdict-sub { font-size: 12px; color: rgba(247,247,244,.62); margin-top: 7px; }

  .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
             gap: 12px; margin-bottom: 18px; }
  .metric-card { background: var(--fai-surface); border: 1px solid var(--fai-border);
                 border-radius: var(--fai-radius-lg); padding: 14px 18px;
                 box-shadow: var(--fai-shadow-chip); }
  .metric-label { font-size: 10px; font-weight: 600; letter-spacing: .06em;
                  text-transform: uppercase; color: var(--fai-text-secondary); margin-bottom: 4px; }
  .metric-value { font-size: 25px; font-weight: 700; letter-spacing: -.02em;
                  font-variant-numeric: tabular-nums; }
  .metric-value.err { color: var(--fai-error); }
  .metric-value.warn { color: var(--fai-warning); }
  .metric-sub { font-size: 11px; color: var(--fai-text-tertiary); margin-top: 3px; }

  section { margin-bottom: 22px; }
  h2 { font-size: 15px; font-weight: 700; letter-spacing: -.01em; margin: 0 0 4px;
       display: flex; align-items: baseline; gap: 9px; }
  h2 .count { font-size: 11px; font-weight: 600; color: var(--fai-text-tertiary); }
  .sec-hint { font-size: 12px; color: var(--fai-text-secondary); margin: 0 0 10px; max-width: 860px; }
  h3 { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em;
       color: var(--fai-text-secondary); margin: 16px 0 7px; }

  .card { background: var(--fai-surface); border: 1px solid var(--fai-border);
          border-radius: var(--fai-radius); padding: 16px 18px; }
  .empty { font-size: 13px; color: var(--fai-text-secondary); background: var(--fai-surface);
           border: 1px solid var(--fai-border); border-radius: var(--fai-radius);
           padding: 12px 16px; }
  .empty.ok { border-left: 3px solid var(--fai-success); background: var(--rep-tint-ok); }
  .empty.off { border-left: 3px solid var(--fai-text-tertiary); }

  .issue { background: var(--fai-surface); border: 1px solid var(--fai-border);
           border-radius: var(--fai-radius); padding: 14px 16px; margin-bottom: 10px; }
  .issue.blocker { border-left: 3px solid var(--fai-error); background: var(--rep-tint-error); }
  .issue.warning { border-left: 3px solid var(--fai-warning); background: var(--rep-tint-warn); }
  .issue.note    { border-left: 3px solid var(--fai-action); }
  .issue-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; }
  .issue-title { font-size: 14px; font-weight: 600; }
  .issue-where { font-size: 12px; color: var(--fai-text-secondary);
                 font-family: var(--rep-code); }
  .issue-body { font-size: 13px; margin-top: 7px; }
  .issue-body p { margin: 0 0 6px; }
  .issue-fix { font-size: 13px; margin-top: 8px; padding: 8px 11px;
               border-radius: var(--fai-radius-sm); background: var(--fai-surface-hover);
               border: 1px solid var(--fai-border); }
  .issue-fix b { color: var(--fai-text); }
  .why { font-size: 12px; color: var(--fai-text-secondary); margin-top: 7px;
         padding-left: 10px; border-left: 2px solid var(--fai-accent-bg); }
  .agree { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; align-items: center; }
  .agree-label { font-size: 11px; font-weight: 600; color: var(--fai-text-secondary); }

  .fai-badge { display: inline-block; padding: 2px 8px; border-radius: 5px;
               font-size: 11px; font-weight: 600; white-space: nowrap; }
  .b-error   { background: var(--fai-error-bg);   color: var(--fai-error); }
  .b-warn    { background: var(--fai-warning-bg); color: var(--fai-warning); }
  .b-ok      { background: var(--fai-success-bg); color: var(--fai-success); }
  .b-accent  { background: var(--fai-accent-bg);  color: var(--fai-action); }
  .b-neutral { background: var(--fai-neutral-bg); color: var(--fai-text-secondary); }
  .mono { font-family: var(--rep-code); font-size: 11px; }

  /* Wide tables scroll inside their own container; the page body never does. */
  .scroll { overflow-x: auto; border: 1px solid var(--fai-border);
            border-radius: var(--fai-radius); background: var(--fai-surface);
            -webkit-overflow-scrolling: touch; }
  table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
  th, td { text-align: left; padding: 7px 12px; border-bottom: 1px solid var(--fai-border);
           vertical-align: top; }
  th { font-size: 10px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase;
       color: var(--fai-text-secondary); background: var(--fai-surface-container);
       position: sticky; top: 0; white-space: nowrap; }
  tr:last-child td { border-bottom: none; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  td.nowrap { white-space: nowrap; }
  td.code { font-family: var(--rep-code); font-size: 11px; }
  .tbl-more { font-size: 11px; color: var(--fai-text-tertiary); padding: 7px 12px; }

  details { margin-top: 8px; }
  summary { cursor: pointer; font-size: 12px; color: var(--fai-text-secondary); }
  details[open] summary { margin-bottom: 7px; }

  .shots { display: grid; gap: 16px; }
  .shot-card { background: var(--fai-surface); border: 1px solid var(--fai-border);
               border-radius: var(--fai-radius); overflow: hidden; }
  .shot-head { padding: 11px 16px; border-bottom: 1px solid var(--fai-border);
               display: flex; flex-wrap: wrap; gap: 9px; align-items: baseline; }
  .shot-name { font-size: 13px; font-weight: 600; }
  .shot-meta { font-size: 11px; color: var(--fai-text-tertiary); font-family: var(--rep-code); }
  /* The dashboards render light. A light mat keeps them from glaring in dark mode. */
  .shot-mat { background: var(--rep-shot-mat); padding: 12px; overflow-x: auto; }
  .shot-mat img { display: block; max-width: 100%; height: auto;
                  border: 1px solid var(--fai-border); border-radius: var(--fai-radius-sm); }
  .shot-missing { padding: 14px 16px; font-size: 12px; color: var(--fai-text-secondary); }

  .kv { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
        gap: 10px 18px; font-size: 12.5px; }
  .kv div span { color: var(--fai-text-secondary); }
  .kv div b { font-variant-numeric: tabular-nums; }

  .footer { margin-top: 26px; padding-top: 14px; border-top: 1px solid var(--fai-border);
            font-size: 11px; color: var(--fai-text-tertiary); }
  .footer .mono { display: block; margin-top: 3px; word-break: break-all; }

  @media (max-width: 720px) {
    .hero { grid-template-columns: 1fr; }
    .verdict { text-align: left; padding-left: 0; border-left: none;
               border-top: 1px solid rgba(255,255,255,.14); padding-top: 14px; }
  }
  @media print {
    body { background: #fff; }
    .hero { box-shadow: none; }
    .scroll { overflow: visible; }
  }
</style>
"""

_SEV_BADGE = {"blocker": "b-error", "warning": "b-warn", "note": "b-accent"}


def _badge(text: Any, kind: str = "b-neutral") -> str:
    return '<span class="fai-badge %s">%s</span>' % (kind, esc(text))


def _html_table(headers: Sequence[str], rows: Sequence[Sequence[Any]],
                classes: Optional[Sequence[str]] = None,
                max_rows: int = MAX_HTML_TABLE_ROWS) -> str:
    """One wide table inside its own horizontal scroll container."""
    if not rows:
        return ""
    classes = list(classes or [""] * len(headers))
    shown = list(rows)[:max_rows]
    head = "".join("<th>%s</th>" % esc(h) for h in headers)
    body = []
    for row in shown:
        cells = []
        for index in range(len(headers)):
            value = row[index] if index < len(row) else ""
            css = classes[index] if index < len(classes) else ""
            cells.append('<td class="%s">%s</td>' % (css, value if _is_markup(value)
                                                     else esc(value)))
        body.append("<tr>%s</tr>" % "".join(cells))
    more = ""
    if len(rows) > len(shown):
        more = '<div class="tbl-more">… %d more of %d (full detail in findings.json)</div>' % (
            len(rows) - len(shown), len(rows))
    return ('<div class="scroll"><table><thead><tr>%s</tr></thead><tbody>%s'
            "</tbody></table>%s</div>" % (head, "".join(body), more))


class _Markup(str):
    """A string already escaped, so _html_table leaves it alone."""


def _is_markup(value: Any) -> bool:
    return isinstance(value, _Markup)


def _section(title: str, hint: str, body: str, count: Optional[str] = None) -> str:
    count_html = '<span class="count">%s</span>' % esc(count) if count else ""
    hint_html = '<p class="sec-hint">%s</p>' % esc(hint) if hint else ""
    return "<section><h2>%s%s</h2>%s%s</section>" % (esc(title), count_html,
                                                     hint_html, body)


def _empty(text: str, kind: str = "") -> str:
    return '<div class="empty %s">%s</div>' % (kind, esc(text))


def _issue_card(group: Dict[str, Any]) -> str:
    sev = group["severity"]
    parts = ['<div class="issue %s">' % esc(sev)]
    parts.append('<div class="issue-head">%s<span class="issue-title">%s</span>'
                 '<span class="issue-where">%s</span></div>' % (
                     _badge(sev.upper(), _SEV_BADGE.get(sev, "b-neutral")),
                     esc(group["label"]), esc(group["subject_label"])))
    if group["why"]:
        parts.append('<div class="why">%s</div>' % esc(group["why"]))
    parts.append('<div class="issue-body">')
    for detail in group["details"][:6]:
        prefix = ""
        if len(group["details"]) > 1:
            prefix = '<span class="mono">%s/%s</span> ' % (esc(detail["pass"]),
                                                           esc(detail["code"]))
        parts.append("<p>%s%s</p>" % (prefix, esc(detail["detail"])))
    if len(group["details"]) > 6:
        parts.append('<p class="mono">… %d more description(s) in findings.json</p>'
                     % (len(group["details"]) - 6))
    parts.append("</div>")
    if group["fixes"]:
        fixes = "".join("<p>%s</p>" % esc(f) for f in group["fixes"][:4])
        parts.append('<div class="issue-fix"><b>Fix</b>%s</div>' % fixes)
    agree = ['<div class="agree"><span class="agree-label">%s</span>' % (
        "Corroborated by %d passes:" % len(group["passes"]) if group["corroborated"]
        else "Reported by:")]
    for pair in _member_pairs(group):
        agree.append(_badge("%s / %s" % (PASS_LABEL.get(pair["pass"], pair["pass"]),
                                         pair["code"]), "b-neutral"))
    if group["occurrences"] > len(_member_pairs(group)):
        agree.append(_badge("%d occurrences" % group["occurrences"], "b-neutral"))
    agree.append('<span class="mono">%s</span></div>' % esc(group["id"]))
    parts.append("".join(agree))
    parts.append("</div>")
    return "".join(parts)


def _flow_section(payloads: Dict[str, Dict[str, Any]]) -> str:
    flow = payloads.get("flow")
    if flow is None:
        return _section("Data flow", "", _empty(
            "The data-flow pass did not run.", "off"))
    summary = flow.get("summary") or {}
    cells = []
    for label, key, warn in (("Input mappings", "mappings", False),
                             ("Resolved", "resolved", False),
                             ("Unresolved", "unresolved", True),
                             ("Type mismatches", "type_mismatches", True),
                             ("Unverified paths", "unverified", False),
                             ("Missing required", "missing_required", True),
                             ("Unconsumed fields", "unconsumed_actionable", False),
                             ("Code steps checked", "code_steps", False)):
        if key not in summary:
            continue
        value = summary[key]
        css = "err" if (warn and value) else ""
        cells.append('<div class="metric-card"><div class="metric-label">%s</div>'
                     '<div class="metric-value %s">%s</div></div>'
                     % (esc(label), css, esc(value)))
    body = ['<div class="metrics">%s</div>' % "".join(cells)] if cells else []

    unproduced = flow.get("unproduced_paths") or []
    if unproduced:
        body.append("<h3>Paths the producer does not declare</h3>")
        body.append(_html_table(
            ["Consumer", "Param", "Source step", "Path", "Why"],
            [[u.get("to_step"), u.get("to_param"), u.get("from_step"),
              u.get("output_attribute"), truncate(u.get("detail") or u.get("reason"), 160)]
             for u in unproduced],
            ["", "code", "", "code", ""]))

    unconsumed = flow.get("unconsumed_fields") or []
    if unconsumed:
        actionable = [u for u in unconsumed if not (u.get("excuse") or "").strip()]
        body.append("<h3>Fields nothing consumes</h3>")
        body.append('<p class="sec-hint">%d of %d have no excuse (not hidden, not '
                    'terminal, no output_type side effect).</p>'
                    % (len(actionable), len(unconsumed)))
        body.append(_html_table(
            ["Step", "Type", "Path", "Field type", "Hidden", "Excuse"],
            [[u.get("step"), u.get("step_type"), u.get("value_path") or u.get("path"),
              u.get("type"), "yes" if u.get("hidden") else "",
              truncate(u.get("excuse") or "", 90)]
             for u in unconsumed],
            ["", "code", "code", "code", "nowrap", ""]))
    if len(body) <= 1:
        body.append(_empty("Every declared input resolves and every field is read.", "ok"))
    return _section("Data flow", "Every declared input mapping resolved against the "
                                 "producing step's normalized output schema.",
                    "".join(body),
                    "%d mappings" % summary.get("mappings", 0) if summary else None)


def _paths_section(payloads: Dict[str, Dict[str, Any]],
                   delivery: Dict[str, Any]) -> str:
    paths = payloads.get("paths")
    if paths is None:
        return _section("Paths and gates", "", _empty(
            "The paths pass did not run.", "off"))
    body: List[str] = []

    # Delivery first, and stated so a terminal-free workflow is not maligned.
    body.append('<div class="card"><div class="kv">')
    body.append("<div><span>Entry steps</span><br><b>%s</b></div>"
                % esc(", ".join(delivery["entries"]) or "—"))
    body.append("<div><span>Terminal delivery steps</span><br><b>%s</b></div>"
                % esc(", ".join(delivery["terminals"]) or "none"))
    body.append("<div><span>Endpoints in the graph</span><br><b>%d</b></div>"
                % delivery["endpoints"])
    body.append("</div>")
    body.append('<p class="sec-hint" style="margin:10px 0 0">%s %s</p>'
                % (esc(delivery["headline"]), esc(delivery["shape"])))
    body.append("</div>")

    if delivery["sinks"]:
        body.append("<h3>Sinks — steps whose output nothing consumes</h3>")
        body.append('<p class="sec-hint">Graph endpoints. A sink is not a defect '
                    'on its own: a display step, a conservation check, or an '
                    'extraction whose grid is the deliverable all end here.</p>')
        body.append(_html_table(["Step"], [[s] for s in delivery["sinks"]]))

    decisions = paths.get("decisions") or []
    if decisions:
        body.append("<h3>Decisions</h3>")
        rows = []
        for dec in decisions:
            branches = ", ".join(dec.get("selectable_branches") or []) or "—"
            impossible = sum(1 for c in dec.get("cases") or [] if c.get("impossible"))
            rows.append([dec.get("step"), dec.get("wiring"),
                         len(dec.get("cases") or []), dec.get("outcome_count"),
                         impossible or "", dec.get("default_branch") or "(none)",
                         truncate(branches, 70)])
        body.append(_html_table(
            ["Decision", "Wiring", "Cases", "Outcomes", "Impossible", "Default",
             "Selectable branches"],
            rows, ["", "nowrap", "num", "num", "num", "", ""]))

    reach = paths.get("reachability") or []
    conditional = [r for r in reach if not r.get("always_runs")]
    unreachable = [r for r in reach if not r.get("reachable")]
    if unreachable:
        body.append("<h3>Unreachable steps</h3>")
        body.append(_html_table(
            ["Step", "Type", "Condition"],
            [[r.get("step"), r.get("type"), truncate(r.get("condition_expr") or "", 110)]
             for r in unreachable], ["", "nowrap", "code"]))
    if conditional:
        body.append("<h3>Conditional steps</h3>")
        body.append('<p class="sec-hint">Steps that do not run in every world. '
                    '"Worlds" are the combinations of decision branches.</p>')
        body.append(_html_table(
            ["Step", "Type", "Worlds running", "Condition"],
            [[r.get("step"), r.get("type"),
              "%s / %s" % (r.get("worlds_running"), r.get("worlds_total")),
              truncate(r.get("condition_expr") or "", 110)]
             for r in conditional], ["", "nowrap", "nowrap", "code"]))

    gates = paths.get("gates") or []
    if gates:
        body.append("<h3>Manual gates</h3>")
        body.append(_html_table(
            ["Step", "Type", "Detail"],
            [[g.get("step"), g.get("type"),
              truncate(json.dumps({k: v for k, v in g.items()
                                   if k not in ("step", "type")}), 150)]
             for g in gates], ["", "nowrap", "code"]))
    else:
        body.append("<h3>Manual gates</h3>")
        body.append(_empty("No pause, hold or manual_input step in this workflow.", "ok"))

    groups = paths.get("path_groups") or []
    if groups:
        body.append("<h3>Route groups</h3>")
        body.append(_html_table(
            ["Terminals reached", "Routes", "Steps (min–max)", "Example selection"],
            [[", ".join(g.get("terminals") or []) or "none (sinks only)",
              g.get("paths"),
              "%s–%s" % (g.get("min_steps"), g.get("max_steps")),
              truncate(json.dumps(g.get("example_selections") or {}), 110)]
             for g in groups], ["", "num", "nowrap", "code"]))
    worlds = paths.get("world_enumeration") or {}
    if worlds.get("lenient"):
        lenient = worlds["lenient"]
        body.append('<p class="sec-hint">%s worlds enumerated of %s total%s.</p>' % (
            esc(lenient.get("enumerated")), esc(lenient.get("total_worlds")),
            " (sampled)" if lenient.get("sampled") else ""))
    return _section("Paths and gates",
                    "Which steps run in which worlds, where routes end, and what "
                    "stops for a human.", "".join(body),
                    "%d step(s)" % len(reach) if reach else None)


def _modules_section(payloads: Dict[str, Dict[str, Any]]) -> str:
    modules = payloads.get("modules")
    if modules is None:
        return _section("Module proposals", "", _empty(
            "The module pass did not run.", "off"))
    body: List[str] = []
    proposals = modules.get("proposals") or []
    if proposals:
        rows = []
        for prop in proposals:
            rows.append([prop.get("job"), prop.get("step"), prop.get("module_id"),
                         prop.get("mechanism"),
                         "%.2f" % prop["best_overlap"] if isinstance(
                             prop.get("best_overlap"), (int, float)) else "",
                         prop.get("field_count"),
                         _Markup('<span class="mono">%s</span>' % esc(prop.get("command") or "")),
                         prop.get("severity")])
        body.append(_html_table(
            ["Job", "Step", "Module", "Mechanism", "Overlap", "Fields", "Command",
             "Severity"],
            rows, ["nowrap", "", "code", "nowrap", "num", "num", "", "nowrap"]))
    else:
        body.append(_empty("No upgrade, adopt or extract proposal.", "ok"))
    catalog = modules.get("catalog") or {}
    if catalog:
        counters = catalog.get("counters") or {}
        body.append("<h3>Registry</h3>")
        body.append('<div class="card"><div class="kv">')
        body.append("<div><span>Registry</span><br><b class=\"mono\">%s</b></div>"
                    % esc(catalog.get("registry")))
        for label, key in (("Modules", "modules"), ("Releases", "releases"),
                           ("Step names", "step_names"), ("Fields", "fields")):
            if key in counters:
                body.append("<div><span>%s</span><br><b>%s</b></div>"
                            % (esc(label), esc(counters[key])))
        body.append("<div><span>Index cache</span><br><b>%s</b></div>"
                    % ("hit" if catalog.get("cache_hit") else "rebuilt"))
        body.append("</div></div>")
    return _section("Module proposals",
                    "Upgrade, adopt and extract candidates against the local "
                    "module registry.", "".join(body),
                    "%d proposal(s)" % len(proposals))


def _field_usage_section(payloads: Dict[str, Dict[str, Any]]) -> str:
    simplify = payloads.get("simplify")
    if simplify is None:
        return _section("Field usage", "", _empty(
            "The simplification pass did not run.", "off"))
    usage = simplify.get("field_usage") or {}
    totals = usage.get("totals") or {}
    body: List[str] = []
    if totals:
        cells = []
        for label, key, css in (("Read by name", "used_named", ""),
                                ("Shown on canvas", "used_visible", ""),
                                ("Read wholesale", "read_wholesale", ""),
                                ("Unused", "unused", "warn"),
                                ("Out of scope", "out_of_scope", "")):
            if key in totals:
                cells.append('<div class="metric-card"><div class="metric-label">%s</div>'
                             '<div class="metric-value %s">%s</div></div>'
                             % (esc(label), css, esc(totals[key])))
        body.append('<div class="metrics">%s</div>' % "".join(cells))
        body.append('<p class="sec-hint">%s field(s) across the workflow. A field '
                    'counted once, in the strongest way it is used.</p>'
                    % esc(usage.get("field_count", "—")))
    per_step = usage.get("per_step") or {}
    if per_step:
        columns = ["used_named", "used_visible", "read_wholesale", "unused",
                   "out_of_scope"]
        rows = []
        for step in sorted(per_step):
            data = per_step[step] or {}
            rows.append([step] + [data.get(c) or "" for c in columns]
                        + [sum(int(data.get(c) or 0) for c in columns)])
        rows.sort(key=lambda r: (-int(r[4] or 0), r[0]))
        body.append("<h3>Per step</h3>")
        body.append(_html_table(
            ["Step", "By name", "On canvas", "Wholesale", "Unused", "Out of scope",
             "Total"],
            rows, ["", "num", "num", "num", "num", "num", "num"]))
    if not body:
        body.append(_empty("No field usage recorded.", "off"))
    return _section("Field usage",
                    "Where every extracted field ends up: read by name, shown on "
                    "the canvas, read wholesale, or unused.", "".join(body))


def _shots_section(payloads: Dict[str, Dict[str, Any]],
                   embedded: Dict[str, str], shots: Dict[str, Any]) -> str:
    html_pass = payloads.get("html")
    if html_pass is None:
        return _section("Dashboard screenshots", "", _empty(
            "The dashboard pass did not run.", "off"))
    producers = html_pass.get("producers") or []
    if not producers:
        return _section("Dashboard screenshots", "",
                        _empty("This workflow has no HTML-producing step.", "ok"))
    chrome = html_pass.get("chrome") or {}
    body: List[str] = []
    if not chrome.get("path"):
        body.append(_empty("No Chrome, Chromium or Edge binary on this machine, so "
                           "nothing was captured. The HTML was still linted and "
                           "rendered locally.", "off"))
    for note in html_pass.get("notes") or []:
        body.append(_empty(str(note), "off"))

    cards: List[str] = []
    for producer in producers:
        step = producer.get("step") or "(unnamed)"
        for name, fixture in sorted((producer.get("fixtures") or {}).items()):
            capture = fixture.get("capture") or {}
            markup = fixture.get("markup") or {}
            meta = []
            if markup.get("bytes"):
                meta.append("%s bytes markup" % markup["bytes"])
            if markup.get("visible_chars"):
                meta.append("%s visible chars" % markup["visible_chars"])
            if capture.get("width"):
                meta.append("%sx%s" % (capture["width"], capture["height"]))
            if capture.get("est_tokens"):
                meta.append("~%s tokens" % capture["est_tokens"])
            head = ('<div class="shot-head"><span class="shot-name">%s</span>%s'
                    '<span class="shot-meta">%s</span></div>'
                    % (esc(step), _badge(name, "b-neutral"), esc(" · ".join(meta))))
            png = capture.get("png_path")
            if png and png in embedded:
                inner = ('<div class="shot-mat"><img alt="%s dashboard, %s fixture" '
                         'src="%s"></div>' % (esc(step), esc(name), embedded[png]))
            elif fixture.get("render_ok") is False:
                inner = ('<div class="shot-missing">Render failed: %s</div>'
                         % esc(truncate(fixture.get("error") or "unknown", 300)))
            elif png:
                inner = ('<div class="shot-missing">Capture on disk but not '
                         'embedded (report size cap): <span class="mono">%s</span></div>'
                         % esc(png))
            else:
                inner = ('<div class="shot-missing">Rendered, not captured.</div>')
            cards.append('<div class="shot-card">%s%s</div>' % (head, inner))
    body.append('<div class="shots">%s</div>' % "".join(cards))
    totals = html_pass.get("totals") or {}
    body.append('<p class="sec-hint">%s fixture(s) attempted, %s rendered, %s '
                'captured. %s image(s) embedded in this report.</p>' % (
                    esc(totals.get("fixtures_attempted", "—")),
                    esc(totals.get("renders_ok", "—")),
                    esc(totals.get("captures", "—")), esc(shots["embedded"])))
    return _section("Dashboard screenshots",
                    "Each HTML producer's code run locally against synthetic "
                    "fixtures and captured in headless Chrome.", "".join(body),
                    "%d producer(s)" % len(producers))


def _simplify_section(payloads: Dict[str, Dict[str, Any]]) -> str:
    simplify = payloads.get("simplify")
    if simplify is None:
        return _section("Simplification", "", _empty(
            "The simplification pass did not run.", "off"))
    body: List[str] = []
    scale = simplify.get("scale") or {}
    if scale:
        body.append('<div class="card"><div class="kv">')
        for label, key in (("Steps", "steps"), ("Hidden steps", "hidden_steps"),
                           ("Fields", "fields"), ("Schema leaves", "schema_leaves"),
                           ("Prompt chars", "prompt_chars"), ("Code chars", "code_chars"),
                           ("Edges", "edges"), ("Dependency edges", "dependency_edges"),
                           ("Longest chain", "longest_chain"),
                           ("Widest fan-in", "widest_fan_in")):
            if key in scale:
                body.append("<div><span>%s</span><br><b>%s</b></div>"
                            % (esc(label), esc(scale[key])))
        body.append("</div>")
        if scale.get("widest_fan_in_step"):
            body.append('<p class="sec-hint" style="margin:10px 0 0">Widest fan-in '
                        'is <b>%s</b>. Longest chain: %s.</p>' % (
                            esc(scale["widest_fan_in_step"]),
                            esc(truncate(" → ".join(scale.get("longest_chain_path") or []), 260))))
        body.append("</div>")

    orphans = simplify.get("orphan_steps") or []
    if orphans:
        body.append("<h3>Orphan steps</h3>")
        body.append(_html_table(
            ["Step", "Type", "Output type", "Fields", "Step bytes", "Code chars"],
            [[o.get("step"), o.get("type"), o.get("output_type"), o.get("field_count"),
              o.get("size_bytes"), o.get("code_chars")] for o in orphans],
            ["", "nowrap", "nowrap", "num", "num", "num"]))

    dup_steps = simplify.get("duplicate_steps") or []
    if dup_steps:
        body.append("<h3>Near-identical steps</h3>")
        body.append(_html_table(
            ["Steps", "Type", "Overlap", "Same sources", "Loop candidate", "Verdict"],
            [[" + ".join(d.get("steps") or []), d.get("type"),
              "%.2f–%.2f" % (d.get("min_overlap") or 0, d.get("max_overlap") or 0),
              "yes" if d.get("same_sources") else "no",
              "yes" if d.get("loop_candidate") else "no",
              truncate(d.get("verdict") or "", 90)]
             for d in dup_steps], ["", "nowrap", "nowrap", "nowrap", "nowrap", ""]))

    dup_fields = simplify.get("duplicate_fields") or []
    if dup_fields:
        body.append("<h3>Fields extracted more than once</h3>")
        body.append(_html_table(
            ["Field", "Steps", "Types", "Distinct sources", "Sibling overlap",
             "Severity", "Verdict"],
            [[d.get("field"), " + ".join(d.get("steps") or []),
              ", ".join(d.get("known_types") or d.get("types") or []),
              "yes" if d.get("distinct_sources") else "no",
              "%.2f" % (d.get("sibling_overlap") or 0),
              d.get("severity"), truncate(d.get("verdict_text") or d.get("verdict") or "", 110)]
             for d in dup_fields],
            ["code", "", "code", "nowrap", "num", "nowrap", ""]))

    for heading, key, headers, cols in (
        ("Oversized schemas", "oversized_schemas",
         ["Step", "Type", "Fields", "Threshold"],
         ("step", "step_type", "field_count", "threshold")),
        ("Deep schemas", "deep_schemas", ["Step", "Depth", "Detail"],
         ("step", "depth", "detail")),
        ("Long prompts", "long_prompts", ["Step", "Prompt chars", "Threshold"],
         ("step", "prompt_chars", "threshold")),
        ("Reshape chains", "reshape_chains", ["Steps", "Length", "Detail"],
         ("steps", "length", "detail")),
    ):
        rows = simplify.get(key) or []
        if not rows:
            continue
        body.append("<h3>%s</h3>" % esc(heading))
        body.append(_html_table(headers, [
            [(" + ".join(r[c]) if isinstance(r.get(c), list) else r.get(c, ""))
             for c in cols] for r in rows]))

    if len(body) <= 3:
        body.append(_empty("Nothing to simplify beyond what is listed above.", "ok"))
    thresholds = simplify.get("thresholds") or {}
    if thresholds:
        body.append('<details><summary>Thresholds in force</summary>'
                    '<div class="scroll"><table><tbody>%s</tbody></table></div></details>'
                    % "".join("<tr><td>%s</td><td class=\"num\">%s</td></tr>"
                              % (esc(k), esc(v)) for k, v in sorted(thresholds.items())))
    return _section("Simplification",
                    "Duplication, dead weight and scale. Everything here is "
                    "advisory — a deliberate cross-document comparison looks "
                    "exactly like redundancy from the outside.", "".join(body))


def _all_issues_section(groups: Sequence[Dict[str, Any]]) -> str:
    if not groups:
        return ""
    rows = []
    for group in groups:
        rows.append([
            _Markup(_badge(group["severity"].upper(),
                           _SEV_BADGE.get(group["severity"], "b-neutral"))),
            group["id"], group["subject_label"], group["label"],
            _Markup('<span class="mono">%s</span>' % esc(", ".join(group["codes"]))),
            _Markup('<span class="mono">%s</span>' % esc(", ".join(group["passes"]))),
            group["occurrences"],
            _Markup('<span class="mono">%s</span>' % esc(", ".join(group["finding_ids"]))),
        ])
    body = _html_table(
        ["", "Issue", "Subject", "What", "Codes", "Passes", "Findings", "Finding ids"],
        rows, ["nowrap", "code", "", "", "", "", "num", "code"])
    return _section("Every issue", "One row per issue. `Findings` is how many raw "
                                   "findings collapsed into it; the ids resolve in "
                                   "findings.json.", body,
                    "%d issue(s)" % len(groups))


def build_html(result: Dict[str, Any], payloads: Dict[str, Dict[str, Any]],
               embedded: Dict[str, str]) -> str:
    wf = result["workflow"]
    counters = result["counters"]
    counts = result["counts"]
    groups = result["groups"]
    dedupe = result["dedupe"]
    delivery = result["delivery"]
    verdict = result["verdict"]
    title = "Verify · %s" % (wf.get("name") or "workflow")

    verdict_css = {"FAIL": "fail", "PASS": "ok"}.get(verdict, "")
    chips = []
    for label, key in (("Steps", "steps"), ("Leaves", "schema_leaves"),
                       ("Edges", "edges"), ("Fields", "fields"),
                       ("Decisions", "decisions"), ("Hidden", "hidden_steps")):
        if counters.get(key) is not None:
            chips.append('<span class="chip">%s <b>%s</b></span>'
                         % (esc(label), esc(counters[key])))
    if wf.get("bytes"):
        chips.append('<span class="chip">Size <b>%.2f MB</b></span>'
                     % (wf["bytes"] / 1048576.0))

    hero = """<div class="hero"><div>
<h1 class="hero-title">%(name)s</h1>
<div class="hero-meta"><b>Workflow verify</b> · %(passes)d passes ran · %(findings)d findings collapsed to %(issues)d issues</div>
<div class="hero-chips">%(chips)s</div>
<div class="hero-note">%(delivery)s</div>
</div>
<div class="verdict"><div class="verdict-label">Verdict</div>
<div class="verdict-value %(vcss)s">%(verdict)s</div>
<div class="verdict-sub">%(b)d blocker(s) · %(w)d warning(s) · %(n)d note(s)</div>
</div></div>""" % {
        "name": esc(wf.get("name") or "(unnamed workflow)"),
        "passes": dedupe["passes_ran"], "findings": dedupe["findings"],
        "issues": dedupe["issues"], "chips": "".join(chips),
        "delivery": esc(delivery["headline"]),
        "vcss": verdict_css, "verdict": esc(verdict),
        "b": counts["blockers"], "w": counts["warnings"], "n": counts["notes"],
    }

    metrics = []
    for label, value, css, sub in (
        ("Blockers", counts["blockers"], "err" if counts["blockers"] else "",
         "workflow will fail or silently produce wrong data"),
        ("Warnings", counts["warnings"], "warn" if counts["warnings"] else "",
         "probably wrong, defensibly intentional"),
        ("Notes", counts["notes"], "", "advisory and simplification"),
        ("Issues", dedupe["issues"], "",
         "after grouping %d finding(s) by root cause" % dedupe["findings"]),
        ("Corroborated", dedupe["corroborated"], "",
         "issues two or more passes agreed on"),
    ):
        metrics.append('<div class="metric-card"><div class="metric-label">%s</div>'
                       '<div class="metric-value %s">%s</div>'
                       '<div class="metric-sub">%s</div></div>'
                       % (esc(label), css, esc(value), esc(sub)))

    degraded = ""
    if result["notes"]:
        degraded = _section(
            "What was degraded", "The run continued and said so rather than failing.",
            _html_table(["Note"], [[n] for n in result["notes"]]),
            "%d note(s)" % len(result["notes"]))

    blockers = [g for g in groups if g["severity"] == "blocker"]
    warnings = [g for g in groups if g["severity"] == "warning"]
    blocker_body = ("".join(_issue_card(g) for g in blockers) if blockers
                    else _empty("No blockers. Nothing here will make the workflow "
                                "fail or silently produce wrong data.", "ok"))
    warning_body = ("".join(_issue_card(g) for g in warnings) if warnings
                    else _empty("No warnings.", "ok"))

    notes_groups = [g for g in groups if g["severity"] == "note"]
    notes_body = ("".join(_issue_card(g) for g in notes_groups) if notes_groups
                  else _empty("No notes.", "ok"))

    pass_rows = []
    for row in result["passes"]:
        if row["pass"] == "orchestrator" and not row.get("ran"):
            continue
        pass_rows.append([
            PASS_LABEL.get(row["pass"], row["pass"]),
            _Markup(_badge("ran" if row.get("ran") else (row.get("status") or "skipped"),
                           "b-ok" if row.get("ran") else "b-neutral")),
            row["counts"]["blockers"], row["counts"]["warnings"],
            row["counts"]["notes"],
            "%.2fs" % row["seconds"] if row.get("seconds") is not None else "",
            _Markup('<span class="mono">%s</span>' % esc(row.get("out") or "")),
        ])

    footer = ("""<div class="footer">Generated by report.py on %(when)s · FurtherAI
workflow verify. Source workflow <span class="mono">%(path)s</span>
Findings <span class="mono">%(findings)s</span></div>""" % {
        "when": esc(result["generated_at"]),
        "path": esc(wf.get("path") or "—"),
        "findings": esc(result["findings_json"]),
    })

    head = ("<meta charset='UTF-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>")
    if fai_render is not None:
        try:
            style = ("<style>%s</style><style>%s</style>%s"
                     % (fai_render.font_face_css(), fai_render.tokens_css(),
                        REPORT_CSS))
        except Exception:
            style = "<style>%s</style>%s" % (_FALLBACK_TOKENS, REPORT_CSS)
    else:
        style = "<style>%s</style>%s" % (_FALLBACK_TOKENS, REPORT_CSS)

    parts = [
        "<!doctype html><html lang=\"en\"><head><title>%s</title>%s%s</head>"
        "<body><div class=\"wrap\">" % (esc(title), head, style),
        hero,
        '<div class="metrics">%s</div>' % "".join(metrics),
        degraded,
        _section("Blockers", "Anything that will fail the workflow, or make it "
                             "silently produce wrong or empty results.",
                 blocker_body, "%d issue(s)" % len(blockers)),
        _flow_section(payloads),
        _paths_section(payloads, delivery),
        _modules_section(payloads),
        _field_usage_section(payloads),
        _shots_section(payloads, embedded, result["screenshots"]),
        _simplify_section(payloads),
        _section("Warnings", "Probably wrong or risky, but a defensible author "
                             "could have meant it.", warning_body,
                 "%d issue(s)" % len(warnings)),
        _section("Notes", "Advisory, simplification, and observations.",
                 notes_body, "%d issue(s)" % len(notes_groups)),
        _all_issues_section(groups),
        _section("Passes", "Each pass writes its own JSON; this report is the "
                           "roll-up over all of them.",
                 _html_table(["Pass", "Status", "Blockers", "Warnings", "Notes",
                              "Time", "Payload"], pass_rows,
                             ["", "nowrap", "num", "num", "num", "num", ""])),
        footer,
        "</div></body></html>",
    ]
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Screenshot embedding
# --------------------------------------------------------------------------- #

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".gif": "image/gif"}


def embed_screenshots(payloads: Dict[str, Dict[str, Any]],
                      budget: int = MAX_EMBED_BYTES
                      ) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """png_path -> data URI, so report.html stays self-contained.

    Bounded: a capture past the budget stays on disk and the report says where
    it is rather than growing without limit.
    """
    html_pass = payloads.get("html") or {}
    stats = {"count": 0, "embedded": 0, "skipped": 0, "bytes": 0,
             "est_tokens": (html_pass.get("totals") or {}).get("est_tokens") or 0}
    embedded: Dict[str, str] = {}
    used = 0
    for producer in html_pass.get("producers") or []:
        for _name, fixture in sorted((producer.get("fixtures") or {}).items()):
            png = (fixture.get("capture") or {}).get("png_path")
            if not png or png in embedded:
                continue
            stats["count"] += 1
            try:
                raw = pathlib.Path(png).read_bytes()
            except OSError:
                stats["skipped"] += 1
                continue
            if used + len(raw) > budget:
                stats["skipped"] += 1
                continue
            mime = _MIME.get(pathlib.Path(png).suffix.lower(), "image/png")
            embedded[png] = "data:%s;base64,%s" % (
                mime, base64.b64encode(raw).decode("ascii"))
            used += len(raw)
            stats["embedded"] += 1
    stats["bytes"] = used
    return embedded, stats


# --------------------------------------------------------------------------- #
# The roll-up
# --------------------------------------------------------------------------- #

def build(out_dir: str, write_html: bool = True,
          extra_notes: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Group, write findings.json and report.html, return the roll-up dict."""
    out_dir = str(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    payloads = load_pass_payloads(out_dir)

    facts_path = pathlib.Path(out_dir) / "facts.json"
    header = facts_header(str(facts_path)) if facts_path.exists() else {
        "workflow": {}, "counters": {}, "read": "none"}
    workflow = dict(header.get("workflow") or {})
    counters = dict(header.get("counters") or {})

    # A pass has usually already summarized the counters; prefer those so a
    # skipped facts read still fills the header.
    scale = (payloads.get("simplify") or {}).get("scale") or {}
    for key in ("steps", "hidden_steps", "fields", "schema_leaves", "prompt_chars",
                "code_chars", "edges", "dependency_edges"):
        if counters.get(key) is None and scale.get(key) is not None:
            counters[key] = scale[key]
    if counters.get("decisions") is None:
        decisions = (payloads.get("paths") or {}).get("decisions")
        if isinstance(decisions, list):
            counters["decisions"] = len(decisions)
    if not workflow.get("name"):
        workflow["name"] = pathlib.Path(out_dir).name

    raw: List[Dict[str, Any]] = []
    pass_rows: List[Dict[str, Any]] = []
    for name in PASS_ORDER:
        payload = payloads.get(name)
        if payload is None:
            pass_rows.append({"pass": name, "ran": False, "status": "not run",
                              "counts": {"blockers": 0, "warnings": 0, "notes": 0},
                              "out": None, "seconds": None})
            continue
        findings = _pass_findings(payload, name)
        raw.extend(findings)
        pass_rows.append({
            "pass": name, "ran": True,
            "status": payload.get("status") or "ran",
            "counts": payload.get("counts") or counts_of(findings),
            "out": payload.get("_path"),
            "seconds": payload.get("seconds"),
        })

    stamped, groups = group_findings(sort_findings(raw))
    counts = counts_of(stamped)
    verdict = verdict_of(counts)
    delivery = delivery_of(payloads, counters)

    notes: List[str] = list(extra_notes or [])
    for name in PASS_ORDER:
        payload = payloads.get(name)
        if payload is None:
            continue
        for note in payload.get("notes") or []:
            text = "%s: %s" % (PASS_LABEL.get(name, name), note)
            if text not in notes:
                notes.append(text)
    if not payloads:
        notes.append("No pass payload found in %s — nothing to roll up." % out_dir)

    embedded, shots = embed_screenshots(payloads)

    result: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "report.py",
        "generated_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "out_dir": out_dir,
        "workflow": workflow,
        "counters": counters,
        "facts_read": header.get("read"),
        "verdict": verdict,
        "counts": counts,
        "dedupe": {
            "findings": len(stamped),
            "issues": len(groups),
            "corroborated": sum(1 for g in groups if g["corroborated"]),
            "collapsed": len(stamped) - len(groups),
            "passes_ran": sum(1 for r in pass_rows if r["ran"]),
        },
        "delivery": delivery,
        "passes": pass_rows,
        "notes": notes,
        "screenshots": shots,
        "groups": groups,
        "findings": stamped,
        "findings_json": str(pathlib.Path(out_dir) / "findings.json"),
        "report_html": None,
    }

    if write_html:
        html_path = pathlib.Path(out_dir) / "report.html"
        result["report_html"] = str(html_path)
        markup = build_html(result, payloads, embedded)
        _atomic_write(html_path, markup)

    _atomic_write(pathlib.Path(result["findings_json"]),
                  json.dumps(_findings_payload(result), indent=2, default=str) + "\n")
    return result


def _findings_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """findings.json — everything, ungrouped and complete.

    The grouping is carried as references in both directions (each finding's
    `group`, each group's `finding_ids`) so a fix loop can consume either view
    and nothing was discarded to produce the roll-up.
    """
    return {
        "schema_version": result["schema_version"],
        "generated_by": result["generated_by"],
        "generated_at": result["generated_at"],
        "out_dir": result["out_dir"],
        "workflow": result["workflow"],
        "counters": result["counters"],
        "verdict": {
            "verdict": result["verdict"],
            "blockers": result["counts"]["blockers"],
            "warnings": result["counts"]["warnings"],
            "notes": result["counts"]["notes"],
            "issues": result["dedupe"]["issues"],
            "findings": result["dedupe"]["findings"],
            "corroborated": result["dedupe"]["corroborated"],
        },
        "delivery": result["delivery"],
        "passes": result["passes"],
        "notes": result["notes"],
        "screenshots": result["screenshots"],
        "grouping": {
            "model": "stage A collapses each pass's own group_key; stage B merges "
                     "across passes on (defect class, subject, locus). A code in "
                     "no class merges only with itself.",
            "classes": [{"id": c.id, "label": c.label, "why": c.why,
                         "codes": sorted(c.codes), "locus": list(c.locus)}
                        for c in DEFECT_CLASSES],
        },
        "groups": result["groups"],
        "findings": result["findings"],
    }


def _atomic_write(path: pathlib.Path, text: str) -> None:
    os.makedirs(str(path.parent), exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as handle:
        handle.write(text)
    os.replace(tmp, str(path))


def json_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """The compact machine view, for `--json`."""
    return {
        "verdict": result["verdict"],
        "counts": result["counts"],
        "dedupe": result["dedupe"],
        "workflow": {k: result["workflow"].get(k)
                     for k in ("name", "path", "bytes", "step_count")},
        "counters": result["counters"],
        "delivery": {k: result["delivery"][k]
                     for k in ("terminals", "sinks", "endpoints", "headline")},
        "passes": result["passes"],
        "notes": result["notes"],
        "screenshots": result["screenshots"],
        "issues": [{"id": g["id"], "severity": g["severity"], "class": g["class"],
                    "subject": g["subject_label"], "label": g["label"],
                    "codes": g["codes"], "passes": g["passes"],
                    "occurrences": g["occurrences"], "fix": (g["fixes"] or [None])[0]}
                   for g in result["groups"]],
        "findings_json": result["findings_json"],
        "report_html": result["report_html"],
    }


# --------------------------------------------------------------------------- #
# Self-test — hermetic. verify.py --self-test covers the real corpus.
# --------------------------------------------------------------------------- #

def _fixture_dir(root: str) -> str:
    """The LPL triple plus a terminal-free workflow plus a fixture-repeated
    blocker, written as pass payloads. Small, hermetic, and exercises exactly
    the invariants that matter."""
    os.makedirs(root, exist_ok=True)

    def _write(name: str, payload: Dict[str, Any]) -> None:
        payload.setdefault("schema_version", 1)
        payload.setdefault("pass", name)
        payload.setdefault("counts", counts_of(payload.get("findings") or []))
        with open(os.path.join(root, "%s.json" % name), "w") as handle:
            json.dump(payload, handle, indent=2)

    def _f(code, severity, step, title, detail, **kw):
        row = finding(code, severity, title, detail, step=step, **kw)
        return row

    _write("facts", {
        "generated_by": "wf_facts.py",
        "workflow": {"path": "/tmp/fixture.json", "name": "Fixture Workflow",
                     "bytes": 680895, "step_count": 29},
        "counters": {"steps": 29, "schema_leaves": 907, "edges": 94, "fields": 129,
                     "decisions": 1, "hidden_steps": 17},
        "findings": [],
    })
    _write("flow", {
        "summary": {"mappings": 94, "resolved": 94, "unresolved": 0,
                    "type_mismatches": 0, "unverified": 2, "missing_required": 0,
                    "unconsumed_actionable": 7, "code_steps": 18},
        "unconsumed_fields": [
            {"step": "Prepared Documents Conservation Check", "step_type": "custom_step",
             "path": "data.a", "value_path": "data.a", "type": "string",
             "hidden": True, "terminal": False, "excuse": ""},
        ],
        "unproduced_paths": [],
        "findings": [
            _f("UNCONSUMED_FIELD", "note", "Prepared Documents Conservation Check",
               "Hidden step produces fields nothing consumes",
               "produces 7 field(s) that nothing reads.",
               fix="Drop the unused output properties.",
               evidence={"count": 7, "paths": ["data.a"]},
               group_key="UNCONSUMED_FIELD:Prepared Documents Conservation Check"),
            _f("TYPE_MISMATCH", "blocker", "Consumer A",
               "Type mismatch", "param `x` wants file, got cell<string>.",
               fix="Add `.value`.", evidence={"to_param": "x"},
               group_key="TYPE_MISMATCH:Consumer A:x"),
            _f("UNRESOLVED_PATH", "blocker", "Consumer A",
               "Path does not exist", "`data.nope` on Producer B.",
               fix="Fix the output_attribute.", evidence={"to_param": "x"},
               group_key="UNRESOLVED_PATH:Consumer A:x"),
            _f("TYPE_MISMATCH", "blocker", "Consumer A",
               "Type mismatch", "param `y` wants number, got string.",
               fix="Cast it.", evidence={"to_param": "y"},
               group_key="TYPE_MISMATCH:Consumer A:y"),
        ],
    })
    _write("paths", {
        "reachability": [], "decisions": [], "gates": [], "paths": [],
        "path_groups": [], "world_enumeration": {},
        "termination": {"terminal_steps": []},
        "graph": {"entries": ["Prepare Documents"], "terminal_steps": [],
                  "sinks_not_terminal": ["Extract A", "Extract B"],
                  "graph_unreachable": []},
        "findings": [
            _f("NO_TERMINAL_OUTPUT", "note", None,
               "Workflow has no terminal output step",
               "None of the 18 steps is a terminal output type.",
               fix="Nothing to fix if the grid is the deliverable.",
               evidence={"step_count": 18}),
        ],
    })
    _write("modules", {"proposals": [], "catalog": {}, "findings": []})
    _write("html", {
        "producers": [{"step": "Compose Email", "output_field": "email_body",
                       "fixtures": {}}],
        "chrome": {"path": None}, "totals": {}, "notes": ["No Chrome on this machine."],
        "findings": [
            _f("HTML_RENDER_ERROR", "blocker", "Compose Email",
               "Step code raised while composing HTML", "empty fixture: boom",
               evidence={"fixture": "empty"},
               group_key="HTML_RENDER_ERROR:Compose Email:blocker"),
            _f("HTML_RENDER_ERROR", "blocker", "Compose Email",
               "Step code raised while composing HTML", "full fixture: boom",
               evidence={"fixture": "full"},
               group_key="HTML_RENDER_ERROR:Compose Email:blocker"),
            _f("HTML_RENDER_ERROR", "blocker", "Compose Email",
               "Step code raised while composing HTML", "stress fixture: boom",
               evidence={"fixture": "stress"},
               group_key="HTML_RENDER_ERROR:Compose Email:blocker"),
        ],
    })
    _write("simplify", {
        "field_usage": {"totals": {"used_named": 10, "unused": 7}, "field_count": 129,
                        "per_step": {"Prepared Documents Conservation Check": {"unused": 7}}},
        "orphan_steps": [{"step": "Prepared Documents Conservation Check",
                          "type": "custom_step", "output_type": "simple",
                          "field_count": 7, "size_bytes": 12043, "code_chars": 900}],
        "duplicate_steps": [{"steps": ["Extract A", "Extract B", "Extract C"],
                             "type": "agentic_extraction", "min_overlap": 0.8,
                             "max_overlap": 0.9, "same_sources": False,
                             "verdict": "deliberate"}],
        "duplicate_fields": [{"field": "effective_date",
                              "steps": ["Extract A", "Extract B", "Extract C"],
                              "types": ["string"], "distinct_sources": True,
                              "sibling_overlap": 0.8, "severity": "note",
                              "verdict_text": "deliberate"}],
        "scale": {"steps": 29, "fields": 129}, "thresholds": {},
        "findings": [
            _f("ORPHAN_STEP", "warning", "Prepared Documents Conservation Check",
               "Step output goes nowhere", "Nothing consumes this step.",
               fix="Delete the step, or wire it.",
               evidence={"step": "Prepared Documents Conservation Check"},
               group_key="ORPHAN_STEP:Prepared Documents Conservation Check"),
            _f("UNUSED_FIELDS", "warning", "Prepared Documents Conservation Check",
               "All output fields unused", "7 of 7 fields unused.",
               fix="Trim the schema.",
               evidence={"fields": ["a"], "unused_count": 7, "field_count": 7},
               group_key="UNUSED_FIELDS:Prepared Documents Conservation Check"),
            _f("DUPLICATE_FIELD", "note", None,
               "`effective_date` is extracted on 3 steps", "Each copy reads a "
               "different source.", fix="Confirm the re-extraction is wanted.",
               evidence={"steps": ["Extract A", "Extract B", "Extract C"],
                         "fields": ["effective_date"]},
               group_key="DUPLICATE_FIELD:Extract A|Extract B|Extract C:cross_source"),
            _f("DUPLICATE_STEP", "note", None,
               "3 steps have near-identical field sets",
               "Is this 3 steps by design, or one loop?",
               fix="Probably intended — confirm it.",
               evidence={"steps": ["Extract A", "Extract B", "Extract C"]},
               group_key="DUPLICATE_STEP:Extract A|Extract B|Extract C"),
        ],
    })
    return root


_FORBIDDEN_TERMINAL_PROSE = (
    "produces nothing", "produces no output", "delivers nothing",
    "no output at all", "does not produce anything", "produces no results",
)


def self_test() -> int:
    failures: List[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    root = tempfile.mkdtemp(prefix="verify-report-selftest-")
    try:
        _fixture_dir(root)
        result = build(root)

        # -- dedupe: the LPL triple collapses to exactly one issue ---------- #
        target = [g for g in result["groups"]
                  if g["subject"] == "Prepared Documents Conservation Check"]
        check(len(target) == 1,
              "the Prepared Documents Conservation Check triple did not collapse "
              "to one issue (got %d)" % len(target))
        if target:
            group = target[0]
            check(sorted(group["codes"]) == ["ORPHAN_STEP", "UNCONSUMED_FIELD",
                                             "UNUSED_FIELDS"],
                  "collapsed issue lost a code: %s" % group["codes"])
            check(group["passes"] == ["flow", "simplify"],
                  "collapsed issue lost a pass: %s" % group["passes"])
            check(group["severity"] == "warning",
                  "collapsed issue must keep the highest severity, got %s"
                  % group["severity"])
            check(group["corroborated"] is True, "collapsed issue not marked corroborated")
            check(len(group["fixes"]) >= 2, "collapsed issue merged away the fix guidance")
            check(len(group["finding_ids"]) == 3,
                  "collapsed issue must reference all 3 findings, got %d"
                  % len(group["finding_ids"]))
            check(bool(group["why"]), "a merged issue must say why it is one thing")

        # -- one step's two bad wires stay two issues ---------------------- #
        wires = [g for g in result["groups"] if g["class"] == "broken_wire"]
        check(len(wires) == 2,
              "two different params on one step must stay two issues, got %d"
              % len(wires))
        merged_wire = [g for g in wires if len(g["codes"]) == 2]
        check(len(merged_wire) == 1,
              "the two codes on the same param must merge, got %d" % len(merged_wire))

        # -- the fixture-repeated blocker collapses ------------------------ #
        render = [g for g in result["groups"] if g["class"] == "dashboard_render"]
        check(len(render) == 1, "3 fixtures of one render failure must be one issue")
        if render:
            check(render[0]["occurrences"] == 3,
                  "the collapsed render issue must keep its occurrence count")

        # -- duplicate field + duplicate step over one step set ------------ #
        dup = [g for g in result["groups"] if g["class"] == "redundant_extraction"]
        check(len(dup) == 1,
              "DUPLICATE_FIELD and DUPLICATE_STEP over the same step set must be "
              "one issue, got %d" % len(dup))

        # -- verdict model -------------------------------------------------- #
        check(result["verdict"] == VERDICT_FAIL,
              "blockers present must be FAIL, got %s" % result["verdict"])
        check(verdict_of({"blockers": 0, "warnings": 1, "notes": 0}) == VERDICT_NOTES,
              "warnings only must be PASS WITH NOTES")
        check(verdict_of({"blockers": 0, "warnings": 0, "notes": 1}) == VERDICT_NOTES,
              "notes only must be PASS WITH NOTES")
        check(verdict_of({"blockers": 0, "warnings": 0, "notes": 0}) == VERDICT_PASS,
              "nothing must be PASS")

        # -- findings.json is complete and valid ---------------------------- #
        payload = _read_json(pathlib.Path(root) / "findings.json")
        check(payload is not None, "findings.json is not valid JSON")
        if payload:
            check(len(payload["findings"]) == 12,
                  "findings.json must carry every raw finding, got %d"
                  % len(payload["findings"]))
            check(all(f.get("group") for f in payload["findings"]),
                  "every finding must reference its group")
            ids = {f["id"] for f in payload["findings"]}
            referenced = set()
            for group in payload["groups"]:
                referenced.update(group["finding_ids"])
            check(referenced == ids,
                  "group references and finding ids must be the same set")
            check(bool(payload["grouping"]["classes"]),
                  "findings.json must document the grouping model")

        # -- the terminals/sinks rule --------------------------------------- #
        check(not result["delivery"]["terminals"], "fixture should have no terminal")
        check(result["delivery"]["endpoints"] == 2,
              "a terminal-free workflow still has endpoints")
        markup = (pathlib.Path(root) / "report.html").read_text()
        terminal_text = render_terminal(result)
        for phrase in _FORBIDDEN_TERMINAL_PROSE:
            check(phrase not in markup.lower(),
                  "report.html says %r about a terminal-free workflow" % phrase)
            check(phrase not in terminal_text.lower(),
                  "the terminal roll-up says %r about a terminal-free workflow"
                  % phrase)
        check("extraction grid" in markup.lower(),
              "the report must name the grid as a legitimate deliverable")

        # -- report.html is self-contained ---------------------------------- #
        check(markup.startswith("<!doctype html>"), "report.html has no doctype")
        check("prefers-color-scheme: dark" in markup,
              "report.html must be readable in dark mode")
        for bad in ("src=\"http", "src='http", "href=\"http://", "href=\"https://"):
            check(bad not in markup,
                  "report.html is not self-contained (%s)" % bad)
        check("--fai-action" in markup, "report.html must use the FurtherAI tokens")
        check("class=\"scroll\"" in markup,
              "wide tables must scroll inside their own container")

        # -- the terminal roll-up is bounded -------------------------------- #
        lines = terminal_text.splitlines()
        check(len(lines) <= MAX_TERMINAL_LINES,
              "terminal roll-up is %d lines, cap is %d"
              % (len(lines), MAX_TERMINAL_LINES))
        check(lines[-1].startswith("SUMMARY verify "),
              "the last stdout line must be the machine-parseable SUMMARY")

        # -- a run with no passes at all still produces a report ------------ #
        bare = tempfile.mkdtemp(prefix="verify-report-bare-")
        try:
            empty = build(bare)
            check(empty["verdict"] == VERDICT_PASS,
                  "an empty run is PASS, not a crash")
            check(bool(empty["notes"]), "an empty run must say it found no payloads")
            check(os.path.exists(empty["report_html"]),
                  "report.html must exist even with no pass payloads")
        finally:
            shutil.rmtree(bare, ignore_errors=True)

        # -- facts header extraction, on a big file ------------------------- #
        big = os.path.join(root, "facts_big.json")
        with open(big, "w") as handle:
            json.dump({"schema_version": 1, "pass": "facts",
                       "workflow": {"name": "Big", "bytes": 3400000},
                       "steps": [{"pad": "x" * 40} for _ in range(20000)],
                       "counters": {"steps": 58}, "findings": []}, handle, indent=2)
        header = facts_header(big)
        check(header["workflow"].get("name") == "Big",
              "facts_header must read the workflow block off a large file")
        check(header["counters"].get("steps") == 58,
              "facts_header must read counters off a large file")
        check(header["read"].startswith("head+tail"),
              "a large facts file must not be parsed whole, got %r" % header["read"])
    except Exception:
        failures.append("self-test raised:\n%s" % traceback.format_exc())
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if failures:
        print("report.py --self-test FAILED (%d)" % len(failures))
        for item in failures:
            print("  - %s" % item)
        return 1
    print("report.py --self-test ok: dedupe, verdict, findings.json, "
          "self-contained HTML, terminals rule, bounded output, facts header")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Roll-up: verdict + findings.json + report.html")
    parser.add_argument("--out", help="directory holding the pass JSONs "
                                      "(required unless --self-test)")
    parser.add_argument("--json", action="store_true",
                        help="print the machine summary instead of the roll-up")
    parser.add_argument("--no-html", action="store_true",
                        help="skip report.html (findings.json is still written)")
    parser.add_argument("--quiet", action="store_true",
                        help="print only the SUMMARY line")
    parser.add_argument("--self-test", action="store_true",
                        help="run the hermetic invariant suite")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        return self_test()
    if not args.out:
        print("error: --out is required (or pass --self-test)", file=sys.stderr)
        return 2
    if not os.path.isdir(args.out):
        print("error: %s is not a directory" % args.out, file=sys.stderr)
        return 2

    try:
        result = build(args.out, write_html=not args.no_html)
    except Exception:
        print("error: report.py failed to build the roll-up:", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(json_summary(result), indent=2, default=str))
    elif args.quiet:
        print(render_terminal(result).splitlines()[-1])
    else:
        print(render_terminal(result))
    return 1 if result["counts"]["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
