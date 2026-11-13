---
name: setup
description: One-time FurtherAI credential setup and connectivity doctor. Configures ~/.fai/credentials.env, migrates keys from legacy locations, and verifies token minting and API access per environment. Use when the user says "set up fai", "configure my FurtherAI credentials", "fai doctor", "check my connection", "migrate my old keys", or when any fai skill fails with a missing-credentials error.
---

# fai:setup — credentials and connectivity doctor

Writes `~/.fai/credentials.env` (chmod 600) and verifies it works. The audience
includes non-engineers: ask one question at a time, explain where to find each
value, and never assume the user knows what an org ID or API key is.

**Secrets rule (absolute): never print, echo, quote, or partially reveal an API
key value — not in output, not in a command shown to the user, not "just the
first few characters". Key names and presence/absence only. Use `status` to
inspect the file; never `cat` it.**

## Commands

Every command is `python3 "${CLAUDE_PLUGIN_ROOT}/skills/setup/scripts/setup.py" <subcommand>`:

| Subcommand | Purpose |
|---|---|
| `status` | Show which envs are configured, which key types exist, file permissions, detected legacy files. No secrets printed. |
| `write --env prod --personal-key - --user-id <ID> [--skill-key -] [--api-url URL] [--app-url URL]` | Merge values into `~/.fai/credentials.env` (other lines preserved, chmod 600), then auto-validate. `--no-validate` skips validation; `--org-id`/`--account` make validation complete. |
| `import-legacy [--dry-run] [--overwrite]` | One-time migration from legacy credential files (read-only sources). `--dry-run` reports what it found without writing. Existing values are kept unless `--overwrite`. |
| `doctor [--env prod] [--org-id UUID | --account REF]` | Per-env checks: API reachability (response shape, not just status), token mint (reports which auth flow), org access. Prints a PASS/FAIL table with remediation hints. Exit 0 = all pass. |

## Flow

1. **Always start with `status`.** It shows what is already configured and
   whether legacy credential files exist. Summarize the state for the user.
2. **If legacy files were detected**, offer to import them:
   run `import-legacy --dry-run`, show the user which value *names* were found,
   and on confirmation run `import-legacy` (then skip to step 4). The legacy
   sources are read-only; they are never modified or deleted. This is the
   single sanctioned read of legacy locations — no other skill may touch them.
3. **Otherwise interview the user**, one question at a time:
   - Which environment(s)? Most users only need **prod**. Staging is for
     engineers/FDEs testing unreleased changes.
   - **Personal API key** — minted in PropelAuth. Walk the user through it:
     1. Go to PropelAuth.
     2. Make sure the environment selector is on **Prod** (for a prod key).
     3. Click **Preview** in the top right.
     4. Choose **Personal API Keys**.
     5. Click **New API Key**, give it a name, and **copy the key before
        closing the modal** — it is not shown again.
     6. Paste the key back to you for writing into the credentials file.

     Each environment issues its own key; mint them separately, with the
     PropelAuth environment selector set to the matching environment.
   - **User ID** — also from PropelAuth, paired with the key for personal-key
     auth: with the environment selector on the matching environment, find the
     user, click **Quick Actions**, and choose **Copy User ID**. It never
     crosses environments.
   - **Org ID (optional, for the `doctor` org-access check)** — same path in
     PropelAuth: search the organization, **Quick Actions** → **Copy Org ID**.
     Store it per account with `/fai:account` rather than in credentials.env.
   - **Restricted skill key (optional, FDE/admin only)** — only for users
     provisioned as restricted skill service users by the platform team. If the
     user doesn't know what this is, they don't have one; skip it.
4. **Write with the key OFF the command line.** Have the user paste the key,
   then pipe it via stdin (`--personal-key -` / `--skill-key -` read stdin;
   `env:VARNAME` reads an environment variable):

   ```bash
   printf '%s' 'PASTED_KEY' | python3 "${CLAUDE_PLUGIN_ROOT}/skills/setup/scripts/setup.py" \
     write --env prod --personal-key - --user-id USER_ID
   ```

   Only one secret can come from stdin per invocation — if writing both a
   personal key and a skill key, run `write` twice (it merges).
5. **Finish with `doctor`.** Pass `--account <name>` or `--org-id <uuid>` if
   known so the org-access check runs (personal tokens are org-scoped at mint,
   so without an org the mint check is skipped; doctor also falls back to the
   first org found in `~/.fai/accounts.json`). Report the PASS/FAIL table and
   walk the user through any hints.

## The two auth flows

`FaiClient` picks the flow automatically from what is configured:

| Flow | Credentials | How it scopes | Who uses it |
|---|---|---|---|
| **Personal API key** (default) | `FAI_{ENV}_PERSONAL_API_KEY` + `FAI_{ENV}_USER_ID` | Token is org-scoped at mint (`active_org_id` required); acts as the key's owner, only in orgs they belong to | Everyone |
| **Restricted skill key** | `FAI_SKILL_{ENV}_PERSONAL_API_KEY` | Token mints without an org; each request scopes via `X-FAI-Target-Org-Id`. Cross-org capable but limited to ~70 allowlisted routes (no workflow delete, dataset delete, or org listing) | Provisioned FDE/admin service users only |

If a skill key is configured for an env it takes precedence over the personal
key for that env.

## Guardrails

- **Staging and prod are separate PropelAuth instances.** Keys, user IDs, and
  org IDs never cross environments. A key that mints in prod will be rejected
  in staging — that is expected, not a bug.
- **Hostnames lie.** The prod backend's raw Azure hostname contains the word
  "staging". Environment comes from config, never from a hostname.
- **The staging app host is not the staging API.** `app-staging.furtherai.com`
  serves the SPA catch-all for `/api/v1` paths (HTML with HTTP 200). The
  doctor's reachability check asserts on response *shape* to catch exactly
  this; the fix is pointing `STAGING_API_URL` at the backend host (default is
  already correct — only overrides go wrong).
- URL overrides are optional; the lib has correct defaults built in. Only set
  `--api-url`/`--app-url` when the user knowingly targets a non-standard host
  (e.g. `--env local`).
- Credentials are written only to `~/.fai/credentials.env`. Never write them
  to shell profiles, project `.env` files, or anywhere else.

## Failure modes

| Symptom | Meaning / fix |
|---|---|
| `token mint rejected (HTTP 400/401/403)` | Key invalid, pasted with whitespace, or from the other environment. Mint a fresh key in PropelAuth with the environment selector on the *matching* environment (Preview → Personal API Keys → New API Key) and re-run `write`. For the restricted flow: user is not provisioned as a restricted skill service user. |
| reachability FAIL with "HTML body — SPA catch-all" | An API URL override points at an app host. Remove or fix the `*_API_URL` override (see guardrails). |
| org access FAIL with 404 | Wrong org UUID for this environment, or the user is not a member of that org. Remember IDs never cross staging/prod. |
| org access FAIL with 403 | Restricted flow: route off the allowlist. Personal flow: user lacks `fai_admin` in the target org (authoring operations need it). |
| `token mint SKIP` (personal flow, no org) | Not an error — personal tokens need an org at mint. Re-run doctor with `--org-id` or `--account`, or add an account via `/fai:account`. |
| doctor unreachable / timeouts | Network/VPN. The mint endpoint can 500 under load; the client already retries 6 times with backoff, so a persistent failure is real. |
