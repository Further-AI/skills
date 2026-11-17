# agentic_extraction

### What this step does

Runs a RAG agent (shared infrastructure with `agentic_guideline_check`) over the workflow's knowledge base. The agent is given the KB handle wired into `kb`, an optional filtered `documents` list to scope its search, a system prompt with domain context, and the `extraction_schema` (JSON Schema, `type: "object"`) describing the fields to extract. It calls `rag_search()` (and, if enabled, `browse_document()` and `spreadsheet_extract()`) iteratively until it can fill every field, returning a TableV1-shaped output:

```json
{ "schema": <json schema>, "data": <field -> {value, citations} dict>, "user_documents": [...] }
```

Rendered in the UI as a key-value table. Each extracted value carries a confidence score and optional source citations hyperlinked back to the original document.

**Two output modes** via `config.output_mode`:
- `"structured_table"` (default) — extracts the `extraction_schema` fields into a per-field table with confidence + citations per cell.
- `"free_form_analysis"` — the agent writes a markdown analysis driven entirely by `system_prompt`; `extraction_schema` is ignored and may be omitted. Output envelope: `{markdown_content, citation_metadata, user_documents, additional_citations}` with inline `<cite>DOC_X:CHUNK_Y</cite>` tags. The backend auto-sets `output_type: "text"`. `extract_per_document` and non-default backends are not supported in this mode.

### Required inputs

| Param          | Type       | Required | Source                                                                                                 |
| -------------- | ---------- | -------- | ------------------------------------------------------------------------------------------------------ |
| `kb`           | `object`   | YES      | `input_mapping` from a `knowledge_base` step (`output_attribute: "kb"`).                              |
| `documents`    | `array`    | no       | `input_mapping` — usually omit; wire only to scope to a classified subset. See "Filtered Documents" below. |
| `system_prompt`| `string`   | no       | `input_mapping`, almost always `input_type: "static"`. Never put this in `config`.                     |

Every runtime input is user-mappable.

**Filtered Documents — leave blank by default.** With no `documents` mapping, the agent searches the full KB. Wire `documents` only to scope to a category, and only from a path that resolves to an array (`Classify Documents.documents.<ClassName>`, or `concat_lists` over several such paths). NEVER wire bare `Classify Documents.documents` — the backend rejects it with `Parameter 'documents' expects type 'array' but step 'Classify Documents' outputs type 'object'`.

### Config keys

From `AgenticExtractionConfig`:

| Key                              | Type    | Default     | Notes                                                                                                                                                                                                |
| -------------------------------- | ------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `output_mode`                    | enum    | `"structured_table"` | Or `"free_form_analysis"` — see above.                                                                                                                                                        |
| `extraction_schema`              | object  | (required)  | JSON Schema, `type: "object"` with `properties` (required for structured mode; ignored in free-form mode). Use field `description` to drive extraction behavior. Arrays at top level are allowed (e.g. `{"properties": {"rows": {"type": "array", ...}}}`). |
| `system_prompt`                  | string  | `null`      | **DEPRECATED here.** Pass via `input_mappings` instead. Executor reads the kwarg first; config is legacy-only fallback.                                                                              |
| `generate_citations`             | bool    | `true`      | Wraps each extracted value with `citations: [...]` keyed to KB chunks.                                                                                                                                |
| `show_confidence_score`          | bool    | `true`      | UI flag — surfaces per-field confidence to the user.                                                                                                                                                  |
| `model`                          | enum    | `gpt-5.4`   | Recommend `"gpt-5.5"` (strongest); use `"gpt-5.1"` for high-volume/simple schemas. Claude Sonnet 4.6 and Opus 4.6/4.7 are also offered on this step type.                                            |
| `reasoning_effort`               | enum    | `null`      | Reasoning models only: `"low" \| "medium" \| "high"` (`gpt-5.1` has no `"xhigh"`). Must be `null` for non-reasoning models — validated at save.                                                     |
| `verbosity`                      | enum    | `null`      | GPT-5 only: `"low" \| "medium" \| "high"`. Same validator gate as `reasoning_effort`.                                                                                                                |
| `extract_per_document`           | bool    | `false`     | When `true`, agent runs once per document in parallel and the outputs are concatenated. Use only when each document is independent (e.g. separate carrier quotes) and the schema applies per-doc.     |
| `enable_document_browse_tool`    | bool    | `false`     | Gives the agent a `browse_document(doc_id, chunk_position)` tool to walk documents sequentially. Costs more tokens; use only when RAG search alone keeps missing context.                            |
| `enable_spreadsheet_extract_tool`| bool?   | `null`      | Tri-state. `null` = auto-on when any uploaded file is `.xlsx`/`.xlsm`/`.csv`. `true` = force on. `false` = force off. Hard-disabled for top-level array schemas regardless.                          |
| `enable_get_step_tool`           | bool    | `false`     | When `true`, the agent gets a `get_step(step_name)` tool to read allowlisted prior step outputs mid-extraction. Pair with `allowed_get_step_dependencies` to scope.                                   |
| `allowed_get_step_dependencies`  | list[str]? | `null`   | Explicit allowlist of step names for `get_step`. If omitted while `enable_get_step_tool` is true, the allowlist is derived from the step's declared dependencies.                                    |
| `extraction_backend`             | enum    | `"pydantic-ai"` | Which agentic backend runs the extraction: `"pydantic-ai"` (default), `"claude-agent-sdk"` (sandbox agent), `"openai-agents"`. **Preserve this on edit** — workflows on `claude-agent-sdk` are intentional; never flip the backend as a drive-by. |
| `preprocess_modes`               | list[enum] | `["full_text"]` | Sandbox-agent backends only: how documents are staged on the sandbox FS. `"full_text"` stages parsed text; `"raw"` stages originals. List both to compose. A legacy singular `preprocess_mode` key still exists on older workflows — preserve whichever form is persisted. |
| `max_turns`                      | int?    | `null`      | Hard ceiling on agent turns (internal default applies when null). Only set when the user explicitly wants to cap runaway extractions.                                                                |
| `max_budget_usd`                 | float?  | `null`      | Per-extraction cost cap (sandbox-agent backends). Leave null unless asked.                                                                                                                            |
| `enforce_schema_validation`      | bool    | `true`      | Sandbox-agent backends only: validates the agent's output against the extraction schema each turn and nudges on enum drift. Leave on.                                                                |
| `e2b_template_id`                | str?    | `null`      | Sandbox template override. Currently a no-op — never set.                                                                                                                                             |

