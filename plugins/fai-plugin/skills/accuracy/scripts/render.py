#!/usr/bin/env python3
"""Self-contained HTML renderer for the accuracy report.

    from render import write
    write(stats, "accuracy_report.html", meta={"workflow_name": "Submission Intake"})

`stats` is the dict produced by aggregate.compute(); `meta` carries
presentation-only values and every key is optional. The output is one HTML file
with no external requests: Chart.js, its datalabels plugin, the design tokens
and the body font are all inlined from lib/assets.

Panels, in DOM order: hero, metric cards, takeaways, error-concentration Pareto,
step x sample heatmap, the Lens (scoped drill-down chart), the Values card, the
per-sample accordion, footer.

stdlib only.
"""
from __future__ import annotations

import html
import json
import re
import sys
from collections import Counter, OrderedDict
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))                       # sibling modules
sys.path.insert(0, str(_HERE.parents[2] / "lib"))    # plugin lib

import fai_render  # noqa: E402

# --------------------------------------------------------------------------- #
# Shared vocabulary / helpers borrowed from the scoring modules.               #
# Fallbacks keep the renderer importable on its own (e.g. for a fixture run).  #
# --------------------------------------------------------------------------- #
# The error taxonomy is owned by aggregate.py — never duplicate it here.
from aggregate import ETYPES, error_type as _etype  # noqa: F401


# Ordered candidates for labelling a record (list item) by its own values.
# Generic on purpose: a `*_name` beats a bare `id`, an `id` beats a `*_number`.
# Entries starting with "*" match any key with that suffix. Owned by
# ScoringConfig — mirrored here only as a fallback.
_FALLBACK_RECORD_LABEL_FIELDS = ("name", "*_name", "title", "label", "id",
                                 "*_id", "*_number", "code", "address")

try:
    from scoring import is_missing, field_label, ScoringConfig
    RECORD_LABEL_FIELDS = ScoringConfig().record_label_fields
except ImportError:  # pragma: no cover — minimal stand-ins
    _MISSING_TOKENS = {
        "", "n/a", "na", "none", "null", "not found", "unknown", "not specified",
        "not applicable", "not provided", "not available",
    }

    def is_missing(v):
        if v is None:
            return True
        s = re.sub(r"\s+", " ", str(v).strip().lower()).strip("\"'").rstrip(".")
        return s in _MISSING_TOKENS

    def field_label(key):
        if not key:
            return "(items)"
        out = re.sub(r"[_.]+", " ", key).strip()
        return " ".join(w if (w.isupper() and len(w) <= 4) else w[:1].upper() + w[1:]
                        for w in out.split())

    RECORD_LABEL_FIELDS = _FALLBACK_RECORD_LABEL_FIELDS

# Plain-language names for the three error types (internal -> display).
PLAIN = {"Missed": "missing", "Wrong value": "wrong", "Hallucinated": "extra"}

MAX_ROWS_DEFAULT = 2000

# Distinguishes "caller passed nothing" from "caller passed the default", so
# meta-supplied values only apply when the argument was genuinely omitted.
_UNSET = object()

# --------------------------------------------------------------------------- #
# Small formatting helpers                                                     #
# --------------------------------------------------------------------------- #


def _pct(v, dp=1):
    """'95.4%' / '—' for a None metric."""
    return "—" if v is None else f"{v:.{dp}f}%"


def _num(v, default=0.0):
    return default if v is None else v


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


_SOURCE_LABELS = {
    "micro_f1": "micro-F1",
    "micro_accuracy": "micro-accuracy",
    "scorer_step_accuracy": "scorer step accuracy",
    "field_macro_accuracy": "field macro accuracy",
}


def _headline(stats, o):
    """The platform headline's provenance: metric name, coverage, and the steps
    that carry no classification counts. Guardrail #7 — the headline may never be
    shown without these. Each value is looked for in every place the producer has
    put it, so the renderer survives a move."""
    plat = stats.get("platform") or {}
    h = o.get("headline") or plat.get("headline") or stats.get("headline") or {}
    src = (o.get("accuracy_source") or o.get("overall_accuracy_source")
           or plat.get("overall_accuracy_source") or stats.get("overall_accuracy_source")
           or h.get("metric") or "")
    cov = o.get("coverage")
    if cov is None:
        cov = h.get("coverage")
    if cov is not None:
        # documented as a 0-1 fraction; tolerate a producer that sends 0-100
        cov = cov * 100.0 if cov <= 1 else cov
    without = list(h.get("steps_without_counts") or plat.get("steps_without_counts") or [])
    return {"source": _SOURCE_LABELS.get(src, str(src).replace("_", " ")) if src else "",
            "coverage": cov, "without": without,
            "scored": h.get("steps_scored"), "with_counts": h.get("steps_with_counts")}


def _fill_url(template, **values):
    """Fill a producer-supplied URL template such as
    "{app_url}/workflow-execution/{execution_id}". Returns "" when the template
    is absent, when any value is missing, or when a placeholder survives — so
    callers can simply guard on truthiness and never emit a broken link."""
    if not template:
        return ""
    out = str(template)
    for key, val in values.items():
        if not val:
            return ""
        out = out.replace("{" + key + "}", str(val))
    return "" if "{" in out else out


def _when(v):
    """Readable stamp for the footer: an ISO timestamp loses its 'T' and seconds,
    anything else passes through untouched."""
    s = str(v)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(:\d{2})?", s)
    return f"{m.group(1)} {m.group(2)}" if m else s


