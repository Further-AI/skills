---
name: screenshots
description: Captures and annotates screenshots of the FurtherAI web app for artifacts that need actual pictures of the product — a user guide with screenshots, a walkthrough, a deck slide showing a real submission. Use ONLY when the user explicitly asks for screenshots, images, or annotated visuals of the app UI ("with screenshots", "screenshot the platform", "show the UI", "annotated walkthrough", "add screenshots to this deck"). Do NOT use it to read data, check a run's status, inspect step outputs, or answer any question about a workflow — the API skills (wb, triage, workflow, accuracy) are faster and correct for all of that, and this skill drives a headless browser against a live session rather than reading the API.
---

# screenshots — pictures of the FurtherAI app

High bar. Capturing is slow, needs an interactive login the first time, and can
create objects in a real org. Most documents do not
need it: a user guide is a perfectly good written document without a single
image, and **"make a user guide" is not a request for screenshots** — only
"user guide **with screenshots**" is.

## Dependencies

Capture requires Chrome, Chromium, or Microsoft Edge. Cropping and annotation
require Pillow (`python3 -m pip install Pillow`). Platform reads still use the
standard-library API client. See `${CLAUDE_PLUGIN_ROOT}/DEPENDENCIES.md` for the
shared dependency matrix. Do not install software without the user's approval.

## Do not use this skill when

| The user wants | Use instead |
|---|---|
| a run's status, why it failed, its logs | `fai:triage` |
| step outputs, extracted values, table data | `fai:wb` (`table`, `status`) |
| workflow structure, steps, prompts, config | `fai:workflow` |
| accuracy, eval results, ground-truth diffs | `fai:accuracy`, `fai:eval-studio` |
| a document, deck, or one-pager with no images | `fai:doc`, `fai:deck` |
| "show me the submission" (they mean the data) | `fai:wb status` — then paste the app URL |

**The only reason to drive a browser is that the deliverable needs pixels.**
When in doubt, ask: "do you want actual screenshots in this, or is a written
version enough?" One question is cheaper than a capture run.

**Never quote a value read off a screenshot.** Numbers in an artifact come from
the API. This skill returns image paths, not facts.

## What you need before starting

Gather all five, then start. Stopping halfway to ask wastes a login.

1. **Account and environment** — `--account <slug>` resolves the org from
   `~/.fai/accounts.json`; `--env` defaults to `defaults.env`.
2. **Workflow** — UUID, or a name resolved against the account record.
3. **An exemplar submission** — a real completed run to photograph.
   `capture.py pick-run` chooses one (newest `completed`, real title).
4. **Which screens** — from the catalog below. Fewer is better; every screen is
   a paragraph someone has to read.
5. **Whether writes are allowed** — the upload screens create a draft
   submission in the org. Off unless the user says yes, and cleaned up after.

The session lives inside the headless browser, which stays running between
commands. `capture.py close` ends it and costs a new sign-in.

## Run it

**Nothing ever appears on screen.** The browser is headless, start to finish.
Sign-in works because the headless browser requests its own magic link and then
opens it itself.

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/screenshots/scripts"

# 0. sign in — headless, two steps, no window at any point
python3 "$S/capture.py" login --email you@furtherai.com --account andrew-sandbox
#   -> check your email, copy the sign-in link, then:
python3 "$S/capture.py" login-open --url '<paste the link>'

# 1. pick a submission worth photographing
python3 "$S/capture.py" pick-run --account andrew-sandbox \
    --workflow "Novacore Submission Intake"

# 2. capture screens (each appends to shots/manifest.json)
python3 "$S/capture.py" shoot --screen workflow_runs --out shots/ \
    --account andrew-sandbox --workflow "Novacore Submission Intake"
python3 "$S/capture.py" shoot --screen step_output --out shots/ \
    --account andrew-sandbox --execution 6a86... --step "Extracted Insured Data"

# 3. crop / annotate from the boxes capture.py recorded
python3 "$S/annotate.py" annotate shots/workflow_runs.png --out fig/queue.png \
    --from-rects shots/manifest.json --box-rect add_new --crop-rect content

