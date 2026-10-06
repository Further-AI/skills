# Pattern: Knowledge Base Wiring

How to wire `knowledge_base` and downstream consumers (`agentic_extraction`, `multi_column_qa`, `generate_qa_table_*`, `submission_summary_generator`).

## Position in the pipeline

```
Workflow Dispatcher (auto)
  → Prepare Documents
  → Initialize Knowledge Base    (in parallel with Classify Documents)
  → Classify Documents
  → KB consumers                 (agentic_extraction, QA, summary, etc.)
```

The KB and classifier don't depend on each other, so when `options.enable_parallel_execution: true`, they run concurrently.

## Step 1: Initialize the KB

```jsonc
{
  "name": "Initialize Knowledge Base",
  "type": "knowledge_base",
  "input_mappings": [
    { "input_parameter_name": "documents",
      "input_type": "dependency",
      "value": { "dependency_step_outputs": [
                   { "step_name": "Prepare Documents", "output_attribute": "documents" }],
                 "resolution_operator": null, "resolution_config": null } }
  ],
  "dependencies": [],
  "config": {}
}
```

**Notes:**
- One KB per workflow. Don't fragment.
- Feed the **full** prepared-documents list — don't pre-filter.
- Defaults are tuned for insurance docs; only override `embedding_model_type` / `pdf_chunker_type` / `query_modes` with a reason.
- Step name `"Initialize Knowledge Base"` is the convention used everywhere.

## Step 2: Wire to KB consumers

Every KB-consuming step uses the same two input mappings: `kb` and `documents`. The `documents` input is **required** for citations to resolve to source files (and for category metadata in agent steps — without it, RAG results show as `"chat"` instead of categorized docs).

### Canonical KB input mapping

```jsonc
{
  "input_parameter_name": "kb",
  "input_type": "dependency",
  "value": {
    "dependency_step_outputs": [
      { "step_name": "Initialize Knowledge Base", "output_attribute": "kb" }
    ],
    "resolution_operator": null,
    "resolution_config": null
  }
}
```

### Canonical `documents` input mapping (varies by source)

```jsonc
// For agentic_extraction — feed full classify output (gives category metadata)
{
  "input_parameter_name": "documents",
  "input_type": "dependency",
  "value": {
    "dependency_step_outputs": [
      { "step_name": "Classify Documents", "output_attribute": "documents" }
    ],
    "resolution_operator": null, "resolution_config": null
  }
}

// For QA / summary — usually prepared docs are fine
{
  "input_parameter_name": "documents",
  "input_type": "dependency",
  "value": {
    "dependency_step_outputs": [
      { "step_name": "Prepare Documents", "output_attribute": "documents" }
    ],
    "resolution_operator": null, "resolution_config": null
  }
}
```

## Why `output_attribute: "kb"` works

The `knowledge_base` step's raw output has top-level keys (`knowledge_base_initialized`, `collection_name`, `sub_collection_name`, etc.). But the mapping resolver special-cases knowledge-base output and wraps it as `{"kb": raw_output}` before resolving any downstream `output_attribute`.

Consequences:
- `output_attribute: "kb"` returns the whole wrapped object.
- `output_attribute: "kb.collection_name"` returns just that field.
- `output_attribute: "collection_name"` does NOT work — wrapping hides raw keys.
- `input_parameter_name: "knowledge_base_instance"` is not the canonical name. Always `"kb"`.

## Common mistakes

| Mistake | Fix |
|---|---|
| Skipping `documents` on KB consumers | Always wire both `kb` and `documents`. |
| `input_parameter_name: "knowledge_base_instance"` | Use `"kb"`. |
| `output_attribute: "collection_name"` (raw key) | Use `"kb"` or `"kb.collection_name"`. |
| Reconstructing `KnowledgeBase(collection_name=...)` in a function step | The resolved `kb` value is already a usable KB object — pass it through. |
| Building two KBs to "filter" | KB-wide search is the intended pattern. Use document classification + step-level filtering instead. |

## When to skip the KB

Not every workflow needs one. Skip it if:

- All extraction is single-document or per-classified-subset (Reducto handles grounding).
- Q&A is satisfied by `extract_from_multiple_sources` rather than agentic search.
- The submission is small enough that RAG provides no benefit.

Add it when:

- Using `agentic_extraction`, `multi_column_qa`, `generate_qa_table_*`, or `submission_summary_generator` with `kb` context.
- Cross-document search/synthesis is needed.
- An agent should reason across the full submission rather than a pre-filtered subset.
