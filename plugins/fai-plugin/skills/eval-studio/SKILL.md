---
name: eval-studio
description: Operate Eval Studio test batches through the platform API — create and inspect datasets, manage samples, upload samples in bulk from CSV/Excel/JSON/folders, edit ground truth (answers), run the evals, and generate accuracy reports. Use when the user mentions "eval studio", a "test batch" or "dataset", "ground truth", an "accuracy report", "run the evals", "score the workflow", wants to import a spreadsheet or folder of labeled samples, or wants to test workflow changes against labeled data.
---

# Eval Studio

Create and manage Test Batches (datasets), their Samples (submissions) and Answers (ground truth), run tests, and generate accuracy reports. Everything goes through the platform API via the bundled script — no database access, no invented routes.

All commands share the same invocation and global flags:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/eval-studio/scripts/eval_studio.py" <command> [args] \
  [--env prod|staging|local] [--account <slug|name|domain>] [--org-id <uuid>]
```

| Task | Command |
|---|---|
| List test batches | `list-datasets [--workflow <uuid\|name>] [--dataset-type live\|gold\|regression]` |
| Inspect one test batch | `get-dataset <dataset-id>` |
| Create a test batch | `create-dataset --name <n> --workflow <uuid\|name> --selected-step <step>... [options]` |
| Update batch config | `update-dataset <dataset-id> [options]` |
| Repin to latest version | `repin <dataset-id> [--selected-step <step>...]` |
| See evaluable steps | `runnable-steps (--dataset <id> \| --workflow <uuid\|name> [--version <n\|latest>])` |
| List samples | `list-submissions <dataset-id>` |
| Copy or move samples | `copy-submissions --source <id> --target <id> --submission-id <id>... [--move]` |
| Remove sample from batch | `remove-submission <dataset-id> <submission-id>` |
| Answers template | `gt-template <dataset-id> [--meta] [--out <file>]` |
| Read answers | `gt-read <dataset-id> <submission-id> [--step <name> [--cursor N --limit N]] [--out <file>]` |
| Edit answer cells | `gt-set-cells <dataset-id> <submission-id> --step <name> --updates <json\|@file>` |
| Insert grid answer row | `gt-add-row <dataset-id> <submission-id> --step <name> --row-index N [--position above\|below]` |
| Delete grid answer row | `gt-delete-row <dataset-id> <submission-id> --step <name> --row-index N` |
| Bootstrap answers from AI output | `copy-output-to-gt <dataset-id> <submission-id> --step <name>` |
| Run a test batch | `run <dataset-id> [--workflow-version N] [--wait]` |
| Rerun one sample | `run-submission <dataset-id> <submission-id> [--wait]` |
| Check run status | `run-status <dataset-id> [--run-id <run-id>]` |
| Cancel a stuck run | `cancel-run <dataset-id>` |
| Score / fetch report | `accuracy-report <dataset-id> [--scoring-mode dataset\|all_fields] [--generate] [--run-id <id> [--config-hash <h>]] [--submission-id <id>] [--json]` |

Most read commands accept `--json` for the raw API payload.

## Vocabulary — say it like the UI

Use the UI words with users; the API names appear in parentheses on first mention.

| API name | UI label (use this in user-facing output) |
|---|---|
| Dataset | Test Batch |
| Submission | Sample |
| ground_truth | Answers / Ground Truth |
| output_data / predicted | AI-Generated |
| evaluation run | Test run |

## IDs and formats

| ID | Shape | Example source |
|---|---|---|
| `workflow_id` | UUID | Workflow Builder |
| `dataset_id` | 24-char hex ObjectId | the `/eval/<dataset-id>` URL in the app |
| `submission_id` | 24-char hex ObjectId | `list-submissions` |
| `evaluation_run_id` | 24-char hex ObjectId | `run` / `run-status` output |

The script validates ID formats before calling; a UUID where an ObjectId belongs (or vice versa) fails fast with a clear message.

## Environment and account resolution

- `--env` defaults to `defaults.env` in `~/.fai/accounts.json`, else `prod`.
- `--account <slug|name|domain>` resolves the org ID (and workflow names) from `~/.fai/accounts.json`. If the reference is ambiguous or unknown, the error lists the known accounts — relay that list and ask the user to pick.
- `--org-id <uuid>` always wins over `--account`.
- `--workflow` accepts a raw UUID anywhere; a workflow *name* is resolved against the account record and therefore requires `--account`.
- Staging and prod are separate identity instances: keys, user IDs, org IDs, and dataset IDs never cross environments. A 404 for a prod ID against staging is expected, not a bug.
- Credentials come from `~/.fai/credentials.env` (written by the `fai:setup` skill). If the script reports missing credentials, ask the user for their API key and user ID for the target environment and write them via `fai:setup`, then retry the command — do not hunt for other credential files.

## Create a test batch

Test batches require a **published** workflow version. If the workflow has never been published, publish it first (use the `wb` skill), then create the batch.

```bash
python3 ".../eval_studio.py" create-dataset \
  --account acme \
  --name "gold-v1" \
  --dataset-type gold \
  --workflow submission_intake \
  --pinned-workflow-version 7 \
  --selected-step "Basic Fields" --selected-step "Loss Runs" \
  --scored-fields-by-step '{"Basic Fields": ["insured_name", "policy.number"]}' \
  --grid-row-match-keys '{"Loss Runs": ["policy_number", "claim_number"]}' \
  --default-step-accuracy-threshold 0.9 \
  --step-accuracy-thresholds-by-step '{"Basic Fields": 0.95}'
