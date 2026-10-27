# agentic_guideline_check

### What this step does

Spins up one RAG-agent invocation **per guideline item** (capped by `concurrency_limit`). Each invocation uses the same agent infrastructure as `agentic_extraction` — same `rag_search()` tool, same optional `browse_document()`, same citation/confidence pipeline. The agent fills a fixed 4-column schema for each guideline:

| Column      | Source                                                                            |
| ----------- | --------------------------------------------------------------------------------- |
| Guideline   | `GuidelineItem.guideline` (title)                                                 |
| Description | `GuidelineItem.description` (optional context, shown when present)                |
| Finding     | Agent's free-text reasoning, wrapped with `citations: [...]` when enabled         |
| Status      | One of `config.status_options` (e.g. `Pass \| Fail \| Needs Review \| N/A`)       |

The output renders as a grid table. The actual rows live externally in TableV1 — the step's runtime `output` dict only carries `{table_v1_log_id, link_to_table_log, view_output_button_text}`.

### Required inputs

| Param          | Type     | Required | Source                                                                                          |
| -------------- | -------- | -------- | ----------------------------------------------------------------------------------------------- |
| `kb`           | `object` | no*      | `input_mapping` from a `knowledge_base` step (`output_attribute: "kb"`).                        |
| `documents`    | `array`  | no       | `input_mapping`. Usually omit; wire only to scope to a classified subset.                       |
| `system_prompt`| `string` | no       | `input_mapping`, almost always `input_type: "static"`. NEVER set this in `config`.              |

*The save-time validator does not strictly require `kb`, but it should always be wired — without a KB the agent has nothing to RAG-search.

The `guidelines` list itself can be either in `config.guidelines` (static) OR wired through `input_mappings` as a dependency (dynamic — overrides config when present).

### Config keys

From `AgenticGuidelineCheckConfig`. This step shares its agent with `agentic_extraction`, so the agent-config knobs (`model`, `reasoning_effort`, `verbosity`, `enable_document_browse_tool`, `generate_citations`, `show_confidence_score`) are intentionally the same.

| Key                          | Type            | Default                                | Notes                                                                                                                                                                       |
| ---------------------------- | --------------- | -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `guidelines`                 | `List[GuidelineItem]` | `[]`                            | Each item: `guideline` (title, required), `description` (optional context shown in table), `question` (the actual prompt the agent answers — falls back to `guideline` if null). |
| `system_prompt`              | string          | `null`                                 | **DEPRECATED here.** Pass via `input_mappings` instead. Executor reads the kwarg first; `config.system_prompt` is legacy-only fallback.                                     |
| `model`                      | enum            | `gpt-5.4`                              | Recommend `"gpt-5.5"` (strongest); use `"gpt-5.1"` for high-volume/simple checklists. Claude Sonnet 4.6 and Opus 4.6/4.7 are also offered on this step type.               |
| `reasoning_effort`           | enum            | `null`                                 | Reasoning models only: `"none" \| "minimal" \| "low" \| "medium" \| "high" \| "xhigh"` — the valid subset depends on the model (`gpt-5.1` has no `"xhigh"`). Must be `null` for models without reasoning support. |
| `verbosity`                  | enum            | `null`                                 | GPT-5 only: `"low" \| "medium" \| "high"`. Same validator gate.                                                                                                             |
| `generate_citations`         | bool            | `true`                                 | Wraps each `Finding` with `citations: [...]` keyed to KB chunks. Almost always leave on — that's the main reason to pick this step over `multi_column_qa`.                  |
| `show_confidence_score`      | bool            | `true`                                 | UI flag — surfaces per-row confidence. Does not change agent behavior.                                                                                                       |
| `enable_document_browse_tool`| bool            | `false`                                | Gives the agent a `browse_document(doc_id, chunk_position)` tool to walk a document sequentially. Costs more tokens; turn on only when RAG search alone keeps missing context. |
| `concurrency_limit`          | int (1-20)      | `5`                                    | How many guideline items run in parallel. Typical values are 6-10. Higher = faster but more rate-limit pressure on the model.                                               |
| `status_options`             | `List[str]`     | `["Pass","Fail","Needs Review","N/A"]` | The status enum the agent must pick from. Customizable — common sets include `["Yes","No","N/A"]`, `["Pass","Fail","Needs Review"]`, `["Complete","Partial","Outstanding","N/A"]`. |
| `enable_get_step_tool`       | bool            | `false`                                | Same knob as on `agentic_extraction`: gives the agent a `get_step(step_name)` tool to read allowlisted prior step outputs while evaluating guidelines.                       |
| `allowed_get_step_dependencies` | list[str]?   | `null`                                 | Allowlist of step names for `get_step`. Derived from declared dependencies when omitted.                                                                                     |

