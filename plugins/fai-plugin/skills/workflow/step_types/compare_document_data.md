# compare_document_data

### What this step does

Compares pre-extracted structured data across N documents on a fixed `comparison_schema` and renders the result as a TableV1 with `display_type=COMPARE`. Each top-level field in the schema is compared independently:

- **Primitive leaves** (string / number / boolean at the top level) → cheap deterministic value analysis, no LLM round-trip per field.
- **Object fields** → one LLM agent call — sub-attributes are evaluated together because they share context.
- **Array fields** → one LLM agent call that does ALIGNMENT (which item-from-doc-A pairs with which item-from-doc-B) AND per-field MATCHING in a single pass.

The schema's per-field `description` is the comparison instruction surface — the agent reads it as "how to decide alignment / what counts as a match". The step-level `system_prompt` is the overarching tone-setter and is injected into every comparison call alongside the per-field description.

After per-field comparison the block also generates a structured LLM summary (`metadata.comparison.summary`, `important_matches`, `recommended_actions`) and computes `total_fields` / `total_mismatches` counts (leaves = 1, objects = N sub-fields, arrays = sum across alignment groups).

### Required inputs

| Parameter | Type | Required | Allowed modes | Purpose |
| --- | --- | --- | --- | --- |
| `data_points` | `array<object>` | YES | `dependency` only | List of extraction `data` objects, one per document, **aligned by index** with `user_documents`. Each item is typically `<extract_step>.data`. |
| `user_documents` | `array<object>` | YES | `dependency` only | List of source-document references aligned by index with `data_points`. Used only for display names (`filename` / `name`) in the rendered table headers. |
| `system_prompt` | `string` | no | `static` or `dependency` | Overarching comparison instructions (alignment rules, matching tolerances, formatting rules). Always pass via `input_mappings`, not `config.system_prompt`. |

Runtime guard: the block raises `ValueError("Comparison requires at least 2 data points, got <n>")` when `len(data_points) < 2`. There is no JSON-Schema way to express "min 2 items" at save time, so this is a runtime failure — your two extraction upstreams must BOTH actually produce data on the same execution.

`data_points` and `user_documents` must be index-aligned. The canonical way to build both lists is to wire two (or more) upstream extraction outputs through `resolution_operator: "concat_lists"`, using the same upstream step order for both inputs — see Pattern 1 below.

### Config keys

Mirrors `CompareDocumentDataConfig`.

| Key | Type | Default | Purpose |
| --- | --- | --- | --- |
| `comparison_schema` | JSON Schema (`type: "object"` with `properties`) | required | Fields to compare. Each field's `description` is read as the comparison instruction for that field. Nested object `properties` and array `items.properties` descriptions are also honored. |
| `system_prompt` | string \| null | `null` | LEGACY config slot. Do NOT set here — pass via `input_mappings`. |
| `model` | AIModel | workflow default | Model used for all object/array comparison agent calls AND the final summary call. Recommend `gpt-5.5` — comparison alignment is reasoning-heavy; `gpt-5.1` for cost-sensitive flows. |

There is no `reasoning_effort` / `verbosity` field on this config — don't add them.

### Output schema (compare table)

A TableV1 with `display_type=COMPARE`:

```json
{
  "type": "object",
  "properties": {
    "schema":         {"type": "object", "description": "JSON Schema of the extraction fields (array-of-objects)"},
    "data":           {"type": "array",  "description": "List of extraction data objects, one per data point/document"},
    "user_documents": {"type": "array",  "description": "Source documents for each data point"},
    "metadata":       {"type": "object", "description": "Comparison tree with match/mismatch annotations for all fields"}
  }
}
```

The `metadata.comparison` payload has three recursive node types — leaf, object, array — that compose to mirror the input schema shape:

- `leaf`: `{type, match, confidence_score, confidence_reason}`
- `object`: `{type, match, confidence_score, confidence_reason, fields: {<name>: <node>}}`
- `array`: `{type, alignment_groups: [{group_id, doc_items, match, confidence_score, confidence_reason, fields}]}`

Top-level keys on `metadata.comparison`: `documents` (per-doc display names), `fields` (the comparison tree), `total_fields`, `total_mismatches`, `summary` (HTML string), `important_matches`, `recommended_actions`.

