---
name: workflow
description: Create, edit, and understand workflow JSON files — step types, data flow, input mappings, inline code, options, safe read/edit tooling, and validation. Use when asked to build a workflow, add/change/remove/rename a step, update a prompt/model/schema, wire steps together, summarize or explain a workflow, or diagnose why a workflow fails validation.
argument-hint: <workflow-json-file> [what to do]
allowed-tools: Read, Edit, Write, Grep, Glob, Bash(python3 *), Bash(cp *)
---

# Workflow JSON Skill

Entry point for creating, editing, and exploring workflow JSON files. This file states the rules, the read/edit protocol, and routes to the right reference doc. **Consult the linked references before authoring or editing JSON.**

Tool paths used throughout:

```
TOOLS="${CLAUDE_PLUGIN_ROOT}/skills/workflow/scripts/workflow_tools.py"
VALIDATE="${CLAUDE_PLUGIN_ROOT}/skills/workflow/scripts/validate_workflow.py"
```

API operations (download, upload draft, publish, execute) are owned by the `wb` skill — `/fai:wb`.

---

## Critical rules (non-negotiable)

1. **Never read or edit `workflow.json` with Read/Edit directly.** Files run 1,000–40,000+ lines. All reads and writes go through `workflow_tools.py`. The only files you write directly are temp files (`/tmp/wf_*.json`, `/tmp/wf_*.py`) passed to the tool.
2. **Never use the `dependencies` field.** Always `[]`; `input_mappings` is the only wiring for data flow AND execution order. On existing steps preserve whatever pattern is there; migrate `dependencies` → `input_mappings` only on explicit request; never the reverse.
3. **New custom code is a `custom_step`** (sandboxed). The in-process `function` type is legacy, edit-only — never author a new one or convert a step into one. Code is always inline in `config.code`; `config.func` does not exist on `custom_step` and must be `null` on legacy `function` steps.
4. **`system_prompt` goes in `input_mappings`** (usually `input_type: "static"`), never in `config`, on every step type that accepts one. `extract_from_multiple_sources` uses `email_system_prompt` + `document_system_prompt`.
5. **Validate after every edit**: `python3 $VALIDATE <workflow.json>`. Fix errors before finishing; never deliver an invalid workflow.
6. **No emojis or hardcoded dates** in prompts, system_prompts, or output content unless the user explicitly asks.

---

## Reading a workflow

Always start with `summary`, then drill in. Report findings concisely — never dump raw JSON.

| Command | Returns |
|---|---|
| `python3 $TOOLS summary <path>` | Step table: names, types, sizes, dependency wiring. **Run first.** |
| `python3 $TOOLS get-step <path> "<step>"` | One step, full JSON (`get-steps` for several) |
| `python3 $TOOLS get-code <path> "<step>"` | Inline Python of a function/custom step |
| `python3 $TOOLS get-config <path> "<step>"` | Config block (prompts, model, schemas) |
| `python3 $TOOLS get-input-mappings <path> "<step>"` | Where the step's data comes from |
| `python3 $TOOLS get-output-schema <path> "<step>"` | What the step produces |
| `python3 $TOOLS deps <path> "<step>"` | Upstream + downstream connections with attributes |
| `python3 $TOOLS get-prompts <path>` | Every prompt/instruction/schema with previews |
| `python3 $TOOLS search <path> "<regex>"` | Search names, config strings, code, params, schema fields |
| `python3 $TOOLS get-options <path>` | Workflow options block |

Workflow files are usually `workflow.json` (Glob `**/workflow.json`), but any path works.

## Editing a workflow

1. **Understand**: `summary`, then `get-step` + `deps` on the step(s) you'll touch.
2. **Back up** before large changes (`cp workflow.json workflow.json.bak`); skip for small tweaks.
3. **Edit** with the most surgical command. Write new content to a temp file, then apply:

| Change | Command | Temp file holds |
|---|---|---|
| Prompt/instruction | `set-prompt <path> "<step>" "<key>" /tmp/wf_prompt.txt` | plain text |
| Inline code | `set-code <path> "<step>" /tmp/wf_code.py` | Python |
| Config block | `set-config <path> "<step>" /tmp/wf_config.json` | full config JSON |
| Input wiring | `set-input-mappings <path> "<step>" /tmp/wf_mappings.json` | mappings array |
| Output schema | `set-output-schema <path> "<step>" /tmp/wf_schema.json` | schema object |
| Whole step | `set-step <path> "<step>" /tmp/wf_step.json` | full step JSON |
| Add step | `add-step <path> <index> /tmp/wf_new_step.json` | full step JSON |
| Remove step | `remove-step <path> "<step>"` (warns on broken downstream refs) | — |
| Rename step | `rename-step <path> "<old>" "<new>"` (updates all references) | — |
| Options | `set-options <path> /tmp/wf_options.json` | options JSON |

4. **Validate**: `python3 $VALIDATE <path>` — every edit, no exceptions. Triage via `reference/validation_errors.md`.
5. **Verify**: `get-step` / `deps` on the changed step.
6. **Log** (optional): `python3 $TOOLS log-change <path> "<summary>"` appends to the workflow directory's `STATE.md` when one exists.

Step names are case-sensitive; renames must go through `rename-step` so every `input_mappings` reference updates. For non-trivial changes (new steps, rewiring, schema changes), work in stages and validate after each stage.

---

## Verifying a workflow

`validate_workflow.py` answers "will the platform accept this". The verify
sub-skill answers the harder questions: does data actually arrive where it is
wired, can every decision branch and manual gate be reached, does the dashboard
render, is a registry module already doing this, and which fields is nobody
reading.

**Read-only.** It produces a report and a fix list; it never edits a workflow,
uploads, publishes or executes. Fixes go through the editing protocol above or
the `workflow-builder` agent.

```bash
VERIFY_DIR="${CLAUDE_PLUGIN_ROOT}/skills/workflow/scripts/verify"
python3 "$VERIFY_DIR/verify.py" <workflow.json> --out <dir>
```

Exit 0 clean, 1 blockers found, 2 the run itself failed. Typical run is 2 to
4 seconds and roughly 10k model-visible tokens; the whole point of the design
is that the model reads conclusions, not evidence.

| Flag | Use |
|---|---|
| `--only facts,flow` | run a subset (`facts` is added automatically when needed) |
| `--skip html` | skip a pass; `--skip html` avoids launching Chrome |
| `--escalate` | render the full HTML fixture matrix instead of one |
| `--no-screenshot` | lint and render HTML but capture no images |
| `--registry PATH` | module registry root (default `~/fai/module-registry`) |
| `--json` | machine-readable roll-up |

### The seven passes

| # | Pass | Answers |
|---|---|---|
| 0 | `wf_facts.py` | Builds `facts.json`. **The only script that reads `workflow.json`.** |
| 1 | `validate_workflow.py` | Will the platform accept it. Errors are blockers. |
| 2 | `flow_check.py` | Does every `output_attribute` resolve, to the right type. Closes the deep-path gap the validator documents. |
| 3 | `paths.py` | Decision worlds, reachability, dead branches, manual gates. |
| 4 | `registry_match.py` | Upgrade / adopt / extract-candidate against the module registry. |
| 5 | `html_probe.py` | Lints the dashboard, renders it locally, screenshots it. |
| 6 | `redundancy.py` | Unused fields, oversized schemas, duplicates, orphan steps. |
| — | `report.py` | Collapses overlapping findings into issues; writes `report.html` + `findings.json`. |

Individual passes take `--facts <facts.json> --out <dir>`; each has
`--self-test`. Run `verify.py` unless you are debugging one detector.

### Reading the output

- **Findings collapse into issues.** Several passes legitimately see the same
  defect, so a dead step appears once as "3 findings agree" rather than three
  times. The corroboration is signal, not noise.
- **"Cannot determine" is not "unused".** A field readable only by a docx
  template cannot be judged, because templates are download-only and these
  passes have no network. On a large workflow that bucket can be hundreds of
  fields and it is reported separately. Never delete a field on the strength of
  the undecidable count.
- **Coverage is stated, not implied.** Pass 2 reports what fraction of the
  wiring it type-checked. "Clean" means clean on what it could verify.
- **`SKIPPED` means a check did not run** — no Chrome, a legacy `function`
  step that cannot run locally, no registry. It never means clean.
- Some detectors have synthetic coverage only and say so.

Internals, the schema language, the findings shape and every threshold with the
measurement behind it: `scripts/verify/CONTRACT.md`.

---

## Step Type Index

