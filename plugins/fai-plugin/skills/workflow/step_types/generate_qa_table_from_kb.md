# generate_qa_table_from_kb

> **DEPRECATED — DO NOT CREATE NEW INSTANCES.** Hidden from the builder's step picker; the bundled validator errors on new instances. If asked to create one, create `generate_qa_table_from_agent` instead. Migration is trivial: the input/output schema is IDENTICAL — only the top-level `type` field changes, so downstream wiring is byte-compatible. Editing existing instances is allowed — they sit mostly on older submission-intake workflows. When touching one, recommend migrating in the same change.

### What this step does

Answers a list of `{title, question}` items via single-shot KB lookup per question. Retrieves chunks, synthesizes an answer, emits confidence + citations. Output is a TableV1 keyed by per-question `title` (slugified to snake_case). The newer `generate_qa_table_from_agent` replaces this with an agent loop (re-query, expand, reason across chunks) — strictly more capable on the same inputs.

### When you'll encounter this

Editing an older submission-intake workflow (insurance, claims, cyber, D&O). Typical shape: `name: "Submission Q&A Table"` / `"Set Workflow Title"` / `"Extract Policy Details"`, 1-10 questions, `model: gpt-4o` (older) or `gpt-5.1` (recently migrated), wired with `dependencies` to `Prepare Documents` + `Initialize Knowledge Base`.

### Migration to generate_qa_table_from_agent (recommended)

The schemas are identical. The only required change is the `type` field. Recommended additional change: bump the model from `gpt-4o` to `gpt-5.1`.

**Before:**
```json
{
  "name": "Submission Q&A Table",
  "type": "generate_qa_table_from_kb",
  "config": {
    "questions": [
      {"title": "Insured Name", "question": "What is the name of the insured entity?"}
    ],
    "table_title": "Submission Details",
    "options": {"concurrency_limit": 5},
    "show_confidence_score": true,
    "model": "gpt-4o"
  }
}
```

**After (one-field rename + model bump):**
```json
{
  "name": "Submission Q&A Table",
  "type": "generate_qa_table_from_agent",
  "config": {
    "questions": [
      {"title": "Insured Name", "question": "What is the name of the insured entity?"}
    ],
    "table_title": "Submission Details",
    "options": {"concurrency_limit": 5, "show_confidence_warnings": true},
    "show_confidence_score": true,
    "model": "gpt-5.1"
  }
}
```

Notes:
- `options.show_confidence_warnings` is new on the agent step (defaults to `true`).
- Downstream `data.<slug>.value` / `.confidence_score` / `.citations` paths do not change.
- `output_type` (`"table"` or older `"field_value_table"`) stays as-is.

### Required inputs (for editing existing)

| Input parameter | Type | Required | Source |
| --- | --- | --- | --- |
| `kb` | object (KB handle) | yes | wired from a `knowledge_base` step via `output_attribute: "kb"` |
| `documents` | array of `file` | yes | wired from `prepare_documents` (`output_attribute: "documents"`) or a `classify_documents` category bucket |
| `questions` | array of `{title, question}` | no | usually inline in `config.questions` |

### Config keys (for editing existing)

From `GenerateQATableFromKBConfig` / `GenerateQATableFromKBOptions`:

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `questions` | list of `{title, question}` | `[]` | `title` doubles as table column header and is slugified to a snake_case output field name. |
| `table_title` | string | `"Question & Answer Report"` | Shown above the output table in the UI. |
| `table_description` | string | `"Answers generated from questions posed to a knowledge base built from the provided documents."` | Shown under the title. |
| `options.concurrency_limit` | int 1-50 | `10` | Questions processed in parallel. |
| `show_confidence_score` | bool | `true` | Whether to show numeric confidence in each cell. |
| `model` | AIModel enum | `DEFAULT_AI_MODEL` | Older instances use `gpt-4o`. Preserve unless the user asks to bump. |
| `reasoning_effort` | enum | `null` | Reasoning models only. |
| `verbosity` | enum | `null` | GPT-5 family only. |

Note: unlike `generate_qa_table_from_agent`, this step's `options` does NOT have a `show_confidence_warnings` field. If you see one set, it's a signal the author meant the agent step.

### Output schema (for editing existing)

Same shape as `generate_qa_table_from_agent`:

```
{
  "schema": {...},
  "data": {
    "<title_in_snake_case>": {
      "value": "...",
      "confidence_score": 0.0-1.0,
      "confidence_reason": "...",
      "citations": [...],
      "thinking_steps": [...]
    },
    ...
  },
  "user_documents": [...],
  "metadata": { "table_title": str, "table_description": str, "total_questions": int, "generation_completed": str }
}
```

Field-name slugification: lowercase, spaces and dashes → `_`, `?` stripped. `title: "Applicant Address"` becomes `data.applicant_address`. Downstream consumers must use the slugified key, not the raw title.

### Input wiring (input_mappings vs dependencies)

- NEW steps: N/A — do not create new instances.
- EXISTING steps: preserve the pattern already there. Most instances use legacy `dependencies` (they predate `input_mappings`):

  ```json
  "input_mappings": [],
  "dependencies": [
    {"step_name": "Prepare Documents", "field_selector": null},
    {"step_name": "Initialize Knowledge Base", "field_selector": null}
  ]
  ```

- Migration `dependencies` → `input_mappings` on explicit request:

  ```json
  "input_mappings": [
    {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Initialize Knowledge Base", "output_attribute": "kb"}]}, "input_parameter_name": "kb"},
    {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Prepare Documents", "output_attribute": "documents"}]}, "input_parameter_name": "documents"}
  ],
  "dependencies": []
  ```

- Reverse migration is forbidden.

### Common patterns

#### Pattern A — header / detail extraction (most common)

1-10 questions pulling identity or coverage fields. Most legacy instances use `dependencies`, `model: "gpt-4o"`, and `output_type: "field_value_table"` or `"table"`. Often `display_step: false` when feeding downstream automation only.

```json
{
  "name": "Submission Q&A Table",
  "type": "generate_qa_table_from_kb",
  "input_mappings": [],
  "dependencies": [
    {"step_name": "Prepare Documents", "field_selector": null},
    {"step_name": "Initialize Knowledge Base", "field_selector": null}
  ],
  "config": {
    "questions": [
      {"title": "Applicant Name", "question": "What is the name of the applicant?"},
      {"title": "Applicant Address", "question": "What is the applicant address?"}
    ],
    "table_title": "Submission Q&A Table",
    "options": {"concurrency_limit": 5},
    "show_confidence_score": true,
    "model": "gpt-4o"
  },
  "display_step": false,
  "incremental_config": {"behavior": "rerun"}
}
```

#### Pattern B — `Set Workflow Title` (single-question title generator)

A single-question instance whose answer labels the workflow run. `output_type: "set_title"`.

```json
{
  "name": "Set Workflow Title",
  "type": "generate_qa_table_from_kb",
  "config": {
    "questions": [{"title": "Title", "question": "What is the name of the insured?"}],
    "show_confidence_score": true,
    "model": "gpt-5.1"
  },
  "display_step": false
}
```

### Common validation errors and fixes

- **New instance of this step type** → the bundled validator errors: deprecated, use `generate_qa_table_from_agent`.
- **Missing `kb` or `documents`** — a step with `input_mappings: []` AND `dependencies: []` fails save validation. Wire both via `input_mappings` or list both upstream step names in `dependencies` (legacy).
- **`output_attribute: "knowledge_base_instance"`** — the actual KB-step output field is `kb`. Always use `output_attribute: "kb"`.
- **Duplicate question titles** — both slugify to the same `data.<key>` and the second silently overwrites the first. Review titles by hand.
- **`reasoning_effort` / `verbosity` on `gpt-4o`** — rejected. Leave both `null`.
- **`show_confidence_warnings` set in `options`** — that field belongs to the agent step's options, not this one. Harmless but a signal the author meant the agent step.
- **Downstream step reads `data.Insured Name`** — wrong; the slugified key is `data.insured_name`.

### See also

- **`generate_qa_table_from_agent`** — MODERN REPLACEMENT. Same input/output schema, agentic retrieval. Create this for any new Q&A step.
- `knowledge_base` — REQUIRED predecessor.
- `multi_column_qa` — schema-driven alternative for a matrix of rows × columns.
- `agentic_extraction` — for a single typed object (extraction_schema) instead of a list of titled answers.
- `submission_summary_generator` — common downstream consumer.
