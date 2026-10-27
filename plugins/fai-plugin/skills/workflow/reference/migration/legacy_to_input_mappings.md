# Migration: Legacy `dependencies` → `input_mappings`

Old workflows used `dependencies: [{step_name, field_selector}]` to declare both execution order and (loosely) data flow. The current standard is `input_mappings`, which makes both explicit. This guide is for converting an old workflow when you touch it.

## When to migrate

- The user explicitly asks for the migration.
- A workflow uses both `dependencies` and `input_mappings` on the same step (mixed-mode is a hazard — the runtime ignores `dependencies` when `input_mappings` is present).

When merely editing a step that still uses `dependencies`, preserve the existing pattern — do not auto-migrate. New steps always use `input_mappings` only. Never convert `input_mappings` back to `dependencies`, under any circumstances.

For workflows that haven't been touched and still work, leaving them on `dependencies` is fine — the engine still supports it.

---

## The shape difference

### Legacy
```jsonc
{
  "name": "Extract from ACORD",
  "type": "extract_from_document",
  "dependencies": [
    { "step_name": "Classify Documents",
      "field_selector": "ACORD" }       // optional; null = whole output
  ],
  "input_mappings": [],
  "config": { ... }
}
```

The legacy engine merged the upstream output into the step's kwargs implicitly, sometimes filtered by `field_selector`. The receiving step had to know which kwargs to expect by convention.

### Modern
```jsonc
{
  "name": "Extract from ACORD",
  "type": "extract_from_document",
  "dependencies": [],                   // always [] in new work
  "input_mappings": [
    { "input_parameter_name": "document",
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Classify Documents",
            "output_attribute": "documents.ACORD.0" }
        ],
        "resolution_operator": null,
        "resolution_config": null
      } }
  ],
  "config": { ... }
}
```

Each input parameter is **explicit**: name, source, glom path. Single source of truth for both the DAG edge and the data flow.

---

## Conversion mapping

| Legacy field | Modern equivalent |
|---|---|
| `dependencies[*].step_name` | `input_mappings[*].value.dependency_step_outputs[*].step_name` |
| `dependencies[*].field_selector` (string) | `input_mappings[*].value.dependency_step_outputs[*].output_attribute` (glom path) |
| `dependencies[*].field_selector` (list of strings) | Multiple `input_mappings` entries — one per selector — OR a single `input_mapping` with multiple `dependency_step_outputs` + a resolution operator |
| Implicit kwarg merging | Explicit `input_parameter_name` per entry |

### `field_selector` translation

Legacy `field_selector` values were inconsistent — sometimes a category name (`"ACORD"`), sometimes a key path. Translate based on what the upstream step actually emits:

| Legacy `field_selector` | Modern `output_attribute` |
|---|---|
| `null` (whole output merged) | `null` (unchanged) |
| `"ACORD"` (when upstream is `classify_documents`) | `"documents.ACORD"` |
| `"data"` (when upstream is an extraction step) | `"data"` (unchanged) |
| List `["ACORD", "Supplemental"]` | Two `dependency_step_outputs` entries + `resolution_operator: "concat_lists"` |

When in doubt, look at the upstream step's `output_schema` (or its `step_types/<type>.md` reference) to find the right glom path.

---

## Step-by-step procedure

For each step using `dependencies`:

1. **Read the upstream step type** to know its output keys (e.g., `classify_documents` emits `documents` keyed by category).
2. **Read the receiving step's input schema** (`step_types/<type>.md` → "Inputs" section) to know what input params it accepts.
3. **For each legacy `dependency`**, decide which input parameter it was feeding (often only one — the convention was implicit). Create one `input_mapping` per receiving param.
4. **Set `dependencies: []`** on the step.
5. **Validate.** The validator should now check `input_parameter_name` against the receiving schema and `output_attribute` against the upstream schema.

If a legacy `dependency` was just for execution ordering (no data needed), still convert to an `input_mapping` — usually with `output_attribute: null` to express "I depend on this step finishing." Or use `pause`'s `steps_before_pause` pattern for explicit gates.