Each linked doc covers contract, config, outputs, wiring, patterns, validation errors, and gotchas.

### Ingest
- [`workflow_dispatcher`](step_types/workflow_dispatcher.md) — auto-injected root, never authored
- [`prepare_documents`](step_types/prepare_documents.md) — fetch + translate + email-extract uploads
- [`classify_documents`](step_types/classify_documents.md) — bucket docs into named categories

### Knowledge Base
- [`knowledge_base`](step_types/knowledge_base.md) — vector index for downstream RAG

### Extraction
- [`extract_from_multiple_sources`](step_types/extract_from_multiple_sources.md) — email + docs KV with tie-breaking
- [`extract_rows_from_multiple_sources`](step_types/extract_rows_from_multiple_sources.md) — array-of-rows (claims, schedules)
- [`extract_mixed_schema_from_multiple_sources`](step_types/extract_mixed_schema_from_multiple_sources.md) — scalars + array in one call
- [`agentic_extraction`](step_types/agentic_extraction.md) — RAG-agent extraction (structured or free-form analysis)
- [`sov_mapping`](step_types/sov_mapping.md) — Excel SOV column alignment
- [`combine_kv_tables`](step_types/combine_kv_tables.md) — deterministic deep-merge of N KV tables

### Q&A / Compliance
- [`generate_qa_table_from_agent`](step_types/generate_qa_table_from_agent.md) — agentic KB Q&A
- [`multi_column_qa`](step_types/multi_column_qa.md) — per-row Q&A grid (checklists, scoring)
- [`agentic_guideline_check`](step_types/agentic_guideline_check.md) — RAG-agent guideline evaluation (preferred)
- [`extract_guidelines`](step_types/extract_guidelines.md) + [`check_guidelines`](step_types/check_guidelines.md) — legacy pair

### Comparison
- [`compare_document_data`](step_types/compare_document_data.md) — compare pre-extracted data objects
- [`compare_documents`](step_types/compare_documents.md) — edit-only legacy; split into extract + compare_document_data

### Screening & enrichment agents
- [`ofac_agent`](step_types/ofac_agent.md) — sanctions screening
- [`osha_agent`](step_types/osha_agent.md) — OSHA inspection records
- [`trellis_law`](step_types/trellis_law.md) — legal case history
- [`web_search`](step_types/web_search.md) — Perplexity-backed research with citations
- [`web_agent`](step_types/web_agent.md) — one recorded browser task from a prompt
- [`execution_matching`](step_types/execution_matching.md) — match prior executions of the same account/deal

### Integration connectors
- [`integrations.md`](step_types/integrations.md) — shared connector idiom + google_maps, nhtsa, riskmeter, hazardhub, maprisk, pitchbook, cotality_valuation, snapsheet, snapsheet_payments (edit-only), applied_epic, benefitpoint, qqcatalyst, ams360, sharepoint, outlook_mail, imageright

### Custom code / synthesis
- [`custom_step`](step_types/custom_step.md) — sandboxed inline Python (the only choice for new code)
- [`function`](step_types/function.md) — legacy in-process Python, edit-only
- [`submission_summary_generator`](step_types/submission_summary_generator.md) — AI narrative summary

### Control flow
- [`decision`](step_types/decision.md) — conditional branching (OR of ANDs)
- [`loop`](step_types/loop.md) — run member steps once per item of an iteration source
- [`pause`](step_types/pause.md) — manual approval gate (no data)
- [`hold`](step_types/hold.md) — block for correction (data passthrough)
- [`manual_input`](step_types/manual_input.md) — collect structured user input
- [`run_workflow`](step_types/run_workflow.md) — trigger a sub-workflow

### Output / delivery
- [`email`](step_types/email.md) — send via Zapier (or render UI-only)
- [`fill_docx`](step_types/fill_docx.md) — fill a Word template
- [`text_block`](step_types/text_block.md) / [`document_viewer`](step_types/document_viewer.md) — rarely used display steps

### Deprecated — the validator errors on new instances
[`extract_from_document`](step_types/extract_from_document.md), [`extract_from_email`](step_types/extract_from_email.md) → `extract_from_multiple_sources`; [`generate_qa_table_from_kb`](step_types/generate_qa_table_from_kb.md) → `generate_qa_table_from_agent`; [`enrich_addresses_with_gmaps`](step_types/enrich_addresses_with_gmaps.md) → `google_maps`. Each doc keeps an edit-only + migration reference.

