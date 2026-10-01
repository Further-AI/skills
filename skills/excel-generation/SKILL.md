---
name: excel-generation
metadata:
  furtherai-display-name: Excel
compatibility: Python 3.12+ and openpyxl 3.1.5+. LibreOffice is optional for formula recalculation.
description: Build Excel workbooks (.xlsx) for insurance brokers, account managers, and underwriters from policies, ACORD forms, schedules, loss runs, quotes, SOVs, and client spreadsheets. Covers reconciliations (policy schedule vs. the insured's list), schedule extractions (vehicles, equipment, locations, drivers), loss run summaries, quote and coverage comparisons, and filtered follow-up lists, with correctly typed values, a formula-driven summary that accounts for every record, and one consistent house style. Use this skill whenever the user wants results as a spreadsheet, Excel file, xlsx, download, export, or "a format I can paste into Excel", including short follow-ups like "put that in excel" or "now just the trailers", and whenever two lists or documents are compared and the answer is a table, even if Excel isn't mentioned until later.
---

# Excel generation for insurance deliverables

The person opening this workbook will act on it: add vehicles to a policy,
send the insured a list of questions, paste rows into an agency management
system or carrier portal, or forward it to a client. They can only do that if
they trust it, and trust comes from four things:

1. **Every record is accounted for.** Each row of each source lands in exactly
   one place, and the summary proves it.
2. **Numbers behave like numbers.** Years, amounts, and dates sort, filter, and
   sum; identifiers keep their leading zeros.
3. **Every row is traceable** to where it came from in the source documents.
4. **The file looks and works the same every time**, across requests and across
   follow-up turns.

Workbooks generated without these rules have repeatedly shipped with years and
dollar amounts stored as text, placeholders like `<empty>` leaking into cells,
counts in the chat reply that disagree with the file, different category
counts for the same inputs on different runs, and a new visual style for every
follow-up file. Each rule below prevents one of those.

## Workflow

1. **Decide what the workbook is for.** Identify the deliverable type and read
   its blueprint in `references/blueprints.md`: reconciliation, schedule
   extraction, loss run summary, quote comparison, or follow-up subset. For a
   follow-up in the same conversation, reuse the earlier workbook's columns,
   names, and matching results instead of starting over.
2. **Extract every record** from every source into Python data, with a source
   reference for each (page and line number, or sheet and row). Check
   completeness against the document itself: schedule numbering with gaps,
   stated totals ("Total vehicles: 109"), or row counts. When a parser returns
   HTML or Markdown tables, walk the table structure instead of regex-matching
   the text. If records can't be read, say which ones; never drop them silently.
3. **Match or transform deterministically.** For reconciliations, use
   `scripts/reconcile.py` (below) so the same inputs always produce the same
   buckets. Normalize values only for matching and keep the source values for
   display.
4. **Build the workbook** with `scripts/xlsx_kit.py`, which applies the house
   style and the data rules for you.
5. **Recalculate and check**, then fix everything the checker reports as an
   error:

   ```bash
   python <skill-dir>/scripts/recalc.py output.xlsx      # caches formula results; needs LibreOffice
   python <skill-dir>/scripts/check_workbook.py output.xlsx
   ```

   `recalc.py` exits with code 2 when LibreOffice isn't installed. That's
   acceptable, since Excel computes formulas on open, but previews will show
   blank formula cells, so mention it if the user may preview the file in a
   browser.
6. **Reply** following "The chat reply" below.

## Data rules

These make the workbook trustworthy. `xlsx_kit` enforces most of them;
`check_workbook.py` detects violations.

- **Store values with their real types.** Amounts, counts, years, and
  percentages are numbers with a number format; dates are dates. A `"$4,000"`
  string can't be summed, and `"2004"` sorts as text. Percentages are stored as
  fractions (0.94 displays as 94.0%).
- **Store identifiers as text.** VINs, serial numbers, unit, policy, claim, and
  location numbers, ZIP codes, and FEINs are text, so `02040138` keeps its
  leading zero and long numbers never turn into `1.23E+17`.
- **Blank means unknown.** Don't write "N/A", "—", or "TBD" into a numeric or
  date column; leave the cell blank and put any explanation in a Notes column.
  Code artifacts ("None", "nan", "<empty>") must never reach a cell.
- **Keep source values verbatim** (trimmed of surrounding spaces). If the fleet
  list says "Chevorlet", show "Chevorlet" and flag it; a silently corrected
  value can't be traced back. Put normalized values in their own column when
  they help.
- **One record per row.** Data tabs have the header in row 1 and nothing above
  it, no merged cells, no blank spacer rows, and no subtotal rows mixed into
  the data. That's what makes every tab paste-ready.
- **Make every row traceable** with a source reference column such as
  `Policy p.12 #4` or `Fleet list row 17`.
- **Account for every record exactly once.** Categories are mutually exclusive:
  a record in "Needs Review" is not also in "Only on Policy". The summary shows
  a check that each source's total equals the sum of its buckets.
- **Derive with formulas.** In `xlsx_kit` helpers, wrap authored formulas in
  `Formula("=...")`; ordinary strings stay literal text. The `count_rows`,
  `sum_column`, and `balance_check` helpers already return `Formula` values.
  Counts, totals, differences, and sums on the summary sheet are formulas over the data tabs (for example
  `=COUNTA('Only on Policy'!A2:A1048576)`), so they update when the user
  deletes resolved rows. Use functions every Excel version and LibreOffice
  support: SUM, SUMIFS, COUNTA, COUNTIFS, IF, IFERROR, INDEX, MATCH. Avoid
  XLOOKUP, FILTER, UNIQUE, SORT, and other dynamic-array functions.

## Workbook layout

**Tabs, in order:** `Summary` first; then tabs that need action (discrepancies,
items to review, records missing from one side); then clean or matched records;
then source extracts, if included. Readers work top to bottom and left to right,
so the problems come first.

**Sheet names** are plain ASCII, 31 characters or fewer, and in the reader's
terms: `Only on Policy`, `Needs Review`, `Value Differences`. No emoji; they
break formula references and some import tools.

**Name sources by their role**, not their file type: "Policy" and "Insured
List", or "Travelers Schedule" and "Fleet List", rather than "PDF" and "XLSX".
Use the same names in headers, sheet names, and the chat reply.

**Column order:** the record's key and source reference first, then
descriptive fields, then amounts, then status, notes, and action columns. In
side-by-side comparisons, group each source's fields with a consistent prefix
(`Policy Year`, `List Year`) and put the differences after them.

**Summary sheet**, top to bottom:

- Title naming the insured and the deliverable, and a subtitle with the
  preparation date and source documents.
- Key facts: insured, policy number, carrier, policy period, valuation date,
  or whatever identifies the context.
- Results: one line per category with a count formula and a one-sentence
  meaning ("Scheduled on the policy but not on the insured's list").
- Checks: reconciliation formulas that show OK or MISMATCH.
- Method and assumptions: matching rules, normalizations, anything excluded or
  unreadable, and open questions.

**Worklists.** When the user will resolve items one by one, add a `Status` or
`Action` column with a dropdown (`add_dropdown`) and shade rows by status
(`color_rows`). Shading is conditional formatting, so it follows edits.

**File name:** `<Insured>_<Deliverable>_<YYYY-MM-DD>.xlsx` in ASCII with
underscores, for example `Ola_Inc_Vehicle_Schedule_Reconciliation_2026-09-11.xlsx`.

**Editing a user's existing workbook** is different: match its conventions,
tab names, and styling exactly instead of applying the house style.

## Using the kit

Add the skill's `scripts/` directory to `sys.path`, then build with typed
column definitions. Each `Column` kind handles coercion and number formats:
`text`, `id`, `integer`, `year`, `number`, `currency`, `percent`, `date`.

```python
import sys
sys.path.insert(0, "<skill-dir>/scripts")
from xlsx_kit import (
    Column, Section, SummaryLine, add_dropdown, add_table_sheet, balance_check,
    color_rows, column_letter, count_rows, new_workbook, sum_column, write_summary,
)

wb = new_workbook()
columns = [
    Column("Policy Veh #", "id"),
    Column("Source Ref", "id"),
    Column("Year", "year"),
    Column("Make / Model"),
    Column("VIN", "id"),
    Column("Cost New", "currency"),
    Column("Action"),
]
only_policy = add_table_sheet(wb, "Only on Policy", columns, policy_only_rows)
add_dropdown(only_policy, "Action", ["Add to list", "Remove from policy", "Sold", "Investigate"])

write_summary(
    wb["Summary"],
    title="Ola, Inc - Vehicle Schedule Reconciliation",
    subtitle="Prepared 09/11/2026 from Travelers policy BA-0P411880 and 2026 Fleet List.xlsx",
    sections=[
        Section("Key facts", [SummaryLine("Policy period", "02/09/2026 - 02/09/2027")]),
        Section(
            "Results",
            [
                SummaryLine(
                    "Only on policy",
                    count_rows(only_policy.title),
                    "Scheduled on the policy but not on the insured's list",
                ),
                SummaryLine(
                    "Cost new of those vehicles",
                    sum_column(only_policy.title, column_letter(only_policy, "Cost New")),
                    number_format="$#,##0",
                ),
            ],
            column_headers=("Category", "Count", "What it means"),
        ),
    ],
)
wb.save(path)
```

For quote and coverage comparisons, where each row has its own type and
cells may say "Excluded", use `add_matrix_sheet` with `MatrixGroup` and
`MatrixRow` instead; `baseline="Expiring"` shades cells that differ from the
expiring program. See blueprint 4.

`add_table_sheet` accepts rows as dicts keyed by header or as lists in column
order. Values it can't convert to the column's kind (say, "TBD" in a currency
column) are kept as text and listed on stderr. Fix them at the source: blank
the cell and move the note to a Notes column.

For reconciliation checks, use `balance_check` to compare each source's total
with its buckets. Matched and review rows each hold one record from each side,
so they count toward both sources:

```python
SummaryLine(
    "Policy vehicles accounted for",
    balance_check(94, [count_rows("Matched"), count_rows("Needs Review"),
                       count_rows("Only on Policy")]),
    "94 vehicles extracted from the policy schedule",
)
```

The total is the literal number of records extracted (say where it came from
in the note), or `count_rows` over a source extract tab.

## Reconciling two lists

`scripts/reconcile.py` matches records in fixed tiers: exact primary key
(VIN, serial), then a secondary key that is unique on both sides (year + unit
number, say), then near-miss keys one or two characters apart and shared
secondary keys, which go to review rather than being matched automatically.
Every record ends up in exactly one bucket, duplicates within each source are
reported, and `accounted_for` verifies the totals.

```python
from reconcile import normalize_key, normalize_vin, reconcile

result = reconcile(
    policy_vehicles,
    fleet_vehicles,
    primary=lambda r: normalize_vin(r.get("VIN")),
    secondary=lambda r: (r.get("Year"), normalize_key(r.get("Make"))[:4], r.get("Unit")),
    primary_label="VIN",
    secondary_label="Year + Make + Unit",
    vin_keys=True,  # Road vehicles only; leave False for equipment serials.
    source_names=("policy", "list"),  # Used in review notes shown to the user.
)
assert result.accounted_for(len(policy_vehicles), len(fleet_vehicles))
```

Choose the secondary key from what the user said to match on. With
`vin_keys=True`, each review pair's `detail` notes which side fails the VIN
check digit when only one does, which usually identifies the typo;
`vin_model_years` shows which model years a VIN supports. Duplicates within a source (the same
VIN on two units) go on their own tab; they are data-quality problems in their
own right.

Domain conventions for VINs, serials, amounts, limits, and dates are in
`references/insurance-data.md`.

## The chat reply

- Lead with the file name and the headline counts. Take every number from the
  same data structure that built the workbook, so the reply and the file
  can't disagree.
- List the items most likely to need action, up to about ten, in a short
  table or bullets, and point to the tab that holds the rest.
- State assumptions and limitations plainly: records that couldn't be read,
  matching rules chosen on the user's behalf, anything left out.
- When the user asks for something "I can paste into Excel", the data tabs
  already are paste-ready; say so. If the list is short (about 50 rows or
  fewer), also include it as a tab-separated code block in the reply.
- Don't repeat the whole workbook in chat.

## When to ask

Most requests are short ("put this in excel", "compare these two lists").
Don't interview the user: choose sensible defaults, apply them, and record them
in the summary's Method section and the reply. Ask only when the answer changes
the workbook's structure and can't be inferred, such as which of three uploaded
schedules is the policy of record.

## Reference files

- `references/blueprints.md`: tab structures, columns, summary metrics, and
  formulas for each deliverable type (reconciliation, schedule extraction, loss
  run summary, quote comparison, follow-up subset).
- `references/insurance-data.md`: field conventions and normalization rules
  for VINs, serial numbers, policy and claim numbers, amounts, limits,
  deductibles, dates, and statuses.
- `scripts/xlsx_kit.py`: house style, typed list tabs, comparison matrices,
  summary sheet, and formula helpers.
- `scripts/reconcile.py`: deterministic two-list matching, VIN normalization,
  check-digit validation, and model-year decoding.
- `scripts/recalc.py`: caches formula results with LibreOffice, verifying the
  data survived the round trip.
- `scripts/check_workbook.py`: read-only checker for the data and layout rules;
  exits 1 on errors.
