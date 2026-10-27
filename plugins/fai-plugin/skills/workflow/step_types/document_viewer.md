# `document_viewer`

**Render a document inline on the workflow canvas. Rarely used as a standalone step type — usually the `output_type: "document_viewer"` is attached to another step (e.g., `fill_docx`).**

## When to use it

Rare. The typical pattern is to attach `output_type: "document_viewer"` + `DocumentViewerOutputConfig` to a step that produces a document ID (like `fill_docx` or a custom_step), not to create a dedicated `document_viewer` step.

The builder UI may not offer this as a standalone type. If a platform save rejects it, use `fill_docx` (which renders through `output_type: "document_viewer"` by default) or a `custom_step` returning a `user_document_id` with `output_type: "document_viewer"` and a `DocumentViewerOutputConfig`.

## Config

No `*Config` model. The `DocumentViewerOutputConfig` does the work.

## Inputs

Depend on wiring — typically a `document_id` field via `input_mappings` (by convention). The mapping resolver hydrates document references — it fetches the `UserDocument` by ID and returns a `WorkflowExecutionDocumentV1`.

## Outputs

A rendered document (UI-only — no data consumers downstream).

## `DocumentViewerOutputConfig`

```jsonc
{
  "content_document_id": "generated_document.user_document_id",   // glom path into source step's output
  "view_output_button_text": "View Document"
}
```

## Defaults

- `output_type`: `document_viewer` (when used directly)
- Incremental: `rerun` / `no_op` only.

## Best practices

- **Prefer attaching to another step** via `output_config: DocumentViewerOutputConfig(...)` rather than creating a dedicated step.
- **`content_document_id`** is a glom path; it resolves through the step's output dict to find the `user_document_id`.
- For `fill_docx`, the default is `"generated_document.user_document_id"` — matches the step's output shape.

## Common gotchas

- Using a dedicated `document_viewer` step when the upstream step could just carry `output_type: "document_viewer"` → unnecessary indirection.
- `content_document_id` glom path pointing at a non-existent field → runtime error.

## Minimal example — attached to another step

```jsonc
// Attached to a fill_docx step (the common case)
{
  "name": "Fill Template",
  "type": "fill_docx",
  "output_type": "document_viewer",
  "output_config": {
    "content_document_id": "generated_document.user_document_id",
    "view_output_button_text": "View Filled Template"
  }
}
```

## Example — dedicated step (rare)

```json
{
  "name": "Display Guideline Doc",
  "type": "document_viewer",
  "input_mappings": [
    { "input_parameter_name": "document",
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Classify Documents", "output_attribute": "documents.Guideline.0" }
        ],
        "resolution_operator": null, "resolution_config": null
      } }
  ],
  "dependencies": [],
  "config": {},
  "output_type": "document_viewer",
  "output_config": {
    "content_document_id": "user_document_id",
    "view_output_button_text": "View Guideline"
  }
}
```
