---
name: distance-to-coast
metadata:
  furtherai-display-name: Distance to Coast
description: Give a ZIP code's distance to the coast (Atlantic, Gulf, Pacific, Caribbean) and to the Great Lakes, measured consistently from the ZIP's center to the Census coastline, with the standard distance band (under 1 mile, 1–5, 5–10, 10–25, 25–50, 50–100, over 100). Use when underwriting property or builders' risk, checking wind/hurricane or named-storm eligibility, applying coastal appetite or deductible rules, ranking a schedule by coastal exposure, or when someone asks how far a location is from the coast.
---

# Distance to Coast

Carriers' coastal appetite, wind deductibles and named-storm rules depend on distance to the coast. Estimates vary with what counts as "coast", so this skill gives one consistent measure for every ZIP code. The table in `references/` is the source of truth; don't estimate distances from memory.

| File | What it holds |
|---|---|
| `references/zip_distance_to_coast.csv` | All 33,791 ZIP codes (Census 2020 ZIP code areas): centroid, miles to the nearest coast, which coast, distance band, and miles to the nearest Great Lakes shoreline |

**How it's measured:**
- **From:** the ZIP's interior center point, as published by Census.
- **To:** the Census TIGER coastline, which includes the open coast, **bays and sounds** (e.g. Chesapeake Bay, Tampa Bay, San Francisco Bay) but **not tidal rivers** (e.g. the Cape Fear, Savannah, Caloosahatchee or Delaware rivers).
- **Projection:** distances use a lower-48 equal-area projection, so they're precise to about a mile near the coast in the lower 48. Treat long distances in Alaska and Hawaii as approximate.

## 1. Look it up
Find the ZIP. If you have the exact address, say the ZIP-center distance can differ from the building's by several miles in large ZIPs.

## 2. Apply the customer's rule when there is one
Coastal rules differ by carrier and program. Common forms are "no wind within 1 mile of tidal water", "named-storm deductible within 5 miles" and "coastal counties". If the user or the carrier's guidelines give a distance rule:
- compare against it;
- say which side of the line the location falls on;
- **flag locations within 2 miles of a threshold** as "confirm with the exact address", since ZIP-center distance can't settle them.

If the rule is measured to **tidal water**, warn that a location near a tidal river may be much closer to tidal water than this table shows. Name the river if you know it, and label that as your own knowledge.

## 3. Rate it (when there's no customer rule)
| Rating | When |
|---|---|
| **High** | Under 5 miles |
| **Elevated** | 5–25 miles |
| **Moderate** | 25–100 miles (hurricane wind can still reach well inland) |
| **Low** | Over 100 miles |

Mention Great Lakes distance separately when it's under 5 miles. It matters for wind, ice and lakeshore flood, but not hurricane rules.

## Confidence
- **High:** ZIP found, and well away from any threshold.
- **Medium:** within 2 miles of a threshold, or a large rural ZIP.
- **Low:** ZIP not found, or the rule is about tidal rivers. Say what would resolve it.

## Output
```
Location: <ZIP> (<city/area if known>)
Distance to coast: <miles> miles to the <coast> coast (<band>)
Coastal exposure: High | Elevated | Moderate | Low | <the customer's rule result, e.g. "inside the 5-mile named-storm zone">
Confidence: High | Medium | Low
Why: <one sentence>

Great Lakes: <miles> miles (only if under 25)

Alternatives considered: <e.g. "tidal Cape Fear River is much closer than the coastline", if relevant; omit otherwise>
Flags: <near a threshold, tidal-river caveat, large ZIP, or "none">
To raise confidence: <exact address for a geocoded distance>
Source: Census 2020 ZIP code area centroids; Census TIGER 2023 coastline
```
For a schedule of locations, give one line per location (ZIP, miles, band), sorted nearest first. Return it as JSON with these fields when the caller asks for structured output.

## Add your own expertise
The table above is the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets):
- what the distance means for this insured: wind/hail and named-storm deductibles, windpool or FAIR-plan eligibility, and storm surge;
- what to ask: elevation, construction and roof, opening protection, and wind mitigation credits;
- what the data doesn't cover: tidal rivers, elevation, and surge zones.

Label anything that comes from your own knowledge rather than the table. Never let your own knowledge override a value in the table.

Omit any output field that has nothing to say.
