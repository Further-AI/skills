# Blueprints

Tab structures, columns, and summary formulas for the deliverables brokers,
account managers, and underwriters ask for most. Adapt names and columns to the
sources at hand; keep the structure.

## Contents

1. Reconciliation of two sources
2. Schedule extraction
3. Loss run summary
4. Quote and coverage comparison
5. Follow-up subset
6. When nothing fits

In every blueprint, column A of a list tab must be filled on every row (the
record key or the source reference), because the summary counts rows with
`count_rows`, which counts column A.

## 1. Reconciliation of two sources

**Use when** two lists describe the same things and the user wants to know how
they differ: a policy's vehicle, equipment, location, or driver schedule
against the insured's own list; a carrier schedule against the agency
management system; certificate holders against a contract list; a commission
statement against the invoice register.

**Buckets.** Every record from each source lands in exactly one:

| Bucket | Meaning |
| --- | --- |
| Matched | Same item on both sides, compared fields agree |
| Matched with differences | Same item, but a compared field differs (year, value, location) |
| Needs Review | Candidate pair a person must confirm (near-miss key, or same secondary key with conflicting keys) |
| Only on A | No match or candidate on the B side |
| Only on B | No match or candidate on the A side |

`reconcile.py` produces Matched (split it into agree and differ yourself by
comparing fields), Needs Review, and the two only-lists. Duplicates within a
source are reported separately; those records still sit in one of the buckets.

**Tabs, in order:** `Summary`, `Needs Review`, `Only on <A>`, `Only on <B>`,
`Differences`, `Matched`, `Duplicates` (if any), then `<A> Extract` and
`<B> Extract`. Include the extracts whenever a source came from a PDF, so the
user can verify what was read; the summary can then count them with formulas.

**Columns:**

- *Needs Review:* Review #, `<A>` Ref, `<A>` key, year, description, value,
  `<B>` Ref, `<B>` key, year, description, value, Reason (the pair's
  `method`), Evidence (its `detail`), and an Action dropdown such as
  "Same item - A is correct", "Same item - B is correct", "Different items",
  "Investigate".
- *Only on A / Only on B:* source reference, key, descriptive fields, value,
  Notes, Action dropdown. Keep the source's own fields and order; don't
  invent columns the source doesn't have.
- *Differences:* key, `<A>` Ref, `<B>` Ref, Differs In (field names, for
  example "Year; Cost New"), then each compared field for A and B side by
  side, then a difference formula for numeric fields (`=G2-H2`), Action.
- *Matched:* key, both references, Match Method, and the main fields from
  both sides.
- *Duplicates:* Source, key, reference, descriptive fields, Note.

**What counts as a difference:** compare normalized values and flag only
differences that matter to coverage or billing: year, value or cost new,
garaging location, VIN or serial. The user usually says to ignore case,
spacing, punctuation, and abbreviations; a "Chevorlet" vs "CHEVROLET" spelling
isn't a difference, though it can be noted.

**Summary:**

- Key facts: insured, policy number, carrier, policy period, and the two
  sources by name and date.
- Results: record count per source, one count per bucket and for duplicates,
  and useful totals such as the value of vehicles on the insured's list but not
  on the policy (potentially uninsured).
- Checks: `balance_check` per source, where total = Matched + Matched with
  differences + Needs Review + Only on that source.
- Method: matching tiers and keys, normalizations applied, fields compared,
  and anything that couldn't be read or was excluded.

## 2. Schedule extraction

**Use when** the user wants a schedule from a document as a spreadsheet: "put
the vehicle schedule from this policy in Excel", "extract the SOV", "list the
drivers from the application".

**Tabs:** `Summary`, the schedule (`Vehicles`, `Equipment`, `Locations`,
`Drivers`), and optionally `Data Issues`, with one row per problem found
(reference, field, value, issue): a VIN failing its check digit, a year that
doesn't match the VIN, a missing value. Data Issues is a list of problems, not
a bucket of records.

**Columns by schedule type** (use what the source provides, in this order):

- *Vehicles:* Veh #, Source Ref, Year, Make, Model, Body Type, VIN, GVW or
  class, Garaging Location or ZIP, Cost New or Stated Amount, Radius, Use,
  Coverages or deductibles, Lienholder.
- *Equipment:* Item #, Source Ref, Description, Make, Model, Year, Serial #,
  Value, Valuation Basis (ACV, RC), Location, Owned or Leased.
- *Locations (SOV):* Loc #, Bldg #, Source Ref, Address, City, State, ZIP,
  Occupancy, Construction, Year Built, Stories, Sq Ft, Sprinklered, Protection
  Class, Building Value, Contents (BPP), Business Income, Other, TIV. TIV is a
  row formula over the value columns, wrapped in `Formula` in each row, e.g.
  `Formula(f"=SUM(O{row}:R{row})")` with `row = index + 2`.
- *Drivers:* Driver #, Source Ref, Name, DOB, License #, License State, Hire
  Date, Violations, Accidents, Excluded. Include personal data only when the
  user needs it.

