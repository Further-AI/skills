# combine_kv_tables

### What this step does

Merges multiple table envelopes (the `{schema, data, user_documents, metadata}` shape produced by extract / agentic_extraction / KV-shaped inline-code steps) into a single envelope. Use it when several upstream steps each extract a SUBSET of the same logical record (e.g. one extractor pulls insured info, another pulls coverage details) and you need one unified row downstream. It also accepts **grid-shaped sources** — an `agentic_extraction` whose `extraction_schema` is a single-property array-of-objects unwraps to a bare grid at runtime; combine re-wraps it under its author key and merges it alongside the KV sources (see "Common gotchas").

The merge is deterministic and internal (no LLM call). Two `config` knobs control *cross-source key conflicts*; the cell-leaf resolution chain below is not configurable. The runtime:

- **Schema merge**: recursive deep-merge of upstream `schema` objects. Keys present in any source survive; nested objects merge key-by-key.
- **Data merge**: for each leaf key, candidates are filtered to drop missing sentinels (`""`, `"Not available"`, `"n/a"`, `"null"`, `"none"`, empty list/dict). Winner is picked by (1) first edited cell (`is_edited=true` — manual corrections are sticky), (2) highest `confidence_score`, (3) first in source order. All cell metadata (`value`, `confidence_score`, `confidence_reason`, `citations`, `thinking_steps`, `is_edited`) propagates unchanged. The `prefix` conflict strategy (below) namespaces colliding keys *before* this chain so they survive as distinct columns instead of one winning.
- **`user_documents` merge**: union of upstream arrays, deduplicated by `user_document_id`.
- **`metadata` merge**: per-key winning source + source count + conflict policy.

### Required inputs

