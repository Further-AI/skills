---
name: payroll-reasonableness
metadata:
  furtherai-display-name: Payroll Reasonableness
description: Check whether a business's reported payroll is plausible for its headcount, occupations, industry and state, using BLS wage data. Use in premium audits, workers' comp or GL underwriting, renewal reviews, or whenever reported payroll per employee looks too low or too high.
---

# Payroll Reasonableness

Compares reported payroll with what the government's wage survey says those workers typically earn. Understated payroll means understated premium on payroll-rated coverage (workers' comp, and GL rated on payroll). The tables in `references/` are the source of truth; don't estimate wages from memory.

| File | What it holds |
|---|---|
| `references/wages_by_state_occupation_2025.csv` | Annual wages by state and occupation, across all industries: 10th, 25th, 50th (median), 75th and 90th percentiles, plus the mean and employment |
| `references/wages_by_industry_occupation_2025.csv` | National annual wages by NAICS industry (4-digit, or 3-digit where BLS publishes only that) and occupation |
| `references/job_title_to_occupation.csv` | About 6,500 job titles mapped to their standard occupation (SOC) code, e.g. "Roofer Helper" → 47-3016 |

Wages are from the BLS Occupational Employment and Wage Statistics survey, **May 2025**. `occ_code` 00-0000 is "All Occupations". Special values:
- A blank wage means BLS didn't publish it.
- `*` means the wage estimate isn't available.
- `**` means the employment estimate isn't available.
- `#` means the wage is at or above the survey's top value. Treat it as "very high"; it can't be compared exactly.

## 1. Collect the facts
For each class code or group of workers on the audit or application, collect:
- the description or job titles;
- headcount, or full-time equivalents if given;
- reported payroll;
- the state.

Also collect the business's NAICS code (use the `industry-code-extraction` skill if it isn't given). Note any owners or officers, and whether the payroll includes them. Many states cap or exclude officer payroll, and owners often take little salary.

## 2. Map each group to an occupation
- Search `job_title_to_occupation.csv` for the job titles. If there are no titles, use the class description ("roofing – residential" → Roofers, 47-2181).
- Clerical and outside sales groups map to office and sales occupations, not the governing trade.
- If a group mixes occupations, use the main one and say so.

## 3. Find the benchmark wage
1. **State:** the occupation's row for the business's state in `wages_by_state_occupation_2025.csv`.
2. **Industry:** the same occupation in `wages_by_industry_occupation_2025.csv` for the business's 4-digit NAICS (fall back to 3 digits). A roofer working for a roofing contractor can earn differently from one at a general contractor.
3. Use the **state** median as the benchmark, and the industry figure to confirm it. If they differ by more than 20%, give both.

## 4. Compare
- **Payroll per head** = reported payroll ÷ headcount (use full-time equivalents if given).
- Compare it with the benchmark's percentiles:

| Result | When |
|---|---|
| **Reasonable** | Between the 25th and 90th percentile |
| **Low – review** | Between the 10th and 25th percentile |
| **Understated – likely** | Below the 10th percentile |
| **High – review** | Above the 90th percentile (possible misclassification of higher-paid staff into this class, or included bonuses or officers) |

**Explain the gap before calling payroll understated.**
- **Part-time or seasonal workers:** the BLS figures assume year-round full-time work. Ask for hours or months worked.
- **A partial policy term:** pro-rate.
- **Subcontractors paid on 1099s:** they aren't payroll. Check their certificates of insurance, because uninsured subcontractors are usually chargeable.
- **Owners and officers:** check the state's inclusion rules.
- **Overtime:** some states exclude the overtime premium portion from workers' comp payroll.

When the per-head figure is low, give the **implied payroll at the benchmark median**:
headcount × median.

The difference between that and the reported payroll is the possible understatement.

## Confidence
- **High:** job titles map cleanly to one occupation, the state benchmark is published, and headcount is known.
- **Medium:** the occupation was mapped from a class description, the group mixes occupations, or only the national industry figure is available.
- **Low:** no headcount, or no clear occupation. Say what's needed.

## Output
One block per class or worker group, then a summary line for the whole account:
```
Group: <class code/description>, <headcount> workers, $<payroll> reported (<state>)
Result: Reasonable | Low – review | Understated – likely | High – review
Confidence: High | Medium | Low
Why: <payroll per head vs. the benchmark percentile, in one sentence>

Benchmark: <occupation> (<SOC code>), <state> median $<x> (p10 $<a>, p25 $<b>, p75 $<c>, p90 $<d>); <industry> national median $<y>
Payroll per head: $<z>, around the <nth> percentile
Implied payroll at median: $<headcount × median> (gap $<difference>)

Alternatives considered:
1. <other occupation the group could be>: <its median, and why it wasn't used>
2. ...

Flags: <part-time or seasonal workers likely, 1099 labor, officers, partial term, misclassification signs, or "none">
To raise confidence: <payroll register, 941s, hours worked, job titles; only when Medium or Low>
Source: BLS Occupational Employment and Wage Statistics, May 2025
```
Return it as JSON with these fields when the caller asks for structured output.

This is a reasonableness test, not an audit finding. Payroll records (941s, payroll registers, the general ledger) decide the actual audited payroll.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets) that turns the result into practical guidance for this insured and the person asking: what it means for this specific business, operation, claim or location; what to check or ask next; and anything relevant the data doesn't cover. Label anything that comes from your own knowledge rather than the tables, so the reader can tell evidence from judgment. Never let your own knowledge override a value in the tables; if they seem to conflict, say so.

Omit any output field that has nothing to say (write nothing rather than "N/A").
