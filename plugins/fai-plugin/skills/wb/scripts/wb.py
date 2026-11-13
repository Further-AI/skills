#!/usr/bin/env python3
"""wb — Workflow Builder CLI for the FurtherAI platform.

One entrypoint covering the workflow lifecycle: create, clone, copy-cross-env,
download, upload, publish, execute, retry, executions, status, results, table,
documents, versions, validate, diff, cancel.

stdlib only. All HTTP goes through lib/fai_client.FaiClient (auth, org
scoping, attribution, retries); account/org/workflow resolution goes through
lib/fai_accounts. Credentials live in ~/.fai/credentials.env (fai:setup);
the account database is ~/.fai/accounts.json (fai:account).

Examples:
    wb.py download --account acme --workflow-id submission_intake
    wb.py upload --org-id <uuid> --workflow-id <uuid> --json wf.json --summary "Fix mappings"
    wb.py execute --account acme --workflow-id <uuid> --docs a.pdf b.xlsx --wait
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_client import FaiClient, FaiError  # noqa: E402
from fai_accounts import (  # noqa: E402
    AccountError,
    default_env,
    resolve_org_id,
    resolve_workflow_id,
)

BUILDER = "/api/v1/workflow-builder/workflows"
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

PUBLISH_GATE_ERRORS = {
    "TEST_RUN_REQUIRED": "the active draft has no successful draft test run since its last edit",
    "TEST_RUN_IN_PROGRESS": "a draft test run is still executing — wait for it to finish",
    "TEST_RUN_FAILED": "the most recent draft test run failed — fix the draft and re-test",
}

# Step statuses are their own enum (in_queue/in_progress/success/failed) —
# NOT the execution-level LROStatus values.
STEP_MARKERS = {
    "success": "[ok]  ",
    "failed": "[FAIL]",
    "in_progress": "[..]  ",
    "in_queue": "[    ]",
}
STEP_FINISHED = ("success", "failed")
TABLE_PAGE_SIZE = 100


def die(msg: str, code: int = 1) -> "None":
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------- resolution

def resolved_env(args: argparse.Namespace) -> str:
    return args.env or default_env()


def make_client(args: argparse.Namespace, env: str | None = None,
                org_id: str | None = None, account: str | None = None) -> FaiClient:
    env = env or resolved_env(args)
    org = org_id or getattr(args, "org_id", None)
    acct = account or getattr(args, "account", None)
    if not org and acct:
        org = resolve_org_id(acct, env)
    return FaiClient(env=env, org_id=org, skill_name="wb")


def resolve_wf(args: argparse.Namespace, env: str) -> str:
    ref = args.workflow_id
    if UUID_RE.match(ref):
        return ref
    if not getattr(args, "account", None):
        die(f"--workflow-id {ref!r} is not a UUID; pass --account so it can be "
            f"resolved by name from ~/.fai/accounts.json")
    return resolve_workflow_id(args.account, ref, env)


# ------------------------------------------------------------------ helpers

def load_wf_json(path: str) -> dict:
    try:
        wf = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        die(f"cannot read workflow JSON {path}: {e}")
    if "steps" not in wf or "name" not in wf:
        die(f"{path} does not look like a workflow JSON (missing 'name' or 'steps')")
    return wf


def content_of(payload: dict) -> dict:
    return payload.get("content", payload) if isinstance(payload, dict) else payload


def draft_version_of(snapshot: dict):
    if snapshot.get("version") is not None:
        return snapshot["version"]
    return snapshot.get("published_version")


def builder_body(wf: dict, name: str | None = None) -> dict:
    # The builder API accepts `description`; the backend stores it on the
    # workflow content's `comments` field, so comments wins on round-trips.
    return {
        "name": name or wf["name"],
        "description": wf.get("comments") or wf.get("description", ""),
        "welcome_message": wf.get("welcome_message"),
        "steps": wf["steps"],
        "options": wf.get("options"),
    }


def normalized_wf(wf: dict) -> dict:
    # `columns` is kept for local fidelity (download/diff) but is NOT part of
    # builder_body: the builder create/edit API has no columns field — the
    # backend manages execution-list columns server-side.
    return {
        "name": wf.get("name"),
        "description": wf.get("description", ""),
        "comments": wf.get("comments", ""),
        "welcome_message": wf.get("welcome_message"),
        "steps": wf.get("steps", []),
        "options": wf.get("options"),
        "columns": wf.get("columns", []),
    }


def gate_error_of(e: FaiError) -> str | None:
    if e.status != 400:
        return None
    try:
        detail = json.loads(e.body).get("detail")
    except (json.JSONDecodeError, AttributeError):
        return None
    if isinstance(detail, dict) and detail.get("error") in PUBLISH_GATE_ERRORS:
        return detail["error"]
    return None


def fetch_active_draft_version(client: FaiClient, workflow_id: str):
    try:
        draft = content_of(client.get(f"/api/v1/workflows/{workflow_id}",
                                      params={"draft": "true"}))
        return draft_version_of(draft)
    except FaiError:
        return None


def draft_test_command(env: str, org_id: str | None, workflow_id: str, version) -> str:
    org = f"--org-id {org_id} " if org_id else ""
    v = version if version is not None else "<draft-version>"
    return (f"wb.py execute --env {env} {org}--workflow-id {workflow_id} "
            f"--draft-version {v} --docs <files>")


def execution_steps(log: dict) -> list:
    return (log.get("result") or {}).get("steps") or []


def step_name_of(step: dict) -> str:
    # Key off input.step_name, never title.
    return (step.get("input") or {}).get("step_name") or "<unnamed step>"


def print_step_table(log: dict) -> None:
    steps = execution_steps(log)
    if not steps:
        print("Steps: (none reported yet)")
        return
    finished = sum(1 for s in steps if s.get("status") in STEP_FINISHED)
    print(f"Steps: {finished}/{len(steps)} finished")
    for step in steps:
        status = step.get("status") or "?"
        marker = STEP_MARKERS.get(status, "[?]   ")
        # step_type is a lossy display bucket — prefer step_type_raw, which
        # may be null on old executions.
        name = step_name_of(step)
        step_type = step.get("step_type_raw") or step.get("step_type") or "-"
        print(f"  {marker} {name}  ({step_type}, {status})")
        output = step.get("output") or {}
        if status == "failed" and isinstance(output, dict) and output.get("error"):
            print(f"         error: {output['error']}")
        if isinstance(output, dict) and output.get("table_v1_log_id"):
            print(f"         table_v1_log_id: {output['table_v1_log_id']}")


def print_execution_summary(client: FaiClient, log: dict, exec_id: str) -> None:
    print(f"Execution ID: {exec_id}")
    print(f"Status: {log.get('status')}")
    if log.get("title"):
        print(f"Title: {log['title']}")
    print(f"Execution URL: {client.execution_url(exec_id)}")
    print_step_table(log)


def slugify(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower() or "step"


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f" ... [{len(text) - limit} more chars]"


def step_error_of(step: dict, width: int = 400) -> str:
    output = step.get("output")
    err = output.get("error") if isinstance(output, dict) else ""
    err = err or step.get("error") or ""
    return truncate(" ".join(str(err).split()), width)


def schema_columns(schema) -> list:
    if not isinstance(schema, dict):
        return []
    if isinstance(schema.get("properties"), dict):
        return list(schema["properties"])
    items = schema.get("items")
    if isinstance(items, dict) and isinstance(items.get("properties"), dict):
        return list(items["properties"])
    return []


def fetch_table_rows(client: FaiClient, table_log_id: str) -> tuple:
    """Paginate GET /api/v1/table/v1/logs/{id}; cursor is a row offset."""
    rows: list = []
    schema = None
    cursor = 0
    while True:
        page = client.get(f"/api/v1/table/v1/logs/{table_log_id}",
                          params={"cursor": cursor, "limit": TABLE_PAGE_SIZE})
        data = page.get("data") or []
        schema = schema or page.get("schema")
        rows.extend(data)
        if len(data) < TABLE_PAGE_SIZE or cursor > 100_000:
            break
        cursor += len(data)
    return rows, schema


def request_document_ids(log: dict) -> set:
    """user_document_ids the execution was started with (its inputs)."""
    ids = set()
    for doc in (log.get("request") or {}).get("documents") or []:
        if isinstance(doc, str):
            ids.add(doc)
        elif isinstance(doc, dict):
            ids.add(doc.get("user_document_id") or doc.get("id"))
    ids.discard(None)
    return ids


# ----------------------------------------------------------------- commands

def cmd_create(args: argparse.Namespace) -> int:
    env = resolved_env(args)
    client = make_client(args, env=env)
    wf = load_wf_json(args.json)
    draft = client.post(f"{BUILDER}/", builder_body(wf, args.name))
    workflow_id = draft["workflow_id"]
    version = draft_version_of(draft)
    print(f"Workflow ID: {workflow_id}")
    print(f"Draft version: {version}")
    print(f"Steps: {len(wf['steps'])}")
    print(f"Workflow URL: {client.workflow_url(workflow_id)}")
    print("Draft only — not published. Test it with:")
    print(f"  {draft_test_command(env, client.org_id, workflow_id, version)}")
    return 0


def cmd_clone(args: argparse.Namespace) -> int:
    env = resolved_env(args)
    client = make_client(args, env=env)
    cloned = client.post(f"{BUILDER}/{args.source_workflow_id}/clone",
                         {"name": args.name} if args.name else {})
    workflow_id = cloned["workflow_id"]
    version = fetch_active_draft_version(client, workflow_id)
    print(f"Source workflow ID: {args.source_workflow_id}")
    print(f"New workflow ID: {workflow_id}")
    print(f"Draft version: {version}")
    print(f"Workflow URL: {client.workflow_url(workflow_id)}")
    print("Clone is a never-published draft. Test it with:")
    print(f"  {draft_test_command(env, client.org_id, workflow_id, version)}")
    return 0


def cmd_copy_cross_env(args: argparse.Namespace) -> int:
    source_env = args.source_env or resolved_env(args)
    target_env = args.target_env or resolved_env(args)
    source = make_client(args, env=source_env, org_id=args.source_org_id,
                         account=args.source_account)
    target = make_client(args, env=target_env, org_id=args.target_org_id,
                         account=args.target_account)

    if args.source_version:
        payload = source.get(
            f"{BUILDER}/{args.source_workflow_id}/versions/{args.source_version}")
    else:
        payload = source.get(f"/api/v1/workflows/{args.source_workflow_id}")
    wf = content_of(payload)
    if not wf.get("steps"):
        die("source workflow has no published content (never published, or a "
            "shell). Pass --source-version, or there is nothing to copy.")

    # Shell first with empty steps (tolerates legacy step shapes), then import
    # the exported JSON into the active draft with validation disabled —
    # the same two-step path as the UI's JSON download/upload. Publish still
    # validates later.
    shell = target.post(f"{BUILDER}/", {**builder_body(wf, args.name), "steps": []})
    new_workflow_id = shell["workflow_id"]
    summary = args.change_summary or (
        f"Imported from {source_env} workflow {args.source_workflow_id}"
        + (f" v{args.source_version}" if args.source_version else ""))
    imported = target.put(f"{BUILDER}/{new_workflow_id}", {
        **builder_body(wf, args.name),
        "change_summary": summary,
        "enable_validation": False,
    })
    version = draft_version_of(imported)
    src_v = f" v{args.source_version}" if args.source_version else " (live)"
    print(f"Copied: {source_env} workflow {args.source_workflow_id}{src_v} "
          f"-> {target_env} workflow {new_workflow_id}")
    print(f"New workflow ID: {new_workflow_id}")
    print(f"Draft version: {version}")
    print(f"Steps: {len(wf['steps'])}")
    print(f"Workflow URL: {target.workflow_url(new_workflow_id)}")
    print("Copy is a draft — not published. Test it with:")
    print(f"  {draft_test_command(target_env, target.org_id, new_workflow_id, version)}")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    env = resolved_env(args)
    client = make_client(args, env=env)
    workflow_id = resolve_wf(args, env)

    if args.version:
        payload = client.get(f"{BUILDER}/{workflow_id}/versions/{args.version}")
        suffix = f"_v{args.version}"
    elif args.draft:
        try:
            payload = client.get(f"/api/v1/workflows/{workflow_id}",
                                 params={"draft": "true"})
        except FaiError as e:
            if e.status == 404:
                die("no active draft exists for this workflow (draft reads are "
                    "also FAI-admin only — a 404 can mean either)")
            raise
        suffix = "_draft"
    else:
        params = {"slim": args.slim} if args.slim else None
        payload = client.get(f"/api/v1/workflows/{workflow_id}", params=params)
        suffix = ""

    wf = content_of(payload)
    out = Path(args.out or f"workflow_{workflow_id[:8]}{suffix}.json")
    body = payload if args.slim else normalized_wf(wf)
    out.write_text(json.dumps(body, indent=2) + "\n")
    print(f"Workflow: {wf.get('name')}")
    if isinstance(payload, dict) and payload.get("published_version") is not None:
        print(f"Published version: {payload['published_version']}")
    print(f"Steps: {len(wf.get('steps') or [])}")
    print(f"Saved: {out.resolve()}")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    env = resolved_env(args)
    client = make_client(args, env=env)
    workflow_id = resolve_wf(args, env)
    wf = load_wf_json(args.json)
    draft = client.put(f"{BUILDER}/{workflow_id}", {
        **builder_body(wf),
        "change_summary": args.summary,
        "enable_validation": False,
    })
    version = draft_version_of(draft)
    print(f"Workflow ID: {workflow_id}")
    print(f"Draft version: {version}")
    print(f"Steps: {len(wf['steps'])}")
    print(f"Workflow URL: {client.workflow_url(workflow_id)}")
    print("Draft saved — NOT published. To go live, run a draft test then:")
    print(f"  wb.py publish --env {env} --org-id {client.org_id} "
          f"--workflow-id {workflow_id} --name \"...\"")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    env = resolved_env(args)
    client = make_client(args, env=env)
    workflow_id = resolve_wf(args, env)

    if args.promote_version:
        published = client.post(
            f"{BUILDER}/{workflow_id}/versions/{args.promote_version}/publish",
            {"published_name": args.name or f"Rollback to v{args.promote_version}",
             "publish_notes": args.notes})
        print(f"Promoted version {args.promote_version} as new live version.")
        print(f"Published version: {published.get('published_version')}")
        print(f"Workflow URL: {client.workflow_url(workflow_id)}")
        return 0

    body = {"published_name": args.name, "publish_notes": args.notes,
            "override": False}
    try:
        published = client.post(f"{BUILDER}/{workflow_id}/publish", body)
    except FaiError as e:
        gate = gate_error_of(e)
        if gate is None:
            raise
        if not args.allow_override:
            version = fetch_active_draft_version(client, workflow_id)
            print(f"Publish blocked by the draft-test gate: {gate} "
                  f"({PUBLISH_GATE_ERRORS[gate]}).", file=sys.stderr)
            print("Run a successful draft test, then publish again:",
                  file=sys.stderr)
            print(f"  {draft_test_command(env, client.org_id, workflow_id, version)}",
                  file=sys.stderr)
            print("Or re-run publish with --allow-override to bypass the test "
                  "gate (validation still runs).", file=sys.stderr)
            return 1
        print(f"WARNING: draft-test gate hit ({gate}); retrying with "
              f"override=true. Override bypasses ONLY the test gate — publish "
              f"validation still runs and cannot be bypassed.", file=sys.stderr)
        published = client.post(f"{BUILDER}/{workflow_id}/publish",
                                {**body, "override": True})
    print(f"Published version: {published.get('published_version')}")
    print(f"Workflow ID: {workflow_id}")
    print(f"Workflow URL: {client.workflow_url(workflow_id)}")
    return 0


def cmd_execute(args: argparse.Namespace) -> int:
    env = resolved_env(args)
    client = make_client(args, env=env)
    workflow_id = resolve_wf(args, env)

    doc_ids = []
    for doc in args.docs:
        path = Path(doc).expanduser()
        if not path.is_file():
            die(f"document not found: {path}")
        doc_id = client.upload_document(path)
        doc_ids.append(doc_id)
        print(f"Uploaded: {path.name} -> {doc_id}")

    payload = {"workflow_id": workflow_id, "instruction": args.instruction or "",
               "documents": doc_ids}
    if args.draft_version is not None:
        resp = client.post(
            f"{BUILDER}/{workflow_id}/versions/{args.draft_version}/execute",
            payload, timeout=120)
    else:
        resp = client.post("/api/v1/workflow-execution", payload, timeout=120)
    exec_id = resp["workflow_execution_id"]
    kind = f"draft v{args.draft_version}" if args.draft_version is not None else "published"
    print(f"Execution ID: {exec_id}  ({kind})")
    print(f"Execution URL: {client.execution_url(exec_id)}")

    if not args.wait:
        print("Follow progress at the URL above, or poll with: "
              f"wb.py status --env {env} --org-id {client.org_id} --exec-id {exec_id}")
        return 0
    log = client.poll_execution(exec_id, interval=args.interval,
                                timeout=args.timeout)
    print_execution_summary(client, log, exec_id)
    return 0 if log.get("status") == "completed" else 1


def cmd_retry(args: argparse.Namespace) -> int:
    # Composite: no retry endpoint exists on the platform. A retry is a brand
    # new execution (new ID, runs from step 1) that reuses the old execution's
    # user_document_ids, so the bytes are not re-uploaded.
    env = resolved_env(args)
    client = make_client(args, env=env)
    old_log = client.execution_log(args.exec_id)
    request = old_log.get("request") or {}
    # Reuse the ORIGINAL input documents from the request. The execution
    # documents listing also contains generated outputs (filled templates,
    # excel exports) which must not be re-fed as workflow inputs.
    doc_ids = request.get("documents") or []
    if not doc_ids:
        docs = client.get(
            f"/api/v1/workflow-execution/logs/{args.exec_id}/documents")["documents"]
        doc_ids = [d["user_document_id"] for d in docs]
    workflow_id = request.get("workflow_id")
    if not workflow_id:
        die(f"execution {args.exec_id} has no request.workflow_id — cannot retry")
    workflow_version = old_log.get("workflow_version")

    instruction = (args.instruction if args.instruction is not None
                   else request.get("instruction") or "")
    payload = {"workflow_id": workflow_id, "instruction": instruction,
               "documents": doc_ids}
    if workflow_version is not None:
        resp = client.post(
            f"{BUILDER}/{workflow_id}/versions/{workflow_version}/execute",
            payload, timeout=120)
    else:
        resp = client.post("/api/v1/workflow-execution", payload, timeout=120)
    new_exec_id = resp["workflow_execution_id"]

    print(f"Old execution ID: {args.exec_id}")
    print(f"New execution ID: {new_exec_id}")
    print(f"Workflow ID: {workflow_id}"
          + (f" (version {workflow_version})" if workflow_version is not None else ""))
    print(f"Documents reused: {len(doc_ids)}")
    print(f"Execution URL: {client.execution_url(new_exec_id)}")
    print(f"Workflow URL: {client.workflow_url(workflow_id)}")
    if not args.wait:
        return 0
    log = client.poll_execution(new_exec_id, interval=args.interval,
                                timeout=args.timeout)
    print_execution_summary(client, log, new_exec_id)
    return 0 if log.get("status") == "completed" else 1


def cmd_executions(args: argparse.Namespace) -> int:
    env = resolved_env(args)
    client = make_client(args, env=env)
    # No --workflow-id lists across all workflows in the org (the endpoint is
    # org-scoped); the client drops None params.
    workflow_id = resolve_wf(args, env) if args.workflow_id else None
    params = {
        "workflow_id": workflow_id,
        "limit": args.limit,
        "statuses": args.statuses.split(",") if args.statuses else None,
        "owner_uids": args.owner_uids.split(",") if args.owner_uids else None,
        "owner_names": args.owner_names.split(",") if args.owner_names else None,
        "search": args.search,
        "date_from": args.date_from,
        "date_to": args.date_to,
        "cursor": args.cursor,
    }
    rows, total, cursor = [], None, args.cursor
    while True:
        params["cursor"] = cursor
        data = client.get("/api/v1/workflow-execution/logs", params=params)
        rows.extend(data.get("workflow_executions") or [])
        total = data.get("total_count", total)
        cursor = data.get("next_cursor")
        if not (args.all and cursor):
            break

    print(f"Total matching: {total}  (showing {len(rows)})")
    for r in rows:
        rid = r.get("id") or r.get("workflow_execution_id") or "?"
        created = str(r.get("created_at") or "")[:19]
        print(f"  {created}  {str(r.get('status') or '?'):<11} "
              f"{str(r.get('owner_name') or '-'):<24} {rid}  {r.get('title') or ''}")
    if cursor and not args.all:
        print(f"Next cursor: {cursor}  (pass --cursor to continue, or --all)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    client = make_client(args)
    log = client.execution_log(args.exec_id)
    print_execution_summary(client, log, args.exec_id)
    return 0


def cmd_results(args: argparse.Namespace) -> int:
    if args.excel and not args.out:
        die("--excel requires --out DIR")
    client = make_client(args)
    log = client.execution_log(args.exec_id)
    print(f"Execution ID: {args.exec_id}")
    print(f"Status: {log.get('status')}")
    if log.get("title"):
        print(f"Title: {log['title']}")
    print(f"Execution URL: {client.execution_url(args.exec_id)}")

    all_steps = execution_steps(log)
    steps = all_steps
    if args.step:
        want = slugify(args.step)
        steps = [s for s in all_steps if want in slugify(step_name_of(s))]
        if not steps:
            names = ", ".join(step_name_of(s) for s in all_steps)
            die(f"no step matching {args.step!r}; steps: {names or '(none)'}")

    out_dir = Path(args.out).expanduser() if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    used_names: dict[str, int] = {}

    for step in steps:
        name = step_name_of(step)
        status = step.get("status") or "?"
        output = step.get("output")
        print(f"\n== {name} [{status}]")
        if status == "failed":
            print(f"  error: {step_error_of(step)}")

        fname = slugify(name)
        n = used_names.get(fname, 0)
        used_names[fname] = n + 1
        if n:
            fname = f"{fname}_{n + 1}"

        table_log_id = output.get("table_v1_log_id") if isinstance(output, dict) else None
        if table_log_id:
            rows, schema = fetch_table_rows(client, table_log_id)
            cols = schema_columns(schema) or (list(rows[0]) if rows else [])
            print(f"  table: {len(rows)} row(s); "
                  f"columns: {', '.join(str(c) for c in cols) or '?'}")
            for row in rows[:3]:
                print("    " + truncate(json.dumps(row, default=str), 220))
            if len(rows) > 3:
                more = "" if out_dir else " (use --out DIR for full JSON)"
                print(f"    ... {len(rows) - 3} more row(s){more}")
            if out_dir:
                fpath = out_dir / f"{fname}.json"
                fpath.write_text(json.dumps(
                    {"step": name, "status": status,
                     "table_v1_log_id": table_log_id,
                     "schema": schema, "rows": rows},
                    indent=2, default=str) + "\n")
                print(f"  wrote: {fpath}")
                if args.excel:
                    export = client.post(
                        f"/api/v1/table/v1/logs/{table_log_id}/export-excel",
                        timeout=300)
                    xpath = client.download_document(
                        export["user_document_id"], out_dir / f"{fname}.xlsx")
                    print(f"  wrote: {xpath}")
        else:
            shown = output
            if (status == "failed" and isinstance(output, dict)
                    and set(output) == {"error"}):
                shown = None  # the error line above already covers it
            if shown in (None, "", {}, []):
                if status != "failed":
                    print("  (no output)")
            else:
                text = json.dumps(shown, indent=2, default=str)
                for line in truncate(text, 2000).splitlines():
                    print("  " + line)
            if out_dir:
                fpath = out_dir / f"{fname}.json"
                fpath.write_text(json.dumps(
                    {"step": name, "status": status, "output": output},
                    indent=2, default=str) + "\n")
                print(f"  wrote: {fpath}")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    client = make_client(args)
    resp = client.post("/api/v1/cancel-workflow-execution",
                       {"workflow_execution_id": args.exec_id})
    print(f"Success: {resp.get('success')}")
    print(f"Status: {resp.get('status')}")
    print(f"Message: {resp.get('message')}")
    return 0 if resp.get("success") else 1


def cmd_table(args: argparse.Namespace) -> int:
    client = make_client(args)

    if args.export_excel:
        resp = client.post(f"/api/v1/table/v1/logs/{args.log_id}/export-excel",
                           timeout=300)
        doc_id = resp["user_document_id"]
        out = Path(args.out or f"table_{args.log_id[:8]}.xlsx")
        client.download_document(doc_id, out)
        print(f"Exported Excel user_document_id: {doc_id}")
        print(f"Saved: {out.resolve()}")
        return 0

    table = client.get(f"/api/v1/table/v1/logs/{args.log_id}",
                       params={"cursor": args.cursor, "limit": args.limit,
                               "search": args.search})
    rows = table.get("data") or []
    pagination = table.get("pagination") or {}
    print(f"Rows returned: {len(rows)}")
    if pagination:
        print(f"Pagination: {json.dumps(pagination)}")
    if rows and isinstance(rows[0], dict):
        cols = list(rows[0].keys())
        print("\t".join(cols))
        for row in rows:
            print("\t".join(_cell(row.get(c)) for c in cols))
    else:
        for row in rows:
            print(json.dumps(row))
    return 0


def _cell(value, width: int = 60) -> str:
    text = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def cmd_documents(args: argparse.Namespace) -> int:
    client = make_client(args)

    if args.action == "list":
        if not args.exec_id:
            die("documents list requires --exec-id")
        docs = client.get(
            f"/api/v1/workflow-execution/logs/{args.exec_id}/documents")["documents"]
        print(f"Documents: {len(docs)}")
        for d in docs:
            print(f"  {d['user_document_id']}  {d.get('filename') or '-'}")
        return 0

    if args.action == "download":
        if not args.doc_id:
            die("documents download requires --doc-id")
        meta = client.get(f"/api/v1/user-documents/{args.doc_id}")
        filename = (meta or {}).get("filename") or f"doc_{args.doc_id}"
        out_dir = Path(args.out or ".")
        path = _claim_path(out_dir, filename, args.doc_id)
        client.download_document(args.doc_id, path)
        print(f"Saved: {path.resolve()}")
        return 0

    # download-all: no bulk/zip endpoint exists — one list call, then N
    # parallel SAS downloads client-side.
    if not args.exec_id:
        die("documents download-all requires --exec-id")
    # The documents listing includes generated outputs (filled templates,
    # excel exports); an "input" is a member of the execution's
    # request.documents.
    input_ids = request_document_ids(client.execution_log(args.exec_id))
    docs = client.get(
        f"/api/v1/workflow-execution/logs/{args.exec_id}/documents")["documents"]
    out_dir = Path(args.out or f"exec-{args.exec_id}-docs")
    out_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, int] = {}
    jobs = []
    # Claim destination filenames over the FULL doc list, before the
    # input/output filter, so a file keeps the same name whether or not
    # --input-only/--output-only is passed; skip-if-exists depends on that.
    for d in docs:
        name = Path(d.get("filename") or f"doc_{d['user_document_id']}").name
        n = seen.get(name, 0)
        seen[name] = n + 1
        if n:
            p = Path(name)
            dedupe = f"{n}_{d['user_document_id']}"
            name = f"{p.stem}_{dedupe}{p.suffix}" if p.suffix else f"{name}_{dedupe}"
        kind = "input" if d["user_document_id"] in input_ids else "output"
        if args.input_only and kind != "input":
            continue
        if args.output_only and kind != "output":
            continue
        jobs.append((d["user_document_id"], kind, out_dir / name))

    def fetch_one(doc_id: str, path: Path) -> tuple:
        if path.exists() and path.stat().st_size > 0:
            return path, "skipped (exists)"
        client.download_document(doc_id, path)
        return path, "downloaded"

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(fetch_one, doc_id, path): (kind, path)
                   for doc_id, kind, path in jobs}
        failed = downloaded = skipped = 0
        for fut in concurrent.futures.as_completed(futures):
            kind, path = futures[fut]
            try:
                _, result = fut.result()
                if result == "downloaded":
                    downloaded += 1
                    print(f"Saved: {path}  ({kind})")
                else:
                    skipped += 1
                    print(f"Skipped (exists): {path}  ({kind})")
            except Exception as e:  # keep the batch alive on a single failure
                failed += 1
                print(f"FAILED: {path}: {e}", file=sys.stderr)
    print(f"{downloaded} downloaded, {skipped} skipped, {failed} failed of "
          f"{len(jobs)} selected ({len(docs)} on the execution) -> {out_dir.resolve()}")
    return 0 if failed == 0 else 1


def _claim_path(out_dir: Path, filename: str, doc_id: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(filename).name or f"doc_{doc_id}"
    path = out_dir / name
    if path.exists():
        p = Path(name)
        path = out_dir / f"{p.stem}_{doc_id}{p.suffix}"
    return path


def cmd_versions(args: argparse.Namespace) -> int:
    env = resolved_env(args)
    client = make_client(args, env=env)
    workflow_id = resolve_wf(args, env)
    data = client.get(f"/api/v1/workflows/{workflow_id}",
                      params={"slim": "true"})
    wf = content_of(data)
    print(f"Workflow: {wf.get('name')}  ({workflow_id})")
    print(f"Live published version: {data.get('published_version')}")
    print(f"Active draft present: {data.get('is_draft_present', False)}")
    versions = data.get("versions") or []
    for v in versions:
        num = v.get("version") if v.get("version") is not None else v.get("published_version")
        published_at = str(v.get("published_at") or v.get("created_at") or "")[:19]
        print(f"  v{num}  {published_at}  {v.get('published_name') or '-'}"
              + (f"  — {v['publish_notes']}" if v.get("publish_notes") else ""))
    if not versions:
        print("  (no published versions)")
    if data.get("is_draft_present"):
        print("Fetch the draft with: wb.py download --draft "
              f"--env {env} --org-id {client.org_id} --workflow-id {workflow_id}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    env = resolved_env(args)
    client = make_client(args, env=env)
    workflow_id = resolve_wf(args, env)
    try:
        result = client.post(f"{BUILDER}/{workflow_id}/validate")
    except FaiError as e:
        if e.status == 404:
            # 404 here means "no draft exists", NOT "invalid".
            die(f"no active draft exists for workflow {workflow_id} — upload "
                f"one first (wb.py upload). 404 on validate means 'no draft', "
                f"not 'invalid'.")
        raise
    is_valid = result.get("is_valid")
    errors = result.get("errors") or []
    print(f"Validation call succeeded. is_valid: {is_valid}")
    if is_valid:
        return 0
    print(f"Errors ({len(errors)}):")
    for i, err in enumerate(errors, 1):
        if isinstance(err, dict):
            step = err.get("step_name") or err.get("step") or ""
            msg = err.get("message") or err.get("error") or json.dumps(err)
            print(f"  {i}. " + (f"[{step}] " if step else "") + str(msg))
        else:
            print(f"  {i}. {err}")
    return 1


def cmd_diff(args: argparse.Namespace) -> int:
    env = resolved_env(args)
    client = make_client(args, env=env)
    workflow_id = resolve_wf(args, env)
    local = normalized_wf(load_wf_json(args.json))

    if args.version:
        remote_payload = client.get(f"{BUILDER}/{workflow_id}/versions/{args.version}")
        remote_label = f"remote v{args.version}"
    elif args.draft:
        remote_payload = client.get(f"/api/v1/workflows/{workflow_id}",
                                    params={"draft": "true"})
        remote_label = "remote draft"
    else:
        remote_payload = client.get(f"/api/v1/workflows/{workflow_id}")
        remote_label = "remote live"
    remote = normalized_wf(content_of(remote_payload))

    changed = False
    for field in ("name", "welcome_message", "description", "comments"):
        if (local.get(field) or "") != (remote.get(field) or ""):
            print(f"{field}: differs ({remote_label}: {remote.get(field)!r} "
                  f"-> local: {local.get(field)!r})")
            changed = True

    def keyed(steps):
        out = {}
        for i, s in enumerate(steps or []):
            out[s.get("step_name") or s.get("name") or f"<step #{i}>"] = s
        return out

    local_steps, remote_steps = keyed(local["steps"]), keyed(remote["steps"])
    added = [k for k in local_steps if k not in remote_steps]
    removed = [k for k in remote_steps if k not in local_steps]
    modified = []
    for k in local_steps:
        if k in remote_steps and json.dumps(local_steps[k], sort_keys=True) != \
                json.dumps(remote_steps[k], sort_keys=True):
            fields = sorted(
                f for f in set(local_steps[k]) | set(remote_steps[k])
                if json.dumps(local_steps[k].get(f), sort_keys=True)
                != json.dumps(remote_steps[k].get(f), sort_keys=True))
            modified.append((k, fields))

    if added:
        changed = True
        print(f"Steps added in local ({len(added)}):")
        for k in added:
            print(f"  + {k}")
    if removed:
        changed = True
        print(f"Steps removed in local ({len(removed)}):")
        for k in removed:
            print(f"  - {k}")
    if modified:
        changed = True
        print(f"Steps modified ({len(modified)}):")
        for k, fields in modified:
            print(f"  ~ {k}  (fields: {', '.join(fields)})")

    lo, ro = local.get("options") or {}, remote.get("options") or {}
    opt_diff = sorted(
        k for k in set(lo) | set(ro)
        if json.dumps(lo.get(k), sort_keys=True) != json.dumps(ro.get(k), sort_keys=True))
    if opt_diff:
        changed = True
        print(f"Options changed: {', '.join(opt_diff)}")

    common_local = [k for k in local_steps if k in remote_steps]
    common_remote = [k for k in remote_steps if k in local_steps]
    if common_local != common_remote:
        changed = True
        print("Step order differs for common steps.")

    if not changed:
        print(f"No differences between local JSON and {remote_label}.")
        return 0
    print(f"Summary: {len(added)} added, {len(removed)} removed, "
          f"{len(modified)} modified, {len(opt_diff)} option keys changed "
          f"(local vs {remote_label}).")
    return 1


# -------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", choices=["prod", "staging", "local"],
                        help="default: accounts.json defaults.env, else prod")
    common.add_argument("--account",
                        help="account slug/name/domain from ~/.fai/accounts.json; "
                             "resolves --org-id and workflow names")
    common.add_argument("--org-id", help="explicit org UUID; overrides --account")

    p = argparse.ArgumentParser(
        prog="wb.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, help_text):
        sp = sub.add_parser(name, parents=[common], help=help_text)
        sp.set_defaults(func=func)
        return sp

    sp = add("create", cmd_create, "create a workflow shell + active draft from JSON")
    sp.add_argument("--json", required=True, help="workflow JSON file")
    sp.add_argument("--name", help="override the workflow name from the JSON")

    sp = add("clone", cmd_clone, "clone a workflow into a new never-published draft")
    sp.add_argument("--source-workflow-id", required=True)
    sp.add_argument("--name", help="name for the clone")

    sp = add("copy-cross-env", cmd_copy_cross_env,
             "copy a workflow between envs/orgs (export + draft import)")
    sp.add_argument("--source-env", choices=["prod", "staging", "local"])
    sp.add_argument("--source-org-id")
    sp.add_argument("--source-account")
    sp.add_argument("--source-workflow-id", required=True)
    sp.add_argument("--source-version", type=int,
                    help="copy a specific published version instead of live")
    sp.add_argument("--target-env", choices=["prod", "staging", "local"])
    sp.add_argument("--target-org-id")
    sp.add_argument("--target-account")
    sp.add_argument("--name", required=True,
                    help="distinct, human-readable name for the copy")
    sp.add_argument("--change-summary")

    sp = add("download", cmd_download, "download workflow JSON (live/draft/version)")
    sp.add_argument("--workflow-id", required=True, help="UUID, or name with --account")
    sp.add_argument("--draft", action="store_true", help="active draft (FAI-admin only)")
    sp.add_argument("--version", type=int, help="a specific published version")
    sp.add_argument("--slim", choices=["true", "exec"],
                    help="reduced payload; slim=true drops steps entirely")
    sp.add_argument("--out", help="output file path")

    sp = add("upload", cmd_upload, "upload JSON to the active draft (never publishes)")
    sp.add_argument("--workflow-id", required=True)
    sp.add_argument("--json", required=True)
    sp.add_argument("--summary", default="Uploaded via wb", help="change summary")

    sp = add("publish", cmd_publish, "publish the active draft (or promote an old version)")
    sp.add_argument("--workflow-id", required=True)
    sp.add_argument("--name", help="published_name (required unless --promote-version)")
    sp.add_argument("--notes", default="", help="publish_notes")
    sp.add_argument("--allow-override", action="store_true",
                    help="bypass the draft-test gate ONLY; validation still runs")
    sp.add_argument("--promote-version", type=int,
                    help="publish this old version as the new live version")

    sp = add("execute", cmd_execute, "run a workflow (published, or a draft version)")
    sp.add_argument("--workflow-id", required=True)
    sp.add_argument("--docs", nargs="+", required=True, help="local files to upload")
    sp.add_argument("--draft-version", type=int, help="execute this draft/version")
    sp.add_argument("--instruction", default="")
    sp.add_argument("--wait", action="store_true", help="poll until terminal status")
    sp.add_argument("--interval", type=int, default=10)
    sp.add_argument("--timeout", type=int, default=3600)

    sp = add("retry", cmd_retry, "new execution reusing an old execution's documents")
    sp.add_argument("--exec-id", required=True, help="the old execution ID")
    sp.add_argument("--instruction", default=None,
                    help="override; defaults to the old execution's instruction")
    sp.add_argument("--wait", action="store_true")
    sp.add_argument("--interval", type=int, default=10)
    sp.add_argument("--timeout", type=int, default=3600)

    sp = add("executions", cmd_executions, "list executions with filters")
    sp.add_argument("--workflow-id",
                    help="UUID or name (with --account); omit to list across "
                         "all workflows in the org")
    sp.add_argument("--statuses", help="comma-separated; e.g. completed,failed")
    sp.add_argument("--owner-uids", help="comma-separated")
    sp.add_argument("--owner-names", help="comma-separated")
    sp.add_argument("--search")
    sp.add_argument("--date-from", help="ISO 8601, inclusive")
    sp.add_argument("--date-to", help="ISO 8601, exclusive")
    sp.add_argument("--limit", type=int, default=20,
                    help="max 100; 0 = all in one response (no cursor)")
    sp.add_argument("--cursor", help="resume from a previous next_cursor")
    sp.add_argument("--all", action="store_true", help="follow next_cursor to the end")

    sp = add("status", cmd_status, "execution status + per-step table")
    sp.add_argument("--exec-id", required=True)

    sp = add("results", cmd_results, "per-step outputs, including full table rows")
    sp.add_argument("--exec-id", required=True)
    sp.add_argument("--step", help="only steps whose step_name matches (substring)")
    sp.add_argument("--out", help="directory to write one JSON file per step")
    sp.add_argument("--excel", action="store_true",
                    help="also export table steps to .xlsx (requires --out)")

    sp = add("cancel", cmd_cancel, "cancel an execution (reliable only when paused)")
    sp.add_argument("--exec-id", required=True)

    sp = add("table", cmd_table, "fetch table data from a step's table_v1_log_id")
    sp.add_argument("--log-id", required=True)
    sp.add_argument("--cursor", type=int, default=0)
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--search")
    sp.add_argument("--export-excel", action="store_true",
                    help="export the table to xlsx and download it")
    sp.add_argument("--out", help="output path for --export-excel")

    sp = add("documents", cmd_documents, "list/download execution documents")
    sp.add_argument("action", choices=["list", "download", "download-all"])
    sp.add_argument("--exec-id", help="for list / download-all")
    sp.add_argument("--doc-id", help="for download")
    sp.add_argument("--out", help="output directory")
    sp.add_argument("--concurrency", type=int, default=8)
    only = sp.add_mutually_exclusive_group()
    only.add_argument("--input-only", action="store_true",
                      help="download-all: only the documents the execution "
                           "was started with")
    only.add_argument("--output-only", action="store_true",
                      help="download-all: only documents generated by the execution")

    sp = add("versions", cmd_versions, "list workflow version history")
    sp.add_argument("--workflow-id", required=True)

    sp = add("validate", cmd_validate, "validate the active draft server-side")
    sp.add_argument("--workflow-id", required=True)

    sp = add("diff", cmd_diff, "compare a local workflow JSON against remote")
    sp.add_argument("--workflow-id", required=True)
    sp.add_argument("--json", required=True, help="local workflow JSON")
    sp.add_argument("--draft", action="store_true", help="diff against the active draft")
    sp.add_argument("--version", type=int, help="diff against a published version")

    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "publish" and not args.promote_version and not args.name:
        die("publish requires --name (the published_name) unless --promote-version is used")
    try:
        return args.func(args)
    except AccountError as e:
        die(str(e))
    except FaiError as e:
        detail = f" | {e.body[:300]}" if e.body else ""
        die(f"{e}{detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
