---
name: commercial-auto-check
metadata:
  furtherai-display-name: Commercial Auto Check
description: Validate a commercial auto fleet schedule by decoding every VIN with NHTSA, and review a motor carrier's FMCSA registration and safety record. Use when underwriting or auditing commercial auto or trucking, checking a vehicle schedule, investigating an auto claim's vehicle, or when someone asks about a carrier's DOT number, safety rating or out-of-service rates.
---

# Commercial Auto Check

Two checks, run together or separately:

1. **Fleet schedule:** decode each VIN with the **NHTSA connector** (`vin_decode_batch`, up to 50 VINs per call) and compare what NHTSA says with what the schedule says.
2. **Carrier safety:** review the carrier's FMCSA registration and safety record against the thresholds in `references/fmcsa_rules.md`.

The FMCSA data comes from the carrier's **SAFER Company Snapshot** (safer.fmcsa.dot.gov). The app can't query FMCSA directly. Use a snapshot that's in the submission or attached by the user. If there isn't one, use web search for the carrier's snapshot and say that's how you got it. If neither works, ask for it: the user enters the DOT number at safer.fmcsa.dot.gov and saves the page as a PDF.

| File | What it holds |
|---|---|
| `references/fmcsa_rules.md` | Weight classes, when a USDOT number and operating authority are required, how to read the SAFER snapshot, and the safety thresholds that warrant referral |

## 1. Fleet schedule
1. **Collect** every vehicle on the schedule: VIN, year, make, model, body type, stated value, GVW or weight class, and garaging address.
2. **Check before decoding:**
   - A VIN must be 17 characters with no I, O or Q (for vehicles made since 1981).
   - Flag duplicates: the same VIN listed twice.
3. **Decode:** call `vin_decode_batch` with all VINs (50 at a time). Pass `model_year` only for pre-1981 VINs.
4. **Compare** each decoded row with the schedule. Flag:
   - **Invalid or undecodable VINs**: the row has `error`, or `error_code` isn't 0. Often a typo, sometimes a vehicle that doesn't exist.
   - **Year, make or model mismatch:** the schedule says 2022 Freightliner and NHTSA says 2015 Ford.
   - **Weight or body mismatch:** a "pickup" that decodes as a Class 8 truck tractor, or a "light van" with a GVWR over 10,000 lb. Weight class drives the rate.
   - **Trailers listed as power units, or the reverse.**
   - **Vehicle type outside the program's appetite:** dump trucks, tankers, buses and so on.
5. **Count** power units (trucks and tractors, not trailers) and the weight-class mix. The power-unit count is compared with FMCSA's records in step 2.

## 2. Carrier safety (FMCSA)
Read the SAFER snapshot and check it with `references/fmcsa_rules.md`:
- **Registration:**
  - Is the USDOT status Active?
  - For for-hire interstate carriers, is the operating authority (MC number) Active?
  - Does the operation (interstate, intrastate, for-hire, private) match the application?
  - Do the cargo types match?
- **Size:** compare the power units and drivers on the snapshot with the schedule and the application.
  - A schedule much **smaller** than the snapshot can mean unscheduled vehicles.
  - A schedule much **larger** can mean an outdated MCS-150 or leased-on units.
- **MCS-150 date:** carriers must update the registration every 2 years. An older date means stale data and a registration that may soon be deactivated.
- **Safety rating:**
  - Satisfactory, Conditional or Unsatisfactory, or none if never rated.
  - Conditional or Unsatisfactory is a red flag.
- **Out-of-service rates:** compare the carrier's vehicle, driver and hazmat rates with the **national averages printed on the same snapshot**. Note how many inspections they're based on; under about 10 isn't reliable.
- **Crashes:** fatal, injury and tow-away crashes in the last 24 months, against the power-unit count.

## 3. Rate it
| Result | When |
|---|---|
| **Refer** | Inactive DOT or authority, Conditional or Unsatisfactory rating, vehicle or driver out-of-service rate over 1.5× the national average (on 10+ inspections), any fatal crash, or schedule power units under 80% of the FMCSA count |
| **Review** | MCS-150 over 2 years old, out-of-service rates above average, VIN mismatches on more than 10% of the schedule, or operation type differs from the application |
| **Acceptable** | None of the above |

## Confidence
- **High:** every VIN decoded and a current snapshot was provided.
- **Medium:** the snapshot was found by web search, some VINs failed, or the inspection counts are small.
- **Low:** no FMCSA snapshot, or most VINs didn't decode. Say what's needed.

## Output
```
Carrier: <legal name>, USDOT <number> (MC <number>)
Result: Refer | Review | Acceptable
Confidence: High | Medium | Low
Why: <the deciding findings in one or two sentences>

Fleet: <n> vehicles on schedule, <power units> power units, <trailers> trailers; weight classes <mix>
VIN issues:
- <VIN>: <issue: invalid, duplicate, year/make/model or weight mismatch> (schedule says <x>, NHTSA says <y>)
FMCSA: status <active/inactive>, authority <active/none>, operation <type>, power units <n> (schedule <m>), drivers <n>, MCS-150 <date>
Safety: rating <rating or none>; out-of-service vehicle <x>% (national <y>%), driver <x>% (national <y>%); crashes 24 mo: <fatal/injury/tow>

Alternatives considered: <other carriers with similar names or DOT numbers, and why they were ruled out; omit when the DOT number was given>
Flags: <snapshot source and date, small inspection counts, VINs not decoded, or "none">
To raise confidence: <current SAFER snapshot, corrected VINs, MVRs, loss runs; only when Medium or Low>
Source: NHTSA vPIC (live, via connector); FMCSA SAFER Company Snapshot dated <date>
```
For long schedules, list only the vehicles with issues and give a count of clean ones. Return it as JSON with these fields when the caller asks for structured output.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets) that turns the result into practical guidance for this insured and the person asking: what it means for this specific business, operation, claim or location; what to check or ask next; and anything relevant the data doesn't cover. Label anything that comes from your own knowledge rather than the tables, so the reader can tell evidence from judgment. Never let your own knowledge override a value in the tables; if they seem to conflict, say so.

Omit any output field that has nothing to say (write nothing rather than "N/A").

**For this skill specifically:**
- For every weight-class mismatch, say what it does to rating (premium is usually understated when the class is understated) and whether the corrected class crosses the 10,000 lb USDOT threshold or the 26,001 lb CDL threshold.
- Flag serial numbers that look like placeholders (1234, 00000, repeated digits) even when the check digit is valid.
- End with the documents to request: registrations or titles for flagged units, and a current SAFER snapshot.
