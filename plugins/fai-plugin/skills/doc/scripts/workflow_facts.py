#!/usr/bin/env python3
"""Collect the facts a workflow document needs, as JSON. stdlib only.

Data layer only — this script gathers and normalizes, it never renders. The
doc skill decides what the .docx/.pdf looks like.

Offline:   workflow_facts.py --json workflow.json
Platform:  workflow_facts.py --account acme --workflow-id <uuid-or-name> [--env prod]
Combined:  both — the local JSON wins for step content; the platform adds
           version info, submission activity, account name, and links.

Emits one JSON document on stdout, or to --out (in which case stdout gets a
short human digest instead). Read-only: no writes, publishes, or executions.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_client import ENVIRONMENTS, FaiClient, FaiError  # noqa: E402
from fai_accounts import (  # noqa: E402
    AccountError, default_env, resolve_account, resolve_org_id, resolve_workflow_id,
)

LOGS_PATH = "/api/v1/workflow-execution/logs"
STATS_WINDOW_DAYS = 30
MAX_STEP_ROWS = 25
MAX_DESCRIPTION_CHARS = 420

# The execution-log endpoint has drifted its date field across API versions.
EXEC_DATE_KEYS = ("created_at", "created_on", "start_time", "createdAt")

# Connector step types shown as "Integrations" (label = UI connector name).
INTEGRATION_STEPS = {
    "google_maps": "Google Maps", "nhtsa": "NHTSA", "riskmeter": "RiskMeter",
    "hazardhub": "HazardHub", "maprisk": "MapRisk", "pitchbook": "PitchBook",
    "cotality_valuation": "Cotality", "snapsheet": "Snapsheet",
    "snapsheet_payments": "Snapsheet Payments", "applied_epic": "Applied Epic",
    "benefitpoint": "BenefitPoint", "qqcatalyst": "QQCatalyst", "ams360": "AMS360",
    "sharepoint": "SharePoint", "outlook_mail": "Outlook Mail", "imageright": "ImageRight",
    "email": "Email (Zapier)",
}

STEP_CATEGORIES = {
    "ingest": {"prepare_documents", "classify_documents", "knowledge_base"},
    "extraction": {
        "extract_from_multiple_sources", "extract_rows_from_multiple_sources",
        "extract_mixed_schema_from_multiple_sources", "agentic_extraction",
        "sov_mapping", "combine_kv_tables", "extract_from_document",
        "extract_from_email",
    },
    "qa": {
        "generate_qa_table_from_agent", "multi_column_qa", "agentic_guideline_check",
        "extract_guidelines", "check_guidelines", "compare_document_data",
        "compare_documents", "generate_qa_table_from_kb",
    },
    "enrich": {
        "ofac_agent", "osha_agent", "trellis_law", "web_search", "web_agent",
        "execution_matching", "enrich_addresses_with_gmaps",
        *(t for t in INTEGRATION_STEPS if t != "email"),
    },
    "logic": {
        "custom_step", "function", "decision", "loop", "pause", "hold",
        "manual_input", "run_workflow",
    },
    "output": {
        "email", "fill_docx", "text_block", "document_viewer",
        "submission_summary_generator",
    },
}

CATEGORY_META = {  # slug -> (label, palette hex)
    "ingest": ("Ingest", "#3481C2"),
    "extraction": ("Extraction", "#E8972C"),
    "qa": ("QA & compliance", "#8B5CF6"),
    "enrich": ("Enrichment", "#14B8A6"),
    "output": ("Output", "#34A853"),
    "logic": ("Logic & code", "#6f6d64"),
    "other": ("Other", "#c3c1b8"),
}


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def first(d: Any, keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def content_of(payload: Any) -> dict:
    """The workflow GET sometimes nests the definition under 'content'."""
    return payload.get("content", payload) if isinstance(payload, dict) else {}


def category_of(step_type: str) -> str:
    for cat, types in STEP_CATEGORIES.items():
        if step_type in types:
            return cat
    return "other"


# ---------------------------------------------------------------- platform

def fetch_workflow(client: FaiClient, workflow_id: str) -> tuple[dict, dict]:
    """Returns (content, meta). meta: published_version, published_at."""
    payload = client.get(f"/api/v1/workflows/{workflow_id}")
    live = payload.get("published_version") if isinstance(payload, dict) else None
    meta: dict = {"published_version": live, "published_at": None}
    for v in (payload.get("versions") or []) if isinstance(payload, dict) else []:
        if not isinstance(v, dict):
            continue
        num = v.get("version") if v.get("version") is not None else v.get("published_version")
        if num is not None and num == live:
            meta["published_at"] = str(v.get("published_at") or v.get("created_at") or "")[:10] or None
            break
    return content_of(payload), meta


def _execution_count(client: FaiClient, params: dict) -> Optional[int]:
    """total_count for a filter; limit=0 asks for count only, limit=1 fallback
    for servers that 422 on limit=0."""
    for limit in (0, 1):
        try:
            data = client.get(LOGS_PATH, params={**params, "limit": limit})
            return data.get("total_count") if isinstance(data, dict) else None
        except FaiError as e:
            if limit == 0 and e.status == 422:
                continue
            raise
    return None


def fetch_activity(client: FaiClient, workflow_id: str) -> Optional[dict]:
    """Submission activity over the last STATS_WINDOW_DAYS.

    Returns None when the endpoint is unavailable — the caller omits the
    activity block entirely rather than emitting zeros it cannot vouch for.
    """
    date_from = (datetime.now(timezone.utc) - timedelta(days=STATS_WINDOW_DAYS)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    windowed = {"workflow_id": workflow_id, "date_from": date_from}
    try:
        total = _execution_count(client, windowed)
        completed = _execution_count(client, {**windowed, "statuses": ["completed"]})
        failed = _execution_count(client, {**windowed, "statuses": ["failed"]})
        latest = client.get(LOGS_PATH, params={"workflow_id": workflow_id, "limit": 1})
        rows = (latest.get("workflow_executions") or []) if isinstance(latest, dict) else []
        last_run_at = first(rows[0], EXEC_DATE_KEYS) if rows else None
    except FaiError as e:
        print(f"note: submission activity unavailable ({e}); omitting the activity block",
              file=sys.stderr)
        return None
    pct = (round(100 * completed / total, 1)
           if total and completed is not None else None)
    return {
        "window_days": STATS_WINDOW_DAYS,
        "total": total,
        "completed": completed,
        "completed_pct": pct,
        "failed": failed,
        "last_run_at": str(last_run_at) if last_run_at is not None else None,
    }


# ------------------------------------------------------------- fact model

def truncate(text: str, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    """Collapse whitespace and cut at a word boundary."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + " …"


