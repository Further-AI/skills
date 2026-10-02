# extract_from_multiple_sources

### What this step does

Runs explicit-schema extraction over an arbitrary mix of email + document sources, then **consolidates** all per-source candidate values into a single key-value table. The output table contains one cell per schema property, where each cell is `{value, confidence_score, confidence_reason, citations, thinking_steps}` (the standard cell envelope — see `../reference/input_mappings.md`).

Tie-breaking across sources is handled by the configured `model`. Documents are extracted via Reducto by default; setting `llm_extraction_model` switches that document path to a direct LLM extractor. The email branch always uses an LLM.

Output envelope:

```json
{
  "schema":          "<JSON Schema mirror of extraction_schema, cell-wrapped at leaves>",
  "data":            "<extracted object, cell-wrapped leaves>",
  "user_documents":  "<source documents>",
  "metadata":        "<extraction metadata>"
}
```

Downstream wiring almost always uses `data.<field>.value` (the scalar) — wiring bare `data.<field>` resolves to type `cell`, which the save-time type-compat check rejects against any primitive consumer.

### Required inputs

No input is JSON-Schema-required, because the real rule is a cross-field "at-least-one-source + matching system prompt" combination enforced at save time:

1. Wire **at least one** of:
   - `documents` (the documents branch), OR
   - any email input — grouped `emails`, or the scalar inputs `eml_user_document_id`, `headers`, `email_body`, `body_html`, `body_plain`.
2. If `documents` is mapped → `document_system_prompt` MUST also be mapped (non-empty static or dependency).
3. If any email input is mapped → `email_system_prompt` MUST also be mapped.
4. If **scalar** email inputs are mapped (and grouped `emails` is not) → `eml_user_document_id` MUST also be mapped, so citations link to the source `.eml`/`.msg`. Without it the runtime falls back to scanning the execution log for any email-like attachment — works on happy-path runs but breaks citations on manual triggers and retries. The grouped `emails` input carries per-email document IDs already and does not need it.
5. `extraction_schema` is required, but it lives on `step.config`, not `input_mappings`.

Both branches (`documents` + email) can be mapped at the same time — that's the canonical "extract submission fields from the incoming email body AND its attached ACORDs/loss-runs/quotes" pattern and by far the most common shape in production.

### Config keys

Mirrors `ExtractFromMultipleSourcesConfig`.

| Key | Type | Default | Purpose |
| --- | --- | --- | --- |
| `extraction_schema` | JSON Schema (`type: "object"` with `properties`) | required | Fields to extract. Leaves can be any primitive; nested objects and arrays are allowed. |
| `email_system_prompt` | string \| null | `null` | LEGACY config slot. Do NOT set here — pass via `input_mappings`. The config slot exists for back-compat. |
| `document_system_prompt` | string \| null | `null` | Same — pass via `input_mappings` only. |
| `model` | AIModel | workflow default | Tie-breaker / consolidation model. `gpt-5.5` for accuracy-critical consolidation; `gpt-5.1` for high-volume/simple schemas. |
| `reasoning_effort` | enum \| null | `null` | Only honored for reasoning models; rejected on non-reasoning models at save. |
| `verbosity` | enum \| null | `null` | GPT-5-only. Same validator. |
| `llm_extraction_model` | AIModel \| null | `null` (use Reducto) | When set, bypasses Reducto for the documents branch and uses a direct LLM. Requires EITHER leaving this null OR turning off `generate_citations` — LLM extraction doesn't emit Reducto-shaped citations. Gemini 2.5 Flash/Pro are also offered here. |
| `generate_citations` | bool | `true` | When `true`, every extracted leaf gets a parallel citations array on the cell (`data.<field>.citations`). |
| `show_confidence_score` | bool | `true` | UI-only — per-cell `confidence_score` chip. |
| `use_agentic_tie_breaker` | bool | `false` | When `true`, consolidation is allowed to call back into the documents (requires a KB upstream). More expensive; useful when sources disagree often. |
| `spreadsheet_agent` | bool | `false` | When `true` AND the document is .xlsx/.xls, routes through Reducto's spreadsheet agent. No-op on PDFs/emails. |
| `use_fastest_extraction` | bool | `false` | Reducto "fast" mode. Mutually exclusive in practice with `use_expensive_extraction`. |
| `use_expensive_extraction` | bool | `false` | Reducto "accurate" mode. Use for complex layouts. |
| `deep_extract` | bool | `false` | Reducto v3 deep extract. Significantly more expensive — only for known-hard documents (typically loss runs). Requires `reducto_version: "v3"` (workflow-level or overridden here). |
| `reducto_version` | `"v2" \| "v3" \| null` | `null` (workflow default) | Per-step override. Keep the workflow default at `"v2"`; flip individual steps to `"v3"` when you need `deep_extract`. |

### Output schema

