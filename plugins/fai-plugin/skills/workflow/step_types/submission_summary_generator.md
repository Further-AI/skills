# submission_summary_generator

### What this step does

The canonical "wrap up the workflow with an AI-written narrative" step:

1. Takes the resolved `data_points` list (any mix of TableV1 outputs, dicts, scalars).
2. Formats each entry for the prompt — tables render as markdown tables, dicts as JSON, scalars as strings.
3. Concatenates the formatted sections with `---` separators and appends them to the configured `system_prompt`.
4. Runs a summary agent. When `kb` is wired, the agent gets a `rag_search` tool; otherwise it must rely on what's already in the prompt.
5. Returns `{"summary": "<markdown or HTML string>"}`.

It does NOT produce a TableV1 — the output is a single `summary` string. The step's `output_type` picks the render mode, and the ONLY valid modes are:

- `"simple"` — compact canvas card showing the summary inline (the default the backend writes when null). **No "View" button.**
- `"chat_message"` — drops the summary into the chat surface as a bubble; the backend auto-writes `ChatMessageOutputConfig(content_key="summary")`.
- `"html"` — styled HTML report behind a "View Report" button; the backend auto-writes `HtmlOutputConfig(content_key="summary")`. Pair with a system prompt that emits HTML markup. **This is the mode whenever the operator wants a report opened via a button** — if they say the final step has "no button" or the report came out blank, set this step to `html` and have its prompt emit HTML.

NEVER `"text"` — SSG's content lives under `summary` (not text's default key), so a `text` SSG renders a blank viewer.

### Required inputs

| Param           | Type             | Required | Typical source                                                                                          |
| --------------- | ---------------- | -------- | ---------------------------------------------------------------------------------------------------------- |
| `data_points`   | array of object  | YES      | `input_mapping` with `dependency_step_outputs` listing every upstream step to summarize; usually `resolution_operator: "concat_lists"`. Each `output_attribute` is `null` (full step output) or `"data"` (just the extracted dict). |
| `system_prompt` | string           | YES      | `input_mapping` with `input_type: "static"` (the human-authored summarization brief). `config.system_prompt` is the legacy fallback. |
| `kb`            | object           | no       | `input_mapping` from a `knowledge_base` step's `output_attribute: "kb"`. Wire whenever the prompt asks the agent to dig deeper into the docs. |
| `documents`     | array of file    | no       | `input_mapping` from `prepare_documents.documents` or a classify bucket. Provides the agent with the doc list for context — does NOT restrict KB retrieval. |

Both `data_points` and `system_prompt` are enforced at save time.

### Config keys

From `SubmissionSummaryGeneratorConfig`:

| Key                 | Type    | Default       | Notes                                                                                                                                  |
| ------------------- | ------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `system_prompt`     | string? | `null`        | **Legacy** location. Prefer wiring through `input_mappings` for new steps; executor reads the kwarg first, config is fallback only.        |
| `model`             | enum    | `DEFAULT_AI_MODEL` | `"gpt-5.5"` for high-stakes narrative syntheses; `"gpt-5.1"` for routine summaries.                                                   |
| `reasoning_effort`  | enum?   | `null`        | Reasoning models only; rejected otherwise at save.                                                                                          |
| `verbosity`         | enum?   | `null`        | GPT-5 only. Same validator gate.                                                                                                            |

There is no `extract_per_document`, no `extraction_schema`, no `generate_citations` knob — the output is freeform text. Suppress flags belong in the prompt, not the config.

### Output schema

```json
{
  "type": "object",
  "description": "AI-generated summary output",
  "properties": {
    "summary": { "type": "string", "description": "Generated markdown summary" }
  }
}
```

Downstream consumers set `output_attribute: "summary"` for the raw string, or `null` for the full `{"summary": "..."}` dict. `output_schema` stays `null` — the schema is static and pinned to the step type.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is already there.
- Migration `dependencies` → `input_mappings` on explicit request; the reverse is forbidden.

The modern wiring for `data_points`:

```json
{
  "input_type": "dependency",
  "value": {
    "dependency_step_outputs": [
      { "step_name": "Extract Submission Info", "output_attribute": null },
      { "step_name": "Extract Loss Runs",       "output_attribute": null },
      { "step_name": "OFAC Check",              "output_attribute": null }
    ],
    "resolution_operator": "concat_lists",
    "resolution_config": null
  },
  "input_parameter_name": "data_points"
}
```

`system_prompt` is almost always a `static` input with the full prompt as the value. If the collection logic is non-trivial, the classic pattern is a small upstream `custom_step` that collects extraction outputs into `{"data_points": [...]}` and this step maps `data_points` from it.

### Common patterns

#### Pattern A — modern: multi-source summary with KB context

Fuses three upstream extractions, pulls in the KB so the agent can verify gaps via RAG, and emits markdown into a `simple` canvas card.

