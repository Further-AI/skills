# Eval Studio accuracy payloads — data contract

Everything the accuracy report reads. Two endpoints, two payload shapes.
Placeholders: `{ds}` = dataset ObjectId, `{sid}` = submission ObjectId,
`{run}` = evaluation run ObjectId.

**The platform already scored this batch. Read its verdicts; never re-score.**
See §8 for the two places where recomputing is wrong and produces numbers that
contradict the Eval Studio UI.

---

## 1. Endpoints

| # | Call | Returns |
|---|---|---|
| 1 | `GET /api/v1/evaluation/datasets/{ds}/accuracy-report?scoring_mode=dataset` | Summary — every aggregate, the sample roster, scoring config |
| 2 | `GET /api/v1/evaluation/datasets/{ds}/accuracy-report/submissions/{sid}?scoring_mode=dataset` | One sample's field-level detail |

Run-scoped twins (API-only, used to pin a comparison anchor across versions):

| Call | Extra param |
|---|---|
| `GET /api/v1/evaluation/runs/{run}/accuracy-report` | `config_hash=<hash>` instead of `scoring_mode` |
| `GET /api/v1/evaluation/runs/{run}/accuracy-report/submissions/{sid}` | `config_hash=<hash>` |

| Param | Values | Notes |
|---|---|---|
| `scoring_mode` | `dataset` (default) \| `all_fields` | `dataset` honors the batch's `scored_fields_by_step`; `all_fields` scores every comparable field. Reports are cached **per mode**; asking for a mode that was not generated returns `status: "not_generated"` |
| `config_hash` | opaque string | Run-scoped only. Returned when a run-scoped report is generated; identifies one exact variant |

Both are plain GETs. The detail endpoint 404s when no report exists, when the
cached report was generated for a different `scoring_mode`, or when the sample
is not in the scored set.

---

## 2. Read-only rules

| Rule | Why |
|---|---|
| Call these GETs directly via `FaiClient` | Report generation is a prod write with real cost |
| **Never pass `--generate`** | Forces regeneration, replaces cached state, spins a background job |
| **Do not shell out to `eval-studio`'s `accuracy-report` command** | On a cache miss (`not_generated` / `failed` / 400 / 404) that command **POSTs to generate**. A read-only report builder must not trigger that as a side effect |
| Generating a report is a user decision | If `status != "generated"`, stop and say so — offer `/fai:eval-studio` and let the user confirm the write |

```python
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_client import FaiClient
c = FaiClient(env=env, org_id=org_id, skill_name="accuracy")
BASE = "/api/v1/evaluation"
summary = c.get(f"{BASE}/datasets/{ds}/accuracy-report", params={"scoring_mode": "dataset"})
detail  = c.get(f"{BASE}/datasets/{ds}/accuracy-report/submissions/{sid}",
                params={"scoring_mode": "dataset"})
```

---

## 3. Status and staleness — check before reading anything

`summary.status`:

| Value | Meaning | Consumer action |
|---|---|---|
| `generated` | Cached report is ready | The only value safe to build from |
| `not_generated` | No cached report for this mode | Stop. Report has never been generated (or a different mode was). Ask the user |
| `generating` | Generation in flight | Stop. Poll or come back |
| `failed` | Generation failed; `failure_reason` present | Stop. Surface the reason |

Three more gates on the summary, all of which must be surfaced in the report
header:

| Key | Meaning |
|---|---|
| `is_stale` (+ `stale_reason`) | Batch scoring config changed after generation. **Every number is untrustworthy** — say so before quoting any of them |
| `partial` | `true` ⇒ at least one sample in the anchor run has no completed execution on the current pin. Report is scored on fewer samples than the run contains |
| `evaluation_run_status` | The anchor run's own historical status. `failed` here is common and harmless in repair flows — it does not invalidate the report |

The detail endpoint injects `is_stale` + `stale_reason` into its response only
when the summary is stale; normally the detail has neither.

Three coverage lists on the summary:

| Key | Meaning | Consumer action |
|---|---|---|
| `missing_completed_execution_submission_ids` | Scored-set samples with no completed current-pin execution | Offer as a "rerun these to clear `partial`" list |
| `missing_gt_submission_ids` | Samples with a completed execution but no answers | Excluded from scoring; does **not** set `partial`. Show as "unlabeled", never as failures |
| `missing_submission_ids` | Run-snapshot rows that failed to load | Rare; surface as an anomaly |

None of these appear in `summary.submissions` — do not attempt a detail fetch
for them.

---

## 4. Summary payload

Top-level keys:

```
status  dataset_id  dataset_name  report_schema_version  scoring_mode  report_scope
overall_accuracy  overall_accuracy_source  field_macro_accuracy  headline  views
precision_recall  total_fields  fields_matched  fields_mismatched
steps  submissions  scoring_config
evaluation_run_id  evaluation_run_status  execution_ids_by_submission
total_submissions_in_run  total_submissions_evaluated  completed_submissions_in_run
scorable_submissions_in_run  execution_status_counts
partial  is_stale  [stale_reason]  [failure_reason]  [config_hash]
missing_completed_execution_submission_ids  missing_gt_submission_ids
missing_submission_ids
```

### 4.1 `submissions` — the sample roster

```jsonc
"submissions": [{"submission_id": "<24-hex>", "submission_name": "<name>", "accuracy": 74.2}]
```

Every scored sample, already scored. **This is the index — never call
`list-submissions` to build it, and never fetch a detail just to learn a
sample's score.** Sample ordering for a worst-first view comes straight from
`accuracy` here.

### 4.2 `execution_ids_by_submission`

```jsonc
"execution_ids_by_submission": {"<submission_id>": "<execution_id>"}
```

Free join key. Gives every sample a deep link (`{app_url}/workflow-execution/{id}`)
and a handoff into `/fai:triage` when a step reports an error — with no extra call.

### 4.3 `headline` — the number you are allowed to quote

```jsonc
"overall_accuracy": 88.5,
"overall_accuracy_source": "micro_f1",  // micro_f1 | micro_accuracy
                                        // | scorer_step_accuracy | field_macro_accuracy
"headline": {
  "metric": "micro_f1",
  "f1": 0.8847, "precision": 0.8553, "recall": 0.9162,   // 0-1 fractions
  "coverage": 0.5979,
  "steps_scored": 19, "steps_with_counts": 17,
  "steps_without_counts": ["<step>", "<step>"]
}
```

`overall_accuracy` is 0–100; `headline.*` are 0–1 fractions. **They are not
interchangeable and are not the same statistic** — never present one as the
other.

**The headline covers classification-emitting steps only** (extraction and
row-extraction). Steps named in `steps_without_counts` cannot move it no matter
how badly they regress. Any surface quoting `overall_accuracy` must also carry
`overall_accuracy_source`, `headline.coverage`, and `steps_without_counts` —
high F1 at low coverage means little was actually labeled.

`views` = `{default_view: "C", overall: {A, B, C}, by_step: {<step>: {A, B, C}}}`,
each view `{accuracy, coverage, f1, precision, recall, support}` as fractions.
View `C` is the headline's source. `precision_recall` =
`{overall, by_step, macro}`.

### 4.4 `steps[<step>]` — per-step aggregate

```jsonc
"steps": {
  "<step name | __fai_eval_loop_doc__:…>": {
    "accuracy": 84.1,                       // 0-100, the number the UI shows
    "accuracy_source": "scorer_step_accuracy" | "field_macro_accuracy",
    "field_macro_accuracy": 72.6,
    "accuracy_threshold": 0.90,             // fraction
    "accuracy_threshold_percent": 90.0,     // 0-100
    "meets_accuracy_threshold": false,
    "accuracy_status": "failing",           // "passing" | "failing"
    "scored_field_count": 12,
    "available_field_count": 20,
    "omitted_field_count": 8,               // high ⇒ the field filter is narrowing on purpose
    "step_score_evaluations": 5,
    "type": "grid",                         // present on grid steps; absent on key-value steps
    "fields": { … },                        // §4.5
    "diagnostics": { … }                    // §6
  }
}
```

