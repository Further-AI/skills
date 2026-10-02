# Integration connector steps

One doc for the external-system connector family: `ams360`, `hazardhub`, `riskmeter`, `maprisk`, `nhtsa`, `pitchbook`, `cotality_valuation`, `snapsheet`, `snapsheet_payments`, `applied_epic`, `benefitpoint`, `qqcatalyst`, `google_maps`, `sharepoint`, `outlook_mail`, `imageright`.

## Shared idiom (applies to every connector)

- **One step runs exactly one `config.action_type`.** Multiple operations = multiple steps.
- **Per-action contracts are generated, not hardcoded.** Each action's input params, types, and output fields come from connector metadata and are surfaced live under `<step_type>_actions` in the platform's workflow-builder step-metadata. Read that for the current contract of your chosen action — do not guess params, and do not copy stale param lists between workflows.
- **`run_type`** (step-envelope field): `"single_call"` (default — one call, params via `input_mappings`) or `"loop"` (run the action once per row of an upstream TableV1). Loop mode needs:
  - `config.table_to_enrich`: the upstream table step's name.
  - Row-source mappings: a dependency whose `step_name` is the `table_to_enrich` step and whose `output_attribute` is `schema.<column_name>`. Combine columns with the normal lowercase `resolution_operator`s.
  - Do NOT map `data`, `schema`, `metadata`, `user_documents`, `_iterator`, or `current_row`.
  - Loop eligibility is per action (`supports_table_enrichment` in step-metadata). Bare list/search reads and bulk writes are single_call-only. Never set `"loop"` on non-connector step types.
- **Output columns**: select projected/enriched columns in `config.output_schema.properties`. The property key is the final column name; each property carries a `source_path` into the connector response. Single-call returns a one-row (or per-result-row) grid; loop preserves input rows and appends the enabled columns (same-name columns are overwritten intentionally).
- **Credentials are org-managed integration secrets** — resolved at runtime from the org's integration configuration, never from `.env`, never passed through `input_mappings`, never inlined in config. Fixed managed secret names (e.g. `riskmeter_client_id`, `sharepoint_tenant_id`, `imageright_credentials`) are looked up by the blocks — never rename or prefix them. Configuring credentials is an API/UI operation — see `/fai:wb`.
- **Confirm the integration is enabled for the org before authoring.** A connector step in an org without the integration fails at run time. `nhtsa` and `google_maps` are the keyless exceptions.
- **Write idempotency**: connector writes are deduped by an integration write ledger keyed on `(execution, step, row, action, inputs)` — a retry or rerun with identical inputs replays the recorded result instead of re-issuing the write. Write loops are capped at 100 rows.
- **Do not wrap connector calls in custom code steps** when a first-class action exists.

## ams360

Vertafore AMS360 agency management. Credentials: managed integration secrets `integration_ams360_agency_no` + `integration_ams360_access_key`.

- **Reads**: `search_customer`, `get_customer`, `search_policies`, `search_contacts`, `search_activities`, `search_by_phone`, `search_employee_basic_info`, `search_naics`, `search_naics_sub_descriptions`, `search_sic_by_naics`. **Reference fetches** (no required inputs): `get_agency_employees`, `get_employees`, `get_companies`, `get_business_units`, `get_agency_lists`, `get_agency_profile_questions`.
- **Forms + paired writes**: `*_form` actions render a key-value form the user fills; the paired write consumes it — `create_customer_form` → `create_customer`, `create_activity_form` → `create_activity`, `create_policy_activity_form` → `create_policy_activity`, `create_suspense_form` → `create_suspense`, `update_customer_form` → `update_customer`, `answer_profile_questions_form` → `answer_customer_profile_questions`. `validate_customer_form` is client-side validation only (no API call) — it returns `{violation_count, violations, markdown_content}` at the top level.
- **Wiring the pair**: the write step maps `form_data` from the form step with `output_attribute: null` (whole form dict — this is the contract, not a bug). Reading one form cell uses `output_attribute: "data.<cell_name>.value"` (the `.value` suffix matters).
- **Write inputs**: `create_activity` / `create_policy_activity` also require `documents` (wire from `prepare_documents` or a merge, not a search step). `attach_file_to_activity` requires `activity_id` + `documents`.
- **Output**: always `{schema, data}`; `data` shape is per-action (customer rows, policy rows, form specs, single-row confirmations). `display_type: "grid"` for list actions, `"key_value"` for single-row/form actions.
- **Gotchas**: each action has its own params model — unknown params are silently ignored (a typo looks valid and does nothing). `create_customer` requires `account_exec_short_name`, `account_rep_short_name`, `customer_type`, `firm_name`, `customer_added_date` (business-unit fields for Customer/Prospect types) — use the form flow rather than calling it from raw extracted data. An invalid `action_type` is rejected at save with the full valid list. Place reference fetches early and share their output.

