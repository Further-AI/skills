# `trellis_law`

**Searches the Trellis legal-cases database for matching cases against one or more insured legal names (plus DBA / trade aliases). Output: grid table of case records with a `case_filing_url` link per row.**

Two-phase runtime:

1. **Search + LLM case-matching.** Queries Trellis per name in `insured_names`, then uses `config.model` to filter keyword hits down to cases actually about this insured (legal-name disambiguation, alias resolution, false-positive suppression). `reasoning_effort` controls how carefully it disambiguates — useful on common names.
2. **Optional document enrichment.** When `config.extract_payouts_from_documents` is `true`, downloads each matched case's available court documents and runs Reducto extraction on ruling text to pull payout/settlement amounts missing from Trellis metadata. Adds latency and cost; enable only when payout amounts are load-bearing downstream.

## Inputs

| Param | Type | Required | Source |
|---|---|---|---|
| `insured_names` | `array` of strings | YES | `input_mappings` — legal names + DBA/trade aliases, typically from an upstream extraction that resolved the insured + AKA list. |
| `insured_address` | `string` | no | Accepted but currently unused by the runtime. |
| `years_back` | `integer` | no | How far back to search. Default 5. |

## Config keys (`TrellisLawConfig`)

| Key | Type | Default | Notes |
|---|---|---|---|
| `model` | `AIModel` | platform default | LLM for case matching (and payout extraction when enabled). |
| `reasoning_effort` | enum or null | `null` | Reasoning depth for disambiguation. Validated against `model` — only set on models that support it. |
| `extract_payouts_from_documents` | `bool` | `false` | Config-only — do NOT put it in `input_mappings`. |

## Output

- `data`: array — one entry per matched case. The column set (~10 columns: case number, case name, insured role, status, case type, filing date, payouts/settlements, confidence, `case_filing_url`) is fixed by the runtime, not an author-declared schema; there is no JSON-Schema row definition to rely on at edit time.
- `user_documents`: populated only when `extract_payouts_from_documents=true`; empty otherwise.
- `metadata`: search metadata (query terms, hit counts).

`data.case_filing_url` is the only attribute downstream consumers should treat as authoritative — it links to the public Trellis page. Everything else is LLM/Reducto-derived and benefits from user verification before claims-history decisions.

## Input wiring

`input_mappings` only; `dependencies: []`.

## Common patterns

### Lookup downstream of insured-name extraction

```json
{
  "name": "Trellis Case Search",
  "type": "trellis_law",
  "input_mappings": [
    {
      "input_type": "dependency",
      "input_parameter_name": "insured_names",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Extract Insured Info", "output_attribute": "data.all_legal_names" }
        ],
        "resolution_operator": null,
        "resolution_config": null
      }
    }
  ],
  "dependencies": [],
  "config": {
    "model": "claude-sonnet-4-6",
    "reasoning_effort": "medium",
    "extract_payouts_from_documents": false
  }
}
```

The upstream must expose an **array** of strings. If it produces a single string, wrap it via a custom_step, or use the extraction's alias-array field.

### Payout enrichment for risk reports

Same wiring, with `"model": "claude-opus-4-7"`, `"reasoning_effort": "high"`, `"extract_payouts_from_documents": true` — higher effort + a stronger model because false-positive case matches translate directly into bogus payout numbers downstream.

## Common validation errors

- **`Missing required input mappings: ['insured_names']`** — wire it from an upstream output.
- **`Parameter 'insured_names' expects type 'array' but step '<src>' outputs type 'string'`** — pick the array-valued attribute (e.g. `data.all_legal_names`, not `data.insured_name`), or wrap the string into a one-element list in a custom_step.
- **`reasoning_effort not supported by model '<X>'`** — clear it to `null` or switch to a reasoning-capable model (see `reference/model_compatibility.md`).

## Common gotchas

- **Trellis coverage is jurisdiction-limited.** Empty results mean "no indexed cases", not "no litigation history". Set expectations downstream.
- **Common-name false positives.** The strongest defense is tight legal names from a registered-entity source, not free-text DBA strings.
- **`extract_payouts_from_documents=true` doubles or triples latency** (1-10 docs per matched case, sequential Reducto per doc).
- **`payout_amount` is best-effort** — many rulings carry no dollar figures; cells are often `"Not available"` even with extraction on.
- **No de-duplication across `insured_names`** — overlapping aliases produce duplicate `case_filing_url` rows; de-dup in a downstream custom_step if the consumer assumes uniqueness.

## See also

- `agentic_extraction` / `extract_from_multiple_sources` — typical upstream for the insured-name + alias list.
- `ofac_agent`, `osha_agent` — sibling insured-screening steps (different data sources).
- `submission_summary_generator` — downstream narrative consumer.
