# extract_mixed_schema_from_multiple_sources

### What this step does

Mixed-schema extraction:

1. Receives a list of `documents` (classified or all-uploaded files) and/or parsed `emails`.
2. For each source, runs Reducto extraction (or direct LLM extraction when `llm_extraction_model` is set) against `extraction_schema` — a single JSON Schema object that mixes **object-shaped fields** (`document_info`) AND **array-shaped fields** (`layers`, `claims`, etc.) at the same top level.
3. Returns one logical record per source document, with all array rows concatenated across documents into a single grid view. The object fields are duplicated onto each row so the UI can render `document_info.*` columns alongside per-row columns.
4. Stores the result externally in TableV1; the runtime `step.output` carries a `table_v1_log_id` the UI hydrates into the "View Output" grid.

This is the **most flexible** extraction variant — the only one that captures both header and rows in one shot. Use it when document-level info and array rows are tightly coupled (a quote's `insured_name` + its `layers`; a policy's `policy_number` + its `endorsements`). Runtime output: `{ "schema": <mixed JSON Schema>, "data": {"document_info": {...}, "layers": [...]}, "user_documents": [...], "metadata": {...} }`.

Usage is **rare** — most workflows split header and rows into two sibling steps. Reach for this one only when colocation matters (citations align to the same source document; the UI grid joins header columns to row columns).

### Required inputs

| Param                  | Type     | Required | Source                                                                                                                |
| ---------------------- | -------- | -------- | --------------------------------------------------------------------------------------------------------------------- |
| `documents`            | `array`  | `documents` or `emails` (save-time rule) | `input_mapping` — typically `Classify Documents.documents.<ClassName>` (filtered list), or `Prepare Documents.documents`. |
| `emails`               | `array`  | (see above) | `input_mapping` — grouped per-email bundles from `Prepare Documents.emails`.                                        |
| `system_prompt`        | string   | no       | `input_mapping`, almost always `input_type: "static"`. Pass via `input_mappings` for NEW steps — `config.system_prompt` is the legacy slot. |

`extraction_schema` is config-provided (`step.config.extraction_schema`, NOT `input_mappings`). The bundled validator rejects the step when neither `documents` nor `emails` is wired.

### Config keys

From `ExtractMixedSchemaFromMultipleSourcesConfig`:

| Key                       | Type   | Default            | Notes                                                                                                                                                                                |
| ------------------------- | ------ | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `extraction_schema`       | object | (required)         | Top-level `type: "object"` whose `properties` contain BOTH object-shaped sub-schemas (e.g. `document_info`) AND array-shaped sub-schemas (e.g. `layers`, `claims`). See shapes below. |
| `system_prompt`           | string | `null`             | Pass via `input_mappings` (`input_type: "static"`) for new steps. Executor reads the kwarg first; `config.system_prompt` is the legacy fallback.                                     |
| `model`                   | enum   | `DEFAULT_AI_MODEL` | `gpt-5.1` for typical schemas; `gpt-5.5` for harder ones.                                                                                                                             |
| `reasoning_effort`        | enum   | `null`             | Reasoning models only; must be `null` otherwise (validated at save).                                                                                                                  |
| `verbosity`               | enum   | `null`             | GPT-5 only: `"low" \| "medium" \| "high"`. Same validator gate.                                                                                                                       |
| `llm_extraction_model`    | enum?  | `null`             | `null` = use Reducto. Set to a model name to bypass Reducto with direct LLM extraction. Citation compatibility is validated.                                                          |
| `spreadsheet_agent`       | bool   | `false`            | Set `true` to use Reducto's spreadsheet agent for `.xlsx` / `.xlsm` source files.                                                                                                     |
| `generate_citations`      | bool   | `true`             | When `true`, each cell value is wrapped `{value, citations}`. Required for the UI citation chips.                                                                                     |
| `show_confidence_score`   | bool   | `true`             | UI-only — toggles confidence chip visibility per cell.                                                                                                                                |
| `exclude_fields`          | array? | `null`             | Field names to hide from the output table. Common pattern: `["notes"]` to keep an LLM-only reasoning column out of the grid.                                                          |
| `use_fastest_extraction`  | bool   | `false`            | Fast-path Reducto mode. Cheaper, less accurate.                                                                                                                                       |
| `use_expensive_extraction`| bool   | `false`            | Quality-path Reducto mode. Use for dense, multi-section documents.                                                                                                                    |
| `add_doc_index`           | bool   | `false`            | When `true`, adds a `_doc_index` integer column on every row indicating the 0-indexed source document. Strongly recommended whenever multiple input documents feed this step.          |

#### extraction_schema shape (the load-bearing piece)

Top-level `type: "object"` whose `properties` mix one or more object sub-schemas (document-level) and one or more array sub-schemas (rows). Skeleton:

```json
{
  "type": "object",
  "properties": {
    "document_info": {"type": "object",
                      "properties": {"<header_1>": {"type": "string"}, "<header_2>": {"type": "string"}},
                      "required": ["<header_1>", "<header_2>"]},
    "<rows_field>":  {"type": "array",
                       "items": {"type": "object",
                                 "properties": {"<col_1>": {"type": "string"}, "<col_2>": {"type": "string"}},
                                 "required": ["<col_1>", "<col_2>"]}}
  },
  "required": ["document_info", "<rows_field>"]
}
```

Both sections must have `properties` (not bare primitives). Enum-style fields use the platform convention `{"type": "enum", "options": [{"id": "0", "name": "...", "value": "..."}, ...]}`, NOT raw JSON Schema `enum`.

### Output schema

This step outputs a **TABLE**, not a key-value object:

```json
{
  "type": "object",
  "description": "TableV1 structure with mixed schema (object fields + array field)",
  "properties": {
    "schema": { "type": "object", "description": "JSON Schema with mixed structure" },
    "data":   { "type": "object", "description": "Extracted data with object and array fields" },
    "user_documents": { "type": "array" },
    "metadata": { "type": "object" }
  }
}
```

Note `data` is an `object` here (not an `array` as in `extract_rows_from_multiple_sources`) — the canonical envelope is `{"document_info": {...}, "layers": [...]}` per logical record. The UI grid flattens this by duplicating `document_info.*` cells onto every row.

Downstream wiring choices for `output_attribute`:

| `output_attribute`                       | Returns                                                                                          |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `null`                                   | Full step output dict.                                                                            |
| `"data.document_info.<field>"`           | A single document-level cell (with `{value, citations}` wrapper when citations are on).           |
| `"data.<rows_field>"`                    | The array of row dicts.                                                                          |
| `"data.<rows_field>.0.<col>.value"`      | A specific row + cell.                                                                            |
| `"schema"`                               | The resolved mixed schema (after enum-id generation).                                             |

Leave step-level `output_type`, `output_config`, and `output_schema` null — auto-filled (see `../reference/output_types.md`).

The actual extracted data is **stored externally** in TableV1. Downstream `input_mappings` hydrate the table automatically — do NOT read `step.output.data` from an inline-code step.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only (with `dependencies: []`).
- EXISTING steps: preserve whatever pattern is already there. Don't churn working steps just to "modernize" them.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

Existing instances include both patterns (one uses `dependencies: [{"step_name": "Prepare Documents", "field_selector": null}]` with `input_mappings: []`; another uses pure `input_mappings`). Preserve whichever shape is already on the step.

### Common patterns (rarely used step type)

#### Pattern 1 — Tower extraction: document_info + layers (per-quote)

The canonical use case. One insurance quote per document, with document-level identifying fields plus an array of quoted layers. `add_doc_index: true` lets a downstream step attribute each layer row back to its source document.

```json
{
  "name": "Extract Tower Data",
  "type": "extract_mixed_schema_from_multiple_sources",
  "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Prepare Documents", "output_attribute": "documents"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "documents"},
    {"input_type": "static",
     "value": "You are an expert at analyzing insurance quote documents...",
     "input_parameter_name": "system_prompt"}
  ],
  "config": {
    "extraction_schema": {
      "type": "object",
      "properties": {
        "document_info": {"type": "object",
                          "properties": {"insured_name": {"type": "string", "title": "Insured Name"}, "insurer_organization": {"type": "string", "title": "Insurer Organization"},
                                         "quote_reference": {"type": "string", "title": "Quote Reference"}, "policy_start_date": {"type": "string", "title": "Policy Start Date"},
                                         "lead_or_follow": {"type": "enum", "title": "Lead or Follow",
                                                             "options": [{"id": "0", "name": "lead", "value": "lead"},
                                                                         {"id": "1", "name": "follow", "value": "follow"},
                                                                         {"id": "2", "name": "unknown", "value": "unknown"}]}},
                          "required": ["insured_name", "insurer_organization", "quote_reference", "policy_start_date", "lead_or_follow"]},
        "layers": {"type": "array",
                    "items": {"type": "object",
                              "properties": {"full_layer_limit": {"type": "string", "title": "Full Layer Limit"}, "carrier_aggregate_limit": {"type": "string", "title": "Carrier Aggregate Limit"},
                                             "attach_point": {"type": "string", "title": "Attach Point"}, "gross_premium": {"type": "string", "title": "Gross Premium"}},
                              "required": ["full_layer_limit", "carrier_aggregate_limit", "attach_point", "gross_premium"]}}
      },
      "required": ["document_info", "layers"]
    },
    "model": "gpt-5.1",
    "generate_citations": true,
    "exclude_fields": ["notes"],
    "use_fastest_extraction": true,
    "add_doc_index": true
  },
  "output_schema": null,
  "incremental_config": {"behavior": "append"}
}
```

#### Pattern 2 — Coverage layers from classified ACORDs

When a `Classify Documents` step has already bucketed input documents, wire `documents` from a class bucket. Pairs naturally with `spreadsheet_agent: true` if the source docs include `.xlsx` schedules.

