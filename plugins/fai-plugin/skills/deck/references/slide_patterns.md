# Slide patterns

Measured from `assets/brand/pptx_templates/`. Every coordinate is inches on the
13.333 x 7.5in canvas unless stated otherwise.

Read this to pick the right layout for a piece of content, and to get the
geometry right when a composite in `deck_kit.py` doesn't cover what you need.
For the parameters each composite takes, run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deck/scripts/build_deck.py" --list-patterns
```

Palette tokens, fonts, logos, and slide chrome live in
`assets/brand/BRAND.md` — read that first.

## B. `Example_Slides.pptx` — slide-by-slide catalog (PRIMARY reference)

Every slide is a hand-built shape composition; the layouts (`DEFAULT`, `BLANK`, `TITLE_AND_BODY`) carry no useful geometry. All coordinates in inches on the 13.333 × 7.5 canvas.

### Shared chrome (memorize these — they repeat)

| Element | Geometry | Style |
|---|---|---|
| Logo, light slides | `L0.4 T0.3 W1.4 H0.3` (PNG 522×112, dark wordmark on transparent) | slides 3–10 |
| Logo, light slides (alt) | `L11.19 T0.247 W1.477 H0.317` top-right | slides 11–12 |
| Logo, dark slides | `L11.95 T0.344 W0.969 H0.276` or `L12.246 T0.271 W0.86 H0.245` (PNG 2048×1152 white on transparent) | slides 13–17 |
| Title bar (analysis) | `L0.4 T1.25 W12.5 H0.6`, anchor MIDDLE | Fraunces 9pt 26pt `#111111` |
| Title bar (compact) | `L0.4 T0.85 W12.5 H0.55` | Fraunces 9pt 24pt `#111111` |
| Sub-headline | `L0.4 T1.9 W12.5 H0.5` | WMD 14pt `#5B6770` |
| Footer left | `L0.4 T7.15 W4.0 H0.25` | WMD 9pt `#5B6770` — `"AI Workspace for Insurance"` |
| Footer center | `L5.0 T7.15 W3.3 H0.25`, CENTER | WMD 9pt `#5B6770` — `"furtherai.com"` |
| Footer (dark decks) | left `L0.276 T6.996/7.008 W3.256 H0.281`; center `L6.051 T6.996`; page `L11.827 T6.997` RIGHT | WMD 8.7pt `#EEEEEE` |
| Footer rule (dark) | `L0.284 T6.839 W12.765` LINE | `#666666` 0.005in |

**Every content pattern places the wordmark.** Light grounds take the dark
wordmark at `L0.4 T0.3 W1.4`; dark grounds take the white one at
`L11.95 T0.344 W0.969`. Two patterns are hybrids and are easy to get wrong:
`agent_diagram` has a full-width dark banner `H2.28` and `security_trust` a dark
left panel `W4.10`, so both take the **white** wordmark at `L0.4 T0.3` even
though the rest of the slide is light. `closing` is the one exception — it
centres a 3.19in white logo instead of using the corner slot.

Before this was systematic, 25 patterns called `footer()` directly and skipped
the logo, so generated decks carried a wordmark only on the cover and agenda.
If you add a pattern, place the logo; a slide without one is off-brand.

---

### Slide 1 — **Cover (full-bleed image)**
*Use when:* opening slide of any external deck.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Background image | `L0 T0 W13.333 H7.5` | PNG 1920×1080 (dark green gradient art) | — |
| Logo (white) | `L0.647 T0.624 W1.94 H0.553` | PNG 2048×1152 | — |
| Headline | `L0.647 T2.904 W7.148 H1.692`, anchor MIDDLE, lineSpacing 0.8 | **Fraunces Light 49.3pt `#FFFFFF`** | `Agentic Workspace for Insurance` |
| Client logo slot | `L0.647 T5.508 W1.454 H0.958` | PNG placeholder | — |
| Footer rule | `L0.284 T6.839 W12.765` | `#666666` 0.005in | — |
| Footer left / center / page | see chrome | WMD 8.7pt `#EEEEEE` | `AI Workspace for Insurance` / `furtherai.com` / `1` |

---

### Slide 2 — **Agenda (split panel + numbered bullets)**
*Use when:* 3–5 agenda items after the cover.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Page bg | `L0 T0 W13.333 H7.5` RECT | fill `#FBFBF9` | — |
| Left panel | `L0 T0 W5.4 H7.5` RECT | fill `#051610` | — |
| Title | `L0.55 T3.067 W4.7 H0.852`, lineSpacing 1.15 | **Fraunces 9pt 44pt `#FFFFFF`** | `Agenda` |
| Logo | `L11.464 T0.38 W1.319 H0.286` | PNG 166×36 | — |
| Number bubbles ×4 | `L5.861 T{2.113, 3.067, 4.124, 5.055} W0.49 H0.477` OVAL | fill `#274E13`, WMD 19pt `#FFFFFF` CENTER | `1` `2` `3` `4` |
| Item text ×4 | `L6.49 T{2.209, 3.055, 4.219, 5.137} W5.4–6.433 H0.329–0.658`, lineSpacing 1.15 | WMD 17pt `#14161C` | `Discuss overall approach of our effort` / `Outline methodology for process mapping and evaluation` / `Initial findings for <workstream>` / `Align on what success looks like & next steps` |

---

### Slide 3 — **Before/after contrast (two cards + arrow)**
*Use when:* framing a shift in approach — old way vs new way.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Title | `L0.4 T1.25 W12.5 H0.6` | Fraunces 9pt 26pt `#111111` | `1. From solution-first to foundation-first` |
| **FROM card** body | `L0.4 T2.6 W5.4 H3.764` | fill `#F7F9F8`, line `#DDDDDD` 0.01in | — |
| FROM header bar | `L0.4 T2.6 W5.4 H0.8` | fill theme-accent3 (**use `#8A9893`** neutral), line `#8A9893` 0.014in | — |
| FROM eyebrow | `L0.65 T2.67 W4.9 H0.25` | WMD 12pt `#FFFFFF` | `FROM` |
| FROM title | `L0.65 T2.9 W4.9 H0.35` | WMD 18pt `#FFFFFF` | `Solution-first` |
| FROM bullets | `L0.65 T3.626 W4.9 H2.435`, spaceBefore 12pt, `buChar` bullets | WMD 14.5pt `#111111` | 4 bullets, last one `Risk: …` |
| Arrow | `L6.0 T4.3 W1.0 H0.8`, CENTER | **WMD 56pt `#2C7A4B`** | `→` |
| **TO card** body | `L7.2 T2.6 W5.4 H3.764` | fill `#FFFFFF`, line `#2C7A4B` **0.021in** (thicker = the winner) | — |
| TO header bar | `L7.2 T2.6 W5.4 H0.8` | fill `#143524`, line `#143524` 0.014in | — |
| TO eyebrow | `L7.45 T2.67 W4.9 H0.25` | WMD 12pt **`#B7D9C5`** | `TO` |
| TO title | `L7.45 T2.9 W4.9 H0.35` | WMD 18pt `#FFFFFF` | `Foundation-first` |
| TO bullets | `L7.45 T3.626 W4.9 H2.435` | WMD 14.5pt `#111111` | 4 bullets, last one `Outcome: …` |
| Footers | see chrome | WMD 9pt `#5B6770` | — |

