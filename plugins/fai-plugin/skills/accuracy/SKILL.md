---
name: accuracy
description: Builds a FurtherAI-branded interactive HTML accuracy report for a workflow or Eval Studio test batch — overall score, error concentration, a step-by-sample heatmap, a drill-down lens, and field-level AI-generated vs ground-truth comparisons. Use when the user asks for an "accuracy report", "how accurate is this workflow", "where is the workflow getting things wrong", "which steps are hurting accuracy", "error analysis", or wants to investigate eval results in depth. Produces one self-contained HTML file and nothing else.
---

# Accuracy report

Scores a Test Batch's AI-generated output against its Ground Truth field by
field, then renders one self-contained interactive HTML file for investigating
where the errors are.

**The HTML report is the only deliverable.** Intermediate JSON, scratch files,
and coverage dumps are working artifacts — do not hand them to the user, do not
leave them in their working directory, and do not summarize the whole report
back in chat. Give them the path, and a two-line verdict.

This skill is **read-only** against the platform. It never generates a server
report, never triggers a run, never touches ground truth.

## Run it

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/accuracy/scripts/accuracy_report.py" \
  --dataset <objectid> --account <slug> [--env prod|staging|local] \
  [--out ACCURACY_REPORT.html]
```

| Flag | Purpose |
|---|---|
| `--dataset <objectid>` | The Test Batch to score. 24-char hex ObjectId |
| `--workflow <uuid\|name>` | Instead of `--dataset`: find the batches on a workflow. Multiple hits → the script lists them and you ask the user which |
| `--out <path>` | Output HTML. Default `accuracy_report_<date>.html` in cwd |
| `--title <str>` | Report title. Default `<Test Batch> — Accuracy Report` |
| `--check` | Coverage only: report status, staleness, the scored sample roster, and samples missing answers or a completed execution. No report built. **Run this first on an unfamiliar batch** |
| `--max-rows <int>` | Per-sample budget for *agreeing* rows (default 50). Disagreeing rows are always kept in full — this trades context for file size, never evidence |
| `--full` | No row cap. Produces a much larger file — only when the user asks to see everything |
| `--scoring-mode dataset\|all_fields` | Which report variant to read. Default `dataset` — the batch's own scored-field config. `all_fields` scores everything comparable |
| `--max-samples <int>` | Read only the first N samples — a probe for a large batch. **Produces a partial report**: the headline and per-step accuracy still describe every sample, while the drill-down covers only N. The report says so in its scoring note; never quote a number from one |
| `--stats-json <path>` | Also write the raw stats dict — for debugging the skill, not for the user |
| `--from-dir <gen> <gt>` | Offline path: score two folders of JSON locally instead of reading the platform |

**Offline-only flags**, meaningful only with `--from-dir`: `--exclude-step`,
`--exclude-field`, `--step-alias A=B`, `--identity-key <step>:<field>`,
`--synonyms <json>`, `--union-keys`, `--config <json>`. Passing one on the
platform path prints a warning and ignores it — the platform did the scoring,
so its verdicts and config are read as-is. Changing how a batch is scored
belongs in Eval Studio, not here. `references/config.md` covers them all.

Standard `--env` / `--account` / `--org-id` resolution applies, per the account
database. `--org-id` always wins.

## Procedure

1. **Resolve the batch.** The user usually names a workflow or an account, not
   an ObjectId. Use the eval-studio skill to find it:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/eval-studio/scripts/eval_studio.py" \
     list-datasets --account <slug> --workflow <uuid|name>
   ```
   More than one batch → show the user the candidates with names and types
   (live / gold / regression) and ask. Never pick for them.

2. **Check before building.** `--check` reports whether a scored report exists,
   whether it is stale, and which samples were skipped for missing answers or a
   missing completed execution. A batch with half its Ground Truth unfilled
   produces a meaningless headline — say so and ask before continuing.

3. **Build the report.** Then tell the user: the output path, the overall
   accuracy, the two or three worst steps, and one sentence on what to look at
   first. Nothing more — the report is the deliverable.

4. **Offer the drill-down, don't perform it.** The report exists so the user can
   click into a step and see the actual values. Answer follow-up questions from
   it when asked; don't preemptively narrate every step's numbers into chat.

## Where the numbers come from

The report is built from the platform's own scored report — the same numbers
Eval Studio shows — not from a second scoring pass. Two reads:

| Call | Count | Carries |
|---|---|---|
| the batch's accuracy report | 1 | headline, per-step and per-field accuracy, the scored sample roster, scoring config, execution IDs |
| that report's per-sample detail | one per sample | every field's ground truth value, AI value, and verdict; grid row alignment |

So this report **agrees with the Eval Studio UI**. It adds the investigation
layer the UI doesn't have: cross-step concentration, a step-by-sample heatmap,
and value-level drill-down.

### Two numbers, and they are not interchangeable

The report shows the platform headline **and** a field-verdict count, separately
labeled, because they answer different questions:

