# fill_docx

### What this step does

Resolves the `template` input to a `UserDocument`, downloads it, and substitutes `{{placeholder}}` markers in the document with values from the `context` dict (Apryse under the hood). Any `docx_images` entries are inserted as inline/background images at their declared placeholder positions with the supplied resize/position settings. The result is saved as a new `UserDocument` and surfaced as `generated_document` on the step's output.

The step always returns exactly one `generated_document` — the singular path. Downstream steps wire the document via `generated_document.user_document_id` (and `generated_document.filename` for display).

If `config.convert_to_pdf=true`, the filled DOCX is converted to PDF before save; the `UserDocument.filename` reflects the `.pdf` extension. The `template` itself must be a `.docx` — a `.doc` or `.pdf` template fails at runtime.

The step is often gated behind a `decision` step (one `fill_docx` per output variant, fanned out from a LoB / section / class router), but works fine as a single unconditional final step too.

### Required inputs

| Param             | Type            | Required | Notes                                                                                                                                                  |
| ----------------- | --------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `template`        | object          | YES      | `{user_document_id, filename}`-shaped document reference. The `.docx` to fill. Wired from an upstream step, OR as a **build-time static file reference** (`input_type: "static"` with `value: {"user_document_id": "<id>", "filename": "<name>"}`) when the template is fixed for every run. |
| `context`         | object          | YES      | Flat or nested dict whose keys match the `{{placeholder}}` markers inside the template. Almost always wired from an inline-code step that shapes data.  |
| `docx_images`     | object (map)    | no       | `{placeholder_name: {document: {user_document_id, filename}, settings: {position, resize_mode, target_height_inches, target_width_inches}}}`.           |
| `output_filename` | string          | no       | Per-execution override for the generated file's name. If omitted the block derives one from the template filename + execution id.                       |

`convert_to_pdf` is the only `config`-level field — everything else lives in `input_mappings`.

### Config keys

From `FillDocxConfig`:

| Key              | Type | Default | Notes                                                                                                          |
| ---------------- | ---- | ------- | -------------------------------------------------------------------------------------------------------------- |
| `convert_to_pdf` | bool | `false` | When `true`, the generated DOCX is converted to PDF before being saved as a `UserDocument`.                    |

### Output schema

```json
{
  "type": "object",
  "properties": {
    "generated_document": {
      "type": "object",
      "properties": {
        "user_document_id": { "type": "string", "description": "UserDocument ID" },
        "filename":         { "type": "string", "description": "Filename of the generated document" }
      }
    }
  }
}
```

Downstream consumers reference fields via the singular nested path:

