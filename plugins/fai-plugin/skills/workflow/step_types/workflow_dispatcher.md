# `workflow_dispatcher`

**Auto-injected virtual root step that provides initial workflow context. Do not author manually.**

## When to use it

Never — the engine adds it at runtime. Every workflow's root `input_mappings` should reference `step_name: "Workflow Dispatcher"` (literal string, case-sensitive) instead of a step you put in the `steps` array.

## Config fields (`WorkflowDispatcherConfig`)

Populated by the engine, not the author:

| Field | Type | Default | Notes |
|---|---|---|---|
| `user_documents` | `List[Any] \| null` | `null` | Pre-prepared WorkflowExecutionDocumentV1 objects |
| `file_paths` | `List[str] \| null` | `null` | Local paths (when `file_access_config.type = "multipart"`) |
| `urls` | `List[str] \| null` | `null` | Public URLs (when `type = "public_urls"`) |
| `user_document_ids` | `List[str] \| null` | `null` | Existing UserDocument IDs to fetch |
| `owner_oid` | `str` | required | Organization ID |
| `owner_uid` | `str` | required | User ID |
| `run_id` | `str` | required | Execution run ID |

## Inputs

None — it's the root.

## Outputs

Top-level keys the resolver exposes:

| Key | Shape | Glom example |
|---|---|---|
| `user_document_ids` | `List[str]` | `"user_document_ids"` or `"user_document_ids.0"` |
| `user_documents` | `List[WorkflowExecutionDocumentV1]` | `"user_documents"` |
| `file_paths` | `List[str]` | `"file_paths"` |
| `urls` | `List[str]` | `"urls"` |
| `owner_oid` | `str` | `"owner_oid"` |
| `owner_uid` | `str` | `"owner_uid"` |
| `run_id` | `str` | `"run_id"` |

**Critical**: dispatcher emits `user_document_ids`, not `documents`. Only `prepare_documents` accepts document IDs — every other step expects `WorkflowExecutionDocumentV1` objects. **Always put `prepare_documents` directly after dispatcher.**

## Defaults

- `output_type`: `simple`

## Best practices

- Wire `prepare_documents.user_document_ids` from `"Workflow Dispatcher" / user_document_ids`.
- Don't add a step with `type: "workflow_dispatcher"` to your workflow's `steps` array.
- The validator specifically whitelists the name `"Workflow Dispatcher"` as a valid dependency target even though it isn't in the steps list.

## Common gotchas

- Using `output_attribute: "documents"` on Workflow Dispatcher — wrong. Use `user_document_ids` and feed it into `prepare_documents`.
- Case-sensitive string: `"Workflow Dispatcher"` exactly (two words, capitalized).

## Example — canonical first mapping

```jsonc
{
  "name": "Prepare Documents",
  "type": "prepare_documents",
  "input_mappings": [
    { "input_parameter_name": "user_document_ids",
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Workflow Dispatcher", "output_attribute": "user_document_ids" }
        ],
        "resolution_operator": null, "resolution_config": null
      } }
  ],
  "dependencies": [],
  "config": { "split_excel_sheets": false, "translate_documents": false, "target_language": "en", "include_hidden_sheets": true }
}
```
