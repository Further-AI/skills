# Document types

The **type contract** layer. `document_patterns.md` tells you how to build a
block; this file tells you which blocks a given kind of document is made of, and
in what order.

Read this before composing any document. Two people asking for the same document
type must get the same structure — that only works if the block plan is followed
as written rather than re-derived per document.

## How to use a type contract

```bash
python3 build_doc.py --list-types                       # docx types
python3 build_pdf.py --list-types                       # PDF types
python3 build_doc.py --type pricing_summary --scaffold > spec.json
python3 build_doc.py spec.json --check --type pricing_summary
python3 build_doc.py spec.json --out doc.docx
```

`--scaffold` emits the type's blocks in the type's order with empty content.
Fill in copy. **Do not reorder, and do not insert a block the contract doesn't
list.** `--check --type` fails on a missing required block, a block out of
order, or an unsanctioned block.

## Slot vocabulary

| Column | Meaning |
|---|---|
| **Slot** | stable name for the block's job in the document |
| **Block** | the `build_doc.py` / `build_pdf.py` block that renders it |
| **Req** | `yes` = always. `cond` = only when the include-rule fires. `rep` = once per item in the source data |

A `cond` slot that doesn't fire is **dropped entirely — heading included**. A
section holding a placeholder row is worse than no section.

## The completeness gate

Before building any document, resolve every unknown. Draft the copy in the
conversation, then present two things:

1. the full drafted content, and
2. a labelled list of every field you are not confident about.

Loop until each item is resolved. An explicit "TBD" from the user is a valid,
publishable value — but it has to come from them. Never backfill a field from
another source after they've called it TBD without asking, and never ship a
literal `[PLACEHOLDER]`.

This matters most on the types that carry numbers and commitments —
`pricing_summary`, `pricing_overview`, `implementation_scoping_plan`. Inventing
a figure to complete a document is the worst failure mode this skill has.

## Grammar and format per type

Two grammars, not interchangeable:

- **client** — brand-correct. Fraunces Light green titles, Wix Madefor Display
  body, hairline table with a green header row.
- **internal** — deliberately plain. Arial, blue headings with bottom rules,
  dense label/value tables. A working document, not a branded one.

Format follows the artifact, not the preference: `.docx` when the recipient will
edit it or it's a contract-shaped document; **designed PDF** when it's a
handout, and specifically whenever the layout needs hairline card grids,
full-width colour panels, or stat bands — python-docx cannot produce those
cleanly.

**Page size is US Letter (8.5 × 11in) for every type.** The reference API guide
is A4; that's the one outlier in the corpus and not a brand decision.

---

## pricing_summary

*Reference:* `assets/brand/doc_templates/Pricing_Document.docx`
*Purpose:* a specific customer's commercial terms — named client, real figures.
*Audience:* client-facing, at quote stage. *Format:* `.docx`, `client` grammar.
*Length:* 1 page.

| # | Slot | Block | Req | Include when |
|---|---|---|---|---|
| 1 | `logo` | `logo` | yes | always — square mark, 0.646in |
| 2 | `title` | `title` | yes | always |
| 3 | `prepared_for` | `subtitle` | yes | always — `Prepared for <Client>  ·  <Month YYYY>` |
| 4 | `lede` | `body` | yes | always — 3–5 sentences naming the headline figures |
| 5 | `context` | `body` | yes | always — headroom, what's included, what changes at the ceiling |
| 6 | `section` | `heading` | yes | always — e.g. `Your Investment` |
| 7 | `fees` | `table` | yes | always — Component / Fee / What it covers, `total_row: true` |
| 8 | `section` + `table` | `heading`, `table` | rep | once per further commercial section |

**Content rules**

- Every figure comes from the conversation or the source data. If a number isn't
  there, ask — do not interpolate from a similar deal.
- Do not let copy review rewrite commercial terms or legal language for flow.
  Accuracy wins over rhythm here.
- Distinct from `pricing_overview`: this one quotes a customer, that one explains
  the model. Different templates, different grammars. Don't merge them.

---

## pricing_overview

*Reference:* `plugin_reference_docs/doc/FurtherAI Pricing Overview.docx`
*Purpose:* explain the four-part pricing model in the abstract — no customer
name, no figures.
*Audience:* client-facing, early in a deal. A sales-enablement leave-behind.
*Format:* **designed PDF**. *Length:* 1 page.

| # | Slot | Block | Req | Include when |
|---|---|---|---|---|
| 1 | `masthead` | `masthead` | yes | always — logo left, `PRICING OVERVIEW` tag right |
| 2 | `title` | `title` | yes | always — plus an italic dek line |
| 3 | `intro` | `paragraph` | yes | always — one paragraph |
| 4 | `components` | `quad_grid` | yes | always — the four fees, numbered `01`–`04`, each with a `WHAT YOU GET` sub-label |
| 5 | `example` | `highlight_panel` | yes | always — what a typical engagement looks like, headline figure bolded inline |
| 6 | `rationale` | `dark_band_columns` | yes | always — why the model works, 3 columns |
| 7 | `cta` | `paragraph` | yes | always — one centred italic closing line |

