#!/usr/bin/env python3
"""Eval Studio accuracy-report bridge — read-only.

The platform has already scored the Test Batch. This module reads its verdicts;
it never re-scores and never generates. Two endpoints, N+2 calls total:

    GET {BASE}/datasets/{ds}/accuracy-report?scoring_mode=dataset   -> 1 call
    GET {BASE}/datasets/{ds}/accuracy-report/submissions/{sid}      -> N calls

Read-only discipline, enforced here:
  - GETs go straight through FaiClient. Never shell out to the eval-studio
    accuracy-report command: on a cache miss it POSTs to generate, which is a
    prod write.
  - status != "generated" raises ReportNotReady. Generating is the user's
    decision, not ours.
  - is_stale is surfaced before any number is quoted.
  - Samples in missing_gt_submission_ids are skipped;
    missing_completed_execution_submission_ids is surfaced as a rerun list.

Size, not call count, is the budget: one live detail payload was 418 KB, so 50
Samples is ~20 MB. iter_sample_bundles() streams — it fetches one detail,
reduces it to the report's own row model, and drops it. No raw detail is ever
accumulated or returned.

See references/data_contract.md for the payload shapes. stdlib only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from fai_client import FaiClient, FaiError  # noqa: E402
from scoring import is_missing  # noqa: E402  — sentinel test only, never a verdict

BASE = "/api/v1/evaluation"
LOOP_PREFIX = "__fai_eval_loop_doc__:"
ITEMS_LABEL = "(items)"

# Cell/wrapper metadata keys dropped by the lazy raw-cell helpers at the bottom.
META_KEYS = frozenset({
    "confidence", "confidence_score", "confidence_reason", "citations",
    "thinking_steps", "metadata", "schema", "user_documents", "preview_value",
})


class ReportNotReady(RuntimeError):
    """No usable accuracy report exists. Generating one is a platform WRITE and
    must be the user's explicit decision."""


# --------------------------------------------------------------------------- #
# Summary                                                                      #
# --------------------------------------------------------------------------- #

def fetch_summary(client: FaiClient, dataset_id: str, scoring_mode: str = "dataset") -> dict:
    """The cached batch report. One call. Carries every aggregate, the scored
    Sample roster, the scoring config and the execution IDs."""
    try:
        data = client.get(f"{BASE}/datasets/{dataset_id}/accuracy-report",
                          params={"scoring_mode": scoring_mode})
    except FaiError as e:
        if e.status in (400, 404):
            raise ReportNotReady(
                f"No accuracy report exists for Test Batch {dataset_id} (HTTP {e.status}). "
                f"Generating one is a platform write — ask the user to run it in Eval Studio "
                f"or via `fai:eval-studio accuracy-report {dataset_id} --generate`, then re-run."
            )
        raise
    return data or {}


def check_report_ready(summary: dict, dataset_id: str) -> List[str]:
    """Raise unless the report is usable. Returns warnings to print first."""
    status = summary.get("status")
    if status != "generated":
        reason = summary.get("failure_reason") or summary.get("stale_reason") or ""
        raise ReportNotReady(
            f"Accuracy report for Test Batch {dataset_id} is {status!r}"
            f"{' — ' + str(reason) if reason else ''}. This skill is read-only and will not "
            f"generate it. Generating is a platform write: ask the user to confirm, then run "
            f"`fai:eval-studio accuracy-report {dataset_id} --generate`."
        )
    warnings = []
    if summary.get("is_stale"):
        warnings.append(
            f"STALE: this report no longer matches the Test Batch config"
            f"{' (' + str(summary['stale_reason']) + ')' if summary.get('stale_reason') else ''}. "
            f"Every number below describes the anchor run, not the current setup.")
    if summary.get("partial"):
        warnings.append("PARTIAL: not every Sample in the batch was scored.")
    missing_gt = summary.get("missing_gt_submission_ids") or []
    if missing_gt:
        warnings.append(f"{len(missing_gt)} Sample(s) have no Ground Truth and are excluded.")
    missing_exec = summary.get("missing_completed_execution_submission_ids") or []
    if missing_exec:
        warnings.append(f"{len(missing_exec)} Sample(s) have no completed run — rerun them "
                        f"to include them: {', '.join(missing_exec[:5])}"
                        f"{' ...' if len(missing_exec) > 5 else ''}")
    return warnings


def scored_samples(summary: dict) -> List[dict]:
    """The scored roster straight from the summary: [{submission_id,
    submission_name, accuracy}]. No list-submissions call is needed."""
    roster = summary.get("submissions") or []
    skip = set(summary.get("missing_gt_submission_ids") or ())
    return [s for s in roster if s.get("submission_id") not in skip]


