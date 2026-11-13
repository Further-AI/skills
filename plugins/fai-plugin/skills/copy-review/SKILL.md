---
name: copy-review
description: Reviews copy in a document, deck, PDF, or raw text against the FurtherAI Writing Style Guide and returns located, rule-cited findings with suggested rewrites. Use when the user says "review this copy", "check the writing", "does this match our style guide", "edit this doc", "proofread", "review my deck copy", or before shipping any FurtherAI-branded artifact. The deck and doc skills call this automatically before they build.
---

# Copy review

Reviews text against the FurtherAI Writing Style Guide. Every rule from that
guide is inlined below — **do not read the source PDF**, it adds nothing.

DOCX and PPTX extraction use built-in OOXML fallbacks and need no third-party
package. PDF extraction requires `pypdf` or `PyPDF2`; when neither is installed,
use pasted or separately extracted text. See `${CLAUDE_PLUGIN_ROOT}/DEPENDENCIES.md`.

Output is always a findings table with a location, a rule ID, the offending
text, and a concrete rewrite. Never a vague "consider tightening this".

## Run it

Two scripts. The mechanical rules are automated; the judgment rules are yours.

```bash
# 1. the deterministic sweep — terminology, AIisms, punctuation, numbers, dates
python3 "${CLAUDE_PLUGIN_ROOT}/skills/copy-review/scripts/check_mechanics.py" <file> \
  [--artifact blog|deck|doc|email|reference] [--json] [--rule R42 --rule R28] \
  [--list-rules]

# 2. the raw text, when you need to read it yourself for the judgment pass
python3 "${CLAUDE_PLUGIN_ROOT}/skills/copy-review/scripts/extract_text.py" <file> \
  [--json] [--include-notes] [--min-chars N] [--stats]

# or pipe one extraction into both
python3 "${CLAUDE_PLUGIN_ROOT}/skills/copy-review/scripts/extract_text.py" <file> --json \
  | python3 "${CLAUDE_PLUGIN_ROOT}/skills/copy-review/scripts/check_mechanics.py" \
      --stdin-json --artifact doc
```

Both support `.docx .pptx .pdf .md .txt .html`. Raw text pasted into the
conversation needs no script — review it directly.

`check_mechanics.py` emits the findings table below, already formatted, and
exits **1** when any blocking finding exists — so a `deck` or `doc` build can
gate on it. Exit **0** is clean or findings-but-nothing-blocking; exit **2** is a
usage or read error, not a verdict — a gate must tell those apart.

It covers R42–R44, R28–R30, R32–R39, and R16/R18. It does **not** cover **R31**,
the Oxford comma — no regex distinguishes a missing serial comma from a
two-item list, so that one stays a manual check. Nor the judgment rules (voice
R1–R12, structure R13–R15/R17/R19–R21, readability R22–R27). Those are yours.

It skips quoted counter-examples — a document that *discusses* banned phrasing
(a style guide, editorial feedback, this skill) doesn't get flagged for citing
it. The trigger is a quoted phrase inside a list item, table row, blockquote or
heading, or a line that names the offence ("banned", "avoid", "never use", a
rule ID). A banned phrase in ordinary running prose still fires.

`--artifact` is inferred when omitted — a path under `skills/` or `docs/` is a
`reference`, otherwise the extension decides (`.pptx`→deck, `.docx`/`.pdf`→doc,
`.md`/`.html`→blog, `.txt`→email) — and it controls which rules run, per the
scoping table below. Pass it explicitly when the path or extension misleads: a
customer one-pager drafted at `docs/proposal.md` is a `doc`, not a reference.

Each extracted unit carries a location token you must cite in findings:

| Source | Location token | Means |
|---|---|---|
| docx | `p:12` / `p:12/r:3` | body paragraph / run inside it |
| docx | `tbl:1/r:2/c:0` | table 1, row 2, cell 0 |
| docx | `hdr:0/p:1`, `ftr:0/p:1` | header / footer part (needs `--include-notes`) |
| pptx | `s:3/sh:2/p:0` | slide 3, shape 2, paragraph 0 |
| pptx | `s:3/tbl:1/r:0/c:2`, `s:3/notes/p:0` | table cell / speaker notes |
| pdf | `pg:4/l:17` | page 4, line 17 (text layer only, no OCR) |
| md/txt/html | `l:42` | line 42 |

