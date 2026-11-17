# run_workflow

### What this step does

Triggers another workflow. It does NOT process documents itself; the runtime branches on whether an `execution_id` was wired in:

- **`execution_id` absent → `new_execution` mode.** Fires a fresh execution of the target workflow with the passed document IDs. Fire-and-forget: the parent step returns once the child execution is *scheduled*, not when it finishes.
- **`execution_id` present → `modify_documents` mode.** Loads the target execution (version-pinned drafts/archives keep their snapshot), diffs the new doc IDs against the target execution's existing documents, and appends the new ones. If every passed doc is already present, the step short-circuits to a no-op with `message: "All documents already present — no changes made."` and does NOT re-trigger.

Other runtime details that bite:

- The mode is inferred at runtime from inputs — there is no `mode` config flag. Wiring `execution_id` "sometimes" via a decision branch is the correct way to switch between modes.
- Incremental behavior in `modify_documents` mode (TIMELINE vs OVERWRITE) is read from the **target** workflow's `options.incremental_config.add_documents.behavior` — the source workflow has no say. If the target was authored as OVERWRITE, the existing execution restarts from scratch with the combined doc list.
- `eml_user_document_id` is a special-cased optional input. `prepare_documents`' `documents` array only exposes *attachments*; the `.eml`/`.msg` file itself must be passed separately and is prepended to the document list. Wire it whenever the upstream is a `prepare_documents` step.
- Source execution/workflow ids are auto-populated into the child execution's metadata — do NOT try to pass them yourself.

### Required inputs (via `input_mappings`)

