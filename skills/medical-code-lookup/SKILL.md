---
name: medical-code-lookup
metadata:
  furtherai-display-name: Medical Code Lookup
description: Explain ICD-10-CM diagnosis codes and HCPCS Level II codes on medical bills, records and claim files, and check them against the reported injury. Use when reviewing a workers' comp, bodily injury, auto or liability claim's medical documents, or whenever someone asks what a diagnosis or HCPCS code means.
---

# Medical Code Lookup

Turns diagnosis and supply/service codes into plain English and checks whether they fit the claim. The tables in `references/` are the source of truth; don't describe codes from memory.

| File | What it holds |
|---|---|
| `references/icd10cm_2027.csv` | Every ICD-10-CM diagnosis code for FY2027 (effective Oct 1, 2026), with `billable` = Y for codes valid on a claim and N for category headers |
| `references/hcpcs_level2_2026q4.csv` | HCPCS Level II codes and modifiers (Oct 2026): ambulance, durable medical equipment, drugs, orthotics, and other items and services |

**Not covered:**
- **CPT codes** (5 digits, e.g. 99213, 27447): the AMA owns them. Say what kind of service the bill describes in words if it does, but don't attach descriptions to CPT codes from memory.
- **CDT dental codes** (D-series), owned by the ADA.

## 1. Collect the codes and the claim facts
From the bill, record or claim file, collect each code with its date of service. Also collect the reported facts: date of injury, how it happened, and the body parts claimed.

## 2. Look up each code
- ICD-10-CM codes are written with a dot (`S83.241A`). Bills often drop it (`S83241A`), so add it back after the third character before searching.
- `billable = N` means the code is a category, not a complete diagnosis. Flag it: a bill using one is incomplete.
- A code not in the table may be mistyped, retired, or from another edition. Say so; don't guess its meaning.

## 3. Read what the diagnosis code says
- **Body part:**
  - Chapter S injury codes are grouped by region: S00–S09 head, S10–S19 neck, S20–S29 thorax, S30–S39 abdomen, lower back and pelvis, S40–S49 shoulder and upper arm, S50–S59 elbow and forearm, S60–S69 wrist and hand, S70–S79 hip and thigh, S80–S89 knee and lower leg, S90–S99 ankle and foot.
  - Side is in the description (right, left, bilateral, unspecified).
- **Encounter (7th character on injury codes):** A = initial treatment, D = follow-up care, S = sequela (a late effect).
- **Acute or degenerative:**
  - S and T codes are injuries.
  - Many M codes are degenerative or chronic, e.g. M17 osteoarthritis of the knee, or M23.2 "derangement of meniscus due to old tear".
  - An M code alongside the claimed injury is a sign of a pre-existing condition, not proof of one.
- **How it happened:** V, W, X and Y codes record the external cause (W01 = fall on the same level from slipping or tripping). Compare them with the reported mechanism.

## 4. Check against the claim
Flag each of these with the code and service date:
- A body part or side that wasn't reported, e.g. a right-knee claim billed with a left-knee code.
- A degenerative or chronic diagnosis on the claimed body part.
- A cause code that conflicts with the reported mechanism.
- Initial-encounter (A) codes appearing again long after the first visit, or treatment starting long after the injury date.
- HCPCS items that don't fit the diagnoses (a knee brace on a shoulder claim), and terminated codes billed after their `termination_date`.

## Confidence
- **High:** every code found, billable, and consistent with the claim, or every flag tied to an exact code and date.
- **Medium:** some codes unbillable or missing, or the claim facts are thin.
- **Low:** most codes can't be found, or there are no claim facts to compare against. Say what would resolve it.

## Output
```
Summary: <one line: what the codes describe, and whether they fit the claim>
Confidence: High | Medium | Low
Why: <one or two sentences>

Codes:
- <code> <description>: <body part, side, acute/degenerative, encounter> (<date of service>)
- ...

Consistency with the claim: Consistent | Partly consistent | Inconsistent
Alternatives considered: <other readings of an ambiguous code or mechanism, and why they lose; omit when none>
Flags: <each issue with its code and date, or "none">
To raise confidence: <the record or fact that would settle it; only when Medium or Low>
Source: CMS ICD-10-CM FY2027; CMS HCPCS Level II, October 2026
```
Return it as JSON with these fields when the caller asks for structured output.

Flags are questions for the adjuster or a nurse reviewer, not coverage decisions. Never say a claim should be denied.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets) that turns the result into practical guidance for this insured and the person asking: what it means for this specific business, operation, claim or location; what to check or ask next; and anything relevant the data doesn't cover. Label anything that comes from your own knowledge rather than the tables, so the reader can tell evidence from judgment. Never let your own knowledge override a value in the tables; if they seem to conflict, say so.

Omit any output field that has nothing to say (write nothing rather than "N/A").

**For this skill specifically:**
- For every mismatch, suggest the corrected code from `references/icd10cm_2027.csv`: the same code with the claimed side, or the external-cause code that matches the reported mechanism (e.g. W11.XXXA for a fall from a ladder). Only suggest codes you found in the table.
- On workers' comp claims, check for the place-of-occurrence (Y92), activity (Y93) and external-cause-status (Y99.0, "civilian activity done for income or pay") codes. Flag them when they're missing, since many state bill-review rules expect them.
- End with a recommended action: pay, pend for records, request a corrected bill, or refer to a nurse reviewer. Pend rather than recode when the mechanism itself is in doubt; that may be a real discrepancy, not a coding error.