**Grammar rule:** the recommended side gets the green `#143524` header + `#2C7A4B` 0.021in outline; the rejected side gets a gray header + `#DDDDDD` hairline.

---

### Slide 4 — **Two pillars (parallel deep cards with nested sub-blocks + OUTPUT footer)**
*Use when:* two co-equal workstreams, each with 2 sub-components and a stated output.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Title / sub-head | `L0.4 T1.25 W12.5 H0.6` / `L0.4 T1.9 W12.5 H0.5` | Fraunces 9pt 26pt `#111111` / WMD 14pt `#5B6770` | `1. Two foundational pillars` / `Both required to unlock durable, scalable agentic execution. Neither sufficient alone.` |
| Card A body | `L0.4 T2.6 W6.15 H4.4` | fill `#E1EFD8`, line `#2C7A4B` 0.017in | — |
| Card A header | `L0.4 T2.6 W6.15 H0.95` | fill `#143524` | — |
| A eyebrow / title | `L0.65 T2.7 W5.65 H0.25` / `L0.65 T2.95 W5.65 H0.55` | WMD 12pt `#B7D9C5` / WMD 17pt `#FFFFFF` | `PILLAR 01` / `Process Discovery & Optimization Capability` |
| A summary | `L0.65 T3.7 W5.65 H0.55` | WMD 12pt `#143524` | `Discovery, mapping, and readiness scoring across Construction's processes.` |
| A sub-block 1 | box `L0.65 T4.35 W5.65 H0.95` fill `#EDF3EF`; label `L0.8 T4.45 W5.35 H0.3`; body `L0.8 T4.77 W5.35 H0.62` | WMD 14pt `#143524` / WMD 12pt `#111111` | `i · Discovery & Mapping` + description |
| A sub-block 2 | box `L0.65 T5.39 W5.65 H0.91`; label `T5.49`; body `T5.81` | same | `ii · Readiness & Optimization Layer` + description |
| A OUTPUT bar | `L0.4 T6.45 W6.15 H0.55` fill `#143524`; text `L0.65 T6.45 W5.758` | `OUTPUT` WMD 10pt `#B7D9C5` + rest WMD 12pt `#FFFFFF` | `OUTPUT  (a) Validated process catalog and (b) prioritization for implementation` |
| Card B | mirrors at `L6.8` / `L7.05` / `L7.2`, body fill `#FFFFFF` | same | `PILLAR 02` / `Knowledge Repository Optimization`; sub-blocks `Inventory`, `Audit & restructure` |
| Legend chip | swatch `L11.345 T1.966 W0.247 H0.225` fill `#E1EFD8` line `#2C7A4B`; label `L11.691 T1.911 W1.233 H0.333` | WMD 12pt `#000000` | `Current focus` |

**Grammar rule:** the in-focus pillar is tinted `#E1EFD8`; the other stays `#FFFFFF`. A legend chip top-right explains the tint.

---

### Slide 5 — **Numbered process steps (left) + options panel (right)**
*Use when:* a 5–8 step method on the left with commentary/alternatives on the right.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Title | `L0.4 T1.25 W12.5 H0.6` | Fraunces 9pt 26pt `#111111` | `2.A. Discovery & Mapping: Core process` |
| Left card | `L0.4 T2.55 W7.2 H4.65` | fill `#FFFFFF`, line `#DDDDDD` 0.01in | — |
| Left header bar | `L0.4 T2.038 W7.2 H0.55` (overlaps card top) | fill `#143524` | — |
| Header eyebrow / title | `L0.6 T2.074 W5.8 H0.22` / `L0.6 T2.278 W5.8 H0.3` | WMD 11pt `#B7D9C5` / WMD 15pt `#FFFFFF` | `i. DISCOVERY & MAPPING` / `Process capture approach` |
| Step bubble ×7 | `L0.58 T{2.75, 3.383, 4.016, 4.696, 5.341, 6.009, 6.677} W0.32 H0.32` OVAL | fill+line `#2C7A4B`; overlaid text shape same box, WMD 14pt `#FFFFFF` CENTER | `1`…`7` |
| Step title ×7 | `L1.0 T{bubble−0.055} W5.5 H0.28` | WMD 14pt `#111111` | `Pointed at a target process`, `Engage relevant SMEs`, `Structured 30-60 min interviews`, `Probe for what's not documented`, `Aggregate input across SMEs`, `Produce validated current-state`, `Iterate with SMEs until alignment` |
| Step caption ×7 | `L1.0 T{title+0.25} W5.5 H0.209` | WMD 14pt `#5B6770` | one-line explanation each |
| Separator lines ×6 | `L0.6 T{3.261, 3.88, 4.542, 5.195, 5.876, 6.523} W6.72` | `#7F7F7F` 0.01in | — |
| Right panel bg | `L7.8 T3.763 W5.267 H2.823` | fill `#FFFFFF`, line `#000000` 0.01in | — |
| Option heading ×2 | `L7.907 T{1.94, 3.852} W~5.0 H0.37` | WMD 16pt `#385623` | `Option A: Agent-driven discovery` / `Option B: Manual execution (FurtherAI-led)` |
| Option bullets ×2 | `L7.907 T{2.308, 4.26} W5.027 H1.363–1.683`, `buChar`, spaceBefore 6pt | WMD 14pt `#000000` | 2–3 bullets each |
| Status heading + note | `L7.913 T6.165 W4.152 H0.337` / `L7.78 T6.62 W5.287 H0.808` | WMD 14pt `#385623` / **WMD 14pt `#C00000`** | `Effort kicked off with <workstream>` / open-question text in red |

---

