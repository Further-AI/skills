# Deck types

The **type contract** layer. `slide_patterns.md` tells you how to draw a slide;
this file tells you which slides a given kind of deck is made of, and in what
order.

Read this before composing any deck. Two people asking for the same deck type
must get the same structure — that is the whole point of this file, and it only
works if the slide plan is followed as written rather than re-derived per deck.

## How to use a type contract

```bash
python3 build_deck.py --list-types                        # what exists
python3 build_deck.py --type implementation_kickoff --scaffold > spec.json
python3 build_deck.py spec.json --check --type implementation_kickoff
python3 build_deck.py spec.json --out deck.pptx
python3 lint_deck.py deck.pptx --type implementation_kickoff
```

`--scaffold` emits the type's slides in the type's order with empty content.
Fill in copy. **Do not reorder, and do not insert a slide the contract doesn't
list.** `--check --type` fails on a missing required slot, a slot out of order,
or an unsanctioned pattern.

If the deck genuinely needs a slide no type covers, that's a signal the type
contract is incomplete — say so and propose the addition, rather than quietly
adding a one-off slide that the next person won't reproduce.

## Slot vocabulary

| Column | Meaning |
|---|---|
| **Slot** | stable name for the slide's job in the deck. Referenced by `--check` |
| **Pattern** | the `slide_patterns.md` / `--list-patterns` pattern that renders it |
| **Req** | `yes` = always present. `cond` = present only when the include-rule fires. `rep` = repeats once per item in the source data |
| **Include when** | the exact condition for a `cond` or `rep` slot. Evaluate against the data you have, not against how full the deck looks |

A `cond` slot that doesn't fire is **dropped entirely**. Never emit a slide with
placeholder content to keep a slot filled — an absent slide reads as "not
applicable", a placeholder slide reads as "we didn't do the work".

## Canvas

**New decks are 13.333 × 7.5in.** All geometry in `slide_patterns.md` and every
`deck_kit` composite assumes it.

Many reference decks are 10 × 5.625in — that's a Google Slides export artifact,
not a brand choice. Scale geometry by 0.75 when reading measurements off those
decks, and never inherit the smaller canvas into something new.

## Theme per type

`brand` is the product palette; `consulting` is the denser analytical palette.
Each type below names the one it uses. Don't mix them inside a deck.

---

## implementation_kickoff

*Source decks:* Novacore, Balance Partners, LICB, FSLSO, DealerGuard
*Purpose:* align a new (or restarted) implementation on scope, timeline,
dependencies and owners.
*Audience:* customer stakeholders plus the FurtherAI delivery team. The
`[Internal]`/`[Shared]` filename tag in the reference decks changes nothing
structurally — same contract either way.
*Length:* 6–10 slides. *Theme:* `brand`.

| # | Slot | Pattern | Req | Include when |
|---|---|---|---|---|
| 1 | `cover` | `cover` | yes | always |
| 2 | `objectives` | `two_column_list` | cond | the customer sponsor set an explicit give/get frame for the session. Columns: "Our objectives" \| "What we need from today" |
| 3 | `agenda` | `agenda` | yes | always |
| 4 | `scope_recap` | `stat_hero` | cond | you have real scale figures (volume, LOB count, entity count). 2–3 stats, `dark: false` |
| 5 | `where_we_are` | `timeline` | cond | this kickoff follows earlier work and the customer needs orienting. Use `today` to mark the current point |
| 6 | `workflow` | `stepper_row` | yes | always — the target workflow, 4–6 steps |
| 7 | `plan` | `workstream_swimlane` | yes | always — workstreams × weeks, with milestone gates |
| 8 | `dependencies` | `status_table` | yes | always — what we need from the customer, with owner and status |
| 9 | `demo` | `section_divider` | cond | a live demo runs in this meeting |
| 10 | `next_steps` | `next_steps` | yes | always, and always last |

**Content rules**

- The workflow slide is the one the customer checks against their own
  understanding. Name their systems and their document types, not generic ones.
- `workstream_swimlane` gates are calendar commitments. Every gate needs a real
  end-of-week date; a gate you can't date is a workstream, not a gate.
- Mark the critical path. A swimlane where nothing is flagged tells the customer
  nothing about risk.