`accuracy_source`: `scorer_step_accuracy` is canonical; `field_macro_accuracy`
is a diagnostic fallback. Never summarize an experiment from macro values when
scorer accuracy exists. Gate un-counted steps on `meets_accuracy_threshold`,
which covers every scored step including the ones outside the headline.

### 4.5 `steps[<step>].fields[<field>]` — per-field aggregate across the batch

Keys are **display labels** (`"Policy Number"`), already humanized. There is no
`field_labels` map in the response — the labels are applied and the map removed
before the payload is returned. Raw schema names appear only inside
`diagnostics.field_filtering.scored_fields`; the two do not join directly.

```jsonc
// counted field (extraction / row extraction)
{"accuracy": 38.3, "precision": 38.3, "recall": 38.3, "submissions_compared": 5}
// key-value field
{"accuracy": 91.0, "submissions_compared": 5, "mismatches": 2}
// nested-array field inside a key-value step — submissions_compared counts ROWS, not samples
{"type": "array", "accuracy": 76.4, "submissions_compared": 118, "mismatches": 28}
```

### 4.6 `scoring_config` — the snapshot actually used

```
scoring_mode  selected_steps  scored_fields_by_step  grid_row_match_keys
default_step_accuracy_threshold  step_accuracy_thresholds_by_step
effective_step_accuracy_thresholds  scorer_overrides_by_step  comparison_config
scoring_engine_version  config_hash
```

Render this verbatim in a methodology footer — it is what makes the numbers
reproducible.

---

## 5. Per-sample detail payload

```jsonc
{
  "submission_id": "<24-hex>",
  "submission_name": "<name>",
  "accuracy": 74.2,                                   // 0-100, platform-computed
  "accuracy_source": "scorer_step_accuracy" | "field_macro_accuracy",
  "field_macro_accuracy": 63.5,
  "steps": {"<step name | loop key>": <step block>}
}
```

A sample's `steps` is a **subset** of the summary's `steps` — a step absent here
was not scored for this sample (no output, or no answers). The payload does not
say which. Treat an absent step as a null heatmap cell, never as zero.

Three step-block shapes:

### 5.1 Errored step

```jsonc
{"error": "<message>", "diagnostics": {…}}
```

No field data. Render the message and offer the execution deep link.

### 5.2 Key-value step

```jsonc
{
  "type": "key_value",
  "accuracy": 70.9, "accuracy_source": "…", "field_macro_accuracy": 70.9,
  "fields": {"<Display Label>": <field record>},
  "diagnostics": {…}
}
```

**Field record — the core drill-down unit:**

```jsonc
{
  "match":     false,                       // the verdict
  "expected":  "<ground truth value>",      // Answers / Ground Truth
  "predicted": "<model value>",             // AI-Generated
  // optional, when the field is an object:
  "type": "object", "preview_value_expected": "…", "preview_value_predicted": "…",
  // optional:
  "scoring_diagnostics": {…}
}
```

Field path = `"<step name> · <Display Label>"`. `expected` / `predicted` are the
raw scored values — any JSON type, including `null`, `""`, `[]`, `{}`.

**Nested-array field** (a list living inside a key-value step):

```jsonc
{
  "type": "array",
  "match": false,
  "summary": {"expected_count": 4, "predicted_count": 5,
              "matched_rows": 3, "mismatched_rows": 0,
              "missing_rows": 1, "extra_rows": 2},
  "preview_value_expected": "…", "preview_value_predicted": "…",
  "items": [{
    "row_index": 0,                 // synthetic, sequential
    "overall_match": true,
    "expected_index": 1,            // position in the answers array
    "predicted_index": 0,           // position in the AI output array
    "preview_value_expected": "…", "preview_value_predicted": "…",
    "fields": {"<raw key>": {"match": true, "expected": …, "predicted": …}}
  }]
}
```

Note: `items[].fields` keys stay **raw** (not display labels), unlike the
top-level `fields`.

### 5.3 Grid step (row extraction / table)

