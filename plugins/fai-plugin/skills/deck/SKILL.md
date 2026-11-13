---
name: deck
description: Creates FurtherAI-branded PowerPoint decks (.pptx) from a library of eight defined deck types, or by following an existing template deck, or by composing one from the brand slide-pattern library. Use when the user says "create a deck", "make a powerpoint", "build a pptx", "update this deck", "put together slides", "a deck for this customer meeting", or asks for an implementation kickoff, a check-in or status deck, a build-scoping deck, a partnership or channel deck, a single-workflow solution deck, the enterprise or platform pitch, an internal enablement deck, or a recent-updates or account-review deck. Runs the copy-review skill over the copy before building so the text matches the FurtherAI Writing Style Guide.
---

# deck — FurtherAI PowerPoint

Three ways to build. Pick before you write anything.

| The user wants… | Mode | Start from |
|---|---|---|
| A kickoff, check-in, scoping, partnership, solution, enterprise, enablement, or account-update deck | **Type** — the default | `references/deck_types.md` |
| "like our enterprise deck", "same format as X", "update this deck" | **Template** | their file, or one in `assets/brand/pptx_templates/` |
| A deck no type covers | **Compose** | the pattern library |

**Start by checking whether a type fits** — most requests are one of the eight,
and a type gives the deck a structure that the next person will reproduce.
Reach for Compose only when nothing fits, and say which type you ruled out.

When it's genuinely unclear, ask once — the paths diverge immediately and redoing
one as another means starting over.

## Mode T — build a known deck type

The eight types, what they're for, and their slide plans live in
`references/deck_types.md`. Read it before building.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/build_deck.py" --list-types
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/build_deck.py" \
  --type implementation_kickoff --scaffold > spec.json
# fill in the copy, delete the conditional slots that don't apply
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/build_deck.py" \
  spec.json --check --type implementation_kickoff
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/build_deck.py" spec.json --out deck.pptx
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/lint_deck.py" deck.pptx \
  --type implementation_kickoff
```

| Type | For |
|---|---|
| `implementation_kickoff` | starting a new implementation — scope, plan, owners |
| `implementation_checkin` | recurring sync during an active build |
| `build_scoping` | agreeing exactly what a workflow build covers, step by step |
| `partnership_discussion` | channel, reseller, or SI partnership pitch |
| `workflow_solution` | one workflow to one prospect |
| `enterprise_capabilities` | full platform pitch to a new logo |
| `internal_process_enablement` | teaching the internal team a process |
| `account_update` | what changed for this account recently |

**The slide plan is the contract.** Fill in content; don't reorder slides, don't
add one the contract doesn't list, and drop conditional slots that don't apply
rather than filling them with placeholders. `--check --type` enforces this, and a
non-zero exit is a build failure.

If the deck genuinely needs a slide no type covers, that means the contract is
incomplete. Say so and propose the addition — don't quietly add a one-off nobody
else will reproduce.

## Dependencies and setup

PowerPoint creation, inspection, and linting require `python-pptx`. Full-deck
rendering requires LibreOffice plus a PDF-to-image tool. Pillow improves logo
handling, and `fonttools` is needed only for optional font alias generation.
See `${CLAUDE_PLUGIN_ROOT}/DEPENDENCIES.md` for the full matrix and fallbacks.

```bash
python3 -m pip install python-pptx
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/fonts.py" check
```

Non-zero exit means a brand font is missing. Fix it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/fonts.py" install
```

Installing fonts changes the current user's font directory. Ask before running
the install command.

**Read `assets/brand/BRAND.md` before setting any font name.** The templates
reference `Fraunces Light` and `Fraunces 9pt`, neither of which resolves, and
bare `Fraunces` resolves to Black. Generated decks use `Fraunces 72pt Light`,
`Fraunces 72pt`, and `Wix Madefor Display`.

## Mode A — follow a template