```

Alternatively pass the whole request body as JSON: `create-dataset --body request.json`. The same client-side validation runs either way.

Request body shape (POST `/api/v1/evaluation/datasets`):

```json
{
  "name": "gold-v1",
  "description": "optional",
  "dataset_type": "live",
  "workflow_id": "<workflow-uuid>",
  "pinned_workflow_version": 7,
  "selected_steps": ["Basic Fields", "Loss Runs"],
  "scored_fields_by_step": {"Basic Fields": ["insured_name", "policy.number"]},
  "grid_row_match_keys": {"Loss Runs": ["policy_number", "claim_number"]},
  "default_step_accuracy_threshold": 0.9,
  "step_accuracy_thresholds_by_step": {"Basic Fields": 0.95}
}
```

Field rules — the script enforces the client-checkable ones before calling:

- `dataset_type`: `live`, `gold`, or `regression`. `gold` and `regression` are tier-gated; users without restricted dataset-tier access cannot see or create them (403).
- `pinned_workflow_version` is optional; omitted pins to the workflow's latest **published** version. Runs and reports are scoped to the pin.
- `selected_steps`: **exact** step names on the pinned workflow version — fetch `runnable-steps` first and copy names verbatim. Dependency steps are resolved by the backend; do not select a step just because another step needs it.
- `scored_fields_by_step` (optional per-step field allowlist; omitted steps score all fields):
  - Keys must be selected step names; values non-empty lists of exact field selectors.
  - Selectors are top-level fields (`insured_name`) or schema dot paths (`policy.carrier`, `quote_options.carrier`). Array traversal is inferred from the schema — **JSONPath syntax like `quote_options[*].carrier` is rejected**.
  - **Never mix a parent and its child** for the same step (`policy` and `policy.carrier`) — the backend rejects it.
  - If the step schema has literal field names containing `.`, nested selector mode is rejected for that step; ask the user whether the dotted names are literal keys.
- `grid_row_match_keys` (optional, for grid/table steps with unstable row order): keys are selected grid-step names, values non-empty lists of exact field names that are present, non-empty, and unique per row in both AI-Generated and Answers. Bad keys make report generation fail loudly — that is by design.
- Thresholds: floats in `0.0`–`1.0`. `default_step_accuracy_threshold` defaults to `0.90` server-side; `step_accuracy_thresholds_by_step` keys must be selected steps. Thresholds drive report pass/fail, not the raw score.

### Selected-step protocol (do this every time)

Never auto-select every runnable step. Before creating:

1. `runnable-steps --workflow <id>` — get the real step names.
2. `list-datasets --workflow <id>` — if a sibling batch exists, recommend its `selected_steps`, scored fields, and row-match keys as the default.
3. Otherwise infer a narrow set from the user's stated goal or available answers; if still unclear, show the runnable steps and ask.
4. Show a short confirmation before the create call: env, org, workflow + version, batch name/type, the exact selected steps and why, scoring config, thresholds. Create only after the user confirms the steps.

## Inspect test batches

- `list-datasets [--workflow ...] [--dataset-type ...]` — one line per batch: ID, name, type, pinned version, sample counts. Filter before showing; for long lists show the matching candidates (usually at most 5), not the full dump.
- `get-dataset <dataset-id>` — full config: selected steps, scored fields, row-match keys, thresholds, pin status, sample counts, app URL.
- `runnable-steps` — per step: `step_name`, `step_type`, `is_extract`, `has_gt`. Copy `step_name` verbatim into any config.

Useful response fields on a dataset: `pinned_workflow_version` vs `latest_workflow_version` and `is_pinned_to_latest` (repin may be needed), `submission_count`, `submissions_with_gt` (samples with *any* answers, not necessarily answers for every selected step), `last_run_accuracy`.

`update-dataset` (PATCH, same field rules as create) changes name, selected steps, scoring config, or thresholds. **Any scoring-config change (scored fields, row-match keys, thresholds) makes cached accuracy reports stale — regenerate before trusting numbers.**

`repin <dataset-id>` re-pins the batch to the workflow's latest published version; pass `--selected-step` replacements when old step names no longer exist on the new version.

## Manage samples

- `list-submissions <dataset-id>` — ID, name, document count, answers present, status per sample.
- `copy-submissions --source <ds> --target <ds> --submission-id <id>... [--move]` — POST to `/datasets/{target}/submissions/copy` with `{"source_dataset_id": ..., "submission_ids": [...], "move": false}`. Source and target must be in the same org **and belong to the same workflow ID**. Copy is add-only: it creates new target samples, reuses document references, clones structured answers for target selected steps only, and does not copy execution pointers. `--move` additionally unlinks the source rows after copying.
- `remove-submission <dataset-id> <submission-id>` — unlinks the sample from that batch. It does **not** necessarily delete the underlying sample or documents globally; never present it as a hard delete. Only run it when the user explicitly asked.

There is no batch-subset run API. For subset-only accuracy: create a temporary test batch (same workflow), `copy-submissions` the subset into it, run and report there, then remove it from use.

Adding *new* samples (documents + answers) in bulk: see "Upload samples in bulk" below.

## Upload samples in bulk

Add new samples to an **existing** test batch from local sources. Separate script, same global flags:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/eval-studio/scripts/eval_upload.py" <command> [args] \
  [--env prod|staging|local] [--account <slug|name|domain>] [--org-id <uuid>]
```

