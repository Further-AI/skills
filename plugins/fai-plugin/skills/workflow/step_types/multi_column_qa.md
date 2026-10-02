# multi_column_qa

### What this step does

When the executor reaches a `multi_column_qa` step it:

1. Resolves the KB handle, the optional `documents` list, the `system_prompt` static, and (optionally) a dynamic `schema_instances` from a previous step.
2. Builds a dynamic response model from `ai_schema` (each column becomes a typed field — `string`, `number`, or `enum` keyed by `enum_mapping`).
3. Fans out one LLM call per `schema_instance` row (concurrency capped by `options.concurrency_limit`, default 10, max 50). Each row's `question` is the RAG search query; `column_values` are passed in as context.
4. Stitches the answers back into a TableV1: one row per `schema_instance`, columns = `input_schema` columns + `ai_schema` columns, each AI cell carrying a confidence score and KB citations.

Output renders as a grid (`output_type: "table"`).

### Required inputs

| Param              | Type     | Required | Source                                                                                                |
| ------------------ | -------- | -------- | ------------------------------------------------------------------------------------------------------ |
| `kb`               | `object` | YES      | `input_mapping` from a `knowledge_base` step (`output_attribute: "kb"`).                              |
| `documents`        | `array`  | YES      | `input_mapping` from `prepare_documents` (or a classified bucket). Used for citations and context.    |
| `system_prompt`    | `string` | no       | `input_mapping`, almost always `input_type: "static"`. NEVER put this in `config` for new steps.      |
| `schema_instances` | `array`  | no       | Either set in `config` (static rows) OR wired from a previous step (dynamic rows). When provided via input_mapping it overrides the `config` value. |

`input_schema` and `ai_schema` are config-only — they cannot be wired through `input_mappings`.

### Config keys

From `MultiColumnQAConfig`:

| Key                          | Type      | Default                       | Notes                                                                                                                                                |
| ---------------------------- | --------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `input_schema`               | array     | (required)                    | Column header definitions for the context columns. Each is `{"name": "<header>"}`. Order is preserved in the rendered grid.                          |
| `schema_instances`           | array     | `[]`                          | Each item is `{"question": "<RAG query>", "column_values": {"<input_col_name>": "<value>"}}`. One item per row. Can be empty if wired via input.     |
| `ai_schema`                  | array     | (required)                    | AI-generated column definitions — see "AI column shape" below.                                                                                       |
| `system_prompt`              | string    | `null`                        | **Legacy fallback only.** Pass via `input_mappings`. Executor reads the kwarg first.                                                                 |
| `table_title`                | string    | `"Multi-Column Q&A Results"`  | Shown above the rendered grid.                                                                                                                       |
| `table_description`          | string    | `"Results from schema-based Q&A analysis"` | Shown under the title.                                                                                                                  |
| `answer_column_display_name` | string    | `null`                        | Override the header for the AI answer column group. Leave null in most cases.                                                                        |
| `options.concurrency_limit`  | int       | `10` (1–50)                   | Rows processed in parallel. Lower it for rate-limited models; raise it (up to 50) for large checklists.                                              |
| `show_confidence_score`      | bool      | `true`                        | UI-only flag — hides the confidence chip; the model still computes it.                                                                               |
| `enable_get_step_tool`       | bool      | `false`                       | When `true`, the row agent can call `get_step(step_name, max_rows)` to read allowlisted prior workflow-step outputs.                                 |
| `allowed_get_step_dependencies` | list[str]? | `null`                     | Explicit allowlist of prior step names for `get_step`. If omitted while enabled, the backend derives the allowlist from declared dependencies/input mappings. `[]` means the tool is enabled but can read nothing. |
| `model`                      | enum      | `DEFAULT_AI_MODEL`            | `"gpt-5.1"` for typical checklists; `"gpt-5.5"` for known-hard ones.                                                                                 |
| `reasoning_effort`           | enum      | `null`                        | Reasoning models only; must be `null` otherwise (validated at save).                                                                                  |
| `verbosity`                  | enum      | `null`                        | GPT-5 only: `"low" \| "medium" \| "high"`. Same validator gate.                                                                                      |

When `allowed_get_step_dependencies` is set, those step names become scheduler-visible dependencies through the canonical dependency graph. The allowlist is step-name based, not JSON-order based: a producer can appear later in the JSON as long as the graph is valid. Do not include the current step's own name, unknown names, duplicates, or non-string values.

#### AI column shape (`ai_schema` items)

Each entry in `ai_schema`:

