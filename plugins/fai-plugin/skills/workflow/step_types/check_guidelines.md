# check_guidelines

> **Legacy pair — prefer `agentic_guideline_check` for new work.** This step and `extract_guidelines` form the legacy two-step compliance pipeline. Both types remain valid and editable — recognize the pair, edit conservatively, and recommend migration when the user touches one. See [extract_guidelines.md](extract_guidelines.md) and [agentic_guideline_check.md](agentic_guideline_check.md).

### What this step does

Checks submission documents against extracted guideline items. Produces a TableV1 with Match / Mismatch / Needs Info verdicts and citations per item.

### Config keys (`CheckGuidelinesConfig`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `system_prompt` | `str \| null` | `null` | Compliance-check instructions / domain context. |
| `model` | `AIModel` | `gpt-4o` | Prefer `gpt-5.1` (or `gpt-5.5` for hard reasoning) on edits. |

### Required inputs

| Name | Required | Source |
|---|---|---|
| `extracted_guidelines` | YES | `Extract Guidelines` step, `output_attribute: "metadata.items"` — NOT `data`. |
| `guideline_document` | YES | Same doc fed to `extract_guidelines` (for citation bbox resolution). |
| `submission_documents` | YES | `Prepare Documents.documents` or a `Classify Documents` bucket. Should be the FULL submission — the check needs to search across it. |

### Outputs (TableV1, grid)

`{ schema, data, user_documents, metadata }` — one row per guideline item with verdict + citations.

Leave `output_type` / `output_config` / `output_schema` null (auto-filled: table, grid). Incremental: `rerun`, `no_op`, `append`, `preserve_edits` all allowed.

### Common gotchas

- Using `output_attribute: "data"` from Extract Guidelines → wrong shape. Use `"metadata.items"`.
- Missing `guideline_document` → citations fail.
- Passing only a subset of submission docs when the guidelines reference fields that live in other categories → false mismatches.

### Migration sketch (on user request)

One `agentic_guideline_check` replaces the pair: checklist items go into its `config.guidelines` (or its dynamic `guidelines` input), `kb` comes from the workflow's `knowledge_base` step, and `status_options` mirrors the old Match/Mismatch/Needs Info vocabulary if downstream consumers parse it.

### Minimal example

```json
{
  "name": "Check Guidelines",
  "type": "check_guidelines",
  "input_mappings": [
    { "input_parameter_name": "extracted_guidelines",
      "input_type": "dependency",
      "value": { "dependency_step_outputs": [{ "step_name": "Extract Guidelines", "output_attribute": "metadata.items" }],
                 "resolution_operator": null, "resolution_config": null } },
    { "input_parameter_name": "guideline_document",
      "input_type": "dependency",
      "value": { "dependency_step_outputs": [{ "step_name": "Classify Documents", "output_attribute": "documents.Guideline.0" }],
                 "resolution_operator": null, "resolution_config": null } },
    { "input_parameter_name": "submission_documents",
      "input_type": "dependency",
      "value": { "dependency_step_outputs": [{ "step_name": "Prepare Documents", "output_attribute": "documents" }],
                 "resolution_operator": null, "resolution_config": null } }
  ],
  "dependencies": [],
  "config": {
    "model": "gpt-5.1"
  }
}
```

### See also

- [extract_guidelines.md](extract_guidelines.md) — the first half of the pair.
- [agentic_guideline_check.md](agentic_guideline_check.md) — the one-step modern replacement.
- `multi_column_qa` — for static/hardcoded guideline strings.
