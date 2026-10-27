#!/usr/bin/env python3
"""Roll scored rows up into the `stats` dict the renderer consumes.

Two entry points, one output contract:

  compute_from_api(summary, bundles, step_labels)   PRIMARY. Rows carry the
      platform's own verdicts; every accuracy percentage is read from the
      payload, never recomputed. See references/data_contract.md §8.
  compute(bundles, config)                          OFFLINE (--from-dir). Rows
      and accuracies both come from the local scorer in scoring.py.

Both funnel into _rollup(), so the two paths emit a byte-identical shape. They
differ only in an AccuracySource, which answers "what is the accuracy of this
scope" — counted locally offline, read from the payload on the API path.

Why an accuracy is never recomputed on the API path: the platform's scorer is a
threshold ladder (levenshtein at 0.85, date, boolean-string, null-equivalence,
currency), not string equality, and its grid accuracy is not a cell count —
recounting diverged by up to 85 points on a single step. A report that mixes a
payload percentage with a locally counted one shows two numbers that disagree,
and the counted one is the wrong one.

Counts (correct / total / errors) ARE derived on both paths. They count the
platform's verdicts; they are not an accuracy and must not be rendered as one.

stdlib only.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scoring import (  # noqa: E402
    DEFAULT_CONFIG,
    ScoringConfig,
    aggregate,
    aggregate_payload,
    collect_v3,
    is_missing,
)

# Error taxonomy — three buckets with opposite root causes.
ETYPES = ["Missed", "Wrong value", "Hallucinated"]

ITEMS_LABEL = "(items)"

# Step types whose per-scope verdict counts may also be expressed as a
# percentage. Grid steps are excluded: the platform's grid accuracy is not a
# cell count, so a counted percentage would contradict it.
COUNTABLE_STEP_TYPES = frozenset({"key_value", "array", ""})


def error_type(claim: dict) -> str:
    """Classify a FALSE verdict as Missed (recall) / Wrong value / Hallucinated.

    Never decides correctness — only which kind of error an already-false
    verdict is.
    """
    lk = claim["leaf_kind"]
    if lk == "missing-in-gen":
        return "Missed"
    if lk == "extra-in-gen":
        return "Hallucinated"
    if lk == "mismatch":
        return "Wrong value"
    if lk == "missing-mismatch":
        if is_missing(claim.get("gen")):
            return "Missed"
        if is_missing(claim.get("gt")):
            return "Hallucinated"
        return "Wrong value"
    return "Wrong value"


def _pct(value: Optional[float], default: Optional[float] = None) -> Optional[float]:
    return (value * 100) if value is not None else default


def _sort_key(row: dict):
    """Worst first, denser scopes ahead of thinner ones — but a scope with no
    accuracy metric at all sorts LAST. Ranking an unscored row as the worst one
    is a claim the payload never made."""
    acc = row.get("accuracy")
    return (acc is None, acc if acc is not None else 0, -row["total"])


# --------------------------------------------------------------------------- #
# Accuracy sources                                                             #
# --------------------------------------------------------------------------- #

class CountedAccuracy:
    """Offline path: every accuracy is the local scorer's correct / total."""

    countable_only = False

    def overall(self, agg: dict) -> Optional[float]:
        return _pct(agg.get("accuracy"))

    def sample(self, name: str, agg: dict) -> Optional[float]:
        return _pct(agg.get("accuracy"))

    def step(self, block: str, agg: dict) -> Optional[float]:
        return _pct(agg.get("accuracy"))

    def cell(self, block: str, sample: str, agg: dict) -> Optional[float]:
        return _pct(agg.get("accuracy"))

    def step_field(self, block: str, label: str, agg: dict,
                   step_type: str = "") -> Optional[float]:
        return _pct(agg.get("accuracy"))

    def cell_field(self, block: str, sample: str, label: str, agg: dict,
                   step_type: str = "") -> Optional[float]:
        return _pct(agg.get("accuracy"))


