#!/usr/bin/env python3
"""Eval Studio CLI for the fai plugin.

Test Batches (datasets), Samples (submissions), Answers (ground truth), test
runs, and accuracy reports — all through the platform API via FaiClient.

Every command takes --env / --account / --org-id. See SKILL.md for task-level
guidance; run with -h for flags.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_client import FaiClient, FaiError  # noqa: E402
from fai_accounts import (  # noqa: E402
    AccountError,
    default_env,
    resolve_org_id,
    resolve_workflow_id,
)

BASE = "/api/v1/evaluation"
OBJECT_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
RUN_TERMINAL = {"completed", "failed", "cancelled", "terminated"}
REPORT_TERMINAL = {"generated", "failed"}
LOOP_DOC_PREFIX = "__fai_eval_loop_doc__:"

MICRO_F1_CAVEAT = (
    "! Headline is micro-F1 over classification-emitting steps only (Extract/ExtractRows).\n"
    "! SOV/function/custom steps emit no counts and CANNOT move the headline.\n"
    "! Gate those steps on per-step accuracy + meets_accuracy_threshold below.\n"
    "! Read coverage with the headline: high F1 on low coverage means little was labeled."
)


def fail(message: str, code: int = 1) -> "NoReturn":  # noqa: F821
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


def require_object_id(value: str, label: str) -> str:
    if not OBJECT_ID_RE.fullmatch(value or ""):
        fail(f"{label} {value!r} is not a 24-char hex ObjectId "
             f"(dataset/submission/run IDs come from /eval/<id> URLs or list commands)")
    return value


def require_uuid(value: str, label: str) -> str:
    if not UUID_RE.fullmatch(value or ""):
        fail(f"{label} {value!r} is not a UUID")
    return value


def load_json_arg(value: str, label: str):
    text = value
    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        if not path.exists():
            fail(f"{label}: file not found: {path}")
        text = path.read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        fail(f"{label} is not valid JSON: {e}")


# --------------------------------------------------------------------- client

def build_client(args) -> FaiClient:
    env = args.env or default_env()
    org_id = args.org_id
    if org_id:
        require_uuid(org_id, "--org-id")
    elif args.account:
        org_id = resolve_org_id(args.account, env)
    if not org_id:
        fail("no org context: pass --account <slug> (from ~/.fai/accounts.json) or --org-id <uuid>")
    return FaiClient(env=env, org_id=org_id, skill_name="eval-studio")


def resolve_workflow(args, ref: str) -> str:
    if UUID_RE.fullmatch(ref or ""):
        return ref
    if not args.account:
        fail(f"workflow {ref!r} is not a UUID; resolving a workflow name needs --account")
    env = args.env or default_env()
    return resolve_workflow_id(args.account, ref, env)


# ----------------------------------------------------------------- validation

def validate_selectors(scored: dict, selected_steps) -> None:
    if not isinstance(scored, dict):
        fail("scored_fields_by_step must be a JSON object: {step name: [field selectors]}")
    for step, fields in scored.items():
        if selected_steps and step not in selected_steps:
            fail(f"scored_fields_by_step key {step!r} is not in selected_steps")
        if (not isinstance(fields, list) or not fields
                or not all(isinstance(f, str) and f.strip() for f in fields)):
            fail(f"scored_fields_by_step[{step!r}] must be a non-empty list of field selector strings")
        clean = [f.strip() for f in fields]
        for f in clean:
            if any(c in f for c in "[]*$"):
                fail(f"scored_fields_by_step[{step!r}]: {f!r} looks like JSONPath. "
                     f"Use schema dot paths (quote_options.carrier), never quote_options[*].carrier.")
        for parent in clean:
            for child in clean:
                if parent != child and child.startswith(parent + "."):
                    fail(f"scored_fields_by_step[{step!r}]: cannot mix parent {parent!r} "
                         f"and child {child!r} — pick one level.")


def validate_row_match_keys(keys: dict, selected_steps) -> None:
    if not isinstance(keys, dict):
        fail("grid_row_match_keys must be a JSON object: {grid step name: [field names]}")
    for step, fields in keys.items():
        if selected_steps and step not in selected_steps:
            fail(f"grid_row_match_keys key {step!r} is not in selected_steps")
        if (not isinstance(fields, list) or not fields
                or not all(isinstance(f, str) and f.strip() for f in fields)):
            fail(f"grid_row_match_keys[{step!r}] must be a non-empty list of exact field names")


def validate_thresholds(body: dict) -> None:
    default = body.get("default_step_accuracy_threshold")
    if default is not None and not (isinstance(default, (int, float)) and 0.0 <= default <= 1.0):
        fail("default_step_accuracy_threshold must be a float from 0.0 to 1.0")
    per_step = body.get("step_accuracy_thresholds_by_step")
    if per_step is not None:
        if not isinstance(per_step, dict):
            fail("step_accuracy_thresholds_by_step must be a JSON object: {step name: float}")
        selected = body.get("selected_steps")
        for step, value in per_step.items():
            if selected and step not in selected:
                fail(f"step_accuracy_thresholds_by_step key {step!r} is not in selected_steps")
            if not (isinstance(value, (int, float)) and 0.0 <= value <= 1.0):
                fail(f"step_accuracy_thresholds_by_step[{step!r}] must be from 0.0 to 1.0")


def validate_dataset_body(body: dict, creating: bool) -> None:
    if creating:
        if not body.get("name"):
            fail("dataset name is required (--name)")
        require_uuid(body.get("workflow_id") or "", "workflow_id")
        steps = body.get("selected_steps")
        if not isinstance(steps, list) or not steps or not all(isinstance(s, str) and s.strip() for s in steps):
            fail("selected_steps must be a non-empty list of exact step names "
                 "(--selected-step, repeatable; copy names from runnable-steps)")
    dtype = body.get("dataset_type")
    if dtype is not None and dtype not in ("live", "gold", "regression"):
        fail("dataset_type must be live, gold, or regression")
    pin = body.get("pinned_workflow_version")
    if pin is not None and (not isinstance(pin, int) or pin < 1):
        fail("pinned_workflow_version must be a published version number >= 1")
    selected = body.get("selected_steps")
    if body.get("scored_fields_by_step") is not None:
        validate_selectors(body["scored_fields_by_step"], selected)
    if body.get("grid_row_match_keys") is not None:
        validate_row_match_keys(body["grid_row_match_keys"], selected)
    validate_thresholds(body)


# ------------------------------------------------------------------- printing

def dump_json(data, out: str = None) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    if out:
        path = Path(out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n")
        print(f"written: {path}")
    else:
        print(text)


def print_table(headers, rows) -> None:
    if not rows:
        print("(none)")
        return
    cells = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    for row in cells:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def fmt_num(value) -> str:
    if isinstance(value, float):
        return f"{value:.1f}"
    return "" if value is None else str(value)


def fmt_ratio(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.3f}"
    return "" if value is None else str(value)


def print_dataset(client: FaiClient, ds: dict) -> None:
    print(f"Test Batch (dataset): {ds.get('name')}")
    print(f"  dataset_id: {ds.get('id')}")
    print(f"  type: {ds.get('dataset_type')}")
    print(f"  workflow_id: {ds.get('workflow_id')}")
    pin = ds.get("pinned_workflow_version")
    latest = ds.get("latest_workflow_version")
    note = ""
    if latest is not None and pin is not None and not ds.get("is_pinned_to_latest", pin == latest):
        note = f"  (latest published: {latest} — repin available)"
    print(f"  pinned_workflow_version: {pin}{note}")
    print(f"  selected_steps: {', '.join(ds.get('selected_steps') or []) or '(none)'}")
    scored = ds.get("scored_fields_by_step") or {}
    print(f"  scored_fields_by_step: {json.dumps(scored) if scored else '(all fields)'}")
    grid = ds.get("grid_row_match_keys") or {}
    if grid:
        print(f"  grid_row_match_keys: {json.dumps(grid)}")
    print(f"  default_step_accuracy_threshold: {ds.get('default_step_accuracy_threshold')}")
    per_step = ds.get("step_accuracy_thresholds_by_step") or {}
    if per_step:
        print(f"  step_accuracy_thresholds_by_step: {json.dumps(per_step)}")
    print(f"  samples: {ds.get('submission_count')} (with answers: {ds.get('submissions_with_gt')})")
    if ds.get("last_run_accuracy") is not None:
        print(f"  last_run_accuracy: {ds['last_run_accuracy']}")
    if ds.get("id"):
        print(f"  url: {client.dataset_url(ds['id'])}")


# ----------------------------------------------------------- dataset commands

def cmd_list_datasets(args) -> None:
    client = build_client(args)
    params = {}
    if args.workflow:
        params["workflow_id"] = resolve_workflow(args, args.workflow)
    if args.dataset_type:
        params["dataset_type"] = args.dataset_type
    data = client.get(f"{BASE}/datasets", params=params or None)
    if args.json:
        dump_json(data)
        return
    datasets = data if isinstance(data, list) else (data or {}).get("datasets") or []
    rows = [[d.get("id"), d.get("name"), d.get("dataset_type"),
             d.get("pinned_workflow_version"), d.get("submission_count"),
             d.get("submissions_with_gt"), d.get("workflow_id")] for d in datasets]
    print(f"test batches: {len(rows)}")
    print_table(["DATASET_ID", "NAME", "TYPE", "PIN", "SAMPLES", "WITH_GT", "WORKFLOW_ID"], rows)


def cmd_get_dataset(args) -> None:
    client = build_client(args)
    require_object_id(args.dataset_id, "dataset_id")
    ds = client.get(f"{BASE}/datasets/{args.dataset_id}")
    if args.json:
        dump_json(ds)
    else:
        print_dataset(client, ds)


def build_dataset_body(args, creating: bool) -> dict:
    if args.body:
        body = load_json_arg(args.body, "--body")
        if not isinstance(body, dict):
            fail("--body must be a JSON object")
    else:
        body = {}
        if args.name is not None:
            body["name"] = args.name
        if args.description is not None:
            body["description"] = args.description
        if args.dataset_type is not None:
            body["dataset_type"] = args.dataset_type
        if getattr(args, "workflow", None):
            body["workflow_id"] = resolve_workflow(args, args.workflow)
        if args.pinned_workflow_version is not None:
            body["pinned_workflow_version"] = args.pinned_workflow_version
        if args.selected_step:
            body["selected_steps"] = args.selected_step
        if args.scored_fields_by_step is not None:
            body["scored_fields_by_step"] = load_json_arg(
                args.scored_fields_by_step, "--scored-fields-by-step")
        if args.grid_row_match_keys is not None:
            body["grid_row_match_keys"] = load_json_arg(
                args.grid_row_match_keys, "--grid-row-match-keys")
        if args.default_step_accuracy_threshold is not None:
            body["default_step_accuracy_threshold"] = args.default_step_accuracy_threshold
        if args.step_accuracy_thresholds_by_step is not None:
            body["step_accuracy_thresholds_by_step"] = load_json_arg(
                args.step_accuracy_thresholds_by_step, "--step-accuracy-thresholds-by-step")
    if not creating and not body:
        fail("nothing to update: pass at least one field flag or --body")
    validate_dataset_body(body, creating)
    return body


def cmd_create_dataset(args) -> None:
    body = build_dataset_body(args, creating=True)
    client = build_client(args)
    ds = client.post(f"{BASE}/datasets", body)
    print("created")
    print_dataset(client, ds)


def cmd_update_dataset(args) -> None:
    require_object_id(args.dataset_id, "dataset_id")
    body = build_dataset_body(args, creating=False)
    client = build_client(args)
    ds = client.patch(f"{BASE}/datasets/{args.dataset_id}", body)
    print("updated (cached accuracy reports for this batch are now stale — regenerate)")
    print_dataset(client, ds)


def cmd_repin(args) -> None:
    require_object_id(args.dataset_id, "dataset_id")
    client = build_client(args)
    body = {"selected_steps": args.selected_step} if args.selected_step else {}
    ds = client.post(f"{BASE}/datasets/{args.dataset_id}/repin", body)
    print("repinned to latest published version")
    print_dataset(client, ds)


def cmd_runnable_steps(args) -> None:
    client = build_client(args)
    if args.dataset:
        require_object_id(args.dataset, "--dataset")
        data = client.get(f"{BASE}/datasets/{args.dataset}/runnable-steps")
    elif args.workflow:
        wf = resolve_workflow(args, args.workflow)
        data = client.get(f"{BASE}/workflows/{wf}/runnable-steps",
                          params={"version": args.version})
    else:
        fail("pass --dataset <dataset-id> or --workflow <uuid|name>")
    if args.json:
        dump_json(data)
        return
    steps = (data or {}).get("steps") or []
    rows = [[s.get("step_name"), s.get("step_type"),
             "yes" if s.get("is_extract") else "no",
             "yes" if s.get("has_gt") else "no"] for s in steps]
    print(f"runnable steps: {len(rows)} (use step_name verbatim in selected_steps)")
    print_table(["STEP_NAME", "STEP_TYPE", "IS_EXTRACT", "HAS_GT"], rows)


# -------------------------------------------------------------------- samples

def cmd_list_submissions(args) -> None:
    client = build_client(args)
    require_object_id(args.dataset_id, "dataset_id")
    data = client.get(f"{BASE}/datasets/{args.dataset_id}/submissions")
    if args.json:
        dump_json(data)
        return
    subs = data if isinstance(data, list) else (data or {}).get("submissions") or []
    rows = [[s.get("id"), s.get("submission_name"), s.get("input_document_count"),
             "yes" if s.get("has_gt") else "no", s.get("status"),
             s.get("workflow_execution_id") or ""] for s in subs]
    print(f"samples: {len(rows)}")
    print_table(["SUBMISSION_ID", "NAME", "DOCS", "ANSWERS", "STATUS", "LAST_EXECUTION"], rows)


def cmd_copy_submissions(args) -> None:
    client = build_client(args)
    require_object_id(args.source, "--source")
    require_object_id(args.target, "--target")
    for sid in args.submission_id:
        require_object_id(sid, "--submission-id")
    body = {
        "source_dataset_id": args.source,
        "submission_ids": args.submission_id,
        "move": bool(args.move),
    }
    resp = client.post(f"{BASE}/datasets/{args.target}/submissions/copy", body)
    if args.json:
        dump_json(resp)
        return
    copied = (resp or {}).get("copied_submissions") or []
    verb = "moved" if (resp or {}).get("moved") else "copied"
    print(f"{verb} {len(copied)} sample(s) from {args.source} to {args.target}")
    rows = [[c.get("source_submission_id"), c.get("target_submission_id"),
             c.get("submission_name"), ", ".join(c.get("gt_steps_copied") or [])]
            for c in copied]
    print_table(["SOURCE_ID", "TARGET_ID", "NAME", "ANSWER_STEPS_COPIED"], rows)
    removed = (resp or {}).get("source_submission_ids_removed") or []
    if removed:
        print(f"unlinked from source: {len(removed)}")


def cmd_remove_submission(args) -> None:
    client = build_client(args)
    require_object_id(args.dataset_id, "dataset_id")
    require_object_id(args.submission_id, "submission_id")
    client.delete(f"{BASE}/datasets/{args.dataset_id}/submissions/{args.submission_id}")
    print(f"sample {args.submission_id} removed from test batch {args.dataset_id}")
    print("note: this unlinks the sample from the batch; underlying documents/answers may persist elsewhere")


# --------------------------------------------------------------------- answers

def gt_base(args) -> str:
    require_object_id(args.dataset_id, "dataset_id")
    require_object_id(args.submission_id, "submission_id")
    return f"{BASE}/datasets/{args.dataset_id}/submissions/{args.submission_id}"


def cmd_gt_template(args) -> None:
    client = build_client(args)
    require_object_id(args.dataset_id, "dataset_id")
    suffix = "gt-template" if args.meta else "gt-template/content"
    data = client.get(f"{BASE}/datasets/{args.dataset_id}/{suffix}")
    dump_json(data, args.out)


def cmd_gt_read(args) -> None:
    client = build_client(args)
    base = gt_base(args)
    if args.step:
        data = client.get(f"{base}/gt/read", params={
            "step_name": args.step, "cursor": args.cursor, "limit": args.limit})
    else:
        data = client.get(f"{base}/gt/content")
    dump_json(data, args.out)


def cmd_gt_set_cells(args) -> None:
    client = build_client(args)
    base = gt_base(args)
    updates = load_json_arg(args.updates, "--updates")
    if isinstance(updates, dict) and "updates" in updates:
        updates = updates["updates"]
    if not isinstance(updates, list) or not updates:
        fail("--updates must be a non-empty JSON list of "
             '{"field_name": ..., "row_index": N, "new_value": ...} (or {"updates": [...]})')
    for i, u in enumerate(updates):
        if not isinstance(u, dict) or "field_name" not in u or "row_index" not in u or "new_value" not in u:
            fail(f"--updates[{i}] must have field_name, row_index, and new_value")
    resp = client.patch(f"{base}/gt/cells", {"updates": updates},
                        params={"step_name": args.step})
    print(f"patched {len(updates)} answer cell(s) on step {args.step!r}")
    if args.json:
        dump_json(resp)


def cmd_gt_add_row(args) -> None:
    client = build_client(args)
    base = gt_base(args)
    resp = client.post(f"{base}/gt/rows",
                       {"row_index": args.row_index, "position": args.position},
                       params={"step_name": args.step})
    print(f"inserted answer row {args.position} index {args.row_index} on step {args.step!r}")
    if args.json:
        dump_json(resp)


def cmd_gt_delete_row(args) -> None:
    client = build_client(args)
    base = gt_base(args)
    resp = client.delete(f"{base}/gt/rows/{args.row_index}",
                         params={"step_name": args.step})
    print(f"deleted answer row {args.row_index} on step {args.step!r}")
    if args.json:
        dump_json(resp)


def cmd_copy_output_to_gt(args) -> None:
    client = build_client(args)
    base = gt_base(args)
    resp = client.post(f"{base}/copy-output-to-gt", params={"step_name": args.step})
    print(f"copied AI-Generated output into Answers for step {args.step!r}")
    print("review the copied answers — unreviewed copies score the model against itself")
    if args.json:
        dump_json(resp)


# ----------------------------------------------------------------------- runs

def poll_run(client: FaiClient, dataset_id: str, run_id, timeout: int, poll: int) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        params = {"run_id": run_id} if run_id else None
        status_resp = client.get(f"{BASE}/datasets/{dataset_id}/run-status", params=params)
        status = (status_resp or {}).get("status")
        subs = (status_resp or {}).get("submissions") or []
        done = sum(1 for s in subs if (s.get("status") or "").lower() in RUN_TERMINAL)
        print(f"  run status={status} samples terminal {done}/{len(subs)}", file=sys.stderr)
        if status in RUN_TERMINAL:
            return status_resp
        if time.monotonic() > deadline:
            resume = f"run-status {dataset_id}" + (f" --run-id {run_id}" if run_id else "")
            fail(f"run still {status!r} after {timeout}s — poll again with: {resume}")
        time.sleep(poll)


def print_run_status(resp: dict) -> None:
    print(f"run status: {resp.get('status')}")
    print(f"  evaluation_run_id: {resp.get('evaluation_run_id')}")
    if (resp or {}).get("selected_steps"):
        print(f"  selected_steps: {', '.join(resp['selected_steps'])}")
    for key in ("started_at", "completed_at"):
        if resp.get(key):
            print(f"  {key}: {resp[key]}")
    subs = resp.get("submissions") or []
    rows = []
    for s in subs:
        progress = ""
        if s.get("steps_total") is not None:
            progress = f"{s.get('steps_completed', 0)}/{s.get('steps_total')}"
        rows.append([s.get("submission_id") or s.get("id"),
                     s.get("submission_name") or "", s.get("status"),
                     progress, s.get("workflow_execution_id") or ""])
    if rows:
        print(f"  samples: {len(rows)}")
        print_table(["SUBMISSION_ID", "NAME", "STATUS", "STEPS", "EXECUTION_ID"], rows)


def cmd_run(args) -> None:
    client = build_client(args)
    require_object_id(args.dataset_id, "dataset_id")
    body = {}
    if args.workflow_version is not None:
        if args.workflow_version < 1:
            fail("--workflow-version must be a published version number >= 1")
        body["workflow_version"] = args.workflow_version
    resp = client.post(f"{BASE}/datasets/{args.dataset_id}/run", body)
    run_id = (resp or {}).get("evaluation_run_id")
    print("test run started")
    print(f"  evaluation_run_id: {run_id}")
    print(f"  samples to run: {(resp or {}).get('submissions_to_run')}")
    print(f"  workflow version: {(resp or {}).get('source_workflow_published_version')}")
    if (resp or {}).get("selected_steps"):
        print(f"  selected_steps: {', '.join(resp['selected_steps'])}")
    if args.wait:
        final = poll_run(client, args.dataset_id, run_id, args.timeout, args.poll)
        print_run_status(final)
        if final.get("status") != "completed":
            sys.exit(2)


def cmd_run_submission(args) -> None:
    client = build_client(args)
    require_object_id(args.dataset_id, "dataset_id")
    require_object_id(args.submission_id, "submission_id")
    resp = client.post(
        f"{BASE}/datasets/{args.dataset_id}/submissions/{args.submission_id}/run", {})
    run_id = (resp or {}).get("evaluation_run_id")
    print("single-sample run started")
    print(f"  evaluation_run_id: {run_id}")
    print(f"  submission_id: {args.submission_id}")
    if args.wait:
        final = poll_run(client, args.dataset_id, run_id, args.timeout, args.poll)
        print_run_status(final)
        if final.get("status") != "completed":
            sys.exit(2)


def cmd_run_status(args) -> None:
    client = build_client(args)
    require_object_id(args.dataset_id, "dataset_id")
    if args.run_id:
        require_object_id(args.run_id, "--run-id")
    params = {"run_id": args.run_id} if args.run_id else None
    resp = client.get(f"{BASE}/datasets/{args.dataset_id}/run-status", params=params)
    if args.json:
        dump_json(resp)
    else:
        print_run_status(resp or {})


def cmd_cancel_run(args) -> None:
    client = build_client(args)
    require_object_id(args.dataset_id, "dataset_id")
    resp = client.delete(f"{BASE}/datasets/{args.dataset_id}/runs")
    print(f"cancel requested for active run on test batch {args.dataset_id}")
    if args.json and resp is not None:
        dump_json(resp)


# ------------------------------------------------------------ accuracy report

def report_step_rows(summary: dict):
    steps = summary.get("steps")
    items = []
    if isinstance(steps, dict):
        items = list(steps.items())
    elif isinstance(steps, list):
        items = [(s.get("step_name") or s.get("name") or s.get("step") or "?", s)
                 for s in steps if isinstance(s, dict)]
    rows = []
    for name, info in items:
        info = info or {}
        display = name
        if isinstance(name, str) and name.startswith(LOOP_DOC_PREFIX):
            diag = ((info.get("diagnostics") or {}).get("loop_iteration") or {})
            logical = (diag.get("definition_step_name") or info.get("display_name")
                       or info.get("original_step_name") or "loop step")
            doc = ((diag.get("document") or {}).get("filename")
                   or diag.get("document_filename") or "per-document")
            display = f"{logical} [{doc}]"
        threshold = info.get("accuracy_threshold_percent")
        if threshold is None and info.get("accuracy_threshold") is not None:
            threshold = info["accuracy_threshold"] * 100
        rows.append([display, fmt_num(info.get("accuracy")),
                     info.get("accuracy_source") or "",
                     fmt_num(threshold),
                     "PASS" if info.get("meets_accuracy_threshold")
                     else ("FAIL" if info.get("meets_accuracy_threshold") is False else "?"),
                     f"{info.get('scored_field_count', '')}/{info.get('available_field_count', '')}"
                     if info.get("available_field_count") is not None else ""])
    return rows


def print_report(summary: dict) -> None:
    print(f"Accuracy report — {summary.get('dataset_name') or summary.get('dataset_id') or ''}")
    print(f"  status: {summary.get('status')}   scoring_mode: {summary.get('scoring_mode')}"
          f"   schema: v{summary.get('report_schema_version', '?')}")
    if summary.get("config_hash"):
        print(f"  config_hash: {summary['config_hash']}")
    print(f"  anchor run: {summary.get('evaluation_run_id')} "
          f"(historical run status: {summary.get('evaluation_run_status')})")
    partial = summary.get("partial")
    print(f"  partial: {partial}"
          + ("  <- some samples lack completed executions; see rerun list below" if partial else ""))
    if summary.get("is_stale"):
        print(f"  !! STALE report ({summary.get('stale_reason')}) — regenerate before trusting numbers")
    if summary.get("total_submissions_evaluated") is not None:
        print(f"  samples scored: {summary.get('total_submissions_evaluated')}"
              f" of {summary.get('total_submissions_in_run')} in anchor run")

    headline = summary.get("headline") or {}
    print()
    print(f"  HEADLINE overall_accuracy: {fmt_num(summary.get('overall_accuracy'))}"
          f"   source: {summary.get('overall_accuracy_source')}"
          f"   metric: {headline.get('metric')}")
    print(f"    coverage: {fmt_ratio(headline.get('coverage'))}"
          f"   precision: {fmt_ratio(headline.get('precision'))}"
          f"   recall: {fmt_ratio(headline.get('recall'))}"
          f"   f1: {fmt_ratio(headline.get('f1'))}")
    for line in MICRO_F1_CAVEAT.splitlines():
        print(f"    {line}")
    without = headline.get("steps_without_counts") or []
    without_text = ", ".join(without) if isinstance(without, list) and without else (without or "none")
    print(f"    steps_with_counts: {headline.get('steps_with_counts')}"
          f"   steps_without_counts (NOT in headline): {without_text}")

    rows = report_step_rows(summary)
    if rows:
        print()
        print("  Per-step (covers ALL scored steps — gate un-counted steps here):")
        print_table(["STEP", "ACCURACY", "SOURCE", "THRESHOLD", "GATE", "FIELDS_SCORED"], rows)

    missing_exec = summary.get("missing_completed_execution_submission_ids") or []
    missing_gt = summary.get("missing_gt_submission_ids") or []
    missing_sub = summary.get("missing_submission_ids") or []
    if missing_exec or missing_gt or missing_sub:
        print()
        print("  Coverage gaps:")
        if missing_exec:
            print(f"    rerun these samples to clear partial: {', '.join(missing_exec)}")
        if missing_gt:
            print(f"    no answers (excluded from scoring): {', '.join(missing_gt)}")
        if missing_sub:
            print(f"    run-snapshot samples that failed to load (investigate): {', '.join(missing_sub)}")


def fetch_report(client, args, config_hash):
    if args.run_id:
        params = {"config_hash": config_hash} if config_hash else None
        return client.get(f"{BASE}/runs/{args.run_id}/accuracy-report", params=params)
    return client.get(f"{BASE}/datasets/{args.dataset_id}/accuracy-report",
                      params={"scoring_mode": args.scoring_mode})


def cmd_accuracy_report(args) -> None:
    client = build_client(args)
    require_object_id(args.dataset_id, "dataset_id")
    if args.run_id:
        require_object_id(args.run_id, "--run-id")

    if args.submission_id:
        require_object_id(args.submission_id, "--submission-id")
        if args.run_id:
            params = {"config_hash": args.config_hash} if args.config_hash else None
            detail = client.get(
                f"{BASE}/runs/{args.run_id}/accuracy-report/submissions/{args.submission_id}",
                params=params)
        else:
            detail = client.get(
                f"{BASE}/datasets/{args.dataset_id}/accuracy-report/submissions/{args.submission_id}",
                params={"scoring_mode": args.scoring_mode})
        dump_json(detail)
        return

    config_hash = args.config_hash
    summary, status = {}, None
    if not args.generate:
        try:
            summary = fetch_report(client, args, config_hash) or {}
            status = summary.get("status")
        except FaiError as e:
            if e.status not in (400, 404, 422):
                raise
            # no report/variant yet for this mode or hash — fall through to generate

    if args.generate or status in (None, "not_generated", "failed"):
        body = {"scoring_mode": args.scoring_mode}
        if args.run_id:
            resp = client.post(f"{BASE}/runs/{args.run_id}/accuracy-report", body)
            config_hash = (resp or {}).get("config_hash") or config_hash
            if config_hash:
                print(f"config_hash: {config_hash}  (record this to re-fetch this exact variant)",
                      file=sys.stderr)
        else:
            resp = client.post(f"{BASE}/datasets/{args.dataset_id}/accuracy-report", body)
        summary = resp if isinstance(resp, dict) else {}
        status = summary.get("status")

    deadline = time.monotonic() + args.timeout
    while status not in REPORT_TERMINAL:
        if status is None:
            fail(f"unexpected report response (no status): {json.dumps(summary)[:400]}")
        if status == "not_generated":
            fail("report generation did not start "
                 f"(requested mode: {summary.get('requested_scoring_mode', args.scoring_mode)}, "
                 f"current: {summary.get('current_scoring_mode')}). "
                 "Usually no sample has a completed current-pin execution — run the batch first.")
        print(f"  report status={status} ...", file=sys.stderr)
        if time.monotonic() > deadline:
            fail(f"report still {status!r} after {args.timeout}s — fetch later with the same command")
        time.sleep(args.poll)
        summary = fetch_report(client, args, config_hash) or {}
        status = summary.get("status")

    if status == "failed":
        print("report generation FAILED", file=sys.stderr)
        dump_json(summary)
        sys.exit(2)
    if status != "generated":
        fail(f"unexpected report status {status!r}: {json.dumps(summary)[:400]}")

    if args.json:
        dump_json(summary)
    else:
        if config_hash and "config_hash" not in summary:
            summary["config_hash"] = config_hash
        print_report(summary)


# ----------------------------------------------------------------------- main

def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env", choices=("prod", "staging", "local"),
                        help="default from ~/.fai/accounts.json defaults.env, else prod")
    parser.add_argument("--account", help="account slug/name/domain from ~/.fai/accounts.json")
    parser.add_argument("--org-id", help="explicit PropelAuth org UUID (wins over --account)")


def add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print raw API JSON")


def add_wait(parser: argparse.ArgumentParser, timeout: int) -> None:
    parser.add_argument("--wait", action="store_true", help="poll until the run reaches a terminal status")
    parser.add_argument("--timeout", type=int, default=timeout, help=f"wait timeout seconds (default {timeout})")
    parser.add_argument("--poll", type=int, default=15, help="poll interval seconds (default 15)")


def add_dataset_config_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--body", help="full JSON request body, inline or @file (other field flags ignored)")
    parser.add_argument("--name")
    parser.add_argument("--description")
    parser.add_argument("--dataset-type", choices=("live", "gold", "regression"))
    parser.add_argument("--pinned-workflow-version", type=int)
    parser.add_argument("--selected-step", action="append", metavar="STEP",
                        help="exact step name from runnable-steps; repeatable")
    parser.add_argument("--scored-fields-by-step", metavar="JSON",
                        help='{"Step": ["field", "nested.path"]}, inline or @file')
    parser.add_argument("--grid-row-match-keys", metavar="JSON",
                        help='{"Grid Step": ["key_field"]}, inline or @file')
    parser.add_argument("--default-step-accuracy-threshold", type=float)
    parser.add_argument("--step-accuracy-thresholds-by-step", metavar="JSON",
                        help='{"Step": 0.95}, inline or @file')


def main(argv=None) -> None:
    top = argparse.ArgumentParser(
        prog="eval_studio.py",
        description="Eval Studio: test batches, samples, answers, runs, accuracy reports.")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list-datasets", help="list test batches")
    add_common(p); add_json(p)
    p.add_argument("--workflow", help="workflow UUID, or name (needs --account)")
    p.add_argument("--dataset-type", choices=("live", "gold", "regression"))
    p.set_defaults(func=cmd_list_datasets)

    p = sub.add_parser("get-dataset", help="inspect one test batch")
    add_common(p); add_json(p)
    p.add_argument("dataset_id")
    p.set_defaults(func=cmd_get_dataset)

    p = sub.add_parser("create-dataset", help="create a test batch (workflow must have a published version)")
    add_common(p)
    p.add_argument("--workflow", help="workflow UUID, or name (needs --account)")
    add_dataset_config_flags(p)
    p.set_defaults(func=cmd_create_dataset)

    p = sub.add_parser("update-dataset", help="update batch config (makes cached reports stale)")
    add_common(p)
    p.add_argument("dataset_id")
    add_dataset_config_flags(p)
    p.set_defaults(func=cmd_update_dataset)

    p = sub.add_parser("repin", help="repin batch to the latest published workflow version")
    add_common(p)
    p.add_argument("dataset_id")
    p.add_argument("--selected-step", action="append", metavar="STEP",
                   help="replacement step names when old ones no longer exist")
    p.set_defaults(func=cmd_repin)

    p = sub.add_parser("runnable-steps", help="list evaluable steps for a batch or workflow")
    add_common(p); add_json(p)
    p.add_argument("--dataset", help="dataset ID")
    p.add_argument("--workflow", help="workflow UUID, or name (needs --account)")
    p.add_argument("--version", default="latest", help="workflow version (default latest)")
    p.set_defaults(func=cmd_runnable_steps)

    p = sub.add_parser("list-submissions", help="list samples in a test batch")
    add_common(p); add_json(p)
    p.add_argument("dataset_id")
    p.set_defaults(func=cmd_list_submissions)

    p = sub.add_parser("copy-submissions", help="copy (or --move) samples between batches of the same workflow")
    add_common(p); add_json(p)
    p.add_argument("--source", required=True, help="source dataset ID")
    p.add_argument("--target", required=True, help="target dataset ID")
    p.add_argument("--submission-id", action="append", required=True, help="repeatable")
    p.add_argument("--move", action="store_true", help="also unlink from source after copy")
    p.set_defaults(func=cmd_copy_submissions)

    p = sub.add_parser("remove-submission", help="unlink one sample from a batch (not a global delete)")
    add_common(p)
    p.add_argument("dataset_id")
    p.add_argument("submission_id")
    p.set_defaults(func=cmd_remove_submission)

    p = sub.add_parser("gt-template", help="fetch the answers template for a batch")
    add_common(p)
    p.add_argument("dataset_id")
    p.add_argument("--meta", action="store_true", help="template metadata instead of content")
    p.add_argument("--out", help="write JSON to file instead of stdout")
    p.set_defaults(func=cmd_gt_template)

    p = sub.add_parser("gt-read", help="read a sample's answers (all steps, or one paginated step)")
    add_common(p)
    p.add_argument("dataset_id")
    p.add_argument("submission_id")
    p.add_argument("--step", help="exact step name (or opaque __fai_eval_loop_doc__: key) for paginated read")
    p.add_argument("--cursor", type=int, default=0)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--out", help="write JSON to file instead of stdout")
    p.set_defaults(func=cmd_gt_read)

    p = sub.add_parser("gt-set-cells", help="patch answer cells for one step")
    add_common(p); add_json(p)
    p.add_argument("dataset_id")
    p.add_argument("submission_id")
    p.add_argument("--step", required=True)
    p.add_argument("--updates", required=True,
                   help='JSON list of {"field_name","row_index","new_value"}, inline or @file')
    p.set_defaults(func=cmd_gt_set_cells)

    p = sub.add_parser("gt-add-row", help="insert a grid answer row")
    add_common(p); add_json(p)
    p.add_argument("dataset_id")
    p.add_argument("submission_id")
    p.add_argument("--step", required=True)
    p.add_argument("--row-index", type=int, required=True)
    p.add_argument("--position", choices=("above", "below"), default="below")
    p.set_defaults(func=cmd_gt_add_row)

    p = sub.add_parser("gt-delete-row", help="delete a grid answer row")
    add_common(p); add_json(p)
    p.add_argument("dataset_id")
    p.add_argument("submission_id")
    p.add_argument("--step", required=True)
    p.add_argument("--row-index", type=int, required=True)
    p.set_defaults(func=cmd_gt_delete_row)

    p = sub.add_parser("copy-output-to-gt", help="copy AI-Generated output into Answers for one step")
    add_common(p); add_json(p)
    p.add_argument("dataset_id")
    p.add_argument("submission_id")
    p.add_argument("--step", required=True)
    p.set_defaults(func=cmd_copy_output_to_gt)

    p = sub.add_parser("run", help="run all complete samples in a batch")
    add_common(p)
    p.add_argument("dataset_id")
    p.add_argument("--workflow-version", type=int,
                   help="run a specific published version once, without repinning")
    add_wait(p, timeout=3600)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("run-submission", help="rerun one sample (debug/repair, not subset accuracy)")
    add_common(p)
    p.add_argument("dataset_id")
    p.add_argument("submission_id")
    add_wait(p, timeout=1800)
    p.set_defaults(func=cmd_run_submission)

    p = sub.add_parser("run-status", help="poll run status (--run-id pins one exact run)")
    add_common(p); add_json(p)
    p.add_argument("dataset_id")
    p.add_argument("--run-id", help="evaluation_run_id from run/run-submission output")
    p.set_defaults(func=cmd_run_status)

    p = sub.add_parser("cancel-run", help="cancel the active run on a batch")
    add_common(p); add_json(p)
    p.add_argument("dataset_id")
    p.set_defaults(func=cmd_cancel_run)

    p = sub.add_parser("accuracy-report",
                       help="fetch the report; generates + polls when missing (or --generate)")
    add_common(p); add_json(p)
    p.add_argument("dataset_id")
    p.add_argument("--scoring-mode", choices=("dataset", "all_fields"), default="dataset")
    p.add_argument("--generate", action="store_true", help="force regeneration even if cached")
    p.add_argument("--run-id", help="run-scoped report for one exact evaluation run (API-only)")
    p.add_argument("--config-hash", help="re-fetch an exact run-scoped report variant")
    p.add_argument("--submission-id", help="fetch one sample's detail (summary must exist first)")
    p.add_argument("--timeout", type=int, default=900, help="generation wait seconds (default 900)")
    p.add_argument("--poll", type=int, default=10, help="poll interval seconds (default 10)")
    p.set_defaults(func=cmd_accuracy_report)

    args = top.parse_args(argv)
    try:
        args.func(args)
    except AccountError as e:
        fail(str(e))
    except FaiError as e:
        detail = f" | body: {e.body[:400]}" if e.body else ""
        fail(f"{e}{detail}")
    except KeyboardInterrupt:
        fail("interrupted", code=130)


if __name__ == "__main__":
    main()