- Dependencies name a **person**, not a team, wherever the person is known. An
  unowned dependency is the single most common reason a kickoff plan slips.
- `next_steps` is its own slide, immediately after the plan. Never folded into
  the swimlane.

---

## implementation_checkin

*Source decks:* FWG, LIA, PMA, CNA
*Purpose:* recurring sync during an active implementation — what shipped, what's
blocked, what's next.
*Audience:* the joint working team.
*Length:* 4–12 slides, scaling with the number of update items.
*Theme:* `brand`.

This type has **two forms**. Pick by what the user gave you.

**Full form** — the user wants a meeting deck.

| # | Slot | Pattern | Req | Include when |
|---|---|---|---|---|
| 1 | `cover` | `cover` | yes | always |
| 2 | `agenda` | `agenda` | yes | always |
| 3 | `timeline_review` | `timeline` | yes | always — workflow rows against weeks, with `today` set |
| 4 | `issues` | `status_table` | yes | always — the running tracker |
| 5 | `updates` | `card_grid` | rep | once per product-update theme, when there are updates to report |
| 6 | `usage` | `stat_hero` | cond | submission volume or accuracy figures are available for the window |
| 7 | `feedback` | `two_column_list` | cond | the session collects live feedback. Columns: "Prior follow-ups" \| "Live feedback" |
| 8 | `next_steps` | `next_steps` | yes | always, and always last |

**Tracker-only form** — the user handed you an issue list and nothing else (the
PMA case). The deck *is* the tracker:

| # | Slot | Pattern | Req | Include when |
|---|---|---|---|---|
| 1..n | `issues` | `status_table` | rep | paginate at ~8 rows per slide; title carries the count, e.g. `Running Issue Tracker (1/2)` |

No cover, no agenda, no next steps. Adding them to a tracker-only deck is the
most common way this type gets built wrong.

**Content rules**

- The Status Update column is an **append-only dated journal**, newest entry
  last — not a single rewritten sentence. The history is the value.
- Status lives in the **cell fill**, not in a separate pill shape. One
  convention, deck-wide.
- Use the platform's own numbers. `gather_updates.py` (see the SKILL) pulls
  published versions and submission stats per workflow for the window; do not
  ask the user to recall them.
- Where a workflow shipped nothing in the window, say so plainly. Never invent a
  number to fill a slot.

---

## build_scoping

*Source deck:* Aviva_Scoping (the fullest and the most on-brand deck in the
whole reference set — follow it closely)
*Purpose:* agree exactly what a workflow build covers, step by step, including
what stays human.
*Audience:* joint working session — customer underwriting/IT leadership plus
FurtherAI delivery.
*Length:* 10–12 slides. *Theme:* `consulting`.

| # | Slot | Pattern | Req | Include when |
|---|---|---|---|---|
| 1 | `cover` | `cover` | yes | always |
| 2 | `agenda` | `agenda` | yes | always |
| 3 | `objectives` | `card_grid` | yes | always — 4 numbered build objectives, `cols: 2` |
| 4 | `timeline` | `workstream_swimlane` | yes | always — calendar columns, phase-coloured bars |
| 5 | `risks` | `two_column_list` | yes | always — risk \| mitigation ledger |
| 6 | `process_overview` | `card_grid` | yes | always — one card per agent role in the process |
| 7 | `step_detail` | `step_detail_io` | rep | **once per process step** — this is the substance of the deck |
| 8 | `asks` | `two_column_list` | yes | always — data we need \| next steps |
| 9 | `closing` | `closing` | yes | always |

**Content rules**

- The repeated `step_detail_io` slides are the deck. Each one answers: what goes
  in, what FurtherAI does, what comes out, what a human still reviews, and what
  we need from you to build it. A step missing the human-review row reads as a
  claim that the step is fully autonomous — only say that when it's true.
- Anything unresolved gets an explicit `TO CONFIRM` callout on the slide it
  belongs to. Do not collect open questions into one slide at the back; they get
  lost there.
- Defer honestly. `Phase 2` and `Not in scope (confirm)` are legitimate answers
  and read as competence. Vague scope reads as a future argument.

---

## partnership_discussion