### Output schema

```json
{
  "type": "object",
  "properties": {
    "schema": {"type": "object"},
    "data":   {"type": "array"},
    "user_documents": {"type": "array"},
    "metadata": {"type": "object"}
  }
}
```

Note `data` is an **array** here (one object per guideline row) — different from `agentic_extraction`, whose `data` is a field-keyed object. Downstream `output_attribute` choices:

| `output_attribute` | Returns                                                                              |
| ------------------ | ------------------------------------------------------------------------------------ |
| `null`             | Full step output (schema + data + user_documents + metadata + KB handle).            |
| `"data"`           | Array of `{guideline, description, finding (with citations), status}` rows. Most common. |
| `"kb"`             | The KB handle the agent used.                                                        |
| `"schema"`         | The resolved row schema.                                                             |

Leave step-level `output_type`, `output_config`, and `output_schema` null — the backend auto-fills them (see `../reference/output_types.md`).

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is there.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

A new `agentic_guideline_check` step always has `dependencies: []` and wires `kb` (and optionally `documents` and `system_prompt`) through `input_mappings`.

### Common patterns

#### Pattern 1 — Static guideline list (compliance / eligibility checklist)

The dominant pattern: ~10-50 fixed guidelines stored in `config.guidelines`, one Knowledge Base, one carrier-specific system prompt wired statically. The agent evaluates each guideline independently.

```json
{
  "type": "agentic_guideline_check",
  "name": "Carrier Eligibility Check",
  "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Initialize Knowledge Base", "output_attribute": "kb"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "kb"},
    {"input_type": "static",
     "value": "You are an expert underwriting assistant evaluating submissions against Carrier Alpha eligibility rules. For each rule, decide Pass / Fail / Needs Review / N/A. Cite the source document and page when possible.",
     "input_parameter_name": "system_prompt"}
  ],
  "config": {
    "guidelines": [
      {"guideline": "Loss Runs Provided",
       "description": "",
       "question": "Do we have N years of loss runs in the submission? If yes, Pass. If no, Fail."},
      {"guideline": "Effective Date Not Backdated",
       "description": "Compares policy effective date against submission/binding date.",
       "question": "Is the policy effective date on or after the submission/binding date? Cite the dates you used."}
    ],
    "model": "gpt-5.5",
    "generate_citations": true,
    "show_confidence_score": true,
    "enable_document_browse_tool": false,
    "concurrency_limit": 8,
    "status_options": ["Pass", "Fail", "Needs Review", "N/A"]
  },
  "output_schema": null
}
```

#### Pattern 2 — Binder-vs-policy audit with custom status set

Audit-style checks (e.g. binder→policy compare) often use `["Yes","No","N/A"]` instead of `Pass/Fail`, run on a strong model with `reasoning_effort: "high"`, and benefit from `enable_document_browse_tool: true` so the agent can walk both source documents end-to-end.

```json
{
  "config": {
    "guidelines": [
      {"guideline": "Q1 Named Insured Match",
       "description": "Named insured legal name AND mailing address are identical on the binder and policy declarations.",
       "question": "Compare the Named Insured legal name and mailing address on the policy declarations to the binder's named-insured block. Answer Yes if identical, No if different, N/A if either source is missing."}
    ],
    "model": "gpt-5.5",
    "reasoning_effort": "high",
    "generate_citations": true,
    "show_confidence_score": true,
    "enable_document_browse_tool": true,
    "concurrency_limit": 10,
    "status_options": ["Yes", "No", "N/A"]
  }
}
```

