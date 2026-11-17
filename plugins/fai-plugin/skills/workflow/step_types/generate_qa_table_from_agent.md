# generate_qa_table_from_agent

### What this step does

Takes a list of `{title, question}` items and asks an AI agent to answer each one using RAG over the configured `knowledge_base` plus any explicitly wired `documents` list for citation scoping. Per question, the agent issues retrieval queries against the KB, synthesizes an answer, and emits a confidence score and source citations. The step writes a TableV1 (`output_type: "table"`) keyed by the per-question `title` (slugified to snake_case for the schema field name).

It is the agentic upgrade over the deprecated `generate_qa_table_from_kb` step:

- **`generate_qa_table_from_kb` (DEPRECATED)** — single-shot KB lookup per question. No tool use, weaker synthesis.
- **`generate_qa_table_from_agent` (modern)** — agent loop with retrieval tool calls; can re-query, expand, and reason across multiple chunks/documents per question.

The two steps share the same input/output schema and output generator, so migration is just a `type` rename plus a model bump.

### Required inputs

| Input parameter | Type | Required | Source |
| --- | --- | --- | --- |
| `kb` | object (KB handle) | yes | wired from a `knowledge_base` step via `output_attribute: "kb"` |
| `documents` | array of `file` | yes | wired from `prepare_documents` (`output_attribute: "documents"`) or from a `classify_documents` category (`output_attribute: "documents.<Category>"`) |
| `questions` | array of `{title, question}` | no | usually defined inline in `config.questions`; can be wired from a previous step when the question list is dynamic |

`questions` is optional in the input schema because the canonical pattern is to define them in `config.questions`. The "wire questions dynamically" path is rare and only used when an upstream step computes the question set (e.g., a `custom_step` builds questions from missing fields). When wired, leave `config.questions: []` — the output schema falls back to a generic shape because field names are only known at runtime.

### Config keys

From `GenerateQATableFromAgentConfig` / `GenerateQATableFromAgentOptions`:

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `questions` | list of `{title, question}` | `[]` | The `title` doubles as the table column header and is slugified to a snake_case field name in the output schema. |
| `table_title` | string | `"Question & Answer Report"` | Shown above the output table in the UI. |
| `table_description` | string | `"Answers generated from questions posed to an AI agent analyzing the provided documents."` | Shown under the title. |
| `options.concurrency_limit` | int 1-50 | `10` | Number of questions processed in parallel. Higher values may hit rate limits — leave at default unless the question set is large. |
| `options.show_confidence_warnings` | bool | `true` | When `true`, warning earmarks render for low-confidence answers in the UI. Set `false` only deliberately (e.g., answers feed a downstream automated step). |
| `show_confidence_score` | bool | `true` | Whether to surface the numeric confidence score in the cell. |
| `model` | AIModel enum | `gpt-5.1` | Use `"gpt-5.1"` for new steps (Q&A fan-outs are high-volume); `"gpt-5.5"` when answer quality on hard questions is the bottleneck. `"gpt-4o"` in older configs is fine to preserve. |
| `reasoning_effort` | enum | `null` | Reasoning models only; rejected otherwise at save. |
| `verbosity` | enum | `null` | GPT-5 family only. |

### Output schema

The step emits a TableV1 (`output_type: "table"`):

```
{
  "schema": {...},                 # JSON Schema of the answer table
  "data": {                        # answers keyed by slugified question title
    "<title_in_snake_case>": {
      "value": "...",              # the agent's answer (string cell)
      "confidence_score": 0.0-1.0,
      "citations": [...]
    },
    ...
  },
  "user_documents": [...],         # documents the agent cited
  "metadata": {
    "table_title": str,
    "table_description": str,
    "total_questions": int,
    "generation_completed": str
  }
}
```

Each question's column field name is derived from the `title` by lowercasing, replacing spaces and dashes with `_`, and stripping `?`. So `title: "Effective Date"` becomes `data.effective_date`. Downstream steps that consume specific answers reference these slugified paths (plus `.value` for the scalar).

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is already there.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

Modern wiring for the two required parameters:

```json
"input_mappings": [
  {
    "input_type": "dependency",
    "value": {
      "dependency_step_outputs": [
        {"step_name": "Initialize Knowledge Base", "output_attribute": "kb"}
      ],
      "resolution_operator": null,
      "resolution_config": null
    },
    "input_parameter_name": "kb"
  },
  {
    "input_type": "dependency",
    "value": {
      "dependency_step_outputs": [
        {"step_name": "Prepare Documents", "output_attribute": "documents"}
      ],
      "resolution_operator": null,
      "resolution_config": null
    },
    "input_parameter_name": "documents"
  }
]
```

### Common patterns

#### Pattern A — submission intake extraction (most common)

