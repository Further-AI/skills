# Document patterns

Measured from `assets/brand/doc_templates/`. Two DOCX templates and one PDF
design reference.

Read this when composing a new document or matching an existing template's
grammar. Palette tokens, fonts, and logos live in `assets/brand/BRAND.md`.

Two grammars, and they are not interchangeable:

- **Client-facing** (`Pricing_Document.docx`) — brand-correct. Fraunces Light
  green titles, Wix Madefor Display body, the borderless hairline table with a
  green header row. Use this for anything a customer sees.
- **Internal** (`Meeting_Brief.docx`) — deliberately plain. Arial, blue headings
  with bottom rules, dense label/value tables. Fast to read, not branded.

## D. DOCX templates

### D.1 `Meeting_Brief.docx` — internal pre-meeting research brief

*For:* a 1-page prep sheet before a sales/discovery call. Internal only, **not brand-styled** (no logo, no Fraunces, no green).

| Aspect | Value |
|---|---|
| Page | 8.5 × 11.0in portrait, margins **0.75in all round**, header dist 0.492in, footer dist 0.492in |
| Header / footer | **empty** |
| `docDefaults` | **Arial**, 10pt (`sz 20`), no default color |
| Styles present | `Title` 28pt; `Heading 1` 16pt `#2E74B5`; `Heading 2` 13pt `#2E74B5`; `Heading 3` 12pt `#1F4D78`; `Heading 4` italic `#2E74B5`; `Heading 5/6`; `Strong` bold; `Hyperlink` `#0563C1`; `footnote text` / `endnote text` 10pt (stock Word set — the doc overrides them inline) |
| Logo | none |

**Section structure, in order:**

| # | Block | Style | Formatting |
|---|---|---|---|
| 1 | Doc title | direct run | Arial **15pt Bold `#1F4E79`**, spaceAfter 2pt — `Meeting Prep — <Contact Name>, <Company>` |
| 2 | Meta line | direct run | Arial **9pt Italic `#555555`**, spaceAfter 8pt — `<Day>, <Month D, YYYY> · <time> · <platform> · <meeting type> (sourced by <name>)` |
| 3 | `Who You're Meeting` | `Heading 2` | 12pt Bold `#1F4E79`, spaceBefore 10 / spaceAfter 5, **bottom border `single #1F4E79 sz=6 space=1`** |
| 4 | Fact table | 4 rows × 2 cols, widths **1.667in / 4.833in** | label cell fill **`#EEF3F8`**, Arial 9.5pt **Bold**; value cell Arial 9.5pt. Rows: `Name / Role`, `Background`, `Read`, `Contact` |
| 5–19 | `The Company` / `Where Things Stand` / `Angle for the Call` | `Heading 2` (same border) + `List Paragraph` bullets | bullets Arial **10pt**, spaceAfter 3pt, 4 per section |
| 20 | Sources line | direct run | Arial **8pt `#555555`** (label run bold), spaceBefore 10pt |

**Grammar:** one bold title, one italic meta line, then alternating `Heading 2` (blue with bottom rule) → either a 2-column label/value table or a 4-bullet list, closed by an 8pt gray sources line.

### D.2 `Pricing_Document.docx` — client-facing pricing summary

*For:* a short, branded, client-facing commercial one-pager. **This is the brand-correct DOCX reference.**

| Aspect | Value |
|---|---|
| Page | 8.5 × 11.0in portrait, margins **L/R 1.0in, T/B 0.9in**, header 0.492in, footer 0.492in |
| Header / footer | **empty** — the logo is the first body paragraph instead |
| `docDefaults` | **Wix Madefor Display**, 11pt (`sz 22`), color **`#2B2D31`** |
| Embedded fonts | `word/fonts/`: WixMadeforDisplay regular+bold, WixMadeforDisplaySemiBold regular+bold, FrauncesLight regular+bold+italic+boldItalic |
| Logo | `word/media/image1.png` inline in P1, **0.646 × 0.646in** (square mark), spaceAfter 12pt |
| Styles | `Normal` (base); `Title` WMD 28pt `#2B2D31`; `Subtitle` Georgia 24pt italic `#666666`; `Heading 1–6` WMD 16/13/12/11/11/11pt `#2E74B5`/`#1F4D78` — **all unused**; the document formats everything with direct runs |

