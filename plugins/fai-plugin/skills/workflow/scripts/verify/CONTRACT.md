# Verify sub-skill — build contract

Every script in this directory is built against this file. It is the only
coordination point: get the shapes here right and the passes compose without
anyone reading anyone else's source.

Read this whole file before writing a line. If something here is wrong or
underspecified, say so in your report rather than inventing a variant.

---

## Hard rules

1. **stdlib only.** No pip packages, ever. Python 3.9+ compatible (no `match`,
   no `X | Y` in annotations at runtime — use `Optional[X]` / `Union`).
2. **Nothing reads `workflow.json` except `wf_facts.py`.** Every other pass
   reads the facts file. A 2.7 MB workflow must never be parsed twice.
3. **Read-only.** No script in this directory writes to the workflow, uploads,
   publishes, or executes anything. The only files written are under the
   output directory passed in via `--out`.
4. **Bounded stdout.** Every script prints a human-scannable summary that is
   capped (see "Output discipline"). Full detail goes to JSON on disk. Model
   context is the scarce resource; a script that dumps 3,000 rows is broken.
5. **Exit codes.** `0` = ran clean, no blockers. `1` = blockers found. `2` =
   the script itself failed (bad input, crash). Never exit 1 for an internal
   error.
6. **No network.** Exception: `registry_match.py` may shell out to
   `registry.py` locally. Nothing calls the FurtherAI API from this directory.

---

## Directory layout

```
skills/workflow/scripts/verify/
    CONTRACT.md          this file
    common.py            shared: schema language, path resolver, findings, output
    wf_facts.py          pass 0 — the only workflow.json reader
    flow_check.py        pass 2 — data flow and type resolution
    paths.py             pass 3 — decision lattice, reachability, manual gates
    registry_match.py    pass 4 — module upgrade / adopt / extract candidates
    html_probe.py        pass 5 — HTML lint, local render, screenshot
    redundancy.py        pass 6 — unused fields, duplicates, size, smells
    report.py            roll-up — HTML report + findings.json
    verify.py            orchestrator CLI
```

Pass 1 (structural) is the existing `../validate_workflow.py`, called by
`verify.py`. Nobody reimplements it.

---

## Common CLI shape

Every pass script takes the same two arguments plus its own options:

```
python3 <pass>.py --facts <facts.json> --out <dir> [pass-specific flags]
```

`wf_facts.py` is the exception:

```
python3 wf_facts.py <workflow.json> --out <dir>       # writes <dir>/facts.json
```

Each pass writes `<dir>/<passname>.json` holding `{"pass": ..., "findings": [...], "<pass data>": ...}`.

---

## The schema language

Platform schemas are messy (JSON Schema, plus named types, plus cell wrapping,
plus dynamic classify keys). `wf_facts.py` normalizes every step's output into
one recursive shape. Everything downstream reasons about this, never raw JSON
Schema.

```jsonc
{
  "kind": "object",            // see kinds below
  "properties": {              // kind=object only
    "data": { "kind": "object", "properties": { ... } }
  },
  "dynamic_keys": ["ACORD", "Loss_Run"],   // kind=object only, optional:
                                           // keys known from config, not schema
                                           // (classify_documents classes)
  "open": false,               // kind=object: true when arbitrary keys are legal
                               // (nothing can be proven absent)
  "items": { ... },            // kind=array only
  "value_type": { ... },       // kind=cell only — the type inside .value
  "title": "Named Insured",    // optional, carried through when declared
  "description": "...",        // optional
  "allowed_values": ["A","B"], // optional, from enum
  "origin": "declared"         // "declared" | "auto" | "config" | "inferred"
}
```

### Kinds

| kind | Means |
|---|---|
| `object` | Has named properties. `properties` may be empty with `open: true`. |
| `array` | Ordered list; `items` describes an element. |
| `string` `number` `boolean` | Scalars. |
| `file` | A document reference (`{user_document_id, filename, ...}`). The platform's named `file` type. |
| `cell` | An extraction leaf: `{value, confidence_score, confidence_reason, citations, thinking_steps}`. `value_type` holds the real type. |
| `table` | A table output (`{data: [...], schema: {...}}` plus `table_v1_log_id`). |
| `knowledge_base` | A KB handle. Opaque. |
| `unknown` | We could not determine it. Downstream treats this as "cannot prove wrong" and never raises a blocker on it alone. |
| `any` | Explicitly untyped and legal (e.g. a `hold` passthrough). |