*Source decks:* ResourcePro, Deloitte
*Purpose:* pitch a channel, reseller, or SI partnership.
*Audience:* prospective partner executives, usually a first meeting.
*Length:* 6–8 slides. *Theme:* `brand`.

| # | Slot | Pattern | Req | Include when |
|---|---|---|---|---|
| 1 | `cover` | `cover` | yes | always |
| 2 | `agenda` | `agenda` | yes | always |
| 3 | `mutual_value` | `two_column_list` | yes | always — what each side brings and gets. Columns are the two company names |
| 4 | `use_cases` | `data_table` | cond | specific joint use cases are on the table |
| 5 | `model` | `phase_chevrons` | yes | always — the phased partnership model |
| 6 | `commercials` | `pricing_table` | cond | the partner asked about economics, or a revenue split is being proposed |
| 7 | `next_steps` | `next_steps` | yes | always |
| 8 | `appendix` | `section_divider` | cond | backup material follows |

**Content rules**

- `phase_chevrons` carries a maturity axis (rows) as well as phases (columns).
  For a simple co-sell motion with no tiering, use one row and say what it is —
  don't fake a second row.
- The mutual-value slide is the one that decides the meeting. Write the
  partner's column first, and write it in their language.
- Never put a FurtherAI-confidential figure on a partner deck.

---

## workflow_solution

*Source deck:* FurtherAI_DOXA_Submission_Gateway (documented in
`slide_patterns.md` §C.2)
*Purpose:* pitch one specific workflow to one prospect.
*Audience:* a single-workflow buyer.
*Length:* 11 slides, fixed. *Theme:* `brand`.

This is the most parameterized type — the slide plan does not vary at all, only
the content does.

| # | Slot | Pattern | Req |
|---|---|---|---|
| 1 | `cover` | `cover` | yes |
| 2 | `context` | `stat_hero` | yes |
| 3 | `problem` | `card_grid` | yes |
| 4 | `approach` | `card_grid` (3 cards) | yes |
| 5 | `pipeline` | `stepper_row` | yes |
| 6 | `benefits` | `card_grid` | yes |
| 7 | `outcome` | `stat_hero` | yes |
| 8 | `proof` | `use_case_rail` | yes |
| 9 | `trust` | `security_trust` | yes |
| 10 | `differentiation` | `why_us` | yes |
| 11 | `next_steps` | `next_steps` | yes |

**Content rules**

- Every slide is about the one workflow. The moment a slide could belong to any
  deck, it's the wrong slide for this type.
- The DOXA source deck is client-branded navy/teal. It is a **structure**
  reference only — port it onto the brand palette using the mapping table at the
  end of `--list-patterns`.

---

## enterprise_capabilities

*Source deck:* Enterprise_Deck (documented in `slide_patterns.md` §C.1)
*Purpose:* full platform pitch to a new enterprise logo.
*Audience:* broad — exec sponsor through to security review.
*Length:* 20–37 slides. *Theme:* `brand`.

Long enough that it's built in sections, not slides. The section spine is fixed;
depth within a section flexes with what the prospect cares about.

| # | Section | Slots | Req |
|---|---|---|---|
| 1 | Open | `cover`, `two_column_list` (roster) | yes |
| 2 | Problem | `before_after` | yes |
| 3 | Platform | `architecture_hero`, `card_grid` (use cases) | yes |
| 4 | Demo | `section_divider`, `use_case_rail` ×N | cond — a demo runs |
| 5 | Agents | `agent_diagram`, `step_detail_io` ×N | cond — the agent story is relevant |
| 6 | Fit | `rag_matrix` (stack fit), `data_table` (capabilities) | cond — a build-vs-buy or vendor comparison is live |
| 7 | Trust | `security_trust` | yes |
| 8 | Why us | `why_us` | yes |
| 9 | Delivery | `workstream_swimlane`, `pricing_table` | cond — the deal is far enough along |
| 10 | Close | `next_steps`, `closing` | yes |

**Content rules**

- Cut sections rather than thinning them. A 20-slide deck with four complete
  sections beats a 37-slide deck where six are half-answered.
