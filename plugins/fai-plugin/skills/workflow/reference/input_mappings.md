# Input Mappings

How data flows between steps. Every `WorkflowStep` declares an `input_mappings` array; each entry populates one input parameter of the receiving step from a static value, a variable, an AI prompt, another step's output, or inline code.

This file covers: the `InputMapping` shape, the `WorkflowField` pattern (used in many other places besides input mappings), glom paths into upstream output, and the eight resolution operators that combine multi-source values.

---

## `InputMapping` shape

`InputMapping` extends `WorkflowField` with one extra field, `input_parameter_name`:

```jsonc
{
  "input_parameter_name": "documents",       // which step-input this fills
  "input_type": "dependency | static | variable | prompt | function",
  "value": "<shape depends on input_type>"
}
```

### `input_type` values and what `value` should be

| `input_type` | `value` type | Meaning |
|---|---|---|
| `static`     | any JSON    | Literal value used as-is. |
| `variable`   | string      | Name of a variable in scope (from `DecisionConfig.input_schema`, `manual_input`, etc.). |
| `prompt`     | string      | AI prompt string; LLM generates a value at runtime. |
| `dependency` | object      | `FieldDependency` — pull values from prior-step outputs and optionally combine. |
| `function`   | string      | Inline Python code: `def fn(execution_log): return ...`. |

---

## `FieldDependency` (`input_type: "dependency"`)

```jsonc
{
  "dependency_step_outputs": [
    { "step_name": "Source Step",
      "output_attribute": "data.field"   // glom path; null = entire output dict
    }
  ],
  "resolution_operator": null,           // required when len(dependency_step_outputs) > 1
  "resolution_config": null              // shape depends on operator
}
```

- `step_name` is **case-sensitive** and must exactly match a step's `name`. The literal `"Workflow Dispatcher"` is also valid (auto-created at runtime).
- `output_attribute` is a **glom-style** path (Python `glom` package): dot-delimited keys, numeric indices for arrays. `null` returns the entire output dict.
- Multi-source mappings (`len > 1`) require a `resolution_operator`.

### Glom path examples

| Path | Returns |
|---|---|
| `null` | Whole output dict |
| `"data"` | The `data` field |
| `"data.insured_name"` | The `insured_name` key inside `data` |
| `"documents.ACORD"` | All ACORD docs from a classify step |
| `"documents.ACORD.0"` | First ACORD doc only |
| `"metadata.items"` | The `items` array inside `metadata` (e.g., from `extract_guidelines`) |

The validator only checks the **first segment** of `output_attribute` against the upstream step's output schema. Deeper paths fail at runtime, not at validation.

### Extraction outputs are cell-wrapped — `.value` matters

Every leaf in an extraction step's `data.<field>` is wrapped at runtime as a **cell** — `{value, confidence_score, confidence_reason, citations, thinking_steps}`. When wiring a downstream step from an extracted field, the path you almost always want is `data.<field>.value` (the extracted scalar), not `data.<field>` (the whole cell object).

Affects: `extract_from_multiple_sources`, `extract_rows_from_multiple_sources`, `extract_mixed_schema_from_multiple_sources`, `agentic_extraction`, `agentic_guideline_check`, `sov_mapping`, `generate_qa_table_from_agent`, `multi_column_qa`.

```jsonc
// Correct — the extracted scalar:
{ "step_name": "Extract Submission Information", "output_attribute": "data.applicant_name.value" }

// Also valid — confidence/citation siblings:
{ "output_attribute": "data.applicant_name.confidence_score" }
{ "output_attribute": "data.applicant_name.citations.0" }

// Usually wrong — delivers the whole cell object at runtime, not the scalar:
{ "output_attribute": "data.applicant_name" }
```

For row-style extractions, index the row first: `data.0.<column>.value`.

#### A bare `data.<field>` is not rejected at save time — it fails at runtime

Worth being precise about, because the failure mode is quiet. The platform
keeps two views of the same extraction schema: the UI view, where primitive
leaves are cell-wrapped so the canvas can render confidence badges and
citation popovers, and the validator view, where they stay plain primitives so
save-time type checking sees the underlying type. The path walker bridges them
by synthesizing the cell shape on demand whenever a primitive leaf is followed
by `value`, `confidence_score`, `confidence_reason`, `citations` or
`thinking_steps`.

The consequence: `data.applicant_name` type-checks as `string` at save time
and the save succeeds, but at runtime the consumer receives
`{value, confidence_score, ...}`. A step expecting a scalar gets a dict, and
depending on what it does with it you get a crash, a stringified dict in your
output, or a silently wrong comparison.

So a bare `data.<field>` wired into a scalar parameter is a real defect that
save-time validation will not catch for you. Wiring one into a parameter that
genuinely wants the cell (a `custom_step` reading `confidence_score` to gate
on extraction confidence, for instance) is legitimate and common — declare
that parameter as `object` so the intent is visible.