- `name` — internal field name the model emits (snake_case is safest).
- `column_display_name` — header shown in the grid (optional; falls back to `name`).
- `field_type` — `"string" | "number" | "enum"` (the executor also coerces `"integer" | "float" | "boolean" | "date"`).
- `enum_mapping` — required when `field_type: "enum"`. Dict of `{stored_value: displayed_label}` (e.g. `{"Pass": "✅ Pass", "Fail": "❌ Fail"}`). Stored values are what the model emits; labels are UI-only.
- `description` — drives the model's behavior for this column. Keep all decision rules / allowed values here, not in the system prompt.
- `is_status_column` — `true` enables the colored status pill in the grid. Set on at most one column per step.

### Output schema

```json
{
  "type": "object",
  "properties": {
    "schema": { "type": "object" },
    "data": { "type": "array",  "items": { "type": "object" } },
    "user_documents": { "type": "array" },
    "metadata": { "type": "object" }
  }
}
```

`data` is one row per `schema_instance`. Each row has every `input_schema` column as a plain string value plus every `ai_schema` column wrapped as a cell `{value, confidence, citations}`.

| `output_attribute` | Returns                                                                          |
| ------------------ | ---------------------------------------------------------------------------------- |
| `null`             | Full step output dict (schema + data + user_documents + metadata + KB handle).   |
| `"data"`           | Just the row array — each AI cell is `{value, citations, confidence}`. Most common. |
| `"kb"`             | The KB handle the step used. Pass-through to another KB consumer.                |
| `"schema"`         | The resolved JSON schema (after enum resolution).                                |

Leave step-level `output_type`, `output_config`, and `output_schema` null — auto-filled (see `../reference/output_types.md`).

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is there.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

A new `multi_column_qa` step wires `kb`, `documents`, `system_prompt` (and optionally `schema_instances`) through `input_mappings`.

### Common patterns

#### Pattern 1 — Static checklist of underwriting guidelines

Fixed authoring-time row set, single `status` enum column with display labels.

```json
{
  "type": "multi_column_qa",
  "name": "Property Guideline Check",
  "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Initialize Knowledge Base", "output_attribute": "kb"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "kb"},
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Prepare Documents", "output_attribute": "documents"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "documents"},
    {"input_type": "static",
     "value": "You are a property underwriter evaluating submissions against carrier guidelines...",
     "input_parameter_name": "system_prompt"}
  ],
  "config": {
    "input_schema": [{"name": "Section"}, {"name": "Guideline"}],
    "ai_schema": [{
      "name": "status",
      "column_display_name": "Status",
      "field_type": "enum",
      "enum_mapping": {"Pass": "Pass", "Fail": "Fail", "Needs Info": "Needs Info"},
      "description": "Pass if guideline met, Fail if violated, Needs Info if insufficient data.",
      "is_status_column": true
    }],
    "schema_instances": [
      {"question": "Is the property in an eligible state per the carrier guideline list?",
       "column_values": {"Section": "Eligibility", "Guideline": "Property must be in an eligible state"}}
    ],
    "model": "gpt-5.1",
    "enable_get_step_tool": true,
    "allowed_get_step_dependencies": ["Account Information"],
    "show_confidence_score": true,
    "options": {"concurrency_limit": 10}
  },
  "output_schema": null
}
```

#### Pattern 2 — Traffic-light appetite check with dynamically generated rows

The `schema_instances` are produced by an upstream step (e.g. a `custom_step` or `agentic_extraction` that builds appetite criteria from a guideline document). The static `input_schema` describes the columns that the upstream step is expected to populate.

```json
{
  "type": "multi_column_qa",
  "name": "Appetite Check",
  "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Initialize Knowledge Base", "output_attribute": "kb"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "kb"},
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Prepare Documents", "output_attribute": "documents"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "documents"},
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Generate Appetite Schema", "output_attribute": "schema_instances"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "schema_instances"},
    {"input_type": "static",
     "value": "Keep outputs short. Return one of the three traffic-light values per row.",
     "input_parameter_name": "system_prompt"}
  ],
  "config": {
    "input_schema": [
      {"name": "Factor"},
      {"name": "Core Requirement"},
      {"name": "Non-Core Requirement"},
      {"name": "Low Appetite Requirement"}
    ],
    "ai_schema": [{
      "name": "traffic_light",
      "column_display_name": "Traffic Light",
      "field_type": "enum",
      "enum_mapping": {"Core": "Green", "Non-Core": "Yellow", "Low Appetite": "Red"},
      "description": "Green if Core requirement met, Yellow if Non-Core, Red if Low Appetite."
    }],
    "schema_instances": [],
    "model": "gpt-5.1",
    "show_confidence_score": false,
    "options": {"concurrency_limit": 30}
  },
  "output_schema": null
}
```

