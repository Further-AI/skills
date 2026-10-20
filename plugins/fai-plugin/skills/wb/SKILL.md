---
name: wb
description: "Operates Workflow Builder workflows via the platform API: create, clone, copy across environments, download, upload a draft, publish, execute, retry, list executions, inspect execution status, fetch per-step results and table outputs, download documents, list versions, validate a draft, diff local vs remote, and fleet-publish one edit across many orgs. Use when the user says things like 'download the workflow', 'upload this draft', 'publish the workflow', 'run the workflow with these documents', 'run a submission', 'upload a submission', 'process documents through a workflow', 'check the status of a run', 'download submission results', 'get the outputs of a run', 'rerun/retry a submission', 'retry that execution', 'why did this run fail', 'copy the prod workflow to staging', or 'roll this change out to every org'."
---

# Workflow Builder (wb)

CLI for the Workflow Builder API. All commands run through one script:

```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/wb/scripts/wb.py" <command> [flags]
```

(Referred to as `wb.py` below. `fleet-publish` is a separate script in the same
`scripts/` directory.)

| Command | What it does |
|---|---|
| `create` | New workflow shell + active draft from a local JSON |
| `clone` | Clone an existing workflow into a new never-published draft |
| `copy-cross-env` | Copy a workflow between environments/orgs (published export → draft import) |
| `download` | Save workflow JSON — live, active draft, or a specific version |
| `upload` | Save local JSON to the active draft (never publishes) |
| `publish` | Publish the active draft, or promote an old version to live |
| `execute` | Upload documents and run a workflow (published, or a draft test run) |
| `retry` | New execution reusing an old execution's documents (composite — no retry endpoint exists) |
| `executions` | List executions with filters and cursor pagination |
| `status` | Execution status + per-step results table |
| `results` | Per-step outputs — full table rows, output JSON, errors; per-step JSON/Excel export |
| `cancel` | Cancel an execution (reliable only for paused / manual-input runs) |
| `table` | Fetch a step's table output with pagination + search; `--export-excel` |
| `documents` | `list` / `download` / `download-all` an execution's documents |
| `versions` | Version history + whether an active draft exists |
| `validate` | Server-side validation of the active draft |
| `diff` | Compare a local workflow JSON against remote (live / draft / version) |
| `fleet-publish` | Apply one edit to the same workflow across many orgs (`scripts/wb_fleet_publish.py`) |

## Common submission flows

The workflow overview page in the UI calls executions "Submissions" — prefer
"submission" with the user; the API and this script say execution. A
submission = a set of documents + one workflow execution.

| User asks | Command |
|---|---|
| Run/upload a submission, process documents through a workflow | `execute --docs ... [--wait]` |
| Check on a submission | `status --exec-id <id>` |
| Get the results / outputs of a run | `results --exec-id <id>` |
| Download the generated output files | `documents download-all --exec-id <id> --output-only` |
| Rerun/retry a submission | `retry --exec-id <id>` |
| List recent submissions | `executions [--workflow-id <uuid-or-name>]` |

## Global flags and account resolution

Every command accepts:

- `--env prod|staging|local` — default: `defaults.env` in `~/.fai/accounts.json`, else `prod`.
- `--account <slug|name|domain>` — resolves the org ID (and workflow names) from `~/.fai/accounts.json`.
- `--org-id <uuid>` — explicit org; **always overrides** `--account`.

`--workflow-id` accepts a raw UUID, or a workflow **name** from the account
record when `--account` is given (`--account acme --workflow-id
submission_intake`). Execution, document, and table IDs are always raw.
If resolution fails, the error names the known accounts/workflows; populate the
database with the `fai:account` skill.

When the user names a workflow, resolve it in this order:

1. A workflow ID or name already established in the conversation.
2. The account's `workflows` list in `~/.fai/accounts.json` — if the account
   has exactly one workflow, use it.
3. Multiple candidates or none: list what the account has and ask the user.

Never guess a workflow name.

Credentials come from `~/.fai/credentials.env`, written by the `fai:setup`
skill. Never read any other credential file. Never print keys, tokens, or SAS
URLs — say "auth valid", not the value.

## Auth — two flows