**Content rules**

- Margins run tighter than the docx types: L/R 0.5in, T/B 0.2in.
- No customer name and no dollar figures anywhere. The moment a real number
  appears, this became a `pricing_summary`.
- The four components are Platform, Usage, Per-Seat, Implementation, in that
  order. That order is the model; don't re-rank it per audience.

---

## capability_onepager

*References:* `assets/brand/doc_templates/One_Pager.pdf`,
`plugin_reference_docs/doc/FurtherAI - Broker Workflows Jul 2026.pdf`
*Purpose:* what FurtherAI does, for a segment or in general — production
workflows, how it works, proof.
*Audience:* client-facing leave-behind. *Format:* **designed PDF**.
*Length:* 1–2 pages.

| # | Slot | Block | Req | Include when |
|---|---|---|---|---|
| 1 | `masthead` | `masthead` | yes | always |
| 2 | `title` | `title` | yes | always — segment eyebrow, H1, dek, hairline rule |
| 3 | `proof_stats` | `stats` | yes | always — 3 figures |
| 4 | `workflows` | `eyebrow` + `cards` | yes | always — production workflows, 2×N hairline grid |
| 5 | `example_flow` | `flow_diagram` | cond | one workflow is worth walking end to end |
| 6 | `how` | `eyebrow` + `pipeline` | yes | always — the stage strip |
| 7 | `features` | `cards` | cond | the platform capabilities need their own row (Evaluations, Assistant, integrations) |
| 8 | `trust` | `dark_band_columns` + `pill_row` | yes | always — security and governance, compliance pills |
| 9 | `engagements` | `table` | cond | you have named engagements cleared for external use |
| 10 | `cta` | `paragraph` | yes | always |
| 11 | `footer` | `footer` | yes | always — every page |

**Content rules**

- Every workflow card needs a proof line. A card that only describes a
  capability is marketing; a card with a measured result is evidence.
- Segment the whole document or none of it. A broker one-pager whose proof
  points are all carrier engagements doesn't land.
- Named clients only where you know they're cleared. When unsure, use the
  profile (`Top-15 US broker`) instead of the name.
- The One_Pager architecture-stack diagram is a one-off. Don't try to
  reconstruct it as a reusable block.

---

## workflow_onepager

*Source:* `workflow_facts.py`
*Purpose:* one page on one workflow — what it does, how it's configured, how
it's performing.
*Audience:* client-facing or internal. *Format:* **designed PDF**.
*Length:* 1 page.

| # | Slot | Block | Req | Include when |
|---|---|---|---|---|
| 1 | `masthead` | `masthead` | yes | always |
| 2 | `title` | `title` | yes | always — workflow name, truncated description at a word boundary |
| 3 | `activity` | `stats` | cond | there is 30-day submission activity to report |
| 4 | `steps` | `pipeline` | yes | always — steps bucketed by category; cap the list and say `+N more steps` |
| 5 | `config` | `cards` | cond | there are configuration highlights worth surfacing |
| 6 | `integrations` | `pill_row` | cond | the workflow has integrations |
| 7 | `ids` | `paragraph` | yes | always — one small monospace line, never in body copy |
| 8 | `footer` | `footer` | yes | always |

**Content rules**

- Gather with `workflow_facts.py`; don't reconstruct facts from memory. Works
  offline against a local `workflow.json` too.
- Skip empty sections entirely.
- Use the UI's words: executions are Submissions, success is `completed`,
  versions are "Draft vN" and "Live".

---

## api_integration_guide

*Reference:* `plugin_reference_docs/doc/FurtherAI - API Integration Guide (1).pdf`
*Purpose:* teach a customer's engineer to integrate against the
workflow-execution API.
*Audience:* client-facing but technical. *Format:* **designed PDF**.
*Length:* 15–25 pages.

| # | Slot | Block | Req | Include when |
|---|---|---|---|---|
| 1 | `cover` | `dark_cover` | yes | always — title, topic pills, version and date, confidentiality line |
| 2 | `contents` | `toc_dotted` | yes | always |
| 3 | `overview` | `numbered_section_header` + `paragraph` + `flow_diagram` + `table` | yes | always — prereqs, integration flow, base URL, quick reference |
| 4 | `section` | `numbered_section_header` | rep | once per major section |
| 5 | `endpoint` | `method_badge` + `code_block` + `field_table` | rep | once per endpoint documented |
| 6 | `note` | `callout` | rep | wherever a warning, info, or success note belongs |
| 7 | `reference` | `table` | yes | always — status codes, errors, troubleshooting |
| 8 | `example` | `code_block` | yes | always — one complete copy-paste script |
| 9 | `footer` | `footer` | yes | always — confidentiality stamp, no page number |

