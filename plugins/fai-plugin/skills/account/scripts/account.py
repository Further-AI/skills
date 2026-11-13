#!/usr/bin/env python3
"""fai:account helper — manage the local account database ~/.fai/accounts.json.

Subcommands:
  list                          all accounts, one line each
  get <ref>                     one account: pretty JSON + completeness line
  add <name> [--slug S]         create an empty account record
  set <ref> <field> <value>     set a (dotted) field, e.g. propelauth_org_id.prod
  add-workflow <ref> --name X [--prod-id UUID] [--staging-id UUID]
  add-dataset <ref> --name X --id OBJECTID [--env prod]
  remove <slug>                 delete a record (exact slug required)
  lookup <value>                which account owns this org/workflow/dataset ID,
                                email domain, or Slack channel
  seed <path>                   scan a folder and create/merge account records

Read commands (list/get/lookup) take --json for machine-readable output.
stdlib only; all reads/writes go through lib/fai_accounts.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_accounts import (  # noqa: E402
    ACCOUNTS_FILE,
    AccountError,
    _slugify,
    empty_account,
    load_db,
    resolve_account,
    save_db,
    set_field,
    upsert_account,
)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def parse_value(raw: str):
    """'null'/'none' -> None; valid JSON (numbers, bools, lists) parses; else string."""
    if raw.lower() in ("null", "none"):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def missing_fields(rec: dict) -> list[str]:
    missing = []
    for env in ("prod", "staging"):
        if not (rec.get("propelauth_org_id") or {}).get(env):
            missing.append(f"propelauth_org_id.{env}")
    for field in ("linear_project_id", "slack_channel_id", "notion_doc_id",
                  "email_domain", "local_folder", "engagement_manager", "fde", "notes"):
        if rec.get(field) in (None, ""):
            missing.append(field)
    if not rec.get("workflows"):
        missing.append("workflows")
    if not rec.get("eval_datasets"):
        missing.append("eval_datasets")
    return missing


# ----------------------------------------------------------------------- list

def cmd_list(args: argparse.Namespace) -> int:
    db = load_db()
    accounts = db["accounts"]
    if args.json:
        print(json.dumps({"defaults": db.get("defaults", {}), "accounts": accounts}, indent=2))
        return 0
    if not accounts:
        print(f"no accounts in {ACCOUNTS_FILE} — use `add` or `seed`")
        return 0
    print(f"{len(accounts)} account(s) in {ACCOUNTS_FILE} "
          f"(default env: {db.get('defaults', {}).get('env', 'prod')})\n")
    w_slug = max(4, *(len(s) for s in accounts))
    w_name = max(4, *(len(r.get("name") or "") for r in accounts.values()))
    print(f"{'slug':<{w_slug}}  {'name':<{w_name}}  {'prod org':<8}  {'stg org':<7}  "
          f"{'wf':>3}  {'ds':>3}  email_domain")
    for slug in sorted(accounts):
        rec = accounts[slug]
        org = rec.get("propelauth_org_id") or {}
        print(f"{slug:<{w_slug}}  {(rec.get('name') or ''):<{w_name}}  "
              f"{'yes' if org.get('prod') else '-':<8}  "
              f"{'yes' if org.get('staging') else '-':<7}  "
              f"{len(rec.get('workflows') or []):>3}  "
              f"{len(rec.get('eval_datasets') or []):>3}  "
              f"{rec.get('email_domain') or '-'}")
    return 0


# ------------------------------------------------------------------------ get

def cmd_get(args: argparse.Namespace) -> int:
    slug, rec = resolve_account(args.ref)
    if args.json:
        print(json.dumps({"slug": slug, "record": rec}, indent=2))
        return 0
    print(f"slug: {slug}")
    print(json.dumps(rec, indent=2))
    missing = missing_fields(rec)
    if missing:
        print(f"\nstill null: {', '.join(missing)}")
    else:
        print("\nrecord complete — every field populated")
    return 0


# ------------------------------------------------------------------------ add

def cmd_add(args: argparse.Namespace) -> int:
    db = load_db()
    slug = args.slug or _slugify(args.name)
    existed = slug in db["accounts"]
    slug, rec = upsert_account(args.name, db=db, slug=slug)
    if existed:
        print(f"account {slug!r} already exists — left unchanged")
    else:
        print(f"added account {slug!r} ({args.name})")
        print(f"fill it in with: set {slug} propelauth_org_id.prod <uuid>  (etc.)")
    return 0


# ------------------------------------------------------------------------ set

def cmd_set(args: argparse.Namespace) -> int:
    slug, _ = resolve_account(args.ref)
    value = parse_value(args.value)
    rec = set_field(slug, args.field, value)
    shown = json.dumps(value) if not isinstance(value, str) else value
    print(f"set {slug}.{args.field} = {shown}")
    missing = missing_fields(rec)
    if missing:
        print(f"still null: {', '.join(missing)}")
    return 0


# --------------------------------------------------------------- add-workflow

def cmd_add_workflow(args: argparse.Namespace) -> int:
    db = load_db()
    slug, rec = resolve_account(args.ref, db=db)
    wf_slug = _slugify(args.name)
    workflows = rec.setdefault("workflows", [])
    entry = next((w for w in workflows if _slugify(w.get("name", "")) == wf_slug), None)
    if entry is None:
        entry = {"name": args.name, "id": {"prod": None, "staging": None}}
        workflows.append(entry)
        verb = "added"
    else:
        entry.setdefault("id", {"prod": None, "staging": None})
        verb = "updated"
    if args.prod_id:
        entry["id"]["prod"] = args.prod_id
    if args.staging_id:
        entry["id"]["staging"] = args.staging_id
    save_db(db)
    print(f"{verb} workflow {entry['name']!r} on account {slug!r}: "
          f"prod={entry['id'].get('prod') or 'null'} "
          f"staging={entry['id'].get('staging') or 'null'}")
    return 0


# ---------------------------------------------------------------- add-dataset

def cmd_add_dataset(args: argparse.Namespace) -> int:
    db = load_db()
    slug, rec = resolve_account(args.ref, db=db)
    datasets = rec.setdefault("eval_datasets", [])
    entry = next((d for d in datasets if d.get("id") == args.id), None)
    if entry is None:
        datasets.append({"name": args.name, "id": args.id, "env": args.env})
        verb = "added"
    else:
        entry.update({"name": args.name, "env": args.env})
        verb = "updated"
    save_db(db)
    print(f"{verb} dataset {args.name!r} (id={args.id}, env={args.env}) on account {slug!r}")
    return 0


# --------------------------------------------------------------------- remove

def cmd_remove(args: argparse.Namespace) -> int:
    db = load_db()
    if args.slug not in db["accounts"]:
        print(f"error: no account with exact slug {args.slug!r} "
              f"(remove requires the exact slug; see `list`)", file=sys.stderr)
        return 1
    removed = db["accounts"].pop(args.slug)
    save_db(db)
    print(f"removed account {args.slug!r} — record was (re-add with `add` + `set` if this "
          f"was a mistake):")
    print(json.dumps(removed, indent=2))
    return 0


# --------------------------------------------------------------------- lookup

def cmd_lookup(args: argparse.Namespace) -> int:
    db = load_db()
    needle = args.value.strip()
    if "@" in needle and "." in needle.rsplit("@", 1)[-1]:
        needle = needle.rsplit("@", 1)[-1]  # full email -> its domain
    low = needle.lower().lstrip("@")

    matches: list[dict] = []

    def hit(slug: str, field: str, value):
        matches.append({"slug": slug, "field": field, "value": value})

    for slug, rec in db["accounts"].items():
        for env, org in (rec.get("propelauth_org_id") or {}).items():
            if org and org.lower() == low:
                hit(slug, f"propelauth_org_id.{env}", org)
        for wf in rec.get("workflows") or []:
            for env, wf_id in (wf.get("id") or {}).items():
                if wf_id and wf_id.lower() == low:
                    hit(slug, f"workflow {wf.get('name')!r} ({env})", wf_id)
        for ds in rec.get("eval_datasets") or []:
            if (ds.get("id") or "").lower() == low:
                hit(slug, f"eval_dataset {ds.get('name')!r}", ds.get("id"))
        for field in ("email_domain", "slack_channel_id", "linear_project_id", "notion_doc_id"):
            if (rec.get(field) or "").lower() == low:
                hit(slug, field, rec[field])
    if not matches:  # fall back to name/slug fuzzy match
        try:
            slug, rec = resolve_account(needle, db=db)
            hit(slug, "name", rec.get("name"))
        except AccountError:
            pass

    if args.json:
        print(json.dumps(matches, indent=2))
        return 0 if matches else 1
    if not matches:
        print(f"no account matches {args.value!r} — if you know which account this "
              f"belongs to, save it with `set`/`add-workflow`/`add-dataset`")
        return 1
    for m in matches:
        print(f"{m['slug']}  ({m['field']} = {m['value']})")
    return 0


# ----------------------------------------------------------------------- seed

def _fill(rec: dict, field: str, value, changes: list[str]) -> None:
    """Set field only when currently null/empty — seed never overwrites."""
    if value in (None, "") or field not in rec:
        return
    if rec.get(field) in (None, "", [], {}):
        rec[field] = value
        changes.append(field)


def _has_workflow_json(folder: Path) -> bool:
    return any(folder.rglob("workflow*.json"))


def _seed_account(db: dict, folder: Path,
                  added: list[str], merged: list[str], skipped: list[str]) -> None:
    """Create or merge one account record for `folder`, never overwriting
    a non-null existing value."""
    slug = _slugify(folder.name)
    if slug in db["accounts"]:
        changes: list[str] = []
        _fill(db["accounts"][slug], "local_folder", str(folder), changes)
        (merged if changes else skipped).append(slug)
    else:
        rec = empty_account(folder.name)
        rec["local_folder"] = str(folder)
        db["accounts"][slug] = rec
        added.append(slug)


def cmd_seed(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1
    db = load_db()
    added: list[str] = []
    merged: list[str] = []
    skipped: list[str] = []

    if any(root.glob("workflow*.json")):
        # PATH is a single account or workflow folder. It counts as a workflow
        # folder — one workflow of the parent account — when it has no
        # workflow-bearing subdirectories but a sibling folder does.
        wf_subdirs = any(d for d in root.iterdir()
                         if d.is_dir() and not d.name.startswith(".")
                         and _has_workflow_json(d))
        try:
            siblings = [d for d in root.parent.iterdir()
                        if d.is_dir() and d != root and not d.name.startswith(".")]
        except OSError:
            siblings = []
        if not wf_subdirs and any(_has_workflow_json(d) for d in siblings):
            _seed_account(db, root.parent, added, merged, skipped)
        else:
            _seed_account(db, root, added, merged, skipped)
    else:
        candidates = [d for d in sorted(root.iterdir())
                      if d.is_dir() and not d.name.startswith(".")
                      and _has_workflow_json(d)]
        if not candidates:
            print(f"error: no workflow*.json found beneath {root} — point seed at "
                  f"an accounts folder or a single account folder", file=sys.stderr)
            return 1
        for child in candidates:
            _seed_account(db, child, added, merged, skipped)

    if added or merged:
        save_db(db)
    print("seed summary:")
    print(f"  added   {len(added)}: {', '.join(added) or '-'}")
    print(f"  merged  {len(merged)}: {', '.join(merged) or '-'}")
    print(f"  skipped {len(skipped)}: {', '.join(skipped) or '-'}")
    print(f"  total accounts now: {len(db['accounts'])} "
          f"({'saved' if added or merged else 'no changes'})")
    return 0


# ----------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="all accounts, one line each")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("get", help="one account: pretty JSON + completeness")
    p.add_argument("ref", help="slug, display name, or email domain")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("add", help="create an empty account record")
    p.add_argument("name")
    p.add_argument("--slug", help="override the auto-generated slug")

    p = sub.add_parser("set", help="set a dotted field, e.g. propelauth_org_id.prod")
    p.add_argument("ref")
    p.add_argument("field")
    p.add_argument("value", help="'null' clears; JSON parses; anything else is a string")

    p = sub.add_parser("add-workflow", help="add/update a workflow on an account")
    p.add_argument("ref")
    p.add_argument("--name", required=True)
    p.add_argument("--prod-id")
    p.add_argument("--staging-id")

    p = sub.add_parser("add-dataset", help="add/update an eval dataset (Test Batch)")
    p.add_argument("ref")
    p.add_argument("--name", required=True)
    p.add_argument("--id", required=True, help="dataset ObjectId")
    p.add_argument("--env", default="prod", choices=("prod", "staging", "local"))

    p = sub.add_parser("remove", help="delete a record (exact slug only)")
    p.add_argument("slug")

    p = sub.add_parser("lookup", help="find which account owns an ID/domain/channel")
    p.add_argument("value")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("seed", help="scan a folder and create/merge account records")
    p.add_argument("path", help="accounts folder, or a single account/workflow folder")

    args = parser.parse_args()
    commands = {
        "list": cmd_list, "get": cmd_get, "add": cmd_add, "set": cmd_set,
        "add-workflow": cmd_add_workflow, "add-dataset": cmd_add_dataset,
        "remove": cmd_remove, "lookup": cmd_lookup, "seed": cmd_seed,
    }
    try:
        return commands[args.command](args)
    except AccountError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