### Slide 6 — **Scorecard / rubric matrix (dimensions × 3 rating chips)**
*Use when:* showing an evaluation rubric — N criteria each rated across the same 3 levels.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Title | `L0.4 T1.25 W12.5 H0.6` | Fraunces 9pt 26pt `#111111` | `2.B. Readiness & Optimization Layer: Prioritization approach` |
| Left narrative heading | `L0.4 T2.728 W4.033 H0.37` | WMD 16pt **Bold** `#385623` | `Evaluation approach` |
| Rule under heading | `L0.505 T3.098 W4.547` | `#000000` 0.021in | — |
| Left narrative body | `L0.4 T3.193 W4.653 H3.669`, `buChar`, spaceBefore 12pt | WMD 16pt `#000000` | 3 bullets ending `Output: a ranked candidate list…` |
| Right card | `L5.531 T2.55 W7.522 H4.5` | fill `#FFFFFF`, line `#DDDDDD` 0.01in | — |
| Right header bar | `L5.531 T2.11 W7.522 H0.683` | fill `#143524` | — |
| Header eyebrow / title | `L5.731 T2.227 W5.8 H0.22` / `L5.731 T2.483 W5.8 H0.3` | WMD 14pt `#B7D9C5` / WMD 14pt `#FFFFFF` | `ii. READINESS & OPTIMIZATION LAYER` / `Scorecard - how each process is scored` |
| Dimension label ×5 | `L5.731 T{2.887, 3.747, 4.553, 5.333, 6.221} W5.8 H0.22` | WMD 14pt `#385623` | `Technical feasibility`, `Complexity`, `Speed to value`, `Relative effort (resource intensity)`, `Directional ROI` |
| Rating chips ×5 rows × 3 cols | `L{5.731, 8.215, 10.67} T{3.193, 4.066, 4.873, 5.666, 6.54} W2.28 H0.36` ROUNDED_RECT | fill `#F7F9F8`, line `#DDDDDD` 0.01in; text WMD 14pt CENTER — middle chip `#143524`, outer chips `#5B6770` | e.g. `Data-constrained` / `Human-judgment` / `Highly automatable`; `Low` / `Medium` / `High` |

**Grammar rule:** column pitch = 2.484in, chip width 2.28in; row pitch varies 0.79–0.87in. The **selected/typical** chip is colored `#143524`, the rest `#5B6770`.

---

### Slide 7 — **Current-state process + manual-activity register (2-pane audit)**
*Use when:* documenting an as-is process alongside the pain points found in it, with severity ratings.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Title | `L0.4 T0.85 W12.5 H0.55` | Fraunces 9pt 24pt `#111111` | `3.A. <Process area>: <sub-process>` |
| Meta strip | `L0.4 T1.355 W12.5 H0.35` | WMD 11pt *Italic* `#5B6770` | `Owner: Underwriter  ·  ~15-60 min hands-on  ·  ~6 days TAT  ·  Tools: Outlook · MyBook (Pega) · Sixfold · Guideline IQ` |
| Left card / header | `L0.4 T1.776 W7.5 H5.271` fill `#FFFFFF` line `#DDDDDD`; header `L0.4 T1.787 W7.5 H0.4` fill `#143524`; text `L0.55` | WMD 12pt `#FFFFFF` | `CURRENT PROCESS — DETAILED` |
| Zebra rows (odd) | `L0.45 T{2.23, 3.59, 4.974, 6.276} W7.4 H0.573` | fill `#F7F9F8` | — |
| Step bubble ×7 | `L0.55 T{2.377, 3.039, 3.737, 4.434, 5.12, 5.771, 6.422} W0.3 H0.3` OVAL | fill `#2C7A4B`; WMD 10pt `#FFFFFF` | `1`…`7` |
| Step text ×7 | `L0.95 T{bubble−0.107} W6.85 H0.513`, anchor MIDDLE | **run 1** WMD 11pt `#111111` (the action) + **run 2** WMD 10pt `#111111` (the ` — commentary`) | e.g. `Broker emails submission to UW's individual inbox` + ` — no central mailbox; …` |
| Right card / header | `L8.05 T1.776 W4.9 H5.271`; header `L8.05 T1.793 W4.9 H0.4` fill `#143524`; text `L8.2` | WMD 12pt `#FFFFFF` | `MANUAL ACTIVITIES IDENTIFIED` |
| Issue zebra | `L8.1 T{2.23, 4.04, 5.879} W4.8 H0.81` | fill `#F7F9F8` | — |
| Issue badge ×5 | `L8.17 T{2.35, 3.22, 4.12, 5.092, 6.01} W0.4 H0.4` OVAL | fill `#C0392B`; WMD 10pt `#FFFFFF` | `M1`…`M5` |
| Issue title ×5 | `L8.65 T{badge−0.04} W3.5 H0.25` | WMD 11pt `#111111` | `Decentralized email inboxes`, `Quick decline path multi-step post-migration`, `Manual pre-qualification by UW`, `Non-standardized UW→UA rating instructions`, `No management transparency` |
| Severity chip ×5 | `L12.2 T{badge−0.02} W0.65 H0.22` ROUNDED_RECT | `Critical`→fill `#C0392B` text `#FFFFFF`; `High`→fill `#F08C3F` text `#5C2F00`; `Med`→fill `#F4C842` text `#664E00`; all WMD 10pt CENTER | — |
| Issue detail ×5 | `L8.65 T{title+0.26} W3.5 H0.51` | WMD 10pt `#5B6770` | one-line evidence each |

---