A scanned PDF has no text layer — the script warns and returns nothing. Read
those with the Read tool and review the text inline instead.

## Scope the rules to the artifact

Applying blog rules to a contract produces noise. Pick the column first.

| Rule group | Blog / web / GEO | Deck (pptx) | Doc (docx/pdf) | Email / Slack | Reference |
|---|---|---|---|---|---|
| A. Voice (R1–R12) | yes | yes | yes | yes | yes |
| B1. Structure (R13–R21) | yes | headings + list rules only | yes | list rules only | yes |
| B2. Readability (R22–R27) | yes | R22–R24 only (slides aren't prose) | yes | yes | R22–R24 only |
| C. AIisms (R28–R30) | yes | yes | yes | yes | yes |
| D. Punctuation (R31–R35) | yes | yes | yes | yes | all but R34 |
| E. Numbers (R36–R39) | yes | yes | yes | yes | yes |
| F. Abbreviations (R40–R41) | yes | yes | yes | yes | yes |
| G. Terminology (R42–R44) | yes | yes | yes | yes | yes |
| R6 (600+ words) | yes | **no** | **no** | **no** | **no** |
| R15 (Key Takeaways / FAQ) | yes | **no** | **no** | **no** | **no** |
| R26 (3–4 line paragraphs) | yes | **no** — bullets are correct on slides | yes | soften | **no** |
| R33 em-dash density | yes | yes | yes | yes | **no** |
| R34 heading case | yes | yes | yes | yes | **no** |

**Reference** is the fifth type: technical docs, skill files, runbooks, API
notes. Its headings are labels, not sentences, and its em dashes live in tables
and command blocks — so R34's sentence case and R33's density budget describe
nothing real there. Everything else still applies; terminology and AIisms
matter as much in a runbook as in a blog post. `check_mechanics.py` infers it
from any path under `skills/` or `docs/`.

If the artifact type is unclear from the file extension or the user's words,
ask once, then proceed.

---

# The rules

## A. Voice and audience

- **R1** Confident, conversational, authoritative. Not stiff, not overly technical.
- **R2** Read like a knowledgeable colleague explaining an insight over coffee — not a whitepaper or press release.
- **R3** Respect the reader's intelligence. No hype-language.
- **R4** Use real data, customer outcomes, and FurtherAI-specific examples wherever possible.
- **R5** Structure for AI extractability: clear headings, direct answers, bold key claims.
- **R6** Blog posts: 600 words minimum; GEO articles perform better at 800–1,200.
- **R7** Hold the brand voice the whole way through, not just the intro.

The voice, verbatim from the guide:

- **R8** Confident but not boastful — let results speak.
- **R9** Grounded and specific. No generic AI hype.
- **R10** Operator-level: written for underwriters and claims teams, not tech enthusiasts.
- **R11** Clear and editorial. Say less, but better.
- **R12** Human. The people running insurance are real; write like you know them.

## B. Structure

- **R13** Hierarchical headings — H1, one level of H2s, H3s under them — so readers (human and model) can scan and skip.
- **R14** A subheading every 3–5 paragraphs. Keep sections short.
- **R15** Include Key Takeaways and FAQ sections in most articles, answering contextually relevant questions directly.
- **R16** Build real tables. Never paste a table as a screenshot — models can't read it.
- **R17** Every table column and row gets a clear, descriptive header.
- **R18** No empty table cells. Use `N/A`; an empty cell reads as a broken table.
- **R19** No excessively long or complex tables. A human has to be able to comprehend it.
- **R20** Numbered lists for steps or hierarchy. Bulleted lists for non-sequential items.
- **R21** Lists must be contextually justified. Don't shred prose into bullets for the look of it.

## B2. Readability

- **R22** Active, present tense.
- **R23** Use contractions.
- **R24** "We" for the company, "you/your" for the reader.
- **R25** Pyramid structure: answer the question as early and as specifically as possible, then go deeper.
- **R26** Paragraphs of 3–4 lines. Not one sentence each; not half-page blocks.
- **R27** Vary sentence length. Mix punchy and complex.

## C. AIisms — the highest-signal check

- **R28** Never use contrast framing:
  - "It's not just X, it's also Y."
  - "It's not about X, it's about Y."
  - "It's not A. It's not even B. It's C."
  - "This is not about X, this is about Y."
  - "That's not a feature, that's a workaround."
  - "We're not a vendor, we're a partner."
  - "It's not faster, it's cheaper."

  Any subject and any copula counts. The tell is the second clause restating
  the same subject affirmatively, not the negation itself — plain negation is
  fine, and the checker leaves it alone.
- **R29** Never open a paragraph with a formulaic preamble:
  - "Here's the thing…" · "In the world of…" · "At its core…"
  - "The X is quietly revolutionizing…" · "This is game-changing…"
- **R30** Rule-of-three constructions sparingly — at most once or twice per article:
  - "It's [adj X], [adj Y], and [adj Z]."
  - "Our platform is X, Y, and Z"
  - "[Verb] X. [Verb] Y. [Verb] Z."

R28 and R29 are absolute. R30 is a budget: count the instances across the
whole artifact before flagging, and report the count.

## D. Punctuation and capitalization

- **R31** Always use the Oxford comma.
  Good: "Track turnaround time, error rates, and renewal lift to quantify results."
- **R32** No exclamation marks in body copy.
- **R33** Em dashes sparingly, and always spaced: ` — `.
  Good: "Orchestration coordinates the flow of automated tasks — sending summaries, assigning follow-ups, and tracking responses."
- **R34** Title Case for H1s (blog titles). Sentence case for every subheading.
  H1: "2026 Guide to Automated Renewal Summaries for Commercial Underwriting Teams"
  H2: "Understanding automated renewal summaries in commercial underwriting"
- **R35** Lower case after a colon or semicolon.
  Good: "The shift is fundamental: the entire UI is collapsing."
  Good: "Most platforms treat intake as a data entry problem; we treat it as a decision intelligence problem"

## E. Dates, percentages, numbers

- **R36** Spell out one–nine; digits for 10 and up. "three years ago" / "over $100M".
- **R37** Currency and percentage use symbols with digits: "down 40%", "more than $25M".
- **R38** Dates in American *Month Day, Year*: "March 26, 2026".
- **R39** Date ranges take an en dash, not a hyphen: "March 22 – 25, 2026".

## F. Abbreviations, acronyms, contractions

- **R40** Spell out on first use, then abbreviate: managing general agent (MGA), large language models (LLMs).
- **R41** Use contractions freely: don't, can't, shouldn't, we're, that's.

## G. Brand and industry terminology

- **R42** **FurtherAI** — one word, capital F, capital AI. Never "Further AI", never "further ai".
- **R43** **AI** all caps. Spell out "artificial intelligence" only on first use, and only if the audience warrants it.
- **R44** **LLMs** / **large language models**. No periods between letters.

## Banned → preferred quick table

| Banned | Preferred |
|---|---|
| "It's not just X, it's also Y." / "It's not about X, it's about Y." / "It's not A. It's not even B. It's C." | Say the thing directly |
| "Here's the thing…" / "In the world of…" / "At its core…" / "…is quietly revolutionizing…" / "This is game-changing…" | Open with the substance |
| Rule-of-three triads, repeated | Max once or twice per piece |
| Exclamation marks in body copy | Full stop |
| Unspaced or overused em dash | ` — `, sparingly |
| Hyphen in a date range | En dash: "March 22 – 25, 2026" |
| Missing Oxford comma | Always use it |
| Capital after a colon or semicolon | Lower case |
| Title Case subheading | Sentence case |
| Sentence case H1 | Title Case |
| "1 year", "3 years" | "one year", "three years" |
| "40 percent", "25 million dollars" | "40%", "$25M" |
| "26 March 2026", "3/26/2026" | "March 26, 2026" |
| "Further AI", "further ai" | "FurtherAI" |
| "ai", "A.I." | "AI" |
| "L.L.M.s" | "LLMs" |
| Passive, past tense | Active, present tense |
| Table as a screenshot | Real table, descriptive headers |
| Empty table cell | `N/A` |
| One-sentence or half-page paragraphs | 3–4 lines |

---

## Review procedure

1. **Establish artifact type** — from the extension, the user's words, or one
   question. Pick the rule column from the scoping table.
2. **Extract** with the script, or read the text inline. For a long document,
   review it in passes rather than truncating: structure first, then line-level.
3. **Run the mechanical sweep** — `check_mechanics.py` covers groups C, E, G,
   part of B, and all of D except R31, in one pass. Don't hand-grep for what it
   already checks.
4. **Do the judgment pass** on what it can't reach, in this order:
   - D (the one mechanical gap): Oxford commas — read every three-item list
   - B (structure): heading hierarchy, subheading cadence, table headers and
     comprehensibility, whether each list is justified
   - B2 (readability): active present tense, contractions, we/you, pyramid
     ordering, paragraph length, sentence-length variety
   - A (voice): the hardest and last. Only flag what you can rewrite
     concretely — "this feels generic" is not a finding, a rewrite is
5. **Report** the findings table (below). Order by severity, then location.
6. **Offer to apply.** Do not edit the source file unless the user asks or a
   calling skill (`deck`, `doc`) is revising a draft it owns.

## Findings format

```
## Copy review — <file> (<artifact type>)

**Verdict:** <clean | N findings, M blocking>

| # | Loc | Rule | Sev | Current | Rewrite |
|---|---|---|---|---|---|
| 1 | s:4/sh:2/p:0 | R42 | blocking | Further AI helps carriers… | FurtherAI helps carriers… |
| 2 | p:14 | R28 | blocking | It's not just faster, it's more accurate. | Submissions clear in four minutes with a 3% error rate. |
| 3 | p:22 | R36 | fix | 3 years of loss runs | three years of loss runs |

**Voice notes** (judgment, not mechanical):
- <observation> → <specific rewrite>
```

Severity:
- **blocking** — terminology errors (R42–R44), AIisms (R28–R29), and anything
  that misstates a fact. These ship wrong; fix before the artifact goes out.
- **fix** — mechanical rule breaks (D, E, R16–R20). Cheap, unambiguous.
- **consider** — voice and rhythm judgment calls (A, B2). Suggest, don't insist.

If a piece is clean, say so in one line and stop. Do not manufacture findings
to look thorough.

## When called by `deck` or `doc`

Those skills call this one on the **drafted copy, before the artifact is
built** — reviewing a finished .pptx means rebuilding it. Return the revised
copy inline (not a findings table) so the caller can build from it, plus a
one-line note of what changed. Blocking findings must be fixed, not reported.

Most `deck` types map to `--artifact deck` and most `doc` types to
`--artifact doc`. Three exceptions, because the rule columns differ:

| Type | Use | Why |
|---|---|---|
| `client_debrief_note` | `--artifact email` | It's a circulated note, not a document. Also enforce its own rule: no individual names in the body, organisation labels only |
| `api_integration_guide` | `--artifact reference` | Headings are labels, em dashes live in tables and code. R33 and R34 describe nothing real there |
| `internal_process_enablement` | `--artifact reference` | Same reason — it's a runbook that happens to be slides |

Never rewrite a figure, a commercial term, or legal language for flow in
`pricing_summary`, `pricing_overview`, or any contract-shaped document.

## Don't

- Don't read `assets/brand/style_guide/Writing_Style_Guide.pdf` — every rule
  in it is above.
- Don't apply blog rules (R6, R15, R26) to slides or contracts.
- Don't rewrite technical terms, product names, legal language, quoted
  customer language, or numbers to make prose flow. Accuracy beats voice.
- Don't flag a rule without quoting the offending text and giving the rewrite.
- Don't edit the user's file without being asked.