### Output schema

```json
{
  "type": "object",
  "properties": {
    "schema": { "type": "object" },
    "data":   { "type": "object" },
    "user_documents": { "type": "array" }
  }
}
```

When wiring downstream steps to read this output, choose `output_attribute` according to need:

| `output_attribute` | Returns                                                                       |
| ------------------ | ----------------------------------------------------------------------------- |
| `null`             | Full step output dict (schema + data + user_documents + KB handle).            |
| `"data"`           | Just the extracted field dict — each value wrapped `{value, citations}`. Most common. |
| `"data.<field>.value"` | One extracted scalar (leaves are cell-wrapped — see `../reference/input_mappings.md`). |
| `"kb"`             | The KB handle the agent used. Pass-through to another KB-consuming step.       |
| `"schema"`         | The resolved extraction schema (after enum-id generation).                     |

Leave step-level `output_type`, `output_config`, and `output_schema` null — the backend auto-fills them (see `../reference/output_types.md`).

The actual extracted data is **stored externally** in TableV1. The step's runtime `output` dict only holds `{table_v1_log_id, link_to_table_log, view_output_button_text}`. Downstream `input_mappings` resolution hydrates the table data automatically — do NOT try to read `step.output.data` directly from an inline-code step.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is there.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

A new `agentic_extraction` step always has `dependencies: []` and wires `kb` (and optionally `documents` and `system_prompt`) through `input_mappings`.

### Common patterns

#### Pattern 1 — Per-document parallel extraction of repeated row records

For workflows that ingest multiple insurer quote slips and want one row per quote. `extract_per_document: true` runs the agent independently on each document in parallel and concatenates the rows.

```json
{
  "type": "agentic_extraction",
  "name": "Extract Quote Options",
  "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Initialize Knowledge Base", "output_attribute": "kb"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "kb"},
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Classify Documents", "output_attribute": "documents.QuoteSlip"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "documents"},
    {"input_type": "static",
     "value": "You are an expert underwriting analyst extracting quote rows from insurer slips...",
     "input_parameter_name": "system_prompt"}
  ],
  "config": {
    "extraction_schema": {
      "type": "object",
      "properties": {
        "quote_options": {
          "type": "array",
          "title": "Quote Options",
          "description": "Extract one row per insured-facing quoted layer. Suppress rows that fail required-field validation.",
          "items": {"type": "object", "properties": { /* per-row fields with rich descriptions */ }}
        }
      },
      "required": ["quote_options"]
    },
    "generate_citations": true,
    "show_confidence_score": false,
    "model": "gpt-5.5",
    "extract_per_document": true,
    "enable_document_browse_tool": true
  },
  "output_schema": null
}
```

#### Pattern 2 — Flat boolean / enum field set with field-description-driven rules

For underwriting eligibility / supplemental-question extraction (e.g. cyber-crime controls, segment-level booleans). One agent invocation across the full KB; the schema is a flat object of small fields, and each field's `description` carries the full extraction contract (allowed values, fallback tokens like `"Not Asked" | "INCONCLUSIVE"`, source-priority rules).