class PlatformAccuracy:
    """API path: every accuracy is read from the accuracy-report payloads.

    A scope the payload does not describe reports None rather than a number the
    platform never published. The one exception is row-structure scopes (the
    "(items)" pseudo-field), where matched vs extra/missing rows are structural
    facts the payload states directly.
    """

    countable_only = True

    def __init__(self, summary: dict, bundles: Sequence[dict],
                 step_labels: Dict[str, dict]):
        self._overall = summary.get("overall_accuracy")
        # step label -> summary step payload
        self._steps: Dict[str, dict] = {}
        for key, payload in (summary.get("steps") or {}).items():
            label = (step_labels.get(key) or {}).get("label", key)
            self._steps[label] = payload or {}
        # sample name -> platform accuracy; (block, sample) -> platform accuracy
        self._samples: Dict[str, Optional[float]] = {}
        self._cells: Dict[tuple, Optional[float]] = {}
        for b in bundles:
            name = b["submission_name"]
            self._samples[name] = (b.get("platform_accuracy")
                                   if b.get("platform_accuracy") is not None
                                   else b.get("roster_accuracy"))
            for block, acc in (b.get("step_accuracy") or {}).items():
                self._cells[(block, name)] = acc

    def overall(self, agg: dict) -> Optional[float]:
        return self._overall

    def sample(self, name: str, agg: dict) -> Optional[float]:
        return self._samples.get(name)

    def step(self, block: str, agg: dict) -> Optional[float]:
        return self._steps.get(block, {}).get("accuracy")

    def cell(self, block: str, sample: str, agg: dict) -> Optional[float]:
        return self._cells.get((block, sample))

    def step_field(self, block: str, label: str, agg: dict,
                   step_type: str = "") -> Optional[float]:
        if label == ITEMS_LABEL:
            # Row presence: matched vs extra/missing is stated by the payload.
            return _pct(agg["accuracy"])
        rec = (self._steps.get(block, {}).get("fields") or {}).get(label)
        if isinstance(rec, dict) and rec.get("accuracy") is not None:
            return rec["accuracy"]
        return _pct(agg["accuracy"]) if step_type in COUNTABLE_STEP_TYPES else None

    def cell_field(self, block: str, sample: str, label: str, agg: dict,
                   step_type: str = "") -> Optional[float]:
        # The payload publishes per-field accuracy for the whole batch, never
        # for one field of one Sample. Counting is a valid substitute on
        # key-value steps, where verdict counts reconcile with the platform's
        # step accuracy exactly; on grid steps it does not, so those report None.
        if label == ITEMS_LABEL:
            return _pct(agg["accuracy"])
        return _pct(agg["accuracy"]) if step_type in COUNTABLE_STEP_TYPES else None


# --------------------------------------------------------------------------- #
# Rollup                                                                       #
# --------------------------------------------------------------------------- #

def _scope_step_type(claims: Sequence[dict]) -> str:
    """The step type shared by every row in a scope, or "mixed"."""
    kinds = {c.get("step_type", "") for c in claims}
    return kinds.pop() if len(kinds) == 1 else "mixed"


def _labeled(claims: Sequence[dict], label: str, accuracy: Optional[float]) -> dict:
    payload = aggregate_payload(claims)
    payload["label"] = label
    payload["errors"] = payload["total"] - payload["correct"]
    payload["accuracy"] = accuracy
    return payload