- Sections 4–6 and 9 are the flexible ones. 1–3, 7, 8 and 10 always ship.
- The roster is the **text** form — `two_column_list`, FurtherAI in one column
  and the customer in the other, each entry `Name — role`. Enterprise_Deck runs
  a headshot variant, which needs the unbuilt `team_roster` pattern
  (`slide_patterns.md` §D.2) and carries an asset dependency no other pattern
  has. Don't reach for headshots unless someone has actually supplied them.

---

## internal_process_enablement

*Source decks:* POC scoping and execution AE-EM swim lanes, FurtherAI Workflow
Overviews
*Purpose:* teach the internal team a process — how to scope a POC, what each
workflow does, who owns which phase.
*Audience:* internal only. Mark it so.
*Length:* 7–9 slides. *Theme:* `consulting`.

| # | Slot | Pattern | Req | Include when |
|---|---|---|---|---|
| 1 | `cover` | `cover` | yes | always |
| 2 | `framing` | `two_column_list` | yes | always — "what this is" \| "what this is not" |
| 3 | `ownership` | `workstream_swimlane` | yes | always — role rows × phase columns, not calendar columns |
| 4 | `decision_path` | `two_column_list` | cond | the process branches (e.g. out-of-box vs custom) |
| 5 | `reference` | `data_table` | rep | once per table page when cataloguing workflows or tools; paginate `(1/3)` |
| 6 | `tooling` | `numbered_steps` | cond | there's a tool the reader has to actually operate |
| 7 | `appendix` | `section_divider` | cond | a rubric or backup follows |
| 8 | `rubric` | `scorecard` | cond | the process includes scoring or qualification |

**Content rules**

- Both source decks are **off-brand** — POC-swimlanes mixes Georgia, Arial and
  Nunito; Workflow Overviews is Nunito throughout with a mistyped green
  (`#25644F` for `#25654F`). Take the structure, apply brand tokens. Never
  sample a colour out of these files.
- Internal decks still get brand fonts and brand colours. "Internal" licenses a
  plainer *structure*, not a broken type system.
- Mark internal-only material on the slide, not just in the filename.

---

## account_update

*Source:* `gather_updates.py`, plus the update slides in LIA and FWG.
*Purpose:* what changed for this account recently.
*Audience:* the account team, or the customer.
*Length:* 5–8 slides. *Theme:* `brand`.

| # | Slot | Pattern | Req | Include when |
|---|---|---|---|---|
| 1 | `cover` | `cover` | yes | always |
| 2 | `headline` | `stat_hero` | yes | always — submissions and accuracy over the window |
| 3 | `shipped` | `card_grid` | rep | once per workflow with activity in the window |
| 4 | `quiet` | `data_table` | cond | one or more workflows shipped nothing — list them plainly |
| 5 | `blockers` | `status_table` | cond | there are open blockers |
| 6 | `next_steps` | `next_steps` | yes | always |

**Content rules**

- Gather the numbers first with `gather_updates.py`, then ask the user **one**
  question covering wins, blockers and next steps — and only if the conversation
  hasn't already answered it. A deck built purely from counts is hollow; a
  four-question interview is worse.
- Rewrite publish notes into what changed *for the customer*, not what changed
  in the config.
- A workflow that errored during gathering gets named honestly or dropped. Never
  smoothed over.

---

## Choosing between types

| The user says… | Type |
|---|---|
| "kickoff", "we're starting with X", "implementation kickoff" | `implementation_kickoff` |
| "check-in", "weekly sync", "status deck", or hands over an issue list | `implementation_checkin` |
| "scoping", "what's in the build", "walk through the workflow steps" | `build_scoping` |
| "partnership", "reseller", "channel", "co-sell" | `partnership_discussion` |
| "a deck for <workflow> for <prospect>" | `workflow_solution` |
| "the enterprise deck", "full platform pitch", "new logo" | `enterprise_capabilities` |
| "how do we scope POCs", "internal enablement", "workflow reference" | `internal_process_enablement` |
| "what changed", "account review", "recent updates" | `account_update` |

Two types can look close from one sentence. `build_scoping` and
`implementation_kickoff` are the pair that gets confused most: scoping decides
**what** gets built (repeated step detail, open questions), kickoff decides
**when and who** (swimlane, dependencies, owners). If the workflow steps are
still being agreed, it's scoping.

Ask once when it's genuinely unclear. The plans diverge from slide 3, so
rebuilding one as the other means starting over.