#### Pattern 3 — "Is this field present?" document audit

Single `input_schema` column (`Field`), single `Status` enum (`Present | Missing`). Use for one-pass audits of which standard fields exist in a submission.

```json
{
  "config": {
    "input_schema": [{"name": "Field"}],
    "ai_schema": [{
      "name": "status",
      "column_display_name": "Status",
      "field_type": "enum",
      "enum_mapping": {"Present": "Present", "Missing": "Missing"},
      "description": "Present if the field appears in the documents, Missing otherwise.",
      "is_status_column": true
    }],
    "schema_instances": [
      {"question": "Find the submitting agent or producer name on the ACORD 125 or cover email.",
       "column_values": {"Field": "Agent Name"}}
    ],
    "model": "gpt-5.1",
    "options": {"concurrency_limit": 10}
  }
}
```

### Common validation errors and fixes

- **`kb` missing from `input_mappings`** → required. Wire it from the `knowledge_base` step.
- **`documents` missing** → unlike `agentic_extraction`, `documents` is REQUIRED here (it scopes citations and is what the UI hyperlinks). Wire from `prepare_documents`.
- **`system_prompt` in `config` instead of `input_mappings`** → flagged for new steps. The executor reads `config.system_prompt` only as a legacy fallback.
- **`ai_schema` empty** → the dynamic response model has no fields and the model has nothing to return. Add at least one column.
- **`field_type: "enum"` without `enum_mapping`** → the response model can't constrain values. Always pair the two.
- **`column_values` keys don't match `input_schema` names** → the row renders with blank context columns. Each `schema_instances[i].column_values` MUST be keyed by every `input_schema[j].name`.
- **`reasoning_effort` / `verbosity` on a non-reasoning model** → rejected. Clear both fields or switch models.
- **`options.concurrency_limit` outside 1–50** → rejected.

### Common gotchas

- **APPEND blocked — fixed rows, no incremental append.** The step produces one row per `schema_instance` and that count is fixed at save time. On-rerun incremental modes are limited to `rerun`, `no_op`, and `preserve_edits`; **`append` is rejected**. New rows require editing `schema_instances` and re-saving.
- **`preserve_edits` IS allowed and uses positional merge.** Because row order is stable (one row per `schema_instances[i]`), user edits merge across reruns by row index.
- **The "question" is the RAG query, not a user-facing label.** Write it as a directive: "Is the property in an eligible state?" — not "Eligible state?". The `input_schema` columns are what the user sees as context.
- **Column descriptions outrank the system prompt.** If the system prompt says "Pass/Fail only" and the column `enum_mapping` includes `"Needs Info"`, the model will use all three. Keep decision rules in the column description / enum mapping.
- **`enum_mapping` stored vs displayed.** The key (stored) is what the model emits and what downstream steps see; the value is the UI label only. Match on the stored key in downstream logic.
- **Citations require `documents`.** Without a wired `documents` input, the grid renders without source-file hyperlinks on citation chips. Always wire `documents` even though the model searches the full KB.
- **get_step source handles are row provenance, not user columns.** When `enable_get_step_tool` is true, used source handles come back in an internal `source_handles` response field. Do not add a user-defined `ai_schema` column named `source_handles`.
- **get_step allowlists are graph inputs.** Explicit `allowed_get_step_dependencies` entries make those producers scheduler-visible so parallel execution waits for them.
- **`schema_instances` resolution precedence.** If present in BOTH `config` and `input_mappings`, the input_mapping wins. Leave `config.schema_instances: []` whenever wiring dynamically.
- **TableV1 storage.** Row data is stored externally, not in `step.output` at runtime. Downstream consumers wire through `input_mappings` with `output_attribute: "data"`.
- **One status column only.** `is_status_column: true` on more than one entry renders, but the colored pill behavior is undefined.

### See also

- **`agentic_guideline_check`** — agentic sibling. Same grid output, but each row is processed by a full RAG agent (browse tools, richer findings, slower). Switch to it when single-call Q&A keeps missing context across documents.
- **`generate_qa_table_from_agent`** — for a flat list of titled questions answered by an agent. Produces a KV-style answer table rather than a multi-column grid.
- **`agentic_extraction`** — schema-rigid extraction (key-value or array), not row-per-question. Use when you want a typed object out, not a Q&A grid.