```json
{
  "name": "Extract Coverage Layers",
  "type": "extract_mixed_schema_from_multiple_sources",
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Document Classification", "output_attribute": "documents.ACORD"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "documents"},
    {"input_type": "static",
     "value": "You are an expert insurance underwriter. Extract the document-level information and all coverage layers.",
     "input_parameter_name": "system_prompt"}
  ],
  "config": {
    "extraction_schema": {
      "type": "object",
      "properties": {
        "document_info": {"type": "object",
                          "properties": {"insured_name": {"type": "string", "title": "Insured Name"}, "policy_period": {"type": "string", "title": "Policy Period"}},
                          "required": ["insured_name", "policy_period"]},
        "layers": {"type": "array",
                    "items": {"type": "object",
                              "properties": {"line_of_business": {"type": "string", "title": "Line of Business"}, "limit": {"type": "string", "title": "Limit"},
                                             "deductible": {"type": "string", "title": "Deductible"}, "premium": {"type": "string", "title": "Premium"}},
                              "required": ["line_of_business", "limit"]}}
      },
      "required": ["document_info", "layers"]
    },
    "model": "gpt-5.1",
    "generate_citations": true,
    "spreadsheet_agent": true
  },
  "output_schema": null
}
```

### Common validation errors and fixes

- **`extraction_schema.type != "object"`** → mixed extraction requires a top-level object schema. Pure rows → `extract_rows_from_multiple_sources`. Pure key-value → `extract_from_multiple_sources`.
- **All children under `properties` are objects (no array)** → you're not using the mixed variant; switch to `extract_from_multiple_sources`.
- **All children are arrays (no object)** → switch to `extract_rows_from_multiple_sources`. There's no payoff to mixed without a document-level section.
- **`must map 'documents' or 'emails'`** → wire from `Classify Documents.documents.<ClassName>` (filtered), `Prepare Documents.documents`, `Prepare Documents.emails`, or a `concat_lists` over several class buckets.
- **`documents` wired from bare `Classify Documents.documents`** → rejected: `Parameter 'documents' expects type 'array' but step 'Classify Documents' outputs type 'object'`. Descend with `documents.<ClassName>` or combine with `concat_lists`.
- **`system_prompt` in `config` instead of `input_mappings`** → flagged by the bundled validator. Executor falls back to `config.system_prompt` only for legacy workflows; new steps use `input_mappings`.
- **`reasoning_effort` / `verbosity` set on a non-reasoning model** → rejected. Move to a reasoning model or clear both fields to `null`.
- **`generate_citations: true` while `llm_extraction_model` is set** → citation-compatibility validation fails. Re-enable Reducto or set `generate_citations: false`.
- **Raw JSON Schema `enum` in a property** → use `"type": "enum"` with `"options": [{"id": "...", "name": "...", "value": "..."}, ...]`. Ids are auto-filled when only `name`/`value` are present.
- **Two array siblings at the top level** → technically allowed but the UI grid joins each array independently to `document_info.*` and the result is confusing. Prefer two sibling steps unless the arrays are genuinely parallel and same-length.

### Common gotchas

- **When to pick mixed vs. its siblings.** Pure key-value (one record, no list) → `extract_from_multiple_sources`. Pure tabular grid (rows, no header) → `extract_rows_from_multiple_sources`. Header + rows colocated with aligned citations / source attribution → THIS step.
- **The grid flattens by duplicating `document_info.*` onto every row.** A document with 5 layers produces 5 rows, each carrying the same `document_info.*` cells — by design.
- **`add_doc_index: true` is strongly recommended** whenever multiple input documents feed the step. Without it, downstream consumers cannot tell which document a row came from after concatenation. `_doc_index` is 0-indexed.
- **Cell values are wrapped with `{value, citations}` everywhere** — including under `document_info.*`. Inline-code steps must unwrap: `val["value"] if isinstance(val, dict) and "value" in val else val`.
- **Citations shape:** `citations["<rows_field>"][row_index]["<col>"]` for per-row cells; `citations["document_info"]["<field>"]` for document-level fields.
- **`exclude_fields` operates on the flattened cell namespace.** To hide both `document_info.notes` AND `layers.notes`, you may need both names — verify in the UI after a test run.
- **`use_fastest_extraction` / `use_expensive_extraction` are mutually exclusive in intent** — never enable both. Default (both `false`) is the balanced Reducto path.
- **The two-step alternative is usually cleaner.** If you don't need citation alignment or coupled source attribution, splitting into `extract_from_multiple_sources` (header) + `extract_rows_from_multiple_sources` (rows) is more maintainable.

### See also

- **`extract_from_multiple_sources`** — sibling for key-value (one record per run) extraction.
- **`extract_rows_from_multiple_sources`** — sibling for pure tabular extraction (rows only).
- **`classify_documents`** — almost always the upstream step that feeds `documents` here via `documents.<ClassName>`.
- **`prepare_documents`** — the alternative upstream when documents aren't classified into buckets.
- **`custom_step`** — typical downstream consumer; reads `output_attribute: "data.<rows_field>"` to get the row array and unwraps the cell values for aggregation, normalization, or tower construction.