| Step | Command |
|---|---|
| 1. Normalize the source | `normalize --input <src> [--out-dir <dir>] [--dataset <id> \| --template <file>] [--mapping <file>] [--documents-dir <dir>]` |
| 2. Dry-run (mandatory) | `dry-run <dataset-id> --manifest <dir>/manifest.json [--allow-partial-gt] [--allow-missing-fields] [--allow-extra-fields] [--allow-duplicate-names]` |
| 3. Upload | `upload <dataset-id> --manifest <dir>/manifest.json [--reason <text>] [same --allow-* flags as the dry-run]` |
| Cleanup (rare) | `close-session --session-file <dir>/upload_session.json` |

Accepted inputs (exact shapes, mapping files, and coercion rules in [references/bulk_upload.md](references/bulk_upload.md)):

| Input | Shape |
|---|---|
| Folder | one subfolder per sample: document files (or a `documents/` subdir) + optional `gt.json` `{"steps": {...}}` |
| CSV / Excel | sample-name column + files column + answer columns (`field` or `Step Name.field`); repeated sample rows become grid rows; needs `--dataset` or `--template` for column inference |
| JSON | normalized manifest, array of submission objects, or a single `{"steps": ...}` answers object |
| Custom layouts | write `manifest.json` by hand in the reference format, then dry-run it |

