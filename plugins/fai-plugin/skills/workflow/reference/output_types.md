# Output Types, Output Config, and Output Schemas

Each step produces a single output dict. Three fields control how that dict is interpreted and rendered:

- `output_type` — enum selecting the UI container.
- `output_config` — container-specific display options.
- `output_schema` — JSON Schema with `glom_path` annotations telling consumers how to read values.

For per-step output details (specific keys, glom paths), see each `step_types/<type>.md`.

---

## `OutputType` enum

| Value | UI renderer | Typical shape |
|---|---|---|
| `table` | TableV1 viewer | `{ schema, data, user_documents, metadata }` |
| `email` | Email panel | `{ email_body, headers, attachments }` |
| `html` | HTML/Tiptap editor | `{ <content_key>: "<html>" }` |
| `text` | Markdown/Tiptap editor | `{ <content_key>: "<markdown>" }` |
| `chat_message` | Chat bubble | `{ <content_key>: "<text>" }` |
| `document_viewer` | PDF/DOCX viewer | shape includes a `user_document_id` at a glom path |
| `knowledge_base` | KB badge/card | `{ knowledge_base_initialized, collection_name, ... }` |
| `document_classification` | Category tiles | `{ documents: { "<category>": [docs...] } }` |
| `simple` | Simple JSON panel | any dict |
| `set_title` | (no-op renderer; sets execution title) | `{ title: "..." }` |
| `compare_log` | Compare viewer | compare-log structure |
| `update_workflow_log` | Log entry | arbitrary |
| `field_value_table`, `excel_view_table`, `sov_mapping_output`, `excel_mapping` | Legacy table variants | TableV1-like |

`output_type` is a RENDER enum, not the step type. Writing a step-type slug (e.g. `output_type: "classify_documents"`) or any value outside this table makes the step fail to parse.

---

## Decision tree — pick exactly ONE pattern per step

**Pattern A — DEFAULT (every step type except `function`/`custom_step` and `submission_summary_generator`):** leave all three null. The backend auto-fills `output_type`, the matching `OutputConfig` subclass, and `output_schema` from the step type at save time; the builder UI doesn't surface these fields on those types either. Setting them manually bypasses auto-fill and trips a mismatch at save.

```json
{ "output_type": null, "output_config": null, "output_schema": null }
```

**Pattern B — `submission_summary_generator` (pick a render mode):** set `output_type` to one of `simple` | `chat_message` | `html`; leave `output_config` null (the backend writes the matching subclass with the right `content_key: "summary"` defaults). **Never `text`** — SSG's content lives under `summary`, not text's default key, so a `text` SSG renders blank.

- `simple` — compact card, summary text inline on the canvas, no View button (the default when null).
- `chat_message` — chat-bubble render (`ChatMessageOutputConfig(content_key="summary")` auto-written).
- `html` — styled HTML report behind a "View Report" button (`HtmlOutputConfig(content_key="summary")` auto-written). Pair with a system prompt that emits HTML. Use this whenever the operator wants a report opened via a button; a "blank report" or "no button" complaint on an SSG step means: set `html` and make the prompt emit HTML.

**Pattern C — `function` / `custom_step` (fully author-controlled):** declare `output_schema` (the engine and UI need the shape), pick `output_type` as the render choice, leave `output_config` null except `document_viewer` (needs `content_document_id`).

```json
{ "output_type": "table | text | html | document_viewer | email | simple | set_title | ...",
  "output_config": null,
  "output_schema": { "type": "object", "properties": { } } }
```

Pattern C notes:
- Human-readable output should be FORMATTED by default: `output_type: "text"` (markdown viewer) or `"html"`, with the producing code emitting markdown/HTML — never a raw JSON dump.
- `output_schema.type` should match the renderer: `array` → grid table, `object` → key-value table.
- The title-extractor pattern (set the run title from extracted data) is a custom code step with `output_type: "set_title"` returning `{"title": ...}`.
- `web_agent` is special: its `output_type` is derived from whether `config.output_schema` is set (table vs document_viewer) — leave `output_type` null when authoring.

**Default remedy for any output-type validation error: set the offending field(s) to `null` and let the backend auto-fill.** Never "fix" by guessing another enum value.

---

## Auto-fill map (what the backend writes when `output_type` is null)

| Step types | `output_type` |
|---|---|
| classify_documents | `document_classification` |
| the extraction family, sov_mapping, generate_qa_table_from_agent (and legacy `_from_kb`), multi_column_qa, ofac_agent, osha_agent, trellis_law, extract_guidelines, check_guidelines, agentic_extraction, agentic_guideline_check, compare_document_data, run_workflow, combine_kv_tables, execution_matching, and the integration connectors | `table` (plus a default `output_config` with a derived `display_type`) |
| submission_summary_generator, workflow_dispatcher | `simple` |
| web_agent | `table` when `config.output_schema` is set, else `document_viewer` |
| prepare_documents, email | `email` |
| knowledge_base | `knowledge_base` |
| compare_documents | `compare_log` |
| fill_docx | `document_viewer` |
| web_search | `text` |
| pause, hold, decision, function, custom_step, manual_input, text_block | (no default — left `null`; for function/custom_step set explicitly per Pattern C) |

---

## OUTPUT_TYPE_MISMATCH / DISPLAY_TYPE_MISMATCH (save-time rejects)

The platform rejects cross-field inconsistencies at validate/save. You author `null` per Pattern A, but editing existing workflows can surface inherited bad values:

- **`OUTPUT_TYPE_MISMATCH`, locked step types**: `classify_documents`, `prepare_documents`, `knowledge_base`, `compare_documents`, `fill_docx` only accept their canonical value (auto-fill map above) or `null`. Fix: set `output_type: null`.
- **`OUTPUT_TYPE_MISMATCH`, multi-mode step types**: `submission_summary_generator` ∈ {`simple`, `chat_message`, `html`}; `generate_qa_table_from_kb` / `generate_qa_table_from_agent` ∈ {`table`, `field_value_table`, `set_title`}; `web_agent` ∈ {`table`, `document_viewer`} (derived — leave null). Anything else → set to `null` or a value from the allowed set.
- **`DISPLAY_TYPE_MISMATCH`**, three rules on `output_config.display_type`:
  1. Only meaningful with `output_type` `table` or `null` — on any other output_type, drop `output_config` entirely.
  2. `"compare"` is only valid on `compare_document_data`.
  3. It must match the step's emit shape — row-list steps are **grid-locked** (`extract_rows_from_multiple_sources`, `extract_mixed_schema_from_multiple_sources`, `sov_mapping`, `multi_column_qa`, `ofac_agent`, `osha_agent`, `extract_guidelines`, `check_guidelines`, `agentic_guideline_check`, `trellis_law`) and single-object steps are **key_value-locked** (`extract_from_document`, `extract_from_email`, `extract_from_multiple_sources`, `generate_qa_table_from_kb`, `generate_qa_table_from_agent`). `agentic_extraction`, `function`/`custom_step`, and connector steps are exempt (shape is runtime-determined).

**Button label goes at the STEP's top level** — `view_output_button_text: "Your label"` as a top-level step field, not inside `output_config`.

---

## `OutputConfig` classes

Every `output_type` has a matching `*OutputConfig` class. The validator enforces the type match (e.g., `TableOutputConfig` only with `output_type: "table"`).

### `TableOutputConfig` (for `output_type: "table"`)

```jsonc
{
  "display_type": "grid | key_value | compare",   // required
  "columns": [
    { "source": "carrier_name",
      "display_name": "Carrier",
      "hidden": false,
      "order": 0 }
  ],
  "view_output_button_text": "View Output"
}
```

- `display_type` is auto-picked when omitted (see below).
- `columns` is optional and purely cosmetic — hidden columns stay in `data` for downstream steps.

### `EmailOutputConfig`
```jsonc
{ "view_output_button_text": "View Email" }
```

### `HtmlOutputConfig`
```jsonc
{
  "content_key": "html_content",      // glom path into step output
  "editable": true,
  "view_output_button_text": "View Output"
}
```

### `TextOutputConfig`
```jsonc
{
  "content_key": "markdown_content",
  "editable": true,
  "view_output_button_text": "View Content"
}
```

### `ChatMessageOutputConfig`
```jsonc
{ "content_key": "answer" }            // default "answer"; set to "summary" for submission_summary_generator
```

### `DocumentViewerOutputConfig`
```jsonc
{
  "content_document_id": "generated_document.user_document_id",
  "view_output_button_text": "View Document"
}
```

---

## Auto-determined `display_type` for table output

When `output_type: "table"` and `output_config` is omitted (or its `display_type` is null), `WorkflowStep._get_table_display_type` picks:

- **GRID**: `extract_rows_from_multiple_sources`, `extract_mixed_schema_from_multiple_sources`, `sov_mapping`, `multi_column_qa`, `ofac_agent`, `osha_agent`, `extract_guidelines`, `check_guidelines`
- **GRID** when `agentic_extraction.extract_per_document=true`; otherwise **KEY_VALUE**
- **COMPARE**: `compare_document_data`
- **KEY_VALUE**: every other table-producing step

For function steps, set `display_type` explicitly — without it, the table won't render correctly.

---

## Output schemas (`output_schema`)

Most built-in steps have static output schemas registered in `STEP_OUTPUT_SCHEMAS` (`workflow/builder/step_schemas.py`). Per-step shape lookup is in each `step_types/<type>.md`.

The custom `glom_path` annotation tells consumers where to find each value:

```jsonc
{
  "metadata": {
    "type": "object",
    "properties": {
      "items":     { "type": "array",   "glom_path": "metadata.items" },
      "num_items": { "type": "integer", "glom_path": "metadata.num_items" }
    }
  }
}
```

These paths are what you reference in `output_attribute` within an `InputMapping` (see `input_mappings.md`).

### Auto-generated schemas

`WorkflowStep.auto_generate_output_schema` auto-fills `output_schema` for **custom-code `function` steps** based on `output_type`:

- `output_type: "table"` → standard table schema
- `output_type: "email"` → email output schema

For `function` steps with output_type `document_viewer` / `chat_message` / `html` / `text`, you must provide `output_schema` and `output_config` yourself.

### Dynamic schema for `manual_input`

`manual_input` output schema is computed at runtime from `config.input_schema.properties`:

| Field `type` | Output schema entry |
|---|---|
| `text` (free / dropdown) | `{ "type": "string" }` |
| `text` (multi_select) | `{ "type": "array", "items": {"type": "string"} }` |
| `number` | `{ "type": "number" }` (array for multi_select) |
| `boolean` | `{ "type": "boolean" }` |
| `file` | `{ user_document_id, filename, category }` (array for multi_select) |
| `file_list` | Array of doc objects |
| `docx_image` | `{ document, settings: { position, resize_mode, target_*_inches } }` |

Plus a `status: "paused"|"completed"` field always present.

---

## Validation rules

- `output_config` class must match `output_type`. The validator raises if you set, e.g., `TableOutputConfig` on a step with `output_type: "email"`.
- For function steps with custom `code` and `output_type: "document_viewer"` or `chat_message`, you must provide both `output_config` and `output_schema` explicitly.
- `output_type` set to an unknown string → warning (not error), but UI won't render correctly.
- For `agentic_extraction` with existing `output_config`, `display_type` is resynced when `extract_per_document` flips.
