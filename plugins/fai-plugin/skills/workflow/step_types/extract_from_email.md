# extract_from_email

> **DEPRECATED — DO NOT CREATE NEW INSTANCES.** Hidden from the builder's step picker; the bundled validator errors on new instances. If asked to create one, use `extract_from_multiple_sources` with email inputs (`email_body`, `headers`, `eml_user_document_id` + the required `email_system_prompt`). See the migration section below. Editing existing instances is allowed — live instances sit predominantly on submission-intake workflows that route a broker email through `Prepare Documents` and split out per-domain extraction steps (broker info, underwriter info, expiring coverage).

### What this step does

Runs schema-guided LLM extraction over the **email body and headers only** (no attached documents). Output is the standard cell-wrapped TableV1 envelope: `{schema, data, user_documents, metadata}` with each leaf in `data` shaped as `{value, confidence_score, confidence_reason, citations, thinking_steps}`. Downstream consumers wire `data.<field>.value`. The output shape is **identical to `extract_from_multiple_sources`** — that's what makes the migration mechanical (downstream consumers don't change).

### When you'll encounter this

The canonical pattern on existing workflows:

- `dependencies: [{"step_name": "Prepare Documents", "field_selector": null}]` and **`input_mappings: []`**. The runtime resolves email parameters (`subject`, `body_html`, `body_plain`, `headers`, `eml_user_document_id`) by name from `Prepare Documents`' output.
- `config.system_prompt` set inline on `config` (NOT via input_mappings); `extraction_schema` also on `config`.
- Models: typically `gpt-4o` or `gpt-5.1`. `generate_citations: true`, `show_confidence_score: true` are the defaults.
- Each step extracts a tightly-scoped sub-domain (e.g. `marketer_uw_name`, or `{broker_office, broker_email, broker_phone}`) rather than one mega-schema.

### Migration to `extract_from_multiple_sources` (RECOMMENDED)

If the user asks to "extend", "rebuild", or "add a new email extraction", convert the step to `extract_from_multiple_sources` in the same edit. The output shape is identical, so downstream consumers that reference `data.<field>.value` keep working unchanged — only the step `type`, `config` shape, and input wiring change.

Equivalent `extract_from_multiple_sources` step (drop-in replacement):

```json
{
  "name": "Extract Broker Information",
  "type": "extract_from_multiple_sources",
  "input_mappings": [
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Prepare Documents", "output_attribute": "eml_user_document_id" }
        ],
        "resolution_operator": null,
        "resolution_config": null
      },
      "input_parameter_name": "eml_user_document_id"
    },
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Prepare Documents", "output_attribute": "headers" }
        ],
        "resolution_operator": null,
        "resolution_config": null
      },
      "input_parameter_name": "headers"
    },
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Prepare Documents", "output_attribute": "email_body" }
        ],
        "resolution_operator": null,
        "resolution_config": null
      },
      "input_parameter_name": "email_body"
    },
    {
      "input_type": "static",
      "value": "<copy-paste the old config.system_prompt verbatim here>",
      "input_parameter_name": "email_system_prompt"
    }
  ],
  "dependencies": [],
  "config": {
    "extraction_schema": "<copy-paste the old extraction_schema unchanged>",
    "model": "gpt-5.1",
    "generate_citations": true,
    "show_confidence_score": true
  }
}
```

Migration notes:

1. Move `config.system_prompt` → an `input_mappings` entry named `email_system_prompt` (`input_type: "static"`).
2. Wire `eml_user_document_id` even when the prompt only references the body — the save-time validator requires it for citation linkage when scalar email inputs are mapped.
3. `extraction_schema` stays on `config` unchanged.
4. Drop the legacy `dependencies: [Prepare Documents]` once inputs are wired through `input_mappings` — execution ordering is then inferred from the dependency graph.

### Required inputs (for editing an existing instance)

