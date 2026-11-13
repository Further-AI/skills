#!/usr/bin/env python3
"""Gather recent-updates data for the fai:deck skill. stdlib only.

Data layer only — this script gathers and normalizes, it never renders. The
deck skill composes the slides from the JSON it writes.

For each workflow on an account: version summaries (what was published inside
the window, plus the current live version) and submission stats over the same
window (volume, status split, completion rate, per-week counts, latest
failures).

  gather_updates.py --account acme [--workflow-id <uuid-or-name>]...
                    [--env prod|staging|local] [--since-days 30]
                    [--org-id <uuid>] [--out updates.json]

Workflows come from repeatable --workflow-id flags (raw UUID or a workflow
name from the account record); with no flags, every workflow on the account
record that has an ID for the chosen environment is included.

Writes one JSON file (--out) for the deck-composing step and prints a compact
per-workflow digest to stdout. Read-only: no writes, publishes, or executions.
Never prints secrets.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_client import ENVIRONMENTS, FaiClient, FaiError  # noqa: E402
from fai_accounts import (  # noqa: E402
    AccountError,
    default_env,
    resolve_account,
    resolve_org_id,
    resolve_workflow_id,
)

# The workflow document and execution-log list arrive from endpoints whose
# field names have drifted across API versions — read each value through an
# ordered key list and take the first hit.
VERSION_LIST_KEYS = ("versions", "workflow_versions", "version_history", "version_summaries")
VERSION_NUM_KEYS = ("version", "version_number", "workflow_version", "published_version")
VERSION_DATE_KEYS = ("published_at", "publish_date", "published_on", "publish_time",
                     "created_at", "updated_at", "last_updated_at")
VERSION_NOTES_KEYS = ("notes", "publish_notes", "release_notes", "version_notes",
                      "description", "comment", "message")
LIVE_VERSION_KEYS = ("published_version", "live_version", "current_version", "active_version")
WORKFLOW_NAME_KEYS = ("name", "workflow_name", "title")

EXEC_LIST_KEYS = ("logs", "data", "executions", "items", "results", "workflow_executions")
EXEC_ID_KEYS = ("_id", "id", "execution_id", "workflow_execution_id")
EXEC_DATE_KEYS = ("created_at", "created_on", "start_time", "createdAt")

MAX_FAILURES = 5


def first(d, keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


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


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def iso_date(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d") if dt else None


def week_start(dt: datetime) -> str:
    d = dt.astimezone(timezone.utc).date()
    return (d - timedelta(days=d.weekday())).isoformat()


# ----------------------------------------------------------------- versions

def extract_versions(wf: dict, since: datetime, live_version) -> list[dict]:
    """Version summaries published inside the window, plus the live version."""
    raw = first(wf, VERSION_LIST_KEYS, default=[])
    if not isinstance(raw, list):
        raw = []
    out = []
    for v in raw:
        if not isinstance(v, dict):
            continue
        num = first(v, VERSION_NUM_KEYS)
        published_at = parse_ts(first(v, VERSION_DATE_KEYS))
        notes = first(v, VERSION_NOTES_KEYS)
        is_live = live_version is not None and str(num) == str(live_version)
        in_window = published_at is not None and published_at >= since
        if not (in_window or is_live):
            continue
        out.append({
            "version": num,
            "published_at": iso(published_at),
            "notes": (str(notes).strip() or None) if notes is not None else None,
            "is_live": is_live,
            "published_in_window": in_window,
        })
    out.sort(key=lambda v: (v["published_at"] or ""), reverse=True)
    return out


# --------------------------------------------------------------- executions

def listify_executions(resp) -> list[dict]:
    if isinstance(resp, list):
        return [e for e in resp if isinstance(e, dict)]
    if isinstance(resp, dict):
        for k in EXEC_LIST_KEYS:
            if isinstance(resp.get(k), list):
                return [e for e in resp[k] if isinstance(e, dict)]
    return []


def execution_stats(client: FaiClient, execs: list[dict],
                    since: datetime, until: datetime) -> dict:
    """Totals, status split, completion rate, weekly volume, latest failures."""
    status_counts: Counter = Counter()
    weekly: Counter = Counter()
    failures = []
    for e in execs:
        status = str(e.get("status") or "unknown")
        status_counts[status] += 1
        dt = parse_ts(first(e, EXEC_DATE_KEYS))
        if dt:
            weekly[week_start(dt)] += 1
        if status == "failed":
            exec_id = str(first(e, EXEC_ID_KEYS, default=""))
            failures.append({
                "execution_id": exec_id or None,
                "date": iso_date(dt),
                "title": first(e, ("title", "name")),
                "url": client.execution_url(exec_id) if exec_id else None,
            })
    failures.sort(key=lambda f: f["date"] or "", reverse=True)

    # Contiguous weekly buckets across the whole window, zeros included.
    weeks = []
    cursor = datetime.fromisoformat(week_start(since)).replace(tzinfo=timezone.utc)
    last = datetime.fromisoformat(week_start(until)).replace(tzinfo=timezone.utc)
    while cursor <= last:
        key = cursor.date().isoformat()
        weeks.append({"week_start": key, "count": weekly.get(key, 0)})
        cursor += timedelta(days=7)

    total = len(execs)
    completed = status_counts.get("completed", 0)
    return {
        "total": total,
        "status_counts": dict(sorted(status_counts.items())),
        "completed": completed,
        "completion_rate_pct": round(100 * completed / total, 1) if total else None,
        "weekly": weeks,
        "recent_failures": failures[:MAX_FAILURES],
    }


# ------------------------------------------------------------------- gather

def gather_workflow(client: FaiClient, label: str | None, wf_id: str,
                    since: datetime, until: datetime, date_from: str) -> dict:
    wf = client.get(f"/api/v1/workflows/{wf_id}")
    if not isinstance(wf, dict):
        raise FaiError(f"unexpected workflow payload for {wf_id}: {str(wf)[:200]}")
    live_version = first(wf, LIVE_VERSION_KEYS)
    # The name lives inside the nested definition, not at the top level.
    content = wf.get("content") if isinstance(wf.get("content"), dict) else {}
    entry = {
        "workflow_id": wf_id,
        "name": (first(wf, WORKFLOW_NAME_KEYS) or first(content, WORKFLOW_NAME_KEYS)
                 or label or wf_id),
        "app_url": client.workflow_url(wf_id),
        "published_version": live_version,
        "versions": extract_versions(wf, since, live_version),
    }
    try:
        resp = client.get(
            "/api/v1/workflow-execution/logs",
            params={"workflow_id": wf_id, "date_from": date_from, "limit": 0},
        )
        execs = listify_executions(resp)
        # Re-filter locally in case the server ignores date_from; entries with
        # no parseable date are kept (trust the server-side filter for those).
        execs = [e for e in execs
                 if (dt := parse_ts(first(e, EXEC_DATE_KEYS))) is None or dt >= since]
        entry["executions"] = execution_stats(client, execs, since, until)
    except FaiError as e:
        print(f"warning: execution list failed for workflow {wf_id}: {e}", file=sys.stderr)
        entry["executions"] = {"error": str(e)}
    return entry


def print_digest(entry: dict, env: str) -> None:
    print(f"\n== {entry['name']} ({env}) ==")
    if entry.get("error"):
        print(f"  ERROR: {entry['error']}")
        return
    live = entry.get("published_version")
    print(f"  Live version:  {('v' + str(live)) if live is not None else '-'}")
    shipped = [v for v in entry["versions"] if v["published_in_window"]]
    print(f"  Published in window: {len(shipped)}")
    for v in entry["versions"]:
        notes = (v["notes"] or "(no notes)").replace("\n", " ")
        if len(notes) > 70:
            notes = notes[:67] + "..."
        live_mark = "  [live]" if v["is_live"] else ""
        date = (v["published_at"] or "?")[:10]
        print(f"    v{v['version']}  {date}  {notes}{live_mark}")
    stats = entry.get("executions") or {}
    if stats.get("error"):
        print(f"  Submissions: ERROR: {stats['error']}")
        return
    rate = stats.get("completion_rate_pct")
    split = ", ".join(f"{n} {s}" for s, n in (stats.get("status_counts") or {}).items())
    print(f"  Submissions:   {stats.get('total', 0)} total"
          + (f" — {split}" if split else "")
          + (f" ({rate}% completed)" if rate is not None else ""))
    weeks = ", ".join(f"{w['week_start']}: {w['count']}" for w in stats.get("weekly", []))
    if weeks:
        print(f"  Weekly volume: {weeks}")
    for f in stats.get("recent_failures", []):
        print(f"  Failed: {f['date'] or '?'}  {f['title'] or '-'}  {f['execution_id']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--account", required=True,
                        help="account slug/name/domain from ~/.fai/accounts.json")
    parser.add_argument("--workflow-id", action="append", default=[],
                        help="workflow UUID or name (repeatable; default: all "
                             "workflows on the account record for this env)")
    parser.add_argument("--env", choices=sorted(ENVIRONMENTS),
                        help="platform environment (default: accounts.json defaults.env)")
    parser.add_argument("--org-id", help="explicit PropelAuth org id (wins over --account)")
    parser.add_argument("--since-days", type=int, default=30,
                        help="window size in days (default 30)")
    parser.add_argument("--out", default="updates.json",
                        help="output JSON path (default updates.json)")
    args = parser.parse_args(argv)
    if args.since_days < 1:
        parser.error("--since-days must be >= 1")

    env = args.env or default_env()
    slug, record = resolve_account(args.account)
    org_id = args.org_id or resolve_org_id(args.account, env)
    client = FaiClient(env=env, org_id=org_id, skill_name="deck")

    if args.workflow_id:
        targets = [(ref, resolve_workflow_id(args.account, ref, env))
                   for ref in args.workflow_id]
    else:
        targets = [(w.get("name"), (w.get("id") or {}).get(env))
                   for w in record.get("workflows", [])
                   if (w.get("id") or {}).get(env)]
        if not targets:
            raise AccountError(
                f"Account {slug!r} has no workflows with a {env} ID in accounts.json. "
                f"Pass --workflow-id <uuid> (repeatable), or record the account's "
                f"workflows with the fai:account skill first."
            )
    seen: set[str] = set()
    targets = [(label, wid) for label, wid in targets
               if not (wid in seen or seen.add(wid))]

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=args.since_days)
    date_from = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    workflows = []
    hard_errors = 0
    for label, wf_id in targets:
        try:
            workflows.append(gather_workflow(client, label, wf_id, since, until, date_from))
        except FaiError as e:
            hard_errors += 1
            print(f"warning: workflow {wf_id} failed: {e}", file=sys.stderr)
            workflows.append({"workflow_id": wf_id, "name": label or wf_id,
                              "error": str(e)})

    payload = {
        "account": {"slug": slug, "name": record.get("name") or slug},
        "env": env,
        "window": {"since": iso(since), "until": iso(until), "days": args.since_days},
        "generated_at": iso(until),
        "workflows": workflows,
    }
    out = Path(args.out)
    out.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"Account:  {payload['account']['name']} ({slug}, {env})")
    print(f"Window:   last {args.since_days} days ({iso_date(since)} to {iso_date(until)})")
    for entry in workflows:
        print_digest(entry, env)
    print(f"\nJSON:     {out.resolve()}")

    if hard_errors == len(targets):
        print("error: every workflow failed to gather — nothing to build a deck from.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FaiError, AccountError) as e:
        print(f"error: {e}", file=sys.stderr)
        if isinstance(e, FaiError) and e.body:
            print(f"  detail: {e.body[:500]}", file=sys.stderr)
        sys.exit(1)