| Param     | Type    | Required | Source                                                                                                                                                                                                                                                                              |
| --------- | ------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sources` | `array` | YES      | `input_mappings` — list of full KV envelopes from upstream steps. MUST use `resolution_operator: "concat_lists"` so the runtime receives a list of envelopes, not a single dict. MUST point at the WHOLE envelope (`output_attribute: null`), NOT at sub-paths like `data.field_x`. |

### Config keys

`CombineKvTablesConfig` has two optional fields, both governing how keys present in 2+ sources are handled. Cell-level resolution (edit-stickiness, confidence ranking) is NOT configurable.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `conflict_handling_strategy` | `"prefix"` \| `"highest_confidence"` \| `null` | `null` | `null` / `"highest_confidence"` use the cell-leaf chain (edited > highest confidence > first source — the default behavior). `"prefix"` namespaces each source's top-level keys per `prefix_mapping` BEFORE the merge, so colliding keys become distinct columns and nothing is dropped. |
| `prefix_mapping` | list of `{step_name, prefix}` | `null` | Used only when `conflict_handling_strategy` is `"prefix"`. Each named source's top-level keys are prefixed with `[prefix]` (e.g. `key` → `[label] key`). Sources absent from this list merge unprefixed. `step_name` must match a source wired into `sources`; prefix labels must be distinct (the save validator enforces both). |

When you don't need conflict control, leave `config: {}` — the default chain applies.

### Output schema

Standard KV-table envelope, with `data` and `schema` deep-merged across sources:

```json
{
  "schema":          { "type": "object", "properties": { /* deep-merged from upstream schemas */ } },
  "data":            { /* merged leaf values; one entry per unioned key */ },
  "user_documents":  [ /* deduplicated union */ ],
  "metadata":        { /* per-key winning-source trace + source count + conflict policy */ }
}
```

Downstream consumers read `data.<field>` like any other extract step's output. Schema-aware steps (e.g. `submission_summary_generator`) can wire `schema`, `data`, `user_documents`, `metadata` separately. Output type is auto-set (`table`, key-value display) — leave `output_type`/`output_config`/`output_schema` null, or set `output_type: "table"` explicitly.

### Input wiring (input_mappings vs dependencies)

`combine_kv_tables` is `input_mappings`-only — wire `sources` exclusively via `input_mappings` with a `concat_lists` resolution operator. The legacy `dependencies` array is NOT supported as a wiring shape for this step. `dependencies` stays `[]`.

### Common patterns

#### Pattern A — combining two agentic-extraction results

Two upstream `agentic_extraction` steps each pulled a different slice of the record (insured details + coverage details). The combine step merges them into one downstream-friendly envelope.

```json
{
  "name": "Merge Insured + Coverage",
  "type": "combine_kv_tables",
  "input_mappings": [
    {
      "input_type": "dependency",
      "input_parameter_name": "sources",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Extract Insured Info",    "output_attribute": null },
          { "step_name": "Extract Coverage Detail", "output_attribute": null }
        ],
        "resolution_operator": "concat_lists",
        "resolution_config": null
      }
    }
  ],
  "dependencies": [],
  "config": {}
}
```

#### Pattern B — combining N envelopes from a fan-out

A classify-then-extract fan-out produced one envelope per document class. `combine_kv_tables` collapses them back into a single record (they describe the same submission, just split by source type).

```json
{
  "name": "Unified Submission Record",
  "type": "combine_kv_tables",
  "input_mappings": [
    {
      "input_type": "dependency",
      "input_parameter_name": "sources",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Extract from Application", "output_attribute": null },
          { "step_name": "Extract from Loss Runs",   "output_attribute": null },
          { "step_name": "Extract from Quotes",      "output_attribute": null }
        ],
        "resolution_operator": "concat_lists",
        "resolution_config": null
      }
    }
  ],
  "dependencies": [],
  "config": {}
}
```

### Common validation errors and fixes

Save-time checks:

- **`'sources' must use resolution_operator 'concat_lists'`** — your `sources` mapping is wired without `resolution_operator` (or with a different one). Fix: set `"resolution_operator": "concat_lists"` on the dependency. The runtime expects a LIST of envelopes; without `concat_lists` it receives a single dict and rejects.
- **`'sources' dependencies must point at the full envelope (output_attribute=null)`** — one of the `dependency_step_outputs` entries has a non-null `output_attribute` (e.g. `"data"` or `"data.insured_name"`). Fix: set every `output_attribute` in the list to `null`. The merge logic needs the full `{schema, data, user_documents, metadata}` envelope, not a sub-path.
- **`Source step '<X>' is not a KV-table producer`** — you wired a step whose output is not a KV envelope (e.g. `decision`, `classify_documents`, `workflow_dispatcher`). Allowed sources: `extract_from_email`, `extract_from_document`, `extract_from_multiple_sources`, `agentic_extraction`, `generate_qa_table_from_kb`, `generate_qa_table_from_agent`, chained `combine_kv_tables`, and inline-code steps whose `output_schema.data` is declared as an `object`. An `agentic_extraction` source that unwraps to a grid (single-property array-of-objects `extraction_schema`) IS allowed — combine re-wraps the bare grid under its author key before merging. Still NOT allowed: `extract_rows_from_multiple_sources`, `extract_mixed_schema_from_multiple_sources`, `agentic_guideline_check` — the save-time source whitelist rejects these step types, so route their grid output through a different downstream step.
- **`Type conflict for key '<field>': source '<A>' declares 'string', source '<B>' declares 'integer'`** — two upstream schemas disagree on the type of the same field. Fix the upstream extractors so the schemas agree, or rename one of the colliding fields so they don't merge.

### Common gotchas

- **Cell-leaf resolution is hard-coded; key-collision strategy is the only knob.** "First edited cell wins, else highest-confidence, else first source" is not configurable. What you CAN control is cross-source key collisions via `config.conflict_handling_strategy`: leave it `null`/`"highest_confidence"` to let one source win each colliding key, or set `"prefix"` + `prefix_mapping` to keep every source's value as its own namespaced column. For anything beyond that, do the merge in a `custom_step` instead.
- **Grid sources merge under their author key.** An `agentic_extraction` whose schema is a single-property array-of-objects (e.g. `{vehicle_schedule: [...]}`) unwraps to a bare grid at runtime; combine re-wraps it under that key before merging, so the array survives in the merged `data`. Two grid sources resolving to the SAME key still hit whole-value-wins semantics — use the `prefix` strategy to keep both.
- **Arrays are leaves.** The conflict chain picks one whole array — no element-level merge. If you need array-concat semantics, do it in a downstream `custom_step`.
- **Edit-stickiness propagates.** A manually-corrected cell (`is_edited=true`) on ANY source wins over a higher-confidence cell on another source. Re-running extractors after a user edit does NOT overwrite the edit — and if the merged output feeds another `combine_kv_tables`, the edit keeps winning there too.
- **Missing-value sentinels are filtered before ranking.** A source that returned `"Not available"` is treated as if the field was empty for that source — the next-best source wins. Keys with no non-missing value from any source are omitted from `data` entirely (no placeholder cell).
- **`metadata.source_count` per key is useful for confidence triage.** Wire `metadata` if you want to surface "3 of 4 sources agreed" semantics in a report.
- **No re-validation of leaf-value content.** If a source returned a malformed value (e.g. a date string in the wrong format) and that source wins, the malformed value flows through. Add a `custom_step` for cleanup if upstream extractors aren't reliable.
- **Rename a source step ⇒ update `prefix_mapping` too.** `prefix_mapping.step_name` must exactly match a step wired into `sources`.

### See also

- `agentic_extraction`, `extract_from_multiple_sources`, `generate_qa_table_from_agent` — the canonical upstream KV producers.
- `custom_step` — alternative when you need custom merge semantics; declare `output_schema` with `type: "object"` for `data` so downstream steps can still treat the output as a KV envelope.
- `submission_summary_generator` — typical downstream consumer; takes the combined data points + `system_prompt` and emits a narrative.
