---
name: triage
description: Diagnose a workflow execution end-to-end — why did this execution or submission fail, investigate this error, what happened to this run, why is this workflow stuck or slow, check the logs for an execution. Produces a focused diagnosis (what failed, where, why, suggested next action), never a log dump. Uses the platform API for everyone, plus Logfire SQL when the Logfire MCP server is connected.
argument-hint: <execution_id or execution URL> [question or step name]
---

# triage — execution diagnosis

Turn an execution id (or URL) plus a question into a focused diagnosis. Two tiers:

| Tier | Needs | What it sees |
|---|---|---|
| 1 — Execution log (always start here) | Platform API credentials only (`~/.fai/credentials.env`) | Whole-execution status, per-step status/type/duration/output/error |
| 2 — Logfire traces | The Logfire MCP server connected (a `mcp__logfire__query_run` tool exists) | Backend spans, exceptions, function-step logger output, infra-level failures |

Tier 1 answers most questions. Go to Tier 2 only when the execution log can't explain the symptom (failure outside step bodies, silent stalls, "what did the code actually log"). If no `mcp__logfire__*` tools are available, say Tier 2 is unavailable in this session and hand off what Tier 1 found.

## Symptom → route

| Symptom | Route |
|---|---|
| A step shows `failed` | Read its `output.error` (the inspect script prints it in full) → diagnose → suggested fix below |
| Execution `running` for a long time | Step table: which step is `in_progress`, for how long; compare per-step durations |
| Execution `paused` / `blocked` / `suspended` | A pause / hold / manual_input step is waiting on a human. Not an error — say who needs to act, in the app URL |
| Execution `cancelled` / `terminated` | TERMINAL. Say so plainly — people forget these never resume. Retry = new execution with the same documents (`/fai:wb retry`) |
| Execution `failed` but no step is `failed` | Failure happened outside step bodies (dispatch/infra) → Tier 2, or escalate with the Tier 1 header |
| Completed but output looks wrong | `step` command on the suspect step — a `completed` execution can still contain a step whose output encodes a downstream failure (e.g. an integration step returning an error payload inside `output.data`) |
| Script's own API call fails | Error-code table at the end of Tier 1 |

## Tier 1 — the execution log

Always run this first, whatever the question — it is one API call and anchors everything else (status, step names, timestamps for Tier 2 windows):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/triage/scripts/inspect.py inspect <execution_id_or_url> \
  [--env prod|staging|local] [--account <slug> | --org-id <uuid>] [--json]
```

Prints: header (status, workflow, version, owner, timing, app URL), a step table (name, genuine type, status, duration), full `output.error` for every failed step, and anomaly flags (step `in_progress` >15m, non-terminal execution stale >30m, terminal-but-unusual statuses, failed-with-no-failed-step).

To read one step's full output (and, for table steps, the first page of table rows):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/triage/scripts/inspect.py step <execution_id_or_url> "<step name>" \
  [--rows N] [--env ...] [--account ... | --org-id ...] [--json]
```

Both commands accept a full execution URL (`https://app.furtherai.com/workflow-execution/<id>/...`) anywhere an id is accepted; the environment is inferred from the host (`app.furtherai.com` → prod, `app-staging.furtherai.com` → staging) unless `--env` overrides it. Personal-key auth is org-scoped, so pass `--account` or `--org-id` for the org that owns the execution.

### Execution anatomy (GET /api/v1/workflow-execution/logs/{execution_id})

Top level:

| Field | Meaning |
|---|---|
| `status` | Whole-execution verdict. Non-terminal: `not_started`, `running`, `paused`, `blocked`, `suspended`. Terminal: `completed`, `failed`, `cancelled`, `terminated` — all FOUR are terminal; polling only for completed/failed hangs forever |
| `created_at` / `last_updated_at` / `end_time` | Execution timing. A non-terminal status with a stale `last_updated_at` (>30m) means stuck, not slow |
| `request` | What was asked: `workflow_id`, `workflow_version`, `documents` (the original upload) |
| `result.steps[]` | Per-step results — the array to key everything off |

Each element of `result.steps[]`:

| Field | Meaning |
|---|---|
| `input.step_name` | The canonical step name. **Key off this, never `title`** — `title` is a dynamic UI string ("Completed Prepare Documents") |
| `status` | `in_queue`, `in_progress`, `success`, or `failed` |
| `output` | The step's result. Failed steps carry `output.error` (a string). Table steps carry `output.table_v1_log_id` — the rows live behind `GET /api/v1/table/v1/logs/{log_id}?cursor=0&limit=N` |
| `step_type_raw` | The genuine step type. May be null on older executions — then recover the type from the workflow version's step config: `GET /api/v1/workflow-builder/workflows/{workflow_id}/versions/{workflow_version}`, match steps by `name` (workflow content steps key on `name`, not `step_name`). The inspect script does this automatically and marks recovered types with `*` |
| `step_type` | **LOSSY display bucket — never trust it for the real type.** Several real types collapse onto one label: `agentic_extraction`, guideline checks, and `combine_kv_tables` all display as `extract_from_document`; `function` and `workflow_dispatcher` display as `wait`; `prepare_documents` displays as `email`. Trusting this field once produced a false root-cause analysis ("wrong workflow ran") on an agentic workflow that displayed as a plain extraction pipeline |
| `start_time` / `end_time` / `duration` | Step timing; `duration` is seconds |

### Suggested next actions (put one in every diagnosis)

- Bad or missing input documents → fix the documents and re-run the submission: `/fai:wb retry` (there is no retry endpoint — retry means a new execution with the same document ids, running from step 1 under a new execution id).
- Broken step config (bad prompt, wrong mapping, code error in a function step) → fix the step and upload a draft: `/fai:wb upload`, then test.
- Waiting on a human (`paused`/`blocked`) → name the step and point at the execution URL; the user resumes in the app.
- Infra/dispatch failure or unexplained stall → Tier 2 if available, otherwise escalate to engineering with the execution id, status, and the Tier 1 header.

### If the API call itself fails

| HTTP | Meaning | Fix |
|---|---|---|
| 400 | Bad/missing token on public builder endpoints (they return 400, not 401) or malformed `X-FAI-Target-Org-Id` | Check credentials and org id format |
| 401 | Token expired or invalid — the client re-mints once automatically | If it persists, run `/fai:setup` |
| 403 | Route not on the restricted-skill allowlist, or the user lacks `fai_admin` in the target org | Use a personal key, or ask for the role |
| 404 | Wrong id, wrong org, or caller not a member of the org. Staging and prod are separate PropelAuth instances — an id from one does not exist in the other | Check `--env` matches where the execution ran; check `--account`/`--org-id` |
| 422 | Malformed request body | Check field names against the shapes documented here |

## Tier 2 — Logfire traces (only when the MCP server is connected)

Precondition: a `mcp__logfire__query_run` tool exists in this session. If not, Tier 2 is unavailable — do not improvise another path; report Tier 1 findings and suggest connecting the Logfire MCP server.

### Query rules

- Every call passes `project: "fai-automation-backend"`. Execution ids are globally unique, so no environment filter is needed to find a run.
- SQL over the `records` table (DataFusion, Postgres-like; `ILIKE` supported). `LIMIT` is required — 50 unless the user asks for more.
- Retention is ~30 days; keep query windows ≤14 days. Size the window from Tier 1's `created_at` (start a few minutes before it). An execution older than retention simply is not there — say so.
- Project per-question column lists; never `SELECT *`; pull `exception_stacktrace` only after you have identified the failing row.
- One query per question. If empty, widen once (window or filter). Three or more refinements means stop and ask the user.
- If a result overflows the token limit, tighten the window or step filter — never re-run the same query unwindowed.

### Schema (inline — do not re-query for it)

| Column | Notes |
|---|---|
| `start_timestamp` / `end_timestamp` | UTC; always SELECT and ORDER BY `start_timestamp` |
| `message` | Log message |
| `span_name` | Key values: `fai.step_started`, `fai.step_completed`, `fai.step_failed`, `fai.workflow_started`, `fai.workflow_completed`, `fai.workflow_failed` |
| `level` | 9 = info, 13 = warn, 17 = error |
| `is_exception`, `exception_type`, `exception_message`, `exception_stacktrace` | Populated on caught exceptions |
| `attributes` | JSON. Correlation lives under metadata: `attributes->'metadata'->>'workflow_execution_id'`, `->>'step_name'`, `->>'error'` |
| `trace_id` | Groups events from one execution/activity |
| `service_name`, `deployment_environment` | See environment scoping below |

Correlation: the fast path is `attributes->'metadata'->>'workflow_execution_id' = '{id}'`. The robust fallback is `attributes ILIKE '%{id}%'` — it also catches Temporal's `workflow-builder-{id}` form and older attribute layouts. Start with the fast path; fall back once if empty.

Environment scoping is messy: `deployment_environment` still carries legacy values (`product-prod` and friends). When you need to separate the worker from the API — or staging noise from prod — filter by `service_name`, not by `deployment_environment`.