def step_rows(content: dict, max_rows: int = MAX_STEP_ROWS) -> tuple[list[dict], int]:
    """Display rows (workflow_dispatcher hidden), capped at max_rows.

    Returns (rows, total_after_hiding). max_rows=0 means no cap. In a workflow
    definition the step's own 'type' is the authoritative step type — the lossy
    'step_type' display bucket only shows up on execution payloads, so raw is
    what categorization keys off.
    """
    rows: list[dict] = []
    for s in content.get("steps") or []:
        if not isinstance(s, dict):
            continue
        raw = s.get("type") or s.get("step_type_raw") or s.get("step_type") or "?"
        if raw == "workflow_dispatcher":
            continue
        cat = category_of(raw)
        label, color = CATEGORY_META[cat]
        rows.append({
            "index": len(rows) + 1,
            "name": s.get("name") or "?",
            "step_type": s.get("step_type") or raw,
            "step_type_raw": raw,
            "category": cat,
            "category_label": label,
            "category_color": color,
        })
    total = len(rows)
    if max_rows and total > max_rows:
        return rows[:max_rows], total
    return rows, total


def schedule_of(options: dict) -> Optional[str]:
    """Schedule string, whether it sits at the top level or under settings, and
    whether it is a plain string or a dict."""
    sched = options.get("schedule")
    if sched is None and isinstance(options.get("settings"), dict):
        sched = options["settings"].get("schedule")
    if isinstance(sched, str) and sched.strip():
        return sched.strip()
    if isinstance(sched, dict):
        for key in ("cron", "interval", "frequency", "expression"):
            if isinstance(sched.get(key), str) and sched[key].strip():
                return sched[key].strip()
    return None


def option_chips(options: dict) -> list[str]:
    """Human labels for the enabled parts of a workflow options block."""
    chips = []
    schedule = schedule_of(options)
    if schedule:
        chips.append(f"Schedule: {schedule}")
    if options.get("assistant_email_id"):
        chips.append("Email intake")
    if options.get("enable_parallel_execution"):
        chips.append("Parallel execution")
    if options.get("enable_knowledge_sources"):
        chips.append("Knowledge sources")
    if options.get("enable_submission_merging"):
        chips.append("Submission merging")
    if (options.get("webhook_config") or {}).get("completion_webhook_urls"):
        chips.append("Webhook delivery")
    if options.get("api_response_steps"):
        chips.append("API access")
    file_type = (options.get("file_access_config") or {}).get("type")
    if file_type in ("box", "oauth2"):
        chips.append("Box file access" if file_type == "box" else "OAuth2 file access")
    return chips


def integrations_used(content: dict) -> list[str]:
    """Connector display names, in first-appearance order."""
    seen: list[str] = []
    for s in content.get("steps") or []:
        if not isinstance(s, dict):
            continue
        label = INTEGRATION_STEPS.get(s.get("type") or "")
        if label and label not in seen:
            seen.append(label)
    return seen