## hazardhub

Property peril/hazard data. Credentials: `hazardhub_api_key`. `config.action_type` is an operation id from the connector's registry (OpenAPI-derived) — set one and let the validator surface the valid list if wrong. Supports `single_call` and `loop`.

## riskmeter

Cotality RiskMeter catastrophe/peril scores. Credentials: `riskmeter_client_id` + `riskmeter_client_secret`. Same shape as `hazardhub`: registry-validated `config.action_type`, `single_call` or `loop` per-row enrichment.

## maprisk

Maprisk (NSM HabPro / Majesco) property/geo-risk reports. Credentials: `integration_maprisk_api_key` (+ optional `integration_maprisk_base_url`).

- **Actions**: a report (`hail_risk`, `wildfire_risk`, `crime`, `quake_temblor`, `county`), `risk_bundle` (hail+wildfire+crime), `geocode` (address → normalized address + lat/lon, no report), or `batch` (async bulk CSV; single_call-only).
- **Location inputs**: map `latitude`+`longitude` (skips geocoding) or `address`; `config.auto_geocode` (default true) geocodes an address-only input. Optional `street`/`city`/`state`/`zip`.
- **Parameterized reports** (e.g. DSS): `config.report_params`, e.g. `{"dss": {"coverageA": 500000, "tiv": 500000, "roofAge": 5}}`.
- **Output paths**: response is flattened to `<reportId>.<field>` for `source_path` (e.g. `hailRiskScore.riskScore`, `crime.Total Crime Index`, `county.name`), plus geocode-derived `latitude`/`longitude`/`matched_address`.
- **`batch`**: `document` input (CSV UserDocument) + required `config.report_list` (e.g. `["dss"]`), `config.column_map` (API field → CSV header), `config.report_params` (per-report param → CSV header), `config.poll_timeout_s`/`poll_interval_s`. Uploads, polls, and parses the results CSV into a TableV1 (original columns + geocode + `<Report>: <Field>` columns, plus `metadata.maprisk_batch`).

## nhtsa

NHTSA vPIC (VIN decode + vehicle reference lookups). **Keyless — no credential secret; never configure one.**

- **`config.action_type`** (default `vin_decode`):
  - `vin_decode`: single_call maps required `vin` (+ optional `model_year`) and returns a one-row table; loop sources `vin` from `config.table_to_enrich` rows via `schema.<column>`, batches 50 VINs/request, preserves rows 1:1, and appends decoded columns. Loop-only `config.mode: "validate"` + `config.validate_columns` (`{existing_row_column: NHTSA_response_key}`) compares decoded values against existing columns, emitting `<column>_nhtsa_value` / `<column>_matches` and filling blanks.
  - Reference lookups (`decode_vin`, `decode_wmi`, `get_all_makes`, `get_models_for_make`, `get_models_for_make_year`, `get_manufacturer_details`, `get_vehicle_variable_list`, `get_equipment_plant_codes`, `get_parts`, `get_canadian_vehicle_specifications`, …): single_call returns the whole `Results` table (one row per item — do not set `table_to_enrich`); loop calls once per row and appends the first matching record's fields. Per-action required inputs come from the lookup metadata (e.g. `get_models_for_make` → `make`; `get_parts` → `type`+`fromDate`+`toDate`; `get_all_makes` takes none).