def _jsonable(v):
    """Make a resolved ScoringConfig printable: sets/tuples -> sorted lists."""
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (set, frozenset)):
        return sorted(str(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _disp(v):
    """Display string for a field value. Bare strings show WITHOUT the surrounding
    quotes JSON would add (internal quotes preserved); everything else (numbers,
    null, objects, lists) keeps its JSON form."""
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s[:240] + "…" if len(s) > 240 else s


def _parse_path(block, path):
    """Split a field path into (record_key, field_path) within its step.

      'Extract Line Items[INV-1042].unit_price' -> ('INV-1042', 'unit_price')
      'Extract Line Items[INV-1042]'            -> ('INV-1042', '')  (whole record)
      'Extract Header.total_amount'             -> ('', 'total_amount')
    """
    rest = path[len(block):] if path.startswith(block) else path
    if rest.startswith("."):
        rest = rest[1:]
    m = re.match(r"^\[([^\]]*)\](.*)$", rest)
    if m:
        field = m.group(2)
        return m.group(1), field[1:] if field.startswith(".") else field
    return "", rest


def _field_name(field):
    """Humanized leaf label for a field path ('seat_cap' -> 'Seat Cap')."""
    parts = [p for p in re.sub(r"\[[^\]]*\]", "", field).split(".") if p]
    return " › ".join(field_label(p) for p in parts) if parts else "Entire record"


_HASH_KEY = re.compile(r"(^deep_|~)")


def _descriptor(key, fmap, label_fields=None):
    """Human label for a record, derived from its own values (key is a fallback).

    Walks `label_fields` in order and takes the first populated match, so a
    record shows up as its name/title/id rather than an opaque hash key."""
    def exact(name):
        for fk, fv in fmap.items():
            if (fk == name or fk.endswith("." + name)) and fv:
                return str(fv)
        return None

    def suffix(suf):
        for fk, fv in fmap.items():
            if fk.endswith(suf) and fv:
                return str(fv)
        return None

    for cand in (label_fields or RECORD_LABEL_FIELDS):
        v = suffix(cand[1:]) if cand.startswith("*") else exact(cand)
        if v:
            return v
    if key and not _HASH_KEY.search(key):
        return key
    return None  # caller falls back to "Record N"


def _build_groups(results, budget=None, label_fields=None):
    """Turn a sample's flat field results into display groups:

      [step, record_label, record_subid, is_record, rows]

    where rows = [[field_label, generated, ground_truth, ok], ...]. Records (list
    items) get a human header so the identifier appears once, not on every row.

    `budget` caps how many NON-mismatch rows (correct + skipped) are emitted for
    this sample; mismatch rows are always emitted. None means no cap. Returns
    (groups, dropped_row_count)."""
    raw = OrderedDict()  # (step, key) -> {'rows': [], 'fmap': {}}
    for r in results:
        block = r.get("block", "")
        key, field = _parse_path(block, r.get("path", ""))
        bucket = raw.setdefault((block, key), {"rows": [], "fmap": {}})
        ok = None if r.get("match") is None else (1 if r["match"] else 0)
        # Skip the redundant whole-record identity row when it matched (its
        # fields are listed individually); keep it only when the record is
        # wholly missing/extra so that case still shows up.
        if field == "" and ok == 1:
            continue
        row = [_field_name(field), _disp(r.get("gen")), _disp(r.get("gt")), ok]
        # 5th slot only when the platform tagged the row, so untagged rows cost
        # nothing in the payload. "extra" = false positive, "missing" = false negative.
        rs = r.get("row_status")
        if rs:
            row.append(rs)
        bucket["rows"].append(row)
        if field:
            leaf = re.sub(r"\[[^\]]*\]", "", field).split(".")[-1]
            v = r.get("gt") if not is_missing(r.get("gt")) else r.get("gen")
            if leaf and not is_missing(v):
                bucket["fmap"].setdefault(leaf, str(v))

    out, rec_n, dropped = [], {}, 0
    left = budget
    for (block, key), g in raw.items():
        rows = []
        for row in g["rows"]:
            if row[3] == 0 or left is None:      # mismatches are never dropped
                rows.append(row)
            elif left > 0:
                rows.append(row)
                left -= 1
            else:
                dropped += 1
        if not rows:
            continue
        if key == "":
            out.append([block, "", "", 0, rows])
            continue
        desc = _descriptor(key, g["fmap"], label_fields)
        if desc is None:
            rec_n[block] = rec_n.get(block, 0) + 1
            desc, subid = f"Record {rec_n[block]}", ""
        else:
            subid = key if (key and not _HASH_KEY.search(key) and key != desc) else ""
        out.append([block, desc, subid, 1, rows])
    return out, dropped


def _vstr(v):
    if isinstance(v, dict) and "value" in v:
        v = v["value"]
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


# --------------------------------------------------------------------------- #
# Payload                                                                      #
# --------------------------------------------------------------------------- #


def build_payload(stats, max_rows=MAX_ROWS_DEFAULT, full=False, record_label_fields=None):
    """Derive the browser-side payload `P` from `stats`.

    Exposed separately from write() so a caller can size-check the JSON before
    committing to a file. `max_rows` caps the per-sample count of non-mismatch
    rows in P.claims (the bulk of the file); mismatch rows are always included.
    `full=True` disables the cap entirely."""
    o = stats.get("overall", {}) or {}
    subs = stats.get("sub_stats", []) or []
    blocks = stats.get("block_stats", []) or []
    per_sub = stats.get("per_submission", []) or []

    pareto = sorted(blocks, key=lambda b: -_int(b.get("errors")))
    pareto_chart = {
        "labels": [b.get("block", "") for b in pareto],
        "errors": [_int(b.get("errors")) for b in pareto],
        # null stays null — an unscored step must never render as 0%
        "accuracy": [b.get("accuracy") for b in pareto],
        "correct": [_int(b.get("correct")) for b in pareto],
        "total": [_int(b.get("total")) for b in pareto],
        "missed": [_int((b.get("composition") or {}).get("Missed")) for b in pareto],
        "wrong": [_int((b.get("composition") or {}).get("Wrong value")) for b in pareto],
        "hallucinated": [_int((b.get("composition") or {}).get("Hallucinated"))
                         for b in pareto],
    }

    def _ov(d, label_key):
        return {"label": d.get(label_key, ""), "accuracy": d.get("accuracy"),
                "precision": d.get("precision"), "recall": d.get("recall"),
                "f1": d.get("f1"), "total": _int(d.get("total")),
                "correct": _int(d.get("correct")), "errors": _int(d.get("errors"))}

    overview = {
        "submission": [_ov(s, "submission") for s in subs],
        "step": [_ov(b, "block") for b in blocks],
    }

    # Field-detail: per-(step, field) top mismatch patterns and error-type
    # composition (+ per-cell composition), computed once from the raw results.
    agg = {}
    cellraw = {}  # (step, sample, field) -> error-type counts (errors only)
    for sub in per_sub:
        sname = sub.get("submission_name", "")
        for c in sub.get("results", []) or []:
            if c.get("match") is None:
                continue
            key = (c.get("block", ""), c.get("field_label", ""))
            d = agg.get(key)
            if d is None:
                d = agg[key] = {"total": 0, "correct": 0, "errors": 0,
                                "comp": {e: 0 for e in ETYPES}, "errpairs": Counter()}
            d["total"] += 1
            if c["match"]:
                d["correct"] += 1
                continue
            et = _etype(c)
            d["comp"][et] = d["comp"].get(et, 0) + 1
            d["errors"] += 1
            if c.get("field_label") != "(items)":
                d["errpairs"][(_vstr(c.get("gen")), _vstr(c.get("gt")))] += 1
            ck = (c.get("block", ""), c.get("submission") or sname,
                  c.get("field_label", ""))
            cc = cellraw.setdefault(ck, {e: 0 for e in ETYPES})
            cc[et] = cc.get(et, 0) + 1

    cell_comp = {}
    for (block, sname, fl), comp in cellraw.items():
        cell_comp.setdefault(block, {}).setdefault(sname, {})[fl] = comp

    field_detail = {}
    for (block, fl), d in agg.items():
        field_detail.setdefault(block, {})[fl] = {
            "patterns": [{"gen": g, "gt": t, "n": n}
                         for (g, t), n in d["errpairs"].most_common(15)],
            "comp": d["comp"], "total": d["total"],
            "correct": d["correct"], "errors": d["errors"],
        }

    # Grouped rows per sample — the single source for both the per-sample
    # accordion and the Values card. This is the bulk of the payload, hence the
    # row cap; what got dropped is reported back to the UI in P.truncated.
    budget = None if full else max(0, int(max_rows))
    claims, truncated = {}, {}
    for s in per_sub:
        name = s.get("submission_name", "")
        groups, dropped = _build_groups(s.get("results", []) or [], budget,
                                        record_label_fields)
        claims[name] = groups
        if dropped:
            truncated[name] = dropped

    # Row-level facts the heatmap body cannot get from the cell grid: a step may
    # carry a real accuracy while producing no rows at all (no_row_model).
    by_block = {b.get("block", ""): b for b in blocks}
    heat = stats.get("heatmap", {"rows": [], "cols": [], "data": []})
    heat_rows = []
    for name in heat.get("rows", []):
        b = by_block.get(name) or {}
        heat_rows.append({"label": name, "accuracy": b.get("accuracy"),
                          "total": _int(b.get("total")), "errors": _int(b.get("errors")),
                          "noRowModel": bool(b.get("no_row_model"))})

    return {
        "pareto": pareto_chart,
        "overall": o,
        "heat": heat,
        "heatRows": heat_rows,
        "taxonomyCaveat": stats.get("taxonomy_caveat") or None,
        "cell": stats.get("cell", {}), "row": stats.get("row", {}),
        "col": stats.get("col", {}), "field": stats.get("field", {}),
        "overview": overview,
        "fieldDetail": field_detail,
        "cellComp": cell_comp,
        "claims": claims,
        "truncated": truncated,
    }


# --------------------------------------------------------------------------- #
# Styles                                                                       #
# --------------------------------------------------------------------------- #

REPORT_CSS = r"""
<style>
  /* Report-local tints, all derived from the FurtherAI palette. */
  :root {
    --rep-tint-error: #fdf1ed;   /* sinopia wash */
    --rep-tint-warn:  #fff7e9;   /* mango wash */
    --rep-tint-ok:    #f0f7f4;   /* pinehurst wash */
    --rep-neutral:    #f1f0ec;   /* segmented controls, chips */
    --rep-ink:        #161611;   /* inverted surfaces */
    --rep-on-dark:    #f7f7f4;
    --rep-hero-1:     #161611;
    --rep-hero-2:     #1e2531;
    --rep-hero-3:     #425c86;
  }
  * { box-sizing: border-box; }
  body { font-family: var(--fai-font); background: var(--fai-surface-container);
         color: var(--fai-text); line-height: 1.5; margin: 0; padding: 0;
         -webkit-font-smoothing: antialiased; }
  .wrap { max-width: 1120px; margin: 0 auto; padding: 22px 18px 70px; }

  .hero { background-image:
      radial-gradient(circle at 92% 14%, rgba(251,150,8,.20), rgba(251,150,8,0) 42%),
      linear-gradient(135deg, var(--rep-hero-1) 0%, var(--rep-hero-2) 58%, var(--rep-hero-3) 100%);
    border: 1px solid rgba(255,255,255,.08); border-radius: 24px; padding: 30px 36px;
    box-shadow: 0 16px 36px rgba(22,22,17,.18); margin-bottom: 18px;
    display: grid; grid-template-columns: 1fr auto; gap: 28px; align-items: center; }
  .hero-title { font-size: 29px; font-weight: 700; color: var(--rep-on-dark); margin: 0 0 8px; letter-spacing: -.02em; }
  .hero-meta { font-size: 13px; color: rgba(247,247,244,.68); }
  .hero-meta b { font-weight: 600; color: var(--rep-on-dark); }
  .hero-note { font-size: 12px; color: rgba(247,247,244,.55); margin-top: 8px; max-width: 640px; }
  .hero-chips { display:flex; gap:8px; margin-top:16px; flex-wrap:wrap; align-items:center; }
  .chip { font-size:11px; font-weight:600; padding:5px 11px; border-radius:var(--fai-radius-sm);
          background: rgba(255,255,255,.08); color: rgba(247,247,244,.85); display:inline-flex; gap:6px; }
  .chip b { color:#fff; }
  a.chip-link { text-decoration:none; background: rgba(251,150,8,.16); color:#ffd7a1; }
  a.chip-link:hover { background: rgba(251,150,8,.26); }
  .hero-score { text-align: right; padding-left: 20px; border-left: 1px solid rgba(255,255,255,.12); }
  .hero-score-label { font-size:10px; font-weight:600; letter-spacing:.08em; text-transform:uppercase;
                      color: rgba(247,247,244,.55); margin-bottom:6px; }
  .hero-score-value { font-size:62px; font-weight:700; line-height:1; color:var(--rep-on-dark);
                      letter-spacing:-.03em; font-variant-numeric: tabular-nums; }
  .hero-score-sub { font-size:12px; color: rgba(247,247,244,.62); margin-top:6px; }
  .hero-score-note { font-size:11px; color: rgba(247,247,244,.45); margin-top:4px;
                     border-bottom:1px dotted rgba(247,247,244,.3); display:inline-block;
                     cursor:help; }

  .metrics { display:grid; grid-template-columns: repeat(3,1fr); gap:12px; margin-bottom:14px; }
  .metric-card { background:var(--fai-surface); border-radius:var(--fai-radius-lg); padding:15px 20px;
                 border:1px solid var(--fai-border); box-shadow:var(--fai-shadow-chip); }
  .metric-label { font-size:10px; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
                  color:var(--fai-text-secondary); margin-bottom:4px; }
  .metric-value { font-size:27px; font-weight:700; color:var(--fai-text); letter-spacing:-.02em;
                  font-variant-numeric: tabular-nums; }
  .metric-value.warn { color:var(--fai-error); }
  .metric-value.ratio { font-size:23px; }
  .metric-value.ratio .of { font-size:15px; font-weight:400; color:var(--fai-text-tertiary); }
  .metric-sub { font-size:11px; color:var(--fai-text-tertiary); margin-top:3px; }

  .scoring-note { font-size:12px; color:var(--fai-text-secondary); background:var(--fai-surface);
                  border:1px solid var(--fai-border); border-radius:var(--fai-radius);
                  padding:9px 14px; margin-bottom:18px; }
  .scoring-note b { color:var(--fai-text); }
  .report-notes { list-style:none; margin:0 0 16px; padding:12px 16px; border-radius:var(--fai-radius);
                  border:1px solid var(--fai-border); background:var(--fai-surface);
                  font-size:12px; display:flex; flex-direction:column; gap:6px; }
  .report-notes.warn { border-left:3px solid var(--fai-warning); background:var(--rep-tint-warn); }
  .report-notes.info { border-left:3px solid var(--fai-action); }
  .report-notes li { color:var(--fai-text); }
  .report-notes li.warn::before { content:"\26A0"; margin-right:7px; color:var(--fai-warning); }
  .report-notes li.info::before { content:"\2139"; margin-right:7px; color:var(--fai-action); }
  .mix-caveat { font-size:11px; color:var(--fai-warning); margin-top:6px; max-width:760px;
                border-top:1px dotted var(--fai-warning-bg); padding-top:5px; cursor:help; }
  .mix-caveat b { color:var(--fai-warning); }
  .scoring-note .src { display:block; margin-top:6px; font-size:11px;
                       color:var(--fai-text-tertiary); }
  .footer .cfgbox { margin-top:8px; text-align:left; max-width:720px;
                    margin-left:auto; margin-right:auto; letter-spacing:0; }
  .footer .cfgbox summary { cursor:pointer; padding:0; display:inline; font-size:11px;
                            color:var(--fai-text-secondary); border-bottom:none; }
  .footer .cfgbox[open] > summary { border-bottom:none; }
  .footer .cfgbox pre { background:var(--fai-surface); border:1px solid var(--fai-border);
                        border-radius:var(--fai-radius-sm); padding:10px 12px; margin:8px 0 0;
                        font-family:ui-monospace,Menlo,monospace; font-size:10px; line-height:1.5;
                        color:var(--fai-text-secondary); overflow-x:auto; white-space:pre; }

  .takeaways { display:grid; grid-template-columns: repeat(2,1fr); gap:12px; margin-bottom:20px; }
  .tk { border-radius:var(--fai-radius-lg); padding:16px 18px; border:1px solid var(--fai-border); }
  .tk.focus { border-top:3px solid var(--fai-error); background:var(--rep-tint-error); }
  .tk.subs  { border-top:3px solid var(--fai-warning); background:var(--rep-tint-warn); }
  .tk-title { font-size:11px; font-weight:600; letter-spacing:.05em; text-transform:uppercase; margin-bottom:4px; }
  .tk.focus .tk-title { color:var(--fai-error); } .tk.subs .tk-title { color:var(--fai-warning); }
  .tk-hint { font-size:11px; color:var(--fai-text-tertiary); margin-bottom:8px; }
  .tk-item { display:flex; justify-content:space-between; gap:10px; padding:5px 0; font-size:13px;
             border-top:1px dashed rgba(22,22,17,.08); }
  .tk-item:first-of-type { border-top:none; }
  .tk-item .v { color:var(--fai-text-secondary); white-space:nowrap; font-variant-numeric: tabular-nums; }
  .tk-item .v b { color:var(--fai-error); }
  .tk-item .v .uns, .uns { font-style:normal; color:var(--fai-text-tertiary); }
  .tk-empty { font-size:12px; color:var(--fai-text-tertiary); padding:6px 0; }

  .card { background:var(--fai-surface); border:1px solid var(--fai-border);
          border-radius:var(--fai-radius-lg); box-shadow:var(--fai-shadow-chip);
          margin-bottom:16px; overflow:hidden; }
  .card-header { padding:14px 24px; border-bottom:1px solid var(--fai-border); display:flex;
                 justify-content:space-between; align-items:center; gap:12px; }
  .card-title { font-size:13px; font-weight:600; color:var(--fai-text-secondary);
                text-transform:uppercase; letter-spacing:.04em; }
  .card-subtitle { font-size:12px; color:var(--fai-text-tertiary); text-align:right; }
  .card-body { padding:18px 24px 22px; }
  .chart-wrap { position:relative; }

  .seg { display:inline-flex; background:var(--rep-neutral); border-radius:var(--fai-radius); padding:3px; }
  .seg button { font-size:11px; font-weight:600; letter-spacing:.02em; border:none; background:transparent;
                color:var(--fai-text-secondary); padding:6px 12px; border-radius:6px; cursor:pointer;
                font-family:inherit; transition:all .12s ease; }
  .seg button.on { background:var(--fai-surface); color:var(--fai-text); box-shadow:0 1px 2px rgba(22,22,17,.14); }

  table.detail { width:100%; font-size:12px; border-collapse:collapse; }
  table.detail th { text-align:left; font-weight:600; color:var(--fai-text-secondary);
                    padding:10px 12px 10px 0; border-bottom:1px solid var(--fai-border);
                    font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
  table.detail td { padding:9px 12px 9px 0; border-bottom:1px solid var(--fai-surface-hover); vertical-align:top; }
  table.detail td.fld { font-weight:500; color:var(--fai-text); white-space:nowrap; padding-left:2px; }
  table.detail td.fld .st { display:inline-block; width:16px; font-weight:700; text-align:center; }
  table.detail tr.row-ok .st { color:var(--fai-success); }
  table.detail tr.row-bad .st { color:var(--fai-error); }
  table.detail tr.row-skip .st { color:var(--fai-text-tertiary); }
  table.detail .blank { color:var(--fai-text-tertiary); }
  .rowtag { display:inline-block; font-size:9px; font-weight:600; letter-spacing:.03em;
            text-transform:uppercase; padding:1px 5px; border-radius:3px; margin-left:6px;
            vertical-align:1px; }
  .tag-extra   { background:var(--fai-warning-bg); color:var(--fai-warning); }
  .tag-missing { background:var(--fai-accent-bg);  color:var(--fai-action); }
  table.detail tr.row-ok td { background:var(--rep-tint-ok); }
  table.detail tr.row-bad td { background:var(--rep-tint-error); }
  table.detail tr.row-skip td { background:var(--fai-surface-hover); color:var(--fai-text-secondary); }
  table.detail tr.grp-block td { background:var(--rep-ink); color:var(--rep-on-dark); font-weight:600;
                                 font-size:11px; text-transform:uppercase; letter-spacing:.05em;
                                 padding:8px 12px; border:none; }
  table.detail tr.grp-item td { background:var(--fai-surface-hover); font-weight:600; font-size:12px;
                                color:var(--fai-text); padding:7px 12px;
                                border-top:1px solid var(--fai-border); border-bottom:1px solid var(--fai-border); }
  table.detail tr.grp-item .gsub { font-weight:400; color:var(--fai-text-tertiary);
                                   font-family:ui-monospace,Menlo,monospace; font-size:11px; margin-left:8px; }
  .badge { display:inline-flex; align-items:center; font-size:11px; font-weight:600; padding:3px 9px;
           border-radius:var(--fai-radius-sm); font-variant-numeric: tabular-nums; }
  .badge-pass   { background:var(--fai-success-bg); color:var(--fai-success); }
  .badge-review { background:var(--fai-warning-bg); color:var(--fai-warning); }
  .badge-fail   { background:var(--fai-error-bg);   color:var(--fai-error); }
  .badge-unscored { background:var(--fai-neutral-bg); color:var(--fai-text-secondary);
                    font-weight:500; }
  code { font-family:ui-monospace,Menlo,monospace; font-size:11px; word-break:break-word;
         color:var(--fai-text-secondary); }

  details summary { cursor:pointer; list-style:none; user-select:none; padding:16px 24px; display:flex;
                    justify-content:space-between; align-items:center; gap:16px; }
  details summary::-webkit-details-marker { display:none; }
  details[open] > summary { border-bottom:1px solid var(--fai-border); }
  details[open] > summary .toggle-icon { transform:rotate(90deg); }
  .toggle-icon { transition:transform .15s ease; display:inline-block; font-size:10px;
                 color:var(--fai-text-tertiary); margin-right:6px; }
  .sum-left { display:flex; align-items:center; gap:6px; min-width:0; }
  .sum-name { font-weight:600; font-size:14px; }
  .sum-right { display:flex; align-items:center; gap:12px; }
  .sum-counts { font-size:12px; color:var(--fai-text-secondary); font-variant-numeric: tabular-nums; }
  .filter-row { padding:8px 24px; font-size:11px; color:var(--fai-text-secondary); }
  .filter-row label { cursor:pointer; }
  .sub-block .detail-body { padding:4px 24px 22px; }
  .sub-block .meta { display:flex; flex-wrap:wrap; gap:18px; font-size:11px; color:var(--fai-text-secondary);
                     margin:10px 0 12px; padding-bottom:12px; border-bottom:1px dashed var(--fai-border); }
  .sub-block .meta b { color:var(--fai-text); }
  .sub-block .meta a { color:var(--fai-action); text-decoration:none; }
  .sub-block .meta a:hover { text-decoration:underline; }
  .trunc-note { font-size:11px; color:var(--fai-warning); background:var(--rep-tint-warn);
                border:1px solid var(--fai-warning-bg); border-radius:var(--fai-radius-sm);
                padding:7px 10px; margin:0 0 12px; }
  .trunc-note b { color:var(--fai-warning); }

  .heatmap-wrap { overflow-x:auto; padding-bottom:6px; padding-top:4px; }
  .heatmap { border-collapse:separate; border-spacing:2px; }
  .heatmap th.col-head { height:150px; vertical-align:bottom; padding:0; position:relative; z-index:1;
                         text-align:left; white-space:nowrap; background:transparent; font-weight:400;
                         border:none; cursor:pointer; }
  .heatmap th.col-head:hover .label-rot { color:var(--fai-action); }
  .heatmap th.col-head.is-sel .label-rot { font-weight:700; color:var(--fai-text); }
  .heatmap th.col-head .label-rot { position:absolute; bottom:4px; left:50%; transform:rotate(-45deg);
                                    transform-origin:0% 100%; white-space:nowrap; font-size:12px;
                                    font-weight:500; color:var(--fai-text); }
  .heatmap th.corner { position:sticky; left:0; z-index:6; background:var(--fai-surface); text-align:left;
                       padding:8px 12px 8px 0; vertical-align:bottom; font-size:11px; font-weight:600;
                       color:var(--fai-text-secondary); text-transform:uppercase; letter-spacing:.04em; border:none; }
  .heatmap td.label { position:sticky; left:0; z-index:3; background:var(--fai-surface); font-size:12px;
                      font-weight:500; padding:4px 14px 4px 6px; width:230px; min-width:230px;
                      color:var(--fai-text); border-right:1px solid var(--fai-border); cursor:pointer; }
  .heatmap td.label:hover { background:var(--fai-surface-hover); }
  .heatmap td.label:hover .blk-name { color:var(--fai-action); }
  .heatmap td.label.is-sel { background:var(--rep-ink); border-right:none; }
  .heatmap td.label.is-sel .blk-name { color:var(--rep-on-dark); }
  .heatmap td.label.is-sel .relbar { background:rgba(255,255,255,.18); }
  .heatmap td.label.is-sel .rel-cap { color:rgba(247,247,244,.6); }
  .blk-name { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:228px; }
  .blk-name .rowtag { margin-left:5px; }
  .relbar { position:relative; height:7px; border-radius:4px; background:var(--fai-neutral-bg); margin-top:4px; }
  .relbar .rng { position:absolute; top:0; height:7px; border-radius:4px; opacity:.42; }
  .relbar .mean { position:absolute; top:-1px; width:3px; height:9px; border-radius:2px; }
  .rel-cap { font-size:9px; color:var(--fai-text-tertiary); margin-top:2px; white-space:nowrap; }
  .heatmap td.cell { text-align:center; min-width:38px; height:24px; padding:0; border-radius:3px;
                     font-size:11px; font-weight:600; cursor:pointer; font-variant-numeric: tabular-nums;
                     transition:box-shadow .12s ease, transform .12s ease; }
  .heatmap td.cell:hover { box-shadow:0 0 0 2px var(--rep-ink); transform:scale(1.08); }
  .heatmap td.cell.is-sel { box-shadow:0 0 0 2px var(--rep-ink); transform:scale(1.08); }
  .heatmap td.cell.unscored { background:var(--fai-neutral-bg); color:var(--fai-text-secondary);
                              font-weight:500; font-size:10px; letter-spacing:.02em;
                              box-shadow:inset 0 0 0 1px rgba(22,22,17,.10); }
  .heatmap td.cell.empty { background:var(--fai-surface-container); border:1px dashed var(--fai-border);
                           color:var(--fai-text-tertiary); font-weight:400; }
  .heatmap td.cell.empty:hover { box-shadow:none; transform:none; }
  .grad-legend { height:12px; border-radius:4px; max-width:420px; }
  .legend-row { display:flex; justify-content:space-between; max-width:420px; font-size:10px;
                color:var(--fai-text-secondary); margin-top:3px; }
  .legendnote { font-size:11px; color:var(--fai-text-secondary); margin-top:12px; padding-top:10px;
                border-top:1px solid var(--fai-border); }
  .legendnote b { color:var(--fai-text); }
  .hm-legend { display:flex; gap:28px; flex-wrap:wrap; margin-top:16px; padding-top:14px;
               border-top:1px solid var(--fai-border); }
  .hm-legend-grad { min-width:300px; }
  .legend-cap { font-size:11px; color:var(--fai-text-secondary); margin-top:6px; }
  .hm-legend-notes { flex:1; min-width:280px; display:flex; flex-direction:column; gap:8px;
                     font-size:11px; color:var(--fai-text-secondary); }
  .hm-legend-notes b { color:var(--fai-text); }
  .lg-bar { position:relative; display:inline-block; width:46px; height:7px; border-radius:4px;
            background:var(--fai-neutral-bg); vertical-align:middle; margin-right:4px; }
  .lg-bar .lg-rng { position:absolute; left:25%; width:55%; height:7px; border-radius:4px;
                    background:var(--fai-brand); opacity:.5; }
  .lg-bar .lg-tick { position:absolute; left:55%; width:3px; height:9px; top:-1px; border-radius:2px;
                     background:var(--fai-warning); }
  .lg-empty { display:inline-block; width:18px; text-align:center; background:var(--fai-surface-container);
              border:1px dashed var(--fai-border); color:var(--fai-text-tertiary); border-radius:3px; margin-right:4px; }
  .lg-unscored { display:inline-block; padding:0 5px; text-align:center; font-size:10px; font-weight:500;
                 background:var(--fai-neutral-bg); color:var(--fai-text-secondary);
                 box-shadow:inset 0 0 0 1px rgba(22,22,17,.10); border-radius:3px; margin-right:4px; }

  .lens-card { border:1px solid var(--fai-border); border-top:3px solid var(--fai-action); }
  .lens-header { background:var(--fai-surface); padding:14px 24px; border-bottom:1px solid var(--fai-border);
                 display:flex; justify-content:space-between; align-items:center; gap:12px; }
  .lens-eyebrow { font-size:11px; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
                  color:var(--fai-action); margin-bottom:2px; }
  .lens-title { font-size:18px; font-weight:600; margin:0; letter-spacing:-.01em; }
  .lens-title .accent { color:var(--fai-action); }
  .lens-meta { font-size:12px; color:var(--fai-text-secondary); text-align:right; }
  .lens-meta .num { font-weight:600; color:var(--fai-text); font-variant-numeric: tabular-nums; }
  .lens-meta .trunc { color:var(--fai-warning); }
  .rowmode { display:inline-flex; border:1px solid var(--fai-border); border-radius:6px; overflow:hidden; }
  .rowmode .rm-btn { border:none; background:var(--fai-surface); font-family:inherit; font-size:12px;
                     font-weight:600; padding:6px 12px; cursor:pointer; color:var(--fai-text-secondary); }
  .rowmode .rm-btn + .rm-btn { border-left:1px solid var(--fai-border); }
  .rowmode .rm-btn.is-active { background:var(--rep-ink); color:var(--rep-on-dark); }
  .lens-controls { background:var(--fai-surface-container); padding:12px 24px; display:flex; align-items:center;
                   gap:12px; flex-wrap:wrap; border-bottom:1px solid var(--fai-border); }
  .lens-controls label { font-size:10px; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
                         color:var(--fai-text-secondary); }
  .lens-controls select { font-family:inherit; font-size:13px; font-weight:500; color:var(--fai-text);
                          background:var(--fai-surface); border:1px solid var(--fai-border);
                          border-radius:6px; padding:6px 10px; cursor:pointer; min-width:240px; }
  .lens-clear { font-family:inherit; font-size:12px; font-weight:500; background:transparent;
                border:1px solid transparent; color:var(--fai-text-secondary); padding:6px 10px;
                border-radius:6px; cursor:pointer; }
  .lens-clear:hover { color:var(--fai-text); border-color:var(--fai-border); background:var(--fai-surface); }
  .lens-metric-strip { display:flex; padding:0 24px; background:var(--fai-surface);
                       border-bottom:1px solid var(--fai-border); }
  .lens-tab { font-family:inherit; font-size:11px; font-weight:600; letter-spacing:.04em; text-transform:uppercase;
              color:var(--fai-text-secondary); background:transparent; padding:12px 16px; border:none;
              border-bottom:2px solid transparent; cursor:pointer; }
  .lens-tab.is-active { color:var(--fai-action); border-bottom-color:var(--fai-action); }
  .lens-tab .tab-value { display:block; font-size:16px; font-weight:700; color:var(--fai-text); margin-top:2px;
                         text-transform:none; letter-spacing:-.01em; font-variant-numeric: tabular-nums; }
  .lens-tab.is-active .tab-value { color:var(--fai-action); }
  .lens-tab .tab-value.na { color:var(--fai-text-tertiary); font-size:14px; }
  .lens-body { padding:22px 24px 26px; }
  .lens-empty { padding:60px 24px; text-align:center; color:var(--fai-text-secondary); font-size:13px; }
  .lens-empty .icon { font-size:32px; opacity:.4; margin-bottom:8px; }
  .lens-cap { font-size:11px; color:var(--fai-text-tertiary); padding:6px 24px 2px; }

  table.patterns { width:100%; border-collapse:collapse; font-size:12px; }
  table.patterns th { text-align:left; font-size:10px; font-weight:600; letter-spacing:.04em;
                      text-transform:uppercase; color:var(--fai-text-tertiary); padding:4px 8px;
                      border-bottom:1px solid var(--fai-border); }
  table.patterns td { padding:5px 8px; border-bottom:1px solid var(--fai-surface-hover); vertical-align:middle; }
  table.patterns td.cnt { width:120px; position:relative; }
  table.patterns .cntbar { display:inline-block; height:10px; background:var(--fai-error); border-radius:2px;
                           vertical-align:middle; margin-right:6px; }
  table.patterns .cntn { font-weight:600; color:var(--fai-text); font-variant-numeric: tabular-nums; }
  table.patterns code { background:var(--fai-surface-hover); padding:1px 5px; border-radius:4px; font-size:11px; }
  table.patterns .blank { color:var(--fai-text-tertiary); }

  .section-title { font-size:20px; font-weight:700; margin:28px 0 6px; letter-spacing:-.02em; }
  .section-hint { font-size:12px; color:var(--fai-text-secondary); margin:0 0 12px; }
  .footer { text-align:center; margin-top:36px; padding-top:18px; border-top:1px solid var(--fai-border);
            font-size:11px; color:var(--fai-text-tertiary); letter-spacing:.04em; }
  .footer .cfg { display:block; margin-top:6px; letter-spacing:0; color:var(--fai-text-tertiary); }
  @media (max-width:900px){ .hero{grid-template-columns:1fr;} .metrics{grid-template-columns:repeat(2,1fr);}
                            .takeaways{grid-template-columns:1fr;} }
</style>
"""

# --------------------------------------------------------------------------- #
# Colour helpers (browser side)                                                #
# --------------------------------------------------------------------------- #

COLOR_JS = r"""
// Continuous accuracy scale on the FurtherAI palette. Stops are weighted toward
// 80-100% because that is where the data lives; the endpoints are sinopia
// (error) and pinehurst (success), with mango carrying the middle.
const _STOPS = [
  [0,   [124, 40, 16]],   // deep sinopia
  [60,  [181, 59, 24]],   // sinopia        (--fai-error)
  [80,  [188,109,  6]],   // mango-1100     (--fai-warning)
  [90,  [251,150,  8]],   // mango          (--fai-brand)
  [95,  [154,167, 62]],   // brand -> success transition
  [98,  [ 54,160,123]],   // success-bg base
  [100, [ 43,120, 93]],   // pinehurst      (--fai-success)
];
const NULL_FILL = '#ecebe5';   // --fai-neutral-bg
function _lerp(a,b,t){ return Math.round(a + (b-a)*t); }
function contColor(pct){
  if (pct === null || pct === undefined) return NULL_FILL;
  pct = Math.max(0, Math.min(100, pct));
  for (let i=0;i<_STOPS.length-1;i++){
    const [p0,c0]=_STOPS[i], [p1,c1]=_STOPS[i+1];
    if (pct>=p0 && pct<=p1){
      const t=(pct-p0)/(p1-p0);
      return `rgb(${_lerp(c0[0],c1[0],t)},${_lerp(c0[1],c1[1],t)},${_lerp(c0[2],c1[2],t)})`;
    }
  }
  return 'rgb(43,120,93)';
}
// Pick black or white text for max contrast on any background (WCAG relative
// luminance). Keeps the mid-scale oranges/limes readable.
function _rgbOf(css){
  if(!css) return [255,255,255];
  let m=css.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
  if(m) return [+m[1],+m[2],+m[3]];
  m=css.match(/^#?([0-9a-f]{6})$/i);
  if(m){ const h=m[1]; return [parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)]; }
  return [255,255,255];
}
function _lin(c){ c/=255; return c<=0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); }
function textOn(color){
  const css = (typeof color==='number') ? contColor(color) : color;
  const [r,g,b]=_rgbOf(css);
  const L = 0.2126*_lin(r) + 0.7152*_lin(g) + 0.0722*_lin(b);
  // Crossover where black beats white for contrast is L~0.20; 0.22 keeps the
  // deep reds/greens on white text and flips oranges/limes to dark.
  return L > 0.22 ? '#161611' : '#fff';
}
function fmtPct(v){ return v===null||v===undefined ? '—' : v.toFixed(1)+'%'; }
// A null percentage never means "data missing" — it means the platform does not
// score this target with a percentage. Counts are still valid.
const UNSCORED='unscored';
function fmtAcc(v){ return v===null||v===undefined ? UNSCORED : v.toFixed(1)+'%'; }
const FONT_BODY="'Wix Madefor Text', -apple-system, 'Segoe UI', sans-serif";
const FONT_LABEL=FONT_BODY;
if (window.Chart){
  Chart.defaults.font.family=FONT_BODY;
  Chart.defaults.color='#161611';
  if (window.ChartDataLabels) Chart.register(ChartDataLabels);
  Chart.defaults.set('plugins.datalabels', {display:false});
}
"""

# --------------------------------------------------------------------------- #
# Behaviour (browser side)                                                     #
# --------------------------------------------------------------------------- #

REPORT_JS = r"""
// Vertical reference line (x = overall accuracy) for the Lens overview.
const refLine = {
  id:'refLine',
  afterDraw(chart, args, opts){
    if(opts.value===undefined) return;
    const {ctx, chartArea:{top,bottom}, scales:{x}} = chart;
    const px = x.getPixelForValue(opts.value);
    ctx.save(); ctx.strokeStyle='#161611'; ctx.setLineDash([4,3]); ctx.lineWidth=1.5;
    ctx.beginPath(); ctx.moveTo(px,top); ctx.lineTo(px,bottom); ctx.stroke();
    if(opts.label){  // label just above the line, flipped inward near the right edge
      const right = px > (x.left + x.right)/2;
      ctx.setLineDash([]); ctx.fillStyle='#161611'; ctx.font="600 10px "+FONT_LABEL;
      ctx.textAlign = right ? 'right' : 'left'; ctx.textBaseline='alphabetic';
      ctx.fillText(opts.label, right ? px-4 : px+4, top-3);
    }
    ctx.restore();
  }
};
Chart.register(refLine);

function esc(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

// ---------- Pareto by step (stacked by error type) + count/mix toggle -------
// Deliberately NOT the accuracy scale: navy / light navy / warm sand read as a
// categorical set and never get confused with a red-to-green score.
const EC = { missed:'#425c86', wrong:'#93a9c8', hall:'#cbb489' };
const CUM_LINE = '#161611';   // a white halo (below) keeps it readable over any bar
const OK_FILL  = '#2b785d';   // pinehurst, for the "Correct" segment in error-mix
let paretoChart=null, paretoMode='volume';
function drawPareto(){
  if(paretoChart) paretoChart.destroy();
  const lab=P.pareto.labels, missed=P.pareto.missed, wrong=P.pareto.wrong, hall=P.pareto.hallucinated;
  const totals=lab.map((_,i)=>missed[i]+wrong[i]+hall[i]);
  const isMix = paretoMode==='mix';
  const norm = (arr)=> arr.map((v,i)=> isMix ? (totals[i]?100*v/totals[i]:0) : v);
  let run=0; const grand=totals.reduce((s,v)=>s+v,0)||1; const cum=totals.map(v=>{run+=v;return 100*run/grand;});
  const labelFor=(name)=>({missed:'Missing',wrong:'Wrong value',hall:'Extra'})[name];
  const mkBar=(name,arr,top)=>({ type:'bar', label:labelFor(name), data:norm(arr), backgroundColor:EC[name],
    stack:'err', yAxisID:'y', order:3, borderWidth:0,
    datalabels:(top && !isMix) ? {display:true,anchor:'end',align:'end',color:'#6f6d64',font:{family:FONT_LABEL,size:9,weight:'600'},
      formatter:(v,ctx)=>{const a=P.pareto.accuracy[ctx.dataIndex];return a==null?'':a.toFixed(0)+'%';}} : {display:false} });
  const datasets=[ mkBar('missed',missed,false), mkBar('wrong',wrong,false), mkBar('hall',hall,true) ];
  if(!isMix){
    // White casing drawn under the line, then the dark line on top -> always
    // legible regardless of which bar segment it crosses.
    datasets.push({ type:'line', label:'__halo', data:cum, borderColor:'#ffffff', backgroundColor:'transparent',
      borderWidth:5, pointRadius:0, tension:.25, yAxisID:'y1', order:2, fill:false, datalabels:{display:false} });
    datasets.push({ type:'line', label:'Cumulative % of errors', data:cum, borderColor:CUM_LINE, backgroundColor:CUM_LINE,
      borderWidth:2.25, pointRadius:3, pointBackgroundColor:CUM_LINE, pointBorderColor:'#ffffff', pointBorderWidth:1.5,
      tension:.25, yAxisID:'y1', order:1, fill:false, datalabels:{display:false} });
  }
  paretoChart=new Chart(document.getElementById('paretoChart'),{ data:{labels:lab, datasets},
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:true,labels:{font:{family:FONT_LABEL,size:11},boxWidth:12,usePointStyle:false,filter:(it)=>it.text!=='__halo'}},
        tooltip:{ backgroundColor:'#161611', padding:10, filter:(it)=>it.dataset.label!=='__halo', callbacks:{
          title:(items)=>lab[items[0].dataIndex],
          label:(ctx)=>{ const i=ctx.dataIndex;
            if(ctx.dataset.type==='line') return `Cumulative: ${cum[i].toFixed(0)}% of all errors`;
            const raw=[missed,wrong,hall][ctx.datasetIndex][i], t=totals[i];
            return `${ctx.dataset.label}: ${raw}${t?` (${(100*raw/t).toFixed(0)}%)`:''}`; },
          afterBody:(items)=>{ const i=items[0].dataIndex; const a=P.pareto.accuracy[i];
            return [`Total ${totals[i]} errors`,
                    a==null ? 'Accuracy: unscored (no metric for this step)' : `Accuracy: ${a.toFixed(1)}%`,
                    `Field verdicts: ${P.pareto.correct[i]} of ${P.pareto.total[i]} agree`]; } }}},
      scales:{ x:{stacked:true, ticks:{autoSkip:false,maxRotation:60,minRotation:60,font:{family:FONT_BODY,size:10}}, grid:{display:false}},
        y:{stacked:true, beginAtZero:true, max:isMix?100:undefined,
           title:{display:true,text:isMix?'Share of step’s errors':'Errors',color:'#6f6d64',font:{family:FONT_LABEL,size:11,weight:'600'}},
           grid:{color:'#f4f4f4'}, ticks:{callback:v=>isMix?v+'%':v}},
        ...(isMix?{}:{ y1:{position:'right',min:0,max:100,title:{display:true,text:'Cumulative %',color:CUM_LINE,font:{family:FONT_LABEL,size:11,weight:'600'}},grid:{display:false},ticks:{callback:v=>v+'%'}} }) } }
  });
}
document.querySelectorAll('#paretoSeg button').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('#paretoSeg button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); paretoMode=b.dataset.m; drawPareto();
}));
if(P.pareto.labels.length) drawPareto();

// ---------- Heatmap (continuous scale) --------------------------------------
const heatBody=document.getElementById('heatBody');
P.heat.rows.forEach((row,ri)=>{
  const tr=document.createElement('tr');
  const lab=document.createElement('td'); lab.className='label'; lab.dataset.block=row;
  // consistency mini-distribution from this step's per-sample cells
  // Only cells the platform actually scored contribute to the spread; a step
  // with no accuracy metric has no distribution to summarise.
  const vals=(P.heat.data[ri]||[]).filter(c=>c!==null&&c!==undefined&&c.accuracy!=null).map(c=>c.accuracy);
  const rowMeta=(P.heatRows||[])[ri]||{};
  // A step the platform scored that produced no rows: its accuracy is real, its
  // counts are empty. Mark it on the label so it reads correctly either way.
  const noRows = rowMeta.noRowModel
    ? ' <span class="rowtag tag-missing" title="The platform scored this step but it produced no rows, so its field counts are empty.">no rows</span>'
    : '';
  lab.innerHTML=`<div class="blk-name">${esc(row)}${noRows}</div>`;
  if(vals.length){
    const n=vals.length, mean=vals.reduce((s,v)=>s+v,0)/n, mn=Math.min(...vals), mx=Math.max(...vals);
    const consistency = mx - mn <= 10 ? 'very consistent' : (mx - mn <= 30 ? 'fairly consistent' : 'inconsistent');
    lab.innerHTML+=`<div class="relbar" title="Consistency across ${n} scored sample${n===1?'':'s'} — ${consistency}. Average ${mean.toFixed(0)}%, ranging from ${mn.toFixed(0)}% (worst sample) to ${mx.toFixed(0)}% (best). Bar spans that range; the tick marks the average.">`+
        `<div class="rng" style="left:${mn}%;width:${Math.max(1.5,mx-mn)}%;background:${contColor(mean)}"></div>`+
        `<div class="mean" style="left:calc(${mean}% - 1.5px);background:${contColor(mean)}"></div></div>`+
      `<div class="rel-cap">avg ${mean.toFixed(0)}% · spans ${mn.toFixed(0)}–${mx.toFixed(0)}%</div>`;
  } else {
    // No scored cells. The step may still carry a real step-level accuracy —
    // e.g. it produced no rows at all — which is not the same as having no metric.
    const meta=rowMeta;
    if(meta.noRowModel){
      lab.innerHTML+=`<div class="rel-cap" title="The platform scored this step but it produced no rows, so there is no per-sample breakdown.">`+
        (meta.accuracy==null?'no rows produced':`step accuracy ${meta.accuracy.toFixed(0)}% · no rows produced`)+`</div>`;
    } else if(meta.accuracy!=null){
      lab.innerHTML+=`<div class="rel-cap" title="A step-level accuracy exists, but there is no per-sample breakdown to compare.">step accuracy ${meta.accuracy.toFixed(0)}% · no per-sample breakdown</div>`;
    } else {
      lab.innerHTML+=`<div class="rel-cap" title="This step is not scored with a percentage; its field counts are still available.">no accuracy metric</div>`;
    }
  }
  tr.appendChild(lab);
  P.heat.cols.forEach((col,ci)=>{
    const cell=(P.heat.data[ri]||[])[ci]; const td=document.createElement('td'); td.className='cell';
    td.dataset.block=row; td.dataset.sub=col;
    if(cell===null||cell===undefined){         // absent — the step never ran
      td.className='cell empty'; td.textContent='–';
      td.style.cursor='default'; td.dataset.empty='1';
      td.title=`${row} was not run for ${col} (no matching documents) — nothing to inspect`; }
    else if(cell.accuracy==null){              // unscored — ran, counts only
      td.className='cell unscored'; td.textContent='n/a'; td.dataset.unscored='1';
      td.title=`${row} × ${col}: no accuracy metric for this step; ${cell.total} fields compared · ${cell.errors} errors · click to inspect`; }
    else {                                     // scored
      const bg=contColor(cell.accuracy); td.style.background=bg; td.style.color=textOn(bg);
      td.textContent=cell.accuracy.toFixed(0);
      td.title=`${row} × ${col}: ${cell.accuracy.toFixed(0)}% accuracy · ${cell.correct} of ${cell.total} fields agree · ${cell.errors} errors · click to inspect`; }
    tr.appendChild(td);
  });
  heatBody.appendChild(tr);
});
(function(){ let s='linear-gradient(to right'; for(let p=0;p<=100;p+=4){ s+=','+contColor(p)+' '+p+'%'; }
  document.getElementById('gradLegend').style.background=s+')'; })();

// ---------- Element handles -------------------------------------------------
const lensBlock=document.getElementById('lensBlock'), lensSub=document.getElementById('lensSub');
const lensTitle=document.getElementById('lensTitle'), lensMeta=document.getElementById('lensMeta');
const lensChartWrap=document.getElementById('lensChartWrap'), lensEmpty=document.getElementById('lensEmpty');
const lensNote=document.getElementById('lensNote');
const lensClear=document.getElementById('lensClear'), lensTabs=document.querySelectorAll('.lens-tab');
const lensFieldSel=document.getElementById('lensFieldSel');
const comboControls=document.getElementById('comboControls'), comboFieldClear=document.getElementById('comboFieldClear');
const comboTitle=document.getElementById('comboTitle'), comboMeta=document.getElementById('comboMeta');
const comboHint=document.getElementById('comboHint'), comboWrap=document.getElementById('comboTableWrap');
const comboFieldWrap=document.getElementById('comboFieldWrap');
const comboTbody=document.getElementById('comboTbody'), comboFilterRow=document.getElementById('comboFilterRow');
const comboBadOnly=document.getElementById('comboBadOnly');
const rowModeToggle=document.getElementById('rowModeToggle');
const overviewToggle=document.getElementById('overviewToggle');
const errMixToggle=document.getElementById('errMixToggle');

let lensChart=null, currentMetric='accuracy';
let lensFocusField=null;   // field filter: {block, field} of the clicked field bar
let errMix=false;          // error-mix mode: by-field bars show error composition
let rowMode='field';       // 'field' = step by its fields (all samples); 'submission' = by sample
let overviewMode='submission';   // Any/Any lens overview — 'submission' | 'step'
let lensCtx={};            // current view, so field-filter clicks skip a rebuild

// ---------- Shared grouped rendering (accordion + Values card) --------------
// group = [step, recordLabel, recordSubid, isRecord, rows]; row = [field, gen, gt, ok]
// row = [field, gen, gt, ok] with an optional 5th element carrying the
// platform's row status: "extra" = a row the AI invented (false positive),
// "missing" = a row it never produced (false negative).
const ROW_TAGS={extra:['tag-extra','extra row'], missing:['tag-missing','missing row']};
function fieldRow(r){
  const ok=r[3];
  const css = ok===null ? 'row-skip' : (ok===1 ? 'row-ok' : 'row-bad');
  const dot = ok===null ? '·' : (ok===1 ? '✓' : '✗');
  const gen = r[1]==='' ? '<span class="blank">—</span>' : `<code>${esc(r[1])}</code>`;
  const gt  = r[2]==='' ? '<span class="blank">—</span>' : `<code>${esc(r[2])}</code>`;
  const t = ROW_TAGS[r[4]];
  const tag = t ? ` <span class="rowtag ${t[0]}">${t[1]}</span>` : '';
  return `<tr class="${css}"><td class="fld"><span class="st">${dot}</span>${esc(r[0])}${tag}</td>`+
    `<td>${gen}</td><td>${gt}</td></tr>`;
}
function renderGroups(tbody, groups, badOnly, showBlock){
  let html='', lastBlock=null, any=false;
  for(const g of groups){
    const rows = badOnly ? g[4].filter(r=>r[3]===0) : g[4];
    if(!rows.length) continue;
    any=true;
    if(showBlock && g[0]!==lastBlock){ html+=`<tr class="grp-block"><td colspan="3">${esc(g[0])}</td></tr>`; lastBlock=g[0]; }
    if(g[3]===1){ // a record header
      const sub = g[2] ? ` <span class="gsub">${esc(g[2])}</span>` : '';
      html+=`<tr class="grp-item"><td colspan="3">${esc(g[1])}${sub}</td></tr>`;
    }
    html += rows.map(fieldRow).join('');
  }
  tbody.innerHTML = any ? html
    : '<tr><td colspan="3" style="color:#969388;padding:12px 2px">Nothing to show.</td></tr>';
}

// ---------- Values card -----------------------------------------------------
function truncNote(s){
  const n=(P.truncated||{})[s];
  return n ? ` · <span class="trunc">${n} correct rows omitted</span>` : '';
}
function renderCombo(b, s, fieldFilter){
  comboFieldWrap.style.display='none';  // hide the error-pattern panel in cell views
  if(!(b && s)){
    comboTitle.textContent='Field-level values';
    comboMeta.innerHTML=''; comboFilterRow.style.display='none';
    comboWrap.style.display='none'; comboHint.style.display='';
    comboHint.innerHTML='<div class="icon">⊹</div>Select a single <b>cell</b> — one step × one sample — in the map above to see its AI-Generated vs. Ground Truth values here.';
    return;
  }
  let groups=(P.claims[s]||[]).filter(g=>g[0]===b);
  if(fieldFilter){ groups=groups.map(g=>[g[0],g[1],g[2],g[3],g[4].filter(r=>r[0]===fieldFilter)]).filter(g=>g[4].length); }
  const allRows=groups.reduce((n,g)=>n+g[4].length,0);
  const bad=groups.reduce((n,g)=>n+g[4].filter(r=>r[3]===0).length,0);
  comboTitle.innerHTML=`<span class="accent">${esc(b)}</span> · <span class="accent">${esc(s)}</span>`+(fieldFilter?` · <span class="accent">${esc(fieldFilter)}</span>`:``);
  comboMeta.innerHTML=`<span class="num">${allRows}</span> ${fieldFilter?'value':'field'}${allRows===1?'':'s'} · <span class="num">${bad}</span> mismatch${bad===1?'':'es'}`+truncNote(s);
  if(!allRows){ comboFilterRow.style.display='none'; comboWrap.style.display='none'; comboHint.style.display='';
    comboHint.innerHTML='<div class="icon">∅</div>'+(fieldFilter?'No rows for this field in this sample.':'No data for this step in this sample.'); return; }
  comboFilterRow.style.display=''; comboHint.style.display='none'; comboWrap.style.display='';
  renderGroups(comboTbody, groups, comboBadOnly.checked, false);
}
function patternsHtml(pats){
  if(!pats.length) return '<div style="color:#969388;padding:8px 2px;font-size:13px">No value-level mismatch patterns for this field (errors here are missing/extra rows, not wrong values).</div>';
  const max=Math.max.apply(null, pats.map(p=>p.n)); const blank='<span class="blank">—</span>';
  const rows=pats.map(p=>'<tr><td><code>'+(p.gen===''?blank:esc(p.gen))+'</code></td><td><code>'+(p.gt===''?blank:esc(p.gt))+'</code></td>'+
    '<td class="cnt"><span class="cntbar" style="width:'+Math.max(2,Math.round(p.n/max*70))+'px"></span><span class="cntn">'+p.n+'</span></td></tr>').join('');
  return '<table class="patterns"><thead><tr><th>AI-Generated</th><th>Ground Truth</th><th>Count</th></tr></thead><tbody>'+rows+'</tbody></table>';
}
function renderComboField(block, field){
  const fd=(P.fieldDetail[block]||{})[field];
  comboFilterRow.style.display='none'; comboWrap.style.display='none';
  comboTitle.innerHTML='<span class="accent">'+esc(block)+'</span> · <span class="accent">'+esc(field)+'</span> — error patterns';
  if(!fd){ comboFieldWrap.style.display='none'; comboHint.style.display=''; comboHint.innerHTML='<div class="icon">∅</div>No data for this field.'; comboMeta.innerHTML=''; return; }
  comboMeta.innerHTML='<span class="num">'+fd.errors+'</span> mismatch'+(fd.errors===1?'':'es')+' · '+fd.correct+'/'+fd.total+' correct';
  comboHint.style.display='none'; comboFieldWrap.style.display='';
  const comp=fd.comp||{};
  const compLine='<div class="lens-cap" style="padding:0 0 10px">Error mix: '+(comp['Missed']||0)+' missing · '+(comp['Wrong value']||0)+' wrong · '+(comp['Hallucinated']||0)+' extra · most common value mismatches below</div>';
  comboFieldWrap.innerHTML=compLine+patternsHtml(fd.patterns);
}
// Decide what the Values card shows, honoring the field filter.
function renderValues(){
  const b=lensCtx.b, s=lensCtx.s, field=(lensFocusField&&lensFocusField.block===b)?lensFocusField.field:null;
  if(field && s){ renderCombo(b, s, field); }            // one cell, filtered to one field
  else if(field){ renderComboField(b, field); }          // a step's field, patterns across samples
  else if(s){ renderCombo(b, s, null); }                 // one cell, all fields
  else if(lensCtx.byField){
    comboFilterRow.style.display='none'; comboWrap.style.display='none'; comboFieldWrap.style.display='none';
    comboTitle.textContent='Field-level values'; comboMeta.innerHTML=''; comboHint.style.display='';
    comboHint.innerHTML='<div class="icon">⊹</div>Click a field bar above (or pick a <b>Field</b>) to see its mismatch patterns here.';
  }
  else { renderCombo(b, s, null); }                      // overview/column -> generic hint
}
comboBadOnly.addEventListener('change',()=>renderValues());

// ---------- Lens ------------------------------------------------------------
function clearSel(){ document.querySelectorAll('.heatmap .is-sel').forEach(e=>e.classList.remove('is-sel')); }
function syncSel(b,s){ clearSel();
  if(b) document.querySelectorAll(`.heatmap td.label[data-block="${CSS.escape(b)}"]`).forEach(e=>e.classList.add('is-sel'));
  if(s) document.querySelectorAll(`.heatmap th.col-head[data-sub="${CSS.escape(s)}"]`).forEach(e=>e.classList.add('is-sel'));
  if(b&&s) document.querySelectorAll(`.heatmap td.cell[data-block="${CSS.escape(b)}"][data-sub="${CSS.escape(s)}"]`).forEach(e=>e.classList.add('is-sel')); }
function buildDS(b,s){
  if(!b&&!s){ const items=(P.overview&&P.overview[overviewMode])||[];
    const noun=overviewMode==='submission'?'sample':'step';
    // Reference line only on the by-sample view.
    const showRef = overviewMode==='submission';
    return {titleHtml:`Overview · accuracy by ${noun} <span style="color:#6f6d64;font-weight:400">· all samples, worst first</span>`,
      metaHtml:`<span class="num">${items.length}</span> ${noun}s · overall accuracy <span class="num">${fmtPct(P.overall.accuracy)}</span> · <span class="num">${P.overall.correct}</span> of <span class="num">${P.overall.total}</span> fields agree`+(showRef?` · dashed line = overall`:``),
      scope:P.overall, items:items, refVal:(showRef?P.overall[currentMetric]:undefined)}; }
  if(b&&s){ const c=(P.cell[b]&&P.cell[b][s])||null;
    if(!c) return {titleHtml:`<span class="accent">${esc(b)}</span> · <span class="accent">${esc(s)}</span>`,metaHtml:'0 fields',scope:null,items:[]};
    return {titleHtml:`<span class="accent">${esc(b)}</span> · <span class="accent">${esc(s)}</span>`,
      metaHtml:`<span class="num">${c.fields.length}</span> fields · <span class="num">${c.errors}</span> err · <span class="num">${c.correct}</span> of <span class="num">${c.total}</span> agree`,
      scope:c, items:c.fields, fieldRows:true, block:b, sub:s}; }
  if(b&&!s){
    if(rowMode==='field'){ const f=P.field[b]; if(!f) return {titleHtml:esc(b),metaHtml:'',scope:null,items:[]};
      return {titleHtml:`<span class="accent">${esc(b)}</span> by field <span style="color:#6f6d64;font-weight:400">· all samples</span>`,
        metaHtml:`<span class="num">${f.fields.length}</span> fields · <span class="num">${f.errors}</span> err · <span class="num">${f.correct}</span> of <span class="num">${f.total}</span> agree`,
        scope:f, items:f.fields, fieldRows:true, byField:true, block:b}; }
    const r=P.row[b]; if(!r) return {titleHtml:esc(b),metaHtml:'',scope:null,items:[]};
    return {titleHtml:`<span class="accent">${esc(b)}</span> across samples`,
      metaHtml:`<span class="num">${r.subs.length}</span> samples · <span class="num">${r.errors}</span> err · <span class="num">${r.correct}</span> of <span class="num">${r.total}</span> agree`,scope:r,items:r.subs}; }
  if(!b&&s){ const c=P.col[s]; if(!c) return {titleHtml:esc(s),metaHtml:'',scope:null,items:[]};
    return {titleHtml:`<span class="accent">${esc(s)}</span> across steps`,
      metaHtml:`<span class="num">${c.blocks.length}</span> steps · <span class="num">${c.errors}</span> err · <span class="num">${c.correct}</span> of <span class="num">${c.total}</span> agree`,scope:c,items:c.blocks}; }
  return null;
}
function updateTabs(scope){ [['accuracy','tabValAccuracy'],['precision','tabValPrecision'],['recall','tabValRecall'],['f1','tabValF1']].forEach(([m,id])=>{
  const el=document.getElementById(id); const v=scope?scope[m]:null;
  if(v===null||v===undefined){ el.textContent='—'; el.classList.add('na');
    el.title='No '+m+' metric for this scope'; }
  else { el.textContent=v.toFixed(1)+'%'; el.classList.remove('na'); el.title=''; } }); }

rowModeToggle.querySelectorAll('.rm-btn').forEach(btn=>btn.addEventListener('click',()=>{
  rowMode=btn.dataset.mode; lensFocusField=null;
  rowModeToggle.querySelectorAll('.rm-btn').forEach(b=>b.classList.toggle('is-active',b===btn));
  renderLens();
}));
overviewToggle.querySelectorAll('.rm-btn').forEach(btn=>btn.addEventListener('click',()=>{
  overviewMode=btn.dataset.mode;
  overviewToggle.querySelectorAll('.rm-btn').forEach(b=>b.classList.toggle('is-active',b===btn));
  renderLens();
}));
errMixToggle.querySelectorAll('.rm-btn').forEach(btn=>btn.addEventListener('click',()=>{
  errMix = btn.dataset.mix==='errors';
  errMixToggle.querySelectorAll('.rm-btn').forEach(b=>b.classList.toggle('is-active',b===btn));
  renderLens();
}));

function renderLens(){
  const b=lensBlock.value||null, s=lensSub.value||null; syncSel(b,s);
  rowModeToggle.style.display=(b&&!s)?'inline-flex':'none';
  overviewToggle.style.display=(!b&&!s)?'inline-flex':'none';
  const ds=buildDS(b,s); const fr=!!(ds&&ds.fieldRows); const byField=!!(ds&&ds.byField);
  errMixToggle.style.display=fr?'inline-flex':'none';
  // Field-filter dropdown — only when the chart rows are fields. Drop a stale
  // filter if its field isn't in this view.
  comboControls.style.display=fr?'':'none';
  if(fr){
    const labels=ds.items.map(i=>i.label);
    if(lensFocusField && (lensFocusField.block!==b || labels.indexOf(lensFocusField.field)<0)) lensFocusField=null;
    lensFieldSel.innerHTML='<option value="">— All fields —</option>'+labels.map(l=>'<option value="'+esc(l)+'"'+((lensFocusField&&lensFocusField.field===l)?' selected':'')+'>'+esc(l)+'</option>').join('');
  } else { lensFocusField=null; }
  lensCtx={b, s, byField, items:(ds&&ds.items)||[]};
  lensTitle.innerHTML=ds.titleHtml; lensMeta.innerHTML=ds.metaHtml; updateTabs(ds.scope);
  if(!ds.items.length){ lensChartWrap.style.display='none'; lensNote.style.display='none'; lensEmpty.style.display='';
    lensEmpty.innerHTML='<div class="icon">∅</div>No fields in this scope.'; if(lensChart){lensChart.destroy();lensChart=null;}
    renderValues(); return; }
  lensChartWrap.style.display=''; lensEmpty.style.display='none'; lensChartWrap.style.height=Math.max(280,32*ds.items.length)+'px';
  // Which axis (if any) a bar click navigates. Field rows never navigate —
  // there a click filters the Values card instead.
  const navTo = fr ? null
    : ((b&&!s&&rowMode==='submission') ? 'sub'
      : ((!b&&s) ? 'block'
        : ((!b&&!s) ? (overviewMode==='submission'?'sub':'block') : null)));
  if(fr && errMix){ drawErrMix(ds.items, {block:ds.block, sub:ds.sub, fieldRows:true}); }
  else { drawLens(ds.items, ds.refVal, {fieldRows:fr, block:ds.block, navTo:navTo}); }
  let note='';
  if(fr && errMix){ note='Each bar = 100% of fields, split correct · missing · wrong · extra. Click a bar (or pick a Field) to filter the values below.'; }
  else if(fr){ note='Click a field bar (or pick a Field) to filter the values below.'; }
  else if(b&&!s&&rowMode==='submission'){ note='Click a sample to drill into its fields for that sample.'; }
  else if(!b&&s){ note='Click a step to drill into its fields for this sample.'; }
  else if(!b&&!s){ note=(overviewMode==='submission'?'Click a sample to filter the Lens to it.':'Click a step to filter the Lens to it.'); }
  lensNote.style.display=note?'':'none'; lensNote.innerHTML=note;
  renderValues();
}
// Which row (category index) was clicked — works even on a 0%-wide bar or on
// the y-axis label, by mapping the click's y-pixel to the category scale.
function rowIndexAt(chart, e, els){
  if(els && els.length) return els[0].index;
  const y = e && (e.y!=null ? e.y : (e.native ? e.native.offsetY : null));
  if(y==null || !chart.scales || !chart.scales.y) return -1;
  const idx = Math.round(chart.scales.y.getValueForPixel(y));
  const n = (chart.data.labels||[]).length;
  return (idx>=0 && idx<n) ? idx : -1;
}
function drawLens(items, refVal, opts){
  opts = opts || {};
  if(lensChart) lensChart.destroy();
  const vals=items.map(i=>i[currentMetric]);
  const hasRef = (typeof refVal==='number' && isFinite(refVal));
  lensChart=new Chart(document.getElementById('lensChart'),{ type:'bar',
    data:{ labels:items.map(i=>i.label), datasets:[{ data:vals.map(v=>v==null?0:v),
      backgroundColor:vals.map(v=>v==null?NULL_FILL:contColor(v)), borderRadius:4, borderSkipped:false }]},
    options:{ indexAxis:'y', responsive:true, maintainAspectRatio:false, animation:{duration:200}, layout:{padding:{right:72,top:14}},
      onClick:(e,els,ch)=>{ const i=rowIndexAt(ch,e,els); if(i<0) return;
        if(opts.navTo){ lensFocusField=null;
          if(opts.navTo==='sub'){ lensSub.value=items[i].label; } else { lensBlock.value=items[i].label; }
          renderLens(); return; }
        if(!opts.fieldRows) return; selectField(opts.block, items[i].label); },
      onHover:(e,els,ch)=>{ if(e.native) e.native.target.style.cursor = (opts.fieldRows||opts.navTo) ? 'pointer' : 'default'; },
      plugins:{ legend:{display:false},
        refLine: hasRef ? {value:refVal, label:'overall '+refVal.toFixed(1)+'%'} : {value:undefined},
        datalabels:{display:true,anchor:'end',align:'end',color:'#6f6d64',font:{family:FONT_LABEL,size:10,weight:'600'},formatter:(v,ctx)=>{const m=items[ctx.dataIndex][currentMetric];return m==null?UNSCORED:m.toFixed(0)+'%';}},
        tooltip:{ backgroundColor:'#161611', padding:10, callbacks:{ label:(ctx)=>{ const i=items[ctx.dataIndex];
          // Accuracy and the field-verdict counts are different statistics —
          // never render one as the ratio behind the other.
          const acc = i.accuracy==null ? 'Accuracy: unscored (no metric for this step)'
                                       : `Accuracy: ${fmtPct(i.accuracy)}`;
          return [acc, `Field verdicts: ${i.correct} of ${i.total} agree`, `Errors: ${i.errors}`,
                  `Precision: ${fmtPct(i.precision)}`, `Recall: ${fmtPct(i.recall)}`, `F1: ${fmtPct(i.f1)}`]; }}}},
      scales:{ x:{min:0,max:100,title:{display:true,text:currentMetric.charAt(0).toUpperCase()+currentMetric.slice(1)+' %',color:'#6f6d64',font:{family:FONT_LABEL,size:11,weight:'600'}},grid:{color:'#f4f4f4'}},
               y:{afterFit:a=>{a.width=280;},ticks:{autoSkip:false,font:{family:FONT_BODY,size:12,weight:'500'}},grid:{display:false}} } }
  });
}
// Error composition (100% stacked). Works for a step's fields (across samples)
// AND for one cell's fields (step × sample) via the right comp source.
function drawErrMix(items, opts){
  opts = opts || {};
  if(lensChart) lensChart.destroy();
  const compOf = (label)=> opts.sub!=null
    ? (((P.cellComp[opts.block]||{})[opts.sub]||{})[label] || {})
    : (((P.fieldDetail[opts.block]||{})[label]||{}).comp || {});
  const seg=items.map(i=>{ const c=compOf(i.label);
    return {t:i.total||0, Correct:i.correct||0, Missing:c['Missed']||0, 'Wrong value':c['Wrong value']||0, Extra:c['Hallucinated']||0}; });
  const COLS=[['Correct',OK_FILL],['Missing',EC.missed],['Wrong value',EC.wrong],['Extra',EC.hall]];
  const pct=(n,t)=> t? n/t*100 : 0;
  lensChart=new Chart(document.getElementById('lensChart'),{ type:'bar',
    data:{ labels:items.map(i=>i.label), datasets:COLS.map(cd=>({label:cd[0], key:cd[0], data:seg.map(s=>pct(s[cd[0]],s.t)), backgroundColor:cd[1], borderWidth:0})) },
    options:{ indexAxis:'y', responsive:true, maintainAspectRatio:false, animation:{duration:200},
      // Horizontal stacked bars: hover/tooltip group along the y (category) axis
      // so the whole row's components show together.
      interaction:{ mode:'index', intersect:false, axis:'y' },
      onClick:(e,els,ch)=>{ if(!opts.fieldRows) return; const i=rowIndexAt(ch,e,els); if(i>=0) selectField(opts.block, items[i].label); },
      onHover:(e,els,ch)=>{ if(e.native) e.native.target.style.cursor = opts.fieldRows ? 'pointer' : 'default'; },
      plugins:{ legend:{display:true, position:'top', labels:{boxWidth:10, boxHeight:10, font:{family:FONT_LABEL,size:11}}},
        datalabels:{display:false},
        tooltip:{ backgroundColor:'#161611', padding:10, mode:'index', intersect:false, axis:'y', callbacks:{
          title:(it)=> it.length? it[0].label : '',
          label:(ctx)=>{ const s=seg[ctx.dataIndex]; const n=s[ctx.dataset.key]; return ctx.dataset.key+': '+n+' ('+pct(n,s.t).toFixed(0)+'%)'; },
          footer:(it)=> it.length? 'Total: '+seg[it[0].dataIndex].t+' fields' : '' }}},
      scales:{ x:{stacked:true,min:0,max:100,title:{display:true,text:'% of fields',color:'#6f6d64',font:{family:FONT_LABEL,size:11,weight:'600'}},grid:{color:'#f4f4f4'}},
               y:{stacked:true,afterFit:a=>{a.width=280;},ticks:{autoSkip:false,font:{family:FONT_BODY,size:12,weight:'500'}},grid:{display:false}} } }
  });
}
// Set/clear the field filter (clicking the same field again clears it).
// No chart rebuild — just the dropdown + Values card.
function selectField(block, field){
  if(lensFocusField && lensFocusField.block===block && lensFocusField.field===field){ lensFocusField=null; }
  else { lensFocusField={block, field}; }
  lensFieldSel.value = lensFocusField ? field : '';
  renderValues();
}

lensTabs.forEach(t=>t.addEventListener('click',()=>{ lensTabs.forEach(x=>x.classList.remove('is-active')); t.classList.add('is-active'); currentMetric=t.dataset.metric; renderLens(); }));
document.querySelectorAll('.heatmap td.label').forEach(e=>e.addEventListener('click',()=>{ lensFocusField=null; lensBlock.value=e.dataset.block; lensSub.value=''; renderLens(); }));
document.querySelectorAll('.heatmap th.col-head').forEach(e=>e.addEventListener('click',()=>{ lensFocusField=null; lensBlock.value=''; lensSub.value=e.dataset.sub; renderLens(); }));
document.querySelectorAll('.heatmap td.cell').forEach(e=>{ if(e.dataset.empty) return; e.addEventListener('click',()=>{ lensFocusField=null; lensBlock.value=e.dataset.block; lensSub.value=e.dataset.sub; renderLens(); }); });
lensBlock.addEventListener('change',()=>{ lensFocusField=null; renderLens(); });
lensSub.addEventListener('change',()=>{ lensFocusField=null; renderLens(); });
lensClear.addEventListener('click',()=>{ lensFocusField=null; lensBlock.value=''; lensSub.value=''; renderLens(); });
lensFieldSel.addEventListener('change',()=>{ const v=lensFieldSel.value; lensFocusField = v ? {block:lensCtx.b, field:v} : null; renderValues(); });
comboFieldClear.addEventListener('click',()=>{ lensFocusField=null; lensFieldSel.value=''; renderValues(); });
// Open on the Any/Any overview so the by-sample ranking is the first thing seen.
lensBlock.value=''; lensSub.value=''; renderLens();

// ---------- Per-sample detail: hydrate rows on demand from P.claims ---------
function fillDetail(det){
  const tb=det.querySelector('table.detail tbody');
  const groups=P.claims[det.dataset.sub]||[];
  renderGroups(tb, groups, det.querySelector('.bad-only').checked, true);
  tb.dataset.rendered='1';
}
document.querySelectorAll('details.sub-block').forEach(det=>{
  det.addEventListener('toggle',()=>{ if(det.open) fillDetail(det); });
  const cb=det.querySelector('.bad-only');
  if(cb) cb.addEventListener('change',()=>{ if(det.open) fillDetail(det); });
});
"""


# --------------------------------------------------------------------------- #
# Document                                                                     #
# --------------------------------------------------------------------------- #


def write(stats, out_path, meta=None, max_rows=_UNSET, full=_UNSET):
    """Render `stats` to a self-contained HTML file at `out_path`.

    Every `meta` key is optional and a missing one falls back to a sensible
    default. Recognised keys:

      title, workflow_name, dataset_name, dataset_id, dataset_url / app_url,
      env, generated_at, source, scoring_note, sample_count,
      record_label_fields, max_rows, full, config (dict) / config_summary (str)

    `max_rows` / `full` are read from `meta` when the caller does not pass them
    explicitly, so a CLI that resolves the budget into `meta` gets the cap it
    asked for. `meta["max_rows"] = None` means "no cap" (i.e. --full)."""
    meta = dict(meta or {})

    # Row budget: an explicit argument wins, otherwise take it from meta.
    if max_rows is _UNSET:
        max_rows = meta.get("max_rows", MAX_ROWS_DEFAULT)
    if full is _UNSET:
        full = bool(meta.get("full", False))
    if max_rows is None:          # the CLI's spelling of --full
        full, max_rows = True, MAX_ROWS_DEFAULT

    # Platform provenance travels either on stats or on meta; read it once, early.
    plat = stats.get("platform") or meta.get("platform") or {}

    dataset_name = meta.get("dataset_name") or ""
    # No workflow_name in API mode — the Test Batch name is the closest label.
    workflow_name = meta.get("workflow_name") or dataset_name or "Workflow"
    title = meta.get("title") or f"{workflow_name} — Accuracy Report"
    dataset_id = meta.get("dataset_id") or ""
    generated_at = meta.get("generated_at") or date.today().isoformat()
    env = meta.get("env") or ""
    # Defaults must describe the pipeline that actually produced these numbers —
    # a methodology note that says "computed locally" over platform-sourced
    # verdicts is worse than no note at all.
    from_platform = bool(plat)
    source = meta.get("source") or ("platform accuracy report" if from_platform
                                    else "local field-level scoring")
    scoring_note = meta.get("scoring_note") or (
        ("Verdicts and accuracy come from the platform's own scored report. The headline "
         "is the platform's metric over classification-emitting steps; the field-verdict "
         "counts tally its per-field match results. They answer different questions and "
         "do not divide into one another.")
        if from_platform else
        ("Every leaf value in the AI-Generated output is compared with Ground Truth. "
         "Long free-text fields are skipped, not scored. This number is computed "
         "here and will not match the platform's headline micro-F1 accuracy.")
    )
    # A partial-scope warning (e.g. --max-samples) leads the note, so the
    # caveat sits above the numbers rather than after them.
    _prefix = (meta.get("scoring_note_prefix") or "").strip()
    if _prefix:
        scoring_note = f"{_prefix} {scoring_note}"
    config_summary = meta.get("config_summary") or ""
    config = meta.get("config") or meta.get("scoring_config") or plat.get("scoring_config") or None
    app_url = meta.get("app_url") or ""
    # The Test Batch link: an explicit dataset_url wins, else build it from the
    # producer's template. Never assembled by hand — the routes are the client's.
    dataset_url = meta.get("dataset_url") or _fill_url(
        meta.get("dataset_url_template"), app_url=app_url, dataset_id=dataset_id)
    exec_url_tpl = meta.get("execution_url_template") or ""

    o = stats.get("overall", {}) or {}
    subs = stats.get("sub_stats", []) or []
    blocks = stats.get("block_stats", []) or []
    per_sub = stats.get("per_submission", []) or []
    heat = stats.get("heatmap", {"rows": [], "cols": [], "data": []}) or {}

    P = build_payload(stats, max_rows=max_rows, full=full,
                      record_label_fields=meta.get("record_label_fields"))
    truncated = P["truncated"]

    esc = html.escape

    # ---- takeaways --------------------------------------------------------
    def _acc_tail(v, dp=0):
        """Accuracy read from the payload — never derived — or the unscored marker."""
        return f"{v:.{dp}f}%" if v is not None else "<i class='uns'>unscored</i>"

    def _step_name(b):
        # A scored step that produced no rows is real, but its counts are empty —
        # say so rather than letting it read as a clean step.
        tag = ("<span class='rowtag tag-missing' title='The platform scored this step "
               "but it produced no rows.'>no rows</span>") if b.get("no_row_model") else ""
        return f"{esc(b.get('block',''))} {tag}" if tag else esc(b.get("block", ""))

    focus_html = "".join(
        f"<div class='tk-item'><span>{_step_name(b)}</span>"
        f"<span class='v'><b>{_int(b.get('errors')):,}</b> errors · {_acc_tail(b.get('accuracy'))}</span></div>"
        for b in sorted(blocks, key=lambda b: -_int(b.get("errors")))[:6]
    ) or "<div class='tk-empty'>No steps scored.</div>"
    subs_html = "".join(
        f"<div class='tk-item'><span>{esc(s.get('submission',''))}</span>"
        f"<span class='v'><b>{_int(s.get('errors')):,}</b> err · {_acc_tail(s.get('accuracy'), 1)}</span></div>"
        for s in subs[:6]
    ) or "<div class='tk-empty'>No samples scored.</div>"

    # ---- global error mix -------------------------------------------------
    tax = stats.get("error_taxonomy") or {}
    tax_total = sum(tax.values())
    tax_sub = (" · overall mix: " + " · ".join(
        f"{PLAIN.get(k, k)} {100 * v / tax_total:.0f}%"
        for k, v in sorted(tax.items(), key=lambda x: -x[1]))) if tax_total else ""

    # ---- selects ----------------------------------------------------------
    def _opt_acc(v):
        return f"{v:.1f}%" if v is not None else "unscored"

    block_options = "".join(
        f'<option value="{esc(b.get("block",""))}">{esc(b.get("block",""))} — '
        f'{_opt_acc(b.get("accuracy"))} · {_int(b.get("errors"))} err</option>'
        for b in sorted(blocks, key=lambda x: -_int(x.get("errors")))
    )
    sub_options = "".join(
        f'<option value="{esc(s.get("submission",""))}">{esc(s.get("submission",""))} — '
        f'{_opt_acc(s.get("accuracy"))}</option>'
        for s in subs
    )
    col_heads = "".join(
        f'<th class="col-head" data-sub="{esc(c)}" title="{esc(c)}">'
        f'<div class="label-rot">{esc(c)}</div></th>'
        for c in heat.get("cols", [])
    )

    exec_ids = (meta.get("execution_ids_by_submission")
                or plat.get("execution_ids_by_submission") or {})

    # ---- per-sample accordion shells --------------------------------------
    # Bodies ship empty (data-rendered="0"); rows hydrate from P.claims on the
    # `toggle` event, which is what keeps a 30k-row DOM off the initial page.
    sub_blocks = []
    # Unscored samples sort last; scored ones stay best-first as before.
    for sub in sorted(per_sub, key=lambda s: (s.get("accuracy") is None,
                                              -_num(s.get("accuracy")))):
        name = sub.get("submission_name", "")
        only_gen = ", ".join(esc(s) for s in sub.get("only_in_generated", [])) or "—"
        only_gt = ", ".join(esc(s) for s in sub.get("only_in_gt", [])) or "—"
        nbad = sum(1 for r in sub.get("results", []) or [] if r.get("match") is False)
        acc = sub.get("accuracy")
        if acc is None:
            badge_cls, badge_txt = "badge-unscored", "unscored"
        else:
            badge_cls = "badge-pass" if acc >= 80 else ("badge-review" if acc >= 50 else "badge-fail")
            badge_txt = f"{acc:.1f}%"
        sid = sub.get("submission_id") or sub.get("sample_id") or ""
        id_meta = f"<span><b>Sample ID:</b> {esc(str(sid))}</span>" if sid else ""
        ex = exec_ids.get(str(sid)) if sid else None
        if ex:
            ex_url = _fill_url(exec_url_tpl, app_url=app_url, execution_id=ex)
            shown = (f'<a href="{esc(ex_url)}" target="_blank" rel="noopener">'
                     f'<code>{esc(str(ex))}</code> ↗</a>') if ex_url else f"<code>{esc(str(ex))}</code>"
            id_meta += f"<span><b>Execution:</b> {shown}</span>"
        drop = truncated.get(name, 0)
        trunc = (f'<div class="trunc-note"><b>{drop:,} matching rows omitted</b> from this file to keep '
                 f'it small. Every mismatch is shown; re-render with <code>--full</code> (or a higher '
                 f'<code>--max-rows</code>) for the complete list.</div>') if drop else ""
        sub_blocks.append(f"""
        <details class="sub-block card" data-sub="{esc(name)}">
          <summary>
            <span class="sum-left"><span class="toggle-icon">&#9654;</span><span class="sum-name">{esc(name)}</span></span>
            <span class="sum-right"><span class="badge {badge_cls}">{badge_txt}</span>
              <span class="sum-counts">{nbad:,} errors · {_int(sub.get('correct')):,} of {_int(sub.get('total')):,} agree</span></span>
          </summary>
          <div class="filter-row"><label><input type="checkbox" class="bad-only" checked> Show mismatches only ({nbad:,})</label></div>
          <div class="detail-body">
            <div class="meta"><span><b>Sample:</b> {esc(name)}</span>{id_meta}
              <span><b>Only in AI-Generated:</b> {only_gen}</span><span><b>Only in Ground Truth:</b> {only_gt}</span></div>
            {trunc}
            <table class="detail"><thead><tr><th>Field</th><th>AI-Generated</th><th>Ground Truth</th></tr></thead>
              <tbody data-rendered="0"></tbody></table>
          </div>
        </details>""")

    # Safe embed: neutralize any "</" that could appear in extracted values.
    payload_json = json.dumps(P, separators=(",", ":"), default=str).replace("</", "<\\/")

    # ---- report health -----------------------------------------------------
    # Anything that changes how the numbers below should be read goes above them:
    # an explicit warning, a stale or partial report, a step that errored and so
    # scored nothing, or samples that were left out.
    notes = []                      # (severity, text) — severity: 'warn' | 'info'
    for w in (meta.get("warnings") or []):
        notes.append(("warn", str(w)))
    if plat.get("is_stale"):
        reason = plat.get("stale_reason")
        notes.append(("warn", "This report is stale — it describes an earlier run"
                              + (f": {reason}" if reason else ".")))
    if plat.get("partial"):
        notes.append(("warn", "Partial report — not every sample or step was scored."))
    st, rst = plat.get("status"), plat.get("run_status")
    if st and st != "generated":
        notes.append(("warn", f"Report status is {st}, not generated."))
    if rst and rst != "completed":
        notes.append(("warn", f"The scored test run is {rst}, not completed."))
    step_errors = plat.get("step_errors") or {}
    if step_errors:
        listed = "; ".join(f"{k}: {v}" for k, v in list(step_errors.items())[:4])
        more = f" (+{len(step_errors) - 4} more)" if len(step_errors) > 4 else ""
        notes.append(("warn", f"{len(step_errors)} step(s) errored and scored nothing — "
                              f"{listed}{more}"))
    n_nogt = len(plat.get("missing_gt_submission_ids") or [])
    if n_nogt:
        notes.append(("info", f"{n_nogt} sample(s) have no Ground Truth and are not scored here."))
    n_norun = len(plat.get("missing_completed_execution_submission_ids") or [])
    if n_norun:
        notes.append(("info", f"{n_norun} sample(s) have no completed run and are not scored here."))
    notes_html = ""
    if notes:
        rows = "".join(f'<li class="{sev}">{esc(txt)}</li>' for sev, txt in notes)
        worst = "warn" if any(sev == "warn" for sev, _ in notes) else "info"
        notes_html = f'<ul class="report-notes {worst}">{rows}</ul>'

    hl = _headline(stats, o)
    # The headline's provenance travels with it — a high score at low coverage
    # means little of the surface was actually labeled (data contract §4.3).
    sub_bits = [b for b in (hl["source"],
                            None if hl["coverage"] is None else f"{hl['coverage']:.0f}% coverage")
                if b]
    headline_sub = esc(" · ".join(sub_bits)) if sub_bits else "platform headline metric"
    excluded_line = ""
    if hl["without"]:
        n = len(hl["without"])
        listed = ", ".join(hl["without"][:12]) + ("…" if n > 12 else "")
        excluded_line = (f'<div class="hero-score-note" title="{esc(listed)}">'
                         f'{n} step{"" if n == 1 else "s"} cannot move this number</div>')

    # The Missing/Wrong/Extra split is only as trustworthy as the labeling behind
    # it — a blank Ground Truth cell may mean "empty" or "never labeled", so say
    # how much of the mix was inferred from blankness rather than a row status.
    caveat = stats.get("taxonomy_caveat") or {}
    caveat_html = ""
    if caveat.get("blank_derived_errors"):
        bd, tot_e = _int(caveat.get("blank_derived_errors")), _int(caveat.get("total_errors"))
        cov_txt = "" if hl["coverage"] is None else f" · Ground Truth coverage {hl['coverage']:.0f}%"
        caveat_html = (f'<div class="mix-caveat" title="{esc(str(caveat.get("note") or ""))}">'
                       f'<b>{bd:,} of {tot_e:,} errors</b> were bucketed from a blank Ground Truth '
                       f'cell rather than an explicit row status{cov_txt} — read the Missing/Extra '
                       f'split against that.</div>')

    n_steps = len(blocks)
    n_samples = _int(o.get("submissions"), _int(meta.get("sample_count"), len(subs)))
    acc_txt = "—" if o.get("accuracy") is None else f"{o['accuracy']:.1f}"
    dataset_chip = (f'<span class="chip">Test Batch <b>{esc(dataset_name)}</b></span>'
                    if dataset_name else "")
    app_chip = (f'<a class="chip chip-link" href="{esc(dataset_url)}" target="_blank" '
                f'rel="noopener">Open in Eval Studio ↗</a>') if dataset_url else ""
    trunc_total = sum(truncated.values())
    trunc_hint = (f" {trunc_total:,} matching rows were omitted across "
                  f"{len(truncated)} sample(s) to keep this file small; every mismatch is "
                  f"included.") if trunc_total else ""
    cfg_line = f'<span class="cfg">{esc(config_summary)}</span>' if config_summary else ""
    if config:
        cfg_json = json.dumps(_jsonable(config), indent=2, sort_keys=True)
        cfg_line += ('<details class="cfgbox"><summary>Scoring configuration \u25be</summary>'
                     f'<pre>{esc(cfg_json)}</pre></details>')
    src_bits = " · ".join(x for x in (f"Source: {source}" if source else "",
                                      f"Environment: {env}" if env else "",
                                      f"Test Batch ID: {dataset_id}" if dataset_id else "")
                          if x)
    src_line = f'<span class="src">{esc(src_bits)}</span>' if src_bits else ""

    head = ("<meta charset='UTF-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>")
    style = (f"<style>{fai_render.font_face_css()}</style>"
             f"<style>{fai_render.tokens_css()}</style>{REPORT_CSS}")
    libs = (f"<script>{fai_render.chartjs_js()}</script>"
            f"<script>{fai_render.datalabels_js()}</script>")

    doc = f"""<!doctype html><html lang="en"><head>
<title>{esc(title)}</title>{head}{style}{libs}</head><body><div class="wrap">
{notes_html}
<div class="hero">
  <div>
    <h1 class="hero-title">{esc(title)}</h1>
    <div class="hero-meta"><b>AI-Generated vs Ground Truth</b> · {_int(o.get('total')):,} fields compared across {n_samples} samples · {n_steps} steps</div>
    <div class="hero-chips">
      <span class="chip">Precision <b>{_pct(o.get('precision'))}</b></span>
      <span class="chip">Recall <b>{_pct(o.get('recall'))}</b></span>
      <span class="chip">F1 <b>{_pct(o.get('f1'))}</b></span>
      {dataset_chip}{app_chip}
    </div>
  </div>
  <div class="hero-score">
    <div class="hero-score-label">Overall accuracy</div>
    <div class="hero-score-value">{acc_txt}<span style="font-size:28px;opacity:.7">%</span></div>
    <div class="hero-score-sub">{headline_sub}</div>
    {excluded_line}
  </div>
</div>

<div class="metrics">
  <div class="metric-card"><div class="metric-label">Samples</div><div class="metric-value">{n_samples}</div><div class="metric-sub">AI-Generated vs. Ground Truth</div></div>
  <div class="metric-card"><div class="metric-label">Field verdicts</div><div class="metric-value ratio">{_int(o.get('correct')):,} <span class="of">of</span> {_int(o.get('total')):,}</div><div class="metric-sub">fields agree with Ground Truth — a count, not the headline metric</div></div>
  <div class="metric-card"><div class="metric-label">Errors to fix</div><div class="metric-value warn">{_int(o.get('errors')):,}</div><div class="metric-sub">Fields that disagree with Ground Truth</div></div>
</div>

<div class="scoring-note"><b>How this is scored.</b> {esc(scoring_note)}{trunc_hint}{src_line}</div>

<div class="takeaways">
  <div class="tk focus">
    <div class="tk-title">Where to focus</div>
    <div class="tk-hint">By error count — fixing these removes the most errors.</div>
    {focus_html}
  </div>
  <div class="tk subs">
    <div class="tk-title">Weakest samples</div>
    <div class="tk-hint">By accuracy. Error counts shown so small samples don't mislead.</div>
    {subs_html}
  </div>
</div>

<div class="card">
  <div class="card-header">
    <div><div class="card-title">Where errors concentrate</div>
      <div style="font-size:12px;color:var(--fai-text-tertiary);margin-top:3px">Errors per step, split by type · line = cumulative share{tax_sub}</div>
      {caveat_html}</div>
    <div class="seg" id="paretoSeg">
      <button data-m="volume" class="on">Count</button>
      <button data-m="mix">Mix %</button>
    </div>
  </div>
  <div class="card-body">
    <div class="chart-wrap" style="height:480px"><canvas id="paretoChart"></canvas></div>
    <div class="legendnote"><b>Missing</b> = the model left it out · <b>Wrong value</b> = extracted the wrong value · <b>Extra</b> = added something not in the documents</div>
  </div>
</div>

<div class="card">
  <div class="card-header"><div class="card-title">Step × sample map</div><div class="card-subtitle"><span style="color:var(--fai-action);font-weight:600">Click any cell, row, or column to break it down below ↓</span></div></div>
  <div class="card-body">
    <div class="heatmap-wrap"><table class="heatmap">
      <thead><tr><th class="corner">Step <span style="font-weight:400;text-transform:none;color:var(--fai-text-tertiary)">+ consistency</span></th>{col_heads}</tr></thead>
      <tbody id="heatBody"></tbody></table></div>
    <div class="hm-legend">
      <div class="hm-legend-grad">
        <div class="grad-legend" id="gradLegend"></div>
        <div class="legend-row"><span>0%</span><span>60%</span><span>80%</span><span>90%</span><span>95%</span><span>100%</span></div>
        <div class="legend-cap">Each cell = the accuracy the platform reports for that step on that sample. It is read from the payload, never recomputed from the field counts.</div>
      </div>
      <div class="hm-legend-notes">
        <div><span class="lg-bar"><span class="lg-rng"></span><span class="lg-tick"></span></span> <b>Consistency bar</b> (left of each row): spans the step's lowest→highest accuracy across samples; the tick marks the average. A wide bar means it works on some samples and fails on others.</div>
        <div><span class="lg-unscored">n/a</span> <b>Ran, but has no accuracy metric</b> — grid and row steps are not scored with a percentage. The field counts are still there: click the cell to inspect them.</div>
        <div><span class="lg-empty">–</span> <b>Didn't run</b> for that sample (no matching documents). Nothing to inspect.</div>
      </div>
    </div>
  </div>
</div>

<div class="card lens-card" id="lensCard">
  <div class="lens-header"><div><div class="lens-eyebrow">Lens</div><h3 class="lens-title" id="lensTitle">Pick a row, column, or cell from the map above</h3></div><div class="lens-meta" id="lensMeta"></div></div>
  <div class="lens-controls">
    <label for="lensBlock">Step</label><select id="lensBlock"><option value="">— Any —</option>{block_options}</select>
    <label for="lensSub">Sample</label><select id="lensSub"><option value="">— Any —</option>{sub_options}</select>
    <span id="rowModeToggle" class="rowmode" style="display:none" title="Break a step down by its fields (pooled across all samples) or by sample">
      <button class="rm-btn is-active" data-mode="field">By field</button>
      <button class="rm-btn" data-mode="submission">By sample</button>
    </span>
    <span id="overviewToggle" class="rowmode" style="display:none" title="Overview: rank every sample, or every step, across the whole test batch">
      <button class="rm-btn is-active" data-mode="submission">By sample</button>
      <button class="rm-btn" data-mode="step">By step</button>
    </span>
    <span id="errMixToggle" class="rowmode" style="display:none" title="Show each field's accuracy, or its error composition (missing / wrong / extra)">
      <button class="rm-btn is-active" data-mix="accuracy">Accuracy</button>
      <button class="rm-btn" data-mix="errors">Error mix</button>
    </span>
    <button class="lens-clear" id="lensClear" style="margin-left:auto">Clear selection</button>
  </div>
  <div class="lens-metric-strip">
    <button class="lens-tab is-active" data-metric="accuracy">Accuracy <span class="tab-value" id="tabValAccuracy">—</span></button>
    <button class="lens-tab" data-metric="precision">Precision <span class="tab-value" id="tabValPrecision">—</span></button>
    <button class="lens-tab" data-metric="recall">Recall <span class="tab-value" id="tabValRecall">—</span></button>
    <button class="lens-tab" data-metric="f1">F1 <span class="tab-value" id="tabValF1">—</span></button>
  </div>
  <div class="lens-body">
    <div id="lensChartWrap" class="chart-wrap" style="height:420px;display:none"><canvas id="lensChart"></canvas></div>
    <div id="lensNote" class="lens-cap" style="display:none"></div>
    <div id="lensEmpty" class="lens-empty"><div class="icon">⊹</div>Click a <b>row</b> (step → by field across all samples), a <b>cell</b> (one step × one sample → by field), or a <b>column</b> (one sample → by step) in the map above.</div>
  </div>
</div>

<div class="card lens-card" id="comboCard">
  <div class="lens-header">
    <div><div class="lens-eyebrow">Values</div><h3 class="lens-title" id="comboTitle">Field-level values</h3></div>
    <div class="lens-meta" id="comboMeta"></div>
  </div>
  <div class="lens-controls" id="comboControls" style="display:none">
    <label for="lensFieldSel">Field</label><select id="lensFieldSel"><option value="">— All fields —</option></select>
    <button class="lens-clear" id="comboFieldClear" style="margin-left:auto">Clear field filter</button>
  </div>
  <div class="filter-row" id="comboFilterRow" style="display:none"><label><input type="checkbox" id="comboBadOnly"> Show mismatches only</label></div>
  <div class="card-body" style="padding-top:8px">
    <div id="comboHint" class="lens-empty"><div class="icon">⊹</div>Select a single <b>cell</b> — one step × one sample — for its AI-Generated vs. Ground Truth values, or open a step and <b>click a field bar</b> for its error patterns.</div>
    <div id="comboTableWrap" style="display:none">
      <table class="detail"><thead><tr><th>Field</th><th>AI-Generated</th><th>Ground Truth</th></tr></thead>
        <tbody id="comboTbody"></tbody></table>
    </div>
    <div id="comboFieldWrap" style="display:none"></div>
  </div>
</div>

<h2 class="section-title">Per-sample detail</h2>
<p class="section-hint">Expand a sample to see its errors. Showing mismatches only — untick to see every field.</p>
{''.join(sub_blocks)}

<div class="footer">FurtherAI · {esc(workflow_name)} · {n_samples} samples · {esc(_when(generated_at))}{cfg_line}</div>

</div>
<script>const P = {payload_json};</script>
<script>{COLOR_JS}</script>
<script>{REPORT_JS}</script>
</body></html>"""

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return out