1. **Inspect it.** Never guess at a template's structure:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/inspect_pptx.py" <file.pptx> [--slide N] [--text-only]
   ```
   You get every shape with its position, size, fill, and run-level fonts.
2. **Map content to slides.** Say which of their slides receives which content,
   and which slides get dropped or duplicated. Show the user that mapping before
   you build if the deck is more than a few slides.
3. **Copy first, then edit.** Work on a copy of their file. Replace text in
   place; do not rebuild a slide from scratch unless the layout genuinely
   changes — you will lose styling that the inspector doesn't surface.
4. **Match the template's own conventions**, even where they differ from the
   brand defaults. A deck the user already approved is the standard for that
   deck.

## Mode B — compose a new deck

Only when no type in `references/deck_types.md` fits. Name the type you ruled out
and why, so the next person can tell whether the library needs extending.

1. **Pick patterns.** Start here — it prints every pattern with its required
   and optional fields, which is all you need for an ordinary deck:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/build_deck.py" --list-patterns
   ```
   Choose by what the content *is*, not by what looks nice.

   Read `references/slide_patterns.md` **only when** `--list-patterns` doesn't
   settle it — you need a pattern's exact geometry to place something custom,
   you're deciding between two similar layouts and want the "use when" text, or
   you're reproducing a template slide by hand. It's a long geometry reference;
   don't load it to build a normal deck.
2. **Draft the copy.** Text first, layout second. A deck whose copy was written
   to fill a layout reads like it.
3. **Run copy-review** (see below).
4. **Write the deck spec** — JSON, one entry per slide:
   ```json
   {"canvas": [13.333, 7.5], "theme": "brand",
    "slides": [{"pattern": "cover", "headline": "...", "footer": "..."},
               {"pattern": "before_after", "from_title": "...", "to_title": "...", ...}]}
   ```
5. **Validate, then build:**
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/build_deck.py" spec.json --check
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/build_deck.py" spec.json --out deck.pptx
   ```
6. **Lint it — always:**
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/lint_deck.py" deck.pptx
   ```
   Off-canvas shapes, text-overflow risk, inherited theme fonts and colors,
   WCAG contrast, inherited shadows, and font families that don't resolve.
   Exits 1 on any error, so treat a non-zero exit as a build failure. This
   catches the class of defect you cannot see — a footer color chosen for a
   light slide reused on a dark one reads fine in code and is invisible on
   the page.

7. **Look at it:**
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/render_preview.py" deck.pptx --out preview/
   ```
   Read the PNGs. Crowding and rhythm are things only a picture shows.

   **Without LibreOffice this renders slide 1 only.** The script prints a
   banner naming the unchecked slides and exits 3. Never report a deck as
   previewed, checked, or verified on the strength of one image — say which
   slides you actually saw, and lean on the lint for the rest.

## Product screenshots — only when the user asks for them

**Default: no screenshots.** Brand slide patterns carry a deck on their own, and
a UI screenshot shrunk onto a slide is usually unreadable anyway.

Reach for `fai:screenshots` only on an explicit ask — "with screenshots", "show
the actual product", "add a screen of the submission". A deck about a workflow
is not, by itself, such a request. If images seem right and the user hasn't
asked, ask once: capturing opens a Chrome window on their machine and may need
an interactive login.

When they do ask, that skill owns capture and annotation and hands back
`shots/manifest.json`; place the resulting PNGs with the deck's own image
patterns. Never drive a browser from this skill, and never read a figure off a
screenshot — numbers come from the API (`fai:wb`, `fai:accuracy`).

## Copy review is not optional

Before building — while the copy is still text — hand it to the `copy-review`
skill with artifact type **deck**. It returns revised copy, not a findings
table. Blocking findings get fixed, not reported.

Reviewing a finished .pptx means rebuilding it, so the order matters. The rules
that bite most on slides: no AIisms ("It's not just X, it's Y", "Here's the
thing"), `FurtherAI` as one word, no exclamation marks, spaced em dashes,
symbols with digits (`40%`, `$25M`), spelled-out numbers under ten. Blog rules
about word counts and paragraph length do not apply to slides.

## Composing a good deck

- **Canvas is 13.333 × 7.5in.** Many reference decks are 10 × 5.625in — that's a
  Google Slides export artifact, not a brand choice. Scale by 0.75 when reading
  geometry off those, and never inherit the smaller canvas into something new.
- **One status convention per deck.** The reference decks solved status four
  different ways (colored table cell, gantt bar, swimlane row fill, inline
  bracket tag). Pick one — cell fill, via `status_table` — and hold it deck-wide.
- **One idea per slide.** If a slide needs two headlines, it's two slides.
- **Five bullets maximum.** Past that, the pattern is wrong — try a card grid,
  a matrix, or a table.
- **Lead with the answer.** The headline states the conclusion; the body
  supports it. A headline that only names the topic ("Timeline") wastes the
  most-read line on the slide.
- **Next steps always gets its own slide**, immediately after any timeline.
  Never fold it into the timeline.
- **No raw IDs in slide bodies.** Workflow IDs, execution IDs, and version
  numbers belong in a footer or the speaker notes.
- **Use the UI's words**: executions are Submissions, success is `completed`
  (never "succeeded"), versions are "Draft vN" and "Live".
- Numbers get thousands separators; percentages get one decimal at most.
- Cream `#FBFBF9`, not pure white, for light slide backgrounds.
- Don't bold Fraunces Light — change the size instead.
- No emoji unless the user asks.