### Slide 8 — **Prioritization matrix (criteria rows × process columns, arrow ratings)**
*Use when:* comparing 4 process areas across 5 criteria with directional judgments + rationale.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Title / sub-title | `L0.4 T0.85 W12.5 H0.55` / `L0.4 T1.493 W10.002 H0.431` | Fraunces 9pt 24pt `#111111` / WMD 19pt `#111111` | `3.B. Prioritization view: detailed assessment` / `<Process family> processes` |
| Legend arrows | `L{8.921, 10.4, 11.7} T~0.3 W0.289 H0.284` UP_ARROW (rot 0 / 90 / 180) | fills `#1C2B11` / `#A8D08C` / `#7F7F7F`, line `#1C3052` / self | labels `Favorable` `Neutral` `Unfavorable`, WMD 12pt `#17250F` at `L{9.323, 10.786, 12.128} T~0.35` |
| Row-header col | `L0.665 T{2.834, 3.887, 4.826, 5.698, 6.6} W1.177–1.353 H0.265–0.431` | WMD 14pt **Bold** `#385623` | `Technical feasibility`, `System complexity`, `Speed to value`, `Relative effort (resource intensity)`, `Directional ROI` |
| Col headers ×4 | `L{2.039, 4.799, 7.747, 10.774} T2.264 W2.06–2.679 H0.239–0.322` | WMD 14pt `#17250F` | `Intake & Clearance`, `Rating & Data Entry`, `Risk Assess., Pricing, Quoting`, `Bind, Book, Issuance` |
| `Metrics` corner label | `L0.665 T2.264 W0.871 H0.284` | WMD `#17250F` | `Metrics` |
| Grid lines | GROUP: thick top `L0.704 T2.651 W11.744` `#000000` 0.021in; rows `L0.62 T{3.704, 4.6, 5.525, 6.425} W11.827` `#7F7F7F` 0.01in | — | — |
| Rating arrow ×20 | `L{2.039, 4.797, 7.847, 10.842} T{2.834, 3.887, 4.772, 5.7, 6.615} W0.289 H0.284` UP_ARROW, rot 0/90/180 | fills as legend | — |
| Rationale cell ×20 | `L{2.496, 5.254, 8.298, 11.21} T{2.78, 3.82, 4.75, 5.68, 6.6} W1.9–2.5 H0.56–0.83` | WMD 12pt `#000000` | short rationale per cell |

**Grammar rule:** column pitch ≈ 2.76in / 2.95in / 3.03in (uneven — set explicitly). Arrow at column-left, rationale text 0.46in to its right.

---

### Slide 9 — **Matrix overview (same grid, one summary sentence per row)**
*Use when:* the executive-summary companion to Slide 8 — same 4 rows, one verdict sentence each.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Title / sub-title | `L0.4 T0.85 W12.5 H0.55` / `L0.4 T1.493 W10.002 H0.431` | WMD 24pt `#111111` (note: **not** Fraunces here) / WMD 19pt `#111111` | `3.B. Prioritization view: overview` / `<Process family> processes` |
| Column labels | `L0.665 T2.264 W1.234` and `L2.539 T2.264 W3.992` | WMD 16pt **Bold** `#17250F` | `Metrics` / `Preliminary assessment` |
| Row labels ×4 | `L0.665 T{2.834, 3.887, 4.826, 5.81} W1.667–1.916 H0.265–0.431` | WMD 16pt `#17250F` | `Intake & Clearance`, `Rating & Data Entry`, `Risk Assess., Pricing, Quoting`, `Bind, Book, Issuance` |
| Verdict ×4 | `L2.82–2.836 T{2.824, 3.887, 4.826, 5.821} W10.237 H0.55` | WMD 16pt `#000000` | one long sentence each |
| Grid lines | GROUP: thick top `L0.704 T2.651 W11.744` 0.021in; rows `T{3.704, 4.6, 5.595}` `#7F7F7F` 0.01in | — | — |

---

### Slide 10 — **Timeline / Gantt with milestones, key outputs and a next-steps box**
*Use when:* a month-by-month plan with swimlanes, milestone diamonds, and a callout of next actions.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Title | `L0.708 T0.792 W11.414 H0.791` | Fraunces 9pt 26pt `#1E1E2E` | `4. Timeline & next steps` |
| Header band | `L0.786 T1.985 W12.266 H0.855` | fill `#E7E6E6` (theme lt2) | — |
| Header rule | `L0.8 T1.934 W12.282` | `#000000` 0.021in | — |
| Column headers | `Timeline` `L0.758 T1.548 W1.445`; months `Apr…Dec` at `L{2.714, 3.606, 4.545, 5.388, 6.172, 7.107, 8.007, 8.88, 9.799} T1.548 W0.567–0.704`; `Key outputs` `L10.627 T1.554 W1.838` | WMD 16pt **Bold** `#000000` | — |
| Row labels ×4 | `L0.758 T{2.044, 3.035, 3.883, 4.874, 5.865}` (varying W) | WMD 13pt `#000000` | `Process mapping (via interviews) and initial prioritization`, `AI application scoping`, `POC development`, `Prep for implementation and contract signing`, `Implementation/ continuous support` |
| Row rules | `L0.8 T{2.881, 3.788, 4.637, 5.628} W12.282` | `#000000` 0.01in | — |
| Gantt bars | `L3.281 T2.197 W3.134 H0.317`; `L6.447 T3.138 W1.178`; `L7.625 T4.071 W0.749`; `L8.362 T4.918 W0.976`; `L9.349 T5.785 W2.455` | fill `#E1EFD8`, line `#FFFFFF` 0.014in | — |
| Milestone diamonds | `W0.376 H0.376` DIAMOND at `L{3.075 T2.163}`, `{6.227 T2.162}`, `{6.247 T3.102}`, `{7.437 T3.118}`, `{7.437 T4.032}`, `{8.166 T4.032}`, `{8.166 T4.888}`, `{9.118 T4.888}`, `{9.124 T5.742}` | fill `#38761D`, line `#FFFFFF` 0.014in | — |
| Milestone connectors | short vertical LINEs between diamond pairs, `#000000` 0.01in | — | — |
| Legend | diamond `L10.172 T0.404 W0.283` + label `L10.497 T0.42`; swatch `L8.212 T0.426 W0.283 H0.286` fill `#E7E6E6` + label `L8.533 T0.429` | WMD 12pt `#000000` | `Key milestones` / `Current focus` |
| Key-output cells ×4 | `L10.627 T{2.05, 2.996, 3.883, 4.695} W2.455 H0.673–0.875` | WMD 12pt `#000000` | one output description each |
| Today marker | triangle `L4.028 T6.393 W0.567 H0.269` fill `#E7E6E6` line `#44546A`; vertical line `L4.312 T1.943 H4.458` `#44546A` 0.01in; label `L3.554 T6.662 W1.512` CENTER | WMD 14pt `#000000` | `Today (5/14)` |
| Next-steps box | `L5.388 T6.233 W7.945 H1.27` | fill `#FFFFFF`, line `#274E13` **0.021in** | `Next steps` heading + 2 `buChar` bullets, WMD 14pt `#000000` |
| Logos | FurtherAI `L0.69 T0.227 W1.512 H0.324`; client `L12.2 T0.359 W0.635 H0.398` | — | — |

---