#### Only primitive leaves are wrapped — nested arrays and objects stay navigable

Cell wrapping happens leaf-by-leaf during a structural walk of the extraction
schema, so containers keep their shape and only scalars become cells:

- An `array` stays an `array`. Its element schema is transformed in turn, and
  the element's glom path gains a `.0` segment.
- An `object` stays an `object`. Each property is transformed in turn.
- A primitive (`string`, `number`, `boolean`) becomes a `cell`.

This matters most for a schema that declares an array **inside** an object,
which is common on mixed-schema extractions (scalars plus a schedule of rows
in one call). The array node itself has no `value` key, so a path that stops
at the array and appends `.value` resolves to nothing:

```jsonc
// Schema: { vehicles: { type: array, items: { vin: string, year: number } } }

// Correct — index the row, then take the cell's value:
{ "output_attribute": "data.vehicles.0.vin.value" }

// Correct — the whole array of row objects (each column still cell-wrapped):
{ "output_attribute": "data.vehicles" }

// Wrong — an array node has no `value`. Resolves to nothing at runtime, and
// every downstream step then runs green on empty data:
{ "output_attribute": "data.vehicles.value" }
```

The same rule applies at any depth: a nested object's fields are reached by
name (`data.limits.each_occurrence.value`), never through a `.value` on the
object itself.

An array of **scalars** follows the rule too, which catches people out. The
array stays an array and its primitive element becomes a cell, so a declared
`aliases: [string]` reads as `data.aliases.0.value` for one entry, or
`data.aliases` for the whole list. `data.aliases.value` fails: the array node
has no `value` key, and the walker will not synthesize a cell on a node that
already has `items`. A scalar list is not a single leaf, however much it looks
like one.

### Save-time type compatibility (directional)

The platform validates type compatibility between every dependency source path and the consuming parameter's declared type; mismatches reject the save with `Parameter '<name>' expects type '<X>' but step '<Y>' outputs type '<Z>'`. Rules are directional — a `file` IS-A `object`, but a generic `object` is NOT-A `file`; `video_file` IS-A `file`, not the reverse:

| Expected (input) | Accepted actuals (output) |
|---|---|
| `string` | `string`, `enum` |
| `enum` | `enum`, `string` |
| `number` | `number`, `integer` |
| `integer` | `integer`, `number` |
| `boolean` | `boolean` |
| `array` | `array` |
| `object` | `object`, and any named type (`file`, `video_file`, `cell`, `email_headers`, `citation`, `docx_image`, `sov_field`) |
| `file` | `file`, `video_file` |
| `video_file`, `cell`, `email_headers`, `docx_image`, `citation`, `sov_field` | only themselves |

Takeaways: a custom code step returning a document must declare `{"type": "file"}` (not a generic object with `user_document_id`); wiring extraction fields uses `.value` (above). Lists of typed values are plain arrays: `{"type": "array", "items": {"type": "file"}}` — there is no `file_list` named type at the step-output layer.

### File-typed inputs — three sources

A step input that takes a document can be wired three ways:

- `input_type: "dependency"` — runtime documents produced by an upstream step (the usual case).
- A `manual_input` step's file field — the operator uploads at run time; wire the manual_input step's output field.
- `input_type: "static"` with a **build-time file reference** baked into the workflow: `{"input_type": "static", "value": {"user_document_id": "<id>", "filename": "<name>"}}` (or a list of such dicts). Each dict must carry a non-empty `user_document_id`; the platform validates existence + ownership on save. Use for a fixed reference doc / template / guideline that's identical on every run, so the operator doesn't re-upload it each time.

### Special-case: knowledge_base output

The mapping resolver wraps `knowledge_base` step output as `{"kb": raw_output}` before glom resolution. Always use `output_attribute: "kb"` (or `"kb.collection_name"`) — never the raw keys like `"collection_name"`. See `step_types/knowledge_base.md`.

### Special-case: `MANUAL_INPUT` output

Validator skips the `output_attribute` first-segment check for `manual_input` sources because manual_input fields are spread directly into the output (no `user_inputs` wrapper). Use `output_attribute: "<field_name>"` directly.

---

## Resolution operators (multi-source mappings only)

When `dependency_step_outputs` has more than one entry, `resolution_operator` is required. **Names are LOWERCASE.**

