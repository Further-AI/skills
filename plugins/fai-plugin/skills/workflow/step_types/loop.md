# loop

### What this step does

A control-flow step that expands a set of "member" steps once per item in an iteration source. The loop itself does not produce business data — it's a runtime fan-out. Each member step runs as many times as there are items in the source list. The runtime injects an iteration scope onto every expanded member entry carrying `iteration_item` (the current item), `iteration_index` (0-based), `iteration_label` (display label), and `loop_parent` (the loop step's name). There is **no `loop_item` variable** — a member reads the current item by wiring an `input_mapping` whose dependency points at the LOOP STEP ITSELF (see "Input wiring").

Execution model — non-obvious bits:

- Members are declared in `config.member_steps` (loop side), and EACH member step must reciprocally set its own `loop_membership: ["<this loop step's name>"]` (member side). The two declarations must agree — asymmetry is rejected at save time.
- A step can belong to ONLY ONE loop. Declaring membership in two loops is rejected because the runtime expansion would collide on step names.
- The iteration source must resolve to a LIST:
  - a list of OBJECTS — the iteration label comes from the producer's output-schema `display_field` annotation when present; fallback label is `"Item {index}"`.
  - a list of PRIMITIVES — labels are the stringified values.
  - Dict / object sources are NOT supported. If your upstream is a dict (e.g. `classify_documents.documents`), insert a `custom_step` that flattens it into a list first.
- `max_iterations` (default `10000`) is a safety ceiling. Exceeding it fails the loop with a clear error rather than spinning forever.

### Required inputs

| Param              | Type    | Required | Source                                                                                                                                                                                                |
| ------------------ | ------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `iteration_source` | `array` | YES      | `input_mappings` — points at the list to iterate over. May reference any upstream step output that resolves to an array of objects or primitives. Cannot reference a `member_step` (would be a cycle). |

### Config keys

`ForEachLoopConfig`:

| Key              | Type                  | Default      | Notes                                                                                                                                                                            |
| ---------------- | --------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `loop_type`      | `Literal["for_each"]` | `"for_each"` | Loop discriminator. Only `for_each` is supported today — do not invent values.                                                                                                    |
| `member_steps`   | `List[str]`           | `[]`         | Names of steps that run once per iteration. Each named step MUST exist AND must reciprocally set `loop_membership: ["<this loop's name>"]`.                                        |
| `max_iterations` | `int` (≥ 1)           | `10000`      | Safety ceiling. The runtime aborts with a clear error if the iteration_source list exceeds this length. Raise it explicitly when you really need more.                             |

### Output schema

Loop has NO output schema — it is a control-flow construct, not a data producer. Downstream steps read from the `member_steps`' outputs (which become arrays in the parent scope, one entry per iteration). Do NOT wire dependencies against the loop step itself outside the loop.

### Input wiring (input_mappings vs dependencies)

`loop` is `input_mappings`-only. Wire `iteration_source` exclusively via `input_mappings`; `dependencies` stays `[]` and is not supported as a wiring shape on loop steps.

Each MEMBER step uses normal `input_mappings` to read per-item data. **To read the current item, wire an `input_mapping` whose `dependency_step_outputs` references the LOOP STEP by name.** When a member runs inside the loop, the mapping resolver short-circuits that dependency and returns the current iteration's `iteration_item` instead of the loop's (non-existent) output. Point `output_attribute` at the field you want off the item (e.g. `documents`, `lob_name`); leave it `null` to get the whole item. For `custom_step` / `decision` members, declare the corresponding variable in the member's `input_schema` and bind it from that loop-step dependency mapping.

### Common patterns

#### Pattern A — loop over classified document groups

A classifier produces N classes; run the same extraction on each non-empty class without copy-pasting branches.

```json
[
  {
    "name": "Iterate LOBs",
    "type": "loop",
    "input_mappings": [
      {
        "input_type": "dependency",
        "input_parameter_name": "iteration_source",
        "value": {
          "dependency_step_outputs": [{"step_name": "LOB List Flattener", "output_attribute": "data.lobs"}],
          "resolution_operator": null,
          "resolution_config": null
        }
      }
    ],
    "dependencies": [],
    "config": {
      "loop_type": "for_each",
      "member_steps": ["Extract LOB Data"],
      "max_iterations": 50
    }
  },
  {
    "name": "Extract LOB Data",
    "type": "agentic_extraction",
    "loop_membership": ["Iterate LOBs"],
    "input_mappings": [ /* wire `documents` / `lob_name` from a dependency on "Iterate LOBs" (the loop step) — the resolver returns the current iteration_item */ ],
    "dependencies": [],
    "config": { /* AgenticExtractionConfig — extraction_schema, etc. */ }
  }
]
```