```json
{
  "config": {
    "extraction_schema": {
      "type": "object",
      "properties": {
        "has_cctv_remote_monitoring": {"type": "boolean", "title": "Has CCTV / Remote Monitoring", "description": "Return true if any reference to CCTV, remote/video monitoring, virtual guard, etc. ..."},
        "cyber_crime_callback_policy": {"type": "string", "title": "Callback Policy", "description": "Allowed outputs (case-sensitive): Yes | No | Not Asked | Unknown - Asked but not Answered | INCONCLUSIVE. ..."}
      },
      "required": ["cyber_crime_callback_policy"]
    },
    "generate_citations": true,
    "show_confidence_score": true,
    "model": "gpt-5.5",
    "extract_per_document": false,
    "enable_document_browse_tool": false
  }
}
```

#### Pattern 3 — Single-call array extraction across the whole KB

Same shape as Pattern 1 but with `extract_per_document: false` — the agent decides which documents to RAG-search and emits a single consolidated array. Used for things like `exposures` (rolling up payroll/sales lines from multiple sources into one list).

```json
{
  "config": {
    "extraction_schema": {
      "type": "object",
      "properties": {
        "exposures": {
          "type": "array",
          "title": "Exposures",
          "items": {"type": "object", "properties": {
            "operation_description": {"type": "string", "title": "Operation Description", "description": "Exact operation name as written."},
            "amount": {"type": "string", "title": "Amount", "description": "Annual dollars formatted '$1,234,567'."},
            "exposure_type": {"type": "string", "title": "Exposure Type", "description": "One of: 'Payroll', 'Gross Sales', 'Subcontracted Costs'."}
          }}
        }
      }
    },
    "model": "gpt-5.5",
    "extract_per_document": false,
    "enable_document_browse_tool": false
  }
}
```

### Common validation errors and fixes

- **`kb` missing from `input_mappings`** → reported as a missing required input. Wire it from the `knowledge_base` step.
- **`documents` wired from `Classify Documents.documents` (bare path)** → rejected with `Parameter 'documents' expects type 'array' but step 'Classify Documents' outputs type 'object'`. Fix: use `documents.<ClassName>` (or `concat_lists` over multiple `documents.<ClassName>` paths), or omit `documents` entirely.
- **`system_prompt` in `config` instead of `input_mappings`** → flagged by the bundled validator; the executor falls back to `config.system_prompt` only for legacy un-migrated workflows. New steps must pass it as a `static` input mapping.
- **`reasoning_effort` / `verbosity` set on a non-reasoning model** → rejected at save. Either move to a reasoning model or clear both fields to `null`.
- **Top-level `enum` in extraction schema** → use `"type": "enum"` with `"options": [...]` (the platform dropdown convention), not JSON Schema `"enum": [...]`.
- **`enable_spreadsheet_extract_tool: true` on a top-level array schema** → hard-disabled by the agent regardless of config; reset to `null`.

### Common gotchas

- **Field descriptions outrank the system prompt.** If the schema's `description` says "numeric only" and the system prompt says "format with `$`", the model follows the field description. Keep formatting rules consistent.
- **Don't reconstruct the KB.** The resolved `kb` value is a ready-to-use handle, not a config dict. Pass it through as-is.
- **Confidence-score visibility is UI-only.** `show_confidence_score: false` does not change agent behavior; it only hides the chip in the rendered table.
- **`extract_per_document: true` does NOT change the schema shape.** Each per-document run still emits the same top-level object; the framework concatenates `items` arrays across documents. Don't add per-document wrapping in the schema.
- **`agentic_extraction` and `agentic_guideline_check` share an agent.** Changes to model config, citation behavior, or browse-tool semantics here apply there too.
- **Don't hardcode dates in the system prompt.** They go stale. Let the agent infer dates from documents.
- **TableV1 storage:** the extracted data is NOT in `step.output` at runtime. Always wire downstream consumers through `input_mappings` with `output_attribute: "data"`.
- **Incremental behaviors:** `rerun`, `no_op`, `append`, and `preserve_edits` are all accepted for this step type.

### See also

- **`extract_from_multiple_sources`** — schema-rigid sibling. Use when source-of-truth (email vs document) is known up front and you want deterministic tie-breaking rather than agent-driven RAG search.
- **`extract_rows_from_multiple_sources`** — for tabular/list data with a fixed row schema (claims, schedules, census). Cheaper and more predictable than asking the agent for an array.
- **`multi_column_qa`** — Q&A-shaped alternative for matrix-style guideline/checklist evaluation, one row per question.
- **`agentic_guideline_check`** — sibling that shares this step's agent infrastructure; use for compliance/checklist evaluation rather than free-form extraction.
- **`combine_kv_tables`** — deterministic merge of several KV extractions into one table.