### Slide 11 — **Phase chevrons × maturity rows (partnership options)**
*Use when:* showing how a relationship/rollout evolves across 3 phases at 2 levels of engagement.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Title | `L0.667 T0.858 W12.0 H0.695` | **Fraunces 33.3pt `#14161C`** | `2. Two concrete partnership options emerge` |
| Logo | `L11.19 T0.247 W1.477 H0.317` | PNG 522×112 | — |
| Phase 1 chevron | `L2.187 T1.899 W4.127 H0.56` **PENTAGON** | fill `#274E13`; WMD 18.7pt `#FFFFFF` CENTER | `Phase 1` |
| Phase 2 / 3 chevrons | `L{6.265, 9.689} T1.912 W3.473 H0.56` **CHEVRON** | same | `Phase 2` / `Phase 3` |
| Row label ×2 | `L0.596 T{2.595, 4.524} W1.477 H0.628` | WMD 14.7pt **Bold** `#14161C` | `Customer first` / `Solutioning first` |
| Cell ×6 | `L{2.265, 6.314, 9.738} T{2.595, 4.524} W3.307–3.775 H0.875–1.369` | para 1 WMD 14.7pt **Bold** `#14161C`; para 2 WMD 14.7pt `#14161C` | e.g. `Customer` / `<short proof-point sentence>` |
| Row divider | `L0.74 T4.236 W12.294` | `#888888` 0.01in | — |
| Recommendation box | `L0.62 T5.804 W12.424 H1.369`, anchor MIDDLE | fill `#E7E6E6`, line `#888888` 0.01in; WMD 13.3pt mixed Bold/regular `#000000`, 2nd para italic | `Our recommendation: a starting point to shape together. …` |

---

### Slide 12 — **Pricing table (line-drawn, not a real table) + brace annotation + option cards**
*Use when:* laying out commercial components. **Built from text boxes + LINE shapes, not a `TABLE`** — gives full control of column widths.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Title | `L0.6 T0.552 W11.0 H0.561` | **Fraunces 33.3pt `#14161C`** | `2. Typical pricing structure` |
| Header row | `L{0.651, 3.228, 9.542} T1.585 W{2.3, 5.0, 2.5} H0.269` | WMD 16pt **Bold** `#1E1E2E` | `Component` / `Description` / `Fee type` |
| Header rule | `L0.658 T1.9 W11.516` | `#000000` **0.016in** | — |
| Row rules ×5 | `L~0.66 T{2.58, 3.177, 3.804, 4.271, 4.746} W10.7–11.5` | `#7F7F7F` 0.008in | — |
| Row cells ×5 | col1 `L0.665 W2.241–2.45`; col2 `L3.228 W5.268–5.997`; col3 `L9.542 W1.848–2.5`; tops `{2.0, 2.655, 3.253, 3.884, 4.389, 4.876}` | col1 WMD 14.7pt `#1E1E2E`; col2 WMD 13.3pt `#1E1E2E`; col3 WMD 14.7pt Bold+regular `#1E1E2E` | `Workflow Subscription`, `Platform & Tenant Fee`, `Integration`, `Implementation & Setup`, `User Access`, `Support & Training` |
| Brace annotation | RIGHT_BRACE `L11.482 T3.93 W0.119 H1.193` `#000000` 0.008in; note `L11.75 T4.091 W1.35 H0.808` | WMD 12pt Bold+regular `#1E1E2E` | `To be waived for high value prospective partners` |
| Section rule | `L0.6 T5.715 W12.363` | `#000000` 0.016in | — |
| Section heading | `L0.624 T5.379 W4.066 H0.269` | WMD 16pt **Bold** `#0A3318` | `Pricing across three options` |
| Option cards ×3 | `L{0.57, 4.75, 8.913} T~5.803 W4.05–4.066 H~1.113` | fill `#FFFFFF`, line `#14452A` 0.008in; title WMD 14.7pt Bold `#1E6B3A`, body WMD 14.7pt `#000000` | `Customer` / `Solutions / Reseller` / `FDE / Certified Delivery` |

---

### Slide 13 — **Product architecture hero (full-bleed dark, centered node diagram)**
*Use when:* the "what FurtherAI is" positioning slide. Reusable verbatim.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Full-bleed bg | `L-0.01 T0 W13.333 H7.5` | PNG 1920×1080 dark art | — |
| Headline | `L1.238 T0.802 W10.858 H0.819` CENTER | **Fraunces Light 40.7pt `#FFFFFF`** | `Agentic Workspace for Insurance` |
| Left / right labels | `L1.412 T2.607 W2.215 H0.97` / `L9.526 T2.553 W2.424 H0.97` CENTER | Fraunces Light 21.3pt `#FFFFFF` | `Frontier AI Models` / `Agentic Workflows` |
| Center node | `L5.974 T2.395 W1.386 H1.393` | PNG 297×299, line `#44546A` 0.005in | — |
| Connector lines | `L3.685 T3.038 W1.738` and `L7.91 T3.038 W1.738` | `#44546A` 0.01in | — |
| Value-prop labels ×4 | groups near `T4.0–5.9`, CENTER | WMD 13.3pt `#FFFFFF` | `Multi-modal AI / tailored for Insurance`, `Automates end-to-end Work`, `With human-in-the-loop`, `Integrates with Insurance systems` |
| Cert icons ×4 | `L{5.222, 5.989, 6.755, 7.523} T6.418 W0.587 H0.588` | PNG 108×108 (`#074B40` glyphs) | — |
| Logo / footer | `L11.95 T0.344 W0.969 H0.276`; footer `L0.276 T7.008` | WMD 8.7pt `#EEEEEE` | — |

---

### Slide 14 — **Use-case rail (left list, right screenshot collage)**
*Use when:* 4 named capabilities under one category, with a product screenshot.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Background art | `L0.614 T0 W12.719 H7.5` PNG | — | — |
| Left panel | `L-0.089 T0 W6.312 H7.66` | fill `#FBFBF9`, line `#44546A` 0.005in | — |
| Category title | `L0.556 T0.62 W5.667 H0.756`, lineSpacing 0.977 | **Fraunces Light 37.7pt `#000000`** | `Distribution` |
| Timeline rail | LINE `L0.526 T2.038 H3.672` `#9E9E9E` 0.01in; bullet icons `L~0.43 T{2.038, 3.263, 4.487, 5.711} W0.197 H0.196` PNG 175×174 | — | — |
| Item title ×4 | `L0.82 T{1.87, 3.119, 4.342, 5.564} W4.939 H0.483` | **Fraunces 20.7pt `#000000`** | `Proposal Generation`, `Create Submission`, `Policy Checking`, `Quote Compare` |
| Item body ×4 | `L0.82 T{2.292, 3.556, 4.799, 6.042} W4.939 H0.561` | WMD 12.7pt `#44546A` | one sentence each with a hard metric |
| Screenshot stack | `L6.432 T0.017 W6.902 H5.779`; `L6.223 T5.796 W7.11 H2.075`; inner UI `L6.657 T1.87 W6.242 H4.357` (PNG 1209×844) | — | — |
| Footer | `L0.276 T7.008 W3.256` | WMD 8.7pt `#595959` | `AI Workspace for Insurance` |