# --------------------------------------------------------------------------- #
# Step identity — loop keys never reach the report                             #
# --------------------------------------------------------------------------- #

def step_identity(step_key: str, payload: Optional[dict]) -> dict:
    """Readable identity for a step key, which may be an opaque per-document
    loop key. The key itself is read-only: pass it back verbatim, never
    construct or normalize one.

    Returns {label, group, definition_step_name, order, document, is_loop}.
    """
    payload = payload or {}
    loop = ((payload.get("diagnostics") or {}).get("loop_iteration")) or {}
    is_loop = bool(loop) or step_key.startswith(LOOP_PREFIX)
    if not is_loop:
        return {"label": step_key, "group": None, "definition_step_name": step_key,
                "order": None, "document": None, "is_loop": False}

    definition = (loop.get("definition_step_name")
                  or payload.get("display_name")
                  or payload.get("original_step_name")
                  or "per-document")
    document = (loop.get("document") or {}).get("filename")
    label = f"{definition} — {document}" if document else definition
    if not document and loop.get("iteration_label"):
        label = f"{definition} — {loop['iteration_label']}"
    return {
        "label": label,
        "group": loop.get("loop_parent") or definition,
        "definition_step_name": definition,
        "order": loop.get("iteration_index"),
        "document": document,
        "is_loop": True,
    }


def build_step_labels(summary: dict) -> Dict[str, dict]:
    """Map every step key in the summary to its readable identity, once, so the
    per-Sample details reuse the same labels."""
    return {key: step_identity(key, payload)
            for key, payload in (summary.get("steps") or {}).items()}


# --------------------------------------------------------------------------- #
# Detail -> the report's row model                                             #
# --------------------------------------------------------------------------- #

def _leaf_kind(match: Optional[bool], predicted, expected,
               row_status: Optional[str] = None) -> str:
    """Bucket an already-decided verdict for the error taxonomy.

    This never decides whether a value is correct — the platform did that. It
    only says which KIND of error a FALSE verdict is, so Missing / Wrong value /
    Extra can be told apart. Blank-derived buckets are ambiguous when Ground
    Truth is sparsely labeled (a blank expected value may mean "empty" or "never
    labeled"), which is why row status wins whenever it exists.
    """
    if match:
        return "match"
    if row_status == "extra":
        return "extra-in-gen"
    if row_status == "missing":
        return "missing-in-gen"
    pred_blank, exp_blank = is_missing(predicted), is_missing(expected)
    if exp_blank and not pred_blank:
        return "extra-in-gen"
    if pred_blank and not exp_blank:
        return "missing-in-gen"
    return "mismatch"


def _value_claim(block: str, path: str, field_label: str, field_key: str,
                 record: dict, step_type: str, kind: str,
                 row_status: Optional[str] = None) -> dict:
    match = record.get("match")
    predicted, expected = record.get("predicted"), record.get("expected")
    return {
        "path": path, "kind": kind, "match": match, "category": None,
        "gen": predicted, "gt": expected,
        "leaf_kind": _leaf_kind(match, predicted, expected, row_status),
        "block": block, "field_key": field_key, "field_label": field_label,
        "step_type": step_type, "row_status": row_status,
    }


def _row_claim(block: str, path: str, field_label: str, item: dict,
               step_type: str) -> dict:
    """One identity claim per aligned row. status absent means the platform
    paired the row; 'extra' is a row the AI produced that the Answers do not
    have; 'missing' is the reverse."""
    status = item.get("status")
    if status == "extra":
        category, match, leaf = "FP", False, "extra-in-gen"
    elif status == "missing":
        category, match, leaf = "FN", False, "missing-in-gen"
    else:
        category, match, leaf = "TP", True, "identity-match"
    return {
        "path": path, "kind": "identity", "match": match, "category": category,
        "gen": item.get("preview_value_predicted"),
        "gt": item.get("preview_value_expected"),
        "leaf_kind": leaf,
        "block": block, "field_key": "", "field_label": field_label,
        "step_type": step_type,
    }