```jsonc
{
  "type": "grid",
  "accuracy": 84.1, "accuracy_source": "…", "field_macro_accuracy": 72.6,
  "items": [ <row> ],
  "diagnostics": {…}
}
```

Read §7 before scoring anything from `items`.

---

## 6. `diagnostics`

Whitelisted to `{scorer, comparison, field_filtering, alignment, loop_iteration,
warnings}`; present on both summary steps and detail steps.

| Key | Contents | Use |
|---|---|---|
| `scorer` | `{name, class}` — e.g. `extract`, `extract_rows` | Methodology footer |
| `comparison` | `{mode, threshold, default_comparisons[], type_comparison_overrides{}, confidence_aware, custom_null_equivalents, comparison_config_applied}` | **Explains why a "different-looking" value matched.** `default_comparisons` is the ladder (exact, null-equivalence, date, case-insensitive, boolean-string, levenshtein) and `threshold` its cutoff |
| `field_filtering` | `{mode, scored_fields[]}` — `mode` is `all_fields` or the dataset filter; `scored_fields` are **raw** schema names | Explains `omitted_field_count` |
| `alignment` | grid only — see §7 | Row FP/FN counts without walking `items` |
| `loop_iteration` | document-loop steps only — see §9 | Readable step + document names |
| `warnings` | list of strings | Surface non-empty ones |

The comparison ladder is why local re-scoring is wrong: a value pair that looks
unequal as strings may be a legitimate match by date, numeric, or
null-equivalence rules.

---

## 7. Grid / row steps — alignment, false positives, false negatives

`items` is the **aligned** row list, in fixed order: matched pairs first, then
extra rows, then missing rows.

```jsonc
// matched pair — NO "status" key at all
{"row_index": 0, "overall_match": false,
 "preview_value_predicted": "…", "preview_value_expected": "…",
 "fields": {"<col>": {"match": false, "expected": "…", "predicted": "…"}}}

// FALSE POSITIVE — row in AI output with no counterpart in the answers
{"row_index": 12, "status": "extra", "overall_match": false,
 "preview_value_predicted": "…",
 "fields": {"<col>": {"match": false, "expected": null, "predicted": "…"}}}

// FALSE NEGATIVE — row in the answers the AI never produced
{"row_index": 13, "status": "missing", "overall_match": false,
 "preview_value_expected": "…",
 "fields": {"<col>": {"match": false, "expected": "…", "predicted": null}}}
```

| Signal | Meaning |
|---|---|
| `status` absent | Matched pair. `overall_match` then splits clean from mismatched |
| `status: "extra"` | **False positive** — present in AI output, absent from answers |
| `status: "missing"` | **False negative** — present in answers, absent from AI output |
| `preview_value_predicted` absent | Always on `missing` rows — render a dash, not "None" |
| `preview_value_expected` absent | Always on `extra` rows |

**`row_index` is synthetic** — a counter assigned while walking matched → extra →
missing. It is not the row's position in the AI table or in the answers, and grid
items carry **no** `expected_index` / `predicted_index` (those exist only on
nested-array items, §5.2). A grid row cannot be traced back to a source row
number from this payload.

`diagnostics.alignment` gives the row-level counts directly:

```jsonc
{"strategy": "metric_alignment", "row_match_keys": ["<col>"],
 "actual_rows": 31, "expected_rows": 31,
 "matched_rows": 31, "missing_rows": 0, "extra_rows": 0}
```

`row_match_keys` here is what was **applied**; `scoring_config.grid_row_match_keys`
is what was **configured**. When applied is empty and rows are misaligning, the
fix is configuring row-match keys on the batch, not blaming the workflow.

**Cell `match` flags inside grid rows are display data, not scoring data.**
Every cell in an `extra` or `missing` row is stamped `match: false` by
construction — including cells that are blank on both sides. Counting cells or
rows from `items` does **not** reproduce the step's `accuracy`. See §8.

Empty `items` with a non-zero `accuracy` is normal for steps that emit no
classification counts — the ones listed in `headline.steps_without_counts`.
Present those as "not covered by the headline", never as "0 rows compared".