The client picks the flow automatically from what's in `~/.fai/credentials.env`:

1. **Personal API key** (default for most users): token is org-scoped at mint,
   acts only as its owner, and only in orgs the owner is a member of. An org ID
   (via `--org-id` or `--account`) is required.
2. **Restricted skill service key** (`FAI_SKILL_{ENV}_PERSONAL_API_KEY`,
   FDE/admin power feature): cross-org capable — each request is scoped with an
   `X-FAI-Target-Org-Id` header — but only ~70 allowlisted routes work. Notable
   absences: workflow delete, dataset delete, org listing. `fleet-publish`
   requires this key.

Authoring operations (`create`, `clone`, `upload`, `publish`, draft reads,
draft execution, `validate`) need `fai_admin` permission in the target org.
Read/run operations (`execute` published, `status`, `table`, `documents`,
`executions`) need only org membership.

## Guardrails

Each of these has a documented incident behind it:

1. **Hostnames lie.** The prod backend's Azure hostname contains "staging".
   Environment comes from `--env` and config, never from a hostname.
2. **Staging and prod are separate PropelAuth instances.** Keys, user IDs, org
   IDs, and workflow IDs never cross environments. A 404 across environments is
   expected, not a bug.
3. **`owner_uid`/`owner_oid` must be empty strings** on document registration —
   the API derives them from the token and 400s otherwise. The client library
   handles this; don't hand-roll uploads.
4. **Publish `override: true` bypasses ONLY the draft-test gate.** Publish
   validation still runs and cannot be bypassed. The gate surfaces as HTTP 400
   with `detail.error` of `TEST_RUN_REQUIRED` / `TEST_RUN_IN_PROGRESS` /
   `TEST_RUN_FAILED`.
5. **`step_type` is a lossy display bucket.** Use `step_type_raw` (may be null
   on old executions — fall back to the version's step config, or `step_type`
   as a last resort). Key steps off `input.step_name`, never `title`.
6. **Executions have FOUR terminal statuses**: `completed`, `failed`,
   `cancelled`, `terminated`. Polling for only two hangs forever. (`--wait`
   handles all four.) Success is `completed` — never say "succeeded".
7. **`validate` returning `is_valid: false` is a successful call** (HTTP 200).
   HTTP 404 there means "no draft exists", not "invalid".
8. **No retry endpoint exists.** Retry = list the old execution's documents →
   start a new execution with the same `user_document_ids`. New execution ID,
   runs from step 1 — it is not a resume.
9. **Public builder endpoints return 400 (not 401) for bad tokens.**

Also: prod writes (upload/publish/execute against a customer org) deserve a
confirmation with the org, workflow, and side effects spelled out before you
run them. Don't dump hundreds of rows of list output into chat — filter first
and show counts plus the relevant candidates.

## Error triage

| HTTP | Meaning | Fix |
|---|---|---|
| 400 | Bad/missing token on public builder endpoints (they return 400, not 401); malformed `X-FAI-Target-Org-Id`; or the publish draft-test gate (`detail.error` = `TEST_RUN_*`) | Check auth, org header, or run the draft test |
| 401 | Token expired/invalid — the client re-mints once automatically | If it persists, run `fai:setup` |
| 403 | Route not on the restricted-skill allowlist, or the user lacks `fai_admin` in the target org | Authoring needs `fai_admin`; some routes simply aren't available to restricted keys |
| 404 | Wrong ID, wrong org, or caller not a member of the org | Verify the ID and org; remember env separation — a prod ID does not exist on staging |
| 422 | Malformed request body | Check field names against the shapes documented below |
| 429 | Rate limited | Honor `Retry-After` |

## Commands

### create

```
wb.py create --account <acct> --json workflow.json [--name "Override Name"]
```

`POST /api/v1/workflow-builder/workflows/` with `{name, description, welcome_message,
steps, options}` (`description` is taken from the JSON's `comments`, falling back
to `description` — the backend stores it on `comments`). Creates a
never-published shell plus active draft; does **not** publish.

Output: workflow ID, draft version, step count, workflow URL, and the exact
draft-test command. For legacy workflows with `dependencies`-style steps that
the create route rejects, use `copy-cross-env` (shell-then-import) instead.

