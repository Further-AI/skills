---
mode: new
workflow: <path/to/workflow.json>
version: 1
created: YYYY-MM-DD
ticket: <id or empty>
status: draft
---

# <Workflow Name> — Design

**Purpose**: <one-line description of what this workflow does>
**Trigger**: <email to X | manual upload | API>
**Reference patterns**: <reference/patterns/ docs or known-good workflows consulted, or "—">

## Document Categories

Classes for the `classify_documents` step. Each class must be clear and mutually exclusive — classification quality drives end-to-end accuracy.

- `<Category_Name>`: <what qualifies>

## Extraction Plan

One row per logical data group. Step type per the workflow skill's routing table; cite the `step_types/<type>.md` doc that governs each.

| Field group | Step name | Step type | Source (inputs) | Notes |
|---|---|---|---|---|
| <e.g., Insured> | <e.g., Extract Insured Data> | <e.g., agentic_extraction> | <kb + classified docs> | <special handling> |

## Decision Flow

Decision branches, manual inputs, conditional execution. "None — linear" if empty.

- `[Decision: <name>]` — branches on <variable>, routes to <branch step names>

## Analysis & Scoring

Compliance checks, screening agents, scoring, aggregation.

- <check name>: <step type + intent, with source doc path if guideline-doc driven>

## Outputs

Every output format the workflow produces, each with its data source.

- [ ] HTML dashboard (`custom_step`) — sections: <list>
- [ ] DOCX report (`fill_docx`) — template at `<path>`
- [ ] Email reply (`email`) — mode: <reply_to_original | compose | UI-only>
- [ ] Other: <describe>

## Step Order (DAG)

Topological order. Array position is display order only; execution follows the `input_mappings` DAG.

1. `Workflow Dispatcher` (auto-injected — never authored)
2. `Prepare Documents`
3. <...>

## Per-Step Detail

For each authored step: inputs (parameter name ← `Source Step.attribute`), outputs downstream will consume, and a 2–3 sentence prompt/code intent summary. Intent only — the builder writes the full prompt/code text.

### <Step Name>
- **Type**: <step type>
- **Inputs**: <param ← source expressions>
- **Outputs**: <field names + types>
- **Prompt/code summary**: <intent>

## Test Expectations

What the tester verifies on a draft test run. List the test documents and one row per expectation.

**Test documents**: <paths to sample submission files>

| # | Step | Expectation | How to verify |
|---|---|---|---|
| 1 | <step name> | <e.g., extracts insured_name and TIV from the ACORD> | <e.g., step output data.insured_name.value non-empty> |

## Open Questions

Genuinely unresolvable ambiguity only — structural, domain-critical, or blocking. These gate the build.

- <question>
