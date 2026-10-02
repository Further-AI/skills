# sov_mapping

### What this step does

SOV mapping normalizes varying spreadsheet layouts to a fixed column set:

1. Receives a list of `documents` (Excel files, typically filtered by `classify_documents`).
2. Reads each sheet (honoring `include_hidden_sheets`), detects header and last-data-row with `header_and_last_row_detection_model` using `last_row_detection_mode` (default `sliding_window`).
3. Optionally runs `raw_data_post_processing` (a function config) to clean raw sheet data before mapping.
4. Uses `column_detection_model` to match each configured `sov_field` to the best source column(s) — based on each field's `description`.
5. For each row, computes the output cell value via either `validator_func` (predefined Python validator) or `validator` (natural-language prompt run by `validation_model`).
6. Concatenates rows across all input sheets/files into ONE grid table stored externally in TableV1; the runtime `step.output` carries a `table_v1_log_id` handle.

The runtime output dict:

```json
{ "schema": <generated row schema>, "data": [<row>, <row>, ...], "user_documents": [...], "metadata": {...} }
```

Each cell value is wrapped `{"value": <scalar>, "citations": [...]}` when citations are produced — downstream inline-code steps must unwrap with `val["value"] if isinstance(val, dict) and "value" in val else val`.

### Required inputs

| Param          | Type    | Required | Source                                                                                   |
| -------------- | ------- | -------- | -------------------------------------------------------------------------------------------- |
| `documents`    | `array` | YES      | `input_mapping` — typically `Classify Documents.documents.<ClassName>` (filtered list).  |
| `sov_fields`   | `array` | YES      | Lives in `step.config.sov_fields` — config-provided, NOT in mappings.                    |

### Config keys

From `SovMappingConfig` and `SovFieldConfig`:

#### Per-field (`config.sov_fields[i]` — `SovFieldConfig`)