`unknown` vs `any` matters: `unknown` means our resolver gave up and should
say so at note severity; `any` means the platform genuinely accepts anything.

### Cell wrapping

Extraction step types wrap every leaf under `data` as a `cell`. `wf_facts.py`
applies this to: `extract_from_multiple_sources`,
`extract_rows_from_multiple_sources`,
`extract_mixed_schema_from_multiple_sources`, `agentic_extraction`,
`agentic_guideline_check`, `sov_mapping`, `generate_qa_table_from_agent`,
`multi_column_qa`. Source of truth for that list:
`../../reference/input_mappings.md` ("Extraction outputs are cell-wrapped").

So a declared extraction field `named_insured: string` becomes
`data.named_insured` of kind `cell` with `value_type` string. A path stopping
at `data.named_insured` resolves to `cell`; `data.named_insured.value`
resolves to `string`. Rows: `data` is an `array` whose `items` is an object of
cells, so `data.0.vin.value` resolves.

---

## The path resolver (`common.py`)

The single most-reused function in the sub-skill. Get its contract exactly
right.

```python
def resolve_path(schema, path):
    """Resolve a glom-style dotted path against a normalized schema.

    schema: a normalized schema dict (the language above)
    path:   None or "" -> the whole schema; else dot-delimited, numeric
            segments index arrays ("data.0.vin.value")

    Returns a dict, always one of:
      {"ok": True,  "type": <normalized schema>, "note": <str|None>}
      {"ok": False, "error": <str>, "at": <str>, "available": [<str>, ...]}

    - "at" is the path prefix that failed, so callers can report precisely.
    - "available" lists the real keys at the failure point (capped at 25,
      sorted) so the message is actionable. Empty list when not applicable.
    - Resolving into kind=unknown returns ok=True with type kind=unknown and
      note="path not verified past <segment> (unknown schema)".
    - Resolving into an object with open=True and an unlisted key returns
      ok=True, kind=unknown, note="key not declared; object is open".
    - Resolving a dynamic_keys object with a key not in dynamic_keys returns
      ok=False with available=dynamic_keys — this is the classify typo case
      and it must be catchable.
    """
```

Also in `common.py`:

```python
def type_name(schema) -> str      # "array<file>", "cell<string>", "object" — for display
def types_compatible(expected, actual) -> bool
    # Directional, mirrors platform save-time behavior:
    #  - unknown/any on either side -> True (never block on ignorance)
    #  - object accepts file; file does NOT accept plain object
    #  - number accepts number; string does not accept number
    #  - cell accepts nothing except cell (this is the .value bug detector)
    #  - array<X> accepts array<Y> iff X accepts Y
def schema_leaf_paths(schema, prefix="") -> List[str]   # every leaf path, capped depth 12
```

---

## `common.py` public API — exact signatures

`common.py` is owned by the facts agent and is the import surface for every
other pass. These names are pinned. Do not rename, do not add positional
parameters, and do not write a competing local copy — if `common.py` is not
on disk yet, build against these signatures behind a temporary shim and
delete the shim when it lands.

```python
THRESHOLDS  # dict, keys exactly as in the Thresholds table below

SEVERITIES = ("blocker", "warning", "note")

def finding(code, severity, title, detail,
            *, step=None, fix=None, evidence=None, group_key=None) -> dict:
    """Build one finding. Returns the dict from the Findings section WITHOUT
    the "pass" key — PassOutput.write() stamps that. Raises ValueError on an
    unknown severity, so a typo fails loudly instead of silently dropping a
    blocker."""


class PassOutput:
    """Collector + writer + bounded printer for one pass."""

    def __init__(self, pass_name, out_dir): ...
        # pass_name is one of: facts|flow|paths|modules|html|simplify

    def add(self, finding_dict) -> None: ...
        # append one finding (the dict returned by finding())

    def add_all(self, findings) -> None: ...

    def set(self, key, value) -> None: ...
        # attach pass-specific payload, e.g. set("proposals", [...])
        # forbidden keys: "pass", "findings"

    def counts(self) -> dict: ...
        # {"blockers": n, "warnings": n, "notes": n}

    def write(self) -> "pathlib.Path": ...
        # writes <out_dir>/<pass_name>.json = {"pass": ..., "findings": [...], **extras}
        # stamps "pass" on every finding. Returns the path.

    def print_summary(self, extra_lines=None) -> int: ...
        # bounded print per Output discipline: <=MAX_FINDINGS_PRINTED findings,
        # blockers first, group_key collapsed with counts, then the
        # machine-parseable SUMMARY line last. extra_lines is an optional list
        # of already-capped strings to print above the findings.
        # Returns the intended process exit code: 1 if any blocker, else 0.


def load_facts(path) -> dict: ...
    """Read and lightly validate facts.json. Raises SystemExit(2) with a clear
    message when the file is missing, unparseable, or schema_version != 1."""


def truncate(text, limit) -> str: ...
def table(rows, headers, max_rows=10) -> List[str]: ...
    """Format a bounded text table. Returns lines, appends '... N more' when
    truncated. Use this rather than hand-rolling column math."""
```

