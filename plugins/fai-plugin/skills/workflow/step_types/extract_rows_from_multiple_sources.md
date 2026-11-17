# extract_rows_from_multiple_sources

### What this step does

Tabular/list extraction:

1. Receives a list of `documents` (already-classified or all-uploaded files) and/or parsed `emails`.
2. For each source, runs Reducto extraction (or direct LLM extraction when `llm_extraction_model` is set) against the `extraction_schema` — which MUST be a top-level JSON Schema array.
3. Concatenates the per-document row arrays into one flat grid table.
4. Stores the result externally in TableV1; the runtime `step.output` carries a `table_v1_log_id` handle the UI hydrates into the "View Output" grid.

The returned output dict at runtime:

```json
{ "schema": <items.properties JSON Schema>, "data": [<row>, <row>, ...], "user_documents": [...], "metadata": {...} }
```

Each row's cell values are wrapped `{"value": <scalar>, "citations": [...]}` when `generate_citations: true` — downstream inline-code steps unwrap with `val["value"]` if `isinstance(val, dict) and "value" in val`.

### Required inputs

| Param               | Type     | Required | Source                                                                                                                                      |
| ------------------- | -------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `documents`         | `array`  | `documents` or `emails` (save-time rule) | `input_mapping` — typically `Classify Documents.documents.<ClassName>` (filtered list), or a `concat_lists` over several class buckets. |
| `emails`            | `array`  | (see above) | `input_mapping` — grouped per-email bundles from `Prepare Documents.emails`.                                                            |
| `system_prompt`     | string   | no       | `input_mapping`, almost always `input_type: "static"`. Pass via `input_mappings` for NEW steps — `config.system_prompt` is the legacy slot. |

`extraction_schema` is config-provided (`step.config.extraction_schema`, NOT `input_mappings`). Optional mapped controls also exist (`generate_citations`, `spreadsheet_agent`, `use_fastest_extraction`, `use_expensive_extraction`, `llm_extraction_model`, `deep_extract`, `add_source_document_name`, `add_source_document_name_to_prompt`, `reducto_version`) but these are usually set in `config`.

### Config keys

From `ExtractRowsFromMultipleSourcesConfig`:

| Key                       | Type                  | Default          | Notes                                                                                                                                                                                       |
| ------------------------- | --------------------- | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `extraction_schema`       | object                | (required)       | JSON Schema **MUST be `type: "array"`** with `items.type: "object"` and `items.properties`. The row shape is defined under `items.properties`. See "Common validation errors" below.        |
| `system_prompt`           | string                | `null`           | Pass via `input_mappings` (`input_type: "static"`) for new steps. Executor reads the kwarg first; `config.system_prompt` is a legacy fallback.                                              |
| `model`                   | enum                  | `DEFAULT_AI_MODEL` | `gpt-5.1` for typical row schemas; `gpt-5.5` for harder ones.                                                                                                                              |
| `reasoning_effort`        | enum                  | `null`           | Reasoning models only; must be `null` otherwise (validated at save).                                                                                                                         |
| `verbosity`               | enum                  | `null`           | GPT-5 only: `"low" \| "medium" \| "high"`. Same validator gate.                                                                                                                             |
| `llm_extraction_model`    | enum?                 | `null`           | `null` = use Reducto. Set to a model name to bypass Reducto with direct LLM extraction. Citation compatibility is validated. Gemini 2.5 Flash/Pro are also offered here.                     |
| `spreadsheet_agent`       | bool                  | `false`          | Set `true` to use Reducto's spreadsheet agent for `.xlsx`/`.xlsm` source files. Strongly recommended when rows live in spreadsheets (driver schedules, property SOVs).                       |
| `generate_citations`      | bool                  | `true`           | When `true`, each cell value is wrapped `{value, citations}`. Required for the UI citation chips.                                                                                             |
| `show_confidence_score`   | bool                  | `true`           | UI-only — toggles confidence chip visibility per cell.                                                                                                                                        |
| `use_fastest_extraction`  | bool                  | `false`          | Fast-path Reducto mode. Cheaper, less accurate.                                                                                                                                               |
| `use_expensive_extraction`| bool                  | `false`          | Quality-path Reducto mode. Use for complex documents (e.g. cyber towers).                                                                                                                     |
| `deep_extract`            | bool                  | `false`          | Reducto v3 deep extract. **Significantly more expensive than v2** — only enable for loss runs and similarly dense tabular sources. **Requires `reducto_version: "v3"` at the step level.**   |
| `reducto_version`         | `"v2" \| "v3" \| null` | `null`          | Override workflow default. Set to `"v3"` whenever `deep_extract: true`.                                                                                                                       |
| `add_source_document_name`| bool                  | `false`          | Stamps each row with a `_source_document_name` field holding the source file's name (extraction is per-document, so the source is known per row).                                             |
| `add_source_document_name_to_prompt` | bool       | `false`          | Appends each document's filename to that document's extraction system prompt so the model can use filename hints (e.g. a line of business encoded in the name). Prompt rules referencing "the source filename" do nothing without this. Email sources unaffected. |

