---
name: location-hazard-history
metadata:
  furtherai-display-name: Location Hazard History
description: Summarize a location's history of severe weather and federal disaster declarations (hail, wind, tornado, flood, wildfire, hurricane and more) from NOAA and FEMA records, ranked against every U.S. county. Use when underwriting or reviewing property, auto physical damage or builders' risk exposures, screening a schedule of locations, or when someone asks how exposed an address or ZIP code is to natural catastrophes.
---

# Location Hazard History

Gives a county-level picture of how often severe weather and declared disasters have hit a location, and how that compares with the rest of the country. The tables in `references/` are the source of truth; don't describe a place's weather history from memory.

| File | What it holds |
|---|---|
| `references/zip_to_county.csv` | Each ZIP code's county (Census 2020 ZIP-code areas), with the share of the ZIP's land in that county |
| `references/storm_events_by_county_2016_2025.csv` | NOAA Storm Events for 2016–2025 by county and peril: event counts, days with events per year, national percentile, largest hail or wind, strongest tornado, property damage, deaths, injuries |
| `references/disaster_declarations_by_county_2006_2026.csv` | FEMA disaster declarations since 2006 by county and incident type, with the latest one |

## 1. Find the county
- **From an address:** use its ZIP code to look up the county in `zip_to_county.csv`. Use the county named in the address if it's given.
- **ZIP in several counties:** the ZIP has several rows, so use the county with the largest `land_share` and say the location may sit in another one.
- **ZIP not found:** PO boxes and single-building ZIPs often aren't in the Census list. Use the city and state to identify the county, and lower confidence.
- **County FIPS codes** are 5 digits (state + county), e.g. `48113` is Dallas County, TX.

## 2. Read the severe-weather history
For each peril row of the county in `storm_events_by_county_2016_2025.csv`:
- **`event_days_per_year`:** how often the peril hits; days with at least one event, averaged over 10 years.
- **`national_percentile`:** where that frequency ranks among all U.S. counties. Counties with no recorded events are counted, so 90 means more frequent than 90% of counties.
- **`max_magnitude`:** largest hail in inches, or strongest thunderstorm wind gust in knots.
- **`max_tornado_rating`:** strongest tornado (EF0–EF5).
- **`property_damage_usd`:** NOAA's damage estimate, all events combined. It's rough and often blank, so use it for scale only.

A county missing from a peril's rows had no recorded events of that type.

## 3. Add the declared disasters
From `disaster_declarations_by_county_2006_2026.csv`, take the county's rows and its state's `Statewide` rows (FIPS ending in `000`).
- **`declaration_type`:**
  - DR = major disaster;
  - EM = emergency;
  - FM = fire management assistance, granted while a wildfire is burning.
- **Hurricanes, coastal storms, wildfires and severe winter storms** rarely show up in the county storm table. NOAA records them by forecast zone rather than county, so declarations are the main evidence for those perils.
- **Map incident types to perils:**
  - Severe Storm, Tornado and Straight-Line Winds → hail, wind and tornado.
  - Flood, Dam/Levee Break and Mud/Landslide → flood.
  - Hurricane, Tropical Storm, Typhoon and Coastal Storm → hurricane.
  - Fire → wildfire.
  - Winter Storm, Snowstorm, Severe Ice Storm and Freezing → winter storm.
  - Earthquake and Tsunami → earthquake.
- **Ignore Biological declarations** (COVID-19) for property hazard.

## 4. Rate each peril
| Rating | When |
|---|---|
| **High** | 90th percentile or above, or 3 or more major-disaster (DR) declarations for that peril since 2006 |
| **Elevated** | 75th–89th percentile, or a major-disaster (DR) declaration for that peril whose `latest_date` is within the last 10 years |
| **Average** | 25th–74th percentile |
| **Low** | below the 25th percentile and no declarations for that peril |

Raise a rating one step for severity the frequency doesn't show: hail of 2 inches or more, an EF3+ tornado, or deaths. Say why when you do.

The **overall** rating is the highest peril rating. Name the peril or perils that drive it.

## Limits to state when they matter
- This is county history. It says nothing about a specific building's elevation, distance to the coast, flood zone, roof, or construction. Large counties (Los Angeles, Maricopa) vary a lot from one end to the other.
- Hail and wind reports come from spotters, so rural counties are under-reported compared with cities.
- Ten years is a short window for rare perils such as earthquakes and major hurricanes. An absence of events isn't proof of low risk.
- Earthquake isn't in the storm table. Use declarations, and say the history doesn't measure seismic risk.

## Confidence
- **High:** county identified exactly (county given, or the ZIP is entirely in one county).
- **Medium:** the ZIP spans counties, or the county is a very large one.
- **Low:** the county was inferred from the city or the ZIP wasn't found. Say what would pin it down.

## Output
One block per location:
```
Location: <address or ZIP> → <county>, <state> (FIPS <code>)
Overall: High | Elevated | Average | Low, driven by <peril(s)>
Confidence: High | Medium | Low
Why: <one or two sentences>

Perils:
- Hail: <rating>, <days/yr> days a year (<percentile>th percentile), largest <inches>"
- Thunderstorm wind: <rating>, <days/yr> (<percentile>th), strongest <knots> kt
- Tornado: <rating>, <events> since 2016, strongest <EF rating>
- Flood / flash flood: <rating>, <days/yr> (<percentile>th)
- Declared disasters since 2006: <count by type, e.g. 4 hurricane, 2 flood>, latest <title> (<date>)
(omit perils with no history and no declarations)

Alternatives considered: <other counties the ZIP touches, and how their rating differs; omit when the county is exact>
Flags: <limits from above that apply, schedule locations outside the county, or "none">
To raise confidence: <full street address, county, or a property-level hazard score; only when Medium or Low>
Source: NOAA Storm Events 2016–2025; FEMA disaster declarations through Sept 25, 2026; Census 2020 ZIP–county relationship file
```
For a schedule of many locations, give a one-line summary per location (county, overall rating, driving peril), then full blocks only for High locations. Return it as JSON with these fields when the caller asks for structured output.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets) that turns the result into practical guidance for this insured and the person asking: what it means for this specific business, operation, claim or location; what to check or ask next; and anything relevant the data doesn't cover. Label anything that comes from your own knowledge rather than the tables, so the reader can tell evidence from judgment. Never let your own knowledge override a value in the tables; if they seem to conflict, say so.

Omit any output field that has nothing to say (write nothing rather than "N/A").

**For this skill specifically:**
- Tie each rated peril to the insured's occupancy and construction. For example, empty steel grain bins buckle in high wind; hail damages corrugated metal roofs, and cosmetic-damage exclusions matter; warehouses with large flat roofs carry snow-load and ponding risk.
- Name the perils the data doesn't measure and give your view on them, labeled as such: wildfire (unless there are Fire declarations), earthquake, lightning, and storm surge.
- End with 3–5 underwriting questions for this location (roof age and type, deductible structure, protection, inventory peaks).
