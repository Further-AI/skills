#!/usr/bin/env python3
"""Tier-1 execution triage for the fai:triage skill. stdlib only.

Commands:
  inspect <execution_id|url> [--env E] [--account A | --org-id O] [--json]
      Header + step table + failed-step details + anomaly flags.
  step <execution_id|url> <step_name> [--env E] [--account A | --org-id O]
       [--rows N] [--json]
      Full output of one step (keyed off input.step_name). Table steps also
      fetch the first page of table rows.

Execution URLs (https://app.furtherai.com/workflow-execution/<id>/...) are
accepted anywhere an execution_id is; the id is parsed out and the environment
inferred from the host (app.furtherai.com -> prod, app-staging -> staging)
unless --env is passed explicitly.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_client import (  # noqa: E402
    FaiClient,
    FaiError,
    NON_TERMINAL_STATUSES,
    TERMINAL_STATUSES,
)
from fai_accounts import AccountError, default_env, resolve_org_id  # noqa: E402

EXEC_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")
URL_ID_RE = re.compile(r"/workflow-execution/([0-9a-fA-F]{24})")

HOST_ENV = {
    "app.furtherai.com": "prod",
    "app-staging.furtherai.com": "staging",
    "localhost": "local",
    "127.0.0.1": "local",
}

IN_PROGRESS_WARN_S = 15 * 60   # step in_progress longer than this is flagged
STALE_WARN_S = 30 * 60         # non-terminal execution not updated for this long

STEP_TABLE_LIMIT = 25          # rows fetched for a table step by default


# --------------------------------------------------------------------- parsing

def parse_execution_ref(ref: str) -> tuple[str, str | None]:
    """Return (execution_id, env_hint_or_None) from a bare id or an app URL."""
    ref = ref.strip()
    if EXEC_ID_RE.match(ref):
        return ref.lower(), None
    m = URL_ID_RE.search(ref)
    if not m:
        raise SystemExit(
            f"error: {ref!r} is neither a 24-hex execution id nor a "
            f"workflow-execution URL"
        )
    env_hint = None
    host_m = re.search(r"https?://([^/]+)", ref)
    if host_m:
        host = host_m.group(1).split(":")[0].lower()
        env_hint = HOST_ENV.get(host)
    return m.group(1).lower(), env_hint


def parse_ts(value) -> datetime | None:
    """Timestamps arrive as ISO strings or epoch numbers; normalize to aware UTC."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 1e12:  # epoch milliseconds
            value /= 1000.0
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        v = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def fmt_ts(value) -> str:
    dt = parse_ts(value)
    if not dt:
        return "-" if value is None else str(value)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def fmt_duration(seconds) -> str:
    if seconds is None:
        return "-"
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if s < 60:
        return f"{s:.0f}s" if s >= 10 else f"{s:.1f}s"
    m, rem = divmod(int(s), 60)
    if m < 60:
        return f"{m}m {rem:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def age_seconds(value) -> float | None:
    dt = parse_ts(value)
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()


# ----------------------------------------------------------------- client glue

def build_client(args, env_hint: str | None) -> FaiClient:
    env = args.env or env_hint or default_env()
    if args.env and env_hint and args.env != env_hint:
        print(
            f"warning: --env {args.env} overrides env {env_hint!r} inferred "
            f"from the URL host",
            file=sys.stderr,
        )
    org_id = args.org_id
    if not org_id and args.account:
        org_id = resolve_org_id(args.account, env)
    return FaiClient(env=env, org_id=org_id, skill_name="triage")


def fetch_log(client: FaiClient, execution_id: str) -> dict:
    log = client.execution_log(execution_id)
    if not isinstance(log, dict):
        raise FaiError(f"unexpected execution-log payload: {str(log)[:200]}")
    return log


# ------------------------------------------------------------------ step types

def step_list(log: dict) -> list[dict]:
    steps = ((log.get("result") or {}).get("steps")) or []
    return [s for s in steps if isinstance(s, dict)]


def step_name_of(step: dict) -> str:
    return ((step.get("input") or {}).get("step_name")) or step.get("title") or "?"