---

## Routing — "I want to…"

| Intent | Use |
|---|---|
| Pull fields from email + docs, best value per field | `extract_from_multiple_sources` |
| Pull a list of rows (claims, locations, drivers) | `extract_rows_from_multiple_sources` |
| Rows AND scalars in one call | `extract_mixed_schema_from_multiple_sources` |
| Flexible extraction across any document via RAG | `agentic_extraction` |
| Loss runs | 3-step pattern — `reference/patterns/loss_runs.md` |
| Map an Excel SOV to canonical fields | `sov_mapping` |
| Merge several KV extractions into one table | `combine_kv_tables` |
| Guideline / compliance checks | `agentic_guideline_check` (or `multi_column_qa` for hardcoded grids) |
| Fixed question list over the docs | `generate_qa_table_from_agent` |
| Compare two extraction results | `compare_document_data` |
| Address/geo enrichment | `google_maps` (see `integrations.md`) |
| VIN decode / vehicle lookups | `nhtsa` (see `integrations.md`) |
| Property peril scores, AMS/claims systems | `integrations.md` |
| Sanctions / OSHA / litigation screening | `ofac_agent` / `osha_agent` / `trellis_law` |
| Web research with citations | `web_search` |
| Drive a website from a prompt | `web_agent` |
| Repeat steps per row/item | `loop` |
| Conditional branch | `decision` + `parent_conditions` downstream |
| Manual gate / correction / input | `pause` / `hold` / `manual_input` |
| Fan out to another workflow | `run_workflow` |
| Send email / fill Word template | `email` / `fill_docx` |
| Anything else (transform, aggregate, format, API call) | `custom_step` |

---

## Conceptual references

| Topic | Reference |
|---|---|
| Top-level workflow + step envelope fields, schema-title rule | `reference/workflow_structure.md` |
| `input_mappings`, glom paths, cell `.value` rule, resolution operators, type compatibility, file-input sources | `reference/input_mappings.md` |
| Required input mappings per step type (scaffolding cheat sheet) | `reference/required_inputs.md` |
| `output_type` decision tree, auto-fill map, mismatch errors | `reference/output_types.md` |
| Model picker, reasoning_effort / verbosity compatibility | `reference/model_compatibility.md` |
| Workflow `options` block, `control_plane_mappings` secrets | `reference/options.md` |
| Validation error catalog + fixes | `reference/validation_errors.md` |
| Versioning, drafts, publish lifecycle | `reference/versioning.md` |
| Loss runs / HTML dashboard / KB wiring patterns | `reference/patterns/` |
| Verify sub-skill internals: schema language, findings shape, thresholds | `scripts/verify/CONTRACT.md` |
| Migration: legacy `dependencies` → `input_mappings` | `reference/migration/legacy_to_input_mappings.md` |
| Known-good example workflow | `reference/examples/` (inspect via `workflow_tools.py`) |

---

## Standard workflow skeleton

```
Workflow Dispatcher (auto)
  → Prepare Documents
  → Classify Documents        (and/or → Initialize Knowledge Base, in parallel)
  → extraction step(s)        (classified subsets, not the full document set)
  → analysis steps            (guideline checks, screening agents, integrations)
  → optional: custom_step composing data_points → submission_summary_generator
  → optional: custom_step composing an HTML dashboard
  → email step                (UI-only or actual send)
```

Steps with no shared inputs run in parallel when `options.enable_parallel_execution: true`. Array order is display order only — execution follows the `input_mappings` DAG.

---

## The most frequent pitfalls

Detailed catalog: `reference/validation_errors.md`.