| Parameter | Type | Required | Notes |
| --- | --- | --- | --- |
| `system_prompt` | string | yes | Legacy slot: existing instances place this in `config.system_prompt`. Leave it wherever it already is. |
| `subject` | string | yes | Resolved from `Prepare Documents.subject` when the step uses the legacy dependencies-only wiring. |
| `body_html` | string | yes | Resolved from `Prepare Documents.body_html`. |
| `body_plain` | string | no | Optional. |
| `eml_user_document_id` | string | de-facto required for citations | Without it, runtime falls back to scanning the execution log for any `.eml`/`.msg` — unreliable on manual reruns. |
| `generate_citations` | boolean | no | Defaults to `true`. |

`extraction_schema` is **not** in input_mappings — it lives on `step.config`.

### Config keys

Mirrors `ExtractFromEmailConfig`.

| Key | Type | Default | Purpose |
| --- | --- | --- | --- |
| `extraction_schema` | JSON Schema (`type: "object"` with `properties`) | required | Fields to extract from the email. |
| `system_prompt` | string \| null | `null` | Legacy slot. Existing instances set this in `config`; do not move it on edit unless asked. |
| `model` | AIModel | workflow default | Existing instances run `gpt-4o` or `gpt-5.1`. |
| `reasoning_effort` | enum \| null | `null` | Only honored for reasoning models; rejected otherwise at save. |
| `verbosity` | enum \| null | `null` | GPT-5-only. Same validator. |
| `generate_citations` | bool | `true` | Cell-level citations on every extracted leaf. |
| `show_confidence_score` | bool | `true` | UI-only — per-cell `confidence_score` chip. |

### Output schema

```json
{
  "type": "object",
  "properties": {
    "schema":         {"type": "object"},
    "data":           {"type": "object"},
    "user_documents": {"type": "array"},
    "metadata":       {"type": "object"}
  }
}
```

Downstream wiring path: `data.<field>.value`. The save-time validator synthesizes the cell wrap when the path ends in `value` / `citations` / `confidence_score` / `confidence_reason` / `thinking_steps`. **Same shape as `extract_from_multiple_sources`** — migration doesn't ripple to downstream consumers.

### Input wiring (input_mappings vs dependencies)

- NEW steps: N/A — you cannot author a new `extract_from_email`.
- EXISTING steps: preserve whatever pattern is already there. Nearly all existing instances use the **legacy `dependencies` wiring** with `input_mappings: []`. Leave it alone unless the user explicitly asks to migrate.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden. If the user asks for that migration, recommend converting the whole step to `extract_from_multiple_sources` at the same time — the work is essentially the same and you end up off the deprecated type.

### Common patterns

#### 1) Single-field underwriter extraction (most common)

```json
{
  "name": "Extract Underwriter Information",
  "type": "extract_from_email",
  "input_mappings": [],
  "dependencies": [{"step_name": "Prepare Documents", "field_selector": null}],
  "config": {
    "extraction_schema": {
      "type": "object",
      "properties": {
        "marketer_uw_name": {
          "type": "string",
          "title": "Marketer or Underwriter Name",
          "description": "Underwriter's full name handling the submission. Identify by the carrier email domain in the From/To/CC headers; DO NOT extract the broker's name. If not found, return 'Not Provided'."
        }
      }
    },
    "system_prompt": "# Email Thread Analysis — Underwriter Extraction\n\nYou are extracting the primary underwriter handling this submission from the email thread...",
    "model": "gpt-4o",
    "reasoning_effort": null,
    "verbosity": null,
    "generate_citations": true,
    "show_confidence_score": true
  }
}
```

#### 2) Multi-field broker contact extraction

```json
{
  "name": "Extract Broker Information",
  "type": "extract_from_email",
  "input_mappings": [],
  "dependencies": [{"step_name": "Prepare Documents", "field_selector": null}],
  "config": {
    "extraction_schema": {
      "type": "object",
      "properties": {
        "broker_office":  {"type": "string", "title": "Broker Office", "description": "Broker office / brokerage name from the signature or sender domain."},
        "broker_email":   {"type": "string", "title": "Broker Email", "description": "Sender's email address."},
        "broker_phone":   {"type": "string", "title": "Broker Phone", "description": "Phone number from the broker's signature."}
      }
    },
    "system_prompt": "# Email Thread Analysis — Broker Extraction\n\nExtract the main broker's contact information from the email thread...",
    "model": "gpt-5.1",
    "generate_citations": true,
    "show_confidence_score": true
  }
}
```