```json
{
  "type": "object",
  "description": "TableV1 structure with multi-source extraction",
  "properties": {
    "schema":         {"type": "object"},
    "data":           {"type": "object"},
    "user_documents": {"type": "array"},
    "metadata":       {"type": "object"}
  }
}
```

Each leaf in `data` is cell-wrapped at runtime — `{value, confidence_score, confidence_reason, citations, thinking_steps}`. The save-time validator synthesizes this wrap when the path you reference ends in one of those five names, so `data.broker_name.value` and `data.broker_name.citations.0.content` resolve correctly without expanding the schema by hand.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is already there.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

Parameters available in `input_mappings`:

| Parameter | Type | Effective rule |
| --- | --- | --- |
| `documents` | `array<file>` | required if no email input is mapped |
| `emails` | `array` | grouped per-email bundles from `Prepare Documents.emails` — carries per-email doc IDs |
| `eml_user_document_id` | `string` | required if scalar email inputs are mapped (and `emails` is not) |
| `headers` | `string`/`email_headers` | wire alongside `email_body`/`eml_user_document_id` for the scalar email branch |
| `email_body` | `string` | the email body HTML/plain |
| `subject` | `string` | optional |
| `body_plain` | `string` | optional |
| `body_html` | `string` | optional |
| `email_system_prompt` | `string` | required if any email input is mapped |
| `document_system_prompt` | `string` | required if `documents` is mapped |

`extraction_schema` is NOT in `input_mappings` — it lives on `step.config`.

The two **system prompts** are always passed as `input_type: "static"`. Their canonical shape is a multi-paragraph Markdown prompt that names the schema's keys verbatim and gives formatting instructions (currencies with `$` and commas, dates as `MM/DD/YYYY`, etc.).

### Common patterns

#### 1) Documents-only extraction from a single upstream classification

Most common shape: documents come from `Prepare Documents` or from a specific class bucket on `Classify Documents`; the email branch is unused.

```json
{
  "name": "Extract Insured Information",
  "type": "extract_from_multiple_sources",
  "input_mappings": [
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Prepare Documents", "output_attribute": "documents" }
        ],
        "resolution_operator": null,
        "resolution_config": null
      },
      "input_parameter_name": "documents"
    },
    {
      "input_type": "static",
      "value": "Extract insured details from the attached submission packet ...",
      "input_parameter_name": "document_system_prompt"
    }
  ],
  "config": {
    "extraction_schema": { "type": "object", "properties": { "insured_name": {"type": "string", "title": "Insured Name"}, "effective_date": {"type": "string", "title": "Effective Date"} } },
    "model": "gpt-5.1",
    "generate_citations": true,
    "show_confidence_score": true
  }
}
```

#### 2) Email-only extraction (replaces deprecated `extract_from_email`)

Scalar email branch active, no documents. `eml_user_document_id` MUST be wired (citation linkage rule).

```json
{
  "name": "Extract Email Routing Fields",
  "type": "extract_from_multiple_sources",
  "input_mappings": [
    {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Prepare Documents", "output_attribute": "eml_user_document_id"}], "resolution_operator": null, "resolution_config": null}, "input_parameter_name": "eml_user_document_id"},
    {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Prepare Documents", "output_attribute": "headers"}], "resolution_operator": null, "resolution_config": null}, "input_parameter_name": "headers"},
    {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Prepare Documents", "output_attribute": "email_body"}], "resolution_operator": null, "resolution_config": null}, "input_parameter_name": "email_body"},
    {"input_type": "static", "value": "You are extracting submission routing fields from an incoming broker email ...", "input_parameter_name": "email_system_prompt"}
  ],
  "config": {
    "extraction_schema": { "type": "object", "properties": { "email_subject": {"type": "string", "title": "Email Subject"}, "email_sender_person_email": {"type": "string", "title": "Sender Email"} } },
    "model": "gpt-5.1",
    "generate_citations": true
  }
}
```

#### 3) Email + documents, both branches (the canonical submission-intake shape)

Both branches active, classification picks specific document buckets, two static prompts (one per branch). Tie-breaker consolidates per-field.

```json
{
  "name": "Extract Submission Details",
  "type": "extract_from_multiple_sources",
  "input_mappings": [
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Classify Docs", "output_attribute": "documents.Loss_Run" },
          { "step_name": "Classify Docs", "output_attribute": "documents.Application" }
        ],
        "resolution_operator": "concat_lists",
        "resolution_config": null
      },
      "input_parameter_name": "documents"
    },
    {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Prepare Documents", "output_attribute": "eml_user_document_id"}], "resolution_operator": null, "resolution_config": null}, "input_parameter_name": "eml_user_document_id"},
    {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Prepare Documents", "output_attribute": "headers"}], "resolution_operator": null, "resolution_config": null}, "input_parameter_name": "headers"},
    {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Prepare Documents", "output_attribute": "email_body"}], "resolution_operator": null, "resolution_config": null}, "input_parameter_name": "email_body"},
    {"input_type": "static", "value": "You are an insurance intake agent. Extract submission fields from the email body and thread headers ...", "input_parameter_name": "email_system_prompt"},
    {"input_type": "static", "value": "Validate / fill missing fields against the attached ACORDs and loss runs ...", "input_parameter_name": "document_system_prompt"}
  ],
  "config": {
    "extraction_schema": { "type": "object", "properties": { "broker_first_name": {"type": "string", "title": "Broker First Name"}, "broker_email": {"type": "string", "title": "Broker Email"} } },
    "model": "gpt-5.5",
    "use_agentic_tie_breaker": false,
    "generate_citations": true
  }
}
```