- `generated_document.user_document_id` — for downstream steps that need the document ID (e.g. an `email` step's `attachment_document_ids` — via a small `custom_step` that wraps it into `[id]`, since that input expects an array).
- `generated_document.filename` — useful for `subject` interpolation or display titles.

Do NOT wire downstream consumers from a `generated_documents.*` (plural) path — that's a legacy batch shape, not this step's output.

The backend auto-fills `output_type` and `output_config` on fill_docx so the UI renders the "View Document" tile on the canvas. Don't set these by hand.

### Input wiring (input_mappings vs dependencies)

- **NEW steps**: use `input_mappings` only; `dependencies` always `[]`. The typical shape maps `template`, `context`, `docx_images`, `output_filename` each from an upstream step's output attribute.
- **EXISTING steps**: preserve whatever pattern is there. The dominant pattern is a single upstream inline-code step (named e.g. `prepare_fill_docx_inputs` or `Prepare Final Context`) that emits all fields at once, with fill_docx mapping each one independently.
- **Migration** `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

### Common patterns

#### Pattern A — Simple template fill (one upstream step, no images, no PDF)

The dominant shape. A single upstream step (`prepare_fill_docx_inputs`) consolidates extracted data into `template`, `context`, `output_filename`, then fill_docx maps each. Used heavily in section-by-section or LoB-by-LoB document generation where one `fill_docx` instance lives per decision branch.

```json
{
  "name": "Fill DOCX Template",
  "type": "fill_docx",
  "input_mappings": [
    { "input_type": "dependency", "input_parameter_name": "template",
      "value": { "dependency_step_outputs": [ { "step_name": "prepare_fill_docx_inputs", "output_attribute": "template" } ] } },
    { "input_type": "dependency", "input_parameter_name": "context",
      "value": { "dependency_step_outputs": [ { "step_name": "prepare_fill_docx_inputs", "output_attribute": "context" } ] } },
    { "input_type": "dependency", "input_parameter_name": "output_filename",
      "value": { "dependency_step_outputs": [ { "step_name": "prepare_fill_docx_inputs", "output_attribute": "output_filename" } ] } }
  ],
  "dependencies": [],
  "config": { "convert_to_pdf": false }
}
```

#### Pattern B — Template fill with image attachments (cover photos, signature stamps)

For proposals and reports that embed dynamic imagery. A separate upstream step (`Prepare Docx Images`) emits a `docx_images` dict keyed by template placeholder name; each value carries the source `UserDocument` reference and rendering settings.

```json
{
  "name": "Fill Proposal Template",
  "type": "fill_docx",
  "input_mappings": [
    { "input_type": "dependency", "input_parameter_name": "template",
      "value": { "dependency_step_outputs": [ { "step_name": "Get Template URL", "output_attribute": "template" } ] } },
    { "input_type": "dependency", "input_parameter_name": "context",
      "value": { "dependency_step_outputs": [ { "step_name": "Analyze Cover Image Brightness", "output_attribute": "context" } ] } },
    { "input_type": "dependency", "input_parameter_name": "output_filename",
      "value": { "dependency_step_outputs": [ { "step_name": "Generate Proposal Filename", "output_attribute": "output_filename" } ] } },
    { "input_type": "dependency", "input_parameter_name": "docx_images",
      "value": { "dependency_step_outputs": [ { "step_name": "Prepare Docx Images", "output_attribute": "docx_images" } ] } }
  ],
  "config": { "convert_to_pdf": false }
}
```

The `docx_images` payload shape:

```json
{
  "cover_photo": {
    "document": { "user_document_id": "...", "filename": "cover.png" },
    "settings": { "position": "original", "resize_mode": "fit_width", "target_width_inches": 7.5 }
  }
}
```

`position` ∈ `{"original", "top_left"}`. `resize_mode` ∈ `{"original", "fit_width", "fit_height", "fixed"}`. For `"fixed"` you must supply both `target_height_inches` and `target_width_inches`.

#### Pattern C — Fill + convert to PDF for email delivery

Same wiring as Pattern A but `convert_to_pdf=true`, and the generated document is fed into a downstream `email` step as an attachment. Set `output_filename` explicitly so the recipient sees a meaningful name; without it the file inherits the template's name with the execution id suffixed.

The downstream `email` step wires `attachment_document_ids` from a `custom_step` that wraps `generated_document.user_document_id` into `[user_document_id]` (an array).

### Common validation errors and fixes

There is no fill_docx-specific cross-field validator — failures come from the generic input-completeness check.

- **`Step '<name>': missing required input 'template'` (or `'context'`)** — add a DEPENDENCY-typed (or static file reference, for `template`) `input_mappings` entry for the missing parameter. `template` and `context` are both required; `docx_images` and `output_filename` are optional.
- **`Step '<consumer>': dependency step '<this_step>' has no output attribute 'user_document_ids'`** — the consumer was wired from `user_document_ids` (the `email` step's attachment input name) instead of the actual fill_docx output path `generated_document.user_document_id`. Fix the consumer's `output_attribute`; if it expects an array of IDs, insert a small `custom_step` in between that returns `{"attachment_document_ids": [generated_document["user_document_id"]]}`.
- **`docx_images` dict shape mismatch** — no save-time validation; mismatches surface at runtime as "could not locate image placeholder X" or a pydantic `ValidationError`. Confirm every placeholder name in your `docx_images` dict matches a `{{X}}` marker in the template (case-sensitive), and every value has at least `document.user_document_id`.

### Common gotchas

- **Output is `generated_document` (singular), not `generated_documents`.** Wire downstream from `generated_document.user_document_id`. `user_document_ids` is the `email` step's INPUT name, not a fill_docx output.
- **Placeholder syntax is `{{name}}`** — double curlies. If a `{{key}}` is in the template but missing from `context`, the literal `{{key}}` remains in the output (no error). Always cross-check your context dict against the template's placeholders before shipping.
- **Template must be a `.docx`, not a `.doc` or `.pdf`.** A `.doc` upload fails at runtime with an opaque error. Validate the template's extension when it's first uploaded.
- **`context` values that are lists/dicts behave differently from strings.** A list value can drive a `{{#section}}…{{/section}}` loop if the template's been authored for it. If the template just has a flat `{{value}}` and you pass a list, you'll see `['a', 'b']`-style repr in the output. Shape `context` to match how the template's placeholders are authored.
- **`output_filename` without an extension gets the right extension appended.** `"ProposalForAcme"` with `convert_to_pdf=true` saves as `ProposalForAcme.pdf`; a literal `.docx` suffix is replaced with `.pdf`.
- **`docx_images` values must be `{document: {...}, settings: {...}}`-shaped objects — not a bare image URL.** A raw blob URL is rejected at runtime.
- **The `email` step's `attachment_document_ids` is an array.** Fill_docx emits a single ID — wrap it.
- **PDF conversion can fail silently on templates with broken fonts/embeds.** The DOCX path almost always succeeds even when the PDF path would fail; fall back to DOCX if you can't get clean PDF output.
- **One `fill_docx` per branch is fine; one shared `fill_docx` for many branches is also fine.** `parent_conditions` drives which branch(es) reach the step. Choose the shape based on whether the template/context differs per branch.

### See also

- `custom_step` — the standard upstream for `fill_docx`: shapes the extracted-data table(s) and document references into the `template` + `context` + `docx_images` + `output_filename` set. When images and text context are prepared separately you'll see two upstreams (Pattern B). (For Excel templates, use `custom_step` + `excel.fill` instead — see custom_step.md.)
- `email` — the standard downstream when the document is delivered to a human. Wire `attachment_document_ids` (array) from a thin `custom_step` wrapper.
- `manual_input` — when the template (or a cover image) is supplied by the user at runtime, a `manual_input` step's file field becomes the source for `template` or an entry inside `docx_images` (the `docx_image` field type exists for exactly this).