def reduce_detail(detail: dict, labels: Dict[str, dict]) -> dict:
    """Turn one ~400 KB detail payload into the report's row model.

    Returns a bundle in the same shape the offline path produces, plus the
    platform-supplied accuracies that must never be recomputed:
    step_accuracy (per step, authoritative for a heatmap cell), step_errors,
    steps_without_fields, platform_accuracy.
    """
    claims: List[dict] = []
    step_accuracy: Dict[str, Optional[float]] = {}
    step_types: Dict[str, str] = {}
    step_errors: Dict[str, str] = {}
    steps_without_fields: List[str] = []

    for key, payload in (detail.get("steps") or {}).items():
        payload = payload or {}
        ident = labels.get(key) or step_identity(key, payload)
        block = ident["label"]

        if "error" in payload:
            step_errors[block] = str(payload.get("error"))
            continue

        stype = payload.get("type") or "key_value"
        step_types[block] = stype
        step_accuracy[block] = payload.get("accuracy")

        if stype == "grid":
            items = payload.get("items") or []
            if not items:
                # Normal for SOV/custom-shaped steps: scored, but no row model.
                steps_without_fields.append(block)
                continue
            for item in items:
                row_index = item.get("row_index")
                row_path = f"{block}[{row_index}]"
                claims.append(_row_claim(block, row_path, ITEMS_LABEL, item, "grid"))
                status = item.get("status")
                for col, record in (item.get("fields") or {}).items():
                    if not isinstance(record, dict) or "match" not in record:
                        continue
                    claims.append(_value_claim(
                        block, f"{row_path}.{col}", col, col, record, "grid", "aux", status))
            continue

        fields = payload.get("fields") or {}
        if not fields:
            steps_without_fields.append(block)
            continue
        for fname, record in fields.items():
            if not isinstance(record, dict):
                continue
            if record.get("type") == "array":
                for item in record.get("items") or []:
                    row_index = item.get("row_index")
                    row_path = f"{block}.{fname}[{row_index}]"
                    claims.append(_row_claim(block, row_path, fname, item, "array"))
                    status = item.get("status")
                    for sub, sub_record in (item.get("fields") or {}).items():
                        if not isinstance(sub_record, dict) or "match" not in sub_record:
                            continue
                        claims.append(_value_claim(
                            block, f"{row_path}.{sub}", fname, fname,
                            sub_record, "array", "aux", status))
                continue
            if "match" not in record:
                continue
            claims.append(_value_claim(
                block, f"{block}.{fname}", fname, fname, record, "key_value", "scalar"))

    return {
        "submission_id": detail.get("submission_id", ""),
        "submission_name": detail.get("submission_name") or detail.get("submission_id") or "(unnamed)",
        "gen_file": "", "gt_file": "",
        "claims": claims,
        "only_in_generated": [], "only_in_gt": [],
        "step_accuracy": step_accuracy,
        "step_types": step_types,
        "step_errors": step_errors,
        "steps_without_fields": sorted(steps_without_fields),
        "platform_accuracy": detail.get("accuracy"),
        "platform_accuracy_source": detail.get("accuracy_source"),
        "platform_field_macro_accuracy": detail.get("field_macro_accuracy"),
    }


def iter_sample_bundles(client: FaiClient, dataset_id: str, summary: dict,
                        scoring_mode: str = "dataset",
                        max_samples: Optional[int] = None,
                        progress: Optional[Callable[[str], None]] = None
                        ) -> Iterator[dict]:
    """Stream one reduced bundle per scored Sample.

    Each ~400 KB detail is fetched, reduced and dropped inside the loop, so at
    most one raw payload is alive at a time and none is ever returned.
    """
    labels = build_step_labels(summary)
    roster = scored_samples(summary)
    if max_samples:
        roster = roster[:max_samples]
    total = len(roster)
    for i, entry in enumerate(roster, 1):
        sid = entry.get("submission_id")
        name = entry.get("submission_name") or sid
        try:
            detail = client.get(
                f"{BASE}/datasets/{dataset_id}/accuracy-report/submissions/{sid}",
                params={"scoring_mode": scoring_mode})
        except FaiError as e:
            if progress:
                progress(f"[{i}/{total}] {name}  SKIPPED — detail read failed: {e}")
            continue
        bundle = reduce_detail(detail or {}, labels)
        del detail                                    # drop the raw payload
        bundle["roster_accuracy"] = entry.get("accuracy")
        if progress:
            progress(f"[{i}/{total}] {name}  steps:{len(bundle['step_accuracy'])} "
                     f"rows:{len(bundle['claims'])}"
                     + (f"  !{len(bundle['step_errors'])} errored step(s)"
                        if bundle["step_errors"] else ""))
        yield bundle


# --------------------------------------------------------------------------- #
# Coverage (--check) — one call                                                #
# --------------------------------------------------------------------------- #

