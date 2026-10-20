# Workflow JSON Structure

The top-level workflow shape and the per-step envelope every step shares. For per-step `config` details, see `step_types/<type>.md`. For workflow-level `options` and `columns`, see `options.md`.

## Top-level shape (`WorkflowContentMixin`)

```jsonc
{
  "name": "string (required)",
  "description": "string | null",
  "welcome_message": "string | null",
  "comments": "string | null",
  "steps": [ /* WorkflowStep — see envelope below */ ],
  "options": { /* WorkflowOptionsV1 — see options.md */ },
  "columns": [ /* WorkflowColumnV1 — see options.md */ ]
}
```

Published `WorkflowV1` documents additionally carry `workflow_id`, `org_id`, `created_at`, `updated_at`, version metadata, etc. — those are database-owned, not authored by hand.

## `WorkflowStep` envelope (every step has the same shape)

```jsonc
{
  "name": "string",                        // unique within workflow, case-sensitive
  "type": "<StepType enum value>",         // see step_types/INDEX.md; the bundled validator carries the authoritative list
  "input_mappings": [ /* see input_mappings.md */ ],
  "dependencies": [],                      // ALWAYS [] — legacy, do not use
  "config": { /* type-specific — see step_types/<type>.md */ },

  "display_step": true,                    // hide from UI if false
  "start_title": "string | WorkflowField", // shown while running
  "end_title":   "string | WorkflowField", // shown on completion
  "override_step_type": null,              // niche UI hint; rarely set
  "run_type": "single_call",               // integration connectors only: "single_call" | "loop"
  "loop_membership": [],                   // set only on members of a loop step (reciprocal with the loop's config.member_steps)

  "output_type":   "<OutputType | null>",  // see output_types.md (auto-defaulted by step type)
  "output_config": { /* see output_types.md */ },
  "output_schema": { /* JSON Schema with glom_path annotations */ },

  "incremental_config": { "behavior": "rerun" },   // see validation_errors.md for rules
  "enable_rerun": false,                   // UI: show "rerun this branch" affordance
  "parent_conditions": [],                 // see step_types/decision.md for branching

  "hide_output_button": false,
  "view_output_button_text": null
}
```

## Field semantics

| Field | Effect |
|---|---|
| `name` | Used everywhere as the step identifier. Must be unique. Case-sensitive. |
| `type` | Selects which `*Config` pydantic class validates `config`. |
| `input_mappings` | Edges of the execution DAG. Empty list = root. See `input_mappings.md`. |
| `dependencies` | LEGACY. Always `[]` for new work. |
| `config` | Type-specific. See the matching `step_types/<type>.md`. |
| `display_step` | Hide step from UI but keep in execution graph. |
| `start_title` / `end_title` | Either a static string, or a `WorkflowField` for dynamic titles (e.g., `{input_type: "variable", value: "insured_name"}`). |
| `output_type` | Drives UI renderer + which `output_config` class applies. Auto-defaulted per step type if `null`. |
| `output_config` | Container-specific display options. Class must match `output_type`. |
| `output_schema` | JSON Schema with `glom_path` keys describing where each value lives in the step result. Auto-generated for many step types. |
| `incremental_config.behavior` | `rerun` (default) / `no_op` / `append` / `preserve_edits`. Per-step-type rules — see `validation_errors.md`. |
| `enable_rerun` | When true, the step acts as a rerun trigger for descendants. |
| `parent_conditions` | Gates the step on a decision step's branch selection. List of `{decision_step, branch}`. |
| `hide_output_button` | Force-hide the output view button. |
| `view_output_button_text` | Override the output button text (defaults vary by output type). Lives at the step top level, NOT inside `output_config`. |
| `run_type` | Integration connectors only: `"single_call"` (default) or `"loop"` (per-row enrichment with `config.table_to_enrich`). Never set `"loop"` on non-connector step types. See `step_types/integrations.md`. |
| `loop_membership` | Names of the `loop` step this step belongs to (at most one). Must mirror the loop's `config.member_steps`. See `step_types/loop.md`. |

## Schema field titles — set a human-readable `title` on every property

Any property you author in a schema — `extraction_schema`, `comparison_schema`, `multi_column_qa` columns, a function/custom_step `input_schema`/`output_schema`, and every nested object and array-item field — should carry a `title`. The key is a machine name (`broker_name`, `tiv`); the `title` is what the operator sees as the column header / field label. The backend fallback title-cases the key, which mangles insurance acronyms (`tiv` → "Tiv", `naics_code` → "Naics Code", `fein` → "Fein"), and some render paths fall back to a generic "Data"/"Field". Use Title Case with spaces, drop trailing `?`, keep acronyms uppercase (TIV, NAICS, FEIN, SIR, ACORD, GL, WC):

```json
"properties": {
  "broker_name":         {"type": "string", "title": "Broker Name",               "description": "Producing broker / agency name."},
  "total_insured_value": {"type": "number", "title": "Total Insured Value (TIV)", "description": "Sum of building + contents + BI values across all locations."},
  "naics_code":          {"type": "string", "title": "NAICS Code",                "description": "6-digit primary NAICS."}
}
```

## Default `output_type` by step type

When `output_type` is omitted, the system auto-fills based on step type — the full map, the three authoring patterns, and the `OUTPUT_TYPE_MISMATCH`/`DISPLAY_TYPE_MISMATCH` save-time rules live in `output_types.md`. Short version: leave `output_type`/`output_config`/`output_schema` null on every step type except `function`/`custom_step` (author-controlled) and `submission_summary_generator` (pick `simple`/`chat_message`/`html`).

## Auto display_type for table output

When `output_type: "table"` and no `output_config.display_type` is provided:
- **GRID**: `extract_rows_from_multiple_sources`, `extract_mixed_schema_from_multiple_sources`, `sov_mapping`, `multi_column_qa`, `ofac_agent`, `osha_agent`, `extract_guidelines`, `check_guidelines`, `agentic_extraction` (when `extract_per_document=true`)
- **KEY_VALUE**: every other table-producing step
- **COMPARE**: `compare_document_data`

## Validation summary

Cross-cutting structural rules enforced at parse time:

- `name` unique within workflow.
- `type` in the `StepType` enum.
- `config` validates against the matching `*Config` pydantic model.
- `output_config` class matches `output_type`.
- DAG has no cycles.
- `input_mappings` semantics validated (see `input_mappings.md`).
- `incremental_config.behavior` valid for the step type (see `validation_errors.md`).

For the full validator catalog: `validation_errors.md`.
