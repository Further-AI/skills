# ofac_agent

### What this step does

Agentic sanctions screening. The agent receives the resolved `entity_data` (an arbitrary dict — typically the `data` payload of an upstream extraction step) and may use any of the keys (`name`, `aliases`, `country`, `addresses`, `entity_type`, etc.) it finds useful. It iteratively issues regex-based fuzzy queries against the indexed sanctions corpus, scores matches, and returns a TableV1-shaped grid:

```json
{ "schema": <json schema of the columns>, "data": [<row>, ...], "user_documents": [], "metadata": {...} }
```

Rendered as a grid (`output_type: "table"` + `display_type: "grid"`). Each row is one potential sanctions hit. If nothing is found, the agent emits a single "No OFAC matches found" placeholder row so the step still renders cleanly.

### Required inputs

| Param         | Type     | Required | Source                                                                                                                                  |
| ------------- | -------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `entity_data` | `object` | YES      | `input_mapping` (`input_type: "dependency"`) from an upstream extraction step. The agent reads `name`, `aliases`, `country`, `addresses`, `entity_type` opportunistically — pass the whole extraction `data` payload. |

The sanctions sources are NOT user-configurable — the agent always has access to the full indexed corpus (OFAC-SDN, OFAC-CONS, UK-HMT, EU-FSF, FBI-MOST-WANTED, UN).

### Config keys

From `OFACAgentConfig` — model-level config only; no sanctions-source toggles:

| Key                | Type | Default      | Notes                                                                                                                            |
| ------------------ | ---- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `model`            | enum | `DEFAULT_AI_MODEL` | `"gpt-5.1"` for new steps; `"gpt-5.5"` only for ambiguous high-stakes screens.                                              |
| `reasoning_effort` | enum | `null`       | Reasoning models only: `"low" \| "medium" \| "high"`. Rejected for non-reasoning models.                                          |
| `verbosity`        | enum | `null`       | GPT-5 only: `"low" \| "medium" \| "high"`. Same validator gate.                                                                   |

### Output schema

TableV1 grid:

```json
{
  "type": "object",
  "properties": {
    "schema": { "type": "object" },
    "data":   { "type": "array" },
    "user_documents": { "type": "array" },
    "metadata": { "type": "object" }
  }
}
```

Each `data` row carries:

| Column         | Description                                                                                       |
| -------------- | --------------------------------------------------------------------------------------------------- |
| `primary_name` | Canonical entity name from the sanctions list.                                                    |
| `aliases`      | Comma-separated AKAs/DBAs from the source record.                                                 |
| `entity_type`  | `"individual" \| "organization" \| "vessel" \| "aircraft"` (or `null`).                           |
| `countries`    | Comma-separated countries associated with the record.                                             |
| `source_list`  | `"OFAC-SDN" \| "OFAC-CONS" \| "UK-HMT" \| "EU-FSF" \| "FBI-MOST-WANTED" \| "UN"`.                  |
| `match_reason` | Agent's reasoning for surfacing this row (token overlap, country match, etc.).                    |
| `details_url`  | Deep-link to the upstream sanctions list entry (may be empty).                                    |

Downstream wiring: `output_attribute: "data"` for the hit rows (most common for `decision` or `submission_summary_generator` use); `null` for the full dict; `"schema"` for the resolved column schema. Leave step-level `output_type`, `output_config`, and `output_schema` null — auto-filled.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is already there.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