### clone

```
wb.py clone --account <acct> --source-workflow-id <uuid> [--name "Copy for testing"]
```

`POST .../workflows/{id}/clone`. The clone is a never-published shell with the
source content in active draft v1. Normal loop: `clone` → `execute
--draft-version` with test docs → `publish`. Output: new workflow ID, draft
version, URL, draft-test command.

### copy-cross-env

```
wb.py copy-cross-env \
  --source-env prod --source-account acme --source-workflow-id <uuid> \
  [--source-version 12] \
  --target-env staging --target-org-id <uuid> \
  --name "RMA intake - AJ - 2026-08-17 - experiment"
```

Same pattern as the UI's JSON download/upload: read the source (live, or a
specific published version with `--source-version`), create a target shell with
empty `steps`, then `PUT` the exported JSON into the target's active draft with
`enable_validation: false` (validation still runs at publish). Source and
target each resolve their own env/org — source and target can use different
accounts, orgs, and environments. Cross-org targets you're not a member of
need the restricted key.

Rules: `--name` is required and should be distinct and human-readable (source
name + operator + date + experiment label), never identical to the source name.
Skips nothing silently — a never-published source (empty `steps`) is an error.
The copy is a draft; it carries the source `options` verbatim, including
prod-targeted values (trigger email addresses, webhooks, Zapier URLs) — patch
those before any end-to-end run on the target, or the copy fans out to prod
endpoints. Output includes the mapping `source workflow <id> vN -> target
workflow <new-id>` plus draft version, URL, and the draft-test command.

### download

```
wb.py download --account <acct> --workflow-id <uuid|name> [--draft | --version N] \
  [--slim true|exec] [--out file.json]
```

- Live (default): `GET /api/v1/workflows/{id}`
- Active draft: `?draft=true` — **FAI-admin only; 404 = no draft (or no admin)**
- Specific version: `GET /api/v1/workflow-builder/workflows/{id}/versions/{v}`
- `--slim true` drops `steps` entirely (metadata-only fetch); `--slim exec`
  keeps steps but drops editor-only per-step fields. Slim writes the raw payload.

Writes normalized JSON (`name`, `description`, `comments`, `welcome_message`,
`steps`, `options`) to `--out` (default `workflow_<id8>[_draft|_vN].json`).
Output: workflow name, published version, step count, file path.

### upload

```
wb.py upload --account <acct> --workflow-id <uuid> --json fixed.json --summary "Fix mappings"
```

`PUT /api/v1/workflow-builder/workflows/{id}` with:

```json
{"name": "...", "description": "<comments || description>", "welcome_message": "...",
 "steps": [...], "options": {...}, "change_summary": "...", "enable_validation": false}
```

Saves the active draft (creates one if none exists). **Draft only — never
publishes.** `enable_validation: false` matches the UI import path so legacy
workflows load; publish still validates later. Output: draft version, step
count, workflow URL, and a reminder of the publish command.

### publish

```
wb.py publish --account <acct> --workflow-id <uuid> --name "v2 fix" [--notes "..."] [--allow-override]
wb.py publish --account <acct> --workflow-id <uuid> --promote-version 12 [--name "Rollback to v12"]
```

`POST .../workflows/{id}/publish` with `{published_name, publish_notes,
override: false}`. The API requires a successful draft test run after the
latest edit; otherwise it 400s with `detail.error` `TEST_RUN_REQUIRED` /
`TEST_RUN_IN_PROGRESS` / `TEST_RUN_FAILED`. On a gate error the command prints
what the gate means and the exact draft-test command to run.

`--allow-override` retries with `override: true` and prints a warning: override
bypasses **only** the test gate — publish validation still runs and cannot be
bypassed. Only pass it when the user explicitly asked for a no-test publish,
and never against a customer prod org without an approval that names the
override.

`--promote-version N` publishes old version N's content as the new live version
(`POST .../versions/{N}/publish`); it does not touch the active draft. This is
the rollback path.

Output: published version, workflow ID, workflow URL.

### execute

```
wb.py execute --account <acct> --workflow-id <uuid> --docs a.pdf b.xlsx \
  [--draft-version 3] [--instruction "..."] [--wait] [--interval 10] [--timeout 3600]
```