**Section structure, in order:**

| # | Block | Formatting | Verbatim text |
|---|---|---|---|
| 1 | Logo | inline PNG 0.646 × 0.646in, spaceAfter 12pt | — |
| 2 | Doc title | **Fraunces Light 32pt `#074B40`**, spaceAfter 4pt | `Pricing Summary` |
| 3 | Subtitle | WMD 11pt `#595959`, spaceAfter 3pt | `Prepared for <Client>  ·  <Month YYYY>` |
| 4 | Lede ¶ | WMD 11pt `#2B2D31`, spaceAfter 8pt | One paragraph naming the headline figures — a 3–5 sentence plain-language summary of the commercial terms |
| 5 | Body ¶ | WMD 11pt `#2B2D31`, spaceAfter 12pt | A second paragraph putting the figures in context — headroom, what is included, what changes at the ceiling |
| 6 | Section heading | **Fraunces Light 22pt `#074B40`**, spaceBefore 6 / spaceAfter 7pt | `Your Investment` |
| 7 | Pricing table | see below | — |

**Table spec (the reusable brand table style):**

| Property | Value |
|---|---|
| Width / layout | `tblW 9360 dxa` (6.5in), `tblLayout fixed`, `tblLook 0000` (no auto-banding) |
| Column widths | **1.701in / 1.701in / 3.097in** |
| Borders | outer top/left/bottom/right = **`nil`**; `insideH` and `insideV` = `single #000000 sz=4` (0.5pt hairline) — **no outer frame, hairline interior only** |
| Cell margins (`tcMar`) | top 140, left 200, bottom 140, right 120 twips = **0.097 / 0.139 / 0.097 / 0.083in** |
| Header row | fill **`#074B40`**, `Wix Madefor Display SemiBold` 10.5pt **Bold** `#FFFFFF` — `Component` / `Fee` / `What it covers` |
| Body rows | col 1 fill **`#FBFBF9`**, `WMD SemiBold` 10.5pt `#2B2D31`; col 2 WMD 10.5pt `#2B2D31` (no fill); col 3 WMD **10pt `#595959`** (no fill) |
| Total row | all cells fill **`#D9EAD3`**, `WMD SemiBold` 10.5pt **`#074B40`** |

Row shape (three columns: component, fee, what it covers):
`<Component>` / `$X,XXX / month` / `<multi-line description; 
 separates lines>`
`<Component>` / `$X,XXX / month` / `<usage cap and overage terms>`
`Total annual investment` / `$XX,XXX` / `$X,XXX/mo`

**Grammar:** logo → Fraunces Light green title → WMD gray subtitle → 1–2 WMD body paragraphs → Fraunces Light green section heading → borderless table with a green header row, `#FBFBF9` label column, and a `#D9EAD3` total row. Repeat heading+table for further sections.

---

## E. `One_Pager.pdf` — design reference (not editable)

2 pages, MediaBox `0 0 612 792` (**8.5 × 11in US Letter portrait**), produced by `Skia/PDF m149 Google Docs Renderer` (i.e. exported from Google Docs — there is **no editable source in the repo**). Embedded fonts: `Calibri`, `Fraunces-Regular`, `Fraunces-Light`, `Fraunces-LightItalic`, `WixMadeforDisplay-Regular/-Bold/-SemiBold`.

**Page 1 — "AI Transformation Partner for Insurance"**