**Summary:** source document, record count, totals as `sum_column` formulas
(total cost new, total TIV), and a small breakdown by a useful category (by
state, by body type) using COUNTIFS or SUMIFS over the schedule tab.

## 3. Loss run summary

**Use when** the user wants loss history summarized, usually for a renewal
submission or an underwriting review.

**Tabs:** `Summary`, `By Policy Year`, `Claims`.

**Claims columns:** Claim #, Source Ref, Carrier, Policy #, Policy Term (text
such as "2024-2025", used for grouping), Line (GL, Auto, WC, Property), Date of
Loss, Date Reported, Claimant or Driver, Description, Status, Paid Loss, Paid
Expense, Reserve, Incurred, Large Loss.

- Incurred is a row formula (`=L{row}+M{row}+N{row}`) when the source gives
  its components. If the source reports only incurred, or its components don't
  add up to its incurred, keep the source's figure and say so in Method.
- Large Loss is a row formula against a threshold cell or a stated amount
  (`=IF(O{row}>=25000,"Yes","No")`); shade it with `color_rows`.
- Normalize Status to a fixed set (Open, Closed, Reopened), because formulas
  count on it; record that in Method.

**By Policy Year columns:** Policy Term, Carrier, Claims, Open Claims, Paid,
Reserve, Incurred, Premium (if provided), Loss Ratio. Every metric is a formula
over Claims, for example `=COUNTIFS(Claims!E:E,A2)` and
`=SUMIFS(Claims!O:O,Claims!E:E,A2)`; loss ratio is `=IF(H2>0,G2/H2,"")`.
Include every term in the requested window, including terms with no claims:
"no losses" is information an underwriter wants to see.

**Summary:** insured, carriers, the window covered, and each carrier's
valuation date. Many underwriters want loss runs valued within the last 90
days, so flag older valuations. Totals across all years as formulas.

## 4. Quote and coverage comparison

**Use when** comparing quotes to each other or to the expiring program, or
comparing coverage between two policies, often for a client-facing proposal.

**Layout:** a comparison matrix built with `add_matrix_sheet`: coverage items
down the side, options across the top, the expiring program first and passed as
`baseline` so differing cells are shaded. A matrix mixes a limit, a percentage,
and text such as "Excluded" in one column, which a list tab can't do; each
`MatrixRow` carries its own kind and number format.

**Groups and typical rows:**

- *General Liability:* Each Occurrence, General Aggregate, Products-Completed
  Operations Aggregate, Personal and Advertising Injury, Damage to Rented
  Premises, Medical Expense, Deductible.
- *Auto:* Combined Single Limit, UM/UIM, Comprehensive Deductible, Collision
  Deductible, Hired and Non-Owned.
- *Property:* Building, Business Personal Property, Business Income,
  Deductible, Wind/Hail Deductible, Valuation, Coinsurance.
- *Umbrella:* Each Occurrence, Aggregate, Retention.
- *Workers Compensation:* Employers Liability limits, states covered.
- *Premium:* premium per line, taxes and fees, total (a formula), change vs.
  expiring in dollars and percent (formulas). Use `number_format="$#,##0.00"`.
- *Terms:* carrier, AM Best rating, admitted or non-admitted, policy form
  (occurrence or claims-made, retro date), notable exclusions,
  subjectivities, quote expiration date.

**Rules:**

- Split combined limits into separate rows: "1,000,000 / 2,000,000" becomes
  Each Occurrence and General Aggregate.
- Blank means the quote didn't say. Write "Not quoted", "Excluded", or
  "Included" only when the document says so.
- Cite where each option's figures came from (document and page) in the
  Summary's Sources section or the Notes column.
- Present facts. The broker makes the recommendation unless they ask you to.

**Summary:** insured, effective date, options compared with carrier and quote
date, a few "key differences" lines in plain words, and sources.

## 5. Follow-up subset

**Use when** the user asks for part of an earlier result: "now just the
trailers", "send me only the ones not on the policy as a separate file".

- Reuse the earlier results. When building the first workbook, save each
  tab's rows as JSON in the working directory exactly as written, including
  derived columns such as Notes, and filter those for the follow-up.
  Rerunning the matching, or re-deriving notes, can give different rows.
- Keep the parent tab's columns, order, names, and style exactly, so rows
  can be pasted back and forth.
- Two tabs: `Summary`, with the parent workbook's name, the filter in words
  ("Type is ATV or side-by-side"), and a count formula; and the data tab.
- Name the file after the parent with the subset:
  `Ola_Inc_ATVs_Not_On_Policy_2026-09-11.xlsx`.
- The rows must be exactly the parent tab's rows that meet the filter. Check
  the count against the parent before delivering.

## 6. When nothing fits

Apply the data rules and layout from SKILL.md: a Summary first, one list tab
per kind of record (`add_table_sheet`), matrices only where items are compared
across options (`add_matrix_sheet`), formulas for everything derived, and the
checker before delivery.
