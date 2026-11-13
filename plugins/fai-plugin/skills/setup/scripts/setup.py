#!/usr/bin/env python3
"""fai:setup helper — write/inspect ~/.fai/credentials.env and run the doctor.

Subcommands:
  status         show which envs are configured and which key types exist
  write          merge credentials into ~/.fai/credentials.env (chmod 600)
  import-legacy  one-time migration from legacy credential files (read-only)
  doctor         connectivity checks per env: token mint, API shape, org access

stdlib only. NEVER prints secret values — key names and presence only.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_client import (  # noqa: E402
    CREDENTIALS_FILE,
    DEFAULT_URLS,
    FaiClient,
    FaiError,
    load_credentials,
)
from fai_accounts import AccountError, load_db, resolve_org_id  # noqa: E402

ENVS = ("prod", "staging", "local")

# Key names per CONVENTIONS.md, {env} is the uppercased environment name.
KEY_PERSONAL = "FAI_{env}_PERSONAL_API_KEY"
KEY_USER_ID = "FAI_{env}_USER_ID"
KEY_SKILL = "FAI_SKILL_{env}_PERSONAL_API_KEY"
KEY_API_URL = "{env}_API_URL"
KEY_APP_URL = "{env}_APP_URL"

SECRET_SUFFIXES = ("_API_KEY", "_SECRET", "_TOKEN")

# Legacy sources (import-legacy only; nothing else may ever read these).
LEGACY_WB_ENV = Path.home() / ".claude" / "skills" / "wb" / ".env"
LEGACY_AGENT_ENV = Path.home() / ".agent-config" / "fai-skills.env"

LEGACY_MAP = {
    # ~/.claude/skills/wb/.env  ->  new names
    "PROD_API_URL": "PROD_API_URL",
    "PROD_APP_URL": "PROD_APP_URL",
    "PROD_PERSONAL_API_KEY": "FAI_PROD_PERSONAL_API_KEY",
    "PROD_USER_ID": "FAI_PROD_USER_ID",
    "STAGING_API_URL": "STAGING_API_URL",
    "STAGING_APP_URL": "STAGING_APP_URL",
    "STAGING_PERSONAL_API_KEY": "FAI_STAGING_PERSONAL_API_KEY",
    "STAGING_USER_ID": "FAI_STAGING_USER_ID",
    # ~/.agent-config/fai-skills.env  ->  names unchanged
    "FAI_SKILL_PROD_PERSONAL_API_KEY": "FAI_SKILL_PROD_PERSONAL_API_KEY",
    "FAI_SKILL_STAGING_PERSONAL_API_KEY": "FAI_SKILL_STAGING_PERSONAL_API_KEY",
}


def is_secret(key: str) -> bool:
    return key.endswith(SECRET_SUFFIXES)


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE parser matching fai_client's rules, file contents only."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def merge_credentials(updates: dict[str, str]) -> Path:
    """Merge updates into ~/.fai/credentials.env, preserving all other lines."""
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(CREDENTIALS_FILE.parent, 0o700)
    lines = CREDENTIALS_FILE.read_text().splitlines() if CREDENTIALS_FILE.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    tmp = CREDENTIALS_FILE.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(CREDENTIALS_FILE)
    os.chmod(CREDENTIALS_FILE, 0o600)
    return CREDENTIALS_FILE


def read_secret_arg(value: str | None, stdin_used: list[bool], fallback_env_var: str) -> str | None:
    """Resolve a secret flag: '-' reads stdin, 'env:NAME' reads that env var,
    no flag falls back to fallback_env_var. Keeps keys off argv."""
    if value is None:
        return os.environ.get(fallback_env_var) or None
    if value == "-":
        if stdin_used[0]:
            raise SystemExit("error: only one secret may be read from stdin per invocation")
        stdin_used[0] = True
        data = sys.stdin.read().strip()
        if not data:
            raise SystemExit("error: expected the key on stdin, got nothing")
        return data
    if value.startswith("env:"):
        name = value[4:]
        data = os.environ.get(name)
        if not data:
            raise SystemExit(f"error: environment variable {name} is empty or unset")
        return data
    return value


# --------------------------------------------------------------------- status

def env_state(creds: dict[str, str], env: str) -> dict[str, bool | str]:
    up = env.upper()
    return {
        "personal_key": bool(creds.get(KEY_PERSONAL.format(env=up))
                             or creds.get(f"{up}_PERSONAL_API_KEY")),
        "user_id": bool(creds.get(KEY_USER_ID.format(env=up))
                        or creds.get(f"{up}_USER_ID")),
        "skill_key": bool(creds.get(KEY_SKILL.format(env=up))),
        "api_url": creds.get(KEY_API_URL.format(env=up)) or "",
        "app_url": creds.get(KEY_APP_URL.format(env=up)) or "",
    }


def cmd_status(_args: argparse.Namespace) -> int:
    exists = CREDENTIALS_FILE.exists()
    print(f"credentials file: {CREDENTIALS_FILE} ({'exists' if exists else 'MISSING'})")
    if exists:
        mode = CREDENTIALS_FILE.stat().st_mode & 0o777
        note = "" if mode == 0o600 else "  <-- should be 600; run any `write` to fix"
        print(f"permissions: {oct(mode)[2:]}{note}")
    creds = parse_env_file(CREDENTIALS_FILE)
    print()
    print(f"{'env':<9}{'personal key':<14}{'user id':<9}{'skill key':<11}{'url overrides'}")
    for env in ENVS:
        st = env_state(creds, env)
        urls = ", ".join(u for u in (st["api_url"], st["app_url"]) if u) or "-"
        print(f"{env:<9}"
              f"{'yes' if st['personal_key'] else '-':<14}"
              f"{'yes' if st['user_id'] else '-':<9}"
              f"{'yes' if st['skill_key'] else '-':<11}"
              f"{urls}")
    print()
    legacy = [str(p) for p in (LEGACY_WB_ENV, LEGACY_AGENT_ENV) if p.exists()]
    if legacy:
        print("legacy credential files detected (import with `import-legacy`):")
        for p in legacy:
            print(f"  {p}")
    else:
        print("no legacy credential files detected")
    overridden = sorted(k for k in os.environ
                        if k.startswith(("FAI_", "PROD_", "STAGING_", "LOCAL_"))
                        and k != "FAI_CONFIG_DIR")
    if overridden:
        print(f"process env vars overriding the file: {', '.join(overridden)}")
    return 0


# ---------------------------------------------------------------------- write

def cmd_write(args: argparse.Namespace) -> int:
    env = args.env
    up = env.upper()
    stdin_used = [False]
    personal = read_secret_arg(args.personal_key, stdin_used, "FAI_SETUP_PERSONAL_KEY")
    skill = read_secret_arg(args.skill_key, stdin_used, "FAI_SETUP_SKILL_KEY")

    updates: dict[str, str] = {}
    if personal:
        updates[KEY_PERSONAL.format(env=up)] = personal
    if args.user_id:
        updates[KEY_USER_ID.format(env=up)] = args.user_id
    if skill:
        updates[KEY_SKILL.format(env=up)] = skill
    if args.api_url:
        updates[KEY_API_URL.format(env=up)] = args.api_url.rstrip("/")
    if args.app_url:
        updates[KEY_APP_URL.format(env=up)] = args.app_url.rstrip("/")
    if not updates:
        print("error: nothing to write — pass --personal-key/--user-id/--skill-key/"
              "--api-url/--app-url", file=sys.stderr)
        return 2
    if personal and not (args.user_id or env_state(parse_env_file(CREDENTIALS_FILE), env)["user_id"]):
        print(f"warning: personal key set for {env} but no user ID on file — "
              f"personal-key auth needs both.", file=sys.stderr)

    path = merge_credentials(updates)
    print(f"wrote {len(updates)} value(s) to {path} (chmod 600):")
    for key in updates:
        print(f"  {key} = {'<redacted>' if is_secret(key) else updates[key]}")

    if args.no_validate:
        return 0
    print()
    print(f"validating env {env!r} ...")
    rows = doctor_env(env, org_id=args.org_id, account=args.account)
    failed = print_doctor_rows(rows)
    if failed:
        print("\nvalidation FAILED — see hints above. Values were still written; "
              "fix and re-run `doctor`.")
    return 1 if failed else 0


# -------------------------------------------------------------- import-legacy

def cmd_import_legacy(args: argparse.Namespace) -> int:
    found: dict[str, str] = {}
    sources: list[str] = []
    for path in (LEGACY_WB_ENV, LEGACY_AGENT_ENV):
        values = parse_env_file(path)
        mapped = {LEGACY_MAP[k]: v for k, v in values.items() if k in LEGACY_MAP and v}
        if values:
            sources.append(str(path))
            found.update(mapped)
    if not found:
        print("no legacy credentials found "
              f"(checked {LEGACY_WB_ENV} and {LEGACY_AGENT_ENV})")
        return 1

    existing = parse_env_file(CREDENTIALS_FILE)
    to_write = {k: v for k, v in found.items()
                if args.overwrite or not existing.get(k)}
    kept = sorted(set(found) - set(to_write))

    print("legacy sources read (read-only, never modified):")
    for s in sources:
        print(f"  {s}")
    print("values found (names only):")
    for key in sorted(found):
        print(f"  {key}")
    if kept:
        print("already set in credentials.env, kept existing (use --overwrite to replace):")
        for key in kept:
            print(f"  {key}")
    if args.dry_run:
        print("\ndry run — nothing written")
        return 0
    if to_write:
        merge_credentials(to_write)
        print(f"\nwrote {len(to_write)} value(s) to {CREDENTIALS_FILE} (chmod 600)")
    else:
        print("\nnothing new to write")
    print("run `doctor` next to verify connectivity.")
    return 0


# --------------------------------------------------------------------- doctor

def check_api_shape(api_url: str) -> tuple[bool, str]:
    """Reachability check asserting RESPONSE SHAPE, not just status.

    GET on the token-mint route: a real FastAPI backend answers JSON
    (405 Method Not Allowed); an SPA catch-all (app-staging.furtherai.com
    serving /api/v1 paths) answers 200 text/html. Status alone would lie.
    """
    url = f"{api_url}/api/v1/oauth2/access_token"
    try:
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                status, ctype, raw = resp.status, resp.headers.get("Content-Type", ""), resp.read(2048)
        except urllib.error.HTTPError as e:
            status, ctype, raw = e.code, e.headers.get("Content-Type", ""), e.read(2048)
    except (urllib.error.URLError, OSError) as e:
        return False, f"unreachable: {str(e)[:120]}"
    text = raw.decode(errors="replace").lstrip().lower()
    if "text/html" in ctype or text.startswith(("<!doctype", "<html")):
        return False, (f"HTTP {status} but HTML body — this host serves the SPA "
                       f"catch-all for /api/v1 paths, not the API")
    return True, f"JSON API response (HTTP {status})"


def resolve_doctor_org(env: str, org_id: str | None, account: str | None) -> tuple[str | None, str]:
    """Org for this env: explicit --org-id > --account > first org in accounts.json."""
    if org_id:
        return org_id, "--org-id"
    if account:
        try:
            return resolve_org_id(account, env), f"--account {account}"
        except AccountError as e:
            return None, f"--account failed: {e}"
    try:
        db = load_db()
    except AccountError:
        return None, "no org given"
    for slug, rec in db.get("accounts", {}).items():
        org = (rec.get("propelauth_org_id") or {}).get(env)
        if org:
            return org, f"accounts.json fallback ({slug})"
    return None, "no org given and none in accounts.json"


def doctor_env(env: str, org_id: str | None = None,
               account: str | None = None) -> list[tuple[str, str, str, str, str]]:
    """Returns rows of (env, check, PASS|FAIL|SKIP, detail, hint)."""
    rows: list[tuple[str, str, str, str, str]] = []
    org, org_source = resolve_doctor_org(env, org_id, account)
    client = FaiClient(env=env, org_id=org, skill_name="setup")

    # (a) API reachability / response shape — unauthenticated, runs first.
    ok, detail = check_api_shape(client.api_url)
    hint = ""
    if not ok and "HTML" in detail:
        hint = (f"Point {env.upper()}_API_URL at the backend host directly "
                f"(default: {DEFAULT_URLS[env]['api']}). app-staging.furtherai.com "
                f"serves the SPA, not the API.")
    elif not ok:
        hint = "Check network/VPN and the URL override in ~/.fai/credentials.env."
    rows.append((env, "api reachability", "PASS" if ok else "FAIL",
                 f"{client.api_url} — {detail}", hint))

    # (b) token mint — reports which auth flow was used.
    flow = "restricted skill key" if client.restricted else "personal key"
    if not client.restricted and not org:
        rows.append((env, "token mint", "SKIP",
                     f"personal-key flow needs an org to mint ({org_source})",
                     "Pass --org-id or --account (personal tokens are org-scoped at mint)."))
        rows.append((env, "org access", "SKIP", "no org resolved", ""))
        return rows
    try:
        client.verify_auth()
        via = "" if client.restricted else f", org via {org_source}"
        rows.append((env, "token mint", "PASS", f"{flow} flow{via}", ""))
    except (FaiError, OSError) as e:
        rows.append((env, "token mint", "FAIL", f"{flow} flow — {str(e)[:160]}",
                     "Key invalid or wrong environment (staging and prod are separate "
                     "PropelAuth instances — keys never cross). Mint a fresh key in "
                     "PropelAuth with the env selector on this environment "
                     "(Preview -> Personal API Keys -> New API Key), then re-run "
                     "fai:setup write."))
        rows.append((env, "org access", "SKIP", "mint failed", ""))
        return rows

    # (c) org access — only meaningful once we hold a token and an org.
    if not org:
        rows.append((env, "org access", "SKIP",
                     "no org given (pass --org-id or --account)", ""))
        return rows
    try:
        resp = client.get("/api/v1/workflows", params={"slim": "true", "limit": 1})
        if isinstance(resp, str):
            rows.append((env, "org access", "FAIL",
                         "HTML/text response where JSON expected (SPA catch-all)",
                         f"Point {env.upper()}_API_URL at the backend host directly."))
        else:
            n = len(resp) if isinstance(resp, list) else "?"
            rows.append((env, "org access", "PASS",
                         f"GET /api/v1/workflows ok for org {org} ({org_source}); "
                         f"{n} workflow(s) in first page", ""))
    except FaiError as e:
        rows.append((env, "org access", "FAIL", str(e)[:200],
                     "404 usually means wrong org for this env or you are not a member; "
                     "403 means the route is off the restricted allowlist or you lack "
                     "fai_admin in the target org."))
    return rows


def print_doctor_rows(rows: list[tuple[str, str, str, str, str]]) -> bool:
    w_env = max(3, *(len(r[0]) for r in rows))
    w_check = max(5, *(len(r[1]) for r in rows))
    print(f"{'env':<{w_env}}  {'check':<{w_check}}  {'result':<6}  detail")
    for env, check, result, detail, _hint in rows:
        print(f"{env:<{w_env}}  {check:<{w_check}}  {result:<6}  {detail}")
    hints = [(r[1], r[4]) for r in rows if r[2] == "FAIL" and r[4]]
    hints += [(r[1], r[4]) for r in rows if r[2] == "SKIP" and r[4]]
    if hints:
        print("\nhints:")
        for check, hint in hints:
            print(f"  [{check}] {hint}")
    return any(r[2] == "FAIL" for r in rows)


def configured_envs() -> list[str]:
    creds = load_credentials()  # file + process-env overrides, same as FaiClient
    envs = []
    for env in ENVS:
        st = env_state(creds, env)
        if st["skill_key"] or (st["personal_key"] and st["user_id"]) or st["api_url"]:
            envs.append(env)
    return envs


def cmd_doctor(args: argparse.Namespace) -> int:
    envs = args.env or configured_envs()
    if not envs:
        print(f"no environments configured in {CREDENTIALS_FILE} — run `write` "
              f"or `import-legacy` first", file=sys.stderr)
        return 2
    all_rows: list[tuple[str, str, str, str, str]] = []
    for env in envs:
        all_rows.extend(doctor_env(env, org_id=args.org_id, account=args.account))
    failed = print_doctor_rows(all_rows)
    print()
    print("doctor result: " + ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


# ----------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show configured envs and key types (no secrets)")

    p_write = sub.add_parser("write", help="merge credentials into ~/.fai/credentials.env")
    p_write.add_argument("--env", required=True, choices=ENVS)
    p_write.add_argument("--personal-key",
                         help="personal API key; '-' reads stdin, 'env:NAME' reads an env var "
                              "(falls back to $FAI_SETUP_PERSONAL_KEY)")
    p_write.add_argument("--user-id", help="PropelAuth user ID for this environment")
    p_write.add_argument("--skill-key",
                         help="restricted skill service key (FDE/admin only); '-'/'env:NAME' "
                              "as for --personal-key (falls back to $FAI_SETUP_SKILL_KEY)")
    p_write.add_argument("--api-url", help="override API base URL for this env")
    p_write.add_argument("--app-url", help="override app base URL for this env")
    p_write.add_argument("--org-id", help="org for post-write validation")
    p_write.add_argument("--account", help="account ref for post-write validation")
    p_write.add_argument("--no-validate", action="store_true",
                         help="skip the post-write doctor pass")

    p_imp = sub.add_parser("import-legacy",
                           help="import values from legacy credential files (read-only sources)")
    p_imp.add_argument("--dry-run", action="store_true",
                       help="report what would be imported, write nothing")
    p_imp.add_argument("--overwrite", action="store_true",
                       help="replace values already present in credentials.env")

    p_doc = sub.add_parser("doctor", help="connectivity checks per configured env")
    p_doc.add_argument("--env", action="append", choices=ENVS,
                       help="check only this env (repeatable; default: all configured)")
    p_doc.add_argument("--org-id", help="org UUID for the org-access check")
    p_doc.add_argument("--account", help="account ref (accounts.json) for the org-access check")

    args = parser.parse_args()
    try:
        return {"status": cmd_status, "write": cmd_write,
                "import-legacy": cmd_import_legacy, "doctor": cmd_doctor}[args.command](args)
    except (FaiError, AccountError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
