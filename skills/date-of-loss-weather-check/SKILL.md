---
name: date-of-loss-weather-check
metadata:
  furtherai-display-name: Date of Loss Weather Check
description: Check whether NOAA recorded hail, wind, tornado, flood or lightning near a claimed loss location around the reported date of loss. Use on property, auto physical damage or inland marine claims that blame weather, when verifying a date of loss, or when someone asks whether a storm really hit a place on a given day.
---

# Date-of-Loss Weather Check

Verifies a weather-related claim against NOAA's Storm Events Database: did the claimed peril actually occur near the location around the date of loss, and was it severe enough to cause the damage? The tables in `references/` are the source of truth; never say a storm did or didn't happen from memory.

| File | What it holds |
|---|---|
| `references/storm_events_states_01_19.csv`, `references/storm_events_states_20_39.csv`, `references/storm_events_states_40_99.csv` | Every NOAA-recorded hail, thunderstorm wind, high/strong wind, tornado, flash flood, flood, heavy rain and lightning event, Jan 2022 – Jun 2026, split by the first two digits of the area FIPS (the state code) |
| `references/zip_to_county.csv` | ZIP code → county FIPS (Census 2020) |

Each event row has the begin and end dates, the area (`area_type` C = county, Z = NWS forecast zone), event type, magnitude, tornado rating, the event's starting latitude/longitude, NOAA's damage estimate and its event ID.

## 1. Collect the claim facts
- Loss address or ZIP code.
- Reported date of loss.
- Claimed peril (hail, wind, tornado, flood, lightning).
- What was damaged: roof, siding, vehicle, interior water. This decides the severity threshold in step 4.

## 2. Find the events
1. Look up the county FIPS in `zip_to_county.csv`. If the ZIP spans counties, check each one.
2. Open the storm file for that state's FIPS code (the first two digits) and filter to the county (`area_type` = C, `area_fips` = the county FIPS).
3. Keep events of the claimed peril within **3 days either side** of the date of loss. Then look **60 days either side** for every damaging event of that peril, so a stronger event just outside the 3-day window isn't missed.
4. **Forecast zones:** wind and flood events are often recorded by forecast zone (`area_type` = Z) rather than county. If nothing is found at county level, look for Z rows in the same state whose `area_name` matches the county or its part of the state, and say you matched by zone.
5. **Distance:** if you have the loss location's coordinates, prefer events whose `begin_lat`/`begin_lon` are within about 10 miles; give the distance.

## 3. Read the severity
- **Hail:** `magnitude` is the stone diameter in inches.
  - Under 1" rarely damages roofs or vehicles.
  - 1–1.75" can damage asphalt shingles, gutters and soft metals.
  - 1.75" and up damages most roofing and vehicles.
- **Wind:** `magnitude` is the gust speed in knots (`magnitude_type` E = estimated, M = measured).
  - Under 50 kt (57 mph) rarely causes structural damage.
  - 50–64 kt can damage shingles, fences and trees.
  - 65 kt and up (hurricane force) can cause significant structural damage.
- **Tornado:** `tornado_rating` (EF0–EF5).
- **Flood, flash flood, heavy rain, lightning:** no magnitude. Use the date match and the damage estimate.

## 4. Decide
| Result | When |
|---|---|
| **Supported** | The claimed peril was recorded in the county (or a matching zone) within 3 days of the date of loss, at a severity that can cause the claimed damage |
| **Weakly supported** | The peril was recorded nearby in time, but below the damage threshold, more than 10 miles away, or only matched by zone |
| **Not supported** | No event of the claimed peril within 3 days. Give the nearest event before and after the date of loss. |
| **Date discrepancy** | No event on the reported date, but a qualifying event within 30 days. The real date of loss may differ, which matters for policy period, late reporting and prior damage. |

**Not supported doesn't mean the claim is false.**
- NOAA relies on spotter reports, so rural areas are under-reported.
- Wind damage can occur below NWS report thresholds.
- Data ends **June 30, 2026**. Losses after that can't be checked here.

Say so whenever the result is Not supported, and recommend a property-level weather report (radar-based hail and wind history) before any coverage action.

## Confidence
- **High:** a county-level event with coordinates close to the loss location, or an exact county with no events in a well-reported area.
- **Medium:** matched by zone or county only, with no coordinates; or a ZIP that spans counties.
- **Low:** the location is unclear, the date of loss is a range, or the loss is after June 30, 2026. Say what would resolve it.

## Output
```
Claim: <peril> at <address/ZIP> on <date of loss>
Result: Supported | Weakly supported | Not supported | Date discrepancy
Confidence: High | Medium | Low
Why: <one or two sentences>

Closest matching events:
- <date> <event type> <magnitude> in <county/zone>, <distance if known> (NOAA event <id>)
- ...
Nearest events outside the window: <before: date and type; after: date and type>

Alternatives considered: <other dates or perils the record points to, e.g. "hail on May 2, not May 14", or "wind, not hail"; omit when none>
Flags: <date discrepancy, damage claimed exceeds recorded severity, zone-only match, data gaps, or "none">
To raise confidence: <exact address or coordinates, a narrower date of loss, a property-level weather report; only when Medium or Low>
Source: NOAA Storm Events Database, Jan 2022 – Jun 2026
```
Return it as JSON with these fields when the caller asks for structured output.

This is evidence for the adjuster, not a coverage decision. Never recommend denying a claim on this check alone.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets) that turns the result into practical guidance for this insured and the person asking: what it means for this specific business, operation, claim or location; what to check or ask next; and anything relevant the data doesn't cover. Label anything that comes from your own knowledge rather than the tables, so the reader can tell evidence from judgment. Never let your own knowledge override a value in the tables; if they seem to conflict, say so.

Omit any output field that has nothing to say (write nothing rather than "N/A").

**For this skill specifically:**
- Besides the nearest events, list every **damaging** event of the claimed peril (hail of 1" or more, or wind of 50 kt or more) within **60 days either side** of the date of loss, largest first. A stronger event just outside the window is often the real date of loss.
- Suggest what to verify: whether the policy was in force on each candidate date, prior claims for the same damage, and a property-level hail/wind report.