### Common validation errors and fixes

| Error message | Fix |
| --- | --- |
| `must map at least one of 'documents' OR an email input ('emails', 'eml_user_document_id', 'headers', or 'email_body').` | Wire one of them. Both branches is also fine. |
| `'documents' is mapped, so 'document_system_prompt' must be a non-empty static value or dependency.` | Add a `document_system_prompt` mapping. Static prompt is the canonical shape. |
| `email inputs are mapped, so 'email_system_prompt' must be a non-empty static value or dependency.` | Add an `email_system_prompt` mapping. |
| `wire 'eml_user_document_id' when email inputs are mapped.` | Scalar email inputs need `eml_user_document_id` for citation linkage (grouped `emails` does not). |
| `Parameter '<name>' does not allow mode 'static'/'dependency'.` | Some parameters restrict the allowed `input_type`. Check the mode you used. |
| `Output attribute '<path>' not found in step '...' output schema. Available top-level properties: [...]` | Glom path doesn't resolve in the upstream output. For extraction-step sources remember `.value` is required on primitive leaves. |
| `Parameter expects type X but step Y outputs type Z` | Common case: wiring `documents` from `Classify Documents.documents` (type `object`) instead of `Classify Documents.documents.<ClassName>` (type `array`). |
| `llm_extraction_model is set, so generate_citations must be False` (or similar) | Either unset `llm_extraction_model` (use Reducto, which emits citations) OR set `generate_citations: false`. |
| `reasoning_effort/verbosity only allowed on reasoning models` | Drop the field, or switch `model` to a reasoning model. |
| Inline-code step error: `imports 'extract_from_multiple_sources' — use the step type instead.` | Don't call the extraction block from step code. Create a real step. |

### Common gotchas

- **System prompts MUST go through `input_mappings`, not `config`.** Even though `email_system_prompt` and `document_system_prompt` exist as config fields, they're legacy slots — the live runtime path reads them from `input_mappings`.
- **`email_system_prompt` becomes required the moment ANY email input is mapped** — even if the only email input you wired is `email_body`. The same goes for `document_system_prompt` when `documents` is mapped. Don't try to "leave one branch unprompted".
- **Citations are a parallel `<field>.citations` array on the cell, not a separate column.** At the data layer there's a single cell-wrapped leaf with `value` + `citations` as siblings. Downstream paths: `data.<field>.value`, `data.<field>.citations`, `data.<field>.citations.0.content`.
- **Citations indexing:** `data.<field>.citations` is an array of `citation` objects (`{type, content, bbox, confidence}`). Index numerically to pick a specific citation; descend into `.content` for the text snippet, `.bbox.page` for the source page.
- **Schema property names propagate to downstream steps verbatim.** A downstream step that reads `data.broker_email.value` breaks if you rename `broker_email` → `broker_email_address`. Treat schema keys as a public contract.
- **`extraction_schema` keys are NOT the workflow's display columns.** Display columns live separately on `workflow.columns`.
- **Don't wire `documents` from bare `Classify Documents.documents`.** That path is type `object` (the dict-of-arrays), not `array`. Always descend to a class bucket: `Classify Documents.documents.<ClassName>`, optionally combined via `concat_lists`.
- **`deep_extract: true` forces Reducto v3 cost.** Only enable when you can name the specific class of documents that need it (loss runs, dense schedule pages).
- **Group related fields as objects.** When extracting related fields (broker name + phone + email), group them as a single object field to prevent the model mixing values across sources; state "must extract from same source" in the field description and tiebreaker instructions.

### See also

- `extract_rows_from_multiple_sources` — when you need MULTIPLE rows per doc (claims schedules, driver lists, census). Output is an array, not an object.
- `extract_mixed_schema_from_multiple_sources` — when the schema is mixed (object fields + array fields). Common for "header info + line items" shapes.
- `agentic_extraction` — when the schema is dynamic, when you need RAG-style search across a KB, or when the agent needs tool use. Requires a `knowledge_base` step upstream.
- `extract_from_document` / `extract_from_email` (both DEPRECATED) — legacy variants; this step is the migration target for both.
- `combine_kv_tables` — merge several KV extractions into one table downstream.
