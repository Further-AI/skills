# Required input mappings — per-step cheat sheet

The save-time validator rejects any step missing a required input mapping. Use this table to scaffold each step's `input_mappings` correctly the first time. Optional inputs are documented per step in `step_types/<type>.md`.

`config`-provided values (`extraction_schema`, `classes` for classify_documents, `sov_fields` for sov_mapping, `ai_schema`/`input_schema` for multi_column_qa, …) live in `config`, NOT in `input_mappings`.

| Step type | Required `input_mappings` (user-mappable) |
| --- | --- |
| `workflow_dispatcher` | (auto-injected — never created manually) |
| `prepare_documents` | `user_document_ids` (from `Workflow Dispatcher.user_document_ids`) |
| `classify_documents` | `documents` |
| `knowledge_base` | **at-least-one source** — wire `documents`, `emails`, or legacy scalar email inputs (`email_body` / `email_body_plain` / `email_headers` / `headers`) plus `email_document_id` or `email_user_document_id` for scalar emails |
| `extract_from_multiple_sources` | **at-least-one source + matching prompt**: `documents` + `document_system_prompt`, OR an email input (`emails` / `eml_user_document_id` / `headers` / `email_body` / `body_html` / `body_plain`) + `email_system_prompt`. Scalar email inputs also need `eml_user_document_id`; grouped `emails` does not |
| `extract_rows_from_multiple_sources` | `documents` or `emails` |
| `extract_mixed_schema_from_multiple_sources` | `documents` or `emails` |
| `sov_mapping` | `documents` |
| `agentic_extraction` | `kb` (`documents` "Filtered Documents" is OPTIONAL — see step doc) |
| `agentic_guideline_check` | (none strictly required at save time; `kb` is typically wired plus a static `system_prompt`) |
| `combine_kv_tables` | `sources` — a `concat_lists` mapping over the FULL outputs (`output_attribute: null`) of multiple upstream KV-table steps |
| `generate_qa_table_from_agent` | `documents`, `kb` |
| `multi_column_qa` | `documents`, `kb` |
| `extract_guidelines` | `guideline_document` |
| `check_guidelines` | `extracted_guidelines`, `guideline_document`, `submission_documents` |
| `submission_summary_generator` | `data_points`, `system_prompt` |
| `compare_document_data` | `data_points`, `user_documents` |
| `web_agent` | `prompt` — exactly one mapping; `input_type` static or dependency |
| `google_maps` | Per action: `geocode_address`/`validate_address` → `address` (or `street`+`city`+`state`+`zip`); `reverse_geocode`/`elevation`/`timezone` → `latitude`+`longitude`; `distance_matrix` → `origin`+`destination`; `find_place` → `query`. Loop mode also needs `config.table_to_enrich` + enabled `config.output_schema.properties` |
| `fill_docx` | `template`, `context` |
| `text_block` | `markdown_content` |
| `run_workflow` | `target_workflow_id`, `documents` |
| `execution_matching` | `account_name` |
| `loop` | `iteration_source` — must resolve to a list; cannot reference one of the loop's own `config.member_steps` |
| `ofac_agent` | `entity_data` |
| `trellis_law` | `insured_names` |
| `osha_agent` | `insured_data` |
| `hold` | `data` |
| `function` / `custom_step` | (defined per-step by `config.input_schema`) |
| `riskmeter` / `hazardhub` / `pitchbook` / `maprisk` / `nhtsa` / `cotality_valuation` / `snapsheet` / `applied_epic` / `benefitpoint` / `qqcatalyst` | Action params from the selected operation (read the action's contract in step-metadata — see `step_types/integrations.md`). Loop mode also needs `config.table_to_enrich` + enabled `config.output_schema.properties` |
| `sharepoint` / `outlook_mail` / `imageright` / `ams360` | Action params from the selected operation — see `step_types/integrations.md` for per-action requirements |
| `pause`, `manual_input`, `decision`, `email`, `web_search`, `document_viewer` | (no strict save-time-required user inputs — see the step doc for typical wiring) |