# 4. shut the headless browser down when the artifact is built
python3 "$S/capture.py" close
```

**The link must be opened by the browser that asked for it.** The request leaves
state behind, and — more importantly — replaying a session into a *second*
browser logs the first one out, because PropelAuth rotates its refresh token on
use. `login-open` therefore drives the same headless browser that ran `login`.

**A fresh sign-in lands in whatever workspace the account defaults to**, which
is usually the wrong one ("This workflow isn't available in this workspace").
`--account` fixes it: the driver sets `active_org_id` before navigating.

### Rules that were paid for in broken sessions

| Never | Why |
|---|---|
| Copy a session between browsers | Rotating refresh token — the source gets logged out |
| Reuse a Chrome profile across launches | A Claude-launched Chrome has no Keychain access and cannot decrypt its own cookies |
| `--password-store=basic` | Chrome writes cookies with a key it cannot recover next launch |
| Minimise a headed window | macOS freezes the renderer: evaluation times out, pages stop rendering |
| `Page.bringToFront` | Steals focus. Only needed for a background *tab* — keep one tab per window |
| Assume `fromSurface` is fixed | Headless needs `true`; a headed occluded window needs `false`. Backwards gives a blank image, not an error |

If a headless session is ever impossible, `capture.py session` still exists: it
signs in through a visible window and leaves it parked, never raised. Treat it
as the fallback, not the path.

`capture.py screens` lists the catalog. `references/screens.md` has each
screen's route, anchor text, geometry, and the ones that are not automated yet.

## Screens that write to the org

`new_submission` and `staged_files` click **Add New**, which creates a draft
submission. They refuse to run without `--allow-write`.

- Never click **Confirm Details & Submit**. Submitting starts a real run that
  costs money and minutes. Screenshot the staged state and stop.
- Delete the draft as soon as the shots are taken:
  `capture.py delete-draft --execution <draft-id>` (it refuses anything that
  isn't an unstarted draft).
- Prefer a sandbox or demo org. Ask before creating anything in a customer org.

## Annotation

Sparingly. At most one annotation per screenshot, and on a minority of
screenshots — a page where everything is circled reads as noise.

- `--box-rect <name>` uses the element box recorded at capture time. Prefer it
  over hand-measured `--box X,Y,W,H`; hand-measuring is where the iterations go.
- `stack` builds the two-part figure: a value cell above, its highlighted source
  below, joined by an arrow. Use it for citations — a single wide screenshot of
  the split view renders at ~4pt and is unreadable in print.
- Accent colour is sinopia `#b53b18`, already the platform's attention colour.

## Legibility — the rule that decides your crops

At a 5in column, a crop wider than about **1900 source pixels** puts UI text
under 4pt. Crop to the panel that matters, not the whole window. `annotate.py`
warns when an output exceeds that.

## Handoff to doc and deck

Everything downstream reads `shots/manifest.json`:

```json
{"screens": [{"name": "workflow_runs", "screen": "workflow_runs",
              "image": "workflow_runs.png", "url": "https://app.furtherai.com/workflows/...",
              "anchor": "Add New", "anchor_found": true,
              "viewport": {"w": 1640, "h": 862, "dpr": 2},
              "rects": {"add_new": [2969, 95, 221, 71], "_dpr": 2}}]}
```

`rects` are image pixels (CSS px x dpr), ready to hand to `annotate.py`.

## Failure modes

| Symptom | Cause and fix |
|---|---|
| `anchor text 'X' never appeared` | The screen did not load, or the UI renamed the control. Do **not** capture anyway — a wrong screenshot ships silently. Check `references/screens.md`, fix the recipe. |
| Capture hangs, then times out | A visible background tab never answers `captureScreenshot`. Headless has no such problem — one more reason it is the only supported mode. |
| `no signed-in browser` | The headless browser was closed or its session expired. Run `login` + `login-open` again. |
| Captures show the wrong workspace | `active_org_id` was not set — pass `--account` (or `--org-id`). |
| Image is a flat dark rectangle | `fromSurface` was false in headless. It must be true there. |
| A window took focus mid-capture | Something called `Page.bringToFront`, or a headed fallback session is in play. Headless never shows a window. |
| `Failed to create a ProcessSingleton` | Stale `SingletonLock` in the profile from a crashed run; the driver clears it when no live process owns the profile. |
| Session gone after the browser closes | Expected: a Claude-launched Chrome cannot re-read its own encrypted cookies. Sign in again; there is no way to persist it. |
| Click does nothing / "matched nothing" | The app layers panels over rows. Navigate back to the base URL first — the catalog recipes do this — rather than clicking through a stacked overlay. |
| Black band down the right of the image | Device metrics were overridden wider than the window. Size the window (`--window W H`); don't override metrics. |
| Screenshot text unreadable in the PDF | Crop is too wide. See the legibility rule. |

## Don't

- Don't open the browser to answer a question. Pixels only.
- Don't screenshot another customer's data, and **crop the left sidebar out** —
  it lists every workflow in the org, including other clients' names.
- Don't submit a run to get a screenshot.
- Don't leave a draft submission behind.
- Don't `pkill` Chrome by name — that kills the user's own browser. The driver
  only ever signals the PID it launched or a process owning its profile.
- Don't navigate anywhere outside `*.furtherai.com`; the driver refuses.
