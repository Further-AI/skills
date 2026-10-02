---
name: wildfire-risk
metadata:
  furtherai-display-name: Wildfire Risk
description: Rate wildfire exposure for a community, ZIP code or county using the USDA Forest Service "Wildfire Risk to Communities" data — risk to homes and burn probability as national and state percentiles, and the share of buildings directly or indirectly exposed to wildfire. Use when underwriting or reviewing property, builders' risk or homeowners-style exposures anywhere in the US (not just California), screening a schedule for wildfire accumulation, setting wildfire deductibles or exclusions, or when someone asks how wildfire-prone a place is.
---

# Wildfire Risk

These scores come from the Forest Service's national wildfire model. They're shown on an interactive map, but web search can't read them, so the model would otherwise guess. The tables in `references/` are the source of truth; don't estimate wildfire risk scores from memory.

| File | What it holds |
|---|---|
| `references/wildfire_risk_by_community.csv` | About 32,000 communities (cities, towns and census-designated places) |
| `references/wildfire_risk_by_county.csv` | Every county (about 3,100) |
| `references/zip_to_community.csv` | ZIP → community, with the share of the ZIP's land in each community (only shares of 5% or more) |
| `references/zip_to_county.csv` | ZIP → county |

Columns:
- **`risk_to_homes_national_pct` / `_state_pct`:** where the area ranks on risk to homes, which combines how likely a fire is with how intense it would be. **This is the headline score.**
- **`burn_probability_national_pct` / `_state_pct`:** where the area ranks on the annual likelihood of burning, whatever the intensity.
- **`pct_buildings_direct_exposure`:** buildings next to or within burnable vegetation, which can be reached by the flame front.
- **`pct_buildings_indirect_exposure`:** buildings that embers or home-to-home spread could reach.
- **`pct_buildings_minimal_exposure`:** buildings unlikely to be exposed.
- **`insufficient_data`:** "Yes" means the Forest Service couldn't score the area; fall back to the county.

Source: USDA Forest Service, Wildfire Risk to Communities, April 2026 release. Scores describe the **area**, not a specific building.

## 1. Find the area
1. Look the ZIP up in `zip_to_community.csv`. If a community covers most of the ZIP (`zip_land_share` 0.5 or more), use its row in `wildfire_risk_by_community.csv`.
2. **Unincorporated areas:** rows with a blank community are land outside any community, which is common in the wildland–urban interface. Use the county from `zip_to_county.csv`, and say the location is unincorporated. Unincorporated land in a fire-prone county is often riskier than the towns.
3. Given a city name, search the `name` column directly (e.g. "Soquel, CA").
4. Always give the county row too, for context.

## 2. Rate it
Use risk to homes (national percentile) as the main signal:

| Rating | When |
|---|---|
| **High** | 90th percentile or above |
| **Elevated** | 70th–89th percentile |
| **Average** | 40th–69th percentile |
| **Low** | Below the 40th percentile |

- **Raise a rating one step** when more than 50% of buildings are directly exposed, or the state percentile is 90+ while the national percentile isn't. Say why.
- **Accumulation:** for a schedule of locations, total the values in High and Elevated areas by county.

## Confidence
- **High:** a community covering most of the ZIP, with sufficient data.
- **Medium:** rated from the county (unincorporated, or the ZIP split across communities).
- **Low:** the location is uncertain, or the data is insufficient. Say what would resolve it.

## Output
```
Location: <address/ZIP/community> → <community or "unincorporated">, <county>, <state>
Wildfire risk: High | Elevated | Average | Low
Confidence: High | Medium | Low
Why: <one or two sentences naming the deciding numbers>

Community: risk to homes <n>th percentile nationally (<n>th in state); burn probability <n>th nationally
Buildings exposed: <direct>% direct, <indirect>% indirect, <minimal>% minimal
County: risk to homes <n>th percentile nationally (<n>th in state)

Alternatives considered: <other communities the ZIP touches, if their scores differ meaningfully; omit if none>
Flags: <unincorporated, insufficient data, area-level not building-level, or "none">
To raise confidence: <full street address, parcel-level wildfire score, defensible-space inspection>
Source: USDA Forest Service, Wildfire Risk to Communities, April 2026
```
For a schedule of locations, give one line per location (community or county, rating, risk percentile), then full blocks only for High locations. Return it as JSON with these fields when the caller asks for structured output.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets):
- what the score means for this insured's occupancy and construction;
- what to ask: roof class, vents, siding, defensible space, distance to wildland, fire department and water supply, state fire-hazard maps such as CAL FIRE hazard severity zones, and wildfire mitigation credits;
- the coverage implications: wildfire deductibles, sublimits or exclusions, and market availability.

Label anything that comes from your own knowledge rather than the tables. Never let your own knowledge override a value in the tables.

Omit any output field that has nothing to say.