Dependencies: CSV, JSON, and folder inputs use the standard library. Excel input
requires `python3 -m pip install pandas openpyxl`. See
`${CLAUDE_PLUGIN_ROOT}/DEPENDENCIES.md` for the shared capability matrix.

Guardrails — hold all of these:

- **Existing test batch required.** The script never creates or configures batches; `create-dataset` above does that first.
- **Add-only.** Existing samples, documents, and answers are never modified or deleted. Manifest names that collide with existing samples block by default (`--allow-duplicate-names` downgrades to a warning and creates a second sample with the same name).
- **Dry-run is mandatory.** `upload` refuses without a fresh matching dry-run report (same manifest hash, same files on disk, same `--allow-*` flags) and re-validates everything again at upload time. `--force-no-dry-run` skips only the report check, still validates, and should stay unused.
- Answers are validated against the live answers template: unknown steps, non-selected steps, and missing/extra fields all block. Relax per flag, sparingly, and mention any relaxation when reporting results.
- A `PRE_BOOTSTRAP_TEMPLATE` warning means a selected step's template is a placeholder (few generic fields) and the backend would silently drop the real answer fields: upload documents only (`--allow-partial-gt`, no answers for that step), run one sample, then upload answers.
- Upload runs through a scoped upload session (created and closed automatically — closed even on failure, which also cleans up samples that never completed; sessions self-expire after ~480 minutes). `upload_session.json` holds the session token: never print, share, or commit it.
- Session creation needs internal-FurtherAI-org membership (403 otherwise) and is blocked while a test run is active on the batch.

The audit (`upload_audit.json` + printed summary) lists per sample: documents uploaded, answer steps attached, and uploaded / uploaded-without-answers / failed status. After a partial failure, completed samples remain (add-only): build a new manifest containing only the missing samples, dry-run, and upload again — never re-upload completed ones.

## Edit answers (ground truth)

Read before writing — always fetch current answers first so edits target real field names and row indexes.

- `gt-template <dataset-id>` — the answer template: which steps/fields expect labels. `--meta` fetches template metadata instead of content. If the template reports `missing_schema_steps`, that step has no known schema yet: run one representative sample first (or select the underlying extraction steps instead) — do not force labels into a schemaless step.
- `gt-read <dataset-id> <submission-id>` — all answers for a sample (GET `.../gt/content`). With `--step <name>` it uses the paginated per-step read (GET `.../gt/read?step_name=&cursor=&limit=`) — use this for large grids.
- `gt-set-cells ... --step <name> --updates <json|@file>` — PATCH `.../gt/cells?step_name=` with body:

```json
{"updates": [{"field_name": "policy_number", "row_index": 0, "new_value": "ABC-123"}]}
```

  For key-value (non-grid) steps use `row_index: 0`.
- `gt-add-row` / `gt-delete-row` — grid answers only. Add posts `{"row_index": N, "position": "above"|"below"}`; delete removes one row index.
- `copy-output-to-gt ... --step <name>` — copies the sample's current AI-Generated output into Answers for that step. Bootstrapping only: the user must review what got copied, otherwise the batch scores the model against itself.

Guardrails:

- **Never edit answers while a run is active** — check `run-status` first.
- Prefer cell edits for small repairs; for whole-answer replacement use the app UI upload flow.
- **Document-loop steps** (a workflow loop iterating over documents): after a run, one logical step expands into one target per document, keyed by an opaque reserved key beginning `__fai_eval_loop_doc__:`. Treat these keys as **read-only strings**: always read them back from the API (`gt-read`, report payloads) and pass them verbatim as `--step`. Never construct one by hand, and never use `Step::iter_N`, a filename, or `Step / filename` as an answers key. Writing under the bare logical step name when multiple document iterations exist is ambiguous — don't.