| Param                  | Type     | Required | Source                                                                                          |
| ---------------------- | -------- | -------- | ------------------------------------------------------------------------------------------------- |
| `target_workflow_id`   | string   | YES      | Almost always a `static` mapping with the target workflow's UUID. Can also be wired from an upstream step output if the target is computed dynamically. |
| `documents`            | array    | YES      | `dependency` mapping to a step that produces documents (`prepare_documents`, an extraction step's `user_documents`, a step that filters docs, etc.). |
| `execution_id`         | string   | no       | When present, the step runs in `modify_documents` mode. Usually a `dependency` from a step that resolved an existing submission ID. |
| `eml_user_document_id` | string   | no       | Required ONLY when `documents` originates from a `prepare_documents` step with email input. Wire it as a `dependency` to the same step's `eml_user_document_id` attribute. |
| `user_inputs`          | array    | no       | List of `StepUserInput` dicts (`{"step_name": "...", "inputs": {...}}`) that pre-fill steps in the target workflow at trigger time. |

`RunWorkflowConfig` is **empty by design** — every dynamic input comes through `input_mappings`.

### Config keys

None. `config: {}` is the only valid shape.

### Output schema

Emitted as a TableV1-shaped dict with `display_type: key_value` by convention:

```json
{
  "type": "object",
  "properties": {
    "schema":  { "type": "object" },
    "data": {
      "type": "object",
      "properties": {
        "workflow_execution_id": { "type": "string" },
        "target_workflow_id":    { "type": "string" },
        "documents":             { "type": "array", "items": { "type": "string" } },
        "message":               { "type": "string" },
        "snapshot_id":           { "type": "string" },
        "timeline_length":       { "type": "integer" }
      }
    },
    "user_documents": { "type": "array" },
    "metadata":       { "type": "object" }
  }
}
```

Downstream consumers read `data.workflow_execution_id` (only when chaining further) and `data.documents` (filenames, not IDs — hydrated for display). `message` only appears in the "no new documents" no-op branch. `snapshot_id` / `timeline_length` only appear when the target uses `behavior == "timeline"`. Do NOT depend on the inner `data` shape from a downstream extraction step — normalize in a `custom_step` first.

### Input wiring (input_mappings vs dependencies)

- **NEW steps**: use `input_mappings` only. Leave `dependencies: []`.
- **EXISTING steps**: preserve whatever pattern is there.
- **Migration** `dependencies` → `input_mappings` on explicit request. The reverse is forbidden.

### Common patterns

#### Pattern A — new-execution trigger from an inbox / triage workflow

Upstream `prepare_documents` produces an attachment list; a decision routes the "no existing submission" branch to a `run_workflow` that fires a fresh submission run.

```json
{
  "name": "Trigger Submission",
  "type": "run_workflow",
  "input_mappings": [
    { "input_type": "static",
      "value": "<TARGET_WORKFLOW_UUID>",
      "input_parameter_name": "target_workflow_id" },
    { "input_type": "dependency",
      "value": { "dependency_step_outputs": [{"step_name": "Read Data", "output_attribute": "documents"}], "resolution_operator": null, "resolution_config": null },
      "input_parameter_name": "documents" },
    { "input_type": "dependency",
      "value": { "dependency_step_outputs": [{"step_name": "Read Data", "output_attribute": "eml_user_document_id"}], "resolution_operator": null, "resolution_config": null },
      "input_parameter_name": "eml_user_document_id" }
  ],
  "dependencies": [],
  "config": {},
  "parent_conditions": []
}
```

#### Pattern B — merge documents into an existing execution

The decision branch resolved an existing submission's `execution_id`; this step appends the newly-received attachments rather than starting a parallel run.

```json
{
  "name": "Merge to Existing Submission",
  "type": "run_workflow",
  "input_mappings": [
    { "input_type": "static",
      "value": "<TARGET_WORKFLOW_UUID>",
      "input_parameter_name": "target_workflow_id" },
    { "input_type": "dependency",
      "value": { "dependency_step_outputs": [{"step_name": "Read Data", "output_attribute": "documents"}], "resolution_operator": null, "resolution_config": null },
      "input_parameter_name": "documents" },
    { "input_type": "dependency",
      "value": { "dependency_step_outputs": [{"step_name": "Identify Submission", "output_attribute": "execution_id"}], "resolution_operator": null, "resolution_config": null },
      "input_parameter_name": "execution_id" },
    { "input_type": "dependency",
      "value": { "dependency_step_outputs": [{"step_name": "Read Data", "output_attribute": "eml_user_document_id"}], "resolution_operator": null, "resolution_config": null },
      "input_parameter_name": "eml_user_document_id" }
  ],
  "dependencies": [],
  "config": {},
  "parent_conditions": []
}
```

#### Pattern C — same target, two branches, same step shape

A common inbox shape uses the *same* `target_workflow_id` from both the "new" and "merge" branches; only the presence of `execution_id` differs. Author this as **two separate `run_workflow` steps** under different decision branches — the runtime infers the mode per-step. Do not try to collapse it into one step with a conditional `execution_id` — a `null` `execution_id` wire-up is treated as "absent" only when the input mapping itself is missing.

### Common validation errors and fixes

- **`target_workflow_id is required for RUN_WORKFLOW step`** (runtime) — the mapping was missing or resolved to an empty string. Add a `static` mapping with the target UUID, or confirm the upstream dependency outputs a non-empty string.
- **`Missing required input mappings: ['target_workflow_id', 'documents']`** — both are required at save time. Wire both.
- **`Target execution <id> not found`** (runtime) — `modify_documents` mode received an `execution_id` that doesn't exist (typo in a static mapping, or a stale upstream ID). Confirm the upstream resolves to a current execution id, not a workflow UUID.
- **`Workflow <id> not found`** (runtime, `modify_documents` mode) — the target execution exists but the workflow lookup failed. Almost always a cross-org target; see the gotcha below.
- **`No valid document IDs found in documents input`** — the docs list resolved empty (upstream filtered everything out, or `prepare_documents` produced no attachments). Gate this step behind a decision with `is_not_empty` on the docs list if triggering on empty is undesirable.
- **`execute_workflow_internal failed: …`** — the child workflow's *startup* failed (missing manual-input wiring on the target, target workflow disabled, etc.). Debug against the target workflow, not this step.

### Common gotchas

- **Cross-org targets are silently broken.** The child lookup only matches workflows in the **same org** as the source (or shared templates with empty `owner_oid`, which only platform admins can author). A `target_workflow_id` belonging to a different org 404s at runtime even though save-time validation passes. Check the target workflow's org matches the source's.
- **Fire-and-forget by default.** `new_execution` mode returns once the child is *scheduled*. Do NOT wire downstream steps expecting the child's *outputs* — there is no completion signal. If you need the child's results, redesign.
- **Modify-documents mode can OVERWRITE.** Behavior is determined by the target's `incremental_config.add_documents.behavior`. If it's `overwrite`, appending one new doc restarts the whole target execution from scratch. Verify the target's incremental config before wiring this.
- **The no-op short-circuit is silent.** When every new doc is already present, nothing triggers. Downstream steps that branch on "did we actually fire?" read `data.message` (or compare `data.workflow_execution_id` to the input `execution_id` — equal in the no-op case).
- **`eml_user_document_id` is asymmetric.** Forgetting it when the upstream is a `prepare_documents` email step means the child processes attachments but not the email body — usually wrong. Always wire it when the upstream is a `prepare_documents` step.
- **`user_inputs` only works for steps that accept pre-fills.** The target step must be a `manual_input` (or another step that reads pre-filled user inputs). Wiring `user_inputs` for a non-pre-fillable step name is silently ignored.
- **`enable_rerun: false` is the sensible default** — a re-run from this step re-triggers the child execution, which is almost always not what you want.
- **`display_step: false` is the common default.** This step is bookkeeping; surface it only when the user genuinely needs to see "we triggered X".

### See also

- `decision` — the canonical upstream for branching between `new_execution` and `modify_documents` modes (Pattern A vs B). Route through a decision; don't conditionally absent an input.
- `prepare_documents` — the usual upstream for the `documents` and `eml_user_document_id` wires.
- `custom_step` — for resolving an existing `execution_id` from an upstream query before this step runs.
- `execution_matching` — first-class step for finding prior executions of the same account/deal; its output commonly feeds the `execution_id` decision.
- `manual_input` — the typical target of `user_inputs` pre-fills.
