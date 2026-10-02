---
name: industry-code-extraction
metadata:
  furtherai-display-name: Industry Code Extraction
description: Identify a business's NAICS and SIC codes. Use whenever a task needs a business's industry classification.
---

# Industry Code Extraction

Codes are **NAICS 2022** and **SIC 1987**. The tables in `references/` are the source of truth; don't rely on memory for codes or titles.

| File | Use it to |
|---|---|
| `references/naics_index.csv` | Find a NAICS code from a specific activity (~20k Census index entries) |
| `references/naics_codes.csv` | Validate a NAICS code and read its title |
| `references/sic_codes.csv` | Validate a SIC code and read its title |
| `references/sic_naics_concordance.csv` | Translate between SIC and NAICS 2022 |
| `references/insurance_gotchas.md` | Check common misclassifications in insurance submissions |

## 1. Know what you need
To classify a business you need two things:
- **Who it is:** legal name and location.
- **What it does:** its main operations, or its purpose if it doesn't sell anything.

If the codes are already stated somewhere, you also need those.

## 2. Look in the context first
Check the chat and any files provided for the business's identity, its operations, and any stated NAICS or SIC codes.

Only use information about the business itself. Details about brokers, carriers or senders are not evidence of its industry. Ignore insurance class codes (GL, WC, property), which are not industry codes.

## 3. Fill gaps
If you can't tell what the business does from the context:
- Look it up: its website and public business information.
- If that fails, ask the user.

Don't guess from the name alone.

## 4. Determine the codes
- **If codes are stated,** confirm they exist in `references/naics_codes.csv` and `references/sic_codes.csv` and that they fit the operations.
  - Valid and fits: use it.
  - Valid but doesn't fit: give the right code and flag the mismatch.
  - Not in the tables: it may be from an older NAICS edition (e.g. 2017's 454110). Say so, then infer the 2022 code.
- **Otherwise, infer them.**
  - Search `references/naics_index.csv` for the activity. It maps specific activities to codes more reliably than the broad code titles do. Try synonyms and singular forms. Rows whose code is `******` are cross-references ("-- see specific activity"); search for the activity they point to.
  - Choose the most specific NAICS code that fits. Confirm it by looking at what else falls under that code.
- **Business with several activities:** the primary code is the activity with the most revenue (payroll if revenue is unknown). Mention secondary activities but don't let them change the primary code.
- **Pick the SIC code** from `references/sic_naics_concordance.csv`:
  - Filter to the chosen NAICS code.
  - `one_to_one`: use that SIC.
  - `partial`: choose the row whose `sic_piece` matches the operations.
  - Never pick a SIC that the concordance doesn't pair with the chosen NAICS without flagging why.
- **Either way,**
  - check `references/insurance_gotchas.md` for common misclassifications;
  - weigh the strongest alternatives (see Output) and say why each loses.

## 5. Cross-reference
Compare the chosen codes against everything you know about the business. Flag anything that doesn't fit: other business names, locations, exposures, or entities.

## Confidence
- **High:** a stated code that fits, or an exact index match with no conflicts.
- **Medium:** a good fit with thin or slightly conflicting evidence.
- **Low:** unclear operations, conflicting sources, or no specific code fits. Say what information would resolve it.

## Output
Use this shape, so a reader and a workflow step see the same thing every time. Return it as JSON with these fields when the caller asks for structured output.

```
NAICS: <code> <title>
SIC: <code> <title>
Confidence: High | Medium | Low
Why: <one or two sentences, citing the evidence and where it came from>

Alternatives considered:
1. <NAICS code> <title>: <why it loses>
2. ...
3. ...

Flags: <inconsistencies, stated-code mismatches, or "none">
To raise confidence: <the question or document that would settle it; only when Medium or Low>
```

- List up to three alternatives, strongest first: the codes a careful reviewer would actually consider. Each reason is one line, tied to the evidence ("no land ownership, so not a for-sale builder"). Skip filler alternatives; one strong contender beats three weak ones.
- An alternative that the gotchas list names as the trap for this kind of business always goes in.
- If classifying several named insureds, give one block per entity.

## Add your own expertise
The tables above are the evidence; you are still the expert. After the output block, add a short **"What this means"** section (up to 5 bullets) that turns the result into practical guidance for this insured and the person asking: what it means for this specific business, operation, claim or location; what to check or ask next; and anything relevant the data doesn't cover. Label anything that comes from your own knowledge rather than the tables, so the reader can tell evidence from judgment. Never let your own knowledge override a value in the tables; if they seem to conflict, say so.

Omit any output field that has nothing to say (write nothing rather than "N/A").