- **Output columns**: enable `config.output_schema.properties` (auto-populated per action); `source_path` points at the NHTSA response key (`Make`, `Model`, `BodyClass`, `GVWR`; `Make_Name`, `Mfr_Name`, `WMI` for lookups).

## pitchbook

PitchBook company enrichment. Credentials: `integration_pitchbook_api_key` (required; no shared-key fallback) + optional `integration_pitchbook_base_url`. **Both must be declared in `options.control_plane_mappings`** (e.g. `[{"key": "integration_pitchbook_api_key", "stickiness": "latest"}]`) — an undeclared key fails at runtime with "PitchBook API key is not configured" even when the credential is set.

- One action: `config.action_type: "company_enrichment"` (the default). Input: `company_name` (loop mode: from `schema.<company_column>`).
- Output columns: the 15 company fields (name, pitchbook_id, website, description, profile_link, year_founded, valuation/funding fields, executives_list), each with `source_path`.

## cotality_valuation

Cotality Commercial ExpressLync (Marshall & Swift) replacement-cost / Insurance-to-Value. A different product from `riskmeter` (different auth + host). Credentials: org integration (`company_id`/`username`/`password`/`base_domain`) — no `control_plane_mappings` needed.

- **~94 actions**: the `estimate_replacement_cost` composite, valuation lifecycle (add/get/calculate/update/delete/history/report), structure CRUD, ~25 reference dictionaries + code lookups, address standardization, read-only admin. Read the generated contract in step-metadata for your action.
- **Two input key styles coexist**: `estimate_replacement_cost` takes snake_case (`line1`, `region_code`, `gross_floor_area`); every other action takes Cotality PascalCase (`ValuationID`, `SectionID`, `Code`). Copy exact keys from step-metadata. Object-valued inputs (`valuation`/`Section`/`Building`) accept a dict or JSON string built upstream.
- **Loop** works for reads AND single-entity writes (one create/update/delete per row); bulk writes (`add_multiple_*_additions`, `update_building_sections`), collection/blob reads (`*_dictionary`, `business_search`, `get_valuation_report`) and no-input actions (`ping`, `company_settings`, `get_users`) are single_call-only.
- **`estimate_replacement_cost` gotchas**: `assigned_user` must be a real Cotality company user — derive from a `get_users` step (error 1003 otherwise); `valuation_number` must be unique within the company — mint fresh per run (error 116 otherwise).

## snapsheet

Snapsheet Cloud — Claims + Vendor Integrations + Claims Workflows behind one org credential set (`api_key`/`secret`/required `base_domain`). **Intent-gated: only author when the user explicitly asks for a Snapsheet action** (claims ingestion, vendor task, workflow/webhook) — never as a generic "claims system" default.

- **Headline actions** have readable names: `search_claims` (date-of-loss window + `claimPartyExtRef` + status filters), `get_claim_v1_by_number` / `get_claim_v2`, `create_claim` (V1 flat body), `update_claim` (V2 JSON:API). The other ~250 operations keep generated `{method}_{path}` slugs (e.g. `post_api_v2_exposures`).
- **Inputs**: reads take query/path params; writes take one composite `body` object (full V1 flat or V2 JSON:API payload) built upstream and mapped whole.
- **Anti-duplicate pattern**: search_claims → decision → create/update, backed by the write-dedup ledger.

## snapsheet_payments

