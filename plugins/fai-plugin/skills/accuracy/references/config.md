# ScoringConfig — every knob

**Applies to `--from-dir` only.** On the `--dataset` / `--workflow` path the
platform did the scoring and its config is read from the report payload
(`stats["platform"]["scoring_config"]`); none of the knobs below have any
effect there, and passing them prints a warning. To change how the platform
scores, change the Test Batch config in Eval Studio and regenerate the report.

One `ScoringConfig` is built by the CLI and threaded through every local scoring
function. Nothing is a mutable module global; there is no monkey-patching.

Set knobs three ways, applied in this order:

1. `--config <inline JSON | @file | path>` — a JSON object whose keys are the
   field names below. Unknown keys are rejected with the list of valid ones.
2. `--synonyms <json>` — merged into `synonyms`.
3. Repeatable flags: `--exclude-step`, `--exclude-field`, `--step-alias`,
   `--identity-key`, `--union-keys`. These add to whatever `--config` set.

The resolved config is handed to the renderer in `meta["config"]` and printed in
the report footer, so any number in the report is reproducible.

| Knob | Default | What it changes |
|---|---|---|
| `step_aliases` | `{}` | `{raw step name: canonical name}`, applied to **both** sides before step matching. Use when the same step is labeled differently in the two payloads (a variant name that does not correlate between sides). Aliasing is symmetric on purpose — a one-directional rename breaks the Samples where both sides already agreed. CLI: `--step-alias "Old Name=Canonical Name"`. |
| `exclude_steps` | `{}` | Step names dropped from both sides entirely, applied **after** aliases. For steps that are not part of the accuracy question (quality gates, derived-ratio steps). CLI: `--exclude-step "Step Name"`. |
| `exclude_fields` | `{}` | `{(step, field label)}` dropped from the claim stream before any aggregation, so overall / step / field / heatmap / cell metrics all agree. Finer-grained than dropping a whole step. Note it matches the **display label** ("Source Name"), not the raw key. CLI: `--exclude-field "Step Name:Source Name"`. |
| `intersect_keys_only` | `True` | Score only dict keys present on both sides. On by default: one-sided keys are dataset-shape noise, not extraction errors (see scoring.md). CLI escape hatch: `--union-keys`. |
| `identifier_fields` | `id, _id, number, code, ssn, ein, tax_id, account` | Path markers where token-subset matching is suppressed, because a partial value is a real error rather than a generalization. Matched at a left word boundary inside the path, so `id` hits `location_id` and `id` but not `guideline`. Add your own identifier vocabulary when a truncated code is being scored as correct. |
| `synonyms` | `{}` | `{path marker: {from: to}}`. Values are compared after both sides pass through the table, so an acronym equals its expansion. Gated to paths containing the marker so a short-value map cannot bridge unrelated fields. Ships off; an insurance line-of-business preset is in `references/insurance_synonyms.json` — pass it explicitly with `--synonyms`. Top-level keys beginning with `_` are treated as comments. |
| `drop_empty_rows` | `{}` | `{step: [field, ...]}` — drop list rows where **all** listed fields are blank or zero, on both sides. A row whose quantity columns are all empty is not a real row, and the two sides disagreeing about which empty rows to enumerate produces spurious FN + FP pairs. Rows carrying none of the listed fields are untouched. |
| `long_text_min_chars` | `40` | Values this long or longer are skipped, not scored, in both directions. Raise it to score more prose (and accept the noise); lower it to skip more. |
| `address_normalization` | `True` | Map US address/unit abbreviations to one token in the comparison path, so `Suite 200` == `Ste 200` == `Ste. 200`. Turn off for non-address-heavy data where `st`/`dr` collisions would be misleading. |
| `record_label_fields` | `name, *_name, title, label, description, id, *_id, number, *_number, code, *_code, address` | Ordered preference for naming a list row in the report UI. Not used in scoring — passed to the renderer via `meta["record_label_fields"]`. `*_suffix` entries are patterns. |
| `identity_candidate_keys` | `id, *_id, uuid, key, number, *_number, code, *_code, name, *_name, title, label, address, *_address, email` | Ordered candidates for list-row identity matching. A candidate is used only when it is present and non-empty on every row of both sides and unique on at least one populated side. `*_suffix` entries are patterns; within one pattern the matching keys are sorted, so the choice is deterministic. |
| `identity_overrides` | `{}` | `{step or dotted path: field}` forcing the identity key for one list, bypassing candidates and auto-detection. Reach for it when rows are being reported as FP+FN pairs that a human would call the same row. CLI: `--identity-key "Step Name:policy_number"` or `--identity-key "Step Name.locations:location_id"`. |
| `auto_identity_keys` | `True` | When no configured candidate fits, derive one from the data: any key present on every row and unique on one side is viable, ranked by uniqueness. Turn off to fall straight through to the deep-equality hash, which matches rows only when they are byte-identical. |
| `acronyms` | `id, ids, url, urls, uri, api, vin, ssn, ein, uw, crm, dba, sku, pdf, csv, json, xml, html, zip` | Tokens uppercased whole when a field key is humanized into a label. |
| `field_labels` | `{}` | `{field key: display label}` explicit overrides, for keys the generic humanizer cannot get right (`lobs` -> `Lines of Business`). Remember `exclude_fields` matches the resulting label. |

## Worked example

A config file replicating a heavily-tuned engagement:

```json
{
  "step_aliases": {"Vendor A Check Output": "Check Output",
                   "Vendor B Check Output": "Check Output"},
  "exclude_steps": ["Quality Check", "Computed Ratios"],
  "exclude_fields": [["Merged Tables", "Source Name"]],
  "identifier_fields": ["policy_number", "claim_number", "case_number", "tax_id"],
  "identity_overrides": {"Loss History": "claim_number"},
  "drop_empty_rows": {"Staffing": ["full_time_count", "part_time_count"]},
  "field_labels": {"lobs": "Lines of Business"}
}
```

```
accuracy_report.py --account acme --dataset 665f... \
  --config engagement.json --synonyms references/insurance_synonyms.json \
  --out report.html
```

## Sizing the output

`--max-rows` (default 2000) budgets the **correct** value rows embedded per
Sample; mismatch rows are always included, because they are the reason to open
the report. `--full` removes the budget. The row payload dominates the file
size, so an unbudgeted report on a large Test Batch runs to several megabytes
and is hostile to email and Slack.

## When a number looks wrong

| Symptom | Likely knob |
|---|---|
| Accuracy tanks on keys the Ground Truth never had | `intersect_keys_only` is off |
| The same row shows as one FP and one FN | `identity_overrides`, or a candidate key that is not unique |
| A truncated code scores as correct | add it to `identifier_fields` |
| An acronym scores as wrong against its expansion | add a `synonyms` table for that path |
| A step dominates the error count for reasons nobody cares about | `exclude_steps` / `exclude_fields` |
| Prose fields are scored at all, or not at all | `long_text_min_chars` |
| Rows that are empty on both sides create errors | `drop_empty_rows` |