Wire `entity_data` through `input_mappings` with `input_type: "dependency"` and `output_attribute: "data"` (or `null` for the full output dict — the agent ignores keys it doesn't recognize).

### Common patterns

#### Pattern 1 — Named insured screen, fed by the main extraction step

The dominant pattern: a single OFAC step after the main extraction, screening the named insured. The agent picks up `insured_name`, `dba_names`, `state`, `addresses` from the extraction payload.

```json
{
  "type": "ofac_agent",
  "name": "Check OFAC Sanctions",
  "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Extract Submission Fields", "output_attribute": "data"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "entity_data"}
  ],
  "config": {"model": "gpt-5.1", "reasoning_effort": "medium", "verbosity": "medium"},
  "output_schema": null,
  "display_step": true,
  "start_title": "Checking OFAC sanctions",
  "end_title": "Checked OFAC sanctions",
  "enable_rerun": true
}
```

#### Pattern 2 — Dedicated upstream "Extract OFAC Data" step

When the main extraction is heavy and you want to avoid coupling OFAC screening to its full schema, a small upstream step shapes a minimal `{name, aliases, country, addresses, entity_type}` dict and feeds it in with `output_attribute: null`.

```json
{
  "type": "ofac_agent",
  "name": "Check OFAC Sanctions",
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Extract OFAC Data", "output_attribute": null}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "entity_data"}
  ],
  "config": {"model": "gpt-5.1", "reasoning_effort": "medium", "verbosity": "medium"}
}
```

#### Pattern 3 — Additional-insured / principal screen (parallel OFAC steps)

For workflows that need to screen multiple entities (named insured + each additional insured), build N parallel `ofac_agent` steps, each wired to a different extraction output. Do NOT pass an array of entities into one OFAC step — the agent screens one entity per invocation.

### Common validation errors and fixes

- **`entity_data` missing from `input_mappings`** → required. Wire it from an extraction step (`output_attribute: "data"` or `null`).
- **`entity_data` wired as `input_type: "static"`** → rejected; `entity_data` is dependency-only. Must come from an upstream step.
- **`reasoning_effort` / `verbosity` set on a non-reasoning model** → rejected. Move to a reasoning model or clear both fields.
- **Trying to set `sanctions_sources` / `lists_to_query` in `config`** → not real config keys; `OFACAgentConfig` has only `model` / `reasoning_effort` / `verbosity`. The full corpus is always queried.
- **Passing an array of entities into `entity_data`** → the agent treats `entity_data` as one entity and latches onto the first name. Fix: one `ofac_agent` step per entity.

### Common gotchas

- **False positives are expected, not bugs.** Fuzzy matching with progressive broadening means short / common business names (e.g. `"ACME LLC"`, `"Global Trading"`) surface unrelated hits. Treat the grid as a triage queue, not a hard fail. Downstream `decision` steps should consider `match_reason` and country alignment, not raw row count.
- **The `aliases` column is a comma-separated STRING, not an array.** If a downstream step expects a list, split it.
- **Empty `entity_data` does not throw — it produces a single "No OFAC matches found" row.** If you see that row when you expected hits, the upstream extraction silently emitted an empty `name`. Inspect the extraction output before suspecting the sanctions corpus.
- **One entity per step.** Re-running re-screens the same entity; it does NOT iterate over an array. For multi-entity screening, fan out at the workflow level.
- **No `system_prompt` knob.** The OFAC agent's prompt is internal and not user-mappable — a `system_prompt` mapping is silently dropped.
- **Costs scale with `reasoning_effort`.** Default `"medium"` is the sweet spot. `"high"` rarely changes the verdict on clear-cut screens and roughly triples token spend.
- **The agent occasionally suppresses obvious-mismatch rows.** It requires at least one distinctive token overlap; very generic queries may return empty even when the corpus has incidental matches. Intentional — surface concern only if a known-listed entity is being missed.

### See also

- **`osha_agent`** — sibling agentic screening step (input parameter is `insured_data`, not `entity_data`). Use for OSHA workplace-inspection lookups.
- **`web_search`** — Perplexity-backed internet search. Use for non-sanctions checks (news / reputational / lawsuit search) not covered by the indexed corpus.
- **`decision`** — typical downstream consumer of the OFAC grid (`output_attribute: "data"`) for stop/proceed branching.
- **`submission_summary_generator`** — typical downstream consumer for the rendered submission summary section.
- **`trellis_law`** — sibling enrichment step for legal case history on the same insured.