| Zone | Treatment |
|---|---|
| Masthead | Square black `F` mark + `FurtherAI` wordmark, top-left, ~0.25in tall |
| Title | Fraunces (serif) ~20pt near-black — `AI Transformation Partner for Insurance` |
| Deck line | WMD ~9pt gray — `An insurance-native AI workspace that deploys on top of existing systems - no rip and replace.` |
| Rule | Full-width **dark green** hairline under the deck |
| Intro ¶ | WMD ~9.5pt, 4 lines, full measure |
| `SELECT WORKFLOWS IN PRODUCTION` | WMD ~7.5pt **Bold, letterspaced, all-caps, dark green** eyebrow |
| Workflow cards | **2 × 2 grid, hairline gray borders, no fill**; each = numbered bold title (`1.  Submission Intake & Triage`), 3-line WMD gray body, then a `Case Study` run in **bold dark green** followed by regular metrics text |
| `HOW IT WORKS` | same eyebrow style |
| Pipeline strip | **single-row 7-cell hairline grid**, each cell = bold ~8pt label + ~6.5pt gray sub-label (`Ingest` / `Email · Portal · API` … `Your Systems` / `Guidewire · CRM` in green) |
| `SELECT ENGAGEMENTS` | eyebrow |
| Engagements table | **dark green header band** with white all-caps ~8pt column labels (`CLIENT PROFILE` / `USE CASE` / `IMPACT`), 5 body rows separated by hairline rules, no vertical rules |
| Footer | centered, hairline rule above — `Backed by **<investors>**  |  **$XXM+** raised  |  furtherai.com` (last item green) |

**Page 2 — "Agentic Workspace for Insurance"**

| Zone | Treatment |
|---|---|
| Masthead + title + deck | same recipe, title `Agentic Workspace for Insurance` |
| Architecture panel | Full-width **dark green** block; centered Fraunces italic white title; inside it, alternating bands: tiny letterspaced caps section labels (`OUT OF BOX USE CASES`, `AGENTS AND GUARDRAILS`, `INTELLIGENCE + CONTEXTUAL DATA`), a white 5-cell row, **two full-width `#E08A4F`-orange bars** (`Agentic Workflow Library`, `Agentic Workflows`), and darker-green cells with bold white labels + tiny gray sub-labels |
| `Evaluation Studio` | Fraunces section title + gray deck line |
| Stat band | full-width dark green bar, 3 stats — number ~26pt white, label ~6.5pt light gray (`95%+`, `10x`, `100%`) |
| Feature grid | 2 × 2 hairline cards, bold ~11pt title + WMD gray body |
| `Security & Compliance` | Fraunces section title, then a **4-column `#F7F7F5` band** with bold ~8.5pt titles + tiny gray bodies |
| Footer | identical to page 1 |

**Takeaway for the `doc` skill:** this is the target look for a printed one-pager — hairline-bordered card grids, letterspaced all-caps green eyebrows, full-width dark-green panels/bands for emphasis, an orange accent (`~#E08A4F`) used sparingly inside the architecture diagram only, and a single centered footer credit line. Reproduce it via HTML→PDF (`build_pdf.py`, headless Chrome), not via python-docx.

---
## F. The wider document corpus — blocks beyond the three templates

Sections D–E are measured from `assets/brand/doc_templates/`. This section
catalogues the block patterns found in the wider corpus of real team documents
that D–E do not cover.

`references/doc_types.md` says which of these a given document type uses. This
section says where each came from and what varies.

Source documents (in `plugin_reference_docs/doc/`):

| Document | Pages | Pipeline | Type it anchors |
|---|---|---|---|
| `FurtherAI Pricing Overview.docx` + PDF twin | 1 | Google Docs export | `pricing_overview` |
| `FurtherAI - Broker Workflows Jul 2026.pdf` | 2 | headless Chrome | `capability_onepager` |
| `FurtherAI - API Integration Guide (1).pdf` | 20 | headless Chrome | `api_integration_guide` |

### F.1 Implemented blocks

Registered in `build_pdf.py` / `build_doc.py`; run `--list-blocks` for fields.

**PDF**