def _rollup(per_submission: List[dict], src, extra_blocks: Optional[Dict[str, dict]] = None,
            overall_overrides: Optional[dict] = None) -> dict:
    """Build the stats contract from per-Sample bundles that already carry rows."""
    all_claims: List[dict] = []
    for b in per_submission:
        all_claims.extend(b["claims"])

    overall = aggregate(all_claims)

    # ---- per sample, worst first ----------------------------------------- #
    sub_stats = []
    for b in per_submission:
        a = aggregate(b["results"])
        sub_stats.append({
            "submission": b["submission_name"],
            "correct": a["correct"], "total": a["total"],
            "errors": a["total"] - a["correct"],
            "accuracy": src.sample(b["submission_name"], a),
            "precision": _pct(a["precision"]),
            "recall": _pct(a["recall"]),
            "f1": _pct(a["f1"]),
        })
    sub_stats.sort(key=lambda x: (x["accuracy"] is None,
                                  x["accuracy"] if x["accuracy"] is not None else 0))

    # ---- per step --------------------------------------------------------- #
    by_block_sub = defaultdict(lambda: defaultdict(list))
    for c in all_claims:
        by_block_sub[c["block"]][c["submission"]].append(c)

    block_stats = []
    for block, by_sub in by_block_sub.items():
        cell_pcts, pooled, scored_subs = [], [], 0
        for sub, cls in by_sub.items():
            a = aggregate(cls)
            if a["total"] > 0:
                scored_subs += 1
                cell_acc = src.cell(block, sub, a)
                if cell_acc is not None:
                    cell_pcts.append(cell_acc)
            pooled.extend(cls)
        if not scored_subs:
            continue
        agg = aggregate(pooled)
        block_stats.append({
            "block": block,
            # Mean of the per-Sample accuracies. Shown beside the pooled/step
            # number: when they diverge, one dense Sample is carrying the step.
            # None, not 0.0, when no Sample of this step carries an accuracy —
            # a mean of nothing is not zero.
            "mean_of_ratios": (sum(cell_pcts) / len(cell_pcts)) if cell_pcts else None,
            "accuracy": src.step(block, agg),
            "submissions": scored_subs,
            "correct": agg["correct"], "total": agg["total"],
            "errors": agg["total"] - agg["correct"],
            "precision": _pct(agg["precision"]),
            "recall": _pct(agg["recall"]),
            "f1": _pct(agg["f1"]),
        })

    # Steps the platform scored that produced no row model at all (SOV/custom
    # shapes). Dropping them would silently hide a scored step.
    for block, meta in (extra_blocks or {}).items():
        if block in by_block_sub:
            continue
        block_stats.append({
            "block": block,
            "mean_of_ratios": meta.get("accuracy"),
            "accuracy": meta.get("accuracy"),
            "submissions": meta.get("submissions", 0),
            "correct": 0, "total": 0, "errors": 0,
            "precision": None, "recall": None, "f1": None,
            "no_row_model": True,
        })
    block_stats.sort(key=lambda x: (x["accuracy"] is None,
                                    x["accuracy"] if x["accuracy"] is not None else 0))

    # ---- error composition ------------------------------------------------ #
    block_comp = defaultdict(lambda: {e: 0 for e in ETYPES})
    global_comp = {e: 0 for e in ETYPES}
    blank_derived = 0
    for c in all_claims:
        if c["match"] is False:
            et = error_type(c)
            block_comp[c["block"]][et] += 1
            global_comp[et] += 1
            # Bucketed from a blank value rather than from a row status. A blank
            # Ground Truth cell may mean "the answer is empty" or "never
            # labeled" — the payload cannot tell them apart — so these lean
            # toward Extra on a sparsely labeled batch. Read with coverage.
            if c.get("row_status") is None and c["leaf_kind"] in (
                    "extra-in-gen", "missing-in-gen") and c.get("kind") != "identity":
                blank_derived += 1
    for b in block_stats:
        comp = block_comp[b["block"]]
        b["composition"] = comp
        b["dominant"] = max(comp, key=comp.get) if b["errors"] else None

    # ---- flat (step . field) rollup --------------------------------------- #
    by_field = defaultdict(list)
    for c in all_claims:
        key = (c["block"], c["field_label"])
        by_field[key].append(c)
    field_stats = []
    for (block, label), cls in by_field.items():
        a = aggregate(cls)
        display = block if label == ITEMS_LABEL else f"{block} · {label}"
        field_stats.append({
            "field": display, "correct": a["correct"], "total": a["total"],
            "errors": a["total"] - a["correct"],
            "accuracy": src.step_field(block, label, a, _scope_step_type(cls)),
        })

    # ---- heatmap ---------------------------------------------------------- #
    heatmap_rows = [b["block"] for b in block_stats]
    heatmap_cols = [s["submission"] for s in sub_stats]
    heatmap_data = []
    for block in heatmap_rows:
        row = []
        for sub in heatmap_cols:
            cls = by_block_sub[block].get(sub, [])
            a = aggregate(cls) if cls else {"total": 0, "correct": 0, "accuracy": None}
            acc = src.cell(block, sub, a)
            if a["total"] == 0 and acc is None:
                row.append(None)         # step not scored for that Sample
            else:
                row.append({"correct": a["correct"], "total": a["total"],
                            "errors": a["total"] - a["correct"], "accuracy": acc})
        heatmap_data.append(row)

    # ---- drill-down lenses ------------------------------------------------ #
    cell_payload: Dict[str, dict] = {}
    for block, by_sub in by_block_sub.items():
        cell_payload[block] = {}
        for sub, cls in by_sub.items():
            a = aggregate_payload(cls)
            a["accuracy"] = src.cell(block, sub, aggregate(cls))
            fb = defaultdict(list)
            for c in cls:
                fb[c["field_label"]].append(c)
            fields = [_labeled(fcls, fl, src.cell_field(block, sub, fl, aggregate(fcls),
                                                       _scope_step_type(fcls)))
                      for fl, fcls in fb.items()]
            fields.sort(key=_sort_key)
            cell_payload[block][sub] = {**a, "errors": a["total"] - a["correct"],
                                        "fields": fields}

    row_payload: Dict[str, dict] = {}
    field_payload: Dict[str, dict] = {}
    for block, by_sub in by_block_sub.items():
        all_cls = [c for cls in by_sub.values() for c in cls]
        a = aggregate_payload(all_cls)
        a["accuracy"] = src.step(block, aggregate(all_cls))
        subs = [_labeled(cls, sub, src.cell(block, sub, aggregate(cls)))
                for sub, cls in by_sub.items()]
        subs.sort(key=_sort_key)
        row_payload[block] = {**a, "errors": a["total"] - a["correct"], "subs": subs}

        fb = defaultdict(list)
        for c in all_cls:
            fb[c["field_label"]].append(c)
        flds = [_labeled(fcls, fl, src.step_field(block, fl, aggregate(fcls),
                                                  _scope_step_type(fcls)))
                for fl, fcls in fb.items()]
        flds.sort(key=_sort_key)
        field_payload[block] = {**a, "errors": a["total"] - a["correct"], "fields": flds}

    by_sub_block = defaultdict(lambda: defaultdict(list))
    for c in all_claims:
        by_sub_block[c["submission"]][c["block"]].append(c)
    col_payload: Dict[str, dict] = {}
    for sub, by_block in by_sub_block.items():
        all_cls = [c for cls in by_block.values() for c in cls]
        a = aggregate_payload(all_cls)
        a["accuracy"] = src.sample(sub, aggregate(all_cls))
        blocks = [_labeled(cls, block, src.cell(block, sub, aggregate(cls)))
                  for block, cls in by_block.items()]
        blocks.sort(key=_sort_key)
        col_payload[sub] = {**a, "errors": a["total"] - a["correct"], "blocks": blocks}

    overall_block = {
        "submissions": len(per_submission),
        "total": overall["total"], "correct": overall["correct"],
        "errors": overall["total"] - overall["correct"],
        "accuracy": src.overall(overall),
        "precision": _pct(overall["precision"]),
        "recall": _pct(overall["recall"]),
        "f1": _pct(overall["f1"]),
    }
    overall_block.update(overall_overrides or {})

    return {
        "overall": overall_block,
        "error_taxonomy": global_comp,
        "sub_stats": sub_stats,
        "block_stats": block_stats,
        "field_stats": field_stats,
        "heatmap": {"rows": heatmap_rows, "cols": heatmap_cols, "data": heatmap_data},
        "cell": cell_payload, "row": row_payload, "col": col_payload,
        "field": field_payload,
        "per_submission": per_submission,
        "taxonomy_caveat": {
            "blank_derived_errors": blank_derived,
            "total_errors": sum(global_comp.values()),
            "note": ("Errors bucketed from a blank value rather than from an explicit "
                     "row status. A blank Ground Truth cell may mean the answer is empty "
                     "or that it was never labeled; read this against coverage before "
                     "trusting the Missing/Extra split."),
        },
    }