Uploads each document through the client library (registration +
blob PUT — the empty-`owner_uid` trap is handled), then triggers:

- Published run: `POST /api/v1/workflow-execution` with
  `{"workflow_id": ..., "instruction": "", "documents": [<user_document_ids>]}`
- Draft test run: `POST .../workflows/{id}/versions/{v}/execute`, same payload.
  A successful draft test is what opens the publish gate.

Output: per-file `user_document_id`, execution ID, execution URL. `--wait`
polls until any of the four terminal statuses, then prints the step table;
exits non-zero unless the final status is `completed`.

### retry

```
wb.py retry --account <acct> --exec-id <old-execution-id> [--instruction "..."] [--wait]
```

Composite — there is no retry endpoint. The script:

1. `GET /api/v1/workflow-execution/logs/{old}` → `request.documents` (the
   ORIGINAL input `user_document_id`s), `request.workflow_id`,
   `request.instruction`, and `workflow_version` (if the old run was a
   draft/version execution). The execution *documents listing* is used only
   as a fallback when `request.documents` is empty — that listing also
   contains generated outputs (filled templates, excel exports) which must
   not be re-fed as workflow inputs.
2. `POST /api/v1/workflow-execution` (or `.../versions/{v}/execute` to match
   the old run's kind) with the same input `user_document_id`s and the old
   instruction (override with `--instruction`)

This is a **brand new execution** — new ID, runs from step 1; only the document
list is inherited. Retry helps for transient failures (network blip, upstream
5xx, cleared rate limit). It does not help for deterministic failures (a doc
that always times out parsing, a code bug, a schema mismatch) — the same step
fails again. **Before bulk-looping retries, retry ONE representative failure
and check `status`**; if it fails at the same step with the same error, stop
and fix the step or skip the offending docs. Old failed executions can stay as
an audit trail or be removed from the queue with `cancel`.

Output: old/new execution IDs, workflow ID (+ version), doc count, both URLs.

### executions

```
wb.py executions --account <acct> [--workflow-id <uuid|name>] \
  [--statuses completed,failed] [--owner-uids ...] [--owner-names ...] \
  [--search "text"] [--date-from 2026-08-01] [--date-to 2026-08-17] \
  [--limit 50] [--cursor <c>] [--all]
```

`GET /api/v1/workflow-execution/logs`. All filters are query params; list
filters repeat the key. Omitting `--workflow-id` lists across all workflows in
the org. `--date-from` is inclusive, `--date-to` exclusive (ISO
8601). Pagination: default `--limit 20` (max 100) with `next_cursor`; `--all`
follows cursors to the end; `--limit 0` returns everything server-side in one
response (no cursor — slower for big sets). The endpoint is scoped to the
request's active org.

Output: total count, then per row `created_at`, `status`, `owner_name`, ID,
`title`, plus the next cursor when more pages exist.

### status

```
wb.py status --account <acct> --exec-id <execution-id>
```

`GET /api/v1/workflow-execution/logs/{id}`. Prints overall status, title, URL,
then a per-step table from `result.steps`: marker, `input.step_name` (never
`title`), `step_type_raw` (falls back to `step_type` when null on old
executions), status. Failed steps print `output.error`; table-producing steps
print their `table_v1_log_id` (feed it to `table`). A `paused` execution is
usually waiting for manual input — send the user to the execution URL to
resume it in the UI.

### results

```
wb.py results --account <acct> --exec-id <id> [--step NAME] [--out DIR] [--excel]
```

Per-step outputs from the execution log:

- **Table steps** (output carries `table_v1_log_id`): fetches every row via
  paginated `GET /api/v1/table/v1/logs/{id}` and prints the row count,
  columns, and a 3-row preview — not the full dump.
- **Other steps**: print their output JSON (truncated at 2000 chars).
- **Failed steps**: print the error.
- `--step NAME` filters by substring match on `step_name`.
- `--out DIR` writes one JSON file per step (full table rows included).
- `--excel` (requires `--out`) additionally exports each table step to `.xlsx`
  via `POST .../export-excel`.

For anything the user wants to eyeball, the execution URL from `status` is
usually better than raw JSON — offer both.

### cancel

```
wb.py cancel --account <acct> --exec-id <execution-id>
```

`POST /api/v1/cancel-workflow-execution` with `{"workflow_execution_id": ...}`.
Known limitation: only reliable for paused / manual-input executions — an
actively running execution can race the cancel and flip back to running.

### table

```
wb.py table --account <acct> --log-id <table_v1_log_id> [--cursor 0] [--limit 50] [--search "kw"]
wb.py table --account <acct> --log-id <id> --export-excel [--out table.xlsx]
```

`GET /api/v1/table/v1/logs/{log_id}` with `cursor`/`limit`/`search` params →
`{data, schema, pagination}`. Prints row count, pagination, and a TSV of the
rows. `--export-excel` calls `POST .../export-excel`, gets back a
`user_document_id`, and downloads the file. Cell edits exist at
`PATCH .../cells` (`{row_index, column_name, new_value}`) but are deliberately
not wrapped — patch prod data only with explicit user sign-off.

### documents

```
wb.py documents list --account <acct> --exec-id <id>
wb.py documents download --account <acct> --doc-id <user_document_id> [--out dir/]
wb.py documents download-all --account <acct> --exec-id <id> [--out dir/] \
  [--input-only | --output-only] [--concurrency 8]
```

- `list`: `GET /api/v1/workflow-execution/logs/{id}/documents` → ID + filename per doc.
- `download`: mints a short-lived SAS URL and streams the bytes; filename from
  document metadata, deduplicated on collision.
- `download-all`: no bulk/zip endpoint exists — one list call, then N parallel
  SAS downloads. Filenames are pre-claimed sequentially so concurrent writes
  can't collide. Each document is classified as **input** (member of the
  execution's `request.documents`) or **output** (generated by the run —
  filled templates, Excel exports, emails); `--input-only` / `--output-only`
  restrict which side gets downloaded. Names are claimed over the full
  document list before filtering, so a file keeps the same name with or
  without the flags, and files already present in `--out` are skipped —
  re-running is safe.

