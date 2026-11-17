# Pattern: Loss Runs (3-step recipe)

Loss-run extraction is the one place workflows use Reducto v3's `deep_extract` — it's significantly more expensive than the v2 default but materially better on dense, multi-page claims tables. Don't use this pattern for any other tabular extraction; standard `extract_rows_from_multiple_sources` on v2 is the right default elsewhere.

## The shape

```
Classify Documents
  → Extract Loss Runs            (extract_rows_from_multiple_sources, deep_extract + v3)
  → Flatten Claims               (custom_step — normalize rows, unwrap cells)
  → Aggregate Claims             (custom_step — totals, year-over-year, summary stats)
```

Output of step 3 is what feeds downstream summary / dashboard / email steps.

## Step 1: Extract Loss Runs

```jsonc
{
  "name": "Extract Loss Runs",
  "type": "extract_rows_from_multiple_sources",
  "input_mappings": [
    { "input_parameter_name": "documents",
      "input_type": "dependency",
      "value": { "dependency_step_outputs": [
                   { "step_name": "Classify Documents", "output_attribute": "documents.Loss_Run" }],
                 "resolution_operator": null, "resolution_config": null } }
  ],
  "dependencies": [],
  "config": {
    "extraction_schema": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "policy_period_start": { "type": "string", "description": "Policy period start (MM/DD/YYYY)" },
          "policy_period_end":   { "type": "string", "description": "Policy period end (MM/DD/YYYY)" },
          "claim_number":        { "type": "string", "description": "Claim or incident number" },
          "date_of_loss":        { "type": "string", "description": "Date of loss (MM/DD/YYYY) — NOT date reported" },
          "claimant":            { "type": "string", "description": "Claimant name if available" },
          "description":         { "type": "string", "description": "Short description of the loss" },
          "status":              { "type": "string", "description": "Open / Closed / Reopened" },
          "paid":                { "type": "string", "description": "Total paid ($ and commas)" },
          "reserved":            { "type": "string", "description": "Total reserved ($ and commas)" },
          "total_incurred":      { "type": "string", "description": "Total incurred = paid + reserved ($ and commas)" }
        }
      }
    },
    "system_prompt": "Extract every claim row from these loss runs. Return one row per claim across all documents. Currency values must include $ and commas. Dates must be MM/DD/YYYY.",
    "model": "gpt-5.1",
    "reasoning_effort": "medium",
    "deep_extract": true,
    "reducto_version": "v3",
    "generate_citations": true,
    "show_confidence_score": true
  },
  "incremental_config": { "behavior": "append" }
}
```

**Notes:**
- `deep_extract: true` requires `reducto_version: "v3"` at the step level.
- `incremental_config: { behavior: "append" }` lets new loss runs be added later without re-extracting old ones.
- Keep schemas flat — no nested objects per row. Downstream unwrapping is simpler.
- Field descriptions carry the format rules (currency, date) — these dominate the system prompt at conflict.

## Step 2: Flatten Claims (custom_step — unwrap and normalize)

Extraction cells are wrapped `{value: X, citations: [...]}`. This step unwraps and normalizes financial strings to floats. New custom code steps are always `custom_step` (sandboxed) — `function` is legacy, edit-only.

```jsonc
{
  "name": "Flatten Claims",
  "type": "custom_step",
  "input_mappings": [
    { "input_parameter_name": "rows",
      "input_type": "dependency",
      "value": { "dependency_step_outputs": [
                   { "step_name": "Extract Loss Runs", "output_attribute": "data" }],
                 "resolution_operator": null, "resolution_config": null } }
  ],
  "dependencies": [],
  "config": {
    "code": "async def run(rows=None, **kwargs):\n    def unwrap(v):\n        if isinstance(v, dict) and 'value' in v: return v['value']\n        return v\n    def to_float(s):\n        try: return float(str(unwrap(s)).replace('$','').replace(',','').strip())\n        except Exception: return 0.0\n    flat = []\n    for r in rows or []:\n        flat.append({\n            'policy_period_start': unwrap(r.get('policy_period_start')),\n            'policy_period_end':   unwrap(r.get('policy_period_end')),\n            'claim_number':        unwrap(r.get('claim_number')),\n            'date_of_loss':        unwrap(r.get('date_of_loss')),\n            'claimant':            unwrap(r.get('claimant')),\n            'description':         unwrap(r.get('description')),\n            'status':              unwrap(r.get('status')),\n            'paid':                to_float(r.get('paid')),\n            'reserved':            to_float(r.get('reserved')),\n            'total_incurred':      to_float(r.get('total_incurred')),\n        })\n    return { 'claims': flat, 'count': len(flat) }",
    "kwargs": {},
    "input_schema": {
      "type": "object",
      "properties": { "rows": { "type": "array" } },
      "required": ["rows"]
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "claims": { "type": "array", "items": { "type": "object" } },
        "count":  { "type": "integer" }
      }
    }
  }
}
```

