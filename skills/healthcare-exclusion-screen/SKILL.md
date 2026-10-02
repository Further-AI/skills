---
name: healthcare-exclusion-screen
metadata:
  furtherai-display-name: Healthcare Exclusion Screen
description: Check whether a person or business is excluded from Medicare, Medicaid or other federal health programs, using the HHS-OIG List of Excluded Individuals/Entities (LEIE) bundled with this skill. Use for any question like "is X excluded from Medicare", "OIG exclusion check", "LEIE screen", or screening healthcare providers, home health, staffing, pharmacy or DME insureds, their owners and practitioners. This is NOT an OFAC sanctions check; use it instead of, or in addition to, OFAC whenever Medicare or Medicaid exclusion is asked about.
---

# Healthcare Exclusion Screen

Checks names against the federal list of people and businesses barred from Medicare, Medicaid and other federal health programs. An excluded owner or practitioner is a serious underwriting and fraud signal. The tables in `references/` are the source of truth; never say someone is or isn't excluded from memory.

| File | What it holds |
|---|---|
| `references/leie_individuals_a_to_l.csv`, `references/leie_individuals_m_to_z.csv` | Excluded individuals, split by last name |
| `references/leie_entities.csv` | Excluded businesses |
| `references/exclusion_types.md` | What each `exclusion_type` code means, and its minimum exclusion period |

**Data is as of September 2026.** OIG updates the list monthly. A clear result is only as current as that date; say so in every answer, and recommend the live search at oig.hhs.gov/exclusions before binding or paying.

## 1. Collect who to screen
- **The insured business:** legal name and DBAs.
- **Owners, officers and managing employees.**
- **Practitioners named in the submission:** physicians, nurses, therapists, pharmacists.

For each, collect whatever identifiers you have: full name, NPI, date of birth, city and state.

## 2. Search
- **Individuals:** use the file for their last-name initial. Search the last name first, then narrow by first name. Also try maiden or former names, hyphenated parts, and common nicknames (Bob/Robert, Liz/Elizabeth).
- **Businesses:** search the distinctive words of the name without suffixes such as Inc, LLC or PC, and search each DBA.
- **Treat the NPI as decisive.** The same NPI is a match; a different NPI rules out a name-only hit. Most listed individuals have no NPI recorded, so a missing NPI decides nothing.

## 3. Adjudicate each hit
| Result | When |
|---|---|
| **Match** | NPI matches, or name plus date of birth matches, or name plus a city/state and specialty that fit |
| **Possible match** | The name matches, but nothing else confirms or rules it out |
| **No match** | No hit, or every hit is ruled out by a different NPI, date of birth, or an incompatible location or specialty |

- **Reinstatement:** a non-empty `reinstatement_date` means the exclusion ended. Report it as **Previously excluded (reinstated <date>)**: still relevant history, but not a current bar.
- **Waiver:** a `waiver_date` means a state was allowed to keep paying this provider. Report it, and the waiver state.
- **Exclusion type:** look it up in `references/exclusion_types.md` and explain it in plain words. Patient abuse (`1128a2`) reads very differently from a defaulted student loan (`1128b14`).

## Confidence
- **High:** decided by NPI or date of birth, or several identifiers agree.
- **Medium:** decided on name plus location or specialty only.
- **Low:** a common name with no other identifiers. Say which identifier would settle it.

## Output
One block per person or business screened:
```
Screened: <name> (<role: insured, owner, practitioner>)
Result: Match | Possible match | No match | Previously excluded (reinstated <date>)
Confidence: High | Medium | Low
Why: <the identifiers that decided it>

Listing: <name on the list>, <specialty>, <city, state>, excluded <date> under <type>: <plain-English reason, minimum period>
Alternatives considered:
1. <other list entry with a similar name>: <why it's ruled out>
2. ...
3. ...

Flags: <waivers, owners linked to an excluded entity, name variations, or "none">
To raise confidence: <NPI, date of birth or other identifier needed; only when Medium or Low>
Source: HHS-OIG LEIE, September 2026. Confirm on the live list at oig.hhs.gov/exclusions before binding or paying.
```
Omit `Listing` when the result is No match. List up to three alternatives, the closest names first, and only names a reviewer would actually wonder about. Return it as JSON with these fields when the caller asks for structured output.

A match is a reason to refer the risk to underwriting management or compliance, not an automatic decline.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets) that turns the result into practical guidance for this insured and the person asking: what it means for this specific business, operation, claim or location; what to check or ask next; and anything relevant the data doesn't cover. Label anything that comes from your own knowledge rather than the tables, so the reader can tell evidence from judgment. Never let your own knowledge override a value in the tables; if they seem to conflict, say so.

Omit any output field that has nothing to say (write nothing rather than "N/A").
