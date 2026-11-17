# enrich_addresses_with_gmaps

> **DEPRECATED — edit-only.** Hidden from the builder's step picker; the bundled validator errors on new instances. New workflows use the `google_maps` connector step (`config.action_type`: `geocode_address` / `reverse_geocode` / `validate_address` — see `integrations.md`), which replaces this step's raw table plumbing with the standard connector idiom. This doc remains for editing existing instances.

### What this step does

Runs an incoming TableV1 through Google Maps enrichment:

1. Reads the TableV1 envelope (`data`, `schema`, `user_documents`, `metadata`) keyed on the snake-case field names declared in `schema.items.properties`.
2. Forward-geocodes any row missing lat/lon, reverse-geocodes any row with lat/lon but missing admin fields, and (when `distance_validation_threshold_miles` is set) cross-checks address geocode vs the row's coordinates and rewrites the worse one.
3. Optionally runs Google's Address Validation API for precise granularity tagging, plus admin-field backfill and normalization passes (counties, country codes, postal codes).
4. Writes the enriched cells back into the same column positions and returns a new TableV1 envelope.

The runtime output dict:

```json
{ "schema": <same schema as input>, "data": [<row>, <row>, ...], "user_documents": [...], "metadata": {...} }
```

Cells preserve TableV1's `{"value": <scalar>, "original_value": ..., "is_edited": ..., "edit_history": [...]}` wrapper; downstream inline-code steps unwrap with the usual `val["value"] if isinstance(val, dict) and "value" in val else val`.

### Required inputs

| Param            | Type     | Required | Source                                                                                   |
| ---------------- | -------- | -------- | ---------------------------------------------------------------------------------------- |
| `data`           | `array`  | YES      | `input_mapping` — `<UpstreamTableStep>.data` (the rows array).                            |
| `schema`         | `object` | YES      | `input_mapping` — `<UpstreamTableStep>.schema` (carries the field name / data_type info). |
| `user_documents` | `array`  | YES      | `input_mapping` — `<UpstreamTableStep>.user_documents` (passed through verbatim).         |
| `metadata`       | `object` | YES      | `input_mapping` — `<UpstreamTableStep>.metadata` (passed through verbatim).               |

All four are dependency-only — none can be wired statically. All four come from the SAME upstream TableV1 producer (usually a `sov_mapping` step). Splitting them across producers corrupts field-name resolution because the schema's `items.properties` keys drive which columns are treated as latitude / longitude / address.

### Config keys

From `EnrichAddressesWithGmapsConfig`:

| Key                                   | Type             | Default                                                                 | Notes                                                                                                                              |
| ------------------------------------- | ---------------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `lat_column_name`                     | string           | `"latitude"`                                                            | Snake-case field name in `schema.items.properties` that holds latitude.                                                            |
| `lon_column_name`                     | string           | `"longitude"`                                                           | Snake-case field name for longitude.                                                                                               |
| `address_target_columns`              | `List[str]` or null | `["address_line_1","address_line_2","city","state","postal_code","county","country"]` | Address field names to enrich / write back into. MUST match the snake-case field keys in the upstream schema.                |
| `country_code_to_country_name`        | `Dict[str,str]` or null | `null`                                                          | Mapping of ISO country codes to full country names (e.g. `{"US":"United States of America"}`). Used during the country pass.       |
| `prefer_abbreviated_states`           | bool             | `false`                                                                 | When true, write `"CA"` instead of `"California"`.                                                                                  |
| `puerto_rico_exception`               | bool             | `false`                                                                 | Normalize Puerto Rico rows so `state`/`country` are consistent with Google's PR formatting.                                          |
| `virgin_islands_exception`            | bool             | `false`                                                                 | Same idea for USVI rows.                                                                                                            |
| `use_connecticut_old_county_names`    | bool             | `false`                                                                 | Replace CT planning regions with historical county names.                                                                            |
| `distance_validation_threshold_miles` | int or null      | `null`                                                                  | If set, run the lat/lon vs geocoded-address distance check and correct whichever side looks wrong. Common values: 1, 5, 25.          |
| `enrich_missing_admin_fields`         | bool             | `false`                                                                 | Run a second reverse-geocode pass per row to fill missing county / postal / country after the initial pass.                          |
| `normalize_counties`                  | bool             | `false`                                                                 | Normalize county names (strips `" County"` / `" Parish"` etc.).                                                                      |
| `normalize_country_codes`             | bool             | `false`                                                                 | Normalize country codes to full names. Pairs naturally with `country_code_to_country_name`.                                          |
| `normalize_all_postal_codes`          | bool             | `false`                                                                 | Strip `+4` extensions, zero-pad US ZIPs to 5 digits.                                                                                 |

