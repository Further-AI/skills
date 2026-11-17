# decision

### What this step does

A `decision` step produces no business data of its own. It evaluates every `case` in `config.cases`, in declaration order, and emits a list of branch names that downstream steps consult to decide whether to execute or be marked `BRANCH_SKIPPED`.

Execution model — details that bite if you forget them:

- Each `case` has a `condition` (`List[List[Comparison]]` — outer list is OR, inner list is AND) and a single `branch` name. When a case's condition evaluates true, its `branch` joins `branches_to_execute`.
- All cases are evaluated. The step is **not** a `switch/break` — multiple branches can fire concurrently from one decision (this is how fan-outs like "Determine LOBs to Extract" pick several LOBs at once).
- If NO case matches, `config.default_branch` is selected — when it is a non-empty string. If `default_branch` is `""` (empty), no branch fires and every step that depended on this decision goes `BRANCH_SKIPPED`. That's intentional in gate patterns: when nothing matched, the entire downstream tree is meant to skip.
- The skip status propagates: at save time the platform walks each branch root through the dependency graph (BFS) and stamps `parent_conditions = [{decision_step, branch}]` on every step it reaches. Any of those steps whose branch is never selected come out `BRANCH_SKIPPED`.
- Diamond joins: a downstream step reached by multiple branches accumulates multiple `parent_conditions`. The runtime rule is "for EACH decision step in `parent_conditions`, at least one of its listed branches must be in that decision's `branches_to_execute`" — i.e. AND across decisions, OR within a single decision.

### Required inputs

The input contract is **declared by the author** in `config.input_schema` (JSON Schema, `type: "object"`); every variable referenced inside `config.cases[*].condition` as `{"input_type": "variable", "value": "<name>"}` must appear in that schema. The save-time required-mapping check rejects the workflow if a variable marked `"required": True` is not wired in `input_mappings`.

Two equivalent ways to read upstream values inside a condition:

1. **Variable** (preferred — same wiring style as inline-code steps): declare in `config.input_schema`, wire in `input_mappings`, reference as `{"input_type": "variable", "value": "<name>"}` inside the comparison's `left_operand`.
2. **Inline dependency** (legacy but still common): reference the upstream step directly inside the comparison's `left_operand` as `{"input_type": "dependency", "value": {"dependency_step_outputs": [{"step_name": "...", "output_attribute": "..."}], "resolution_operator": null, "resolution_config": null}}`. With this form `input_mappings` can be `[]`. New steps SHOULD use the variable form so the wiring is visible in the workflow JSON and in the UI's input panel.

### Config keys

From `DecisionConfig`:

| Key              | Type                       | Required | Notes                                                                                                                                       |
| ---------------- | -------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `cases`          | `List[Case]`               | YES      | Ordered list of `{condition, branch}` cases. Order is for readability, not `break`-style short-circuit — ALL are evaluated.                 |
| `default_branch` | `string`                   | YES      | Branch name selected when no case matches. Use `""` to mean "no fallback — let everything downstream skip". A non-empty value MUST be the name of an existing step. |
| `input_schema`   | `Dict[str, Any]` or `null` | no       | JSON Schema (`type: "object"` with `properties`) declaring variables used by `cases[*].condition` via `input_type: "variable"`. Required iff any condition uses a `variable`. |

Each `Case`:

```json
{
  "condition": [
    [ <Comparison>, <Comparison>, ... ],
    [ <Comparison>, ... ]
  ],
  "branch": "<step_name_of_branch_root>"
}
```

The inner lists are AND groups; the outer list ORs them.

Each `Comparison`:

```json
{
  "left_operand":  { "input_type": "variable"|"dependency"|"static"|"prompt"|"function", "value": ... },
  "right_operand": { "input_type": "variable"|"dependency"|"static"|"prompt"|"function", "value": ... },
  "operator": "eq" | "neq" | "gt" | "gte" | "lt" | "lte" | "contains" | "not_contains" | "starts_with" | "ends_with" | "after" | "before" | "is_true" | "is_false" | "is_empty" | "is_not_empty"
}
```

Operator notes:

- **Short-form only.** `eq`, `gt`, `is_empty` — long forms like `equals` / `greater_than` / `less_than` fail validation.
- `is_true` / `is_false` / `is_empty` / `is_not_empty` ignore `right_operand` — set it to `{"input_type": "static", "value": ""}` as a placeholder (the field is still required).
- `contains` / `not_contains` work on strings, lists, dicts (anything that supports `in`).
- `after` / `before` parse both operands as dates — strings are fine, but ambiguous dates ("01/02/2026") follow the default locale.
- `gt`/`gte`/`lt`/`lte` are numeric — wiring a `string` source through them is a runtime crash, not a save-time error.

### Output schema

```json
{
  "type": "object",
  "properties": {
    "branches_to_execute": { "type": "array", "items": {"type": "string"} },
    "evaluation_results":  { "type": "array" }
  }
}
```

`branches_to_execute` is the only field downstream consumers should read. The runtime auto-injects `BRANCH_SKIPPED` for every step whose `parent_conditions` exclude its branch from this list — you typically do NOT wire other steps to a decision step's output; instead, you create child steps whose `name` equals one of the `branch` values, and the runtime handles skip propagation via `parent_conditions`.

`evaluation_results` is a per-case trace useful for debugging in the execution log; do not depend on its shape from another step.

### Input wiring (input_mappings vs dependencies)