Standard tail of every pass script:

```python
out = PassOutput("html", args.out)
# ... add findings ...
out.write()
sys.exit(out.print_summary())
```

---

## The facts file

`wf_facts.py` writes `<out>/facts.json`. This is the contract every other
pass depends on. Shape:

```jsonc
{
  "schema_version": 1,
  "generated_by": "wf_facts.py",
  "workflow": {
    "path": "/abs/path/workflow.json",
    "name": "CNA DUA Audit - LPL",
    "bytes": 680895,
    "step_count": 29,
    "options": { ... },                  // verbatim workflow options block
    "control_plane_mappings": { ... }    // verbatim, keys only if values look secret
  },

  "steps": [
    {
      "index": 0,
      "name": "Prepare Documents",
      "type": "prepare_documents",
      "display_step": true,
      "start_title": null,
      "end_title": "Documents prepared",
      "output_type": null,
      "enable_rerun": false,
      "incremental_behavior": "rerun",
      "parent_conditions": [ {"decision_step": "Gate", "branch": "Branch A"} ],
      "loop_membership": [],
      "is_terminal": false,
      "is_manual": false,
      "is_hidden": false,                 // display_step is false
      "size_bytes": 12043,                // json.dumps length of the step
      "model": null,
      "reasoning_effort": null,
      "verbosity": null,
      "prompt_chars": 0,
      "prompts": [ {"key": "system_prompt", "where": "input_mappings", "chars": 812} ],
      "code_chars": 0,
      "code": null,                       // or the object below, for code steps
      "packages": [],
      "output": { <normalized schema> },  // the resolved output schema
      "declared_output_schema_present": true
    }
  ],

  "code_steps": {                          // keyed by step name, code steps only
    "Standardize Extracted Data": {
      "is_async": true,
      "params": ["standardized_data", "gc_alpha"],
      "has_kwargs": true,
      "returned_keys": ["email_body"],     // union of literal dict keys across returns
      "returns_dynamic": false,            // true if any return is not a literal dict
      "declared_input_properties": ["standardized_data", "gc_alpha"],
      "declared_output_properties": ["email_body"],
      "imports": ["html", "fai_sandbox.context"],
      "syntax_ok": true,
      "parse_error": null
    }
  },

  "edges": [
    {
      "to_step": "Extract LPL Quote Fields",
      "to_param": "documents",
      "input_type": "dependency",          // dependency | static | variable | prompt | function
      "from_step": "Classify LPL Documents",
      "output_attribute": "documents.LPL_Quote",
      "source_index": 0,                   // position in dependency_step_outputs
      "multi_source": false,
      "resolution_operator": null,
      "via": "input_mappings",             // or "decision_operand"
      "resolved": true,
      "resolved_type": "array<file>",      // type_name() of the resolved schema
      "resolved_schema": { ... },          // normalized, for consumers that need it
      "resolve_error": null,
      "resolve_note": null
    }
  ],

  "fields": [
    {
      "step": "Extract LPL Application Fields",
      "path": "data.named_insured",        // path to the CELL, not .value
      "value_path": "data.named_insured.value",
      "name": "named_insured",
      "type": "string",                    // the value type, unwrapped
      "title": "Named Insured",
      "description": "Exact legal named insured.",
      "allowed_values": [],
      "is_cell": true,
      "depth": 2
    }
  ],

  "decisions": [
    {
      "step": "LPL Comparison Gate",
      "default_branch": "",
      "input_schema_properties": ["has_pairs"],
      "cases": [
        {
          "index": 0,
          "branch": "Compare LPL Documents",
          "branch_step_exists": true,
          "comparison_count": 1,
          "condition": [
            [ {"left": {"kind": "variable", "ref": "has_pairs"},
               "op": "is_true",
               "right": {"kind": "static", "value": ""},
               "operand_step": null} ]
          ]
        }
      ],
      "variables": {
        "has_pairs": {"wired": true, "from_step": "Prepare LPL Comparison Data",
                      "output_attribute": "has_pairs", "resolved_type": "boolean"}
      }
    }
  ],

  "manual_steps": [
    {"step": "Notes", "type": "manual_input", "field_count": 3,
     "fields": ["note_text"], "passthrough_params": [], "enable_rerun": false}
  ],

  "graph": {
    "adjacency": { "Prepare Documents": ["Classify LPL Documents"] },
    "reverse":   { "Classify LPL Documents": ["Prepare Documents"] },
    "roots": ["Prepare Documents"],
    "terminals": ["Notes"],
    "cycles": []
  },

  "html_producers": [
    {"step": "Compose HTML Summary", "output_field": "email_body",
     "consumed_by": [{"step": "Submission Summary", "param": "email_body"}],
     "code_chars": 18422, "interpolated_paths": []}
  ],

  "counters": {
    "steps": 29, "schema_leaves": 907, "edges": 84, "max_schema_depth": 6,
    "prompt_chars": 6641, "code_chars": 194078, "decisions": 1, "cases": 1,
    "manual_steps": 1, "hidden_steps": 4, "html_producers": 1,
    "unresolved_edges": 0
  }
}
```