def fetch_version_step_types(client: FaiClient, workflow_id, version) -> dict:
    """Map step_name -> genuine type from the workflow version's step config.

    Used only as the fallback when step_type_raw is null (old executions).
    Failure is non-fatal — we degrade to the lossy display bucket.
    """
    if not workflow_id or version is None:
        return {}
    try:
        data = client.get(
            f"/api/v1/workflow-builder/workflows/{workflow_id}/versions/{version}"
        )
    except FaiError as e:
        print(f"warning: could not fetch workflow version config: {e}", file=sys.stderr)
        return {}
    steps = None
    if isinstance(data, dict):
        for container in (data, data.get("content"), data.get("workflow")):
            if isinstance(container, dict) and isinstance(container.get("steps"), list):
                steps = container["steps"]
                break
    if not steps:
        return {}
    out = {}
    for s in steps:
        # Workflow content steps key on "name" ("step_name" kept as a
        # defensive secondary for older shapes).
        if isinstance(s, dict) and (s.get("name") or s.get("step_name")):
            out[s.get("name") or s.get("step_name")] = s.get("type")
    return out


def resolve_step_types(client: FaiClient, log: dict, steps: list[dict]) -> dict:
    """Map step_name -> (type, source). source: raw | version | display.

    step_type is a LOSSY display bucket (several real types collapse onto one
    label) — trust it only when both step_type_raw and the version config fail.
    """
    version_types = {}
    if any(not s.get("step_type_raw") for s in steps):
        req = log.get("request") or {}
        version_types = fetch_version_step_types(
            client, req.get("workflow_id"),
            req.get("workflow_version") if req.get("workflow_version") is not None
            else log.get("workflow_version")
        )
    resolved = {}
    for s in steps:
        name = step_name_of(s)
        if s.get("step_type_raw"):
            resolved[name] = (s["step_type_raw"], "raw")
        elif version_types.get(name):
            resolved[name] = (version_types[name], "version")
        else:
            resolved[name] = (s.get("step_type") or "?", "display")
    return resolved


# --------------------------------------------------------------------- inspect

def collect_flags(log: dict, steps: list[dict]) -> list[str]:
    flags = []
    status = log.get("status")

    if status in ("cancelled", "terminated"):
        flags.append(
            f"status {status!r} is TERMINAL — this run will never resume. "
            f"Retry means a new execution with the same documents."
        )

    if status in NON_TERMINAL_STATUSES:
        stale = age_seconds(log.get("last_updated_at"))
        if stale is not None and stale > STALE_WARN_S:
            flags.append(
                f"execution is {status!r} but last_updated_at is "
                f"{fmt_duration(stale)} old (threshold {STALE_WARN_S // 60}m) — "
                f"likely stuck; check Tier 2 (Logfire) if available."
            )

    for s in steps:
        if s.get("status") == "in_progress":
            running = age_seconds(s.get("start_time"))
            if running is not None and running > IN_PROGRESS_WARN_S:
                flags.append(
                    f"step {step_name_of(s)!r} has been in_progress for "
                    f"{fmt_duration(running)} (threshold {IN_PROGRESS_WARN_S // 60}m)."
                )

    if status == "failed" and not any(s.get("status") == "failed" for s in steps):
        flags.append(
            "execution failed but no step has status 'failed' — the failure "
            "happened outside step bodies (dispatch/infra). Use Tier 2 "
            "(Logfire) or escalate."
        )
    return flags