- **NEW steps**: use `input_mappings` only. Leave `dependencies: []`. Declare every variable referenced by the conditions in `config.input_schema`, and wire each one with `{"input_type": "dependency", "value": {...}, "input_parameter_name": "<varname>"}`.
- **EXISTING steps**: preserve whatever pattern is there. Some production decisions read upstream values inline inside `cases[*].condition[*][*].left_operand` with `input_type: "dependency"` and leave `input_mappings: []` — that still works; do not "modernize" it incidentally.
- **Migration** `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

One gotcha specific to decision: even if `input_mappings` is `[]`, the step can still be fully wired — every operand may be an inline `input_type: "dependency"`. Do not assume "no input_mappings" means "no upstream dependencies"; dependency-name resolution walks operands recursively for decision steps.

### Common patterns

#### Pattern A — fan-out by classification result (multi-way routing)

A classifier produces a per-class document list; the decision selects which downstream extractors run. Multiple branches fire concurrently when more than one class is present.

```json
{
  "name": "Determine LOBs to Extract",
  "type": "decision",
  "input_mappings": [
    { "input_type": "dependency",
      "value": { "dependency_step_outputs": [{"step_name": "Classify Documents", "output_attribute": "documents.General_Liability_QuoteOrProposal"}], "resolution_operator": null, "resolution_config": null },
      "input_parameter_name": "gl_docs" },
    { "input_type": "dependency",
      "value": { "dependency_step_outputs": [{"step_name": "Classify Documents", "output_attribute": "documents.Workers_Compensation_QuoteOrProposal"}], "resolution_operator": null, "resolution_config": null },
      "input_parameter_name": "wc_docs" }
  ],
  "dependencies": [],
  "config": {
    "cases": [
      { "condition": [[ { "left_operand": {"input_type": "variable", "value": "gl_docs"}, "right_operand": {"input_type": "static", "value": ""}, "operator": "is_not_empty" } ]],
        "branch": "Extract General Liability" },
      { "condition": [[ { "left_operand": {"input_type": "variable", "value": "wc_docs"}, "right_operand": {"input_type": "static", "value": ""}, "operator": "is_not_empty" } ]],
        "branch": "Extract Workers Compensation" }
    ],
    "default_branch": "",
    "input_schema": {
      "type": "object",
      "properties": { "gl_docs": {"type": "array"}, "wc_docs": {"type": "array"} }
    }
  }
}
```

Why `default_branch: ""`: if no LOB matched, no extractor should run. Every "Extract …" branch root has its parent_condition stamped by this decision and skips cleanly.

#### Pattern B — Yes/No gate on a manual-input value (single branch)

The user filled a form in a `manual_input` step; the decision routes to a heavy downstream action only on the affirmative answer.

```json
{
  "name": "Create Follow-Up Task?",
  "type": "decision",
  "input_mappings": [
    { "input_type": "dependency",
      "value": { "dependency_step_outputs": [{"step_name": "Review Extracted Data", "output_attribute": "data.create_task.value"}], "resolution_operator": null, "resolution_config": null },
      "input_parameter_name": "create_task" }
  ],
  "dependencies": [],
  "config": {
    "cases": [
      { "condition": [[ { "left_operand": {"input_type": "variable", "value": "create_task"}, "right_operand": {"input_type": "static", "value": "Yes"}, "operator": "eq" } ]],
        "branch": "Create Follow-Up Task" }
    ],
    "default_branch": "",
    "input_schema": { "type": "object", "properties": { "create_task": {"type": "string"} } }
  }
}
```

#### Pattern C — multi-step branch gated by one boolean

A single upstream boolean controls a whole sequence of downstream steps. You can list each downstream step root as its own `branch` in `cases` (each repeating the same condition), but the simpler form is to declare ONE branch (the chain's root) and let the save-time compiler propagate the skip through the dependency graph — every step that data-depends on the root inherits the same `parent_conditions` automatically. Prefer the simpler form for new workflows.

### Explicit `parent_conditions` (cross-cutting)

`parent_conditions` lives on EVERY step type. It is auto-computed at save time from the decision's branches, so leave it `[]` on new steps — but you will see it populated in saved workflows:

```jsonc
"parent_conditions": [
  { "decision_step": "Triage Decision", "branch": "Auto-Approve Email" }
]
```

- A step runs only when **all** its `parent_conditions` are satisfied (AND across decisions; OR within one decision's listed branches).
- If a decision's branch wasn't fired, the gated step's status becomes `BRANCH_SKIPPED`.

### Common validation errors and fixes

- **`Step '<name>': Missing required input mappings: ['<var>']`** — a variable declared `"required": True` in `config.input_schema` was not wired in `input_mappings`. Add the corresponding entry.
- **`Decision step '<name>' references branch '<b>' but no step with that name was found`** — logged as a warning (NOT a save-time rejection), so the workflow saves but the branch is dead. Fix: rename either the referenced step or the `cases[*].branch` so they match exactly (case- and whitespace-sensitive).
- **`No branch selected by decision step '<name>' (branches: ...)`** — runtime error when a downstream step's `parent_conditions` list the decision but none of those branches fired AND `default_branch == ""` AND the step was still pulled in via a separate data dependency. Fix: either set a `default_branch`, or rewire the data dependency so it reads from a non-branched step.
- **`cases.<n>.branch field required`** — every `Case` MUST declare `branch` as a non-empty string. Empty-string branch names silently break skip propagation.
- **`default_branch field required`** — the field MUST exist; the empty string `""` is a valid value meaning "no fallback". Don't omit the key.
- **`Parameter '<var>' expects type 'X' but step '<src>' outputs type 'Y'`** — type mismatch between the operand source and the comparison. Fix: pick a different `output_attribute` (e.g. `data.create_task.value` rather than `data.create_task`).
- **Flat condition `[c1, c2]`** — invalid; must be `[[c1, c2]]` (AND of two) or `[[c1], [c2]]` (OR of two).

### Common gotchas

- `BRANCH_SKIPPED` status on steps in unmatched branches is normal, not an error. The UI shows them greyed out.
- Decision steps cascade — every step transitively downstream of a branch root inherits that branch's `parent_conditions`, so a "skipped" branch silently skips an arbitrarily long tail.
- Each branch references another step **by name** — renaming or deleting a step that any `cases[*].branch` or `default_branch` points at leaves a dead reference with only a warning log.
- All cases evaluate — no `break`. If two cases share a true condition, BOTH branches fire. Express mutual exclusion by adding the negation into the later case's condition.
- `default_branch: ""` means "fall through and let downstream skip" — it does NOT mean "always run everything". For a real fallback path, point it at a named step.
- Variables in `condition` operands must be declared in `config.input_schema` — otherwise no save-time check fires and the runtime lookup returns `None`, making every comparison silently false.
- Decision steps run under a short execution timeout — keep them pure logic. If you need an LLM call to "decide", compute the decision value upstream (extraction or `custom_step`), then route on it here.
- `display_step: false` is the common default — decision steps are usually hidden from the UI. Set `display_step: true` only to show the "which branches ran" trace.

### See also

- `pause` / `manual_input` — typical pattern: decision routes to a `pause` for human review when a confidence flag trips, and continues straight through otherwise.
- `custom_step` — for computing the decision value pre-decision (normalize an enum, compute a sum, compare two extracted dates) so the `decision` itself stays a thin comparator.
- `classify_documents` — the canonical upstream for Pattern A. Its `documents.<ClassName>` outputs feed `is_not_empty` checks, one branch per class.
- `extract_from_multiple_sources` / `agentic_extraction` — usual downstream branch roots for LOB-fanout decisions.