| Block | Source | Notes on variation |
|---|---|---|
| `dark_cover` | API Guide p1 | Full-bleed dark green gradient, topic pills, version line, confidentiality stamp. For long technical documents only — a one-pager opens with `masthead` |
| `toc_dotted` | API Guide p2 | Green numerals, dotted rule trailing each row. **No page numbers** in the source; adding them is a change, not a fix |
| `numbered_section_header` | API Guide, all 8 sections | Large numeral, bold heading, hairline rule. Section numbers are cross-referenced in body copy (`See §4`) — renumbering means fixing every reference |
| `quad_grid` | Pricing Overview | Four numbered components, each with a `WHAT YOU GET` sub-label. Green rule on the card's top edge, tan sides, real gutters rather than table borders |
| `highlight_panel` | Pricing Overview, Broker Workflows | Two weights for the same structural job: cream with a green border, or plain muted with none. Headline figure bolded inline |
| `dark_band_columns` | Pricing Overview, Broker Workflows, One_Pager | Full-width dark band closing a section. `green` is standard; `near_black` (`#162C28`) is the darkest tier, used for the trust panel |
| `pill_row` | Broker Workflows, API Guide | Compliance marks and topic chips. Outline on a cover, filled inside a panel |
| `flow_diagram` | Broker Workflows, API Guide, One_Pager | Three visual executions of one semantic pattern: icon nodes on a line, boxed `STEP-N` cards with arrows, and a plain hairline cell strip. Keep the per-node sub-caption — dropping it loses half the content |
| `callout` | API Guide, ~8 instances | **Three variants carry real meaning** — warning, info, success. Collapsing them to one grey box loses which failure mode each note addresses |
| `code_block` | API Guide, every section | JSON snippets and long scripts. Never rebuild one from text extracted out of a PDF; column wrapping corrupts the indentation |
| `method_badge` | API Guide | Coloured HTTP verb pill plus monospace path. No native OOXML equivalent — if this ever needs to be a .docx, a shaded 1×1 table cell is the closest fake |
| `field_table` | API Guide, every section | Parameter reference. Field names as monospace chips, `Required` in `#B53B18` and `Optional` in muted grey |

**DOCX**

| Block | Source | Notes |
|---|---|---|
| `status_table` | scoping-plan corpus | Status lives in the cell fill. Brand table chrome in `client` grammar, Meeting_Brief chrome in `internal` |
| `checklist` | scoping-plan readiness section | Checkbox glyph per row; falls back to bracketed `[ ]` where the brand font lacks the glyph |
| `signoff_table` | scoping-plan §11 | Role / name / date. Blank name and date cells are expected — they get signed by hand |
| `milestone_table` | scoping-plan §8 | Milestone / date / owner / status, status coloured per the `status_table` map |
| `grouped_bullets` | DealerGuard dependency slides, scoping-plan readiness | A bold label line then its bullets, repeated. For week-grouped or category-grouped lists |

### F.2 Catalogued, not yet built

| Block | Source | What it does |
|---|---|---|
| `timeline_bar` | Broker Workflows p2 | Segmented horizontal progress bar — `8 WEEKS TO GO LIVE`. Segment fill darkens toward the near term, so flattening to one colour loses the "where we are" cue |
| `framed_panel` | Broker Workflows p2 | Green-bordered rounded rect containing its own mini pill-and-arrow flow, for calling out one product concept |
| `engagement_proof_table` | One_Pager p1 | Client profile / use case / impact, dark green header band and hairline row rules. The existing `table` block covers most of this |

### F.3 Not carried over

| Seen in | Why not |
|---|---|
| `architecture_stack_diagram` (One_Pager p2) | Nested bands inside a dark panel with two orange `#E8854A` accent bars. The most complex block in the corpus, nothing else resembles it, and it isn't parameterisable. Treat as a one-off |
| A4 page size (API Guide) | The single non-Letter document in the set. Not a brand decision — every generated type is US Letter |

### F.4 Known drift in the reference documents

Do not sample colours or fonts out of these files.

| Document | Drift |
|---|---|
| `FurtherAI Pricing Overview` | Greens are `#084B41` / `#1A6B5A` against canonical `#074B40` / `#25654F`. Visually identical, but copying them propagates the drift. Card border and callout fills are also off-token |
| `One_Pager.pdf` | Same `#084B41` green. Body text uses a generic Google Docs grey ramp (`#444444`, `#555555`, `#777777`) rather than brand `ink` / `muted` / `warm_dark` |
| `Broker Workflows` | Headings are correct Fraunces with brand-token colours, but **body copy fell back to system `.SFNS`** instead of Wix Madefor Display — a webfont-loading gap in the HTML→PDF template |
| `API Integration Guide` | Off-brand typography end to end: Instrument Serif for the cover title, Inter for body, Fira Code for code. Only the green accents loosely track brand. Copy its layout, table and callout mechanics — not its type system |
| `Pricing_Document.docx` | Fully on-brand, but carries run-splitting from a copy-paste edit (`"1,2"` + `"00 submissions annually"`). Any script reading "the number" from a single run gets a truncated value |

---
