---
name: flood-claim-history
metadata:
  furtherai-display-name: Flood Claim History
description: Show how often a ZIP code or county has had NFIP (National Flood Insurance Program) flood claims since 1978 — total claims, recent claims, dollars paid, worst year, how many years had claims, how much of it was outside FEMA high-risk zones — ranked against the rest of the country. Use when underwriting or reviewing property, builders' risk, inland marine or flood exposures, deciding whether to require flood coverage or a flood sublimit, screening a schedule of locations, or when someone asks how flood-prone a place really is.
---

# Flood Claim History

NFIP claim history is the best free evidence of where it actually floods, including outside FEMA's mapped high-risk zones. It isn't published per ZIP anywhere a web search can reach. The tables in `references/` are the source of truth; don't estimate claim counts from memory.

| File | What it holds |
|---|---|
| `references/flood_claims_by_zip.csv` | One row per ZIP code with at least one NFIP claim (about 26,000 ZIPs) |
| `references/flood_claims_by_county.csv` | The same measures by county FIPS code (about 2,900 counties) |
| `references/zip_to_county.csv` | ZIP → county, for the county fallback |

Columns:
- **`claims_all_years`, `claims_since_2016`:** NFIP claims filed, by year of loss.
- **`paid_all_years_usd`, `paid_since_2016_usd`:** building plus contents payments.
- **`years_with_claims`:** how many different years had at least one claim (1978–2026). Many years means chronic flooding; one huge year means a single catastrophe.
- **`share_in_high_risk_zone`:** % of claims on properties rated in FEMA A or V zones. A low share means much of the flooding happened **outside** mapped high-risk zones.
- **`worst_year`, `worst_year_claims`:** the single worst year.
- **`national_percentile`:** rank by total claims among ZIPs (or counties) that have any claims.

Data covers losses from **1978 to June 1, 2026** (FEMA froze this dataset version then).

**What the data doesn't cover:**
- It counts only **NFIP-insured** losses. Most commercial and many residential buildings have no NFIP policy, so a low count can mean low take-up, not low flood risk.
- **A ZIP that isn't in the table had no recorded NFIP claims.** It doesn't mean no flood risk.

## 1. Find the area
- Look up the ZIP in `flood_claims_by_zip.csv`.
- Also look up its county (via `zip_to_county.csv`) in `flood_claims_by_county.csv` for context, since a ZIP can be quiet inside a flood-heavy county.

## 2. Read the pattern
- **Chronic:** claims in 20+ different years, or more than 100 claims since 2016.
- **Catastrophe-driven:** most claims in the worst year, e.g. Sandy in 2012, Harvey in 2017 or Ian in 2022. Ask whether the property was affected then, and what's been done since.
- **Outside mapped zones:** `share_in_high_risk_zone` under 50% means flood has repeatedly hit properties FEMA rates as moderate or low risk. Don't rely on the flood zone alone.

## 3. Rate it
| Rating | When |
|---|---|
| **High** | 90th percentile or above, or 100+ claims since 2016 |
| **Elevated** | 75th–89th percentile, or claims in 15+ different years |
| **Average** | 25th–74th percentile |
| **Low** | Below the 25th percentile, or not in the table |

Raise a rating one step when `share_in_high_risk_zone` is under 50%; say why.

## Confidence
- **High:** a ZIP with 25 or more claims.
- **Medium:** a ZIP with fewer than 25 claims, or rated from the county only.
- **Low:** the ZIP isn't in the table and the county has few claims, or the location is uncertain.

## Output
```
Location: <ZIP> (<county>, <state>)
Flood claim history: High | Elevated | Average | Low
Confidence: High | Medium | Low
Why: <one or two sentences naming the deciding numbers>

ZIP: <claims> NFIP claims since 1978 (<since 2016> since 2016), $<paid> paid; claims in <n> different years; worst year <year> (<claims>)
Outside high-risk zones: <100 − share>% of claims
County: <claims> claims, <percentile>th percentile nationally

Alternatives considered: <neighboring ZIPs or the county pattern if they differ meaningfully; omit if none>
Flags: <catastrophe-driven history, low NFIP take-up likely, ZIP not in table, or "none">
To raise confidence: <exact address, FEMA flood zone determination, elevation certificate, prior loss history>
Source: FEMA OpenFEMA NFIP redacted claims, 1978 – June 1, 2026
```
For a schedule of locations, give one line per location (ZIP, rating, claims since 2016), then full blocks only for High locations. Return it as JSON with these fields when the caller asks for structured output.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets): what the history means for this insured's property and business income exposure, whether to require flood coverage, a flood sublimit or deductible, what to ask (FEMA flood zone, elevation certificate, first-floor height, basement contents, stock on the floor, prior flood losses, flood mitigation), and what the data doesn't cover (non-NFIP commercial losses, pluvial/flash flooding without NFIP claims). Label anything that comes from your own knowledge rather than the tables. Never let your own knowledge override a value in the tables.

Omit any output field that has nothing to say.