---

### Slide 15 — **Multi-agent system diagram (hub + 4 workflow nodes)**
*Use when:* showing the orchestrator and its child workflows.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Top banner image | `L0 T0 W13.333 H2.284` | PNG 1920×1080 | — |
| Title | `L0.572 T0.938 W6.039 H0.853` | **Fraunces Light 34.7pt `#FFFFFF`** | `Multi-agent System` |
| Hub card | `L5.103 T3.228 W3.032 H0.89` ROUNDED_RECT | fill `#FFFFFF`, line `#25654F` 0.009in | — |
| Hub logo | `L5.525 T3.422 W2.189 H0.502` (PNG 1600×900) + `L5.332 T3.394 W2.653 H0.569` (PNG 522×112) | — | — |
| Node cards ×4 | `L{1.634, 4.254, 6.875, 9.495} T5.17 W2.189 H0.797` ROUNDED_RECT | fill `#FFFFFF`, line `#25654F` 0.009in; 2 paras **Fraunces Light 15.8pt**, colors `#1D4438` (outer) / `#074B40` (inner) CENTER | `Inbox / Workflow`, `Intake / Workflow`, `Risk / Workflow`, `Rating / Workflow` |
| Elbow connectors | rotated LINEs `#44546A` 0.005in from hub to each node; short stubs `L{3.823, 6.443, 9.064} T5.569 W0.431` 0.006in | — | — |
| Footer | `L0.276 T7.008` | WMD 8.7pt `#595959` | — |

---

### Slide 16 — **Security / trust (left cert rail on dark, right stacked cards)**
*Use when:* the security & compliance slide. Reusable verbatim.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Left dark panel | `L0 T-0.023 W4.1 H7.523`, rot 180 | PNG 1920×1080 | — |
| Cert table | `L0.89 T0.659 W2.089 H6.587` TABLE 4×1, rowHeights `[1.633, 1.708, 1.713, 1.533]`, no header/banding | — | — |
| Cert icons ×4 | `L~1.45 T{0.908, 2.654, 4.311, 5.846} W0.994–1.052` | PNG 108×108 | — |
| Cert labels ×4 | `L~0.879 T{1.944, 3.64, 5.41, 6.896} W0.685–1.105` | **Courier New 11.3pt Bold `#D0CEC3`** | `SOC2`, `GDPR`, `ISO 27001`, `HIPAA` |
| Headline | `L4.664 T0.491 W7.789 H1.256` (2 paras) | **Fraunces 29.3pt `#2B2D31`** | `Your data, models, and knowledge` / `stay secure. Always.` |
| Cards ×4 | `L4.803 T{2.093, 3.3, 4.508, 5.716} W7.957 H1.079` ROUNDED_RECT | fill `#F8F7F5`, line `#D0CEC3` 0.01in | — |
| Card title ×4 | `L4.955 T{2.151, 3.373, 4.567, 5.76} W5.254 H0.673` | **Fraunces 24pt `#444339`** | `Industry Leading Certifications`, `Zero Data Training Policy`, `End-to-End Encryption`, `Single Tenant Architecture` |
| Card body ×4 | `L4.944 T{2.63, 3.852, 5.046, 6.239} W7.675 H0.482` | WMD 12.7pt `#444339` | one sentence each |

---

### Slide 17 — **"Why us" (dark full-bleed, big paragraph left + 3 satellite callouts)**
*Use when:* the differentiation slide over a product screenshot.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Backgrounds | `L0 T0 W13.333 H7.5` + `L-3.749 T0 W8.957 H7.5` + product shot `L5.833 T0.583 W7.236 H6.778` | PNGs | — |
| Headline | `L0.708 T0.694 W6.111 H0.78`, lineSpacing 0.977 | **Fraunces 31pt `#FFFFFF`** | `Why FurtherAI?` |
| Body paragraph | `L1.109 T2.414 W3.222 H3.333`, lineSpacing 1.275 | WMD 16pt `#EEEEEE` | `Unlike general-purpose AI tools that fall short on insurance workflows…` |
| Left rail | LINE `L0.778 T2.478 H3.245` `#44546A` 0.01in | — | — |
| Callout title ×3 | `L{6.528, 10.521} T1.438 W2.076 H0.222`; `L7.833 T5.216 W3.222` | **Wix Madefor Text 12pt Bold `#FFFFFF`**, lineSpacing 1.139 | `Insurance-Native`, `Holistic`, `Enterprise-Ready` |
| Callout body ×3 | `L{6.528, 10.521} T1.735 W2.069 H1.333`; `L7.833 T5.514 W3.215 H0.889` | Wix Madefor Text 12pt `#FFFFFF` | one sentence each |

---

### Slide 18 — **Closing / thank-you (full-bleed dark, centered logo)**
*Use when:* the final slide.

| Shape | Geometry | Style | Text |
|---|---|---|---|
| Full-bleed bg | `L0 T0 W13.333 H7.5`, rot 180 | PNG 1920×1080 | — |
| Centered logo | `L2.173 T3.149 W3.194 H0.91` | PNG 2048×1152 white | — |
| Vertical accent | LINE `L6.315 T2.989 H1.231` | `#44546A` 0.01in | — |
| Footer rule | `L0.647 T6.834 W12.101` | `#666666` 0.005in | — |
| Footer left / right | `L0.585 T6.897 W3.256` / `L11.595 T6.897 W1.231` | WMD 10pt `#D0CEC3` | `AI Workspace for Insurance` / `furtherai.com` |

---

## C. `Enterprise_Deck.pptx` and the systematic client deck

### C.1 Enterprise_Deck (37 slides, 10 × 5.625in canvas)

