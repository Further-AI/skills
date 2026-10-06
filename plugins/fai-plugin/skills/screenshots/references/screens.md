# Screen catalog

How to reach each screen of the FurtherAI app and what is worth framing once
you are there. This file owns *how to get a picture of a screen*. It does not
decide which screens an artifact uses — that is the artifact's contract
(`doc`'s `workflow_user_guide`, a deck's slide plan).

Verified against the app on 2026-08-21. If an anchor below no longer matches,
fix it here in the same change that fixes the recipe in `scripts/capture.py`.

## UI vocabulary — use these words in copy

| Thing | The UI calls it |
|---|---|
| workflow execution | a row in **Runs**; the page is a submission |
| success status | **Completed** (never "succeeded") |
| start a submission | **Add New** |
| submit staged files | **Confirm Details & Submit** |
| the step list header | **… Successfully Executed**, with **N Steps** |
| open a step's output | the **→** at the right of the step row |
| re-run one step | the **↺** left of the arrow (only on steps configured for it) |
| step output controls | **Export**, **Edit** |
| source-document chips | the file pills above a step's table |
| final output step | the last step, e.g. **Display Summary**, with **View Email** |
| queue controls | **Search**, **Add filter**, **Saved views**, **All / Published / Draft** |

## Geometry

| Element | Where |
|---|---|
| main content | `#main-content` — starts at the sidebar's right edge |
| sidebar, expanded | ~273 CSS px wide; **lists every workflow in the org** |
| sidebar, collapsed | ~64 CSS px icon rail; collapses on its own inside a submission |
| right panel | opens over the right half when a step or file is opened |

The sidebar is the recurring confidentiality problem: on a shared org it names
other customers' workflows. Crop from the content edge, and check the frame
before shipping. `rects` records `content` for exactly this.

Captures are headless, so the viewport is exactly what `--window W H` asks for
(the driver reserves 100px for the browser chrome a headed window would have)
at a pinned dpr of 2: the default 1640x1100 gives a 1640x1000 viewport and a
3280x2000 image, on any display. A headed run instead inherits the real window,
which the OS may clamp — 1492x842 on a 14in screen — so coordinates from a
headed capture will not match a headless one. Prefer `--crop-rect` over fixed
crop numbers for exactly this reason.

Wide tables still need horizontal scroll, which is what the user sees too.

## Catalogued screens

### `workflow_runs`
Route `/workflows/{workflow_id}`. Anchor `Add New`.
The queue: NAME, STATUS, ASSIGNEE, STARTED BY, VERSION, PROGRAM, plus workflow
columns. Rects: `add_new`, `runs_tab`, `content`.
Crop hint: from the content edge through the ASSIGNEE column; the far-right
columns clip mid-header otherwise.
Failed rows are normal in a real org and are honest to show — the status column
is the point. Do not filter them away without saying so.

### `filters`
`workflow_runs` then click **Add filter**. Anchor `Status`.
Menu lists Status, Started by, Run date, Program, Effective Date, Priority,
Triage Score, Submission Status, Documents Complete, Columns.

### `new_submission`  — **WRITES**
`workflow_runs` then **Add New**. Anchor `Drag & Drop`.
Clicking Add New immediately creates a draft submission and navigates to
`/workflow-execution/{draft_id}`; the id is returned as `draft_execution_id`.
Empty drop zone, subtitle "Choose from Document Library, Sharepoint, etc."

### `staged_files`  — **WRITES**
`new_submission` then `--files`. Anchor `Confirm Details & Submit`.
Files attach through the hidden `input[type=file]`; wait for the green ticks
before capturing. Rects: `submit`, `add_more`.
**Stop here.** Never click submit.

### `submission_overview`
Route `/workflow-execution/{execution_id}`. Anchor `Steps`.
The step list, in run order, with per-step **→** and **↺**. Long lists scroll;
crop to the card, not the page.

### `step_output`
`submission_overview` then click the step's label (`--step`). Anchor `Value`.
Right panel: source-document chips, **Search table**, **Export**, **Edit**, then
Field/Value for single-record steps or one row per record for schedules.
Good exemplars are steps with populated values — an all-"Not found" table makes
a poor picture. Rects: `export`, `edit`.

### `citation`
`step_output` then click the exact cell value (`--value`). Anchor `Share`.
The cell takes a blue outline and the source document opens beside it with the
passage highlighted in green.
For print, do **not** use the raw split view — build the two-part figure with
`annotate.py stack` (cell crop on top, highlighted source below).

### `final_summary`
`submission_overview` then the **last** `View Email` (the first one belongs to
Prepare Documents and shows the inbound broker email — a different screen).
Anchor `Share`. Generated files appear as chips above the rendered summary.

### `generated_file`
`final_summary` then click the file chip (`--file`, substring). Anchor `Share`.
Opens the in-app viewer: sheet tabs along the bottom for a workbook, download
icon top right. Frame the tabs — they show the workbook is more than one sheet.

## Not automated yet

Drive these with `goto` / `click` / `shot` and add a recipe once the labels hold
still. Each one mutates something, so treat them as write screens.

| Screen | How to reach it | Caution |
|---|---|---|
| edit a value | `step_output` → **Edit** → click a cell | writes a real edit with edit history; do it on a throwaway draft |
| re-run a step | `submission_overview` → **↺** on a step | re-runs that step and everything downstream; costs money |
| delete a submission | queue row **⋯** → Delete | capture the confirm dialog only, on a draft you created |
| download menu | file chip → download icon | starts a real download into the browser profile |
| document library | sidebar → File Library | shows org-wide documents; check the frame for other customers' files |

## When a recipe breaks

`capture.py` raises rather than capturing the wrong screen — that is deliberate.
A silently wrong screenshot ships into a customer document; a loud failure costs
a minute. When one fires:

1. `capture.py goto --url <route> --out /tmp --name probe` and look at the image.
2. Find the new label. Update the anchor here **and** in `SCREENS` in
   `capture.py`, in one change.
3. If the screen moved rather than being renamed, update the route in both.