### Recipes

Substitute `{id}`, `{step}`, `{from}` (RFC3339 UTC, from Tier 1 `created_at`).

**Failure — "why did this run fail?"**

```sql
SELECT start_timestamp, span_name, level, message,
       exception_type, exception_message,
       attributes->'metadata'->>'step_name' AS step_name,
       attributes->'metadata'->>'error'     AS error
FROM records
WHERE start_timestamp >= '{from}'
  AND attributes->'metadata'->>'workflow_execution_id' = '{id}'
  AND (is_exception = true OR level >= 17
       OR span_name IN ('fai.step_failed', 'fai.workflow_failed'))
ORDER BY start_timestamp ASC
LIMIT 50
```

If empty, re-run with `attributes ILIKE '%{id}%'` instead of the metadata equality (once). Pick the earliest failing row, then run the step-output recipe for that step for context.

**Status / progress — "why isn't it moving?"**

```sql
SELECT start_timestamp, span_name,
       attributes->'metadata'->>'step_name' AS step_name,
       message
FROM records
WHERE start_timestamp >= '{from}'
  AND attributes->'metadata'->>'workflow_execution_id' = '{id}'
  AND span_name IN ('fai.workflow_started', 'fai.workflow_completed', 'fai.workflow_failed',
                    'fai.step_started', 'fai.step_completed', 'fai.step_failed')
ORDER BY start_timestamp ASC
LIMIT 100
```

Read the tail: a `fai.step_started` with no matching `fai.step_completed` is the step it is stuck on; all starts matched but no `fai.workflow_completed` means it is between steps, waiting on the dispatcher/Temporal.

**Step output — "what did step X log?"**

```sql
SELECT start_timestamp, level, message
FROM records
WHERE start_timestamp >= '{from}'
  AND attributes->'metadata'->>'workflow_execution_id' = '{id}'
  AND attributes->'metadata'->>'step_name' = '{step}'
ORDER BY start_timestamp ASC
LIMIT 100
```

**Function-step gotcha:** a `function` step's own `logger.info(...)` lines run inside the Temporal activity, on the activity's trace — they carry **neither** `workflow_execution_id` **nor** `step_name`, so the query above returns only the two `fai.step_started`/`fai.step_completed` markers. To read the body logs: take the step's marker window from the status recipe, then drop the execution filter entirely and search by message text inside that window:

```sql
SELECT start_timestamp, level, trace_id, message
FROM records
WHERE start_timestamp >= '{step_started}'
  AND start_timestamp <= '{step_completed}'
  AND message ILIKE '%{distinctive log phrase}%'
ORDER BY start_timestamp ASC
LIMIT 100
```

The marker window is usually tight (<2s), so a text filter is safe. All matching rows should share one `trace_id` (the activity's) — confirm they do, or you may be reading a concurrent run.

**Skip — "why didn't step X run?"**

A skip shows as no `fai.step_started` for that step, or a completion marker with a non-success status. Run the status recipe, then check the upstream step's output (Tier 1 `step` command) — skips usually trace to a decision branch or an input mapping that returned empty.

**Duration — "why so slow?"**

```sql
SELECT attributes->'metadata'->>'step_name' AS step_name,
       start_timestamp, end_timestamp,
       EXTRACT(EPOCH FROM (end_timestamp - start_timestamp)) AS duration_s
FROM records
WHERE start_timestamp >= '{from}'
  AND attributes->'metadata'->>'workflow_execution_id' = '{id}'
  AND span_name = 'fai.step_completed'
ORDER BY duration_s DESC
LIMIT 30
```

Lead with the top three slowest steps. (Tier 1's step table already has per-step durations — use Tier 2 only when you need the intra-step breakdown or the log timeline.)

## Output contract

Hand the user a diagnosis, never a log dump:

```
**<execution_id> — <one-line verdict>**

Failed step: `<step_name>` (<real type>) at <timestamp>
  <the exact error text, quoted>

Context: <1-3 bullets — upstream output, branch taken, doc anomaly>

Likely cause: <one sentence>
Next action: <one concrete step — e.g. fix the input docs and retry the
submission with /fai:wb retry, or fix step <X> and upload a draft
with /fai:wb upload>
```

For status questions, replace the failed-step block with a step-progress timeline; for duration questions, lead with the slowest steps. Quote exact error text; name steps by `input.step_name`. Use UI vocabulary in user-facing output: an execution in a workflow overview is a "Submission", and the success status is `completed` (never "succeeded").