### Output schema

This step outputs a **TABLE**, not a key-value object:

```json
{
  "type": "object",
  "description": "TableV1 structure with array data",
  "properties": {
    "schema": { "type": "object", "description": "JSON Schema of extracted rows" },
    "data":   { "type": "array",  "description": "Array of extracted rows" },
    "user_documents": { "type": "array" },
    "metadata": { "type": "object" }
  }
}
```

The derived output schema walks `extraction_schema.items.properties` to produce a row schema where each cell is wrapped as `cell` (carries `value` + `citations`).

Downstream wiring choices for `output_attribute`:

| `output_attribute`             | Returns                                                                                          |
| ------------------------------ | ------------------------------------------------------------------------------------------------ |
| `null`                         | Full step output dict.                                                                            |
| `"data"`                       | Array of row dicts — what downstream `custom_step` / `agentic_*` steps usually want.             |
| `"data.0.<col>.value"`         | Index a single row + cell. Use only when the table is guaranteed single-row.                     |
| `"schema"`                     | The resolved item schema (after enum-id generation), used for typed downstream propagation.       |

Leave step-level `output_type`, `output_config`, and `output_schema` null — auto-filled (see `../reference/output_types.md`).

The actual extracted rows are **stored externally** in TableV1. Downstream `input_mappings` hydrate the table automatically — do NOT read `step.output.data` from an inline-code step.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only (with `dependencies: []`).
- EXISTING steps: preserve whatever pattern is already there. Don't churn working steps just to "modernize" them.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

Both patterns coexist in the wild — an older "Extract Claims" step may wire its source via `dependencies: [{"step_name": "Classify Documents", "field_selector": "Loss Run Report"}]`, while newer steps use only `input_mappings`. Preserve whichever shape is already there.

### Common patterns

#### Pattern 1 — Claims rows from loss runs (one row per claim)

Most common shape. One row per claim, all policy-level fields denormalized onto every row. Classifier feeds a filtered document list.

```json
{
  "type": "extract_rows_from_multiple_sources",
  "name": "Extract Claims",
  "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Classify Documents", "output_attribute": "documents.Loss_Run"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "documents"},
    {"input_type": "static",
     "value": "You are an expert insurance data extraction specialist. Extract one row per claim from the loss run...",
     "input_parameter_name": "system_prompt"}
  ],
  "config": {
    "extraction_schema": {
      "type": "array",
      "description": "List of all claims in the loss run document. Each claim includes both policy-level and claim-level information flattened into a single record.",
      "items": {
        "type": "object",
        "properties": {
          "insurer_organization": {"type": "string", "title": "Insurer Organization", "description": "Insurance organization/company name..."},
          "policy_number":        {"type": "string", "title": "Policy Number", "description": "Insurance policy number..."},
          "claim_number":         {"type": "string", "title": "Claim Number", "description": "Claim identifier..."},
          "claim_incident_date":  {"type": "string", "title": "Claim Incident Date", "description": "Incident date in MM/DD/YYYY format..."},
          "indemnity_paid":       {"type": "string", "title": "Indemnity Paid", "description": "Indemnity paid as '$1,234,567'..."}
        }
      }
    },
    "model": "gpt-5.1",
    "generate_citations": true,
    "show_confidence_score": true,
    "use_expensive_extraction": false,
    "deep_extract": false
  },
  "output_schema": null
}
```

#### Pattern 2 — Loss-run policies with v3 deep extract (NESTED rows allowed)

Used when each "row" is actually a policy that owns a nested `claims` array. Schema is still top-level array; the nested array sits under one of the row's properties. **`deep_extract: true` + `reducto_version: "v3"` are paired** — v3 is significantly more expensive than v2, only enable for loss runs. See `../reference/patterns/loss_runs.md` for the full 3-step pattern.

```json
{
  "config": {
    "extraction_schema": {
      "type": "array",
      "description": "List of all insurance policies in the loss run. Each policy groups its identifying info with its claims as a nested array. Extract every policy found, even those with zero claims.",
      "items": {
        "type": "object",
        "properties": {
          "policy_number":            {"type": "string", "title": "Policy Number", "description": "Insurance policy number..."},
          "policy_effective_date":    {"type": "string", "title": "Policy Effective Date", "description": "Effective Date in MM/DD/YYYY format..."},
          "claims": {
            "type": "array",
            "title": "Claims",
            "description": "All claims under this policy. Empty array if no claims for the period.",
            "items": {"type": "object", "properties": { /* per-claim fields */ }}
          }
        }
      }
    },
    "model": "gpt-5.1",
    "deep_extract": true,
    "reducto_version": "v3"
  }
}
```

#### Pattern 3 — Multi-quote insurance tower extraction (per-layer rows)

For cyber / tech E&O / D&O tower documents wanting one row per quoted layer. Often runs on a strong reasoning model with `use_expensive_extraction: true`.