---

## 8. What to read vs what to compute — the reconciliation rule

Verified against a real generated report (19 steps, 5 samples):

| Quantity | Source | Reconciles with the UI? |
|---|---|---|
| Batch headline | `summary.overall_accuracy` + `headline` | Yes — authoritative |
| Per-step accuracy | `summary.steps[s].accuracy` | Yes — authoritative |
| Per-field accuracy / precision / recall | `summary.steps[s].fields[f]` | Yes — authoritative |
| Per-sample accuracy | `summary.submissions[].accuracy` = `detail.accuracy` | Yes — authoritative |
| **Per-(step, sample) accuracy** | **`detail.steps[s].accuracy`** | **Yes — authoritative. Use this for a heatmap cell** |
| Key-value correct / total counts | Count `detail.steps[s].fields[*].match` | Yes — matched the platform's per-step accuracy **exactly**, on every key-value step tested |
| **Grid correct / total counts** | Counting `items` rows or cells | **No.** Row-exactness and cell-counting both diverge from the platform's grid accuracy, by up to 85 points on a single step |
| Grid row FP / FN counts | `status` tallies, or `diagnostics.alignment` | Yes — these are row-structure facts, independent of the accuracy metric |

**Rule: read every accuracy percentage from the payload. Derive counts only for
key-value steps. For grid steps, derive row structure (matched / extra /
missing) but never an accuracy.** A heatmap or step table that mixes a
platform-supplied percentage with a locally counted one will show two numbers
that disagree, and the locally counted one will be wrong.

**Second trap — blank ground truth is ambiguous.** A field record with
`expected: null` may mean the answers say "empty" *or* that the cell was never
labeled. The detail payload carries no per-cell "unlabeled" flag; only the
batch-level `headline.coverage` reports how much of the surface was labeled at
all. So an error taxonomy derived from blankness will over-count "extra /
hallucinated" on a sparsely labeled batch. Classify from `status` where it
exists (unambiguous), and caveat blank-derived buckets against `coverage`.

---

## 9. Document-loop steps

A step inside a per-document loop expands into one scored target per document,
keyed by an opaque string:

```
__fai_eval_loop_doc__:<base64url-encoded JSON, padding stripped>
```

The encoded object is `{"step_name": "<definition step>", "user_document_id": "<id>"}`
plus optional `"loop_parent"` and `"file_hash"`.

**Treat the key as a read-only string.** Never construct, normalize, or
hand-write one; never substitute `Step::iter_N`, a filename, or `Step / filename`.
Pass it back verbatim wherever a step name is required.

Recover readable identity from diagnostics rather than decoding:

```jsonc
"diagnostics": {"loop_iteration": {
  "definition_step_name": "<the human step name>",
  "execution_step_name": "<step>::iter_2",
  "loop_parent": "<the loop step>",
  "iteration_index": 2,
  "iteration_label": "…",
  "document": {"user_document_id": "<id>", "filename": "<file>", "file_hash": "…"},
  "document_error": "…"          // present when the loop item is not a single document
}}
```

| Need | Read |
|---|---|
| Display name | `definition_step_name` — `document.filename` |
| Grouping | group all keys sharing `definition_step_name`, label the group `loop_parent` |
| Stable order | `iteration_index` |
| Threshold / scored-field lookup | `definition_step_name` — config is keyed on the logical step, **not** the loop key |

Fallbacks when diagnostics are absent: `display_name`, then
`original_step_name`, then a generic per-document label. Never show the raw key.

---

## 10. Call budget

For N scored samples and S steps, against an already-generated report:

| # | Call | Count |
|---|---|---|
| 1 | `GET /datasets/{ds}` (config, pin status, app URL) | 1, optional |
| 2 | `GET /datasets/{ds}/accuracy-report` | **1** |
| 3 | `GET /datasets/{ds}/accuracy-report/submissions/{sid}` | **N** |
| | **Total** | **N + 2** |

- `list-submissions` and `runnable-steps` are **not needed** — §4.1 and §4.4
  already carry the roster and the scored step set.