| Operator | Purpose | Config class | Notes |
|---|---|---|---|
| `concat_lists` | Combine values into one list | `ConcatListsConfig` | Non-list values are wrapped. `flatten_nested` flattens one level. |
| `merge_dicts` | Merge dicts | `MergeDictsConfig` | Shallow by default; `deep_merge: true` recurses. Conflict resolution: `last_wins` / `first_wins` / `error_on_conflict`. |
| `numeric_add` | Sum | `NumericConfig` | `None` operands need `operand_default_value` or raise. |
| `numeric_subtract` | Subtract (left-associative over `values[0]`) | `NumericConfig` | |
| `numeric_multiply` | Multiply | `NumericConfig` | |
| `numeric_divide` | Divide (left-associative) | `NumericConfig` | Division by zero raises. |
| `string_join` | Join string reps | `StringJoinConfig` | Only field is `separator` (default `", "`). |
| `custom_function` | Call user code | `FunctionConfig` | Values passed as positional args; `execution_log_id` injected as kwarg. Mutex `code` / `func`. |

### Operator config field names (pydantic-validated)

```jsonc
// ConcatListsConfig
{ "remove_duplicates": false,
  "flatten_nested": false }      // NOT flatten_nested_lists

// MergeDictsConfig
{ "conflict_resolution": "last_wins",   // last_wins | first_wins | error_on_conflict — NOT override_existing_keys
  "deep_merge": false }

// NumericConfig
{ "operand_default_value": 0 }   // optional; required if any operand can be None

// StringJoinConfig
{ "separator": ", " }            // ONLY field. NO skip_null_or_empty.

// FunctionConfig (for custom_function operator)
{ "code": "def fn(*values, execution_log_id, **kwargs): return ...",   // OR
  "func": "registered_function_name",                                   // mutex with code
  "kwargs": { /* static extras */ }
}
```

---

## `WorkflowField` — the same pattern, used elsewhere

The `{input_type, value}` pair (without `input_parameter_name`) is reused across the system, not just in input_mappings:

- **Decision operands** (`Comparison.left_operand` / `right_operand`) — see `step_types/decision.md`
- **Step titles** — `start_title` / `end_title` can be a bare string OR a `WorkflowField`
- **Custom workflow columns** — `WorkflowColumnV1.value_source` — see `options.md`

All five `input_type` values work in those contexts too.

---

## Worked examples

### 1. Single-source mapping (the common case)

```jsonc
{
  "input_parameter_name": "documents",
  "input_type": "dependency",
  "value": {
    "dependency_step_outputs": [
      { "step_name": "Classify Documents", "output_attribute": "documents.ACORD" }
    ],
    "resolution_operator": null,
    "resolution_config": null
  }
}
```

### 2. Combine two classified categories

```jsonc
{
  "input_parameter_name": "documents",
  "input_type": "dependency",
  "value": {
    "dependency_step_outputs": [
      { "step_name": "Classify Documents", "output_attribute": "documents.ACORD" },
      { "step_name": "Classify Documents", "output_attribute": "documents.Supplemental" }
    ],
    "resolution_operator": "concat_lists",
    "resolution_config": { "remove_duplicates": false, "flatten_nested": false }
  }
}
```

### 3. Custom function combining two extractions

```jsonc
{
  "input_parameter_name": "data_points",
  "input_type": "dependency",
  "value": {
    "dependency_step_outputs": [
      { "step_name": "Extract Policy", "output_attribute": "data" },
      { "step_name": "Extract Binder", "output_attribute": "data" }
    ],
    "resolution_operator": "custom_function",
    "resolution_config": {
      "code": "def combine(policy, binder, execution_log_id, **kw): return [policy, binder]"
    }
  }
}
```

### 4. Static literal

```jsonc
{
  "input_parameter_name": "system_prompt",
  "input_type": "static",
  "value": "Extract the effective date and premium."
}
```

### 5. Inline function-based input (one-off compute)

```jsonc
{
  "input_parameter_name": "tier",
  "input_type": "function",
  "value": "def fn(execution_log):\n    score = execution_log.result.steps[2].output['data']['triage_score']\n    return 'high' if score > 80 else 'low'"
}
```

### 6. AI-prompt-generated value

```jsonc
{
  "input_parameter_name": "subject",
  "input_type": "prompt",
  "value": "Generate a one-line email subject summarizing the submission status."
}
```

---

## Validation rules (enforced at save time)

- `input_parameter_name` must be in the receiving step's input schema (except `function` steps, which accept arbitrary param names).
- `value` must match the type implied by `input_type` (dict for `dependency`; string for `prompt`/`variable`/`function`; anything for `static`).
- Dependency step names must exist (or be `"Workflow Dispatcher"`).
- `output_attribute` first segment must exist in upstream step's output schema (KB and `manual_input` are special-cased).
- Multi-source mappings require `resolution_operator`.
- `custom_function` operator requires a valid `FunctionConfig` with `code` or `func`.
- Resolution operator names must be **lowercase**.
- Resolution config field names must match the pydantic models (e.g., `flatten_nested`, NOT `flatten_nested_lists`).

For the full validator catalog: `validation_errors.md`.
