---
name: mine-safety-record
metadata:
  furtherai-display-name: Mine Safety Record
description: "Look up a mine, quarry or sand-and-gravel pit in the MSHA (Mine Safety and Health Administration) records — its mine ID, operator, controller, commodity, status and headcount — and its safety record since 2021: violations, significant-and-substantial citations, unwarrantable-failure and imminent-danger orders, proposed penalties, accidents, fatalities and permanent disabilities. Use when underwriting or auditing workers' comp, GL, auto or umbrella for mining, quarrying, aggregates, sand and gravel, cement or crushed-stone operations, or when someone asks about a mine's MSHA history."
---

# Mine Safety Record

Mines are inspected by MSHA, not OSHA, so an OSHA search says nothing about them. This skill holds MSHA's records for about 22,000 mines that are active, recently active, or have violations or accidents since 2021. The tables in `references/` are the source of truth; don't describe a mine's record from memory.

| File | What it holds |
|---|---|
| `references/mines_states_a_to_m.csv`, `references/mines_states_n_to_z.csv` | One row per mine, split by the state's postal code (AK–MT, NC–WY): identity, status, operator, controller, commodity, employees, and safety counts for **Jan 2021 – Sep 2026** |

Safety columns:
- **`violations`:** all citations and orders.
- **`significant_substantial`:** violations "reasonably likely to result in a reasonably serious injury".
- **`high_or_reckless_negligence`:** violations MSHA judged high or reckless negligence.
- **`unwarrantable_failure_104d`:** citations and orders for aggravated conduct (section 104(d)).
- **`imminent_danger_orders_107a`:** miners withdrawn because of imminent danger.
- **`failure_to_abate_orders_104b`:** a cited hazard wasn't fixed in time.
- **`proposed_penalties_usd`:** total proposed penalties.
- **`accidents`, `fatalities`, `permanent_disability`, `lost_or_restricted_time`:** accident reports by outcome.

Latitude and longitude are as MSHA recorded them and are sometimes wrong (a Texas mine with New York coordinates); identify mines by name, operator, state and county, never by coordinates.

## 1. Find the mine
- Search `mine_name`, `operator` and `controller` for the insured's name, within the right state file. Insureds often operate several mines; list every one under the same operator or controller.
- Confirm with the county and commodity. Similar names are common ("North Plant", "Pit 1").
- `controller` is the parent company; an insured that is a subsidiary may appear only there.

## 2. Read the record
- **Size first:** a mine with 400 employees will have more violations than one with 5. Use `employees` to judge, and compare like with like (commodity, surface vs. underground).
- **What matters most for underwriting:**
  - any fatality or permanent disability;
  - any 107(a) imminent-danger order;
  - 104(d) unwarrantable-failure citations, which point to management attitude, not just conditions;
  - 104(b) failure-to-abate orders;
  - a high share of S&S violations (over about 30% of violations).
- Low counts at a small mine with recent inspections are a good sign. Zero violations with zero accidents can also mean the mine is idle, so check `status`.

## 3. Rate the record
| Rating | When |
|---|---|
| **Poor** | Any fatality or permanent disability, any 107(a) order, any 104(d) citation, or proposed penalties over $100,000 since 2021 |
| **Fair** | 104(b) orders, high or reckless negligence citations, or S&S over 30% of violations |
| **Good** | None of the above, with an active or intermittent status |
| **Not enough data** | Mine not found, or idle or abandoned with no recent activity |

## Confidence
- **High:** a single mine matched on name, operator, state and county.
- **Medium:** matched on operator and state only, or several similar mines.
- **Low:** a common name with no county or operator. Say what would resolve it.

## Output
```
Mine: <mine name> (MSHA ID <id>), <commodity>, <surface/underground>, <county>, <state>
Operator: <operator> (controller <controller>); status <status> since <date>; <employees> employees
Safety record: Poor | Fair | Good | Not enough data
Confidence: High | Medium | Low
Why: <the deciding facts in one or two sentences>

Since 2021: <violations> violations (<S&S> S&S, <104d> unwarrantable failure, <107a> imminent danger, <104b> failure to abate), $<penalties> proposed
Accidents since 2021: <accidents> (<fatalities> fatal, <perm> permanent disability, <lost> lost or restricted time)
Other mines under the same operator/controller: <list with ratings, or "none">

Alternatives considered: <similar-named mines ruled out and why; omit if none>
Flags: <data quality issues, idle status, multiple matches, or "none">
To raise confidence: <county, operator name, or MSHA mine ID>
Source: MSHA Mines, Violations and Accidents data sets, Jan 2021 – Sep 2026
```
Return it as JSON with these fields when the caller asks for structured output.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets) that turns the result into practical guidance: what the record means for workers' comp, auto (haul trucks), GL and umbrella exposure at this operation; what to ask (safety program, contractor use, haul-road practices, training under MSHA Part 46/48); and anything relevant the data doesn't cover (contractor violations recorded under other IDs, state mine inspections). Label anything that comes from your own knowledge rather than the tables. Never let your own knowledge override a value in the tables.

Omit any output field that has nothing to say.