SAS URLs are per-call and short-lived (~24h): never store them, re-mint to
refetch. The same `user_document_id` can appear across executions when a
customer reuses uploads — dedup by content hash when aggregating.

### versions

```
wb.py versions --account <acct> --workflow-id <uuid|name>
```

`GET /api/v1/workflows/{id}?slim=true`. Prints the live published version,
whether an active draft exists, and per version: number, publish date,
published name, notes. Use UI vocabulary when reporting: "Draft vN", "Live",
"Publish", "Promote". Fetch full content for any version with
`download --version N`; promote one with `publish --promote-version N`.

### validate

```
wb.py validate --account <acct> --workflow-id <uuid>
```

`POST /api/v1/workflow-builder/workflows/{id}/validate` — runs the full
collect-all validator on the **active draft** and returns
`{is_valid, errors: [...]}`.

- HTTP 200 with `is_valid: false` is a **successful call** — the command prints
  every error readably and exits 1 so scripts can gate on it.
- HTTP 404 means **no draft exists** (upload one first) — not "invalid".

Needs `fai_admin`. Validate/publish parity: a draft this endpoint calls valid
will not then be rejected by publish validation.

### diff

```
wb.py diff --account <acct> --workflow-id <uuid> --json local.json [--draft | --version N]
```

Fetches the remote definition (live by default) and compares the local JSON
against it: top-level fields (`name`, `welcome_message`, description/comments),
steps added/removed/modified (keyed by `step_name`; modified steps list which
fields differ, e.g. `config`, `input_mappings`), changed `options` keys, and
step-order changes. Exits 0 when identical, 1 when different. Use before
`upload` to confirm a local edit touches only what it should.

### fleet-publish

```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/wb/scripts/wb_fleet_publish.py" \
  --env prod \
  --orgs-file orgs.json \
  --workflow-id <uuid> \
  --transform transform_lib.py \
  --change-summary "..." --publish-name "..." [--publish-notes "..."] \
  [--results-file results.json] \
  --dry-run
```