def print_digest(facts: dict) -> None:
    wf = facts["workflow"]
    print(f"Workflow: {wf['name']}")
    if facts.get("account", {}).get("name"):
        print(f"Account:  {facts['account']['name']} ({facts['account']['slug']}, {facts['env']})")
    live = wf.get("published_version")
    if live is not None:
        at = f" (published {wf['published_at']})" if wf.get("published_at") else ""
        print(f"Version:  Live v{live}{at}")
    shown, total = facts["steps_shown"], facts["steps_total"]
    more = f" (+{total - shown} not listed)" if total > shown else ""
    print(f"Steps:    {total}{more}")
    if facts.get("integrations"):
        print(f"Integrations: {', '.join(facts['integrations'])}")
    if facts.get("option_chips"):
        print(f"Options:  {', '.join(facts['option_chips'])}")
    act = facts.get("activity")
    if act:
        pct = f" — {act['completed_pct']}% completed" if act.get("completed_pct") is not None else ""
        print(f"Activity: {act['total']} Submissions in {act['window_days']}d{pct}"
              + (f", {act['failed']} failed" if act.get("failed") else ""))
        if act.get("last_run_at"):
            print(f"Last run: {act['last_run_at']}")
    if wf.get("app_url"):
        print(f"App URL:  {wf['app_url']}")


# -------------------------------------------------------------------- main

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Gather workflow facts as JSON for the doc skill (read-only).")
    ap.add_argument("--json", help="local workflow JSON (offline mode; wins for step content)")
    ap.add_argument("--account", help="account slug/name/domain from ~/.fai/accounts.json")
    ap.add_argument("--org-id", help="explicit org UUID (overrides --account)")
    ap.add_argument("--workflow-id", help="workflow UUID, or a name resolved via --account")
    ap.add_argument("--env", choices=sorted(ENVIRONMENTS),
                    help="default: accounts.json defaults.env")
    ap.add_argument("--max-steps", type=int, default=MAX_STEP_ROWS,
                    help=f"cap the step list (default {MAX_STEP_ROWS}; 0 = no cap)")
    ap.add_argument("--out", help="write JSON here (default: stdout)")
    args = ap.parse_args(argv)

    if not args.json and not args.workflow_id:
        ap.error("need --json FILE (offline) and/or --account/--workflow-id (platform)")
    if args.max_steps < 0:
        ap.error("--max-steps must be >= 0")

    local = None
    if args.json:
        try:
            local = json.loads(Path(args.json).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            die(f"cannot read {args.json}: {exc}")
        if not isinstance(local, dict) or "steps" not in local or "name" not in local:
            die(f"{args.json} does not look like a workflow JSON (missing 'name' or 'steps')")

    content: dict = local or {}
    account: dict = {}
    env = None
    org_id = None
    workflow_id = None
    app_url = None
    meta: dict = {"published_version": None, "published_at": None}
    activity = None

    if args.workflow_id:
        try:
            env = args.env or default_env()
            if args.org_id:
                org_id = args.org_id
            elif args.account:
                org_id = resolve_org_id(args.account, env)
            else:
                die("platform mode needs --account or --org-id")
            workflow_id = (resolve_workflow_id(args.account, args.workflow_id, env)
                           if args.account else args.workflow_id)
            if args.account:
                slug, record = resolve_account(args.account)
                account = {"slug": slug, "name": record.get("name") or slug}
        except AccountError as exc:
            die(str(exc))
        client = FaiClient(env=env, org_id=org_id, skill_name="doc")
        try:
            remote, meta = fetch_workflow(client, workflow_id)
        except FaiError as exc:
            die(str(exc))
        content = {**remote, **local} if local else remote  # local wins for step content
        app_url = client.workflow_url(workflow_id)
        activity = fetch_activity(client, workflow_id)

    if not content.get("name") or content.get("steps") is None:
        die("workflow content has no 'name'/'steps' — nothing to describe")

    steps, steps_total = step_rows(content, args.max_steps)
    options = content.get("options") or {}
    facts: dict = {
        "workflow": {
            "id": workflow_id,
            "name": content["name"],
            "description": truncate(content.get("comments")
                                    or content.get("description") or ""),
            "app_url": app_url,
            "published_version": meta.get("published_version"),
            "published_at": meta.get("published_at"),
        },
        "account": account,
        "env": env,
        "org_id": org_id,
        "steps": steps,
        "steps_shown": len(steps),
        "steps_total": steps_total,
        "integrations": integrations_used(content),
        "option_chips": option_chips(options),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if activity is not None:
        facts["activity"] = activity

    blob = json.dumps(facts, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(blob)
        print_digest(facts)
        print(f"JSON:     {out.resolve()}")
    else:
        sys.stdout.write(blob)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FaiError, AccountError) as e:
        print(f"error: {e}", file=sys.stderr)
        if isinstance(e, FaiError) and e.body:
            print(f"  detail: {e.body[:500]}", file=sys.stderr)
        sys.exit(1)
