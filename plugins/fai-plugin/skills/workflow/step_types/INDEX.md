# Step Types Index

One file per documented step type. Each covers: purpose, required inputs, config, outputs, wiring, patterns, validation errors, and gotchas. The authoritative type list lives in `scripts/validate_workflow.py` (`VALID_STEP_TYPES`).

## Ingest
- [workflow_dispatcher](workflow_dispatcher.md) — auto-injected root; never authored
- [prepare_documents](prepare_documents.md) — fetch, translate, unpack, extract email content
- [classify_documents](classify_documents.md) — bucket docs into named categories

## Knowledge Base
- [knowledge_base](knowledge_base.md) — vector KB for downstream RAG

## Extraction
- [extract_from_multiple_sources](extract_from_multiple_sources.md) — email + multi-doc KV with tie-breaking
- [extract_rows_from_multiple_sources](extract_rows_from_multiple_sources.md) — array-of-rows (claims, schedules)
- [extract_mixed_schema_from_multiple_sources](extract_mixed_schema_from_multiple_sources.md) — scalars + array in one call
- [agentic_extraction](agentic_extraction.md) — RAG-agent extraction (structured or free-form)
- [sov_mapping](sov_mapping.md) — Excel SOV column alignment
- [combine_kv_tables](combine_kv_tables.md) — deterministic deep-merge of N KV tables

## Q&A / Compliance
- [generate_qa_table_from_agent](generate_qa_table_from_agent.md) — agentic KB Q&A
- [multi_column_qa](multi_column_qa.md) — per-row Q&A grid
- [agentic_guideline_check](agentic_guideline_check.md) — RAG-agent guideline evaluation (preferred)
- [extract_guidelines](extract_guidelines.md) / [check_guidelines](check_guidelines.md) — legacy guideline pair

## Comparison
- [compare_document_data](compare_document_data.md) — compare pre-extracted data
- [compare_documents](compare_documents.md) — edit-only legacy

## Screening & enrichment
- [ofac_agent](ofac_agent.md) — sanctions screening
- [osha_agent](osha_agent.md) — OSHA records
- [trellis_law](trellis_law.md) — legal case history
- [web_search](web_search.md) — Perplexity research with citations
- [web_agent](web_agent.md) — prompt-driven browser task
- [execution_matching](execution_matching.md) — match prior executions of the same account

## Integration connectors
- [integrations.md](integrations.md) — shared idiom + google_maps, nhtsa, riskmeter, hazardhub, maprisk, pitchbook, cotality_valuation, snapsheet, snapsheet_payments (edit-only), applied_epic, benefitpoint, qqcatalyst, ams360, sharepoint, outlook_mail, imageright

## Custom code / synthesis
- [custom_step](custom_step.md) — sandboxed inline Python (all new custom code)
- [function](function.md) — legacy in-process Python; edit-only
- [submission_summary_generator](submission_summary_generator.md) — AI narrative summary

## Control flow
- [decision](decision.md) — conditional branching
- [loop](loop.md) — run member steps once per item
- [pause](pause.md) — manual approval gate
- [hold](hold.md) — block for correction (passthrough)
- [manual_input](manual_input.md) — collect structured user input
- [run_workflow](run_workflow.md) — trigger a sub-workflow

## Output / delivery
- [email](email.md) — send via Zapier or render UI-only
- [fill_docx](fill_docx.md) — fill a Word template
- [text_block](text_block.md) — display markdown (rarely used)
- [document_viewer](document_viewer.md) — inline document render (rarely used)

## Deprecated — validator errors on new instances
- [extract_from_document](extract_from_document.md) → extract_from_multiple_sources
- [extract_from_email](extract_from_email.md) → extract_from_multiple_sources
- [generate_qa_table_from_kb](generate_qa_table_from_kb.md) → generate_qa_table_from_agent
- [enrich_addresses_with_gmaps](enrich_addresses_with_gmaps.md) → google_maps (see integrations.md)