## Step 3: Aggregate Claims (custom_step — summary stats)

Computes totals, per-policy-period rollups, status breakdown.

```jsonc
{
  "name": "Aggregate Claims",
  "type": "custom_step",
  "input_mappings": [
    { "input_parameter_name": "claims",
      "input_type": "dependency",
      "value": { "dependency_step_outputs": [
                   { "step_name": "Flatten Claims", "output_attribute": "claims" }],
                 "resolution_operator": null, "resolution_config": null } }
  ],
  "dependencies": [],
  "config": {
    "code": "async def run(claims=None, **kwargs):\n    from collections import defaultdict\n    by_period = defaultdict(lambda: {'count':0, 'total_incurred':0.0})\n    by_status = defaultdict(int)\n    total_incurred = 0.0\n    total_paid = 0.0\n    for c in claims or []:\n        period = (c.get('policy_period_start') or '') + ' - ' + (c.get('policy_period_end') or '')\n        by_period[period]['count'] += 1\n        by_period[period]['total_incurred'] += float(c.get('total_incurred') or 0)\n        status = (c.get('status') or 'Unknown')\n        by_status[status] += 1\n        total_incurred += float(c.get('total_incurred') or 0)\n        total_paid += float(c.get('paid') or 0)\n    return {\n        'total_claims': len(claims or []),\n        'total_incurred': f'${total_incurred:,.0f}',\n        'total_paid':     f'${total_paid:,.0f}',\n        'by_policy_period': [\n            { 'period': p, 'count': v['count'], 'total_incurred': f\"${v['total_incurred']:,.0f}\" }\n            for p, v in sorted(by_period.items())\n        ],\n        'by_status': dict(by_status)\n    }",
    "kwargs": {},
    "input_schema": {
      "type": "object",
      "properties": { "claims": { "type": "array" } },
      "required": ["claims"]
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "total_claims":     { "type": "integer" },
        "total_incurred":   { "type": "string" },
        "total_paid":       { "type": "string" },
        "by_policy_period": { "type": "array" },
        "by_status":        { "type": "object" }
      }
    }
  }
}
```

## Wiring downstream

Downstream summary / dashboard / email steps consume the aggregate output:

```jsonc
{
  "input_parameter_name": "loss_summary",
  "input_type": "dependency",
  "value": { "dependency_step_outputs": [
               { "step_name": "Aggregate Claims", "output_attribute": null }],
             "resolution_operator": null, "resolution_config": null }
}
```

Or pull specific fields:

```jsonc
{ "step_name": "Aggregate Claims", "output_attribute": "total_incurred" }
{ "step_name": "Aggregate Claims", "output_attribute": "by_policy_period" }
```

## Variations

- **Skip aggregation step** if downstream consumers just need the flat claims list — wire from "Flatten Claims".
- **Add a chart/visualization step** between Aggregate and the final email if you want a per-period bar chart in the summary.
- **Per-carrier loss runs**: if loss runs are split by carrier and you want per-carrier totals, group inside the aggregate function by carrier name (would need to be in the schema first).

## Cost notes

- v3 deep extract is the most expensive single thing in this pattern. Use it ONLY for loss runs.
- The two custom_steps are negligible cost (no LLM calls).
- For workflows that don't need the rich grid (just total incurred and counts), consider whether a lighter pattern (`agentic_extraction` with a flat schema) suffices first.