- **Headline accuracy** — the platform's number, matching Eval Studio. On
  current report versions it is micro-F1 over classification-emitting steps
  only. Function, SOV, and custom steps carry full field-level detail in this
  report but **cannot move this number**.
- **Field verdicts** — how many scored fields agree, as a count. A different
  denominator, so it will not equal the headline percentage.

Never quote one as if it were the other, and never quote the headline without
its accuracy source, its coverage, and the steps excluded from it.

Grid and row steps deliberately show **no derived accuracy** — only counts and
row alignment. Computing one locally diverged from the platform's by up to 85
points on a real step, so the report shows what the platform scored and stays
silent where it didn't. In the heatmap that is a distinct third cell state:
scored, **unscored** (ran, counts only), and absent (didn't run).

Three consequences worth stating to the user when they come up:

- **A report must already exist.** If the batch has never been scored, this
  skill stops and tells you to generate it in Eval Studio. Generating is a
  write, and it belongs behind the eval-studio skill's approval gate — this
  skill will not do it for you.
- **A stale report is reported as stale.** The workflow may have moved on since
  the anchor run. Say so before quoting a number.

`--from-dir` is a separate offline path that scores two folders of JSON with no
platform access. Its numbers are computed locally and will differ slightly from
the platform's on formatting-only differences. Don't mix the two in one
comparison.

## What the report contains

| Panel | What it answers |
|---|---|
| Hero + metric cards | Headline accuracy with its source and coverage, field verdicts, errors to fix |
| Takeaways | The six worst steps and the six worst samples |
| Where errors concentrate | Pareto: which steps own the errors, split by error type, with a cumulative curve. Toggle raw count vs mix % |
| Step × sample map | Heatmap of accuracy per step per sample, with a consistency bar per step. Three cell states: scored, unscored (ran, counts only), absent (didn't run). Click a row, column, or cell to scope the lens |
| Lens | The analytical core. Bars for whatever scope is selected — samples, steps, or fields — across Accuracy / Precision / Recall / F1. Fields can switch to an error-mix view. Click a field bar to filter the values below |
| Values | Side-by-side AI-Generated vs Ground Truth for the selected scope, or the top mismatch patterns for a selected field |
| Per-sample detail | One expandable section per sample, mismatches shown by default |

## Reading the report

Three error types, used consistently: **Missing** (ground truth has a value, the
AI produced nothing), **Wrong value** (both have values and they differ), and
**Extra** (the AI produced a value or a row ground truth doesn't have).

Grid and row steps are matched by row identity, not position, so one extra row
near the top doesn't cascade into everything below it reading as wrong. Rows on
only one side become Extra or Missing.

Steps that run once per document appear per document, labeled with the source
filename rather than an internal key.

**One caveat the report surfaces itself.** A blank Ground Truth cell is
ambiguous — it can mean "the answer is genuinely empty" or "this was never
labeled", and the payload cannot tell them apart. Errors bucketed from a blank
therefore lean toward **Extra** on a sparsely labeled batch. The report prints
how many of its errors were derived this way; read that against coverage before
trusting the Missing/Extra split. If the number is large, the fix is filling in
ground truth, not reinterpreting the chart.

`references/data_contract.md` has the payload shapes and the exact field record
structure — read it **only** when the report shows something you can't explain
from the page itself, or when debugging the skill. The scripts do the parsing;
you don't need it to run a report.
`references/config.md` and `references/scoring.md` cover the offline
`--from-dir` path — its scoring knobs, and how a field is compared when this
skill does the scoring itself. Read them **only** when working offline or
explaining an offline number; the platform path uses neither.

## Failure modes

| Symptom | Cause and fix |
|---|---|
| `--check` shows most samples with no ground truth | The batch's Answers were never filled in. Report it and stop; a report over empty answers is a fake number |
| A headline that doesn't match the samples shown | `--max-samples` was used. The headline comes from the platform over the whole batch; the drill-down is truncated. Re-run without it before quoting anything |
| The skill refuses with "no report generated" | Nothing has scored this batch yet. Ask the user to run it in Eval Studio — generating is a write and needs their confirmation |
| A grid step shows many Extra + Missing rows | Row identity isn't resolving on the platform side. Check the batch's row-match keys in Eval Studio before treating it as an extraction problem |
| Every field in one step is Missing | That step likely didn't run for those samples — confirm on the heatmap before treating it as an accuracy problem |
| Output HTML is very large | Lower `--max-rows`, or drop `--full`. Mismatch rows are never dropped |
| Credentials error | The user's `~/.fai/credentials.env` is missing or wrong for this environment. Send them to `/fai:setup`; don't hunt for other credential files |

## Don't

- Don't hand over intermediate files. One HTML report, nothing else.
- Don't quote the local headline and the platform headline as one number.
- Don't score a batch whose Ground Truth is mostly empty without flagging it.
- Don't add customer-specific step names, field names, or synonyms to the
  defaults — pass them per run with the flags, or in a `--config` file.
- Don't make any write call. If the user wants a batch run or ground truth
  edited, that is the eval-studio skill's job, with its own approval gate.
