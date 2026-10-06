---
name: doc
description: Creates and edits FurtherAI-branded Word documents (.docx) and print-quality PDFs from a library of nine defined document types, or by following an existing template, or by composing one from the brand document patterns. Use when the user says "create a document", "write a doc", "update this document", "make a PDF", "create a DOCX", "write a report", "meeting brief", or "one-pager", or asks for a customer pricing summary or quote, a pricing overview explaining how FurtherAI prices, a capability or segment one-pager, a workflow one-pager, an API integration guide, a screenshot walkthrough or user guide with screenshots of a workflow, a client debrief or meeting recap note, or an implementation scoping plan. Runs the copy-review skill over the copy before building so the text matches the FurtherAI Writing Style Guide.
---

# doc — FurtherAI documents and PDFs

**Start with the type.** Nine document types are specified in
`references/doc_types.md`, and each one already pins its grammar, its output
format, and its section order — which settles all three decisions below at once.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/doc/scripts/build_doc.py" --list-types   # .docx types
python3 "${CLAUDE_PLUGIN_ROOT}/skills/doc/scripts/build_pdf.py" --list-types   # PDF types
```

| Type | Format | Grammar | For |
|---|---|---|---|
| `pricing_summary` | .docx | client | one customer's commercial terms, with figures |
| `pricing_overview` | PDF | client | how FurtherAI prices, in the abstract |
| `capability_onepager` | PDF | client | what we do, for a segment or in general |
| `workflow_onepager` | PDF | client | one page on one workflow |
| `api_integration_guide` | PDF | client-technical | how a customer's engineer integrates |
| `screenshot_walkthrough` | PDF | client | driving one workflow, screen by screen |
| `meeting_brief` | .docx | internal | prep sheet before a call |
| `client_debrief_note` | .docx / md | internal | post-meeting takeaways and actions |
| `implementation_scoping_plan` | .docx | internal | what an implementation covers |

Build one with:

```bash
python3 .../build_doc.py --type pricing_summary --scaffold > spec.json
# fill in the copy, delete the conditional slots that don't apply
python3 .../build_doc.py spec.json --check --type pricing_summary
python3 .../build_doc.py spec.json --out doc.docx
```

**The section plan is the contract.** Fill in content; don't reorder, don't add a
block the contract doesn't list, and drop conditional slots that don't apply
rather than filling them with placeholders — a section holding a placeholder row
is worse than no section. `--check --type` enforces it.

`pricing_summary` and `pricing_overview` are the pair most often confused. If a
customer name or a dollar figure belongs in it, it's `pricing_summary`.

If no type fits, fall through to the two decisions below and say which type you
ruled out.

## The completeness gate

Before building anything with numbers or commitments in it, resolve the unknowns.
Draft the copy in the conversation, then present both:

1. the full drafted content, and
2. a labelled list of every field you are not confident about.

Loop until each is resolved. An explicit "TBD" from the user is a valid,
publishable value — but it has to come from them. Never backfill a field they
called TBD without asking, and never ship a literal `[PLACEHOLDER]`.

Inventing a figure to complete a document is the worst failure mode this skill
has. It matters most on `pricing_summary`, `pricing_overview` and
`implementation_scoping_plan`.

## Which grammar

| Audience | Grammar | Reference template |
|---|---|---|
| Customer, prospect, counterparty, partner | **Client-facing** — brand-correct | `Pricing_Document.docx` |
| Team, ops, internal prep, research | **Internal** — plain and fast to read | `Meeting_Brief.docx` |

Client-facing means Fraunces Light green titles, Wix Madefor Display body, and
the borderless hairline table with a green header row. Internal means Arial,
blue headings with bottom rules, and dense label/value tables — deliberately not
branded, because it's a working document.

If the audience isn't clear from the request, ask once.

## Which format

| Output | When | Path |
|---|---|---|
| `.docx` | The user will edit it, or it's a contract/proposal/brief | `build_doc.py` (python-docx) |
| PDF | A designed, printed, or emailed handout — a one-pager | `build_pdf.py` (HTML → headless Chrome) |
| Both | They asked for both | build the .docx, then export |

A designed one-pager is **not** a .docx. The reference one-pager in
`assets/brand/doc_templates/One_Pager.pdf` uses hairline card grids, full-width
green panels, and stat bands that python-docx cannot produce cleanly. Build that
look in HTML and print it.

`.docx` → PDF export needs LibreOffice or Word; the helper reports honestly when
neither is present rather than producing nothing. The .docx is the deliverable
in that case.

## Screenshots — only when the user asks for them

**Default: no screenshots.** A user guide, a walkthrough, an onboarding doc are
all complete as written documents. Prose describing where a control lives beats
a picture of it for most readers, and costs nothing to keep current.

Reach for `fai:screenshots` only when the request says so outright: "with
screenshots", "annotated", "show the screens", "include images of the UI".
**"Make a user guide" is not such a request.** If a document looks like it would
benefit from images and the user hasn't asked, ask once before capturing — it
drives a headless browser and needs a sign-in link from their email the first
time.

When they do ask, that skill owns the entire capture path — session, screen
catalog, annotation, and the `manifest.json` it hands back. Never drive a
browser from this skill.

A document that is *mostly* screenshots is its own type: `screenshot_walkthrough`
pairs a numbered caption in a 30% left column with the screenshot in the right
one, three rows to a sheet. Reach for it when the request is a walkthrough of
the screens rather than a document that happens to carry a figure; a document
with one or two supporting images just uses the `figure` block.

Facts in the document still come from the API (`fai:wb`, `fai:workflow`), never
from reading a value off a screenshot.

## Dependencies and setup

DOCX creation and inspection require `python-docx`. Designed PDF output requires
Chrome, Chromium, or Microsoft Edge. DOCX-to-PDF conversion requires
LibreOffice or Word. Pillow improves logo cropping. See
`${CLAUDE_PLUGIN_ROOT}/DEPENDENCIES.md` for the full matrix and fallbacks.

```bash
python3 -m pip install python-docx
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/fonts.py" check
```

Install missing brand fonts only after the user approves the machine-level
change:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/fonts.py" install
```

