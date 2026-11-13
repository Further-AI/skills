# knowledge_base

### What this step does

A `knowledge_base` step chunks the supplied documents (and/or parsed emails), embeds the chunks with the configured embedding model, and writes them into a vector index. It then emits a small handle object (`kb`) that downstream steps wire in as `input_parameter_name: "kb"` and use to issue vector / sparse / hybrid retrieval queries at runtime.

The step does NOT answer questions or extract data — it only builds the index. All actual retrieval happens in the consumer steps.

### Required inputs

At-least-one-source rule (save-time enforced): wire `documents`, `emails`, or legacy scalar email inputs.

| Input parameter | Type | Notes |
| --- | --- | --- |
| `documents` | array of `file` | usually wired from `prepare_documents` via `output_attribute: "documents"` |
| `emails` | array | grouped per-email bundles from `Prepare Documents.emails` — preferred for email ingestion |
| `email_body` / `email_body_plain` / `email_headers` / `headers` | scalars | legacy scalar email inputs; when mapped (without `emails`), also wire `email_document_id` or `email_user_document_id` for citation linkage |

### Config keys

From `KnowledgeBaseConfig`. All keys are optional — the runtime fills in sane defaults when they are null.

| Key | Type | Default at runtime | Notes |
| --- | --- | --- | --- |
| `embedding_model_type` | string | `openai_text_embedding_3_large` | The bare alias `text-embedding-3-large` is also accepted in existing configs. |
| `pdf_chunker_type` | enum string | `reducto_table_summary` | One of `reducto`, `reducto_metadata_page`, `reducto_hybrid_page`, `reducto_table_summary`. |
| `collection_name` | string | auto-generated | Leave `null` unless you intentionally want to share an index across executions. |
| `sub_collection_name` | string | auto-generated | Same — leave `null` in 99% of cases. |
| `query_modes` | list of enum strings | `["vector", "sparse"]` (hybrid retrieval) | Members: `vector`, `sparse`, `hybrid`, `text_search`. |

`collection_name` / `sub_collection_name` set to `null` is the strong default — the runtime allocates a fresh per-execution collection so KBs from different executions never collide.

### Output schema

```
{
  "knowledge_base_initialized": bool,
  "collection_name": str,
  "sub_collection_name": str,
  "embedding_model_type": str,
  "pdf_chunker_type": str,
  "query_modes": [str],
  "document_count": int,
  "kb": {
    "collection_name": str,
    "sub_collection_name": str,
    "embedding_model_type": str,
    "pdf_chunker_type": str,
    "query_modes": [str]
  }
}
```

The `kb` sub-object is the handle downstream steps consume — wire `output_attribute: "kb"` (NOT `"knowledge_base_instance"`, an older name that no longer exists). `output_type` is `"knowledge_base"`, which the UI uses to render a compact KB summary card.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is already there — do not silently rewrite a dependency-only step into input_mappings.
- Migration `dependencies` → `input_mappings` on explicit request; the reverse is forbidden.

The modern wiring for `documents`:

```json
{
  "input_type": "dependency",
  "value": {
    "dependency_step_outputs": [
      { "step_name": "Prepare Documents", "output_attribute": "documents" }
    ],
    "resolution_operator": null,
    "resolution_config": null
  },
  "input_parameter_name": "documents"
}
```

Downstream consumers of this step's `kb` output use this exact pattern, swapping `output_attribute` to `"kb"` and `input_parameter_name` to `"kb"`.

### Common patterns

#### Pattern A — modern: input_mappings only, defaults preserved

The recommended shape for any newly created step.

```json
{
  "name": "Initialize Knowledge Base",
  "type": "knowledge_base",
  "input_mappings": [
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          {"step_name": "Prepare Documents", "output_attribute": "documents"}
        ],
        "resolution_operator": null,
        "resolution_config": null
      },
      "input_parameter_name": "documents"
    }
  ],
  "dependencies": [],
  "config": {
    "embedding_model_type": "text-embedding-3-large",
    "pdf_chunker_type": "reducto_table_summary",
    "collection_name": null,
    "sub_collection_name": null,
    "query_modes": ["vector", "sparse"]
  },
  "incremental_config": {"behavior": "rerun"}
}
```