Pull a short list of header fields off the submission. Inline `config.questions` is the canonical shape. Each question gets a clear `title` (becomes the table column + downstream field name) and a focused `question` body.

```json
{
  "name": "Extract Submission Fields",
  "type": "generate_qa_table_from_agent",
  "input_mappings": [
    {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Initialize Knowledge Base", "output_attribute": "kb"}]}, "input_parameter_name": "kb"},
    {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Prepare Documents", "output_attribute": "documents"}]}, "input_parameter_name": "documents"}
  ],
  "dependencies": [],
  "config": {
    "questions": [
      {"title": "Insured Name", "question": "What is the name of the insured entity?"},
      {"title": "Effective Date", "question": "What is the effective date? Return in MM/DD/YYYY format."}
    ],
    "table_title": "Submission Header",
    "options": {"concurrency_limit": 5, "show_confidence_warnings": true},
    "show_confidence_score": true,
    "model": "gpt-5.1"
  },
  "incremental_config": {"behavior": "rerun"}
}
```

#### Pattern B — category-scoped Q&A

Run the agent against a specific class by wiring `documents` from a `classify_documents` category bucket. `kb` is still the full KB — the `documents` parameter scopes citations, not retrieval (see gotchas).

```json
"input_mappings": [
  {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Initialize Knowledge Base", "output_attribute": "kb"}]}, "input_parameter_name": "kb"},
  {"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "Document Classification", "output_attribute": "documents.Loss Run"}]}, "input_parameter_name": "documents"}
]
```

#### Pattern C — dynamic question list from a prior step

When the question set is computed at runtime (e.g., a `custom_step` lists missing SOV fields), leave `config.questions: []` and wire `questions` as a third `input_mapping`. The output schema becomes generic — downstream consumers must use glom paths, not hard-coded snake_case keys.

### Common validation errors and fixes

- **Missing `kb` or `documents` input** — both are required. A naked step with `input_mappings: []` and `dependencies: []` fails validation. Wire both via `input_mappings` (modern) or keep legacy `dependencies` entries on an existing step.
- **`questions` wired AND populated in `config`** — pick one. If `questions` is in `input_mappings`, `config.questions` should be `[]`. Mixing the two leaves the runtime ambiguous.
- **Duplicate question titles** — both slugify to the same `data.<key>`, so the second answer silently overwrites the first. Validation does not catch this; the author must.
- **`reasoning_effort` set with a non-reasoning model** — rejected: `reasoning_effort '<value>' is not valid for model '<model>'`. Same for `verbosity` on unsupported models. Leave both `null` for non-reasoning models.
- **Wiring `output_attribute: "knowledge_base_instance"`** — older docs mention that name, but the actual field on the KB step output is `kb`. Always use `output_attribute: "kb"`.
- **Output schema field name mismatch** — downstream steps reading `data.Insured Name` silently get nothing. The actual path is `data.insured_name` (lowercased, spaces → underscores, `?` stripped).

### Common gotchas

- **`generate_qa_table_from_kb` is deprecated. Always create `generate_qa_table_from_agent` for new work.** Migrating an old workflow requires only the `type` rename and a model bump. Inputs, output schema, and downstream wiring are byte-compatible.
- **Question titles are field names.** Polished titles like `"Forms – Policy + Key Endorsements & Exclusions"` slugify to brittle keys. Keep titles short, ASCII, and stable — downstream references break if the title is renamed.
- **`kb` is the full corpus; `documents` scopes citations, not retrieval.** The agent searches all ingested documents regardless of the `documents` selection. If you genuinely need to restrict retrieval to a subset, build a separate KB over just those docs — but the canonical pattern is to pass everything and let the agent find what it needs.
- **`output_type` must be `"table"` for the UI button to appear.** Older configs use `"field_value_table"` — that still renders; do not silently rewrite an existing `"field_value_table"`.
- **`concurrency_limit` is a foot-gun.** Most workflows use the default `10` or a conservative `5`. Going above `10` only helps if the question count is large AND the model has rate-limit headroom.
- **`show_confidence_warnings: false` hides UX signal.** Use deliberately, not to "clean up" the UI.
- **`incremental_config.behavior: "rerun"` is the universal default** — same as the rest of the agentic steps.

### See also

- `knowledge_base` — REQUIRED predecessor. Build the KB once, near the top of the workflow, and wire `kb` into this step.
- `multi_column_qa` — schema-driven alternative for a matrix of rows × questions or structured per-row input columns. Use this step for a flat list of titled questions; use `multi_column_qa` for a table with shared input columns.
- `generate_qa_table_from_kb` — **DEPRECATED sibling.** Same I/O contract, weaker non-agentic retrieval. Never create new instances.
- `agentic_extraction` — when you want a single typed object (extraction schema) instead of a list of titled answers.
- `submission_summary_generator` — downstream consumer that often reads this step's table plus the KB to produce a summary.