| Key                          | Type      | Default          | Notes                                                                                                                                                              |
| ---------------------------- | --------- | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                       | string    | (required)       | Snake-case canonical column id, e.g. `address_line_1`. Becomes the output column name and the glom path `data.0.<name>`.                                          |
| `title`                      | string    | (required)       | Display title shown in the UI grid header, e.g. `"Address Line 1"`.                                                                                                |
| `description`                | string    | (required)       | **Selects which input column to map from.** This is the prompt the column-detection LLM sees per field. Be explicit — include common header aliases.              |
| `data_type`                  | string    | `"text"`         | One of `"text" \| "string" \| "integer" \| "float" \| "boolean" \| "date"`. Drives the JSON-Schema type of the output cell.                                       |
| `validator`                  | string?   | `null`           | **Natural-language prompt** that computes the cell value from the selected column. Runs on `validation_model`. Pick exactly ONE of `validator` or `validator_func`. |
| `validator_func`             | string?   | `null`           | **Name of a predefined Python validator** (from the platform's SOV validators registry, e.g. `address_line_1_spelled_out_with_reasoning`). Cheaper + deterministic. |
| `default_value`              | any       | `null`           | Returned when the column is missing OR the validator yields empty. Special sentinel `"AUTO-GENERATE"` is used for synthetic ids.                                   |
| `use_multiple_input_columns` | bool      | `false`          | Set `true` when one output field needs to read from multiple input columns (e.g. address combining `street` + `apt`). Pair with a `multi_column_*` validator.       |
| `global_ai_extract`          | bool      | `false`          | Bypass column matching and run a free-form AI extraction against the entire row. Use sparingly — slower and pricier per cell.                                      |
| `enable_batch_validation`    | bool      | `true`           | Batch rows through the validator for speed. Disable only when a validator is row-context-sensitive.                                                                |
| `enable_confidence`          | bool      | `false`          | Emit confidence scores for this field. Pair with `show_confidence_score=true`.                                                                                     |
| `show_confidence_score`      | bool      | `true`           | UI-only — toggles confidence chip visibility per cell.                                                                                                             |
| `excel_data_type`            | string?   | `null`           | Optional Excel-formatting hint (e.g. for currency export).                                                                                                         |

#### Step-level (`config.*` — `SovMappingConfig`)

| Key                                  | Type                 | Default             | Notes                                                                                                                                       |
| ------------------------------------ | -------------------- | ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `sov_fields`                         | `List[SovFieldConfig]` | (required)        | Defines output schema columns. Order is preserved in the grid.                                                                              |
| `track_changes_in_ui`                | bool                 | `false`             | Show pre-edit values in the UI when reviewers correct cells. On in most production workflows.                                               |
| `enable_confidence_for_all_fields`   | bool                 | `false`             | Force-enable confidence on every field.                                                                                                     |
| `include_unmapped_columns`           | bool                 | `false`             | Append source columns that didn't match any `sov_field` as extra "Unmapped" columns on the right of the grid.                              |
| `display_source_column`              | bool                 | `false`             | Add a `Source Name` (sheet/file) column to the output. Always included in the generated schema; this flag controls UI visibility.          |
| `exclusive_sov_fields`               | `List[str]?`         | `null`              | Field names that MUST NOT share input columns. When multiple exclusive fields match the same source col, all are left unmapped.            |
| `column_detection_model`             | enum                 | `DEFAULT_AI_MODEL`  | Model that picks input columns per `sov_field.description`.                                                                                 |
| `header_and_last_row_detection_model`| enum                 | `DEFAULT_AI_MODEL`  | Model that finds header row and last data row in each sheet.                                                                                |
| `validation_model`                   | enum                 | `DEFAULT_AI_MODEL`  | Model that runs the natural-language `validator` per field. Predefined `validator_func` does NOT use this model.                            |
| `last_row_detection_mode`            | enum                 | `"sliding_window"`  | Method to detect last data row. The default is right for >99% of cases.                                                                     |
| `custom_header_and_last_row_prompt`  | string?              | `null`              | Override prompt for header/last-row detection. Rare; only for unusual layouts.                                                              |
| `include_hidden_sheets`              | bool                 | `true`              | Process hidden sheets too. Customers often hide working tabs that still hold canonical data.                                                |
| `raw_data_post_processing`           | function config?     | `null`              | Function applied to raw sheet data AFTER source-column injection, BEFORE LLM field mapping. Used to drop junk rows, unmerge cells, etc.    |
| `show_confidence_score`              | bool                 | `true`              | Step-level UI toggle for confidence chips.                                                                                                  |

#### Exclusivity rule

`exclusive_sov_fields` requires `include_unmapped_columns=True` — rejected otherwise (conflicting columns are routed to the unmapped-columns list, which must exist).

### Output schema

This step outputs a **TABLE**:

```json
{
  "type": "object",
  "description": "TableV1 structure with SOV mapping results",
  "properties": {
    "schema": { "type": "object" },
    "data":   { "type": "array",
                "items": { "type": "object", "properties": { "Source Name": "<cell>", "<sov_field_name>": "<cell>", "...": "..." } } },
    "user_documents": { "type": "array" },
    "metadata": { "type": "object" }
  }
}
```

The generator walks `config.sov_fields` and produces one output column per field, wrapped as the `cell` named type (carries `value` + `citations`). It always prepends a `Source Name` column. `data_type` maps to JSON Schema as: `string|text → string`, `integer → integer`, `float → number`, `boolean → boolean`, `date → string`.

Downstream wiring choices for `output_attribute`: `"data"` (row array — most common), `"data.0.<field_name>.value"` (single row + cell, only when guaranteed single-row), `"schema"`, or `null` (full dict). Leave step-level `output_type`, `output_config`, and `output_schema` null — auto-filled.

Mapped rows live externally in TableV1, NOT in `step.output.data` at runtime — wire through `input_mappings` so the resolver hydrates the table.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only (with `dependencies: []`).
- EXISTING steps: preserve whatever pattern is there. Don't churn working steps.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

Both patterns exist in the wild: long-lived "Map SOV" steps with `dependencies: [{"step_name": "Document Classification", "field_selector": "SOV"}]`, and newer steps wiring `documents` via `input_mappings` with `output_attribute: "documents.SOV"`.

### Common patterns

#### Pattern 1 — Property SOV (predefined validators, classifier feeds documents)

The most common shape. Property submissions land an Excel SOV; ~40 canonical fields; each field has a `validator_func` for deterministic extraction.

```json
{
  "type": "sov_mapping",
  "name": "Map SOV",
  "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Classify Documents", "output_attribute": "documents.SOV"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "documents"}
  ],
  "config": {
    "sov_fields": [
      {"name": "location_number",  "title": "Location #",      "data_type": "text",
       "description": "Location Number",
       "validator_func": "location_number_with_reasoning",     "enable_batch_validation": true},
      {"name": "address_line_1",   "title": "Address Line 1",  "data_type": "text",
       "description": "The primary street address. If not explicitly present, see if there are separate street/number columns.",
       "validator_func": "address_line_1_spelled_out_with_reasoning"}
    ],
    "track_changes_in_ui": true,
    "include_unmapped_columns": false,
    "include_hidden_sheets": true,
    "last_row_detection_mode": "sliding_window",
    "column_detection_model": "gpt-5.1",
    "validation_model": "gpt-5.1"
  },
  "output_schema": null
}
```

#### Pattern 2 — Census mapping (natural-language validators + `display_source_column`)

Medical-benefits census files. Schema is short (~10 fields), but the per-row transforms are messy (gender mapping, date normalization) so each field uses a NL `validator` instead of a predefined function. `display_source_column=true` shows reviewers which file/sheet a row came from.

```json
{
  "type": "sov_mapping",
  "name": "Census Data Mapping",
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Classify Documents", "output_attribute": "documents.Census document"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "documents"}
  ],
  "config": {
    "sov_fields": [
      {"name": "dob", "title": "DOB", "data_type": "text",
       "description": "Member's date of birth. Common header aliases: 'DOB', 'Date of Birth', 'Birthdate'.",
       "validator": "Normalize any parseable date to MM/DD/YYYY; if day/month are ambiguous, prefer US format."},
      {"name": "gender", "title": "Gender", "data_type": "text",
       "description": "Member's gender/sex. Common values: 'M', 'F', 'Male', 'Female'.",
       "validator": "Map common variants to M/F/U (e.g., Male→M, Female→F, Unknown→U)."}
    ],
    "track_changes_in_ui": true,
    "display_source_column": true,
    "raw_data_post_processing": {"func": "drop_empty_census_rows", "kwargs": {}},
    "column_detection_model": "gpt-5.1",
    "validation_model": "gpt-5.1"
  },
  "output_schema": null
}
```

#### Pattern 3 — Unmapped passthrough + multi-column AI extract

When the customer's tail columns vary by submission and the carrier wants to see all of them, set `include_unmapped_columns: true`. For fields that span multiple columns (composite addresses), set `use_multiple_input_columns: true` and use the `multi_column_ai_extract(...)` validator.

```json
{
  "config": {
    "sov_fields": [
      {"name": "address_line_1", "title": "Address Line 1", "use_multiple_input_columns": true,
       "description": "Select the column(s) that most likely contain the primary street address (street number + name + unit if applicable).",
       "validator": "multi_column_ai_extract(\"Extract ONLY the primary street address (no city/state/zip).\")"}
    ],
    "include_unmapped_columns": true,
    "track_changes_in_ui": true
  }
}
```

### Common validation errors and fixes

- **`documents` missing from `input_mappings` (for new steps)** → required. Wire from `Classify Documents.documents.<ClassName>` (filtered).
- **`documents` wired from bare `Classify Documents.documents`** → rejected: `Parameter 'documents' expects type 'array' but step 'Classify Documents' outputs type 'object'`. Descend with `documents.<ClassName>`.
- **`sov_fields` placed in `input_mappings`** → config-provided; it MUST live in `step.config.sov_fields` only.
- **Both `validator` and `validator_func` set on the same field** → pick exactly one. Conflicting values yield undefined behavior.
- **`validator_func` references a name not in the validators registry** → runtime `Unknown validator function: <name>`. Verify against the platform's SOV validators.
- **`exclusive_sov_fields` set without `include_unmapped_columns=True`** → rejected: conflicting columns are added to the unmapped-columns list, which must exist.
- **`use_multiple_input_columns: true` with a single-column validator (or vice versa)** → the validator receives the wrong shape. Pair `use_multiple_input_columns: true` with `multi_column_*` validators.
- **Non-Excel documents wired into `documents`** → SOV mapping only handles spreadsheets. PDFs/images are ignored or error out. Filter through `classify_documents` first.

### Common gotchas

- **`description` ≠ `validator`. They do different jobs.** `description` is read by the column-detection LLM to pick which INPUT column maps to this field. `validator` is read by the validation LLM to compute the OUTPUT value from the selected column. Write the description as "what column header to look for"; write the validator as "what to return given that cell."
- **Source Name column is ALWAYS in the output schema, but only renders in the grid when `display_source_column: true`.** Downstream consumers can still index `data.0.["Source Name"].value` regardless.
- **Cells are wrapped `{value, citations}`.** Unwrap before doing string ops or arithmetic.
- **Predefined `validator_func` runs in Python and ignores `validation_model`.** Natural-language `validator` calls `validation_model`. Mixing both kinds of fields in one step is fine.
- **`raw_data_post_processing` is a function config, not free Python in a string.** It must declare `code` (or a registered `func`) plus matching schemas; it receives `all_sheets_data` and returns the modified version BEFORE column matching runs.
- **`include_hidden_sheets: true` is the default and the right answer in almost every case.** Only flip to `false` when the customer explicitly says hidden sheets are stale.
- **`enable_batch_validation: true` is the default for performance.** Disable only when a validator is row-context-sensitive.
- **`last_row_detection_mode: "sliding_window"` is correct for >99% of cases.**
- **TableV1 storage:** mapped rows are hydrated only via `input_mappings` resolution — code reading `step.output.data` directly gets an empty list.

### See also

- **`extract_rows_from_multiple_sources`** — sibling tabular extraction WITHOUT column-mapping. Use when source rows come from PDFs and you don't need to canonicalize varying spreadsheet headers.
- **`extract_from_multiple_sources`** — sibling for key-value (one row total) extraction.
- **`extract_mixed_schema_from_multiple_sources`** — when you need BOTH object fields AND a nested rows array in one step.
- **`classify_documents`** — almost always the upstream step that feeds `documents` here via `documents.<ClassName>`.
- **`enrich_addresses_with_gmaps`** (deprecated) / `google_maps` loop mode — downstream address enrichment over the mapped table.
- **`custom_step`** — typical downstream step after SOV mapping (aggregations, ratings, exports). Remember to unwrap `{value, citations}` cells.