- Nothing above is O(N×S). The routes that are — the per-sample-per-step
  side-by-side view and per-step ground-truth reads — must stay **lazy**: one
  call for the one step a user expands, never a precompute sweep.
- **Size is the real budget, not call count.** A single detail payload runs from
  tens of KB to **hundreds of KB** (observed: 33 KB to 466 KB for one sample).
  At N=50 that is tens of MB of JSON.

**Stream and reduce. Never accumulate.** Fetch one detail, fold it into the
report's own counters, drop it. Never hold all N in memory, never write all N
into one intermediate file you later re-read, and never put a raw detail payload
into model context — parse it in a script and emit only the aggregates.

Skip detail fetches for `missing_gt_submission_ids`; they are not in the scored
set and will 404.

---

## 11. From payload to the report's row model

What `studio_fetch.reduce_detail()` emits, and why. One row per field record the
payload states, plus one row per aligned list row. Nothing is invented; nothing
is re-judged.

### Row kinds

| kind | Emitted for | `category` | Feeds |
|---|---|---|---|
| `scalar` | one field of a key-value step | — | verdict counts, value tables |
| `aux` | one cell of a grid row, or one field of a nested array row | — | verdict counts, value tables |
| `identity` | one aligned row (grid `items[]` or array `items[]`) | `TP` / `FP` / `FN` | precision / recall / F1, Missing-vs-Extra row counts |

Every row carries `match` exactly as the payload states it, `gen` =
`predicted`, `gt` = `expected`, plus `block` (the step's display label),
`field_label`, and `step_type` (`key_value` / `array` / `grid`).

`step_type` exists for one reason: it marks which scopes may express their
verdict counts as a percentage. See §8 — key-value counts reconcile with the
platform's step accuracy, grid counts do not.

### Row identity from `status`

| `status` | Meaning | identity row |
|---|---|---|
| absent | the platform paired the row | `match: true`, `TP` |
| `"extra"` | in AI-Generated output, not in Answers | `match: false`, `FP` |
| `"missing"` | in Answers, not in AI-Generated output | `match: false`, `FN` |

Grid steps with `items: []` and a non-null accuracy (SOV / custom shapes) yield
no rows at all. They still appear in the report, flagged `no_row_model`, so a
scored step is never silently dropped.

### Error buckets

An already-false verdict is bucketed for display only — this never decides
correctness:

1. row `status` when present (`extra` → Extra, `missing` → Missing) — unambiguous.
2. otherwise from blankness: blank `expected` → Extra, blank `predicted` →
   Missing, both populated → Wrong value.

Step 2 is the ambiguous one, and the report must say so. A blank `expected` may
mean "the answer is empty" or "this cell was never labeled", and the payload
carries no per-cell labelled flag — only batch-level `headline.coverage`. On a
sparsely labelled batch step 2 over-counts Extra. The count of buckets derived
this way is published as `stats["taxonomy_caveat"]["blank_derived_errors"]`;
render it next to `coverage`, never bury it.

### Which accuracy goes where

| stats scope | Source |
|---|---|
| `overall.accuracy` | `summary.overall_accuracy` (+ `accuracy_source`, `coverage`) |
| `sub_stats[].accuracy` | `detail.accuracy`, falling back to the roster entry |
| `block_stats[].accuracy` | `summary.steps[<key>].accuracy` |
| `block_stats[].mean_of_ratios` | mean of the per-Sample `detail.steps[<key>].accuracy` |
| `heatmap` cell | `detail.steps[<key>].accuracy` |
| batch-level field | `summary.steps[<key>].fields[<label>].accuracy` |
| per-(step, Sample) field | counted — key-value and array steps only; `null` on grid |
| `(items)` pseudo-field | counted row structure — matched vs extra/missing is stated by the payload |

`correct` / `total` / `errors` are tallies of the payload's own verdicts at
every scope. They are counts, not an accuracy, and on grid steps they will not
divide out to the step's accuracy. Label them as verdict counts wherever they
are shown.