---

## Common conversion patterns

### One-to-one dependency

```jsonc
// Before
"dependencies": [{ "step_name": "Prepare Documents", "field_selector": null }]

// After
"input_mappings": [
  { "input_parameter_name": "documents",
    "input_type": "dependency",
    "value": {
      "dependency_step_outputs": [
        { "step_name": "Prepare Documents", "output_attribute": "documents" }
      ],
      "resolution_operator": null, "resolution_config": null
    } }
]
```

### Category-keyed selector

```jsonc
// Before
"dependencies": [{ "step_name": "Classify Documents", "field_selector": "ACORD" }]

// After
"input_mappings": [
  { "input_parameter_name": "documents",
    "input_type": "dependency",
    "value": {
      "dependency_step_outputs": [
        { "step_name": "Classify Documents", "output_attribute": "documents.ACORD" }
      ],
      "resolution_operator": null, "resolution_config": null
    } }
]
```

### Multiple selectors (combine)

```jsonc
// Before
"dependencies": [
  { "step_name": "Classify Documents", "field_selector": ["ACORD", "Supplemental"] }
]

// After
"input_mappings": [
  { "input_parameter_name": "documents",
    "input_type": "dependency",
    "value": {
      "dependency_step_outputs": [
        { "step_name": "Classify Documents", "output_attribute": "documents.ACORD" },
        { "step_name": "Classify Documents", "output_attribute": "documents.Supplemental" }
      ],
      "resolution_operator": "concat_lists",
      "resolution_config": { "remove_duplicates": false, "flatten_nested": false }
    } }
]
```

### Multiple sources for different inputs

```jsonc
// Before (single dependency loosely fed multiple kwargs)
"dependencies": [{ "step_name": "Prepare Documents", "field_selector": null }]

// After (one input_mapping per receiving param)
"input_mappings": [
  { "input_parameter_name": "documents",
    "input_type": "dependency",
    "value": { "dependency_step_outputs": [{ "step_name": "Prepare Documents", "output_attribute": "documents" }], "resolution_operator": null, "resolution_config": null } },
  { "input_parameter_name": "body_html",
    "input_type": "dependency",
    "value": { "dependency_step_outputs": [{ "step_name": "Prepare Documents", "output_attribute": "body_html" }], "resolution_operator": null, "resolution_config": null } },
  { "input_parameter_name": "subject",
    "input_type": "dependency",
    "value": { "dependency_step_outputs": [{ "step_name": "Prepare Documents", "output_attribute": "subject" }], "resolution_operator": null, "resolution_config": null } }
]
```

---

## Watch-outs

- **Don't mix `dependencies` and `input_mappings` on the same step.** The validator treats them as alternatives — having both leads to ambiguity in execution.
- **Step names are case-sensitive.** Spell-check the migrated `step_name` against the actual step's `name` field.
- **Use canonical input/output keys.** Legacy workflows often used wrong names (e.g., `email_body` from prepare_documents) that "worked" because of loose merging. The new validator will reject those — replace with the canonical names listed in `validation_errors.md` §3.
- **Function steps with custom `code` need schemas in modern format.** If you migrate a function step from `dependencies` to `input_mappings`, you must add `config.input_schema` and `config.output_schema`.
- **Validate after every step migration.** Catches mistakes incrementally rather than all at once.

---

## Why this exists

The `dependencies` model was implicit and brittle:

- The receiving step had to know which kwargs to expect by name conventions, not by declaration.
- `field_selector` was overloaded (category name? glom path? list?).
- Multi-source combination required custom function steps because there was no operator concept.
- The validator couldn't catch wiring errors because the schema contract wasn't expressed.

`input_mappings` is explicit and statically checkable:

- Receiving step's input schema declares accepted parameters.
- Source's output schema declares accessible fields.
- Resolution operators handle multi-source combination declaratively.
- The validator can check both ends at save time.

Migrating preserves all the runtime behavior while making the workflow self-documenting and debuggable.