#### Pattern B — mixed wiring (existing workflows)

Many older workflows declare BOTH `input_mappings` AND a `dependencies` entry pointing at the same upstream step. This is legal and should be preserved when editing such a step — do not strip the duplicate `dependencies` entry unless asked.

#### Pattern C — legacy: dependencies only

Older workflows omit `input_mappings` entirely and rely on `dependencies` plus runtime auto-wiring. Preserve as-is; only migrate on explicit user request.

```json
{
  "name": "Processing Documents",
  "type": "knowledge_base",
  "input_mappings": [],
  "dependencies": [{"step_name": "Prepare Documents", "field_selector": null}],
  "config": {
    "embedding_model_type": "openai_text_embedding_3_large",
    "pdf_chunker_type": "reducto_table_summary"
  }
}
```

### Common validation errors and fixes

- **`must map 'documents', 'emails', or scalar email inputs`** — wire at least one source (Pattern A), or via a `dependencies` entry whose upstream step's output contains a `documents` field (Pattern C).
- **`wire 'email_document_id' when scalar email inputs are mapped`** — scalar email inputs need the email document id for citation linkage; the grouped `emails` input does not.
- **Bad `pdf_chunker_type` value** — must be one of the enum members. Free-form strings fail config validation.
- **Bad `query_modes` member** — must be a subset of `{"vector", "sparse", "hybrid", "text_search"}`. Empty list is rejected at runtime; use `null` to get the default.
- **Reconstructing the KB downstream** — a frequent authoring error is to wire `documents` into a downstream KB-consumer instead of wiring this step's `kb` output. Always wire `output_attribute: "kb"` into `input_parameter_name: "kb"` — never rebuild a KB object by hand.
- **Two KB steps with the same `collection_name`** — if you hard-code `collection_name`, two runs of the same workflow write into the same index. Leave it `null` unless you have a specific reason.

### Common gotchas

- **One KB per workflow is the default — class-scoped KBs are a legitimate exception.** The standard shape is exactly one `knowledge_base` step placed right after `prepare_documents`, with consumer steps scoping retrieval via their own `documents` input. Some workflows intentionally run multiple KB steps each fed from a different `classify_documents` bucket (per-class KBs for very large or noisy document sets) — don't "fix" that shape when you encounter it; for NEW workflows, start with one KB.
- **`kb` flows; `documents` flows in parallel.** Downstream RAG steps (`agentic_extraction`, `agentic_guideline_check`, `generate_qa_table_from_agent`, `multi_column_qa`, `submission_summary_generator`) take BOTH `kb` (the handle) and, where supported, `documents` (scoping/citations). All use `input_parameter_name: "kb"`.
- **Include email files in the KB when relevant:** `Prepare Documents.eml_files` (flat list of `.eml`/`.msg` as workflow-document refs) can be merged with classified docs via `concat_lists` to feed downstream `documents` inputs alongside the KB.
- **Defaults beat config tuning.** Sampled configs almost all use the defaults; don't surface config knobs to the user unless they ask.
- **`display_step` is a UX choice.** `false` hides the KB processing card; `true` is fine when indexing time warrants a progress card.
- **`incremental_config.behavior: "rerun"` is the universal default.** Re-running rebuilds the index from scratch; there is no incremental-add path today.
- **Don't put a KB inside a branch.** Place the KB on the main path before any decision branching. Downstream branches can each consume the same `kb` handle.

### See also

- `generate_qa_table_from_agent` — most common downstream consumer.
- `multi_column_qa` — schema-driven Q&A against this KB.
- `agentic_extraction` — RAG-based structured extraction.
- `agentic_guideline_check` — agent verifies docs against rules in this KB.
- `submission_summary_generator` — agentic summary that consumes the KB plus prior extraction tables.
- `../reference/patterns/kb_wiring.md` — KB wiring conventions.