# --------------------------------------------------------------------------- #
# Entry point — API (primary)                                                  #
# --------------------------------------------------------------------------- #

def compute_from_api(summary: dict, bundles: Iterable[dict],
                     step_labels: Optional[Dict[str, dict]] = None) -> dict:
    """Build stats from the platform's own verdicts.

    `bundles` is the stream from studio_fetch.iter_sample_bundles() — already
    reduced to rows, no raw payload. `summary` is the batch accuracy report.
    """
    step_labels = step_labels or {}
    per_submission = []
    for b in bundles:
        for c in b["claims"]:
            c["submission"] = b["submission_name"]
        a = aggregate(b["claims"])
        b.update(
            accuracy=(b.get("platform_accuracy")
                      if b.get("platform_accuracy") is not None
                      else b.get("roster_accuracy")),
            correct=a["correct"], total=a["total"],
            tp=a["tp"], fp=a["fp"], fn=a["fn"],
            results=b["claims"],       # alias, not a copy
        )
        per_submission.append(b)

    src = PlatformAccuracy(summary, per_submission, step_labels)

    # Steps the platform scored that yielded no rows on any Sample.
    scored_without_rows: Dict[str, dict] = {}
    seen_blocks = {c["block"] for b in per_submission for c in b["claims"]}
    for key, payload in (summary.get("steps") or {}).items():
        label = (step_labels.get(key) or {}).get("label", key)
        if label in seen_blocks:
            continue
        n = sum(1 for b in per_submission if label in (b.get("step_accuracy") or {}))
        scored_without_rows[label] = {"accuracy": (payload or {}).get("accuracy"),
                                      "submissions": n}

    headline = summary.get("headline") or {}
    stats = _rollup(
        per_submission, src, extra_blocks=scored_without_rows,
        overall_overrides={
            # The headline the Eval Studio UI shows, with its provenance.
            "accuracy": summary.get("overall_accuracy"),
            "accuracy_source": summary.get("overall_accuracy_source"),
            "precision": _pct(headline.get("precision")),
            "recall": _pct(headline.get("recall")),
            "f1": _pct(headline.get("f1")),
            "coverage": headline.get("coverage"),
            # The renderer reads overall.headline first for coverage and the
            # steps excluded from the metric.
            "headline": headline,
        },
    )

    step_errors: Dict[str, List[str]] = defaultdict(list)
    for b in per_submission:
        for block, msg in (b.get("step_errors") or {}).items():
            step_errors[block].append(f"{b['submission_name']}: {msg}")

    stats["platform"] = {
        "scoring_source": "platform accuracy report",
        "dataset_id": summary.get("dataset_id"),
        "dataset_name": summary.get("dataset_name"),
        "status": summary.get("status"),
        "is_stale": bool(summary.get("is_stale")),
        "stale_reason": summary.get("stale_reason"),
        "partial": bool(summary.get("partial")),
        "run_id": summary.get("evaluation_run_id"),
        "run_status": summary.get("evaluation_run_status"),
        "overall_accuracy": summary.get("overall_accuracy"),
        "overall_accuracy_source": summary.get("overall_accuracy_source"),
        "field_macro_accuracy": summary.get("field_macro_accuracy"),
        "headline": headline,
        "views": summary.get("views"),
        "precision_recall": summary.get("precision_recall"),
        "scoring_config": summary.get("scoring_config"),
        "report_schema_version": summary.get("report_schema_version"),
        "execution_ids_by_submission": summary.get("execution_ids_by_submission") or {},
        "missing_gt_submission_ids": summary.get("missing_gt_submission_ids") or [],
        "missing_completed_execution_submission_ids":
            summary.get("missing_completed_execution_submission_ids") or [],
        "steps_without_counts": headline.get("steps_without_counts") or [],
        "steps_without_row_model": sorted(scored_without_rows),
        "step_errors": dict(step_errors),
        "verdicts": {"total": stats["overall"]["total"],
                     "correct": stats["overall"]["correct"],
                     "errors": stats["overall"]["errors"]},
    }
    return stats