1. **Wrong input parameter name.** `email_body` → `body_html`; `knowledge_base_instance` → `kb`; `entity_data` (osha) → `insured_data`. Canonical names per step doc; scaffold from `reference/required_inputs.md`.
2. **Resolution operator uppercase or wrong name.** Lowercase only: `concat_lists`, `merge_dicts`, `numeric_add`, `string_join`, `custom_function`.
3. **`data.<field>` instead of `data.<field>.value`.** Extraction leaves are cell objects; wire `.value` (rows: `data.0.<col>.value`).
4. **Setting `output_type` on steps that auto-fill.** Default is null/null/null; only `function`/`custom_step` and `submission_summary_generator` are author-controlled — see `reference/output_types.md`.
5. **Missing schemas on code steps.** `config.input_schema` + `config.output_schema` are both required with `code` + `input_mappings`.
6. **Forgetting `documents` on KB-consuming steps** (`agentic_extraction`, `multi_column_qa`, …) — citations can't resolve without it; never wire bare `Classify Documents.documents` (object, not array) — use `documents.<Class>`.
7. **`reasoning_effort`/`verbosity` on an incompatible model.** See `reference/model_compatibility.md`.
8. **Decision conditions flat or long-form.** Must be OR-of-ANDs `[[c1, c2]]` with short-form operators (`eq`, `gt`, `is_empty`).
9. **Missing schema `title`s.** Every authored schema property carries a human-readable Title Case `title` (acronyms uppercase: TIV, NAICS, FEIN).
10. **`incremental_config.behavior: "skip"`.** Not valid — use `no_op` (`rerun`, `append`, `preserve_edits` per step-type rules).

---

## Editing checklist

- [ ] Step `type` is in the validator's enum (it rejects unknown and deprecated types).
- [ ] `dependencies: []` on every new/modified step.
- [ ] `input_mappings[*].input_parameter_name` matches the step's input schema; sources exist.
- [ ] `output_attribute` paths exist in upstream output schemas (`.value` for extraction cells).
- [ ] Lowercase resolution operators; correct config field names.
- [ ] `output_type`/`output_config`/`output_schema` follow the decision tree.
- [ ] Model + reasoning_effort + verbosity compatible.
- [ ] Code steps have both schemas; entry function is `async def run(..., **kwargs)`.
- [ ] **`validate_workflow.py` passes.**

---

## When you're stuck

1. Don't load the JSON — `workflow_tools.py summary` / `get-step` the part you need.
2. Read the relevant `step_types/*.md` — every documented step has a full contract.
3. Run the validator; map errors via `reference/validation_errors.md`.
4. Compare against `reference/examples/submission_intake_es_umbrella.json` (known-good, validator-clean).

---

## Building a workflow with the agent team

For non-trivial work — a new workflow, or an edit that adds/removes steps, changes schemas, or rewires data flow — the main thread orchestrates four plugin agents. Only the main thread spawns agents; agents never spawn agents. Trivial edits (prompt wording, typo, model swap) skip the team and use the editing protocol above directly.

| Agent | Does | Writes |
|---|---|---|
| `workflow-designer` | Requirements → `DESIGN.md` (mode: new \| scoped-edit) | `DESIGN.md` |
| `workflow-builder` | Approved `DESIGN.md` → validated `workflow.json` | `workflow.json`, `CHANGES.md`, `workflow.json.bak` (scoped-edit baseline) |
| `workflow-validator` | Structural + semantic + scope-drift review vs `DESIGN.md` | nothing — verdict report only |
| `workflow-tester` | Draft test run on the platform, graded against the design's Test Expectations | platform draft + test executions only |

Flow (artifacts in parentheses):

1. Spawn **workflow-designer** with mode, workflow dir, requirements, sample-doc paths. (→ `DESIGN.md`)
2. **USER GATE — design approval.** Surface the design summary + open questions; the user approves or amends. Never build an unapproved design.
3. Spawn **workflow-builder** with the workflow dir. (→ `workflow.json`, validator-clean)
4. Spawn **workflow-validator** with the workflow dir. (→ verdict)
   - FAIL: send the fix list back through **workflow-builder** (in-scope fixes) or **workflow-designer** (design gaps), then re-validate. Loop until PASS / PASS WITH NOTES.
5. Orchestrator uploads the draft via `/fai:wb` (`wb.py upload`) — a prod write to a customer org, so confirm org + workflow with the user first.
6. Spawn **workflow-tester** with env/account, workflow ID, workflow dir, test-doc paths. (→ test report; the tester re-syncs the draft itself on later loops)
   - FAIL: route per its triage — builder fix → re-validate → re-test; design gap → back to step 1 (scoped-edit).
7. **USER GATE — publish.** Present the test report; the user decides. Publish via `/fai:wb` (`wb.py publish`) — never automatic, never `--allow-override` without the user naming it.

Design/build contract templates live in `reference/templates/` (`DESIGN-new.md`, `DESIGN-scoped-edit.md`, `CHANGES.md`).