### Notes on producing it

- `Workflow Dispatcher` is implicit. It is a legal `from_step` and is not in
  `steps`. Give it a synthetic output: object with `user_document_ids`
  (array of string) and `documents` (array of file), plus `open: true`.
- Terminal step types: `email`, `fill_docx`, `submission_summary_generator`,
  `document_viewer`, `text_block`, `hold`.
- Manual step types: `pause`, `hold`, `manual_input`.
- Code step types: `custom_step` and legacy `function`.
- Decision operands may reference upstream steps inline while
  `input_mappings` is empty. Those references MUST appear in `edges` with
  `via: "decision_operand"` and MUST appear in `graph.adjacency`. A workflow
  in the corpus (`cna_dua_audit_lpl.json`, step `LPL Comparison Gate`) relies
  on this; a graph that misses it reports the gate as unreachable, which is
  the single worst false positive this sub-skill could produce.
- `prompt_chars` counts config strings over 200 chars plus static
  `input_mappings` values over 200 chars. Keep the rule in one helper.
- Never put step code or prompt text in the facts file. Only lengths, keys,
  and derived facts. The facts file for the 2.7 MB workflow should land well
  under 3 MB and is never read into model context.

---

## Findings

Every pass emits findings in one shape. `common.py` owns the constructor and
the JSON writer.

```jsonc
{
  "pass": "flow",                 // facts|flow|paths|modules|html|simplify
  "code": "UNRESOLVED_PATH",      // stable SCREAMING_SNAKE, greppable
  "severity": "blocker",          // blocker | warning | note
  "step": "Extract LPL Quote Fields",   // or null when workflow-level
  "title": "Path does not exist in source output",
  "detail": "`documents.LPL_Quotes` on Classify LPL Documents: no such class. Available: LPL_Application, LPL_Binder, LPL_Policy, LPL_Quote.",
  "fix": "Change the output_attribute to `documents.LPL_Quote`.",
  "evidence": { "from_step": "...", "output_attribute": "..." },
  "group_key": "UNRESOLVED_PATH:Classify LPL Documents"   // optional, for rollup
}
```

Severity means exactly this:

- **blocker** — the workflow will fail, or will silently produce wrong or
  empty results. Something must change.
- **warning** — probably wrong or risky, but a defensible author could have
  meant it.
- **note** — advisory, simplification, or an observation.

Do not promote a warning to a blocker without specific evidence. An
`unknown` type is never on its own a blocker.

---

## Output discipline

`common.py` provides the printer. Rules it enforces:

- At most **40 findings** printed per pass, blockers first, then warnings,
  then notes. Beyond that, print `... N more (see <file>)`.
- Findings sharing a `group_key` collapse to one line with a count.
- Tables print at most **10 example rows** plus a count.
- No pass prints raw schemas, raw code, or raw prompts. Ever.
- One-line summary as the last stdout line, machine-parseable:
  `SUMMARY <pass> blockers=<n> warnings=<n> notes=<n> out=<path>`