**Content rules**

- **Callouts carry meaning.** Warning, info and success are three distinct
  variants; collapsing them to one grey box loses which failure mode each note
  addresses.
- **Never rebuild a code sample from extracted PDF text.** The reference guide's
  final script has indentation corrupted by column wrapping — copied literally it
  won't compile. Write code samples from a real, run source.
- Section numbers are cross-referenced in the body (`See §4`). Renumbering a
  section means fixing every reference to it.
- The reference guide's TOC has no page numbers. Adding them is a change, not a
  fix — ask before you do it.
- Correct the reference guide's typography, don't copy it. It ships Instrument
  Serif and Inter; brand is Fraunces and Wix Madefor Display.

---

## screenshot_walkthrough

*Source:* `fai:screenshots` (`shots/manifest.json`) for the images; `fai:wb` and
`fai:workflow` for every fact.
*Purpose:* teach a user to drive one workflow in the app, screen by screen.
*Audience:* client-facing. *Format:* **designed PDF**.
*Length:* 2–4 pages, one row per screen.

| # | Slot | Block | Req | Include when |
|---|---|---|---|---|
| 1 | `masthead` | `masthead` | yes | always — page 1 only |
| 2 | `title` | `title` | yes | always — the workflow's name, with the workspace named in the deck |
| 3 | `intro` | `paragraph` | cond | the reader needs framing the steps cannot carry themselves |
| 4 | `step` | `step_figure` | rep | once per screen, in the order a user meets them |
| 5 | `note` | `callout` | rep | a caveat belongs with the step it qualifies |
| 6 | `footer` | `footer` | yes | always — every page |

Slots 4 and 5 are adjacent `rep` slots, so they check as one repeatable zone: a
walkthrough may cycle step → step → note → step as many times as it needs.

**Content rules**

- The images come from `fai:screenshots`. Never drive a browser from this skill,
  and never read a fact off a screenshot — facts come from the API.
- One row per screen, and fewer screens is better. Every row is a paragraph
  someone has to read.
- Keep `body` to one or two lines. Row height is set by the screenshot, so long
  copy only pushes the next step onto another page.
- Set `width` per row so about three rows fit a sheet: a wide short crop takes
  `100%`, a tall panel `50–65%`. Rows never split across a page.
- Crop the left sidebar out. It lists every workflow in the org, including other
  clients' names.
- `first: true` on the opening row drops its leading hairline.
- Prose belongs in another type. Once the document needs tables of statuses,
  field references, or limits, it is a composed document, not a walkthrough.

---

## meeting_brief

*Reference:* `assets/brand/doc_templates/Meeting_Brief.docx`
*Purpose:* a one-page prep sheet before a sales or discovery call.
*Audience:* internal only. *Format:* `.docx`, `internal` grammar.
*Length:* 1 page.

| # | Slot | Block | Req | Include when |
|---|---|---|---|---|
| 1 | `title` | `internal_title` | yes | always — `Meeting Prep — <Contact>, <Company>` |
| 2 | `meta` | `meta` | yes | always — day, date, time, platform, meeting type, sourced by |
| 3 | `who` | `internal_heading` + `facts` | yes | always — Name/Role, Background, Read, Contact |
| 4 | `company` | `internal_heading` + `bullets` | yes | always — 4 bullets |
| 5 | `status` | `internal_heading` + `bullets` | yes | always — where things stand, 4 bullets |
| 6 | `angle` | `internal_heading` + `bullets` | yes | always — angle for the call, 4 bullets |
| 7 | `sources` | `sources` | yes | always |

**Content rules**

- Four bullets per section. Not three, not seven — the density is the format.
- Deliberately unbranded. Don't add a logo or brand colours to make it look
  nicer; it's a working document and the plainness is the point.
- The `Read` row is a judgment, not a summary: what this person actually cares
  about going into the call.

---

## client_debrief_note

*Reference:* the `client-debrief-note` team skill
*Purpose:* post-meeting summary — takeaways and action items — circulated after
a customer session.
*Audience:* the joint attendee list. *Format:* `.docx` or markdown, `internal`
grammar. *Length:* under 200 words of body.

| # | Slot | Block | Req | Include when |
|---|---|---|---|---|
| 1 | `greeting` | `body` | yes | always — `Hi All –` (em dash, not a comma) |
| 2 | `thanks` | `body` | yes | always — one line thanking them for the session |
| 3 | `takeaways` | `internal_heading` + `bullets` | cond | substantive discussion happened. Up to 4 bullets, bold label + description |
| 4 | `actions` | `internal_heading` + `bullets` | cond | actions were agreed. Org-labelled: `[FurtherAI] …`, `[Client team] …` |
| 5 | `signoff` | `body` | yes | always — one warm contextual line, then `Best,` and the sender |