## Run tests

- `run <dataset-id> [--wait]` — runs all complete samples against the pinned workflow version. `--workflow-version N` runs a different **published** version once, without repinning the batch. Response includes `evaluation_run_id` — keep it; `--wait` polls it to a terminal status.
- `run-submission <dataset-id> <submission-id> [--wait]` — reruns one sample. Creates its own single-submission run row; use for debugging/repair, not for subset accuracy.
- `run-status <dataset-id>` — the dataset-level view (usually the running or latest full run). `--run-id <id>` pins polling to one exact run — always use it right after starting a run, and for version-comparison experiments where later runs must not overwrite your anchor.
- `cancel-run <dataset-id>` — cancels the active run.

Run statuses: `running`, `completed`, `failed`, `cancelled`, `terminated` — the last four are all terminal; polling only for completed/failed hangs on a cancelled or terminated run. Per-sample rows show the surfaced execution status and `steps_completed`/`steps_total`.

Repair flow after a partially failed full run:

1. `run-status` — note failed/missing samples.
2. `run-submission --wait` each failed sample.
3. Regenerate the accuracy report. It anchors on the latest full run but scores each sample's freshest completed execution for the current pin, so repaired samples count.
4. If the report says `partial: true`, rerun the IDs in `missing_completed_execution_submission_ids`.

If `run` rejects with stale selected-step errors, fetch `runnable-steps` and ask the user whether to `update-dataset` or `repin`.

## Get accuracy reports

`accuracy-report <dataset-id>` fetches the report; if it is not generated (or `--generate` is passed to force a rebuild) it generates, polls until terminal, then prints. Report statuses: `not_generated`, `generating`, `generated`, `failed`.

- `--scoring-mode dataset` (default) applies the batch's `scored_fields_by_step`; `all_fields` ignores it and scores every comparable field — use for audits without mutating config.
- Reports are **cached per mode and config hash**. Fetching a mode other than the one generated returns `not_generated` (with `requested_scoring_mode` / `current_scoring_mode`) — the script handles this by generating the requested mode.
- `--submission-id <id>` fetches one sample's detail (requires the summary for the same mode to exist first). Use it for field-level debugging instead of recomputing.
- `--run-id <evaluation-run-id>` uses the run-scoped report (`/api/v1/evaluation/runs/{run_id}/accuracy-report`) — API-only, invisible in the UI. Use for cross-version experiments so later runs don't replace the comparison anchor. Generation returns a `config_hash`; the script reuses it for polling and prints it — record it, and pass `--config-hash` to re-fetch that exact variant later.
- `--json` prints the raw payload.

### The headline guardrail — read this before quoting any number

On current reports (`report_schema_version` 4+), **`overall_accuracy` is micro-F1 computed over classification-emitting steps only** — Extract / ExtractRows. SOV, function, and custom steps emit no classification counts and **cannot move the headline no matter how badly they regress**. Never quote the headline alone. Always surface together:

- `overall_accuracy` with `overall_accuracy_source` (`micro_f1` normally; `micro_accuracy` when the outcome mix leaves F1 undefined; `scorer_step_accuracy`/`field_macro_accuracy` only when no classification counts exist at all — trust the source field, not a fixed label);
- `headline.coverage` — high F1 on low coverage means little was actually labeled;
- `headline.steps_without_counts` — the scored steps the headline does *not* cover;
- per-step `accuracy` + `meets_accuracy_threshold` — the only way to gate un-counted steps.

The script's default output prints exactly this set with the caveats inline. Keep them when relaying results.

### Reading the summary