| # | Pattern | New vs Example_Slides |
|---|---|---|
| 1 | Cover — **gradient background** `#074B40→#1A421F→#14452A→#203E13` + `ADD LOGO HERE` placeholder at `L6.376 T2.38 W2.751` (27pt white) | **NEW**: gradient cover (no image needed) + explicit client-logo drop zone |
| 2–10 | Team card — cream `#F4F3F0` bg, 3 rounded-rect headshots at `T1.107 W2.847 H2.386`, name Fraunces Light 18pt `#074B40`, role WMD 9pt `#074B40`, full-width rule `#25654F` 0.01in at `T4.396`, investor/logo strips below, gradient fade masks (`#F4F3F0`→`#14161C`) on the edges | **NEW**: headshot roster; gradient-fade edge masks |
| 11 | Problem statement — 3 columns of icon + `WMD SemiBold 13.5pt #074B40` title + `WMD 9.5pt #2B2D31` body under a 2-line Fraunces Light 26.8pt headline | **NEW**: bare 3-column icon triad on white |
| 12 | Product architecture hero on `#0B0B12` | same as Example 13 |
| 13 | Use-case grid — 47 shapes, many small labeled chips on a `#424242→#010101` gradient | **NEW**: dense chip grid |
| 14 | Platform stack diagram — nested groups of layered bands (`Skills`, `Model Router`, `Insurance Systems`) with 6.1–10.2pt WMD labels over a full-bleed image | **NEW**: layered-stack architecture |
| 15 | Section cover (`Demo`) reusing the cover gradient with a nav row of module names | **NEW**: gradient section divider |
| 16–17 | Workflow swimlane diagrams (44–49 shapes, `#FBFBF9` bg) | **NEW**: BPMN-ish swimlanes |
| 18 | Portfolio matrix (programs × policy admin) | **NEW** |
| 19–23 | **Use-case rail on dark** — left `#FBFBF9` panel `L-0.067 W4.734`, Fraunces Light 28.2pt category title, 4 items (Fraunces 15.5pt title + WMD 9.5pt `#44546A` body) on a `#9E9E9E` rail with 175×174 icons, right side a screenshot collage on the `#424242→#010101` gradient | 0.75× version of Example 14, **dark backdrop variant** |
| 24 | **Question slide** — 4-quadrant image mosaic, small WMD 15.5pt setup text top-left, giant right-aligned Fraunces Light 41.5pt question with one word in **Fraunces Italic** | **NEW**: strong rhetorical-question layout |
| 25 | Three-step approach — 3 centered `WMD SemiBold 10.5pt` labels joined by `#25654F` 0.01in rules with 175×174 dot icons, illustrative art below, on `#F4F3EF` | **NEW**: horizontal connected 3-step |
| 26 | **Table slide** — Fraunces Light 29pt `#074B40` title + native 7×4 `TABLE` `L0.603 T1.35`, header row fill `#F4F3EF` WMD 11pt Bold, value column WMD 11pt Bold `#25654F` | **NEW**: real PPTX table with brand styling |
| 27 | Stack-fit matrix on `#000000` — 5 function columns × 3 capability bands in rounded-rect outlines, WMD 10pt `#FFFFFF` | **NEW** |
| 28 | Multi-agent diagram | same as Example 15 |
| 29–32 | Per-agent workflow detail (35–41 shapes each) | **NEW**: agent spec pages |
| 33 | Security/trust | same as Example 16 |
| 34 | Why FurtherAI | same as Example 17 |
| 35 | **Implementation phases** — 3 `Weeks x-y` headers (WMD SemiBold 16.8pt white) + `WMD Medium 9.5pt` sub-labels over a native TABLE, with **rotated 270° swimlane labels** `FurtherAI` / `Customer` at `L0.119` | **NEW**: phased plan with rotated lane labels |
| 36 | Closing with `ADD LOGO HERE` | variant of Example 18 |
| 37 | **Accuracy table** — Fraunces Light 29pt title with the metric in `Fraunces #25654F`, 15×3 TABLE, header fill `#1D4438` white 13.5pt, value cells fill `#D9EAD3` | **NEW**: metrics table |

### C.2 The systematic client deck (11 slides, 13.333 × 7.5in)

Shipped in `assets/brand/pptx_templates/` under its original client filename.

Programmatically generated, perfectly systematic, **one shape grammar reused on every slide**:

- **Standing chrome:** eyebrow `L0.55 T0.45 W11.5 H0.3` Calibri 11.5pt Bold (`#0E8C82` on light, `#12B5A6` on dark); headline `L0.55 T0.78 W11.5 H0.95` Georgia 26–28pt Bold (`#16233F` on light, `#FFFFFF` on dark); deck `L0.55 T1.78 W12.2` Calibri 14.5–15pt `#33415C`; footer `L0.55 T7.08 W8.0` Calibri 9pt `#5A6B86` = `FurtherAI  ·  Prepared for <Client>  ·  Confidential`; page number `L12.35 T7.08 W0.5` RIGHT.
- **Light card:** ROUNDED_RECT fill `#F4F7FC` line `#DDE6F2` 0.014in; icon circle OVAL `W0.85 H0.85` fill `#E3F4F2` with a 256×256 PNG inset `W0.47`; title Calibri 16pt Bold `#16233F`; body Calibri 12.5pt `#5A6B86` lineSpacing 1.15.
- **Dark card:** ROUNDED_RECT fill `#1E2F54` line `#2C3F66`, optional 0.1in `#12B5A6` top cap bar.
- **Slide list:** 1 cover (navy, overlapping decorative ovals, 3 pill chips); 2 context + 3 stat cards with a 0.09in teal left bar; 3 four-problem grid (2×2); 4 three-step cards on navy with teal cap bars; 5 five-step horizontal pipeline (2.18 × 2.3in cards, 0.28in arrow PNGs at 2.5in pitch) + a full-width reassurance line; 6 four-benefit grid; 7 **stat hero** — 6 tiles `W3.85 H1.95` on navy, number Georgia 44pt Bold `#12B5A6`, label Calibri 13pt `#CADCFC`, plus a disclaimer line; 8 logo wall (8 chips `W2.85 H0.82`) + a full-width `#16233F` quote bar with attribution in `#12B5A6`; 9 trust 2×2; 10 differentiation 2×2; 11 next steps.

### C.3 Which deck to start from

**Start from the systematic client deck for structure, and re-skin it with the Enterprise_Deck palette and fonts.**

