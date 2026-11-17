# Validation Errors — Diagnostic Reference

Catalog of every rule the platform's save-time validators enforce, common error messages, and the matrices for incremental-config compatibility. Reach for this when a workflow fails to save or behaves unexpectedly.

Run the bundled pre-flight first: `python3 ${CLAUDE_PLUGIN_ROOT}/skills/workflow/scripts/validate_workflow.py <workflow.json>`. It mirrors the platform checks and additionally **hard-errors on the four deprecated step types** — `extract_from_email`, `extract_from_document`, `generate_qa_table_from_kb`, `enrich_addresses_with_gmaps` — steering you to the supported replacement.

---

## 1. Parse-time errors (pydantic rejects before runtime)

These fail during step model validation. Error message: `"Invalid configuration for step 'X' of type T: <pydantic detail>"`.

- [ ] **Unknown step `type`.** Must be a current `StepType` enum value (the authoritative list is `VALID_STEP_TYPES` in the bundled validator). Made-up names like `info_extraction`, `loss_run`, `property_sov_mapping` do **not** exist.
- [ ] **Duplicate step `name`.** Names must be unique within the workflow (case-sensitive).
- [ ] **Missing required config field.** Each `*Config` has required fields. Common ones: `extraction_schema`, `sov_fields`, `cases`, `default_branch`, `system_prompt` for `submission_summary_generator`/`extract_from_email`.
- [ ] **`config` shape doesn't match the `type`.** The platform constructs the matching `*Config` model and fails.
- [ ] **`FunctionConfig` with both `code` and `func`, or neither.** Mutex enforced. (`custom_step` has no `func` field at all — inline `code` only.)
- [ ] **Function/custom_step with `code` + `input_mappings` but no `input_schema`/`output_schema`.** Both required in `config` (for `custom_step` they are always required).
- [ ] **`CompareDocumentsConfig.extraction_schemas` is empty.** At least one required.
- [ ] **`SovMappingConfig.exclusive_sov_fields` set without `include_unmapped_columns: true`.** Cross-field validator rejects.
- [ ] **`output_config` class mismatches `output_type`.** E.g., `TableOutputConfig` with `output_type: "email"`. Raised in `set_default_output_type_and_config`.

---

## 2. Model / reasoning_effort / verbosity compatibility

Applied via `validate_model_effort_verbosity` on most extraction, QA, decision, email, and agent configs.

- [ ] **`reasoning_effort` not valid for chosen `model`.** See the matrix in `model_compatibility.md`.
- [ ] **`verbosity` set on a non-GPT-5 model.** Only `gpt-5.1`, `gpt-5.2`, `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano` accept it.

---

## 3. Input-mapping errors

See `input_mappings.md` for the canonical shape.

- [ ] **`input_parameter_name` not in step's input schema.** This is the most common bug class. Quick map of names SKILL.md got wrong:
  - `email_document_id` → `eml_user_document_id`
  - `email_headers` → flat: `subject`, `body_html`, `body_plain`
  - `email_body` → `body_html` (and/or `body_plain`)
  - `zapier_webhook_url` → `zapier_reply_url`
  - `gmail_thread_id` → `thread_id`
  - `attachment_data` → `attachment_data_list`
  - `knowledge_base_instance` → `kb`
  - `extraction_data` (compare_document_data) → `data_points`
  - `documents` (compare_document_data) → `user_documents`
  - `entity_data` (osha_agent) → `insured_data`
  - `convert_to_pdf` (fill_docx) → it's `config`, not an input
  - `message` (manual_input) → it's `config`, not an input
- [ ] **`input_type` is invalid.** Must be `static | variable | prompt | dependency | function`.
- [ ] **`value` doesn't match `input_type`:**
  - `dependency` → must be a dict (FieldDependency).
  - `prompt` / `variable` / `function` → must be a string.
  - `static` → any JSON.
- [ ] **Dependency step name doesn't exist.** Allowed exception: literal `"Workflow Dispatcher"`.
- [ ] **`output_attribute` first segment not in upstream output schema.** Validator does top-level only. Common culprits:
  - `email_body` from `prepare_documents` → use `body_html` / `body_plain`.
  - `email_headers` → doesn't exist; use flat header keys (`subject`, `from`, `to`, `cc`, `bcc`, `date`).
  - `knowledge_base_instance` → use `kb` (resolver special-case).
  - `collection_name` directly from `knowledge_base` → use `kb.collection_name` (the resolver wraps).