#### 3) Renewal / expiring-coverage extraction with nested array

Same wiring as patterns 1 & 2; schema mixes scalars with a nested array (the extractor handles `array.items.properties` natively):

```json
"extraction_schema": {
  "type": "object",
  "properties": {
    "is_renewal":              {"type": "boolean"},
    "current_carrier":         {"type": "string"},
    "expiring_coverage_lines": {
      "type": "array",
      "items": {"type": "object", "properties": {
        "line_name": {"type": "string"}, "limit": {"type": "string"}, "deductible": {"type": "string"}
      }}
    }
  }
}
```

### Common validation errors and fixes

| Error message | Likely cause | Fix |
| --- | --- | --- |
| `invalid step type` / deprecated-type error on a new instance | You authored a new `extract_from_email`. | Don't. Author `extract_from_multiple_sources` with email inputs + `email_system_prompt`. |
| `Missing required input mappings: ['subject']` / `['body_html']` / `['system_prompt']` | Step has `input_mappings: []` but no upstream `Prepare Documents` dependency to source those parameters by name. | Restore the legacy `dependencies: [{"step_name": "Prepare Documents", "field_selector": null}]`, OR wire each parameter explicitly via `input_mappings`. |
| `Output attribute 'body_html' not found in step 'Prepare Documents' output schema.` | The upstream step isn't actually a `prepare_documents` step, or its output schema was customized. | Check the upstream step type. If it's email-shaped, the email parameters should all be present. Otherwise switch to `extract_from_multiple_sources` and wire `email_body` from the actual upstream attribute. |
| `reasoning_effort/verbosity only allowed on reasoning models` | `config.model` is a non-reasoning model but `reasoning_effort` or `verbosity` is set. | Drop the offending field, or switch `model` to a reasoning model. |
| Downstream step error: `data.broker_email` resolved to type `cell`, expected `string` | Downstream consumer wired `data.<field>` instead of `data.<field>.value`. | Append `.value` to the path. |

### Common gotchas

- **Do NOT create a new instance.** The builder picker hides it and the bundled validator errors on it. If you're about to author one from scratch, stop and switch to `extract_from_multiple_sources` with email inputs.
- **`config.system_prompt` is the legacy slot — existing instances use it.** Don't "modernize" to `input_mappings` unless the user explicitly asks. If they do, recommend converting the entire step to `extract_from_multiple_sources` in the same edit.
- **Legacy `dependencies`-only wiring is the norm here.** Existing instances typically have `input_mappings: []` and a single `dependencies: [Prepare Documents]` entry; the runtime resolves email parameters by name. Don't try to "fix" this — preserve it on edit.
- **Citations depend on `eml_user_document_id` being resolvable.** Satisfied automatically by the `Prepare Documents` dependency. Keep that dependency.
- **Schema property names are a public contract.** Downstream steps reference `data.<field>.value`; renaming a key breaks every downstream wire.
- **No `documents` / no Reducto.** This step type has no document branch — `llm_extraction_model`, `use_expensive_extraction`, `deep_extract`, `spreadsheet_agent` are not config keys here. If the user wants document extraction too, that's `extract_from_multiple_sources` territory.

### See also

- **`extract_from_multiple_sources`** — MODERN REPLACEMENT. Use it with email inputs (`email_body`, `headers`, `eml_user_document_id`) + `email_system_prompt` for new email-only extraction, and add `documents` + `document_system_prompt` whenever you also want attachment extraction in the same step.
- `extract_from_document` (also deprecated) — single-doc legacy variant. Same migration target.
- `prepare_documents` — produces the `subject` / `body_html` / `body_plain` / `headers` / `eml_user_document_id` outputs that this step consumes via the legacy dependencies wiring.