def print_header(client: FaiClient, execution_id: str, log: dict) -> None:
    status = log.get("status") or "?"
    kind = (
        "terminal" if status in TERMINAL_STATUSES
        else "non-terminal" if status in NON_TERMINAL_STATUSES
        else "unknown"
    )
    req = log.get("request") or {}
    owner = (
        log.get("owner_name") or log.get("owner_email")
        or log.get("owner_uid") or log.get("owner_oid") or "-"
    )
    docs = req.get("documents") or []
    print(f"Execution:  {execution_id}  ({client.env})")
    print(f"URL:        {client.execution_url(execution_id)}")
    print(f"Status:     {status}  [{kind}]")
    if log.get("title"):
        print(f"Title:      {log['title']}")
    wf_name = req.get("workflow_name") or "-"
    print(f"Workflow:   {wf_name}  ({req.get('workflow_id') or '-'})")
    print(f"Version:    {req.get('workflow_version') if req.get('workflow_version') is not None else '-'}")
    print(f"Owner:      {owner}")
    print(f"Created:    {fmt_ts(log.get('created_at'))}")
    print(f"Updated:    {fmt_ts(log.get('last_updated_at'))}")
    print(f"Ended:      {fmt_ts(log.get('end_time'))}")
    if isinstance(docs, list):
        print(f"Documents:  {len(docs)}")


def print_step_table(steps: list[dict], types: dict) -> None:
    if not steps:
        print("\nNo steps recorded in result.steps (run may not have started).")
        return
    used_version_fallback = used_display = False
    rows = []
    for i, s in enumerate(steps, 1):
        name = step_name_of(s)
        typ, source = types.get(name, ("?", "display"))
        marker = ""
        if source == "version":
            marker, used_version_fallback = "*", True
        elif source == "display":
            marker, used_display = "~", True
        dur = s.get("duration")
        if dur is None and s.get("status") == "in_progress":
            dur = age_seconds(s.get("start_time"))
        rows.append((str(i), name, f"{typ}{marker}", s.get("status") or "?", fmt_duration(dur)))

    widths = [max(len(r[c]) for r in rows + [("#", "Step", "Type", "Status", "Duration")])
              for c in range(5)]
    header = ("#", "Step", "Type", "Status", "Duration")
    print()
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    for r in rows:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))
    if used_version_fallback:
        print("\n  * type recovered from the workflow version's step config "
              "(step_type_raw missing on this execution)")
    if used_display:
        print("\n  ~ LOSSY display bucket only (step_type) — the real type may "
              "differ: agentic_extraction/guideline checks/combine_kv_tables "
              "all display as extract_from_document; function/"
              "workflow_dispatcher as wait; prepare_documents as email.")


def print_failed_details(steps: list[dict], types: dict) -> None:
    failed = [s for s in steps if s.get("status") == "failed"]
    for s in failed:
        name = step_name_of(s)
        typ = types.get(name, ("?", ""))[0]
        print(f"\n--- Failed step: {name} ({typ}) ---")
        print(f"  started:  {fmt_ts(s.get('start_time'))}")
        print(f"  ended:    {fmt_ts(s.get('end_time'))}")
        print(f"  duration: {fmt_duration(s.get('duration'))}")
        output = s.get("output")
        error = output.get("error") if isinstance(output, dict) else None
        if error:
            print("  error:")
            for line in str(error).splitlines() or [""]:
                print(f"    {line}")
        elif output is not None:
            print("  output (no .error field):")
            for line in json.dumps(output, indent=2, default=str).splitlines():
                print(f"    {line}")
        else:
            print("  no output recorded for this step.")


def cmd_inspect(args) -> int:
    execution_id, env_hint = parse_execution_ref(args.execution)
    client = build_client(args, env_hint)
    log = fetch_log(client, execution_id)
    if args.json:
        print(json.dumps(log, indent=2, default=str))
        return 0
    steps = step_list(log)
    types = resolve_step_types(client, log, steps)
    print_header(client, execution_id, log)
    print_step_table(steps, types)
    print_failed_details(steps, types)
    flags = collect_flags(log, steps)
    if flags:
        print("\nFlags:")
        for f in flags:
            print(f"  - {f}")
    return 0


# ------------------------------------------------------------------------ step

def find_step(steps: list[dict], wanted: str) -> dict:
    exact = [s for s in steps if step_name_of(s) == wanted]
    if len(exact) == 1:
        return exact[0]
    ci = [s for s in steps if step_name_of(s).lower() == wanted.lower()]
    if len(ci) == 1:
        return ci[0]
    sub = [s for s in steps if wanted.lower() in step_name_of(s).lower()]
    if len(sub) == 1:
        return sub[0]
    names = ", ".join(step_name_of(s) for s in steps) or "(none)"
    hint = "ambiguous" if len(sub) > 1 else "not found"
    raise SystemExit(f"error: step {wanted!r} {hint}. Steps in this execution: {names}")


