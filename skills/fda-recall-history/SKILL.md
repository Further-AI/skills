---
name: fda-recall-history
metadata:
  furtherai-display-name: FDA Recall History
description: "Look up a company's complete FDA recall history (food, drug and medical device enforcement reports since 2012): how many recalls, when, which class (I, II or III), what was recalled and why. Use when underwriting products liability, product recall, GL or umbrella for food and beverage makers, co-packers, distributors, grocers' private labels, drug or supplement makers, compounding pharmacies and medical device manufacturers, or when someone asks whether a company has had FDA recalls."
---

# FDA Recall History

Web search finds a company's headline recalls but rarely its full history: counts and classes are usually wrong. This skill holds every FDA enforcement report (recall) since June 2012. The tables in `references/` are the source of truth; don't describe a company's recalls from memory.

| File | What it holds |
|---|---|
| `references/food_recalls.csv` | Food, beverage and dietary supplement recalls (about 29,000) |
| `references/drug_recalls.csv` | Drug recalls, including compounding pharmacies (about 18,000) |
| `references/device_recalls_a_to_l.csv`, `references/device_recalls_m_to_z.csv` | Medical device recalls (about 40,000), split by the firm's first letter |

Each row is one recalled product: firm, city, state, country, report date, class, status (Ongoing, Completed, Terminated), recall number, product and reason. One recall event often covers many products, so **count distinct report dates and reasons, not rows**, when describing how many recalls a firm had.

**Recall classes:**
- **Class I:** a reasonable probability of serious harm or death. Examples: Listeria or Salmonella, undeclared major allergens, a device failure that can kill.
- **Class II:** temporary or reversible harm, or a remote chance of serious harm.
- **Class III:** unlikely to cause harm, e.g. labeling issues.

Data runs **June 2012 – September 2026**. It doesn't include USDA-regulated meat, poultry and egg products (FSIS recalls) or CPSC consumer products.

## 1. Find the firm
- Search `recalling_firm` for the distinctive words of the name, without Inc, LLC and similar suffixes. Also try DBAs and the parent's name.
- **Private-label products:** a retailer's store-brand item is usually recalled by the **manufacturer**, not the retailer. Search the product or brand name in `product` too.
- Confirm with the city and state. Common names (e.g. "Fresh Foods") match unrelated firms.

## 2. Summarize the history
- **Recall events:** the number of distinct (report date, reason) pairs, and the number of products they covered.
- **Class mix:** count the Class I events separately. Even one Class I recall in the last 3 years matters.
- **Repeat causes:** the same reason recurring (e.g. Listeria twice) points to a systemic control problem, not bad luck.
- **Recency:** recalls in the last 3 years, and anything still Ongoing.

## 3. Rate it
| Rating | When |
|---|---|
| **Poor** | A Class I recall in the last 3 years, or two or more recall events for the same pathogen or allergen at any time |
| **Fair** | Class II recalls in the last 3 years, or any Class I recall more than 3 years ago |
| **Good** | Only Class III recalls, or none in the last 5 years |
| **Clean** | No recalls found (state the data period) |

## Confidence
- **High:** the firm matched on name plus city and state.
- **Medium:** matched on name only, or recalls found only by brand or product name.
- **Low:** a common name with several possible firms. Say what would resolve it.

## Output
```
Firm: <recalling firm as listed>, <city>, <state>
Recall history: Poor | Fair | Good | Clean
Confidence: High | Medium | Low
Why: <the deciding facts in one or two sentences>

Recall events since 2012: <n> (<Class I count> Class I, <Class II> Class II, <Class III> Class III), covering <products> products
Most recent: <date>, Class <x>: <product>, <reason> (<status>)
Repeat causes: <e.g. "Listeria: 2018, 2023", or "none">

Alternatives considered: <similar firm names ruled out and why; omit if none>
Flags: <private-label or parent-company matches, ongoing recalls, or "none">
To raise confidence: <city and state, brand names, parent company>
Source: FDA enforcement reports (openFDA), June 2012 – September 2026
```
Return it as JSON with these fields when the caller asks for structured output.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets): what the history means for products liability and product recall coverage for this insured; what to ask (food safety plan, HACCP or FSMA preventive controls, environmental monitoring, supplier approval, recall plan and mock recalls, third-party audits such as SQF or BRC); and gaps in this data (USDA-regulated products, recalls by co-packers making the insured's brands). Label anything that comes from your own knowledge rather than the tables. Never let your own knowledge override a value in the tables.

Omit any output field that has nothing to say.