Snapsheet Payments (payouts / payee profiles) — a separate product from `snapsheet`: different host, different HMAC scheme, its own credential set. **EDIT-ONLY: it moves money — never author a new one** (the backend rejects any net increase in these steps); a human places it and you may edit around it. ~9 actions (e.g. `create_a_new_payout`, `view_payout`, `cancel_payout`).

## applied_epic

Applied Epic API Suite (Applied Systems). Credentials: org integration (`consumer_key`/`consumer_secret`, required `environment` = `mock`|`production`, optional `base_url`/`audience`).

- Every OpenAPI operation from the Applied Dev Center bundle is an action — read the generated contract in step-metadata.
- **Inputs**: path/query/header params by their exact metadata names; optional `query` and `headers` object inputs add extra non-auth values; writes expose a composite `body` or generated flat `body_*` inputs (required vs optional from the spec).
- Loop per `supports_table_enrichment`; write loops = one live mutation per row, 100-row cap, dedup ledger.

## qqcatalyst

Vertafore QQCatalyst M451. Credentials: org-managed OAuth refresh token + optional base-URL override — never inline OAuth values or override the managed keys.

- Every M451 OpenAPI operation is an action (two ambiguous file-stream actions are visible but disabled). Path/query/header params by name; write payloads through `body`; pagination caller-controlled.
- **Loop is restricted to single-record GETs with path identifiers**; all mutations and list/search actions are single_call-only.

## google_maps

Google Maps enrichment. **Platform-managed API key — no per-org credentials, no secret config, no control-plane mapping.** Replaces the deprecated `enrich_addresses_with_gmaps` step (the local validator errors on that type; see `step_types/enrich_addresses_with_gmaps.md` for migration).

- **Actions** (one per step): `geocode_address`, `reverse_geocode`, `validate_address`, `elevation`, `timezone`, `distance_matrix`, `find_place`.
- **Inputs**: `geocode_address`/`validate_address` → `address` (or `street`+`city`+`state`+`zip`, joined); `validate_address` optional `region_code` (default `"US"`). `reverse_geocode`/`elevation`/`timezone` → `latitude`+`longitude` (`timezone` optional `timestamp`). `distance_matrix` → `origin`+`destination` (optional `mode`/`units`). `find_place` → `query`.
- **Output columns**: e.g. `gmaps_latitude`, `gmaps_formatted_address`, `gmaps_county` — each with `source_path` into the response.

## sharepoint

Microsoft SharePoint document libraries. **single_call only** — wrap with a `loop` step for iteration. Credentials: `sharepoint_tenant_id`, `sharepoint_client_id`, `sharepoint_client_secret`, `sharepoint_site_id`, `sharepoint_drive_id`.

- **Actions**: `list_items` (recursive listing; optional `folder`, `created_after`/`created_before` window — `config.date_filter_target` picks folder vs file rows), `download_items` (requires `items` — accepts the `list_items` output table; folders download recursively; output includes a `user_documents` list — feed those ids downstream, not raw SharePoint item ids), `upload_documents` (requires `documents` — a TableV1, `user_documents` list, `{user_document_id, target_filename?, context?}` objects, or bare id strings; a static mapping must pass the selection as a JSON string; optional `destination_folder` created mkdir-p; `config.conflict_behavior`: `replace` default / `fail` / `rename`; `config.upload_concurrency` default 5), `update_excel_range` (requires `worksheet_name` + `range_address` + `values` 2D array matching the range; `null` cells left unchanged), `append_excel_table_rows` (requires `table_name` + `rows` — 2D array in column order or objects keyed by column).
- **Excel workbook ref**: exactly one of `workbook_path` XOR `workbook_item_id`. Both or neither is rejected.
- **Idempotency**: `replace` is retry-safe; `fail` skips byte-identical files (`skipped_existing` — also the create-workbook-if-missing pattern: upload a seed `.xlsx` with `conflict_behavior: "fail"`); `rename` is NOT retry-safe (creates `report (1).xlsx` duplicates — avoid). `append_excel_table_rows` duplicates rows on retry unless `dedupe_key_column` is set — set it whenever rows carry a natural key.
- Per-file isolation on upload: one failed file yields a `failed` row; the step fails only if every file fails.

