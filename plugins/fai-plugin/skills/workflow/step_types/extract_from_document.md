# extract_from_document

> **DEPRECATED — DO NOT CREATE NEW INSTANCES.** Hidden from the builder's step picker; the bundled validator errors on new instances. If asked to create one, use `extract_from_multiple_sources` with `documents` mapped to a one-element list plus a `document_system_prompt` — that covers the single-doc case identically. Editing existing instances is allowed — many live workflows still carry this step type.

### What this step does

Schema-guided extraction over a **single document**. Takes one `document` (a `UserDocument`, usually wired from `Prepare Documents` or one bucket of `Classify Documents`) plus an `extraction_schema`, and produces a TableV1 whose leaves are cell-wrapped at runtime (`{value, confidence_score, confidence_reason, citations, thinking_steps}`).

Documents go through Reducto by default; setting `llm_extraction_model` switches the extraction path to a direct LLM. There is no email branch and no per-source tie-breaking — only one source is consulted.

Output envelope:

```json
{
  "schema":         "<JSON Schema mirror of extraction_schema, cell-wrapped at leaves>",
  "data":           "<extracted object, cell-wrapped leaves>",
  "user_documents": "<source documents>",
  "metadata":       "<extraction metadata>"
}
```

Downstream wiring almost always uses `data.<field>.value`. Wiring bare `data.<field>` resolves to type `cell`, which the save-time type-compat check rejects against any primitive consumer.

### When you'll encounter this

Only on **existing** workflows. Common edit reasons: schema additions / renames, prompt tweaks, model swaps (`gpt-4o` → `gpt-5.1`), toggling `deep_extract` / `reducto_version` for hard documents, migrating to `extract_from_multiple_sources`.

### Migration to `extract_from_multiple_sources` (recommended)

A one-way migration to offer whenever editing an existing `extract_from_document` step:

1. `type`: `extract_from_document` → `extract_from_multiple_sources`.
2. Rename input parameter `document` → `documents`. Upstream `output_attribute` typically changes too — a single `UserDocument` becomes a one-element array: `Classify Documents.documents.<ClassName>.0` → `Classify Documents.documents.<ClassName>`. If the source is genuinely a scalar, wrap with a `custom_step`.
3. Rename input parameter `system_prompt` → `document_system_prompt`.
4. Keep `extraction_schema` on `config` (same key name, no changes).
5. Downstream `output_attribute` paths assuming the legacy flat shape need updating to `data.<field>.value`.

Example (before / after):

```json
// BEFORE — extract_from_document
{
  "name": "Extract <Section>",
  "type": "extract_from_document",
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Classify Documents", "output_attribute": "documents.<CLASS>.0"}], "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "document"},
    {"input_type": "static", "value": "Extract ...", "input_parameter_name": "system_prompt"}
  ],
  "config": {"extraction_schema": {"type": "object", "properties": {"...": "..."}}, "model": "gpt-4o", "generate_citations": true, "spreadsheet_agent": true}
}

// AFTER — extract_from_multiple_sources
{
  "name": "Extract <Section>",
  "type": "extract_from_multiple_sources",
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Classify Documents", "output_attribute": "documents.<CLASS>"}], "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "documents"},
    {"input_type": "static", "value": "Extract ...", "input_parameter_name": "document_system_prompt"}
  ],
  "config": {"extraction_schema": {"type": "object", "properties": {"...": "..."}}, "model": "gpt-5.1", "generate_citations": true, "spreadsheet_agent": true}
}
```

### Required inputs (for editing existing instances)

| Parameter | Type | Required | Notes |
| --- | --- | --- | --- |
| `document` | `file` (single `UserDocument`) | **yes** | Wire from `Prepare Documents.documents.0` or `Classify Documents.documents.<ClassName>.0`. |
| `system_prompt` | `string` | no | Pass via `input_mappings` as `input_type: "static"`. |
| `mode` | `string` | no | Execution mode; almost always omitted. |
| `generate_citations` | `bool` | no | Usually set on `config`, not input_mappings. |
| `llm_extraction_model` | `string` | no | Usually set on `config`. |

`extraction_schema` is NOT in `input_mappings` — it lives on `step.config`.

### Config keys (for editing existing instances)

Mirrors `ExtractFromDocumentConfig`.

| Key | Type | Default | Purpose |
| --- | --- | --- | --- |
| `extraction_schema` | JSON Schema (`type: "object"` with `properties`) | required | Fields to extract. Nested objects and arrays allowed. |
| `system_prompt` | string \| null | `null` | LEGACY slot. Do NOT set here on edits — pass via `input_mappings`. Runtime reads the kwarg first; config is the fallback for un-migrated workflows. |
| `spreadsheet_agent` | bool | `false` | Routes `.xlsx`/`.xls` through Reducto's spreadsheet agent. No-op on PDFs. |
| `model` | AIModel | workflow default | Extraction model. Existing instances typically run `gpt-4o`; safe to swap to `gpt-5.1`. |
| `llm_extraction_model` | AIModel \| null | `null` (use Reducto) | When set, uses the direct-LLM path. Citation compatibility is validated (see errors below). |
| `generate_citations` | bool | `true` | Per-leaf citations array on the cell. |
| `show_confidence_score` | bool | `true` | UI-only — per-cell confidence chip. |
| `use_fastest_extraction` | bool | `false` | Reducto "fast" mode. |
| `use_expensive_extraction` | bool | `false` | Reducto "accurate" mode. |
| `deep_extract` | bool | `false` | Reducto deep extract. Significantly more expensive; implicitly expects `reducto_version: "v3"`. |
| `reducto_version` | `"v2" \| "v3" \| null` | `null` (workflow default) | Per-step override. |

