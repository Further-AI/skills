---
name: account
description: Manage the local FurtherAI account database (~/.fai/accounts.json) that maps account names to org IDs, workflow IDs, eval datasets, Slack channels, and other identifiers. Use when the user says "list accounts", "add an account", "which account is this org ID", "save this workflow ID", "seed my accounts", "look up this UUID", or when any fai skill reports a missing org or workflow ID for an account.
---

# fai:account — the local account database

`~/.fai/accounts.json` is what lets every fai skill accept `--account acme`
instead of pasted UUIDs. This skill maintains it. All reads and writes go
through `scripts/account.py` (which uses `lib/fai_accounts.py`) — never edit
the JSON by hand from here.

Every command is `python3 "${CLAUDE_PLUGIN_ROOT}/skills/account/scripts/account.py" <subcommand>`.
Read commands (`list`, `get`, `lookup`) accept `--json`.

## Decision table — user intent to command

| User intent | Command |
|---|---|
| "what accounts do we have" / "list accounts" | `list` |
| "show me acme" / "what do we know about X" | `get acme` |
| "add account Acme Insurance" | `add "Acme Insurance"` |
| "the prod org ID for X is <uuid>" | `set x propelauth_org_id.prod <uuid>` |
| "set the Slack channel / Linear project / email domain / FDE / notes" | `set x slack_channel_id C0123ABC` (any field, dotted paths for nested) |
| "clear a field" | `set x notes null` |
| "X has a workflow called submission_intake, prod ID <uuid>" | `add-workflow x --name submission_intake --prod-id <uuid>` |
| "save this eval dataset / Test Batch for X" | `add-dataset x --name gold-v1 --id <objectid> --env prod` |
| "delete account X" | `remove <exact-slug>` (confirm with the user first; prints the removed record so it can be restored) |
| "whose org is <uuid>" / "which account is this workflow/dataset/domain/channel" | `lookup <value>` |
| "populate/import my accounts" / first run with an empty db | `seed <path>` (ask for the folder first — see Seeding) |

Account references (`ref`) are forgiving: slug, display name, or email domain
all resolve, and unique substrings work. `remove` alone demands the exact slug.

## Record schema

Each account holds: `name`, `propelauth_org_id` (`{prod, staging}` — separate
PropelAuth instances, so per-env), `linear_project_id`, `slack_channel_id`,
`notion_doc_id`, `email_domain`, `local_folder`, `workflows` (each
`{name, id: {prod, staging}}`), `eval_datasets` (each `{name, id, env}`),
`engagement_manager`, `fde`, `notes`. Every field is nullable — a half-filled
record is still useful. `get` prints the record plus a "still null: ..." line
showing exactly what is missing.

Values for `set`: `null`/`none` clears a field; valid JSON parses (numbers,
booleans, lists); anything else is stored as a string. Dotted paths reach into
nested objects: `propelauth_org_id.staging`, etc.

## Finding an org ID

When the user doesn't have the org UUID to hand, walk them through PropelAuth:

1. Go to PropelAuth and set the environment selector to the environment you
   need — **Prod** and **Staging** are separate instances with different IDs.
2. Search for the organization.
3. Click **Quick Actions** → **Copy Org ID**.
4. Paste it back, then save it: `set <slug> propelauth_org_id.<env> <uuid>`.

The same path yields a user ID (find the user → **Quick Actions** → **Copy
User ID**); that one belongs in credentials, not here — send them to
`/fai:setup`.

## Seeding

`seed <path>` scans a folder and creates or merges account records. Ask the
user for the path to their accounts folder (or a single account/workflow
folder) when it isn't obvious from context, then run `seed <path>`. Three
layouts are recognized:

- **Accounts folder** — subdirectories each holding `workflow*.json` files
  somewhere beneath them: every such subdirectory becomes an account, slug
  from the directory name, `local_folder` set to its path.
- **Single account folder** — `workflow*.json` directly inside `<path>`: one
  account named after the folder.
- **Workflow folder** — holds `workflow*.json` directly, has no
  workflow-bearing subfolders, and sits beside other workflow folders: treated
  as one workflow of its parent account, so the account takes the parent
  folder's name and path. (For an account with a single workflow, pass the
  account folder, not the workflow folder.)

Seeding **never overwrites a non-null existing value**. It prints a summary of
what was added, merged, and skipped. Safe to re-run any time.

## Enrichment — keep the database alive

This database only stays useful if IDs get saved the moment they surface.
Whenever you are working with an account and an identifier appears,
**proactively offer to save it** (one short question, then run the command):

- An org UUID appears in a pasted app URL or API response, and `lookup <uuid>`
  finds nothing → offer `set <slug> propelauth_org_id.<env> <uuid>`.
- A workflow is created or its ID surfaces (e.g. from a create response or a
  `/workflows/<uuid>` URL) → offer `add-workflow <slug> --name <name> --<env>-id <uuid>`.
- An eval dataset (Test Batch) is created or referenced by ObjectId → offer
  `add-dataset <slug> --name <name> --id <objectid> --env <env>`.
- A Slack channel, Linear project, email domain, EM, or FDE for the account
  comes up in conversation → offer the matching `set`.

Conversely, when the user mentions a bare UUID/ID without context, run
`lookup <value>` first — it may already be known. When resolution fails in
another fai skill ("account has no org ID for env staging"), the fix is a
`set` here, then retry — see **Finding an org ID** if the user doesn't have the
UUID.

Remember the env matters: a prod org/workflow ID does not exist in staging and
vice versa. Always save IDs under the environment they came from — the app URL
tells you (`app.furtherai.com` = prod, `app-staging.furtherai.com` = staging).
