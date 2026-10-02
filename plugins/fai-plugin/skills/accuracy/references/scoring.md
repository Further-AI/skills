# Local scoring — the offline (`--from-dir`) path only

**This is not how the report normally gets its numbers.** With `--dataset` or
`--workflow`, every accuracy percentage is read from the platform's own accuracy
report, and the platform's verdicts decide what is correct — see
`data_contract.md`. Nothing in this file runs on that path, and no knob in
`config.md` changes it.

The scorer described here runs only for `--from-dir <generated> <ground-truth>`:
two folders of per-Sample JSON, no platform access, no cached report. Use it to
score data you already have on disk, or to score a batch the platform has never
run.

**Its numbers are not the platform's.** The platform scores with a threshold
ladder (levenshtein at 0.85, date, boolean-string, null-equivalence, currency);
this scorer uses the equivalence rules below. On formatting-only differences the
two will disagree. Never present an offline number as the platform's, and never
compare one against the Eval Studio UI.

Local, field-level scoring: **correct fields / scored fields**. Every scored
value is one *claim*; claims compose additively from leaf to headline.

## Pipeline

1. Load AI-Generated and Ground Truth payloads per Sample, both as
   `{step name: dict | list[row dict]}`.
2. Apply `step_aliases` to **both sides**, then drop `exclude_steps` from both.
   A one-directional rename silently breaks the Samples where both sides
   already agreed, so aliasing is always symmetric.
3. Apply `drop_empty_rows` filters.
4. Score only steps present on **both** sides. A step on one side only is
   recorded in `only_in_generated` / `only_in_gt` and contributes **no claims** —
   it never inflates or deflates accuracy.
5. Walk each shared step, emitting claims; drop any claim in `exclude_fields`.

## Claim kinds

| kind | emitted for | counts toward |
|---|---|---|
| `scalar` | a top-level value | accuracy |
| `identity` | one list row's presence (TP / FP / FN) | accuracy + precision/recall/F1 |
| `aux` | a sub-field inside a matched list row | accuracy |

Precision, recall and F1 come from **identity claims only**. A scope with no
list rows reports `null`, never a synthesized value.

## Leaf comparison — decision order

First hit wins.

| # | Test | Result |
|---|---|---|
| 1 | both sides blank-or-zero | match, `missing-match` |
| 2 | exactly one side is a missing sentinel | miss, `missing-mismatch` |
| 3 | both parse as a full calendar date | equal -> `date`; unequal -> `mismatch` (decisive) |
| 4 | both parse as numbers | within 1e-6 absolute or 0.5 % relative -> `numeric`; else `mismatch` (decisive) |
| 5 | either side >= `long_text_min_chars` (40) | **not scored** — `skipped-long-text` |
| 6 | normalized text equal | match, `exact` |
| 7 | abbreviation-normalized equal | match, `normalized` |
| 8 | a configured synonym table matches the path and maps both sides to the same value | match, `synonym` |
| 9 | non-identifier path and one token set is a subset of the other | match, `subset-match` |
| 10 | otherwise | miss, `mismatch` |

**Missing sentinels** (compared against normalized text): empty, `n/a`, `na`,
`none`, `null`, `not found`, `not found in uploaded documents`, `unknown`,
`not specified`, `not applicable`, `not provided`, `not available`.

**null == 0.** A blank, a missing sentinel and a numeric zero are the same
thing. An extracted `0` against an unreported field is a match, both ways.
`False` is not a zero quantity.

**Normalization.** `normalize_text` lowercases, collapses whitespace, strips
surrounding quotes and a trailing period. `normalize_compare` additionally maps
address/unit abbreviations to one token, so `Suite 200` == `Ste 200` == `Ste. 200`
and `Main Street` == `Main St`. Turn that off with `address_normalization: false`.

**Dates.** Ordinal suffixes are stripped and `, / . -` all collapse to spaces,
so `December 30, 2025` == `Dec 30 2025` == `12/30/2025` == `2025-12-30`. Only
fires when **both** sides parse as a complete date — a bare year or
`December 2025` falls through to the text path, so the parser can never bridge a
date to a non-date.

**Token-subset match** exists so a refinement equals its generalization:
`Commercial` is a subset of `Umbrella - Commercial`. Stopwords (`of the and for
a an to in on or`) cannot bridge two values on their own. It is **suppressed on
identifier paths** (`identifier_fields`), where a partial value is a real error,
not a generalization — a truncated policy number must not subset-match the full
one.

## Long text is skipped, in both directions

Values at or above 40 characters are not scored at all. Fuzzy long-text matching
proved unreliable (boilerplate scores falsely similar), and crediting only the
long *exact* matches while dropping the long mismatches was a one-way upward
bias — a long field could score correct but never wrong. The length test sits
**before** the exact test on purpose, which makes the skip symmetric.