`data` is filtered to only the schema's top-level keys, so downstream consumers can rely on the keys matching `comparison_schema.properties` exactly.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is already there.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

`system_prompt` MUST live in `input_mappings` (typically `input_type: "static"`) for new steps, not in `config.system_prompt`. `comparison_schema` is NOT in `input_mappings` — it lives on `step.config`.

### Common patterns

Note: this step type is rarely used, and existing usage is dominated by variants of the same insurance "policy packet vs binder" comparison. Treat the patterns below as the battle-tested baseline; other shapes are unproven at the step level.

#### 1) Two-class comparison (the battle-tested shape)

Upstream is a `Classify Documents` + two parallel extraction steps — one extraction per class bucket — both of which produce the same `comparison_schema`-shaped `data`. The compare step concatenates the two extraction outputs into a 2-row comparison.

```json
{
  "name": "Compare Document Data",
  "type": "compare_document_data",
  "input_mappings": [
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          {"step_name": "Combine Class A Extractions", "output_attribute": "data"},
          {"step_name": "Combine Class B Extractions", "output_attribute": "data"}
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
          {"step_name": "Combine Class A Extractions", "output_attribute": "user_documents"},
          {"step_name": "Combine Class B Extractions", "output_attribute": "user_documents"}
        ],
        "resolution_operator": "concat_lists",
        "resolution_config": null
      },
      "input_parameter_name": "user_documents"
    },
    {
      "input_type": "static",
      "value": "You are comparing two documents for the same entity. Alignment vs Matching: ALIGNMENT groups items that logically represent the same entity (use semantic similarity, form-number bases, addresses). MATCHING then compares each attribute. Pure formatting differences (spacing, punctuation, case, common abbreviations) are matches. Different numbers / dates are ALWAYS a mismatch. Missing-in-one-doc = mismatch.",
      "input_parameter_name": "system_prompt"
    }
  ],
  "config": {
    "comparison_schema": {
      "type": "object",
      "properties": {
        "effective_date":   {"type": "string", "title": "Effective Date",   "description": "Coverage start date. Format: MM/DD/YYYY with leading zeros"},
        "expiration_date":  {"type": "string", "title": "Expiration Date",  "description": "Coverage end date. Format: MM/DD/YYYY with leading zeros"},
        "carrier_names":    {"type": "array",  "items": {"type": "string"}, "title": "Carrier Name(s)", "description": "Array of participating carrier names; compare the full list strictly."},
        "schedule_items":   {
          "type": "array",
          "title": "Schedule Items",
          "description": "Per-item schedule rows (locations / endorsements / forms). Align rows by base identifier; compare attributes per row.",
          "items": {
            "type": "object",
            "properties": {
              "identifier":  {"type": "string", "description": "Base identifier (form number / address). Ignore edition suffixes / spacing for alignment."},
              "description": {"type": "string", "description": "Long description; semantic equivalence = MATCH."},
              "value":       {"type": "string", "description": "Numeric attribute; different numbers = MISMATCH always."}
            }
          }
        }
      }
    },
    "model": "gpt-5.5"
  }
}
```

#### 2) N-way comparison from a single upstream array

Same step shape, but `data_points` and `user_documents` come from a single upstream extraction whose output `data` is already an array of objects (e.g., an `extract_rows_from_multiple_sources` or a custom step that maps each doc → one extraction). In that case use a single `dependency_step_outputs` entry with no `concat_lists` operator.

```json
{
  "input_type": "dependency",
  "value": {
    "dependency_step_outputs": [{"step_name": "Extract Per-Doc Fields", "output_attribute": "data"}],
    "resolution_operator": null,
    "resolution_config": null
  },
  "input_parameter_name": "data_points"
}
```

Caveat: this shape is NOT battle-tested for `compare_document_data` — extrapolate from the schema, validate manually.

### Common validation errors and fixes

