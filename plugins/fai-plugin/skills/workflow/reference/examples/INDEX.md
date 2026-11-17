# Workflow Examples

Validated whole-workflow JSON files. Inspect them with `workflow_tools.py` (never Read them whole) for real-world wiring patterns when authoring or editing.

| File | Demonstrates |
|---|---|
| `submission_intake_es_umbrella.json` | Full E&S Umbrella submission intake — prepare → classify → KB → agentic extraction fan-out → 3-step loss runs (extract_rows + custom_step flatten/aggregate) → 3-carrier agentic guideline checks → AI summary → Excel fills → HTML dashboard → email |

Every example must pass `python3 ${CLAUDE_PLUGIN_ROOT}/skills/workflow/scripts/validate_workflow.py` before being added here.