## Lists — identity matching, not position

1. **Pick an identity key.** In order: an `identity_overrides` entry for this
   step/path; the first `identity_candidate_keys` entry that is present and
   non-empty on **every** row on **both** sides and unique on at least one
   populated side; then (when `auto_identity_keys`) the same viability test over
   every key in the data, ranked by uniqueness; then a SHA1 of the row's
   deep-unwrapped JSON. Scalar rows key on their own normalized value.
   Keys are normalized, so `123 Main St` and `123 Main Street` land in the same
   bucket instead of splitting into an FP + FN pair.
2. **Strict matches** (keys on both sides): one `identity` claim, `TP`, plus one
   `aux` claim per sub-field.
3. **Soft pairs**: leftover rows are scored pairwise by how many top-level
   fields agree, sorted, greedily paired. A pair is accepted at >= 2 agreeing
   fields or >= 40 % of the wider row's field count. Soft pairs emit **aux
   claims only, no identity claim** — accuracy gets value-by-value partial
   credit while precision/recall/F1 stay strict about row identity.
4. **Truly unpaired**: one `identity` claim each — `FP` for a leftover
   AI-Generated row, `FN` for a leftover Ground Truth row. No aux claims.
5. Two empty lists yield nothing. There is **no list-length claim**: identity
   claims subsume length honestly, so a list with the right row count and all
   the wrong rows scores 0 %, not 100 %.

## `intersect_keys_only` — on by default

Dict comparison scores only keys present on **both** sides. One-sided keys yield
no claim at all.

AI-Generated output carries schema-wide keys that curated Ground Truth never
contains (back-compat aliases, internal markers), and Ground Truth carries stray
artifact keys the model never emits. Both are dataset-shape noise, not
extraction errors, and scoring them buries the real errors. A genuinely missed
extraction still scores as a miss, because the model emits its full schema with
null values — the key is present and blank against a Ground Truth value.
Identity FP/FN row claims are unaffected either way.

Set `--union-keys` to score the union instead.

## Aggregation

```
skip claims where match is None (long text)
total     = scored claims          correct = claims with match True
tp/fp/fn  = category counts on identity claims only
accuracy  = correct / total                (null when total == 0)
precision = tp / (tp + fp)                 (null when the denominator is 0)
recall    = tp / (tp + fn)
f1        = 2tp / (2tp + fp + fn)
```

Step accuracy is reported **pooled** (`sum(correct) / sum(total)` across
Samples), with `mean_of_ratios` alongside. When the two diverge, one dense
Sample is carrying the step.

## Error taxonomy

Every FALSE claim lands in one of three buckets with opposite root causes:

| leaf_kind | bucket | UI label |
|---|---|---|
| `missing-in-gen` | Missed | Missing |
| `extra-in-gen` | Hallucinated | Extra |
| `mismatch` | Wrong value | Wrong value |
| `missing-mismatch` | Missed if the AI-Generated side is blank, Hallucinated if Ground Truth is blank, else Wrong value | |
| anything else | Wrong value | |

Missed means the extraction did not find it. Hallucinated means it invented it.
Wrong value means it found the field and got it wrong. Fixing them takes three
different changes, which is why they are never pooled into one "error" number.

## Decisions that are deliberately unflattering

Each reverses an earlier version that produced a better-looking but wrong
number. Do not "fix" them without reading this list:

- Long text is skipped **symmetrically**, not fuzzy-matched.
- `null == 0 == 0.0`.
- Soft-paired rows get aux credit but **no** identity credit, so
  precision/recall/F1 stay strict.
- The list `__length__` claim was removed on purpose.

---

## Regression baseline

The offline scorer is pinned to a known-good result. Re-run this after any
change to `scoring.py`; the three numbers must match exactly.

```
Overall 49634/52041 = 95.4%  (2407 errors)
```

Reference dataset: 30 paired Samples, 18 scored steps, from the pipeline this
scorer was ported from. Reproduced with `--from-dir <generated> <ground-truth>`
plus a config replicating that pipeline's own settings — `intersect_keys_only`,
its step aliases, its exclude lists, its identifier and identity-candidate
vocabularies, its empty-row rule, and `--synonyms references/insurance_synonyms.json`.
Defaults alone will NOT reproduce it: the port deliberately replaced that
pipeline's domain-specific defaults with domain-neutral ones, so the vocabulary
has to be supplied explicitly.

Beyond the headline, `sub_stats`, `block_stats`, the heatmap axes, the error
taxonomy and the per-field totals all matched the original result exactly,
ordering included. The only intentional difference is field *labels*: the
customer label dictionary was replaced by a generic humanizer, so `lobs` renders
"Lobs" rather than "Lines of Business" unless `field_labels` says otherwise.