def coverage(summary: dict) -> dict:
    """Everything --check needs, from the summary alone."""
    headline = summary.get("headline") or {}
    labels = build_step_labels(summary)
    steps = []
    for key, payload in (summary.get("steps") or {}).items():
        payload = payload or {}
        steps.append({
            "step": labels[key]["label"],
            "type": payload.get("type") or "key_value",
            "accuracy": payload.get("accuracy"),
            "accuracy_source": payload.get("accuracy_source"),
            "threshold": payload.get("accuracy_threshold_percent"),
            "status": payload.get("accuracy_status"),
            "scored_fields": payload.get("scored_field_count"),
            "omitted_fields": payload.get("omitted_field_count"),
            "evaluations": payload.get("step_score_evaluations"),
        })
    steps.sort(key=lambda s: (s["accuracy"] if s["accuracy"] is not None else 0))
    return {
        "dataset_id": summary.get("dataset_id"),
        "dataset_name": summary.get("dataset_name"),
        "status": summary.get("status"),
        "is_stale": bool(summary.get("is_stale")),
        "partial": bool(summary.get("partial")),
        "run_id": summary.get("evaluation_run_id"),
        "run_status": summary.get("evaluation_run_status"),
        "overall_accuracy": summary.get("overall_accuracy"),
        "overall_accuracy_source": summary.get("overall_accuracy_source"),
        "coverage": headline.get("coverage"),
        "steps_without_counts": headline.get("steps_without_counts") or [],
        "samples": scored_samples(summary),
        "missing_gt": summary.get("missing_gt_submission_ids") or [],
        "missing_execution": summary.get("missing_completed_execution_submission_ids") or [],
        "steps": steps,
    }


# --------------------------------------------------------------------------- #
# Lazy raw-cell helpers — NOT part of the report path                          #
# --------------------------------------------------------------------------- #
# side-by-side and gt/read are O(samples x steps) and carry no verdict, so they
# are never swept. They exist for a single Sample + step the user drilled into,
# where the raw cells (confidence scores, the real schema) add something the
# report cannot show.

def _extract_raw_values(value):
    if value is None:
        return None
    if isinstance(value, dict) and "value" in value:
        return _extract_raw_values(value["value"])
    if isinstance(value, dict):
        return {k: _extract_raw_values(v) for k, v in value.items() if k not in META_KEYS}
    if isinstance(value, list):
        return [_extract_raw_values(v) for v in value]
    return value


def _is_empty_row(row) -> bool:
    if row is None:
        return True
    if isinstance(row, dict):
        return all(_is_empty_row(v) for v in row.values()) if row else True
    if isinstance(row, list):
        return all(_is_empty_row(v) for v in row) if row else True
    if isinstance(row, str):
        return row.strip() == ""
    return False


def _paged(client: FaiClient, path: str, step: str, data_key: str, page: int = 1000):
    cursor, rows, kv, dtype = 0, [], None, None
    while True:
        d = client.get(path, params={"step_name": step, "cursor": cursor, "limit": page}) or {}
        dtype = d.get("display_type")
        data = d.get(data_key)
        if dtype == "key_value":
            kv = data
            break
        if isinstance(data, list):
            rows.extend(data)
        if not (d.get("pagination") or {}).get("has_more"):
            break
        cursor += page
    return dtype, kv, rows


def raw_ground_truth(client: FaiClient, dataset_id: str, sid: str, step: str):
    """Ground Truth cells for ONE Sample + step. Pass a loop step key verbatim."""
    dtype, kv, rows = _paged(
        client, f"{BASE}/datasets/{dataset_id}/submissions/{sid}/gt/read", step, "data")
    return _extract_raw_values(kv if dtype == "key_value" else rows)


def raw_generated(client: FaiClient, dataset_id: str, sid: str, step: str):
    """AI-Generated cells for ONE Sample + step, padding rows stripped (the
    output side is padded to match Ground Truth column length; Ground Truth is
    not)."""
    dtype, kv, rows = _paged(
        client, f"{BASE}/datasets/{dataset_id}/submissions/{sid}/side-by-side",
        step, "output_data")
    if dtype == "key_value":
        return _extract_raw_values(kv)
    return [r for r in (_extract_raw_values(r) for r in rows) if not _is_empty_row(r)]


def resolve_dataset_for_workflow(client: FaiClient, workflow_id: str) -> List[dict]:
    """Test Batches pinned to a workflow. The caller disambiguates."""
    data = client.get(f"{BASE}/datasets", params={"workflow_id": workflow_id})
    if isinstance(data, dict):
        data = data.get("datasets") or data.get("items") or []
    return data or []


def get_dataset(client: FaiClient, dataset_id: str) -> dict:
    return client.get(f"{BASE}/datasets/{dataset_id}") or {}