# --------------------------------------------------------------------------- #
# Entry point — offline (--from-dir)                                           #
# --------------------------------------------------------------------------- #

def compute(bundles: Sequence[dict], config: ScoringConfig = DEFAULT_CONFIG) -> dict:
    """Score two folders of JSON locally and roll them up.

    Used only by --from-dir, where there is no platform report to read. These
    numbers come from the local scorer in scoring.py and will differ from the
    platform's on formatting-only differences.
    """
    per_submission = []
    for sample in bundles:
        bundle = collect_v3(sample, config)
        for c in bundle["claims"]:
            c["submission"] = bundle["submission_name"]
            c.setdefault("step_type", "")
        a = aggregate(bundle["claims"])
        bundle.update(
            accuracy=_pct(a["accuracy"]),
            correct=a["correct"], total=a["total"],
            tp=a["tp"], fp=a["fp"], fn=a["fn"],
            results=bundle["claims"],
        )
        per_submission.append(bundle)

    stats = _rollup(per_submission, CountedAccuracy())
    stats["platform"] = {
        "scoring_source": "local field-level scoring (--from-dir)",
        "status": None, "is_stale": False, "partial": False,
        "overall_accuracy": stats["overall"]["accuracy"],
        "overall_accuracy_source": "local_field_verdicts",
        "verdicts": {"total": stats["overall"]["total"],
                     "correct": stats["overall"]["correct"],
                     "errors": stats["overall"]["errors"]},
    }
    return stats