**Content rules**

- **Never name individuals in the body.** Substitute the organisation:
  "Tori's edits" becomes "[Client team] edits". This is a hard rule, not a
  stylistic preference — these notes get forwarded.
- Maximum 4–5 bullets across both sections combined. A section with nothing in
  it is omitted, heading and all.
- Deadlines appear only if they were said on the call. Never inferred.
- Headings are bold **and** underlined. Bullets, never dashes.
- Sign as whoever is running the skill, falling back to "The FurtherAI Team"
  only when the sender genuinely isn't known.
- This type produces a **file**. Sending it — email, Slack, anywhere — is a
  separate action the user has to ask for.

---

## implementation_scoping_plan

*Reference:* the `implementation-scoping-notion` team skill
*Purpose:* the single source of truth for what an implementation covers — scope,
workflow, data model, systems, plan, acceptance, sign-off.
*Audience:* internal plus customer counterparts. *Format:* `.docx`, `internal`
grammar. *Length:* 4–8 pages.

| # | Slot | Block | Req | Include when |
|---|---|---|---|---|
| 1 | `title` | `internal_title` | yes | `<Customer> — <Program>`, or just the customer when there's no specific LOB |
| 2 | `snapshot` | `internal_heading` + `facts` | yes | always — legal name, program/LOB, engagement type, stage, FurtherAI lead, customer sponsor, counterparts, IT contact, invoicing contact, kickoff date, target go-live, Drive folder, plan link, data-model link, Slack channel, Linear project |
| 3 | `context` | `internal_heading` + `bullets` | yes | always — users and goal, how it works today, volume |
| 4 | `scope` | `internal_heading` + `body` + `status_table` | yes | always — a "Building:" paragraph, then in-scope / out-of-scope |
| 5 | `workflow` | `internal_heading` + `status_table` | yes | always — `# / Step / What happens / Output or hand-off`, rewritten per engagement |
| 6 | `data_model` | `internal_heading` + `body` | cond | a field-level data model exists or is being built |
| 7 | `systems` | `internal_heading` + `status_table` | yes | always — System / Purpose / Read-Write / Owner / Access status |
| 8 | `readiness` | `internal_heading` + `checklist` | yes | always — sample inputs, business rules, templates, reference data, named reviewers |
| 9 | `milestones` | `internal_heading` + `milestone_table` | yes | always |
| 10 | `acceptance` | `internal_heading` + `bullets` | yes | always — accuracy, coverage, outcome, acceptance |
| 11 | `open_items` | `internal_heading` + `status_table` | yes | always — open questions, risks, assumptions, with owner and needed-by date |
| 12 | `signoff` | `internal_heading` + `signoff_table` | yes | always — customer sponsor, FurtherAI lead, EM reviewer; name and date |

**Content rules**

- **Specificity is the deliverable.** Real system names ("Azure Blob",
  "IMS/MGA SOAP API"), real numbers ("150-field superset", "30k submissions/yr",
  "≥95% accuracy"), real owners. Generic filler in this document is worse than a
  blank — a blank prompts a question, filler doesn't.
- Mark unknowns as `Phase 2`, `Deferred`, `Open` or `TBD`. Those are real
  answers. `[PLACEHOLDER]` never ships.
- The completeness gate above is mandatory for this type. It has the most
  TBD-prone fields of anything here.
- The plan and data model usually live in other artifacts. Link them from
  §2 rather than duplicating them — a copy goes stale silently.

---

## Choosing between types

| The user says… | Type |
|---|---|
| "pricing for <customer>", "send them a quote", "commercial terms" | `pricing_summary` |
| "how we price", "explain our pricing model", "pricing leave-behind" | `pricing_overview` |
| "one-pager", "what we do for brokers", "leave-behind", "capabilities" | `capability_onepager` |
| "one-pager on <workflow>", "summarize this workflow" | `workflow_onepager` |
| "API docs", "integration guide", "how do they call us" | `api_integration_guide` |
| "user guide **with screenshots**", "walkthrough", "show them the screens" | `screenshot_walkthrough` |
| "prep me for this call", "meeting brief", "who am I meeting" | `meeting_brief` |
| "debrief note", "recap that meeting", "follow-up summary" | `client_debrief_note` |
| "scoping doc", "what's in scope", "implementation plan document" | `implementation_scoping_plan` |

The pair most often confused is `pricing_summary` and `pricing_overview`. If a
customer name or a dollar figure belongs in it, it's `pricing_summary`.

Ask once when it's genuinely unclear — the grammars and output formats diverge
immediately.