Applies one edit to the **same** `workflow_id` across many orgs. Per org:
download that org's own current published JSON → apply the transform → `PUT`
the active draft (`enable_validation: false`) → publish. It never blasts one
org's JSON fleet-wide — per-org copies of a shared `workflow_id` can sit at
different versions and configs.

- **Requires the restricted skill key.** The script fails fast with a clear
  message if `FAI_SKILL_{ENV}_PERSONAL_API_KEY` is absent: a personal key acts
  only as its owner and cannot reach orgs the owner isn't a member of, so it
  cannot run a fleet rollout.
- **Orgs file**: `{"organizations": [{"organization_name": "...", "owner_oid": "<uuid>"}, ...]}`.
  If the user points at a page or table of orgs, convert it to this shape.
- **Transform module**: a python file exposing `transform(wf: dict) -> dict`.
  **Hard-assert every anchor** it depends on (step name present, prompt
  substring found, mapping shape as expected). An `AssertionError` marks that
  org `transform_failed` and leaves it untouched; any other exception fails
  that org only and the fleet loop continues. Omit `--transform` to republish
  each org as-is.
- **`--dry-run`** fetches + transforms everywhere and writes nothing. **Always
  run it first** and show the user the per-org JSON-lines output before a real
  run.
- **`--allow-override`** retries publish with `override: true` only when the
  draft-test gate blocks (validation still runs). Only pass it when the user
  explicitly asked for a no-test rollout.
- **Skips** (org untouched, reported): workflow not present in org (404), and
  no published content — a never-published workflow returns a shell whose
  upload would clobber the org's active draft.
- Progress is JSON-lines per org; `--results-file` writes the full per-org
  results for verification. Final line: `DONE: {published}/{eligible} published
  ({n} skipped)`. Exit 0 on dry-run or when every eligible org published, else 1.

**This writes to customer-facing prod workflows across many orgs. Keep the
human in the loop at two points: approving the transform module before the real
run, and the publish itself.** Never go straight to a non-dry run.

## Endpoint quick reference

```
Versioned editing (base: /api/v1/workflow-builder/workflows):
  POST   /                              Create shell + active draft
  POST   /{id}/clone                    Clone into a new draft workflow
  PUT    /{id}                          Save active draft (never publishes)
  POST   /{id}/validate                 Validate active draft (200 even when invalid)
  POST   /{id}/versions/{v}/execute     Draft / specific-version test run
  POST   /{id}/publish                  Publish active draft (test gate applies)
  POST   /{id}/versions/{v}/publish     Promote old version to live
  GET    /{id}/versions/{v}             Full content of a published/archived version

Workflow reads:
  GET    /api/v1/workflows                        List workflows in the active org (?slim=true&limit=100)
  GET    /api/v1/workflows/{id}                   Live content + version summaries (+?slim=true|exec)
  GET    /api/v1/workflows/{id}?draft=true        Active draft (FAI-admin only; 404 = no draft)

Executions:
  POST   /api/v1/workflow-execution               Start a published execution
  POST   /api/v1/resume-workflow-execution        Resume after pause/manual input
  POST   /api/v1/cancel-workflow-execution        Cancel (reliable only when paused)
  GET    /api/v1/workflow-execution/logs          List (filters + cursor pagination)
  GET    /api/v1/workflow-execution/logs/{id}     Status + per-step results
  GET    /api/v1/workflow-execution/logs/{id}/documents   List the run's documents

Documents & tables:
  POST   /api/v1/user-documents-upload            Register (owner_uid/oid = "")
  POST   /api/v1/user-documents-download          Mint a ~24h SAS URL
  GET    /api/v1/user-documents/{id}              Metadata
  GET    /api/v1/user-documents/{id}/content      Stream bytes (proxied)
  GET    /api/v1/table/v1/logs/{log_id}           Table data (cursor/limit/search)
  POST   /api/v1/table/v1/logs/{log_id}/export-excel   → {user_document_id}
```

## Related skills

Workflow copy/publish as part of Eval Studio setup pairs with the
`fai:eval-studio` skill — datasets pin a workflow version, so copy the pinned
version, publish the copy, and only then create/run datasets against it.
Dataset-only questions (ground truth, accuracy reports, runs) belong to
`fai:eval-studio`, not here.
