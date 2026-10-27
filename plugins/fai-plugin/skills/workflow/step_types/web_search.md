# web_search

### What this step does

Runs a web-search agent. The agent is given:

- A flat dict of upstream inputs (the `data` mapping is flattened — TABLE envelopes, KEY_VALUE cell dicts, and single-row GRIDs are unwrapped into top-level keys; multi-row GRIDs are rendered as markdown tables).
- A user-question built from `query_template` (with `{{field_name}}` placeholders) plus an auto-appended "Input data:" labelled list of every flat field.
- A web-search tool bound to the chosen Perplexity model (`sonar` or `sonar-pro`) and `temperature`. The agent passes a **list of queries** in one tool call; they are dispatched in parallel.
- The user-supplied `system_prompt` (or the default research-assistant prompt).

The orchestrator LLM decides how to chunk the work into queries, batches them, then synthesizes a single markdown answer. Citations from every parallel query are renumbered into a global `[N]` sequence and post-processed into `[[N]](url)` markdown links.

### Required inputs

| Param           | Type     | Required | Source                                                                                                                        |
| --------------- | -------- | -------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `data`          | `object` | no       | `input_mapping` — `input_type: "dependency"` only (no static). Fields inside `data` are referenced in `query_template` as `{{field_name}}`. |
| `system_prompt` | `string` | no       | `input_mapping` — almost always `input_type: "static"`. Never put this in `config`.                                            |

If you don't wire `data` at all, the agent runs with an empty input dict and **must** have a `config.query_template` that doesn't reference any `{{...}}` placeholders, otherwise the executor returns `success: False` with `"No search query could be composed"`.

### Config keys

From `WebSearchConfig`:

| Key                | Type                       | Default                                            | Notes                                                                                                                                                                  |
| ------------------ | -------------------------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `query_template`   | string                     | `null`                                             | `{{field_name}}` placeholders pull from the flattened `data` input. If `null`, the agent only sees the auto-appended "Input data:" list and must derive its own queries. |
| `system_prompt`    | string                     | research-assistant default                         | **DEPRECATED here.** Pass via `input_mappings` instead. Executor reads the kwarg first; config is legacy-only fallback.                                                |
| `model`            | enum (`AIModel`)           | `gpt-5.5` recommended                              | Orchestrator LLM (NOT the search backend). Plans queries, fans them out, synthesizes. `gpt-5.1` for cost-sensitive flows.                                              |
| `reasoning_effort` | enum                       | `"medium"` for GPT-5 models                        | Set `"medium"` on a GPT-5 model (turns reasoning on); `null` for non-reasoning models.                                                                                  |
| `verbosity`        | enum                       | `null`                                             | GPT-5 only: `"low" \| "medium" \| "high"`. Same validator gate.                                                                                                        |
| `search_model`     | `"sonar" \| "sonar-pro"`   | `sonar`                                            | Perplexity backend. `sonar` = fast/cheap, `sonar-pro` = deeper research with broader sourcing — use for higher-stakes lookups (sanctions cross-ref, securities cases). |
| `temperature`      | float (0.0-1.0)            | `0.2`                                              | Perplexity sampling temperature. Keep `0.0`-`0.2` for factual/citation-grade work.                                                                                      |

A legacy migration rule rewrites old configs where `model` was set to `"sonar"` / `"sonar-pro"` — those get moved to `search_model` and `model` is reset to the orchestrator default. Don't author with that legacy shape.

### Output schema

```json
{
  "type": "object",
  "properties": {
    "markdown_content": {
      "type": "string",
      "description": "Markdown-formatted search results with inline citations"
    }
  }
}
```

Downstream `input_mappings` typically use `output_attribute: "markdown_content"` to consume the rendered narrative directly. The default `output_type` is `text` — the UI renders the markdown as a text block.

If the executor returns `success: False`, the output contains `{"error": "..."}` instead of `markdown_content`. Downstream steps that branch on success should handle both shapes.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is there.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

A new `web_search` step wires `data` (and optionally `system_prompt`) through `input_mappings`.

### Common patterns

#### Pattern 1 — Address-driven business lookup ("what does this company do?")

The most common shape. `data` is wired from an upstream extraction step's `data` output (insured name, DBA, address fields). `query_template` interpolates those fields into a research prompt. Use `sonar` (fast) — this lookup tolerates lower depth.

```json
{
  "type": "web_search",
  "name": "Search Insured Information",
  "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Extract Insured Data", "output_attribute": "data"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "data"},
    {"input_type": "static",
     "value": "You are a helpful research assistant. Provide accurate, well-sourced information. Always cite your sources inline. Focus on factual, up-to-date information.",
     "input_parameter_name": "system_prompt"}
  ],
  "config": {
    "query_template": "Research \"{{insured_name}}\" in {{insured_address_city}}, {{insured_address_state}}. Look for general operations, official company website, and a short description. Also search \"{{dba_names}}\" if different from the primary name. For each finding, provide the source URL and approximate date.",
    "model": "gpt-5.5",
    "reasoning_effort": "medium",
    "search_model": "sonar",
    "temperature": 0.2
  }
}
```

#### Pattern 2 — Conditional / gated enrichment (e.g. public-company-only research)

