---
mode: scoped-edit
workflow: <path/to/workflow.json>
version: <N+1 from previous design, or 1>
created: YYYY-MM-DD
ticket: <id or empty>
status: draft
---

# Scoped Edit — <short description>

## Primary Scope (full edit rights)

Steps whose content (prompt, code, config, schema, name) may change. Only steps listed here are fully editable by the builder.

- `[add]` <New Step Name> — <type>, <purpose>
- `[modify]` <Existing Step Name> — <what changes and why>
- `[remove]` <Dead Step Name> — <why>
- `[rename]` <Old Name> → <New Name> — <why>

## Wiring-Only Scope (input_mappings + parent_conditions ONLY)

Neighbors that need rewiring but whose content must stay identical. The builder may touch ONLY `input_mappings` and `parent_conditions` on these steps.

- `<Neighbor Step Name>` — add input_mapping: param `<param>` ← `<NewStep>.<output_attribute>`

If empty, the change is fully contained within primary scope.

## Out of Scope

Everything else. The builder refuses any operation that would modify these steps. The validator enforces via diff against the pre-edit baseline `workflow.json.bak` (the builder creates it before the first edit).

## Context

<Why this change — ticket summary, user request, regression. 2–3 sentences.>

## Changes (primary scope detail)

### <Step Name>
- **Kind of change**: <add | modify | remove | rename>
- **Inputs**: <param ← source expressions>
- **Outputs**: <field names + types downstream will consume>
- **Prompt/code summary**: <2–3 sentences of intent; the builder writes the full text>

## Rewiring (wiring-only scope detail)

Exact mapping deltas, one line each.

- `<step>`: `input_mappings` — ADD { source: `<NewStep>`, attribute: `<attr>`, param: `<param>` }
- `<step>`: `parent_conditions` — REPLACE with <new conditions>

## Validation Focus

Semantic checks the validator should prioritize on the affected subgraph.

- <e.g., do downstream consumers of `<NewStep>` reference output attributes that exist in its schema?>

## Test Expectations

What the tester verifies on a draft test run. Cover the changed subgraph plus one untouched downstream consumer (regression guard).

**Test documents**: <paths — reuse a prior submission's documents when possible>

| # | Step | Expectation | How to verify |
|---|---|---|---|
| 1 | <step name> | <expected behavior after the edit> | <observable check on step output> |

## Open Questions

- <question>