All bools default to `false`. The typical shape leaves every optional flag at `false` and only sets the column names + `address_target_columns`.

### Output schema

Same TableV1 envelope shape it received:

```json
{
  "type": "object",
  "properties": {
    "schema":         {"type": "object"},
    "data":           {"type": "array"},
    "user_documents": {"type": "array"},
    "metadata":       {"type": "object"}
  }
}
```

Downstream wiring choices for `output_attribute`: `null` (full envelope, chain into another table consumer), `"data"` (enriched rows array — what inline-code steps usually want), `"schema"` (the pass-through schema). Leave step-level `output_type`, `output_config`, and `output_schema` null.

### Input wiring (input_mappings vs dependencies)

Editing existing instances: preserve whatever pattern is there. Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden. The canonical shape pulls all four params from the same upstream table step.

### Migration to `google_maps`

The replacement is one `google_maps` step per lookup with `run_type: "loop"` + `config.table_to_enrich` pointing at the upstream table step, action params mapped from `schema.<column>` row sources, and output columns selected in `config.output_schema.properties`. See `integrations.md`. Migrate only on explicit user request.

### Common validation errors and fixes

- **New instance of this step type** → the bundled validator errors: deprecated, use `google_maps`.
- **One of `data` / `schema` / `user_documents` / `metadata` not wired** → save-time rejection. All four are required; wire all four from the same upstream step.
- **`data` wired from a non-table producer (e.g. `extract_from_multiple_sources`)** → wrong shape; the step early-returns on empty data or silently fails to find lat/lon columns. Upstream MUST be `sov_mapping` / `extract_rows_from_multiple_sources` / another TableV1 producer.
- **`lat_column_name` / `lon_column_name` set to a column that doesn't exist in `schema.items.properties`** → geocoding runs but nothing is persisted back. Verify names against the upstream `sov_mapping.config.sov_fields[*].name`.
- **`address_target_columns` mixes titles (`"Address Line 1"`) with field names (`"address_line_1"`)** → the internal mapping uses snake-case field names; title-cased entries won't match.
- **Setting `country_code_to_country_name` without `normalize_country_codes: true`** → the mapping is built but never applied.

### Common gotchas

- **Rate limits and cost.** Each row can fan out to multiple Google Maps API calls. Large SOVs (1000+ rows) can run for several minutes.
- **Ambiguous city names** (30+ "Springfield"s in the US): without state or ZIP the geocoder picks the most-populous match — sometimes wrong. Always feed at least `city + state` or `city + postal_code` from the upstream SOV mapping.
- **Address validation returns the highest-confidence match only** — lower-confidence alternates are discarded; no conflict metadata is emitted.
- **Distance validation can REWRITE customer-supplied coordinates.** When the customer lat/lon is farther than the threshold from the geocoded address, the coordinates are replaced. Track edits via TableV1 `is_edited` / `edit_history` if an audit trail matters.
- **Connecticut counties.** CT replaced counties with planning regions in 2022; Google returns the new regions while many rating tables key on historical counties. Flip `use_connecticut_old_county_names: true` for CT-aware rating consumers.
- **Header vs field-name drift.** Matching is by snake-case field name, not header title. If the upstream field is `name: "zip_code"` titled `"ZIP"`, use `"zip_code"` in `address_target_columns`.

### See also

- **`integrations.md` (google_maps)** — the replacement connector step.
- **`sov_mapping`** — the near-universal upstream producer; its `config.sov_fields[*].name` set determines which columns this step can target.
- **`extract_rows_from_multiple_sources`** — alternative upstream TableV1 producer when addresses come from PDFs.
