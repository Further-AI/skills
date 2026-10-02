---
name: industry-hazard-benchmark
metadata:
  furtherai-display-name: Industry Hazard Benchmark
description: Benchmark how dangerous a business's industry is for its workers, from official BLS injury and fatality rates by NAICS code. Use when underwriting or reviewing workers' comp, GL, or any risk where workplace hazard matters, or when someone asks how risky an industry or class of business is.
---

# Industry Hazard Benchmark

Compares an industry's worker injury and fatality rates with the U.S. private-industry average, using the Bureau of Labor Statistics' 2024 data. The tables in `references/` are the source of truth; don't quote rates from memory.

| File | What it holds |
|---|---|
| `references/injury_rates_2024.csv` | Nonfatal injury and illness rates per 100 full-time workers, by NAICS and ownership (private, state government, local government) |
| `references/injury_rate_quartiles_2024.csv` | The spread of establishment rates (mean, 25th percentile, median, 75th percentile) by NAICS and establishment size |
| `references/fatal_injury_rates_2024.csv` | Fatal injuries per 100,000 full-time workers, for about 90 industry groups |
| `references/fatal_injury_counts_by_event_2024.csv` | Number of worker deaths by NAICS and cause (falls, transportation, contact with objects, and so on) |
| `references/naics_2022_to_2017.csv` | NAICS 2022 codes that changed from 2017 |

## 1. Get the NAICS code
Use the code stated in the context, or classify the business with the `industry-code-extraction` skill. If you can't get a code, say so and stop; don't benchmark from a guessed industry.

## 2. Translate to NAICS 2017
BLS publishes these tables on **NAICS 2017**. If the code appears in `naics_2022_to_2017.csv`, use the 2017 code it maps to; if it maps to several, use each and say so. Codes not listed are the same in both editions.

## 3. Find the most specific row
Rows use each code's natural length: roofing is `23816`, not `238160`; sectors that span ranges are `31-33`, `44-45`, `48-49` (written `31, 32, 33` and `48, 49` in the fatal-rate table).

For each table:
1. Look for the full code (`238160`). If the code ends in `0`, also try it without the trailing zero (`23816`).
2. If there's no row, or the rate you need is blank (BLS didn't publish it), move up one level: `2381`, then `238`, then `23`.
3. Use `ownership = private` unless the insured is a state or local government entity.

Always report which level you used. "Roofing contractors (23816)" and "Construction (23)" are very different answers.

## 4. Compare with the average
The private-industry averages are the `Private industry` rows in each table: currently 2.3 total recordable cases and 1.4 days-away/restricted (DART) cases per 100 workers, and 3.6 deaths per 100,000.

- **Nonfatal:** the industry's total recordable rate and DART rate, divided by the average.
- **Fatal:** the fatal rate at the most specific level available, divided by the average. Fatal rates exist only for broad groups, so also give the industry's death count and its leading cause from `fatal_injury_counts_by_event_2024.csv`.

## 5. Rate the hazard
Take the worse of the two signals:

| Tier | Nonfatal (total recordable or DART vs. average) | Fatal (rate vs. average) |
|---|---|---|
| **High** | 1.5× or more | 2× or more |
| **Elevated** | 1.15× to 1.5× | 1.25× to 2× |
| **Average** | 0.75× to 1.15× | 0.75× to 1.25× |
| **Low** | under 0.75× | under 0.75× |

Nonfatal rates understate the danger in industries with many small employers and severe, rather than frequent, injuries. Roofing's injury rate is close to the average, yet falls killed 96 roofing workers in 2024. When an industry's death count or leading cause points to severity that its injury rate doesn't show, raise the tier and say why.

## 6. Compare the insured's own rate (when you have it)
If the insured's OSHA 300A log or loss history gives recordable cases and hours worked, compute its rate: cases × 200,000 ÷ hours worked. Compare it with `injury_rate_quartiles_2024.csv` for the same code and establishment size: better than the 25th percentile, between the 25th and 75th, or worse than the 75th. Values shown as blank weren't published; fall back to the `Total all sizes` row or a broader code.

## Confidence
- **High:** a full code from a confident classification, benchmarked at the 5- or 6-digit level.
- **Medium:** fell back to a 3- or 4-digit level, the code split in the 2022 → 2017 translation, or the classification itself was Medium.
- **Low:** only sector-level (2-digit) data, or the classification was Low. Say what would resolve it.

## Output
```
Industry: <NAICS 2017 code> <title> (benchmark level used, if broader than the insured's code)
Hazard: High | Elevated | Average | Low
Confidence: High | Medium | Low
Why: <one or two sentences, naming the deciding signal>

Injury rate: <total recordable> per 100 workers (<N>× average); DART <rate> (<N>× average)
Fatal rate: <rate> per 100,000 (<N>× average) at <level>, <deaths> deaths in 2024, mostly <leading cause>
Insured's own rate: <rate>, <percentile band> for <size> establishments | not available

Alternatives considered:
1. <alternative NAICS code from the classification>: <its hazard tier, and why it isn't the benchmark used>
2. ...
3. ...

Flags: <fallbacks to broader codes, NAICS 2022 → 2017 translations, unpublished values, the insured's own experience pointing a different way, or "none">
To raise confidence: <a more specific operations description, OSHA 300A log, or payroll by activity; only when Medium or Low>
Source: BLS Survey of Occupational Injuries and Illnesses and Census of Fatal Occupational Injuries, 2024
```
- Take alternatives from the industry classification's own alternatives, when it gave any. Show their tiers only when they differ from the chosen one: "if this is really 484 truck transportation rather than 4238 machinery wholesaling, the hazard is High, not Elevated" is exactly what an underwriter needs.
- Omit the section when the classification was unambiguous.
- Return it as JSON with these fields when the caller asks for structured output.

This benchmarks the industry, not the insured. Say so whenever the insured's own experience (its rate, loss runs, OSHA history) points a different way.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets) that turns the result into practical guidance for this insured and the person asking: what it means for this specific business, operation, claim or location; what to check or ask next; and anything relevant the data doesn't cover. Label anything that comes from your own knowledge rather than the tables, so the reader can tell evidence from judgment. Never let your own knowledge override a value in the tables; if they seem to conflict, say so.

Omit any output field that has nothing to say (write nothing rather than "N/A").