- It is the only deck built as a **consistent, parameterized system**: fixed eyebrow/headline/footer positions, one card recipe, one stat recipe, one grid pitch. A build script can reproduce it from data with ~6 helper functions.
- It has **zero placeholder art dependency** — every visual is a shape or a small 256×256 icon, so a generated deck never has broken images.
- Its two backgrounds (`#FFFFFF` light, `#16233F` dark) alternate to give rhythm without needing photography.
- **But**: its palette (navy/teal) and fonts (Georgia/Calibri) are **not** FurtherAI brand. Swap navy `#16233F`→`#074B40`, deep `#0E1730`→`#0B0B12`, card-on-dark `#1E2F54`→`#1D4438`, teal `#12B5A6`→`#25654F`, teal-on-light `#0E8C82`→`#074B40`, light card `#F4F7FC`→`#F8F7F5`, card border `#DDE6F2`→`#D0CEC3`, body-on-dark `#CADCFC`→`#D0CEC3`, muted `#5A6B86`→`#595959`, body-on-light `#33415C`→`#2B2D31`; and Georgia→`Fraunces 72pt Light`, Calibri→`Wix Madefor Display`.

`Example_Slides.pptx` remains the **pattern vocabulary** (which layout for which content), and `Enterprise_Deck.pptx` the **brand-fidelity reference** (colors, gradients, chrome, table styling). Neither is a good copy-and-fill base: both are hand-composed with 15–67 shapes per slide and heavy image dependencies.

---
## D. The delivery-deck corpus — patterns beyond `Example_Slides`

Sections A–C are measured from the three decks in
`assets/brand/pptx_templates/`. This section catalogues the patterns found in the
wider corpus of 18 real account decks (kickoffs, check-ins, scoping sessions,
partnership pitches, internal enablement) that sections A–C do not cover.

`references/deck_types.md` says which of these a given deck type uses. This
section says where each came from and what varies between the decks that use it.

**Canvas warning.** Eight of these decks are 10 × 5.625in Google Slides exports.
Scale their measurements by 1.3333 to land on the 13.333 × 7.5in canvas. The
Google-Slides decks also leak `scheme:` theme-colour references — never carry
those over, always set explicit hex.

### D.1 Implemented

Registered in `deck_kit.PATTERNS`; run `--list-patterns` for exact fields.

| Pattern | Source decks | Notes on variation |
|---|---|---|
| `workstream_swimlane` | Balance, FSLSO, DealerGuard, Aviva, CNA, Enterprise_Deck | The most reused layout in the corpus. Column axis is **either** calendar (weeks/months) **or** phase; row axis is **either** workstream **or** owning party. Gates render two ways in the wild — diamond markers with captions, or bordered `END OF WEEK N` cards; the composite uses diamonds. Critical path is flagged with a red row, buffer weeks with a muted tan band |
| `two_column_list` | ResourcePro, Deloitte, Aviva, Strategic Counseling, CNA, FSLSO, POC-swimlanes | Umbrella for four framings that are structurally identical: mutual benefits, scope in/out, asks vs next steps, and the risk→mitigation ledger. `mode: "ledger"` pairs rows across the columns; `mode: "columns"` keeps them independent. No connecting glyphs — that's `before_after`'s job |
| `stepper_row` | Balance, FSLSO, DOXA, CNA, Workflow Overviews | Horizontal numbered process. Two card skins in the wild (light fill with a left accent bar; solid fill with none). `terminal_inverted` marks the step that exits back to the customer — worth using, it reads instantly |
| `status_table` | Novacore, PMA, CNA | Dependency checklists and issue trackers. Status is the **cell fill**, never a separate pill. The Status Update column in a tracker is an append-only dated journal, not a rewritten sentence |
| `step_detail_io` | Aviva ×4, Enterprise_Deck ×4 | One process step's full spec: inputs, what FurtherAI does, outputs, what a human still reviews, what we need to build it. The `confirm` callout keeps open questions on the slide they belong to instead of collecting them at the back |

### D.2 Catalogued, not yet built

Real patterns from the corpus with a narrower blast radius. Each appears in one
or two decks. Build one when a deck actually needs it — and add it to
`PATTERNS` and to the relevant type contract at the same time, so it doesn't
stay a one-off.

| Pattern | Source | What it does |
|---|---|---|
| `timeline_table_gantt` | FWG s3, LICB s3 | A real PPTX table of workflow rows × week columns with a hand-drawn Gantt overlay on top — bars, diamond milestones, a Today triangle, and legend chips. Status text stays uncoloured because the bar carries the signal |
| `milestone_track` | Balance s3, DealerGuard s3 | "Where we are" orientation strip. Two skins: dots on a horizontal line with a halo on the current node, or a vertical squares list with the today row enlarged and filled |
| `raci_swimlane` | POC-swimlanes s3 | Role rows × phase columns with chip badges for secondary involvement. Distinct from `workstream_swimlane` in that columns are lifecycle phases and rows are roles, and there are no dates at all |
| `objective_grid` | Aviva s3 | `OBJECTIVE 0N` numbered statements, 2×2, bold lead plus body. `card_grid` covers most of this; a dedicated pattern would get the numbering right |
| `role_cards_row` | Aviva s6 | Agent roles in one row, each card carrying bullets plus a tools footer and a human-review footer, with an optional `LATER` badge |
| `scoring_rubric` | POC-swimlanes s7 | Weighted qualification rubric — category, metric, description, thresholds — built from borderless rects and rules rather than a table, with a scoring key badge in the corner |
| `case_study_narrative` | Strategic Counseling s8 | Anonymised case study as a three-act before / approach / after column set on a dark ground |
| `team_roster` | CNA p13 | Text-only two-column team list, `name — role`, FurtherAI on one side and the client on the other. Use when there are no headshots |
| `walkthrough_exhibit` | Workflow Overviews s6-9 | Per-product exhibit page: title, description, example prompts, screenshot, callout stickers. Reusable as a customer-deck appendix |
| `kpi_definition_grid` | DealerGuard s5 | Metric-definition glossary wall — several mini-sections of bold subheading plus short definition list, tiled without a grid, sometimes with an inline formula and a plain-text tiered scale |

### D.3 Patterns deliberately not carried over

| Seen in | Why not |
|---|---|
| `statement_lead_in` (Deloitte s8) | A lone sentence where a table or gantt was clearly meant to go. It reads as an unfinished slide, not a pattern |
| `email_draft_insert` (LIA s7) | A full draft email pasted onto a slide to socialise it internally. Legitimate as a one-off; not a layout worth systematising |
| `architecture_stack_diagram` (One_Pager p2) | Nested bands inside a dark panel with two orange accent bars. Nothing else in the corpus looks like it, and it isn't parameterisable |
| Strategic Counseling's palette | An entire undocumented "editorial" colour system — near-black greens, `#F4EFE5` cream, `#C9A86A` gold. Its three layouts are worth harvesting; the palette is not sanctioned and must be restyled to `BRAND.md` tokens. The deck also carries stray Calibri and Quattrocento runs that are font-resolution bugs, not choices |

---