def fetch_table_page(client: FaiClient, log_id: str, limit: int):
    try:
        return client.get(
            f"/api/v1/table/v1/logs/{log_id}",
            params={"cursor": 0, "limit": limit},
        )
    except FaiError as e:
        print(f"warning: could not fetch table rows for {log_id}: {e}", file=sys.stderr)
        return None


def print_table_page(table: dict, log_id: str) -> None:
    print(f"\nTable output (table_v1_log_id: {log_id}, first page):")
    schema = table.get("schema")
    if isinstance(schema, dict):
        cols = list((schema.get("properties") or schema).keys())
        if cols:
            print(f"  columns: {', '.join(cols)}")
    rows = table.get("data")
    if isinstance(rows, dict):  # some tables key rows by column
        rows = [rows]
    if not rows:
        print("  (no rows)")
        return
    for i, row in enumerate(rows):
        print(f"  row {i}: {json.dumps(row, default=str)}")
    pagination = table.get("pagination")
    if pagination:
        print(f"  pagination: {json.dumps(pagination, default=str)}")


def cmd_step(args) -> int:
    execution_id, env_hint = parse_execution_ref(args.execution)
    client = build_client(args, env_hint)
    log = fetch_log(client, execution_id)
    steps = step_list(log)
    step = find_step(steps, args.step_name)
    name = step_name_of(step)
    types = resolve_step_types(client, log, [step])
    typ, source = types.get(name, ("?", "display"))

    output = step.get("output")
    log_id = output.get("table_v1_log_id") if isinstance(output, dict) else None
    table = fetch_table_page(client, log_id, args.rows) if log_id else None

    if args.json:
        print(json.dumps({"step": step, "table_page": table}, indent=2, default=str))
        return 0

    print(f"Execution:  {execution_id}  ({client.env})")
    print(f"URL:        {client.execution_url(execution_id)}")
    print(f"Step:       {name}")
    print(f"Type:       {typ}  (source: {source}{'' if source != 'display' else ' — LOSSY bucket'})")
    print(f"Status:     {step.get('status') or '?'}")
    print(f"Started:    {fmt_ts(step.get('start_time'))}")
    print(f"Ended:      {fmt_ts(step.get('end_time'))}")
    print(f"Duration:   {fmt_duration(step.get('duration'))}")
    print("\nOutput:")
    if output is None:
        print("  (none)")
    else:
        for line in json.dumps(output, indent=2, default=str).splitlines():
            print(f"  {line}")
    if table:
        print_table_page(table, log_id)
    return 0


# ------------------------------------------------------------------------ main

def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--env", choices=["prod", "staging", "local"],
                   help="platform environment (default: from URL host, else accounts.json)")
    p.add_argument("--account", help="account slug/name/domain from ~/.fai/accounts.json")
    p.add_argument("--org-id", help="explicit PropelAuth org id (wins over --account)")
    p.add_argument("--json", action="store_true", help="print raw JSON instead of tables")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="header + step table + failures + flags")
    p_inspect.add_argument("execution", help="execution id or app URL")
    add_common(p_inspect)
    p_inspect.set_defaults(func=cmd_inspect)

    p_step = sub.add_parser("step", help="full output of one step")
    p_step.add_argument("execution", help="execution id or app URL")
    p_step.add_argument("step_name", help="canonical step name (input.step_name)")
    p_step.add_argument("--rows", type=int, default=STEP_TABLE_LIMIT,
                        help=f"table rows to fetch for table steps (default {STEP_TABLE_LIMIT})")
    add_common(p_step)
    p_step.set_defaults(func=cmd_step)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FaiError, AccountError) as e:
        print(f"error: {e}", file=sys.stderr)
        if isinstance(e, FaiError) and e.body:
            print(f"  detail: {e.body[:500]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