**Read `assets/brand/BRAND.md` before setting a font name** — the family names
the templates reference do not resolve, and bare `Fraunces` gives you Black.
Generated documents use `Fraunces 72pt Light`, `Fraunces 72pt`, and
`Wix Madefor Display`.

## Follow a template

1. Inspect it — never guess at its structure:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/doc/scripts/inspect_docx.py" <file.docx> [--text-only]
   ```
   Page setup, every paragraph with its style and formatting, tables as grids
   with cell fills and widths, images, headers and footers.
2. Work on a copy. Replace content in place and keep the template's own
   conventions, even where they differ from the brand defaults — an approved
   document is the standard for its own kind.
3. For structural changes, rebuild with `doc_kit` helpers rather than patching
   XML by hand.

## Compose a new document

Only when no type in `references/doc_types.md` fits. Name the type you ruled out.

1. Draft the copy first. `references/document_patterns.md` has the section
   grammar for both templates and the exact brand table spec.
2. Run **copy-review** on the draft (below).
3. Write a block spec and build:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/doc/scripts/build_doc.py" --list-blocks
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/doc/scripts/build_doc.py" spec.json --check
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/doc/scripts/build_doc.py" spec.json --out doc.docx
   ```
   For a PDF one-pager, the same shape through `build_pdf.py`.
4. Re-open the result with `inspect_docx.py` and check it against what you
   intended. Table borders and cell fills are the usual casualties.

The client-facing grammar, in order: logo → Fraunces Light green title → gray
subtitle → one or two body paragraphs → green section heading → table. Repeat
heading + table for further sections.

## Copy review is not optional

Before building — while the copy is still text — hand it to the `copy-review`
skill with artifact type **doc**. It returns revised copy, not a findings table.
Blocking findings get fixed, not reported.

The rules that bite hardest in documents: `FurtherAI` as one word, Oxford
commas, sentence case for subheadings and Title Case for the document title,
`March 26, 2026` dates with an en dash for ranges, symbols with digits
(`40%`, `$25M`), numbers under ten spelled out, and no AIisms. Do not let copy
review rewrite legal language, quoted figures, or contract terms for flow —
accuracy wins.

## Workflow one-pagers

For a one-page overview of a workflow, gather the facts rather than
reconstructing them:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/doc/scripts/workflow_facts.py" \
  --account <slug> --workflow-id <uuid|name> --out facts.json
```

Steps bucketed by category with colors, integrations, configuration highlights,
30-day submission activity, version and publish date, and the app URL. Works
offline against a local `workflow.json` too.

Editorial rules that make these read well:

- **Skip empty sections entirely.** A section with a placeholder row is worse
  than no section.
- Cap the step list and say `+N more steps` rather than running to three pages.
- Raw IDs go on one small monospace line, never in body copy.
- Truncate the description at a word boundary.
- Use the UI's words: executions are Submissions, success is `completed`.

## Failure modes

| Symptom | Cause and fix |
|---|---|
| Fonts revert to Calibri in Word | `run.font.name` alone doesn't stick. `doc_kit.set_font` sets `w:rFonts` ascii/hAnsi/cs — use it for every run |
| The document looks right for you, wrong for the recipient | They don't have the fonts. Embed them (`doc_kit.embed_fonts`) — the brand pricing template does exactly this |
| Fraunces renders very heavy | Bare `Fraunces` is the variable font's Black default. Use `Fraunces 72pt Light`, or pin `font-variation-settings` in CSS |
| Table has an outer frame you didn't want | The brand table has **nil outer borders** and hairline interiors only. Don't fall back to a built-in Word table style |
| PDF has no color fills | Add `print-color-adjust: exact` to the print stylesheet |
| `print_to_pdf` returns nothing | No Chrome on the machine. Ship the HTML and tell the user to print it — the script does not fail silently |

## Don't

- Don't skip copy-review, and don't run it after the document is built.
- Don't use the internal grammar for anything a customer sees, or the branded
  grammar for a scratch working doc.
- Don't invent commercial terms, dates, figures, or legal language. If a number
  isn't in the conversation or the source data, ask for it. Run the completeness
  gate rather than guessing.
- **Don't sample a colour or font out of a reference document.** Two of them
  carry drifted greens (`#084B41`, `#1A6B5A`) against canonical `#074B40` /
  `#25654F`, and the API guide ships Instrument Serif and Inter where Fraunces
  and Wix Madefor Display belong. Take structure from them; take every token from
  `BRAND.md`.
- Don't rebuild a code sample from text extracted out of a PDF. Column wrapping
  corrupts indentation, and the result won't compile.
- Don't carry one customer's content into another customer's document.
- Don't add product screenshots to a document that didn't ask for them, and
  don't open a browser from this skill — that's `fai:screenshots`, and only on
  an explicit request.
- Don't create a README or cover note alongside the file.
- Don't send the document anywhere without the user asking. Produce the file and
  hand over the path.