---

## Thresholds (approved, keep in one place)

`common.py` exposes these as a single dict so they are tunable in one edit:

| Name | Value | Used by |
|---|---|---|
| `MAX_FIELDS_PER_STEP` | 40 | redundancy |
| `MAX_PROMPT_CHARS` | 8000 | redundancy |
| `DUP_STEP_OVERLAP` | 0.75 | redundancy |
| `ADOPT_MIN_OVERLAP` | 0.50 | registry_match (candidate floor) |
| `ADOPT_STRONG_OVERLAP` | 0.75 | registry_match (report as strong) |
| `MAX_CASE_COMPARISONS` | 6 | paths |
| `MAX_RESHAPE_CHAIN` | 2 | redundancy |
| `MAX_PATHS_SHOWN` | 20 | paths |
| `MAX_FINDINGS_PRINTED` | 40 | common printer |
| `SHOT_WIDTH` | 900 | html_probe |
| `SHOT_MAX_HEIGHT` | 2000 | html_probe |

---

## Reference docs — the source of truth for rules

This sub-skill syncs with the workflow skill, **not** the backend repo. When a
rule is needed, it comes from these files and the code carries a comment
naming the one it came from:

- `../../reference/input_mappings.md` — glom paths, cell wrapping, resolution
  operators, type compatibility
- `../../reference/output_types.md` — which steps auto-fill output_type
- `../../reference/workflow_structure.md` — step envelope fields
- `../../reference/required_inputs.md` — required inputs per step type
- `../../reference/validation_errors.md` — error catalog
- `../../step_types/<type>.md` — per-step contract, config keys, output schema
- `../../reference/patterns/html_summary_dashboard.md` — dashboard standard

If a rule cannot be found in those, do not invent platform behavior. Emit
`unknown` and a note.

---

## Test corpus

Four real workflows, all readable, none to be modified:

```
/Users/andrewjeffers/Documents/Work/contractors/workflows/
    cna_dua_audit_ads.json    2.70 MB, 58 steps, 3825 schema leaves, 172 edges
    cna_dua_audit_lpl.json    0.68 MB, 29 steps,  907 leaves,  84 edges, 1 decision
    submission_intake.json    0.66 MB, 18 steps, 1284 leaves,  24 edges
    quantum_cny.json          0.28 MB,  6 steps,  577 leaves,   6 edges
```

Plus the known-good validator-clean example:
`../../reference/examples/submission_intake_es_umbrella.json` (30 steps,
includes the reference `Compose HTML Summary` step).

Performance bar: the whole facts build on the 2.7 MB workflow must stay under
3 seconds. For reference, `validate_workflow.py` does its full pass on that
file in 0.07s.

Every script ships with a `--self-test` flag that runs it against the corpus
and asserts its own invariants. No pytest, no test framework: a `--self-test`
that exits non-zero on failure and prints what broke.

---

## Amendments — rev 1, after the facts layer shipped

These are rulings, not proposals. They override anything above that
contradicts them. Build against these.

### A1. `facts.json` IS pass 0's output. One file, both shapes.

Pass 0 is named `facts`, so `PassOutput.write()` targets `<out>/facts.json`,
which is the same path every other pass reads via `--facts`. The first
implementation silently clobbered the facts file down to a stub.

Resolution: the facts payload *is* pass 0's pass-output. One file carrying
`{schema_version, pass, counts, findings, workflow, steps, edges, fields,
decisions, manual_steps, graph, html_producers, code_steps, counters}`. It
satisfies the facts-file shape and the per-pass shape simultaneously, and
`load_facts` reads it unchanged. **Do not re-split this into two files.**

### A2. `edges` covers every declared input, not only dependency wiring.

Non-dependency inputs (`static`, `prompt`, `variable`, `function`) appear as
edges with `from_step: null`. This is deliberate: `flow_check` needs to see
every declared parameter to catch a required input that is present but not
wired, not just the wired ones.

Counters are therefore split, and both keys are required:

- `counters.edges` — every input mapping, all input types.
- `counters.dependency_edges` — only `input_type: "dependency"` entries.

The corpus table earlier in this file quotes dependency-edge counts (84 for
LPL, 172 for ads). Those remain correct for `dependency_edges`. Totals across
all input types run higher (94 and 202).

### A3. Five additional kinds, sourced from the reference docs.