- [ ] **Multiple `dependency_step_outputs` without `resolution_operator`.** Required for `len > 1`.
- [ ] **Resolution operator name in wrong case.** Lowercase only: `concat_lists`, `merge_dicts`, `numeric_add`, `numeric_subtract`, `numeric_multiply`, `numeric_divide`, `string_join`, `custom_function`. NOT `CONCAT_LISTS` / `ADD_NUMBERS`.
- [ ] **Resolution config has wrong field names:**
  - `ConcatListsConfig`: `flatten_nested` (NOT `flatten_nested_lists`), `remove_duplicates`.
  - `MergeDictsConfig`: `conflict_resolution` ∈ `last_wins | first_wins | error_on_conflict` + `deep_merge: bool`. **NO `override_existing_keys`.**
  - `StringJoinConfig`: only `separator`. NO `skip_null_or_empty`.
  - `NumericConfig`: `operand_default_value` (required if any operand can be `None`).
- [ ] **`custom_function` operator without valid `FunctionConfig`.** `resolution_config` must have `code` or `func`.

---

## 4. DAG / dependency errors

- [ ] **Cycle in `input_mappings`.** Two steps reference each other directly or transitively. Detected via DFS.
- [ ] **Using legacy `dependencies` instead of `input_mappings`.** Legacy still works but is deprecated; modern tooling assumes `input_mappings`.
- [ ] **Referencing `"Workflow Dispatcher"` as upstream is allowed**, but the dispatcher only emits `user_document_ids` (not document objects). Only `prepare_documents` accepts IDs — every other step expects `WorkflowExecutionDocumentV1` objects.

---

## 5. Decision / parent_conditions errors

- [ ] **Comparison operator wrong name.** Use short-form: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `contains`, `not_contains`, `starts_with`, `ends_with`, `after`, `before`, `is_true`, `is_false`, `is_empty`, `is_not_empty`. `equals` / `greater_than` / `less_than` are NOT valid.
- [ ] **Case condition flat instead of `List[List[Comparison]]`.** Outer = OR, inner = AND. Common bug: `[c1, c2]` instead of `[[c1, c2]]`.
- [ ] **Operand `value` doesn't match operand `input_type`.** Same rules as `InputMapping`.
- [ ] **`DecisionConfig.default_branch` missing.** Required.
- [ ] **`parent_conditions[*].branch` doesn't exist in referenced decision step.** Step never runs — silent runtime bug.
- [ ] **`parent_conditions[*].decision_step` doesn't exist.** Silent runtime bug.
- [ ] **`DecisionConfig.input_schema` missing variables used in operands.** `variable`-type operands resolve against this schema; undeclared names silently null.

For decision step semantics: `step_types/decision.md`.

---

## 6. Incremental behavior errors

- [ ] **Invalid `behavior` string.** Must be `rerun | append | no_op | preserve_edits`. **`skip` is NOT valid — use `no_op`.**
- [ ] **`append` or `preserve_edits` on a non-table step type.** Only `TABLE_STEP_TYPES` allow these.
- [ ] **`append` on `multi_column_qa`.** Blocked (one row per question — appending makes no sense).
- [ ] **`preserve_edits` on a grid-extraction step.** Only allowed on KV-stable types (see PRESERVE_EDITS_ALLOWED below).

### Compatibility matrix

**`TABLE_STEP_TYPES`** (allowed `append` / `preserve_edits` in addition to `rerun` / `no_op`):
```
extract_rows_from_multiple_sources
extract_mixed_schema_from_multiple_sources
sov_mapping
multi_column_qa
extract_from_document
extract_from_email
extract_from_multiple_sources
generate_qa_table_from_kb
generate_qa_table_from_agent
agentic_extraction
function
```