```json
{
  "config": {
    "extraction_schema": {
      "type": "array",
      "description": "One row per quoted layer in the submitted tower.",
      "items": {
        "type": "object",
        "properties": {
          "insured_name":        {"type": "string", "title": "Insured Name", "description": "Named insured..."},
          "insurer_organization":{"type": "string", "title": "Insurer Organization", "description": "Insurer / carrier name..."},
          "lead_or_follow":      {"type": "string", "title": "Lead or Follow", "description": "Allowed: Lead | Follow..."},
          "full_layer_limit":    {"type": "string", "title": "Full Layer Limit", "description": "Layer limit formatted '$1,000,000'..."},
          "attach_point":        {"type": "string", "title": "Attach Point", "description": "Attachment point — MUST resolve for excess layers..."},
          "gross_premium":       {"type": "string", "title": "Gross Premium", "description": "Gross premium..."},
          "commission_rate":     {"type": "string", "title": "Commission Rate", "description": "Commission % as written..."}
        }
      }
    },
    "model": "gpt-5.5",
    "reasoning_effort": "high",
    "verbosity": "medium",
    "use_expensive_extraction": true,
    "generate_citations": true
  }
}
```

### Common validation errors and fixes

- **`extraction_schema.type != "array"`** → wrap rows as `{type: 'array', items: {type: 'object', properties: {...}}}`. This is the #1 mistake — authors copy an object-style schema from `extract_from_multiple_sources` and forget to wrap it. The backend then silently returns the static envelope (`data: array` with no items) and downstream paths like `data.0.<col>.value` can't resolve.
- **`extraction_schema.items.type != "object"` or empty `items.properties`** → must have `items.type == 'object'` with row columns under `items.properties`. A bare `{"type": "array", "items": {"type": "string"}}` won't render a table.
- **`must map 'documents' or 'emails'`** → wire `documents` from `Classify Documents.documents.<ClassName>` (filtered), or `emails` from `Prepare Documents.emails`.
- **`documents` wired from bare `Classify Documents.documents`** → rejected: `Parameter 'documents' expects type 'array' but step 'Classify Documents' outputs type 'object'`. Descend with `documents.<ClassName>` or combine with `concat_lists`.
- **`system_prompt` in `config` instead of `input_mappings`** → flagged by the bundled validator on new steps. The executor falls back to `config.system_prompt` only for legacy workflows.
- **`reasoning_effort` / `verbosity` set on a non-reasoning model** → rejected. Move to a reasoning model or clear both fields to `null`.
- **`deep_extract: true` without `reducto_version: "v3"`** → deep extract is a v3-only feature. Set both together.
- **Raw JSON Schema `enum` in a row column** → use `"type": "enum"` with `"options": [...]` (the platform dropdown convention).

### Common gotchas

- **Output is a TABLE, not a key-value object.** Downstream steps consuming this need table-aware input mappings. Use `output_attribute: "data"` to get the row array; use `output_attribute: "data.0.<col>.value"` only when the table is guaranteed single-row.
- **Row concatenation across sources is order-dependent.** Rows from document 1 appear before rows from document 2. If the consumer needs source attribution per row, set `add_source_document_name: true`, or use `extract_mixed_schema_from_multiple_sources` with `add_doc_index: true`.
- **Cell values are wrapped with `{value, citations}`.** Inline-code steps reading the table must unwrap; the same rule applies per row when iterating.
- **Financial strings need parsing.** Values like `"$1,250,000"` come out as strings (per field description). Strip `$` and `,`, then `float(...)` before any arithmetic.
- **`spreadsheet_agent: false` on an Excel-sourced extraction loses precision.** When rows live in `.xlsx` / `.xlsm`, set `spreadsheet_agent: true`.
- **`deep_extract` ↔ `reducto_version: "v3"` is a paired toggle.** Reserve for loss runs and other dense tabular sources. The workflow-level default should stay `"v2"`; only override at the step level.
- **`use_fastest_extraction` and `use_expensive_extraction` are mutually exclusive in intent** — don't enable both. Default (both `false`) is the balanced path.
- **Nested arrays inside rows are allowed** (see Pattern 2's `claims` field), but a follow-on `custom_step` is almost always needed to flatten them before they're consumable by SOV-mapping or aggregation steps.
- **TableV1 storage:** the extracted rows are NOT in `step.output.data` at runtime — they're hydrated only via `input_mappings` resolution. Code that tries to read `step.output.data` directly gets an empty list.

### See also

- **`extract_from_multiple_sources`** — sibling for key-value (one row total) extraction.
- **`extract_mixed_schema_from_multiple_sources`** — when you need BOTH object fields AND array rows in the same step. Supports `add_doc_index: true` for per-row source attribution.
- **`sov_mapping`** — for mapping a varying-schema input table (a customer's SOV) to a fixed-column output schema. Don't use this step for that.
- **`agentic_extraction`** — agent-driven RAG extraction. Use when the rows could be anywhere in a multi-document KB. More expensive and less predictable; reserve for cases where classification can't narrow the source.
- **`classify_documents`** — almost always the upstream step that feeds `documents` here via `documents.<ClassName>`.
- **`../reference/patterns/loss_runs.md`** — the loss-runs 3-step pattern built on this step.
