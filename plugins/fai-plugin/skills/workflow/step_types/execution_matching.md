# `execution_matching`

**Indexes the current execution's structured matching fields and returns prior executions the matching LLM judges to be the same account and same deal. Output: grid table of matched executions.**

## Inputs

Explicit canonical fields mapped from previous extraction steps:

| Param | Required | Notes |
|---|---|---|
| `account_name` | YES | The insured/account name. |
| `account_address`, `fein`, `broker_email`, `broker_domain`, `broker_name`, `broker_signature`, `effective_date`, `line_of_business`, `quote_policy_ref` | no | Each additional field improves match precision. |

Wire each from the producing extraction step (`data.<field>.value` for cell-wrapped extraction outputs).

## Config

| Key | Notes |
|---|---|
| `candidate_limit` | Max prior executions considered. |
| `model` | LLM used for the same-account/same-deal judgment. |
| `reasoning_effort` | Only on models that support it — see `reference/model_compatibility.md`. |

## Output

Grid table of matched executions; a key-value message when no match is found. Downstream consumers should handle both shapes.

## Gotchas

- **Scope is fixed**: always searches the current execution's org and workflow. Do not add `target_workflow_id` or date-window inputs — they don't exist.
- `account_name` is the only save-time-required mapping, but matching quality is poor with name alone; wire the optional fields you have.