#### Pattern 3 — Dynamic system prompt from an upstream step

When the carrier/program is determined at runtime, build the system prompt in a `custom_step` and wire it through `input_mappings` instead of hardcoding it. The `guidelines` list is still static config; only the prompt changes per execution.

```json
{
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Initialize Knowledge Base", "output_attribute": "kb"}], "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "kb"},
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Prepare Guideline Context", "output_attribute": "system_prompt"}], "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "system_prompt"}
  ]
}
```

### Common validation errors and fixes

- **`system_prompt` in `config` instead of `input_mappings`** → flagged by the bundled validator; the executor falls back to `config.system_prompt` only for legacy un-migrated workflows. New steps must pass it as a `static` input mapping.
- **`reasoning_effort` / `verbosity` set on a non-reasoning model** → rejected with `reasoning_effort '<x>' is not valid for model '<y>'`. Either move to a reasoning model or clear both fields to `null`.
- **`concurrency_limit` outside 1-20** → rejected (`ge=1, le=20`). Typical values are 5-10.
- **`status_options: []`** → rejected (`min_length=1`). Always include at least one allowed status.
- **`guidelines` items missing the `guideline` field** → `GuidelineItem.guideline` is required (the title). `description` and `question` are both optional; if `question` is null, the agent uses `guideline` as the prompt.
- **Inline-code step importing `guideline_check_qa`** → rejected as a forbidden import ("use the agentic_guideline_check step type instead"). Don't re-implement this block inside a `function`/`custom_step`.
- **`documents` wired from `Classify Documents.documents` (bare path)** → rejected with `Parameter 'documents' expects type 'array' but step 'Classify Documents' outputs type 'object'`. Fix: use `documents.<ClassName>` (or `concat_lists` over multiple classes), or omit `documents` entirely.

### Common gotchas

- **Findings are richer than `multi_column_qa`.** Each row's `Finding` carries the agent's full reasoning AND a `citations` array of KB chunk references — that's the main reason to pick this step. If you only need a one-line answer per question with no citations, `multi_column_qa` is cheaper and faster.
- **One agent invocation per guideline.** With `concurrency_limit: 5` and 50 guidelines that's 10 sequential waves of model calls. Cost and latency scale linearly with the guideline count — don't add filler items.
- **Field name is `question`, not `requirement`.** Older examples called the prompt field `requirement`; the actual pydantic model is `question`. Use `question`.
- **`description` is for the table UI, `question` is for the agent.** `description` is shown verbatim in the rendered grid. Keep the *evaluation contract* in `question`, keep the *user-facing context* in `description`.
- **`status_options` is enforced as the output enum.** The agent is constrained to pick exactly one of these strings — match casing carefully against what downstream consumers expect.
- **Shared agent with `agentic_extraction`.** Changes to model config, citation behavior, or browse-tool semantics apply to both step types.
- **Don't reconstruct the KB.** The resolved `kb` value is a ready-to-use handle. Wire it through as-is.
- **TableV1 storage:** the rows array is NOT in `step.output` at runtime — it lives externally in TableV1. Always wire downstream consumers through `input_mappings` with `output_attribute: "data"`.

### See also

- **`agentic_extraction`** — sibling that shares this step's agent infrastructure. Use that for free-form structured extraction; use this for fixed-question checklist evaluation.
- **`multi_column_qa`** — Q&A-shaped alternative for matrix-style checklist evaluation, one row per question. Cheaper and faster (single LLM call per row, no per-row agent), but no agent-level RAG search and weaker citations.
- **`compare_document_data`** — when the check is "compare these two documents field-by-field" rather than "evaluate the KB against a rule list".
- **`extract_guidelines` / `check_guidelines`** — the legacy two-step guideline pair this step replaces.
