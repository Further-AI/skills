# manual_input

### What this step does

When the executor reaches a `manual_input` step, it parks the step at `IN_PROGRESS`, promotes the workflow status to `PAUSED`, and surfaces the form defined in `config.input_schema` in the UI. The workflow stays paused until the user submits the form (or cancels). On submit, the resolved values are spread **directly** into the step's output (NO `user_inputs` wrapper), and execution resumes.

Model details that bite if you forget them:

- Field types (`type`) are the platform-local `DataType` enum: `text`, `number`, `boolean`, `file`, `file_list`, `docx_image`. NOT plain JSON-Schema primitives — `text` becomes `string` on output, `file` becomes a document-shaped object, `docx_image` becomes a nested `{document, settings}` object.
- Input modes (`input_type`) are the platform-local enum: `free`, `dropdown`, `multi_select`. `multi_select` wraps the field's output type in an array.
- All fields are required by default — there is no per-field `optional` flag.
- `dropdown` / `multi_select` options need stable `id`s. Missing `id`s are auto-filled on save; `value` is what flows downstream (the id is just selection bookkeeping).
- Step's `output_type` is usually `"simple"` (or `null`) and `output_schema` stays `null` — the output schema is dynamically generated from `input_schema`. `incremental_config.behavior` defaults to `"rerun"` (the right default for forms).

### Required inputs

The form fields the USER fills in are described by `config.input_schema`, NOT by `input_mappings`. `input_mappings` on a `manual_input` step exist only to pass data from upstream steps that the form needs to render (e.g. a pre-filled dict the UI uses to populate defaults). Typically empty.

### Config keys

From `ManualInputConfig`:

| Key                         | Type                       | Required | Notes                                                                                                                              |
| --------------------------- | -------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `message`                   | `string` or `null`         | no       | Title shown above the form when the workflow pauses.                                                                               |
| `description`               | `string` or `null`         | no       | Subtitle under `message`.                                                                                                          |
| `input_schema`              | `Dict[str, Any]` or `null` | YES (effectively) | JSON-Schema-shaped (`type: "object"`, `properties: {...}`). Every property describes one form field. If omitted, the step has no fields (rarely useful). |
| `submit_button_text`        | `string` or `null`         | no       | Overrides the default `"Submit"` button label.                                                                                     |
| `cancel_button_text`        | `string` or `null`         | no       | Overrides the default `"Cancel"` button label.                                                                                     |
| `show_extra_submit_button`  | `boolean`                  | no       | When `true`, renders a third button alongside Submit/Cancel. Used for Yes/No/Skip-style gates.                                     |
| `extra_submit_button_text`  | `string` or `null`         | no       | Label for the third button (only rendered when `show_extra_submit_button` is `true`).                                              |

#### Input field types (`type`)

| Field `type` | What the user does                                                            | Output value shape                                                                                              |
| ------------ | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `text`       | Type a string, or pick from a `dropdown` / `multi_select` of string options.  | `string` (or `array<string>` for `multi_select`).                                                                |
| `number`     | Type a number, or pick numeric option(s).                                     | `number` (or `array<number>` for `multi_select`).                                                                |
| `boolean`    | Toggle a checkbox.                                                            | `boolean`.                                                                                                       |
| `file`       | Upload one file, or pick one from a `dropdown` of pre-registered documents.   | `{user_document_id, filename, category: "manual_input"}` — a document-shaped object.                             |
| `file_list`  | Upload multiple files, or pick a list from a dropdown.                        | `array<{user_document_id, filename, category}>`.                                                                 |
| `docx_image` | Pick (or upload) an image with rendering settings for `fill_docx` substitution. | `{document: {user_document_id, filename}, settings: {position, resize_mode, target_width_inches, target_height_inches}}`. |

#### Input modes (`input_type`)

| `input_type`   | What it renders                                              | Notes                                                                                                       |
| -------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `free`         | Free-form text box / number box / file upload control.       | No `options` array.                                                                                          |
| `dropdown`     | Single-select picker.                                        | `options: [{id?, name, value}]` required — `id` auto-generated if omitted.                                   |
| `multi_select` | Multi-select picker.                                         | Same `options` shape. Output is always an array of the underlying field type.                                |

