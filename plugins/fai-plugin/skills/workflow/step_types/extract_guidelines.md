# extract_guidelines

> **Legacy pair — prefer `agentic_guideline_check` for new work.** This step and `check_guidelines` form a two-step pipeline (`extract_guidelines` pulls checklist items out of a guideline document; `check_guidelines` evaluates submission documents against them) that predates `agentic_guideline_check`, which does the same job in one step with KB-grounded citations and configurable status options. Both types remain valid and editable — recognize the pair, edit conservatively, and recommend migration when the user touches one. See [check_guidelines.md](check_guidelines.md) and [agentic_guideline_check.md](agentic_guideline_check.md).

### What this step does

Given a guideline document (underwriting guidelines, eligibility rules, etc.), extracts a checklist of items — each with a title, question, acceptable answer, and citation bboxes. Feeds `check_guidelines`.

Use when the guidelines themselves are documents you need to parse (vs. hardcoded guideline strings in `multi_column_qa` or `agentic_guideline_check.config.guidelines`).

### Config keys (`ExtractGuidelinesConfig`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `system_prompt` | `str \| null` | `null` → insurance-auditor default | Helps when the guideline doc has a specific structure (e.g., "Extract only items from section 3 — section 2 is non-binding guidance."). |
| `min_items` | `int \| null` | `20` | Target minimum checklist items. 40-60 for dense guideline documents. Too high forces fabrication; too low leaves items on the table. It's a target, not a hard limit. |

### Required inputs

| Name | Required | Source |
|---|---|---|
| `guideline_document` | YES | Single document — typically `Classify Documents.documents.Guideline.0` or a knowledge-source doc. A single doc, NOT a list. |
| `system_prompt` | no | |
| `min_items` | no | |

### Outputs (TableV1, grid)

| Key | Shape |
|---|---|
| `schema` | JSON schema of the guideline table |
| `data` | `List[row]` — one row per extracted guideline item |
| `user_documents` | `List` |
| `metadata` | `{ items: [...], num_items: int }` — **`items` is the raw list consumed by `check_guidelines`** |

Leave `output_type` / `output_config` / `output_schema` null (auto-filled: table, grid). Incremental: `rerun`, `no_op`, `append`, `preserve_edits` all allowed.

### Common gotchas

- Passing a list instead of a single doc → runtime error.
- Ignoring `metadata.items` and trying to use `data` downstream — `check_guidelines` wants the raw items list (`output_attribute: "metadata.items"`).
- Assuming the displayed count matches `min_items` exactly — it's a target.

### Migration sketch (on user request)

One `agentic_guideline_check` replaces the pair: move the checklist items into `config.guidelines` (or wire them dynamically via its `guidelines` input from this step), wire `kb` from the workflow's `knowledge_base` step, and set `status_options` to match the old Match/Mismatch/Needs Info vocabulary if downstream consumers parse it.

### Minimal example

```json
{
  "name": "Extract Guidelines",
  "type": "extract_guidelines",
  "input_mappings": [
    { "input_parameter_name": "guideline_document",
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Classify Documents", "output_attribute": "documents.Guideline.0" }
        ],
        "resolution_operator": null, "resolution_config": null
      } }
  ],
  "dependencies": [],
  "config": {
    "min_items": 30
  }
}
```

### See also

- [check_guidelines.md](check_guidelines.md) — the second half of the pair.
- [agentic_guideline_check.md](agentic_guideline_check.md) — the one-step modern replacement.
- `multi_column_qa` — for static/hardcoded guideline strings.