```json
{
  "name": "Generate Submission Summary",
  "type": "submission_summary_generator",
  "input_mappings": [
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          {"step_name": "Extract Submission Info", "output_attribute": null},
          {"step_name": "Extract Loss Runs",       "output_attribute": null},
          {"step_name": "OFAC Check",              "output_attribute": null}
        ],
        "resolution_operator": "concat_lists",
        "resolution_config": null
      },
      "input_parameter_name": "data_points"
    },
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          {"step_name": "Initialize Knowledge Base", "output_attribute": "kb"}
        ]
      },
      "input_parameter_name": "kb"
    },
    {
      "input_type": "static",
      "value": "You are an expert underwriter. Summarize the submission in markdown with sections for Executive Summary, Key Details, Risk Highlights, and Recommended Next Steps. Cite specific values from the data tables.",
      "input_parameter_name": "system_prompt"
    }
  ],
  "dependencies": [],
  "config": {"model": "gpt-5.5"},
  "output_type": "simple",
  "output_schema": null,
  "incremental_config": {"behavior": "rerun"}
}
```

#### Pattern B — chat-message variant (auto-wired content_key)

`output_type: "chat_message"` triggers the backend auto-fill that sets `output_config.content_key = "summary"`. Do NOT also add a downstream step that re-wraps the summary into a chat message — that pattern exists only in legacy workflows.

```json
{
  "name": "Add Summary to Chat",
  "type": "submission_summary_generator",
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [
        {"step_name": "Extract Quote Options", "output_attribute": "data"},
        {"step_name": "Check Guidelines",      "output_attribute": "data"}
     ], "resolution_operator": "concat_lists"},
     "input_parameter_name": "data_points"},
    {"input_type": "static",
     "value": "Write a concise (3-5 sentence) plain-language summary of the quote options and any guideline issues.",
     "input_parameter_name": "system_prompt"}
  ],
  "config": {"model": "gpt-5.1", "reasoning_effort": "low"},
  "output_type": "chat_message"
}
```

#### Pattern C — HTML email body (no KB)

Used when the summary is the body of an outgoing email. The prompt demands HTML output; the downstream `email` step consumes `output_attribute: "summary"` and inlines it. KB is intentionally omitted — the agent should rely only on the upstream extractions for an email body.

```json
{
  "name": "Generate HTML Email Summary",
  "type": "submission_summary_generator",
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [
        {"step_name": "Extract Submission Info", "output_attribute": "data"},
        {"step_name": "Map SOV",                 "output_attribute": "data"}
     ], "resolution_operator": "concat_lists"},
     "input_parameter_name": "data_points"},
    {"input_type": "static",
     "value": "Generate a well-formatted HTML email body. Output MUST be valid HTML, NOT markdown. Use <h3>, <p>, <table>, <ul>. Sections: Submission Summary, SOV Highlights, Next Steps.",
     "input_parameter_name": "system_prompt"}
  ],
  "config": {"model": "gpt-5.1"},
  "output_type": "simple"
}
```

### Common validation errors and fixes

- **Missing `data_points`** — required; there is no `config` fallback for this input. An empty list resolves but yields `"Unable to generate summary - no valid data provided."` at runtime.
- **Missing `system_prompt`** — required. The executor raises `ValueError("Submission summary generator step requires system_prompt in config")` when both the kwarg and config are empty.
- **`system_prompt` only in `config`, not in `input_mappings`** — soft warning preserved for legacy steps; new steps must use a `static` input mapping.
- **Inline-code step importing `generate_submission_summary_from_data_points` / `summary_agent` / `create_summary_agent`** — rejected as a forbidden import. Use this step type directly.
- **`reasoning_effort` / `verbosity` on a non-reasoning model** — rejected. Move models or clear both fields.
- **`output_schema` set to anything other than `null`** — the schema is fixed (`{"summary": "string"}`).
- **`output_type: "table"` or `"text"`** — not valid SSG modes. Use `simple`, `chat_message`, or `html`.

### Common gotchas

- **Prompts get a markdown-formatted data dump appended.** Tables render as markdown tables, dicts as JSON. Tell the agent explicitly that "the data below is provided as markdown tables" — don't assume it infers the formatting contract.
- **The summary agent does not persist its run history.** If you need the agent's intermediate steps recorded, use `agentic_extraction` instead — this step is deliberately fire-and-forget.
- **KB is optional, but agent capabilities scale with it.** Without `kb`, no `rag_search` tool is bound and the agent only sees what's in the prompt. With `kb`, it can dig back into the source docs — what you want when the prompt asks it to "verify" or "cite" values.
- **`documents` does NOT scope KB retrieval.** It's purely a context list. If you need scoped retrieval, say so in the prompt.
- **Token budget.** No internal truncation — long prompts (many large extraction tables) will hit the model's context limit. Trim `data_points` to the outputs you actually need; do not blanket-wire every upstream step.
- **No citations.** The output is a plain string. If you need linked citations back to documents, do the extraction with `agentic_extraction` and let the summary cite extracted values by reference.
- **`output_type: chat_message` triggers auto-config.** Don't set `output_config` manually.
- **`incremental_config.behavior: "rerun"` is the universal default.** Re-running regenerates the summary from scratch.

### See also

- **`knowledge_base`** — typical predecessor when this step needs RAG context.
- **`multi_column_qa`** / **`agentic_extraction`** / **`extract_from_multiple_sources`** — typical predecessors producing the outputs fed into `data_points`.
- **`agentic_extraction`** — pick instead when you need a *structured* output (fields + citations + confidence) rather than a freeform narrative.
- **`email`** — typical downstream consumer when the summary is an email body (`output_attribute: "summary"`).
- **`custom_step`** — pick instead when the "summary" is deterministic templating (string concatenation, no LLM reasoning); also the classic Collect → AI step → Compose pattern's bookends.