Why a `LOB List Flattener` step upstream: `classify_documents` outputs `documents.<ClassName>` as a dict-of-arrays, not a single array. The loop's `iteration_source` must be a list, so a `custom_step` flattens the dict into `[{"lob_name": "...", "documents": [...]}, ...]`.

#### Pattern B — manual review loop over a table of records

After a multi-row extraction, ask the user to verify each row.

```json
[
  {
    "name": "Verify Rows",
    "type": "loop",
    "input_mappings": [
      {
        "input_type": "dependency",
        "input_parameter_name": "iteration_source",
        "value": {
          "dependency_step_outputs": [{"step_name": "Extract Loss History Rows", "output_attribute": "data"}],
          "resolution_operator": null,
          "resolution_config": null
        }
      }
    ],
    "dependencies": [],
    "config": {
      "loop_type": "for_each",
      "member_steps": ["Confirm Row"],
      "max_iterations": 100
    }
  },
  {
    "name": "Confirm Row",
    "type": "manual_input",
    "loop_membership": ["Verify Rows"],
    "input_mappings": [ /* wire item fields onto the form from a dependency on "Verify Rows" — resolves to the current iteration_item */ ],
    "dependencies": [],
    "config": { /* manual_input form spec */ }
  }
]
```

### Common validation errors and fixes

- **`Loop '<name>' has no member_steps`** (`LOOP_STRUCTURE_ERROR`) — empty list. Add at least one member step name (or remove the loop).
- **`Loop '<name>' iteration_source references member step '<X>' — cycle`** — `iteration_source` is wired to a step inside the loop. Wire it from a step OUTSIDE the loop.
- **`Loop '<name>' lists member '<X>' but no step with that name exists`** — typo or stale reference. Names are exact and case-sensitive.
- **`Step '<X>' has loop_membership ['<L>'] but loop '<L>' does not list it as a member`** (`LOOP_MEMBERSHIP_MISMATCH`) — asymmetric declaration. Fix BOTH sides.
- **`Step '<X>' belongs to multiple loops`** (`LOOP_MEMBERSHIP_CARDINALITY`) — each step belongs to AT MOST one loop. Pick one and remove the other membership entry.
- **`Loop '<name>' exceeded max_iterations`** — runtime error, not save-time. Raise `config.max_iterations` if the size is expected, OR bound the list length upstream.

### Common gotchas

- **Dict iteration sources don't work.** Wiring `classify_documents.documents` (a dict-of-arrays) directly as `iteration_source` is a frequent failure. Flatten to a list first.
- **There is no `loop_item` variable — read the current item by depending on the loop step.** Internally the runtime injects `iteration_item` / `iteration_index` / `iteration_label` / `loop_parent`, but you never name those directly.
- **Loops run per-iteration timeouts, not one long-running step.** A loop over 100 docs with a 30s extract per doc takes ~50min wall-clock; plan UX accordingly.
- **No `break` / `continue`.** All members run for all iterations. For conditional skipping, wrap the heavy member behind a `decision` member that fires only when needed.
- **Output collation is automatic.** Member steps' per-iteration outputs are collated into arrays in the parent scope — declare a `custom_step` member's `output_schema` accordingly.
- **Display labels matter for UI.** A list of objects without a `display_field`-annotated schema shows "Item 0 / Item 1 / …". Wire from a producer that declares `display_field`, or use a list of primitive labels (e.g. document names).
- **Eval Studio compatibility:** if a loop member will be scored in Eval Studio as a per-document target, make the loop iterate one UserDocument per item (or a one-element list containing one). Document-scoped ground truth is not supported for loop items containing multiple documents or arbitrary records. Keep the member's logical step name stable.

### See also

- `custom_step` — typical upstream flattener that converts dict-of-arrays into list-of-objects for a valid `iteration_source`.
- `classify_documents` — common indirect upstream (its output needs flattening before loop consumption).
- `decision` — used inside loops to gate heavy members (skip iterations conditionally).
- `manual_input` — common loop member for per-row user review.
- `combine_kv_tables` — typical downstream when each iteration extracts a slice and the collated outputs need merging into one record.
