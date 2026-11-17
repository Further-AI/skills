"""Local account database for the fai plugin: ~/.fai/accounts.json.

One JSON file, all accounts, human-editable. Every skill resolves accounts
through this module so users can say `--account acme` instead of pasting
UUIDs. Explicit --org-id / --workflow-id flags always override resolution.

Schema:

{
  "defaults": {"env": "prod"},
  "accounts": {
    "<slug>": {
      "name": "<Display Name>",
      "propelauth_org_id": {"prod": "<uuid>|null", "staging": "<uuid>|null"},
      "linear_project_id": null,
      "slack_channel_id": null,
      "notion_doc_id": null,
      "email_domain": null,
      "local_folder": "<path to the account's local folder>|null",
      "workflows": [
        {"name": "<workflow_name>",
         "id": {"prod": "<uuid>|null", "staging": "<uuid>|null"}}
      ],
      "eval_datasets": [{"name": "<dataset name>", "id": "<objectid>", "env": "prod"}],
      "engagement_manager": null,
      "fde": null,
      "notes": null
    }
  }
}

All identifier fields are nullable — a record is useful even half-filled.
Org and workflow IDs are per-environment because staging and prod are
separate PropelAuth instances; an ID from one does not exist in the other.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

CONFIG_DIR = Path(os.environ.get("FAI_CONFIG_DIR", str(Path.home() / ".fai")))
ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"

ACCOUNT_FIELDS = (
    "name", "propelauth_org_id", "linear_project_id", "slack_channel_id",
    "notion_doc_id", "email_domain", "local_folder", "workflows",
    "eval_datasets", "engagement_manager", "fde", "notes",
)


class AccountError(RuntimeError):
    pass


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def empty_db() -> dict:
    return {"defaults": {"env": "prod"}, "accounts": {}}


def empty_account(name: str) -> dict:
    return {
        "name": name,
        "propelauth_org_id": {"prod": None, "staging": None},
        "linear_project_id": None,
        "slack_channel_id": None,
        "notion_doc_id": None,
        "email_domain": None,
        "local_folder": None,
        "workflows": [],
        "eval_datasets": [],
        "engagement_manager": None,
        "fde": None,
        "notes": None,
    }


def load_db() -> dict:
    if not ACCOUNTS_FILE.exists():
        return empty_db()
    try:
        db = json.loads(ACCOUNTS_FILE.read_text())
    except json.JSONDecodeError as e:
        raise AccountError(f"{ACCOUNTS_FILE} is not valid JSON: {e}")
    db.setdefault("defaults", {"env": "prod"})
    db.setdefault("accounts", {})
    return db


def save_db(db: dict) -> None:
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACCOUNTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(db, indent=2, sort_keys=False) + "\n")
    tmp.replace(ACCOUNTS_FILE)


def default_env(db: Optional[dict] = None) -> str:
    db = db or load_db()
    return db.get("defaults", {}).get("env", "prod")


def resolve_account(ref: str, db: Optional[dict] = None) -> tuple[str, dict]:
    """Resolve a user-supplied reference (slug, display name, or email domain)
    to (slug, record). Raises AccountError with candidates on ambiguity."""
    db = db or load_db()
    accounts = db["accounts"]
    if not accounts:
        raise AccountError(
            f"No accounts in {ACCOUNTS_FILE}. Run the fai:account skill "
            f"(add or seed) to populate it."
        )
    ref_slug = _slugify(ref)
    if ref_slug in accounts:
        return ref_slug, accounts[ref_slug]
    matches = []
    for slug, rec in accounts.items():
        name_slug = _slugify(rec.get("name") or "")
        if ref_slug == name_slug or ref.lower() == (rec.get("email_domain") or "").lower():
            matches.append(slug)
        elif ref_slug and (ref_slug in slug or ref_slug in name_slug):
            matches.append(slug)
    matches = list(dict.fromkeys(matches))
    if len(matches) == 1:
        return matches[0], accounts[matches[0]]
    if not matches:
        raise AccountError(
            f"No account matches {ref!r}. Known: {', '.join(sorted(accounts))}"
        )
    raise AccountError(f"Ambiguous account {ref!r}: matches {', '.join(matches)}")


def resolve_org_id(ref: str, env: str, db: Optional[dict] = None) -> str:
    slug, rec = resolve_account(ref, db)
    org = (rec.get("propelauth_org_id") or {}).get(env)
    if not org:
        raise AccountError(
            f"Account {slug!r} has no PropelAuth org ID for env {env!r}. "
            f"Set it: fai:account set {slug} propelauth_org_id.{env} <uuid>"
        )
    return org


def resolve_workflow_id(account_ref: str, workflow_ref: str, env: str,
                        db: Optional[dict] = None) -> str:
    """workflow_ref may be a workflow name from the account record or a raw UUID."""
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    workflow_ref.lower()):
        return workflow_ref
    slug, rec = resolve_account(account_ref, db)
    wf_slug = _slugify(workflow_ref)
    candidates = [w for w in rec.get("workflows", [])
                  if _slugify(w.get("name", "")) == wf_slug
                  or wf_slug in _slugify(w.get("name", ""))]
    if len(candidates) != 1:
        names = ", ".join(w.get("name", "?") for w in rec.get("workflows", [])) or "(none)"
        raise AccountError(
            f"Workflow {workflow_ref!r} not uniquely found on account {slug!r}. "
            f"Known workflows: {names}"
        )
    wf_id = (candidates[0].get("id") or {}).get(env)
    if not wf_id:
        raise AccountError(
            f"Workflow {candidates[0].get('name')!r} on {slug!r} has no ID for env {env!r}."
        )
    return wf_id


def set_field(slug: str, dotted_field: str, value: Any, db: Optional[dict] = None) -> dict:
    """Set a (possibly dotted, e.g. propelauth_org_id.prod) field and save."""
    db = db or load_db()
    if slug not in db["accounts"]:
        raise AccountError(f"Unknown account {slug!r}")
    rec = db["accounts"][slug]
    parts = dotted_field.split(".")
    if parts[0] not in ACCOUNT_FIELDS:
        raise AccountError(f"Unknown field {parts[0]!r}; known: {', '.join(ACCOUNT_FIELDS)}")
    target = rec
    for part in parts[:-1]:
        if not isinstance(target.get(part), dict):
            target[part] = {}
        target = target[part]
    target[parts[-1]] = value
    save_db(db)
    return rec


def upsert_account(name: str, db: Optional[dict] = None, slug: Optional[str] = None) -> tuple[str, dict]:
    db = db or load_db()
    slug = slug or _slugify(name)
    if slug not in db["accounts"]:
        db["accounts"][slug] = empty_account(name)
        save_db(db)
    return slug, db["accounts"][slug]