The system prompt itself encodes the gating logic — "ONLY perform a substantive search if `entity_type == 'Public'`; otherwise return a single short N/A line." Saves search calls when the upstream signal says enrichment isn't needed. Use `sonar-pro` when the substantive-case lookup is high-stakes.

```json
{
  "type": "web_search",
  "name": "Public Company Web Enrichment",
  "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Build Web Search Data", "output_attribute": null}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "data"},
    {"input_type": "static",
     "value": "You are a research analyst supporting claims notice triage. ONLY perform a substantive search when the insured is publicly traded (entity_type == 'Public') OR when securities class action allegations are present. Otherwise reply with a single short line: 'N/A — Not a public company; no web enrichment performed.' When searching, report on: securities class action history, stock-drop events and approximate market cap, pending regulatory actions, criminal indictments of officers/directors, bankruptcy filings, major adverse news in the last 24 months. Cite every claim inline. Concise markdown, H3 sections per topic, 2-5 bullets each.",
     "input_parameter_name": "system_prompt"}
  ],
  "config": {
    "query_template": null,
    "model": "gpt-5.5",
    "reasoning_effort": "medium",
    "search_model": "sonar-pro",
    "temperature": 0.0
  }
}
```

#### Pattern 3 — Negative-news / adverse-event check for underwriting

Same upstream-data wiring as Pattern 1, but the query template biases the agent toward *adverse* signals. Keep temperature low to avoid hallucinated headlines.

```json
{
  "config": {
    "query_template": "Research \"{{insured_name}}\" {{insured_address_city}}, {{insured_address_state}} for any negative press or adverse news relevant to insurance underwriting in the last 24 months. Also search \"{{dba_names}}\" if different from the primary name. For each finding, provide the source URL and approximate date. If nothing is found, state that explicitly.",
    "model": "gpt-5.5",
    "reasoning_effort": "medium",
    "search_model": "sonar",
    "temperature": 0.2
  }
}
```

### Common validation errors and fixes

- **`{{field_name}}` placeholder doesn't resolve to anything.** Unknown keys are replaced with an empty string and runs of spaces collapse. The agent still runs but with a vague query. Fix: confirm the upstream step's output exposes the field and that flattening surfaces it at the top level (KEY_VALUE cells and single-row grids are auto-unwrapped; nested objects are not).
- **No `query_template` AND no `data` mapping.** Executor returns `success: False` with `"No search query could be composed — set a query_template or map at least one input."` Wire `data` from an upstream step, or set `query_template` to a fully-literal string.
- **`system_prompt` in `config` instead of `input_mappings`.** Flagged by the bundled validator. The executor still falls back to `config.system_prompt` for legacy workflows, but new steps must pass it as a `static` input mapping.
- **`reasoning_effort` / `verbosity` set on a non-reasoning `model`.** Rejected. Move to a reasoning model or clear both fields to `null`.
- **`search_model` set on the `model` field (legacy authoring).** Silently migrated on load. Don't rely on this — author with the correct field.
- **`temperature` outside `[0.0, 1.0]`.** Hard rejected.

### Common gotchas

- **`{{...}}` is the query template syntax, NOT `{...}` or `${...}`.** The pattern is `\{\{(\w+)\}\}` — anything else passes through literally. Keep placeholder names matching the flattened input keys exactly (case-sensitive).
- **Citations are post-processed, not raw.** `[N]` markers are renumbered globally and converted into `[[N]](url)` markdown links automatically. **Do not** instruct the model to "format citations as links" in the system prompt — doing both produces malformed double-links.
- **The orchestrator LLM (`model`) is NOT the search backend.** `model` plans queries and writes the synthesis; `search_model` (`sonar` / `sonar-pro`) does the actual web retrieval. Cost optimization usually means dropping the orchestrator to `gpt-5.1`, not changing the search backend.
- **Inputs are flattened aggressively.** TABLE envelopes unwrap to their `data` payload; KEY_VALUE cell dicts collapse to just the value; single-row GRIDs collapse into top-level keys; multi-row GRIDs render as a markdown table under the parent key. If two upstream fields share a name after flattening, the last one wins.
- **`output_attribute: null` on the `data` mapping is valid** when the upstream step's full output dict is itself the data payload (common when the upstream is an inline-code step emitting a flat dict). When wiring from extraction steps, use `output_attribute: "data"`.
- **No `output_type` override needed.** Leave step-level `output_type` and `output_schema` as `null` — the executor returns `{"markdown_content": "..."}` and the UI renders it as text automatically.
- **Web search is stateless and uncached.** Two consecutive runs against the same inputs can return different markdown. Don't rely on byte-for-byte determinism in downstream comparisons or evals.

### See also

- **`knowledge_base`** — RAG over the workflow's own documents. Use when the answer is in an uploaded PDF/Excel/email, not on the public web. `web_search` reaches outside the workflow; `knowledge_base` stays inside it.
- **`agentic_extraction`** — RAG-search over the KB to extract structured fields. Pair with `knowledge_base` when you need typed data, not narrative markdown.
- **`ofac_agent`** — sanctions screening specifically. Queries the sanctions corpus directly — deterministic, citation-grade, and cheaper than `sonar-pro` for sanctions lookups. Use `web_search` for sanctions only when you need cross-references beyond the lists (e.g. press coverage of an enforcement action).
- **`submission_summary_generator`** — common downstream consumer of `web_search.markdown_content`.