#### Dropdown / multi_select options

```json
{
  "options": [
    { "id": "auto-generated-uuid", "name": "Display Label", "value": "<what flows downstream>" }
  ]
}
```

- `name` is shown in the UI; `value` is what propagates to downstream steps.
- For `file` / `file_list` / `docx_image` options, `value` is a structured object (see Pattern A), NOT a string.

### Output schema

Static envelope:

```json
{
  "type": "object",
  "properties": {
    "status": { "type": "string", "description": "Manual input step status: 'paused' or 'completed'" }
  }
}
```

At validation/runtime the output schema is derived by walking `config.input_schema.properties` and spreading each field directly into the output. A `text/dropdown` field named `layout_type` becomes `{"type": "string"}` at output path `layout_type`; a `file/dropdown` field named `proposal_template` becomes a document object at output path `proposal_template`. Downstream steps wire these by name with `output_attribute: "<field_name>"` or `<field_name>.user_document_id` for files.

Important: **the values are spread directly — there is no `data.` or `user_inputs.` wrapper**. Wiring downstream from `output_attribute: "data.proposal_template"` is the most common mistake; it must be just `"proposal_template"`.

### Input wiring (input_mappings vs dependencies)

- **NEW steps**: use `input_mappings` only; `dependencies` always `[]`. Most `manual_input` steps need no `input_mappings` at all — the form's options are usually static. Only add a mapping when the form needs upstream data to render.
- **EXISTING steps**: preserve whatever pattern is there. Some steps use bare `dependencies: [{"step_name": "...", "field_selector": null}]` to express ordering without consuming data — leave that alone.
- **Migration** `dependencies` → `input_mappings` on explicit request; the reverse is forbidden.

If the only reason to add an `input_mapping` is to force ordering and the form doesn't need the data, don't add a sham mapping — it inflates the visible parameter list for no reason.

### Common patterns

#### Pattern A — file upload via dropdown (template chooser)

User picks a DOCX template from a curated list, plus a footer logo. The `value` of each option is a fully-formed document or `docx_image` payload that downstream `fill_docx` consumes directly.

```json
{
  "name": "Select Proposal Template",
  "type": "manual_input",
  "input_mappings": [],
  "dependencies": [],
  "config": {
    "message": "Please upload the DOCX proposal template and optionally override team member information.",
    "input_schema": {
      "type": "object",
      "properties": {
        "proposal_template": {
          "title": "Proposal Template (DOCX)",
          "type": "file",
          "input_type": "dropdown",
          "description": "Upload the DOCX template file with {{placeholders}} for variable substitution",
          "options": [
            { "name": "Base Template",
              "value": {"user_document_id": "<doc_id>", "filename": "Base Proposal Template.docx", "category": "manual_input"} }
          ]
        },
        "Footer_Image": {
          "type": "docx_image", "input_type": "dropdown", "title": "Footer Logo",
          "options": [
            { "name": "Brand Logo",
              "value": {"document": {"user_document_id": "<img_id>", "filename": "brand-logo.png"},
                        "settings": {"position": "original", "resize_mode": "fit_height", "target_height_inches": 0.3}} }
          ]
        }
      }
    }
  },
  "display_step": true,
  "start_title": "Waiting for template upload...",
  "end_title": "Template received"
}
```

Downstream `fill_docx` wires `template ← Select Proposal Template.proposal_template`, and images per-field with `output_attribute: "Footer_Image"`.

#### Pattern B — multi-select of section names (curate a downstream extraction)

User picks which sections to extract. The output is `array<string>` of the picked option `value`s; a downstream `decision` step gates section-specific extractors on this list.

```json
{
  "name": "Select Sections",
  "type": "manual_input",
  "input_mappings": [],
  "dependencies": [],
  "config": {
    "message": "Review the preview document above and select which sections to include in the final report.",
    "input_schema": {
      "type": "object",
      "properties": {
        "selected_sections": {
          "type": "text",
          "input_type": "multi_select",
          "title": "Sections to Include in Final Report",
          "description": "Select sections to include. Unselected sections will be removed from the final document.",
          "options": [
            {"id": "section_3", "name": "Section 3 - Loss Analysis", "value": "section_3"},
            {"id": "section_4", "name": "Section 4 - Description of Operations", "value": "section_4"},
            {"id": "section_5", "name": "Section 5 - Property Premises", "value": "section_5"}
          ]
        }
      }
    }
  },
  "display_step": true
}
```

