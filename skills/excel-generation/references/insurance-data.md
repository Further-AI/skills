# Insurance data conventions

Field conventions that change what goes into a cell. Read the section for the
fields you're handling.

## Contents

1. Identifiers
2. VINs
3. Amounts, limits, and deductibles
4. Dates
5. Statuses and controlled values
6. Normalizing for matching

## 1. Identifiers

Store every identifier as text (`Column(..., "id")`), exactly as the source
prints it, and normalize only for matching (`normalize_key`).

- **Policy numbers** keep their prefixes, suffixes, and term segments
  (`BA-0P411880-26-93-G`).
- **Claim, unit, tag, item, and location numbers**: "Loc 1 / Bldg 2" often
  appears as `1-2`; keep it as written or split it into Loc # and Bldg #
  columns.
- **ZIP codes** are 5 or 9 digits as text, so `02134` survives.
- **FEIN** is written `NN-NNNNNNN`.
- **Class codes**: workers' compensation class codes are four digits and some
  start with zero (`0042`); GL class codes are five digits. Always text.
- **Equipment serial numbers and product identification numbers** vary by
  manufacturer. A 17-character equipment PIN is not a VIN, so don't apply VIN
  checks to it.
- **Driver's license numbers** are personal data; include them only when the
  user needs them.

## 2. VINs

- Road vehicles from model year 1981 on have 17-character VINs. Older
  vehicles, homemade trailers, and some imports have shorter serials; a short
  serial isn't an error by itself.
- VINs never contain I, O, or Q. `normalize_vin` maps them to 1, 0, and 0 in
  17-character VINs, since they are almost always misreads.
- Position 9 is a check digit for North American vehicles.
  `vin_check_digit_ok` returns False when it doesn't validate, which is strong
  evidence of a typo. When two VINs differ by a character and only one passes,
  that one is probably right. Pass `vin_keys=True` to `reconcile` so review
  notes include this.
- Position 10 encodes the model year on a 30-year cycle. `vin_model_years`
  returns the two candidates (for example 1998 and 2028); a stated year
  matching neither is a discrepancy worth flagging.
- Show VINs as the source prints them. Report a suspected typo in a Notes,
  Evidence, or Data Issues column; don't correct the source value.

## 3. Amounts, limits, and deductibles

- **Formats:** `$#,##0` for limits, values, and TIV; `$#,##0.00` for premiums,
  taxes, fees, and claim payments.
- **Split limits** into separate columns or rows: "1,000,000/2,000,000" is Each
  Occurrence and Aggregate. Auto split-limit shorthand is in thousands: 100/300/100
  means $100,000 bodily injury per person, $300,000 bodily injury per accident,
  and $100,000 property damage. A combined single limit is one number.
- **Percentage deductibles** (a 2% wind/hail deductible) are stored as
  percentages (0.02) with their basis in a note ("of location TIV"); a dollar
  minimum goes in its own column or row.
- **Valuation basis matters.** Cost New, Stated Amount, Actual Cash Value,
  Replacement Cost, and Agreed Value aren't interchangeable. Keep the source's
  term in the header ("Policy Cost New", "List Stated Value") or add a Basis
  column. When a reconciliation compares different bases, say so in Method
  rather than implying one side is wrong.
- **Text states** such as "Included", "Excluded", "Not covered", "Declined",
  "Not quoted", or a limit of "ACV" belong in their own column on list tabs
  (for example "Limit Basis"); in comparison matrices they can sit in the
  cell. A blank cell means the document didn't say, which is different from
  "Excluded".

## 4. Dates

- Store dates as dates, displayed `mm/dd/yyyy`. Store model year and year built
  as integers (`"year"` kind).
- Write a policy period as two date columns, Effective and Expiration. Use a
  text label such as "2025-2026" when grouping by term.
- A loss run's valuation date belongs on the Summary. Flag valuations more than
  90 days old.
- Compute ages and durations against an as-of date stored in a labeled cell,
  not `TODAY()`, which changes every time the file is opened and makes the
  numbers irreproducible.

## 5. Statuses and controlled values

Values that formulas count on may be normalized to a fixed vocabulary, as an
exception to keeping source values verbatim. Record the mapping in Method.

- Claim status: Open, Closed, Reopened.
- Yes/No fields: "Yes" and "No", not Y, TRUE, or checkmarks.
- Lines of coverage: GL, Auto, APD (auto physical damage), WC, Property, IM
  (inland marine), CE (contractors equipment), Umbrella, Excess.

## 6. Normalizing for matching

- `normalize_key` removes case, spacing, and punctuation differences from
  identifiers.
- Carrier schedules often merge make and model into one field ("PETERBILT
  386") while insured lists separate them. Build secondary keys from fields
  both sources reliably have, such as year plus the first four letters of the
  make, or year plus unit number.
- Common make variants to treat as equal when comparing (extend as needed):

| Canonical | Variants seen |
| --- | --- |
| Chevrolet | Chevy, Chev, Chevorlet |
| Peterbilt | Pete, Peterbuilt |
| International | Intl, IH, Navistar, Internatio |
| Freightliner | FRHT, Freightlnr |
| Kenworth | KW |
| Ram | Dodge Ram (Ram is its own make from model year 2011) |
| John Deere | JD, Deere |
| Caterpillar | Cat |
| Case | Case IH |

- The first four letters of the normalized make is a serviceable fallback
  when no alias list covers a make.
- A year that differs between sources for the same VIN is a difference to
  report; check `vin_model_years` to see which year the VIN supports.