### Output schema

```json
{
  "type": "object",
  "description": "TableV1 structure matching extraction_schema",
  "properties": {
    "schema":         {"type": "object"},
    "data":           {"type": "object"},
    "user_documents": {"type": "array"},
    "metadata":       {"type": "object"}
  }
}
```

Each leaf in `data` is cell-wrapped at runtime. The save-time validator synthesizes this wrap when paths terminate in `value` / `confidence_score` / `confidence_reason` / `citations` / `thinking_steps`, so `data.broker_name.value` and `data.broker_name.citations.0.content` resolve without expanding the schema by hand.

### Input wiring (input_mappings vs dependencies)

- **NEW steps**: N/A — this type can't be added. Use `extract_from_multiple_sources`.
- **EXISTING steps**: preserve whatever pattern is already there.
- **Migration** `dependencies` → `input_mappings` on explicit request; the reverse is forbidden. Most existing instances already use `input_mappings` with `dependencies: []` — but some legacy-wired ones remain, so check before assuming.

### Common patterns

#### 1) Pull a single doc from a Classify-Documents class bucket

Most common shape. Class bucket indexed to `.0` because the step takes exactly one `document`. If multiple docs of that class can show up, either the workflow implicitly assumes one, or the right move is to migrate.

```json
{
  "name": "Extract <Section>", "type": "extract_from_document", "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Classify Documents", "output_attribute": "documents.<CLASS>.0"}], "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "document"},
    {"input_type": "static", "value": "### Insurance Proposal Document Context\n\nYou are extracting ...", "input_parameter_name": "system_prompt"}
  ],
  "config": {"extraction_schema": {"type": "object", "properties": {"is_present": {"type": "boolean"}, "...": "..."}}, "model": "gpt-4o", "generate_citations": true, "show_confidence_score": true, "spreadsheet_agent": true}
}
```

#### 2) Same shape with deep extract for hard documents

Identical wiring; `deep_extract: true` + `reducto_version: "v3"` toggled for one specific class (typically dense schedules, loss runs, or complex coverage grids).

#### 3) Spreadsheet branch for Excel documents

`spreadsheet_agent: true` is the right toggle when the document class is `.xlsx`/`.xls`. No-op on PDFs, so leaving it on for mixed-class buckets is safe.

### Common validation errors and fixes

| Error message | Fix |
| --- | --- |
| `invalid step type 'extract_from_document'` (new instance) | Deprecated — author `extract_from_multiple_sources` instead. |
| `Missing required input mappings: ['document']` | Wire a `document` input. |
| `Parameter 'document' expects type 'file' but step '...' outputs type 'array'` | You wired the array (`documents.<CLASS>`) instead of an element (`documents.<CLASS>.0`). Add `.0`, or migrate and wire the array as `documents`. |
| `Output attribute '<path>' not found in step '...' output schema. Available top-level properties: [...]` | Glom path doesn't resolve. For `Classify Documents`: `documents.<ClassName>.0`; for `Prepare Documents`: `documents.0`. |
| `llm_extraction_model is set, so generate_citations must be False` (or similar) | Unset `llm_extraction_model` (use Reducto) OR set `generate_citations: false`. |
| Inline-code step error: `imports 'extract_from_document' — use the step type instead.` | Don't call the extraction block from step code. Use the dedicated step type — better, the `extract_from_multiple_sources` replacement. |
| `Parameter '<name>' does not allow mode 'static'/'dependency'.` | `document` is dependency-only; `system_prompt` is effectively static-only. |

### Common gotchas

- **Do not create new instances.** Refuse and propose `extract_from_multiple_sources` with a one-element `documents` mapping plus `document_system_prompt`.
- **`system_prompt` via input_mappings, not config.** Runtime reads the kwarg first; `config.system_prompt` is the legacy fallback. Preserve the input_mappings pattern on existing steps.
- **`document` is singular.** Load-bearing difference from `extract_from_multiple_sources`. The upstream path must terminate at a single `UserDocument` — `Classify Documents.documents.<CLASS>.0`, not `.<CLASS>`.
- **`deep_extract: true` is expensive.** Only enable for the specific class that needs it, paired with `reducto_version: "v3"` at the step level.
- **Schema property names propagate downstream verbatim.** Renaming a field in `extraction_schema` breaks every downstream step that reads `data.<old>.value`. Treat schema keys as a public contract.
- **`extraction_schema` keys are NOT the workflow's display columns.** `workflow.columns` is a separate workflow-level surface — adding a schema field does not automatically create a display column.
- **Single-source by design.** No per-field tie-breaking, no multi-doc reasoning. If the user needs that, migrate to `extract_from_multiple_sources` — don't stack multiple single-doc steps.

### See also

- **`extract_from_multiple_sources`** — MODERN REPLACEMENT. Use for all new extraction needs, including the single-doc case.
- `extract_rows_from_multiple_sources` — multiple rows per doc (claims schedules, driver lists, census).
- `extract_mixed_schema_from_multiple_sources` — mixed shape (object fields + array fields).
- `agentic_extraction` — dynamic schema, RAG over a KB, tool use.