## outlook_mail

App-only Microsoft Graph Outlook mail in a scoped mailbox. **single_call only.** Credentials: `outlook_tenant_id`, `outlook_client_id`, `outlook_client_secret`, optional `outlook_mailbox` (default mailbox; the `mailbox` input overrides — every action needs a mailbox, app-only cannot use "me").

- **Actions** (13): `move_to_folder`, `apply_category`, `resolve_folder`, `find_message`, `get_message`, `read_message` (full content incl. body), `list_folders`, `create_folder`, `list_attachments`, `download_attachment`, `send_mail`, `reply`, `get_incoming_message`.
- **Message identity (XOR)**: message actions take exactly one of `graph_message_id` (fast) or `internet_message_id` (search). `get_incoming_message` takes no inputs — it surfaces the triggering email's `graph_message_id`/`internet_message_id`/`conversation_id`/`mailbox` from the execution's email data locally (no Graph call); wire its output into later message actions to act on the email that triggered the run.
- **Folders**: `destination_folder`/`parent_folder` accept a name, `/`-separated path, or well-known id (`inbox`, `archive`, …); `config.create_if_missing` (default true) creates missing segments.
- **`move_to_folder`** output: `status` (`moved`/`already_filed`), `message_id` (**the NEW id — a move invalidates the original**), `original_message_id`, folder info. Idempotent on rerun.
- **`send_mail`** (`to_recipients`+`subject`+`body`) and **`reply`** (`comment` + message identity) require the Mail.Send role, which the default filing scope does not grant.
- Keep the block customer-agnostic: seating/company → folder mapping belongs in a preceding custom_step that outputs `destination_folder`.

## imageright

Vertafore ImageRight document management. **single_call only** — iterate task rows with a `loop` step. Credentials: single secret `imageright_credentials` (JSON blob: `base_url`, `tenant_id`, `username`, `password`); override the key via `config.credentials_secret_key`.

- **Retrieval**: `retrieve_documents` (requires `documents` — objects with an `id`; one merged PDF UserDocument per ImageRight Document), `retrieve_container_zip` (requires `containers` — `{id, name}`; one zip per File/Folder subtree), `fill_documents` (requires `user_document_ids` — fills pre-registered ImageRight placeholder docs in place). You usually don't need `fill_documents`: `prepare_documents` auto-fills pending ImageRight placeholders among its inputs.
- **Task lifecycle** (for scheduled poll → claim → process → route master workflows): `find_tasks` (`file_id` XOR `filter`), `claim_task` (required `task_id`), `release_task` (`refresh: true` extends instead of releasing), `create_task` (`file_id`+`step_id`; the file type must have a flow bound or ImageRight 500s), `route_task`, `split_task`, `cancel_task`, `set_attributes`, `find_users`, `get_workflow_steps` (resolve a `step_id` by name at runtime instead of hardcoding), `upload_document` (sources + create-document body; use `file_search` for file-level dedup — a mid-upload retry can otherwise create a duplicate Document).
- **Lock model**: mutating task actions (`route_task`/`split_task`/`cancel_task`/`set_attributes`) assume the task is already claimed and do NOT auto-lock — add a `claim_task` step first or they fail with "claim the task first". Lock contention on `claim_task` fails the step (never a silent skip); cross-run safety comes from non-overlapping schedules.
- **Output**: a table of created/filled rows plus a `user_documents` list — downstream `run_workflow` receives ids from `user_documents`, not raw ImageRight object ids.

## Other enum-valid connectors

`agencyzoom`, `aim`, `alis_dx`, `sambasafety`, `sagitta`, `financepro` are valid step types without reference docs here — confirm the org integration and read the action contract from step-metadata before authoring one.