**`APPEND_BLOCKED_STEP_TYPES`** (cannot use `append` even though they're table-producing):
```
multi_column_qa     // fixed rows per question
```

**`PRESERVE_EDITS_ALLOWED_STEP_TYPES`** (the only types that can use `preserve_edits`):
```
extract_from_document
extract_from_email
extract_from_multiple_sources
generate_qa_table_from_kb
generate_qa_table_from_agent
multi_column_qa            // grid but stable row order
function
agentic_extraction
```

Grid extraction steps (`extract_rows_from_multiple_sources`, `extract_mixed_schema_from_multiple_sources`, `sov_mapping`) are excluded from `preserve_edits` because positional merge is unreliable when row order can change.

**Non-table step types**: only `rerun` and `no_op` are valid.

---

## 7. Output type / output config errors

- [ ] **`output_type` is an unknown string.** Warning, not error — but UI won't render correctly.
- [ ] **`output_config` class doesn't match `output_type`.** `TableOutputConfig` only with `"table"`, etc.
- [ ] **`function` step with `output_type: "table"` but no `output_schema`.** Auto-generated for table/email; for other output types, must be provided.
- [ ] **`function` step with `output_type: "document_viewer"` or `"chat_message"` but no `output_config`.** Must be provided manually.
- [ ] **`TableOutputConfig.display_type` missing.** Required (`grid | key_value | compare`).

For the full matrix see `output_types.md`.

---

## 8. Function step runtime errors (not caught statically)

- [ ] **Function signature not `async def`.** Runtime fails.
- [ ] **Function doesn't return a dict.** Downstream resolution silently breaks.
- [ ] **Returned dict doesn't match `output_schema`.** Glom paths return `None`.
- [ ] **Imports at module top.** Code runs in isolation — put imports inside the function.
- [ ] **Parameters without `=None` default.** Runtime can fail.
- [ ] **Forgetting `**kwargs`.** Runtime context (`execution_log_id`, `owner_uid`, `owner_oid`, `run_id`) arrives there.
- [ ] **Reading TableV1 data off `step.output` directly.** Tables are stored externally — wire via `input_mappings`.
- [ ] **Calling internal backend functions** (`generate_submission_summary_from_data_points`, `guideline_check_qa`, etc.). Use the matching step type instead.

---

## 9. Knowledge-base wiring pitfalls

The mapping resolver wraps the KB step output as `{"kb": raw_output}`:

- [ ] **Using `output_attribute: "collection_name"` directly.** Won't work — wrap hides the raw keys. Use `"kb"` or `"kb.collection_name"`.
- [ ] **Using `input_parameter_name: "knowledge_base_instance"`.** Not the canonical name; use `kb`.
- [ ] **Reconstructing a KB object in a function step (e.g., `KnowledgeBase(collection_name=...)`).** The resolved `kb` value is already a ready-to-use object; pass it through.
- [ ] **Skipping the `documents` input on `agentic_extraction` / `multi_column_qa`.** Citations don't resolve to source files; results show as `"chat"` category.

---

## 10. Extraction schema pitfalls

- [ ] **JSON-Schema `"enum": [...]` for dropdowns.** Use `"type": "enum"` with `"options": [{id, name, value}, ...]` instead. First option blank `{id:"0", name:"", value:""}`.
- [ ] **Field description overrides system-prompt formatting.** More specific (description) wins. Keep them consistent on currency/date/integer formatting.
- [ ] **Related fields split into separate properties.** Model mixes values across sources. Group as object field with "must extract from same source" note.
- [ ] **`spreadsheet_agent: true` on non-Excel docs.** Wastes tokens.
- [ ] **`llm_extraction_model` set while depending on Reducto features.** `null` = Reducto (default); non-null = direct LLM (no grounding).
- [ ] **`deep_extract: true` without step-level `reducto_version: "v3"`.** Deep extract requires v3. v3 is significantly more expensive — only use for loss runs.
- [ ] **Missing `add_doc_index: true` on `extract_mixed_schema_from_multiple_sources`** when downstream needs source provenance.

---

## 11. SOV mapping pitfalls

- [ ] **Treating output as a flat table.** Output is `{<sheet_name>: ExcelMappingResult}`. Glom into a specific sheet.
- [ ] **Both `validator` and `validator_func` set.** They're alternatives.
- [ ] **`exclusive_sov_fields` without `include_unmapped_columns: true`.** Validator rejects.
- [ ] **`include_hidden_sheets` default differs from `classify_documents`.** SOV default is `true`; classify default is `false`. Be explicit.

---

## 12. Email step pitfalls

- [ ] **`reply_to_original: true` with manual `from_email` / `to_email`.** Manual fields ignored when replying.
- [ ] **`use_compose_function: true` with static `email_body`.** Compose overwrites the static body.
- [ ] **Missing `zapier_reply_url`.** Set in step config or in workflow `options.zapier_reply_url`. Otherwise silent failure.
- [ ] **Wrong input names**: `zapier_webhook_url` → `zapier_reply_url`; `gmail_thread_id` → `thread_id`; `attachment_data` → `attachment_data_list`.

---

## 13. Manual input / decision data flow

- [ ] **`manual_input.input_schema` field missing `type` or `input_type`.** Both required.
- [ ] **Dropdown/multi_select without `options`.** Required (`{id?, name, value}` array).
- [ ] **Wiring `message` via `input_mappings`.** It's config-only.
- [ ] **`output_attribute: "user_inputs.<field>"` for manual_input.** No wrapper exists; fields are spread. Use `output_attribute: "<field>"`.
- [ ] **Decision `variable` operand name not in `config.input_schema.properties`.** Silently null.

---

## 14. `run_workflow` pitfalls

- [ ] **Missing `eml_user_document_id`** when upstream is a `prepare_documents` email step. Email file isn't in `documents`.
- [ ] **Confusing `execution_id` semantics.** Present = add-documents mode; absent = new execution.
- [ ] **Setting incremental config on this step.** It's read from the TARGET workflow's `options.incremental_config.add_documents.behavior`.
- [ ] **`user_inputs[*].step_name` not in target workflow.** Silent — those inputs just don't apply.

---

## 15. Coded save-time errors (platform validate/save)

The platform's validate and save paths return structured error codes. Fixes:

| Code | Meaning | Fix |
|---|---|---|
| `DUPLICATE_STEP_NAME` | Two steps share a name. | Rename one; names must be unique. |
| `UNRESOLVED_DEPENDENCY` | An `input_mapping` references a step that doesn't exist (typo, deleted upstream). | Fix the reference; names are case-sensitive and several types look alike (`extract_from_multiple_sources` vs `extract_rows_from_multiple_sources`). After removing any step, grep the JSON for its name and fix every reference. |
| `DEPENDENCY_CYCLE` | The step graph has a cycle. | Break it — usually caused by rewiring input_mappings without considering downstream consumers. |
| `FORBIDDEN_IMPORT` | A `function` step's inline code imports a denylisted module. | Remove the import; new custom code belongs in a `custom_step` anyway. |
| `INVALID_PYTHON_SYNTAX` | Inline code fails to parse. | Fix the syntax in `config.code`; the `step_name` in the error tells you which step. |
| `STEP_MAPPING_ERROR` | A step's `input_mappings` don't match its required inputs. | Check `reference/required_inputs.md` and the step's `step_types/<type>.md`. |
| `Missing required input mappings: [...]` | A save-time-required input is unwired. | Wire every required input via `input_mappings` (see `reference/required_inputs.md`). Optional inputs left unwired don't block save but execute empty. |
| `OUTPUT_TYPE_MISMATCH` | `output_type` invalid for the step type (locked types accept only their canonical value or null; multi-mode types only their allowed set). | Set `output_type: null` and let auto-fill work — see `output_types.md`. |
| `DISPLAY_TYPE_MISMATCH` | `output_config.display_type` dead (output_type isn't table) or wrong for the step's shape (grid vs key_value vs compare). | Drop `output_config` or match the shape — see `output_types.md`. |
| `LOOP_STRUCTURE_ERROR` | A `loop` step has empty `config.member_steps`, or `iteration_source` references one of its own members. | Add at least one member (or remove the loop); point `iteration_source` at a step outside the loop. |
| `LOOP_MEMBERSHIP_MISMATCH` | A loop's `config.member_steps` and a member's `loop_membership` disagree, or a referenced step is missing / isn't a loop. | Make both sides agree — every member lists the loop in `loop_membership` and vice versa. |
| `LOOP_MEMBERSHIP_CARDINALITY` | A step declares membership in more than one loop. | One loop membership per step — remove the extras. |
| `RESTRICTED_STEP_TYPE` | The save adds a step of a restricted type (`snapsheet_payments` — it moves money). | Never author it; a human places it. |

---

## 16. Things validators DON'T catch (runtime-only)

- [ ] **Deep glom paths.** Only first segment validated. `documents.ACORD.0.metadata.pages.0.bbox` may fail at runtime.
- [ ] **Schema drift in function output.** Returns `{total: 5}` when `output_schema` says `{count: integer}` → downstream glom silently null.
- [ ] **`parent_conditions` referencing non-existent branches.** Step silently skipped.
- [ ] **Stale `workflow_title_question`.** Asks for fields no extraction step produces.
- [ ] **`FileAccessConfig` with secret env vars not set.** Runtime fails on first execution.
- [ ] **Webhook auth broken.** No URL/credential validation.
- [ ] **Model availability.** Org may not be enabled for the chosen model — runtime 401.
- [ ] **`reducto_version: "v3"` at step level when workflow-level is `v2`.** Generally OK (step wins), but some features are v2-only.

---

## Quick triage order

When a workflow won't save:

1. Is `type` a current, non-deprecated `StepType` value? (Run the bundled validator — it carries the authoritative list and errors on deprecated types.)
2. Do all step names appear in `input_mappings[*].dependency_step_outputs[*].step_name`? Check typos/case mismatches.
3. Are all `input_parameter_name`s valid for the step type? See §3 above.
4. Are all `output_attribute` first segments in upstream output schemas (KB special case noted)?
5. Any `resolution_operator` in UPPERCASE? Any `incremental_config.behavior: "skip"`? Any `equals`/`greater_than` decision ops?
6. Any non-GPT-5 model with `reasoning_effort` or `verbosity` set?
7. Any `function`/`custom_step` with `code` + `input_mappings` missing `input_schema`/`output_schema`?
8. Any `output_config` class mismatching `output_type`?
9. Any coded error from the platform (§15)? Follow its fix column.
