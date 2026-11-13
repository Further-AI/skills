# osha_agent

### What this step does

Agentic OSHA workplace-safety screening. The agent receives the resolved `insured_data` dict (typically the `data` payload of an upstream extraction step) and may use any of the keys (`name`, `aliases`, `state`, `naics_code`, `address`, etc.) it finds useful. It iteratively calls OSHA establishment-search and inspection-detail tools — broadening from full name → suffix-stripped → distinctive-word + STEM → phonetic variants — and scores matches. Output is a TableV1 grid:

```json
{ "schema": <json schema of the columns>, "data": [<row>, ...], "user_documents": [], "metadata": {...} }
```

Rendered as a grid (`output_type: "table"` + `display_type: "grid"`). Each row is one potential OSHA inspection hit; no hits yields an empty `data` array (or a single placeholder row).

### Required inputs

| Param          | Type     | Required | Source                                                                                                                                  |
| -------------- | -------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `insured_data` | `object` | YES      | `input_mapping` (`input_type: "dependency"`) from an upstream extraction step. The agent reads `name`, `aliases`, `state`, `naics_code`, `address` opportunistically — pass the whole extraction `data` payload. |

The OSHA corpus is NOT user-configurable.

### Config keys

From `OSHAAgentConfig` — model-level config only, no source toggles:

| Key                | Type | Default            | Notes                                                                                                                            |
| ------------------ | ---- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `model`            | enum | `DEFAULT_AI_MODEL` | `"gpt-5.1"` for new steps; `"gpt-5.5"` only for ambiguous high-stakes screens.                                                    |
| `reasoning_effort` | enum | `null`             | Reasoning models only: `"low" \| "medium" \| "high"`. Rejected for non-reasoning models.                                          |
| `verbosity`        | enum | `null`             | GPT-5 only: `"low" \| "medium" \| "high"`. Same validator gate.                                                                   |

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

Per-row columns:

| Column               | Description                                                                  |
| -------------------- | -------------------------------------------------------------------------------- |
| `establishment_name` | Establishment name as recorded in the OSHA database.                         |
| `inspection_link`    | URL to the OSHA inspection detail page.                                      |
| `inspection_date`    | Date of the inspection (`YYYY-MM-DD`).                                       |
| `inspection_status`  | Current status of the inspection (open / closed / pending).                  |
| `violation_count`    | Number of violations found (string; the schema is `string`, not integer).    |
| `site_address`       | Physical address of the inspection site.                                     |
| `naics_code`         | NAICS industry classification code at the inspected site.                    |
| `match_reason`       | Agent's reasoning for surfacing this row (token overlap, NAICS match, etc.). |

Downstream wiring: `output_attribute: "data"` for the inspection rows (most common); `null` for the full dict; `"schema"` for the resolved column schema. Leave step-level `output_type`, `output_config`, and `output_schema` null — auto-filled.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is already there.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

Wire `insured_data` through `input_mappings` with `input_type: "dependency"` and `output_attribute: "data"` (or `null` for the full output dict). `insured_data` is dependency-only — `static` is rejected.

### Common patterns

`osha_agent` is a rarely used step type (commercial-lines underwriting workflows). Existing usage feeds the agent directly off an upstream extraction `data` payload.

#### Pattern 1 — Direct off the main submission extraction

```json
{
  "type": "osha_agent",
  "name": "OSHA Safety Check",
  "dependencies": [],
  "input_mappings": [
    {"input_type": "dependency",
     "value": {"dependency_step_outputs": [{"step_name": "Extract Submission Information", "output_attribute": "data"}],
               "resolution_operator": null, "resolution_config": null},
     "input_parameter_name": "insured_data"}
  ],
  "config": {"model": "gpt-5.1", "reasoning_effort": "medium", "verbosity": "medium"},
  "output_schema": null,
  "display_step": true,
  "start_title": "Running OSHA safety screening",
  "end_title": "OSHA screening complete",
  "enable_rerun": true
}
```

#### Pattern 2 — Dedicated upstream "Extract OSHA Data" step

When the main extraction is heavy, an upstream step shapes a minimal `{name, aliases, state, naics_code, address}` dict and feeds it in with `output_attribute: null`.

#### Pattern 3 — Sibling pair with `ofac_agent`

Underwriting workflows that screen against sanctions almost always want OSHA next. Wire them in parallel off the same extraction (or off a shared "Extract Screening Inputs" step) — the two agents are independent and don't share state.

### Common validation errors and fixes

- **`insured_data` missing from `input_mappings`** → required. Wire it from an extraction step (`output_attribute: "data"` or `null`).
- **`insured_data` wired as `input_type: "static"`** → rejected; dependency-only.
- **Wrong input parameter name (`entity_data`, `entity`, `business_data`)** → only `insured_data` is in the input schema. The `ofac_agent` sibling uses `entity_data`; do NOT copy that key here.
- **`reasoning_effort` / `verbosity` set on a non-reasoning model** → rejected. Move models or clear both fields.
- **Trying to set `naics_filter` / `state_filter` / `inspection_lookback_years` in `config`** → not real config keys. Filtering must happen upstream in the extraction step or downstream in a `decision` / `custom_step`.
- **Passing an array of entities into `insured_data`** → the agent treats it as one entity and latches onto the first name. One `osha_agent` step per entity.

### Common gotchas

- **False positives are expected, not bugs.** The agent uses progressive query broadening, so short / common business names ("ABC Services", "Smith Construction") surface unrelated hits. The agent is instructed to LEAN TOWARD INCLUSION and defer to downstream verification. Treat the grid as a triage queue; downstream `decision` steps should consider `match_reason` + state / NAICS alignment, not raw row count.
- **The agent self-suppresses on garbage input.** Values like `"not found"`, `"N/A"`, `"unknown"`, empty, or generic suffixes (`"LLC"`, `"Inc"`) cause the agent to return an empty matches array WITHOUT searching. Zero rows when you expected hits → inspect the upstream extraction; it likely emitted a placeholder.
- **`violation_count` is a STRING, not an integer.** Downstream consumers that sort or threshold on it must parse (and tolerate non-numeric placeholders like `"N/A"`).
- **`inspection_date` is a free-form string.** Usually `YYYY-MM-DD`, but other formats occur. Don't parse without a try/except.
- **The agent budget is capped at 25 tool calls.** Tightly-scoped entities usually finish in 3–6 calls. Very generic names can exhaust the budget without a confident match — the row still renders with `match_reason` explaining the broadened search. Not a bug.
- **No `system_prompt` knob.** The OSHA agent's prompt is internal and not user-mappable.
- **Costs scale with `reasoning_effort`.** Default `"medium"` is the sweet spot.
- **One entity per step.** Fan out at the workflow level for multi-entity screening.
- **Cell-wrapper flattening is automatic.** The block flattens `insured_data` before the agent sees it, so verbose extraction cells (`{value, confidence_score, citations}`) are reduced to bare values. You do NOT need a "strip cells" step between extraction and OSHA.

### See also

- **`ofac_agent`** — sibling agentic screening step for sanctions lists. Input parameter is `entity_data`, not `insured_data` — do NOT copy mappings between the two without renaming.
- **`web_search`** — for non-database public-record checks (news, lawsuits, reputational signals).
- **`decision`** — typical downstream consumer of the OSHA grid (`output_attribute: "data"`) for stop / proceed / triage branching.
- **`submission_summary_generator`** — typical downstream consumer; map `output_attribute: "data"` into one of its `data_points`.
- **`agentic_extraction`** — the natural upstream feeder for a tightly-shaped `{name, state, naics_code, address}` dict (Pattern 2).
