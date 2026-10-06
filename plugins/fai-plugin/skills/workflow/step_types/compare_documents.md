# `compare_documents`

**Extract key fields from each of several documents and compare them. Produces a compare log (`output_type: "compare_log"`).**

> **Edit-only.** This type is hidden from the builder's add-step picker. Editing an existing instance is fine; for new work, split into `extract_from_multiple_sources` (per document) + `compare_document_data`.

## When to use it

Only when maintaining an existing instance. For new comparisons: extract each document's data first, then use `compare_document_data` — it avoids duplicate LLM cost and gives you control over the extraction.

## Config fields (`CompareDocumentsConfig`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `extraction_schemas` | `List[SchemaPromptPairConfig]` | **required (≥1)** | One or more schema + prompt pairs |

### `SchemaPromptPairConfig`

| Field | Type | Notes |
|---|---|---|
| `schema` | `Dict` | JSON Schema for this comparison section |
| `system_prompt` | `str` | Extraction instructions |
| `schema_name` | `str \| null` | Display name (e.g., "Coverage Terms") |

Hardcoded behavior: comparison strategy is always `schema_based`.

## Inputs

Documents — wired into the step (exact input names depend on builder wiring; look at a working example in the reference/).

## Outputs

Compare log format (`output_type: "compare_log"`).

## Defaults

- `output_type`: `compare_log`
- Incremental: `rerun` / `no_op` only.

## Best practices

- **Use one schema per comparison "section"** — e.g., one for coverage, one for financials, one for loss history. They render as separate sections.
- **Schemas can be small and focused** — don't cram everything into one.
- **Name schemas clearly** (`schema_name: "Coverage Terms"`) for UI clarity.
- **Prefer `compare_document_data`** if you've already extracted the data in earlier steps — avoids duplicate LLM cost.

## Common gotchas

- Empty `extraction_schemas` → validator rejects (`validate_comparison_config` requires ≥1).
- Expecting TableV1-shaped output — this step produces `compare_log`, not `table`. Downstream consumers render it differently.

## Minimal example

```json
{
  "name": "Compare Quotes",
  "type": "compare_documents",
  "input_mappings": [],
  "dependencies": [],
  "config": {
    "extraction_schemas": [
      { "schema": {
          "type": "object",
          "properties": {
            "per_occurrence":  { "type": "string", "description": "Per-occurrence limit" },
            "aggregate_limit": { "type": "string", "description": "Aggregate limit" }
          }
        },
        "system_prompt": "Extract policy limits from the quote.",
        "schema_name":   "Coverage Limits" },
      { "schema": {
          "type": "object",
          "properties": { "premium": { "type": "string", "description": "Total annual premium" } }
        },
        "system_prompt": "Extract pricing from the quote.",
        "schema_name": "Pricing" }
    ]
  }
}
```