| Field | Meaning |
|---|---|
| `is_stale` / `stale_reason` | Batch scoring config changed since generation. Regenerate before trusting anything. |
| `partial` | Execution coverage only: `true` means ≥1 anchor-run sample lacks a completed current-pin execution. A `failed` historical anchor run does **not** by itself make the report partial. |
| `evaluation_run_id` / `evaluation_run_status` | The anchor run and its historical status (often `failed` in repaired flows — that's fine). |
| `missing_completed_execution_submission_ids` | Rerun these to clear `partial`. |
| `missing_gt_submission_ids` | Completed executions with no answers — excluded from scoring, does not set `partial`. |
| `headline` | `{metric, f1, coverage, precision, recall, steps_scored, steps_with_counts, steps_without_counts}` (v4+). |
| `scoring_config` | Snapshot actually used: selected steps, scored fields, row-match keys, `effective_step_accuracy_thresholds`. |

Per-step: `accuracy` (canonical when `accuracy_source` is `scorer_step_accuracy`; `field_macro_accuracy` is diagnostic fallback — never summarize experiments from macro values when scorer accuracy exists), `accuracy_threshold_percent`, `meets_accuracy_threshold`, `accuracy_status` (`passing`/`failing`), and `scored_field_count`/`available_field_count`/`omitted_field_count` (high omitted counts = the field filter is intentionally narrowing).

Scoring behavior worth knowing before blaming the workflow:

- Grid/table steps use a stricter ladder than key-value extraction — no fuzzy string credit inside aligned rows. Before treating a grid score drop as a regression, check whether `grid_row_match_keys` is configured and inspect row alignment.
- Boolean-like strings match conservatively whole-phrase (`confirmed` ≈ `yes`, but `N/A` ≠ `no`; `available coverage` ≠ `available`).
- Empty arrays/objects are explicit comparable paths and can score as matches.
- Document-loop steps appear in report `steps` under their opaque `__fai_eval_loop_doc__:` keys; the script renders them by logical step name + document filename.

## Production safety

Reading prod is fine when asked. Any **prod write or prod run** — create/update/repin a batch, copy/move/remove samples, a bulk `upload`, any answers edit, `copy-output-to-gt`, `run`, `run-submission`, `cancel-run`, or a report generation that replaces cached state — needs explicit user confirmation immediately before the call, naming the exact operation, dataset ID (or "new batch"), sample IDs if applicable, and side effects (run cost, cached-report replacement, add-only vs unlink). Do not proceed on a vague "yes" given for a different target. Never write to prod as a side effect of an inspection request.

Also: never print tokens, API keys, or SAS URLs — the client handles auth internally; report "auth OK" at most.

## Failure modes

| Symptom | Cause / fix |
|---|---|
| 404 on everything | Wrong org for the ID, or crossing environments (prod ID on staging). Check `--account`/`--org-id` and `--env`. |
| 403 | Dataset tier (`gold`/`regression`) permission gate, or route not allowed for the restricted key. |
| Create fails: no published version | Publish the workflow first (`wb` skill), then create. |
| 400 on `scored_fields_by_step` | JSONPath syntax, parent+child mix, unknown field vs schema, or schema unavailable. Fix selectors; script pre-checks the first two. |
| 400 on `grid_row_match_keys` | Key not a selected grid step, or field not in schema. |
| Report `not_generated` right after generating another mode | Mode-specific cache — generate the mode you want to fetch. |
| Report `is_stale` | Config changed since generation — regenerate. |
| Run start rejects selected steps | Steps no longer on the pinned version — `runnable-steps`, then `update-dataset` or `repin`. |
| Report generation blocked | No sample has a completed current-pin execution — run the batch first. |
| `upload` refuses to start | No dry-run report, or the manifest/files/flags changed since the dry-run — rerun `dry-run`. |
| 403 creating an upload session | Caller is not a member of the internal FurtherAI org, or a test run is active on the batch. |
| Grid step score looks wrong | Check row alignment + `grid_row_match_keys` before touching the workflow. |

## Out of scope

- **Cross-environment dataset copies**, multi-run experiment batches, and scorer-override sweeps. (Bulk sample upload is in scope — see "Upload samples in bulk".)
- **Deleting a test batch**: not available through this skill's API surface — it's a separate admin operation. Say so plainly if asked; do not improvise a delete.