`../../reference/input_mappings.md`'s type-compatibility table names
`video_file`, `email_headers`, `citation`, `docx_image` and `sov_field` as
named types whose directional rule is "accepts only themselves". Collapsing
them into `object` would lose that rule, so they join the kind set as
first-class kinds. `types_compatible` enforces the self-only rule for each.

### A4. A numeric segment against an `open` non-array node degrades.

`resolve_path` returns `ok: True` with kind `unknown` plus a note, rather than
failing. Without this rule, open objects generate false blockers.

### A5b. Facts-file size bar raised to 8 MB.

The A6 fix fanned nested arrays into their row fields, so the ads workflow's
field count went from 425 to 517 and its facts file from 2.5 MB to 3.40 MB,
past the 3 MB figure quoted earlier in this document. The bar is now 8 MB and
the earlier number is superseded.

The reasoning that made 3 MB arbitrary in the first place: the facts file never
enters model context, and parse cost is immaterial (0.17s to build the ads
facts, 3ms for `flow_check` to consume them). Size only matters as a smoke
signal for accidental duplication. 8 MB leaves room for a workflow twice the
size of the largest one we have while still catching a runaway.

### A5. `resolved_schema` stays on every edge.

It is the bulk of the facts file (the ads workflow lands at 2.5 MB, under the
3 MB bar, mostly from field descriptions duplicated across `steps[].output`,
`fields[]` and each edge's `resolved_schema`). Keeping it stands: the facts
file never enters model context, and parse cost is immaterial at 0.06s for
the largest workflow. Consumers get a fully resolved type without walking
back to the producing step.

### B — rulings on the contract gaps the passes found

Numbered separately from the A series because these answer questions the
original document never asked. All are rulings.

#### B1. A table step's columns MUST be exposed under `data`.

`kind: table` is not an opaque node. `data` normalizes to an `array` whose
`items` is an object carrying every column, cell-wrapped only when the step
is one of the eight extraction types. Never collapse a table step to a single
node.

Why it matters: module artifacts carry a `custom_step` table's columns as real
schema properties, so a collapsed table step scores zero field overlap against
its own module and nothing reports the failure. Silent zero, not an error.

#### B2. Envelope keys are stripped from every field-set comparison.

Extraction and table outputs carry platform-added root keys: `metadata`,
`schema`, `user_documents` (documented at `../../reference/output_types.md`)
and `table_v1_log_id`. They are roughly **13% of all catalog leaves** and are
near-identical across steps, so counting them deflates every similarity score
by a near-constant and makes every overlap threshold in this document wrong by
that amount.

Ruling: strip them at the root on both sides of any field-set comparison.
This binds **both** `registry_match.py` (adopt scoring, rename detection) and
`redundancy.py` (`DUP_STEP_OVERLAP`, duplicate-field detection), which have
identical exposure.

Because two passes must strip identically or their thresholds mean different
things, this lives in **one shared helper in `common.py`**, not in each pass:

```python
def comparable_fields(step_output, *, authored_only=True) -> List[str]:
    """Field identities for similarity comparison, envelope stripped.

    One entry per authored field. Cells count once (not as `.value` plus
    siblings). Opaque named types are not descended. `dynamic_keys` count as
    fields, so classify steps are comparable. Root envelope keys are removed.
    This is the ONLY sanctioned input to any overlap score.
    """
```

`schema_leaf_paths` stays as it is: it answers the resolver's question and is
the wrong input for matching. Any pass computing an overlap score calls
`comparable_fields` and nothing else.

#### B3. `fields[]` is the reporting inventory, not the comparison input.

`fields[]` is the authored, human-facing field inventory: one entry per
authored field on steps that declare an extraction or table schema, envelope
keys excluded, cell sub-members excluded. That is why ads reports
`counters.fields` 517 against `counters.schema_leaves` 3825, and both numbers
are correct for what they count.

Use it for reporting, unused-field analysis and the labelling-style inventory.
Do **not** derive an overlap score from it; use `comparable_fields` (B2).
A pass may state a field count from either source provided it says which.

#### B4. Threshold ownership has one test.

Does changing this value change which findings a human sees? If yes it is
policy and belongs in `common.THRESHOLDS`. If no it is an implementation limit
and stays local.

By that test `MIN_EXTRACT_FIELDS` and `MAX_EXTRACT_INPUTS` are policy, because
they gate whether an extract candidate is proposed at all, so they move into
`THRESHOLDS`. Recursion depth caps, buffer sizes and print column widths stay
local. The contradictory comment in `common.py` should be corrected to state
this test.

#### B5. Adopt scoring is per module STEP, not per module.

Confirmed. A module has many unrelated steps, so a module-level field union
would mix them and make the "step types must agree" rule meaningless. Score
each workflow step against each module step. Every proposal names
`module@version` **and** `module_step`.

#### B6. A lockfile is the only exact answer to "which version is installed".

All four `workflow_base` releases declare identical step names, as do
`pc_common_foundation` 2.0.0 and 2.1.0. Version is therefore often undecidable
from artifacts alone.

Ruling: a `config.json` / `config.lock.json` beside the workflow is the only
exact source. Its absence downgrades the output: no asserted version, no
mechanism, no imperative fix line. Say what matched, say what could not be
determined, and tell the user that placing a config beside the workflow makes
it knowable next run.

#### B7. `SUMMARY` is always the last line. No exception.

My "exit 0 with a single note and no other output" for a missing registry
conflicted with Output discipline. Output discipline wins: `SUMMARY` is
machine-parseable and the orchestrator parses it. "No other output" meant no
tables and no proposals, not no summary.

#### B9. Four thresholds added, and `MAX_SCHEMA_DEPTH` is defined against one metric.

`THRESHOLDS` gains, all policy by B4's test:

| Name | Value | Gates |
|---|---|---|
| `MAX_SCHEMA_DEPTH` | 5 | "excessive nesting" half of the oversized-schema detector |
| `MIN_DUP_STEP_FIELDS` | 2 | degeneracy guard on duplicate-step Jaccard |
| `MIN_WHOLESALE_FIELDS` | 2 | suppresses the which-field-is-read note on a 1-field step |
| `MIN_EXTRACT_FIELDS` | 4 | extract-candidate floor |
| `MAX_EXTRACT_INPUTS` | 3 | extract-candidate input-boundary limit |

**`MAX_SCHEMA_DEPTH` is measured against `fields[].depth`** — the authored
field depth recorded in facts — and against nothing else. Not resolver
traversal depth, not depth including cell members. Two passes measuring depth
differently would make the number mean two different things, which is the same
failure the envelope-key ruling (B2) exists to prevent. State the metric
wherever the value is read.

The value comes from the corpus rather than from feel. Pooled max-depth per
step across all five workflows, 141 steps with a schema:

```
depth: 1→37 steps  2→45  3→23  4→34  5→1  6→1
threshold 6 flags   1/141 (1%)
threshold 5 flags   2/141 (1%)     <- chosen
threshold 4 flags  36/141 (26%)
threshold 3 flags  59/141 (42%)
```

Depth 4 is ordinary for insurance extraction (`data.limits.each_occurrence.value`),
so a threshold of 4 would flag a quarter of all steps and read as noise. 5
catches the genuine outliers only. If a pass reports a different maximum than 6
for this corpus, it is measuring a different thing and should say so.

#### B13. Two degeneracy floors, one concept, one pinned relationship.

`ADOPT_MIN_FIELDS` (3) and `MIN_DUP_STEP_FIELDS` (2) guard the same thing: a
Jaccard ratio carries no information on a very small set. B2's own logic warns
that two numbers for one concept drift apart, and that warning is correct.

They stay different anyway, for a reason that has to be written down or it will
look like an oversight:

**The cost of a false positive is asymmetric.** An adopt proposal says "replace
your step with this module", which a human may act on and which rewires a live
workflow. A duplicate-step note asks "are these two the same, would a loop be
better", which costs a moment's thought. Stricter evidence is required to
recommend an action than to ask a question.

**And the value of small sets differs.** On the adopt side, raising the floor
to 3 loses nothing measurable: the corpus separates cleanly with corroborated
matches at 0.92 and 0.93 and nothing above 0.29 below them. On the
duplicate-step side, the single most actionable finding in the corpus —
`Fill Alpha/Beta/Delta Workbook`, three code steps with byte-identical sources
differing only by carrier template — is a 2-field group and would be lost at 3.

So the ruling is not one number, it is a **pinned relationship**:

```
ADOPT_MIN_FIELDS >= MIN_DUP_STEP_FIELDS      # assert this
```

Recommending an action is never allowed to require weaker evidence than asking
a question. Assert it in `common.py` next to `THRESHOLDS` with that reasoning
in a comment. If someone later tunes one of them, the assertion either holds or
tells them they have inverted the relationship.

#### B12. Three field views, three purposes. Reading `steps[].output` is expected.

There are now three ways to ask a step what it produces, and using the wrong
one is a bug rather than a style choice:

| Source | Question it answers | Used by |
|---|---|---|
| `steps[].output` | **Structure.** Top-level property keys, container shape, what nests inside what. | anything structural |
| `fields[]` | **Inventory.** One entry per authored field, for reporting and unused-field analysis. | reporting, simplify |
| `common.comparable_fields()` | **Similarity.** Envelope-stripped display identities, the only sanctioned overlap input. | any score |

`fields[]` and `comparable_fields()` both flatten away top-level structure, so
neither can answer a structural question. A concrete case: the emitted
`registry.py extract` command must choose `--output` port names, and
`MODULE_SCHEMA.md`'s port resolution turns on whether the step has a declared
top-level property, a `data` payload, or no properties at all. Rule 4 of that
list is a build error, so guessing an alias for a step with several top-level
properties and no `data` payload produces a module that fails to build.

So `registry_match.py` reading `steps[].output.properties` directly is
**correct and expected**, not a pass circumventing the shared helper. Any pass
answering a structural question should do the same and say why in a comment.

#### B10. Unused-field evidence is incomplete, and the report must say so.

The original brief listed docx template placeholders and email-body field
references as evidence sources for the unused-field detector. Only one of them
is reachable.

- **Email body static text IS available** and must be added to facts: an
  `email` step's `config.email_body` and subject are plain strings in the
  workflow. Scan them for field references and record them so a field
  mentioned only in an email body is not called unused.
- **Docx placeholders are NOT available offline.** A `fill_docx` template is a
  platform document reachable only by download, and these passes are forbidden
  from network access. This is a permanent limitation, not a gap to close.

Consequence, and it is not optional: a field whose only possible consumer is a
docx template resolves to **"cannot determine"**, never to "unused". The
report must state the undecidable count alongside the unused count. On the ads
workflow that is 207 of 517 fields, which is too large to leave implied. An
inventory that silently presents 207 unknowns as a verdict is worse than one
that admits the boundary.

#### B11. `html_producers[].interpolated_paths` needs a step qualifier.

Entries currently carry a path with no owning step, so a path cannot be
attributed to its producer and consumers match defensively by exact-or-suffix.
Add the producing step name to each entry. The field is empty on all five
corpus workflows, so this is untested against real data either way.

#### B8. Upgrade severity has three bands.

Accepted as implemented, and it is more careful than the original brief:

- Affected downstream consumers → **warning**, naming them.
- A drop with no consumers → **warning** that says to confirm they are unused.
- Nothing affected → **note**.

The original brief covered only renames; a dropped step with live consumers is
the more dangerous case and deserves the same treatment.

### A6. RESOLVED — nested arrays and objects stay navigable. Only primitives wrap.

The open question was whether an array declared inside an object-typed
extraction schema is cell-wrapped as one leaf (`data.vehicles.value`) or stays
navigable (`data.vehicles.0.vin.value`).

**It stays navigable.** Cell wrapping is a leaf-by-leaf structural walk:
arrays stay arrays with their element schema transformed in turn and a `.0`
added to the element's glom path, objects stay objects with each property
transformed, and only primitives become cells.

The rule now lives where this sub-skill is supposed to read it:
`../../reference/input_mappings.md`, section "Only primitive leaves are
wrapped — nested arrays and objects stay navigable". Cite that, not this
section.

Consequence, and it is the opposite of the interim fallback: `data.vehicles.value`
is **wrong**, not merely unverified. An array node has no `value` key, so that
path resolves to nothing at runtime and every downstream step then runs green
on empty data. That is the same silent-failure class as a classify-class typo,
so it earns the same severity.

Required changes:

- `common.py` / `wf_facts.py`: stop wrapping non-primitive nodes in a cell and
  stop marking them `open`. An array node resolves as `array`, its `items` as
  the row object, and a `.value` segment against either fails with the real
  key list.
- `flow_check.py`: a `.value` applied to an array or object node is a
  **blocker**, with the corrected path in the fix line
  (`data.vehicles.0.vin.value` when the intent was a single column,
  `data.vehicles` when the intent was the whole list).
- No pass may treat this shape as unverifiable any more.