| Error message / signal | Fix |
| --- | --- |
| `Missing required input mappings: ['data_points']` or `['user_documents']` | Wire both. `data_points` and `user_documents` are non-negotiable and must be index-aligned. |
| `Parameter 'data_points' does not allow mode 'static'` (or same for `user_documents`) | Both arrays only accept `dependency`. There is no way to hardcode comparison data — it always comes from upstream extractions. |
| `Output attribute '<path>' not found in step '...' output schema. Available top-level properties: [...]` | Check the upstream extraction step's output_schema. Standard extraction-step output keys are `data`, `user_documents`, `schema`, `metadata` — use `.data` and `.user_documents` verbatim. |
| `Parameter 'data_points' expects type 'array' but step '...' outputs type 'object'` | An upstream extraction step's `data` is an object, not an array (true for `extract_from_multiple_sources`). Either route through a `custom_step` that wraps it (`return {"data": [data], "user_documents": [user_documents]}`) or use a step type whose `data` is natively a list. |
| Warning: `system_prompt is in config but not in input_mappings` | Move the prompt out of `config.system_prompt` and into `input_mappings` with `input_type: "static"`. The runtime still falls back to `config.system_prompt` for legacy workflows, but new steps must use the modern shape. |
| Runtime: `ValueError: Comparison requires at least 2 data points, got 1` (or 0) | `data_points` resolved to fewer than 2 items at runtime. Either the upstream bucket is empty on this execution, or `concat_lists` is missing and you're getting a single-step output. Fix the upstream wiring; this never trips at save time. |
| Empty / wrong `data` in output | `data` is filtered to keys present in `comparison_schema.properties`. If a field is missing from the rendered table, either the schema doesn't declare it or the upstream extraction's data key name doesn't match the schema key — they must be byte-identical (case-sensitive). |

### Common gotchas

- **`comparison_schema` keys MUST match upstream extraction key names exactly** — the block filters `data_points` to `comparison_schema.properties.keys()`, so any drift between the extraction's `extraction_schema` and the compare step's `comparison_schema` silently drops fields from the rendered table. Easiest path: copy the relevant subtree from the upstream extraction schema verbatim, then trim — never rename.
- **Field `description` is load-bearing, not cosmetic** — it's read by the comparison agent as the per-field instruction. Vague descriptions ("the name") produce noisy mismatches; concrete descriptions ("Primary insured entity legal name. Include DBA in source formatting.") produce stable matches. Put alignment/matching rules in `description` rather than in the step-level `system_prompt`.
- **`system_prompt` is global; `description` is per-field** — use `system_prompt` for cross-cutting policy ("N/A and empty are equivalent", "different numbers always mismatch", "ignore edition suffixes on form numbers") and `description` for per-field formatting/alignment hints. Don't duplicate.
- **`data_points` and `user_documents` MUST be index-aligned** — the block zips them positionally to label each row. If your two `concat_lists` lists are in different orders, the row labels will be swapped silently with no validation warning. Always wire both inputs from the SAME `dependency_step_outputs` order.
- **`user_documents` is display-only** — used ONLY to label rows in the rendered comparison. The actual comparison runs over `data_points`. A missing or short `user_documents` array falls back to `"Data Point <i>"` labels instead of failing.
- **Minimum 2 data points is enforced at RUNTIME, not save time** — if one of your upstream classification buckets is empty on a given execution, the step will fail with `ValueError`. Plan for this if either bucket can be empty (gate with a `decision` step, or accept the failure).
- **Output `display_type=COMPARE` is set by the block, not config** — the UI picks the compare renderer based on this; no option exists to render the same output as a regular grid table.
- **Per-field comparison uses different code paths by type** — primitive leaves are cheap (no LLM), but objects and arrays each take a full LLM call. Schemas with many top-level object/array fields ARE expensive — keep nesting moderate and prefer flat primitive leaves where the comparison is "literal equality with formatting tolerance".
- **`compare_documents` is the deprecated predecessor** — it does extract-then-compare in one shot using `extraction_schemas`. Never create a new one. Migration path: split it into N parallel `extract_from_multiple_sources` steps (one per schema/prompt pair) + a single `compare_document_data` downstream.

### See also

- `compare_documents` — deprecated predecessor; listed only so you recognize it in legacy workflows.
- `extract_from_multiple_sources` — the typical upstream when you have multiple classification buckets each feeding their own extraction (Pattern 1).
- `extract_rows_from_multiple_sources` — the typical upstream when each document yields one comparison row (Pattern 2) — its `data` is already an array.
- `classify_documents` — the canonical step upstream of the per-bucket extractions; its `documents.<ClassName>` outputs feed the parallel extractions.