Downstream consumers wire `output_attribute: "selected_sections"` (typed `array<string>`) and use `contains` / `not_contains` comparisons in a `decision` step.

#### Pattern C — single dropdown drives a `decision` route

The simplest gate: one `dropdown` choice from a fixed list. A downstream `decision` step routes on `output_attribute: "layout_type"` with an `eq` comparison.

```json
{
  "name": "Choose Layout",
  "type": "manual_input",
  "input_mappings": [],
  "dependencies": [],
  "config": {
    "message": "Please choose a file layout for transformation.",
    "input_schema": {
      "type": "object",
      "properties": {
        "layout_type": {
          "type": "text",
          "input_type": "dropdown",
          "title": "Layout Type",
          "description": "Pick the suitable layout type for file transformation",
          "options": [
            {"name": "Mode A", "value": "ModeA"},
            {"name": "Mode B", "value": "ModeB"}
          ]
        }
      }
    }
  },
  "display_step": true,
  "start_title": "Waiting for confirming the layout",
  "end_title": "Layout confirmed"
}
```

### Common validation errors and fixes

- **`Input should be 'text', 'number', 'boolean', 'file', 'file_list' or 'docx_image' [type=enum]`** — a field's `type` is set to a JSON-Schema primitive (`"string"`, `"integer"`). Fix: use `"text"`, not `"string"`; `"number"`, not `"integer"`.
- **`Input should be 'free', 'dropdown' or 'multi_select'`** — `input_type` is missing or misspelled. Every field MUST have an `input_type`.
- **`Field '<name>' must have both 'type' and 'input_type' attributes`** — both keys are mandatory on every property in `input_schema.properties`.
- **`Output attribute 'data.<field>' not found in step '<src>' output schema`** — downstream step wired with a `data.` prefix. Drop the prefix — manual_input outputs are spread at the top level.
- **`Parameter '<var>' expects type 'array' but step '<src>' outputs type 'string'`** — downstream consumer needs an array; the field is `text/dropdown` (scalar). Change to `multi_select`, or adapt in a `custom_step`.
- **Silent runtime crash on file fields** — `file`/`file_list`/`docx_image` option `value` must be a structured object (not a string id). A string saves fine but the downstream step gets a string where a document object is expected.

### Common gotchas

- **No `user_inputs` wrapper.** Wire downstream as `output_attribute: "<field_name>"` — NOT `"user_inputs.<field_name>"` and NOT `"data.<field_name>"`.
- **Field `type` is platform-local, not JSON Schema.** `"string"` / `"integer"` are rejected on save.
- **`multi_select` always returns an array** — even for a single pick. Downstream consumers expecting a scalar will mismatch type-compat.
- **Dropdown `value` for file fields is a full document object**, not a `user_document_id` string.
- **`status` is the only static output field** (`"paused"` while waiting, `"completed"` after submit). Don't rely on `output_attribute: "status"` as a signal — use a `decision` against an actual form field.
- **No top-level `required` array.** All fields are required; adding `"required": ["..."]` is harmless but redundant.
- **Re-running re-shows the form by default** (`behavior: "rerun"`). Preserving the previous submission is usually NOT what you want for a form.
- **`display_step: true` is the default and the right choice** — hiding the form means the user never sees it and the workflow appears stuck.
- **`show_extra_submit_button` requires `extra_submit_button_text`** — not enforced; the UI renders an unlabeled third button if you forget it.

### See also

- `pause` — use when the workflow just needs human acknowledgement, NOT structured data.
- `hold` — use when the workflow needs the user to review upstream output before continuing (passes input through as output).
- `decision` — the canonical downstream consumer of a Yes/No / dropdown choice from `manual_input`.
- `fill_docx` — the canonical downstream consumer of `file` and `docx_image` fields from a template-chooser form (Pattern A).