## Account update decks

For a "what changed recently" or account-review deck, gather the real numbers
first rather than asking the user to recall them:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/gather_updates.py" \
  --account <slug> [--workflow-id <uuid|name>]... [--since-days 30] --out updates.json
```

Published versions and submission stats per workflow over the window, straight
from the platform.

Then ask the user **one** question covering wins, blockers, and next steps —
and only when the conversation hasn't already answered it. A deck built purely
from counts is hollow; a four-question interview is worse. Rewrite publish notes
into what changed *for the customer*, not what changed in the config.

Where a workflow shipped nothing in the window, the slide says so plainly. Where
a workflow errored during gathering, name it honestly or drop it. Never invent a
number to fill a slot.

## Failure modes

| Symptom | Cause and fix |
|---|---|
| Text renders in Arial or Times | The font name doesn't resolve. Run `fonts.py check`; use the `BRAND.md` family names, not the template's |
| Fraunces renders extremely heavy | You used bare `Fraunces` — that's the variable font's Black default. Use `Fraunces 72pt` |
| Colors look like stock Office blue | A run or shape inherited a theme color. Every run and shape needs an explicit hex |
| The logo is tiny and off-center | `Logo_Full_Green_on_White.jpg` is 57% padding. Crop to its content bbox, or use the transparent SVG |
| Everything has a drop shadow | python-pptx inherits one by default. Set `shape.shadow.inherit = False` |
| `build_deck.py --check` fails on an unknown pattern | Check `--list-patterns`; the name is wrong or the pattern doesn't exist yet |
| `lint_deck.py` reports a contrast error | Text is using a light-background color on a dark slide (or vice versa). Footers are `#5B6770` on light, `#EEEEEE` on dark |
| `render_preview.py` exits 3 | Partial render — only slide 1 was rasterized. Install LibreOffice for the full deck, or say plainly which slides went unchecked |

## Don't

- Don't skip copy-review, and don't run it after the deck is built.
- Don't rely on the template theme for fonts or colors — every brand deck here
  ships the stock Office theme, so inheritance gives you Arial and Office blue.
- Don't copy a client-themed deck's palette into a FurtherAI deck.
  `assets/brand/pptx_templates/` contains a client-branded deck: it is a
  **structure** reference, and its navy/teal palette and Georgia/Calibri fonts
  are not the brand.
- **Don't sample a colour or font out of any reference deck.** Several are
  off-brand in ways that look fine on screen: Georgia and Nunito where Fraunces
  belongs, and a green mistyped as `#25644F` for `#25654F`. Take structure from
  them; take every token from `BRAND.md`.
- Don't put customer-confidential content from one account's template into
  another account's deck.
- Don't add product screenshots to a deck that didn't ask for them, and don't
  open a browser from this skill — that's `fai:screenshots`, on explicit request
  only. When you do use them, crop the app sidebar out: it names other clients'
  workflows.
- Don't send the deck anywhere — Slack, email, a shared drive — without the user
  asking. Produce the file and hand over the path.
