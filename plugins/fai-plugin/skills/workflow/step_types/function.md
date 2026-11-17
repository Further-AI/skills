# function

> **`function` is a LEGACY, EDIT-ONLY step type. NEVER author a new one, and NEVER convert another step into one — for ANY capability the user asks for.** This doc exists so you can recognize and edit `function` steps that already live in older workflows. It is **not** an escape hatch and **not** a fallback.
>
> All new custom code goes in a `custom_step` (see custom_step.md) — same `run(**kwargs)` contract, isolated sandbox. If something genuinely cannot be done in a `custom_step`, that is a missing platform capability, not a reason to reach for `function`: tell the user you can't do it yet. (There is no capability — DB/execution-log writes, backend imports, non-allowlisted packages — that justifies a new `function`; those are platform gaps, not authoring choices.)

### What this step does

A `function` step runs **inline Python code in-process on the platform worker** against the values resolved from its `input_mappings`. `config.code` is a Python string defining a callable (commonly named `run`). It can be sync or `async def`. Inputs arrive as keyword arguments matching `input_parameter_name` from each mapping; extra runtime values (`execution_log_id`, `owner_uid`, `owner_oid`, `run_id`) arrive via `**kwargs`.

Function steps only do **data transformation** — never orchestrate other blocks. The validator actively rejects function steps that import known block helpers (`generate_submission_summary_from_data_points`, `extract_from_multiple_sources`, `guideline_check_qa`, `classify_documents`, etc.) and names the dedicated step type to use instead.

### Required inputs

No fixed required inputs — they are derived per-step from `config.input_schema`. A property is required iff it carries `"required": true` or appears in the schema's top-level `"required": [...]` array. Static extras can be passed via `config.kwargs`.

Cross-field rule: if the inline code's entry function takes explicit named parameters (anything beyond `**kwargs`), `config.input_schema` is **required**. If it returns a value, an output schema must be resolvable (config.output_schema → step.output_schema).

### Config keys

| key            | type             | default  | notes |
| -------------- | ---------------- | -------- | ----- |
| `code`         | `str`            | required | Inline Python source. Must define a callable; sync or `async def`. |
| `func`         | `str \| null`    | `null`   | Registered-function name. **Always `null`** — inline `code` only, so the logic is visible in the builder UI. Mutex with `code`. |
| `kwargs`       | `dict[str, Any]` | `{}`     | Extra static kwargs merged into the call. |
| `input_schema` | JSON Schema or `None` | `None` | Required when inline `code` defines explicit parameters. Drives input-mapping validation and the UI. Use named-type tags (`{"type": "file"}`, `"email_headers"`, `"docx_image"`) for platform shapes — see gotchas. |
| `output_schema`| JSON Schema or `None` | `None` | Required when inline `code` returns a value. Must also mirror onto the step-level `output_schema` for downstream wiring. |

### Code conventions

```python
async def run(
    param1=None,
    param2=None,
    **kwargs,
) -> dict:
    ...
```

- All params with `=None` defaults.
- **`**kwargs` is REQUIRED** on the entry function — the save-time validator rejects function steps whose entry lacks it (system-param injection raises `TypeError` at runtime without it).
- **Must return a dict** matching `output_schema`.
- **All imports INSIDE the function body** — the code runs in isolation.
- Available packages (in-process): `httpx`, `jwt` (PyJWT), `cryptography`, `dotenv`, `rich`, stdlib (`json`, `uuid`, `time`, `os`, `logging`).

#### Code string encoding

Inline code is stored as a JSON string: newlines → `\n`, quotes → `\"`, backslashes → `\\`. A literal `\n` in Python source (e.g. `key.replace("\\n", "\n")`) is `\\\\n` in the JSON.

#### Unwrapping extraction cells

Extraction data arrives cell-wrapped: `{"value": X, "citations": [...]}`. Standard helpers to paste into function code:

```python
def extract_value(val):
    if isinstance(val, dict) and "value" in val:
        return val["value"]
    if isinstance(val, list):
        return [v["value"] if isinstance(v, dict) and "value" in v else v for v in val]
    return val

def get_field(data_dict, field_name, default=None):
    if not data_dict or not isinstance(data_dict, dict):
        return default
    raw = data_dict.get(field_name)
    if raw is None: return default
    val = extract_value(raw)
    if val is None or str(val).strip() == "": return default
    return val

def unwrap_step_data(step_output):
    if not isinstance(step_output, dict): return {}
    if "data" in step_output:
        inner = step_output["data"]
        if isinstance(inner, dict): return inner
        if isinstance(inner, list) and inner and isinstance(inner[0], dict): return inner[0]
    return step_output

def get_array_data(step_output):
    if step_output is None: return []
    if isinstance(step_output, dict):
        d = step_output.get("data", [])
        return d if isinstance(d, list) else []
    return step_output if isinstance(step_output, list) else []
```

- **Single-object extractions** → `unwrap_step_data()` then `get_field()`.
- **Array extractions (claims, locations)** → `get_array_data()` then iterate.
- Financial strings (`"$1,250,000"`) need parsing: strip `$` and `,`, then `float(...)`.

### Output schema

A function step **must return a dict**. Conventions:

- **Simple result**: `{"<key>": <value>}` — set `step.output_type = "simple"` (or leave null for pure data hand-offs).
- **Table-shaped result**: `{"data": [<rows>], "schema": {<column-meta>}}` — set `step.output_type = "table"` AND `output_config: {"view_output_button_text": "View Output", "display_type": "grid", "columns": null}`. Without `display_type: "grid"` the table won't render properly.
- **HTML result**: `{"email_body": html}` with `output_type: "html"` and `output_config.content_key: "email_body"`.
- **Document-shaped result**: return a `{"user_document_id": ..., "filename": ...}` payload and declare the output_schema field as `{"type": "file"}` — not a generic object — so downstream `file`-typed inputs accept it.

Do **not** set `"error"` on the success path — the executor uses truthy `error` to mark failure; validators flag `return {"error": "", ...}` as misleading. Only set the key on real failure branches.

If the function returns mixed shapes across branches (a list on one path, a dict on another), validation rejects it: all return statements must produce the same type.

### Input wiring (input_mappings vs dependencies)

- **NEW steps**: N/A — don't author new `function` steps.
- **EXISTING steps**: preserve whatever pattern is already there — don't churn a stable workflow.
- Migration `dependencies` → `input_mappings` on explicit request; the reverse is forbidden.

Each `input_mappings` entry's `input_parameter_name` must match a property in `config.input_schema`. Legacy `dependencies` wiring dumps the dependency output into `**kwargs` without naming the kwarg. Hybrid steps (both lists populated) resolve via `input_mappings` — the runtime ignores `dependencies` whenever any `input_mappings` exist.

### Common patterns (existing instances)

#### Pattern 1 — Inline transformation with explicit schema (table output)

A function that consumes an upstream extraction's table, reshapes rows, and returns a new table:

```json
{
  "name": "Aggregate Claims",
  "type": "function",
  "input_mappings": [
    { "input_parameter_name": "claims",
      "input_type": "dependency",
      "value": { "dependency_step_outputs": [{ "step_name": "Extract Loss Runs", "output_attribute": "data" }],
                 "resolution_operator": null, "resolution_config": null } }
  ],
  "dependencies": [],
  "config": {
    "code": "async def run(claims=None, **kwargs):\n    def unwrap(v):\n        if isinstance(v, dict) and 'value' in v: return v['value']\n        return v\n    total = 0\n    count = 0\n    for row in claims or []:\n        raw = unwrap(row.get('total_incurred'))\n        try:\n            total += float(str(raw).replace('$','').replace(',','').strip())\n        except Exception:\n            pass\n        count += 1\n    return { 'total_incurred': f'${total:,.0f}', 'claim_count': count }",
    "func": null,
    "kwargs": {},
    "input_schema": {
      "type": "object",
      "properties": { "claims": { "type": "array" } },
      "required": ["claims"]
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "total_incurred": { "type": "string", "title": "Total Incurred" },
        "claim_count":    { "type": "integer", "title": "Claim Count" }
      }
    }
  },
  "output_type": null,
  "output_config": null,
  "output_schema": {
    "type": "object",
    "properties": {
      "total_incurred": { "type": "string", "title": "Total Incurred" },
      "claim_count":    { "type": "integer", "title": "Claim Count" }
    }
  }
}
```

#### Pattern 2 — Tiny connector function (passthrough / shape adapter)

Legacy glue: take a dependency's output, repackage it under a different key, and emit. No schemas needed because the entry function takes only `**kwargs`.

```json
{
  "name": "prior_claims_connector",
  "type": "function",
  "input_mappings": [],
  "dependencies": [{"step_name": "extract_prior_claims_data", "field_selector": null}],
  "config": {
    "code": "async def prior_claims_connector(**kwargs):\n    if \"table_v1_log_id\" in kwargs:\n        return {\"prior_claims\": {\"table_v1_log_id\": kwargs[\"table_v1_log_id\"]}}\n    return {\"prior_claims\": kwargs}",
    "kwargs": {},
    "input_schema": null,
    "output_schema": null
  },
  "output_type": "simple"
}
```

### Common validation errors and fixes

- **`Function step must have either 'func' (predefined function name) or 'code' (inline code)`** — `config.code` was not set. Fix: set the inline source.
- **`Step '<name>': code has a syntax error — <msg> (line <n>)`** — the validator AST-parses the code; fix the Python syntax at the reported line. Common causes: stray `await` outside `async def`, dangling copy-paste indents.
- **`Step '<name>': imports '<x>' — use the '<step_type>' step type instead.`** — forbidden import. Replace the function step with the named dedicated step type: `generate_submission_summary_from_data_points` → `submission_summary_generator`; `extract_from_multiple_sources` → same-named step; `guideline_check_qa` → `agentic_guideline_check`; `extract_rows_from_multiple_documents` → `extract_rows_from_multiple_sources`; `generate_qa_table_from_agent` / `classify_documents` → same-named steps; `reply_email_using_zapier` → `email` (with `reply_to_original`); `summary_agent` / `create_summary_agent` → `submission_summary_generator`.
- **`function '<fn>' takes explicit parameters — must define input_schema in config`** — add `config.input_schema` describing each declared parameter, or change the signature to `def run(**kwargs):`.
- **`function '<fn>' returns a value — must define output_schema in config`** — set `config.output_schema`.
- **`function '<fn>' has incompatible return types [...] across different paths`** — normalize all return paths to the same shape (wrap the list: `return {"rows": rows}`).
- **`remove 'error' from the return on success`** — delete the `error` key from success returns.
- **`Missing required input mappings: [...]`** — add an `input_mappings` entry per missing parameter, or relax the schema if the value is genuinely optional.
- **Entry function missing `**kwargs`** — rejected at save; add it.

### Common gotchas

- **Use named-type tags in schemas, not generic `object`.** A document return declared `{"type": "object", "properties": {"user_document_id": ...}}` is rejected against a downstream `file`-typed input; declare `{"type": "file"}`. Same for `email_headers`, `docx_image`, `cell`, `citation`, `sov_field`. Lists: `{"type": "array", "items": {"type": "file"}}`.
- **`output_type` is set on the step, not the config.** `"simple"` for plain dict returns, `"table"` for `{data, schema}` payloads. Wrong values break downstream rendering.
- **Inline imports inside the function body are required** — top-level imports don't work; the code runs in isolation.
- **`**kwargs` is the safe default for tiny adapters.** If the function only forwards a dependency's output under a new key, skip the schemas and let `kwargs` carry the upstream values.
- **Don't reimplement a block as a function.** "Summarize these documents" → `submission_summary_generator`; "guideline-check these clauses" → `agentic_guideline_check`. The validator rejects the function-step version on save.
- **You cannot test-execute step code locally against real platform inputs.** Run the bundled validator (`python3 ${CLAUDE_PLUGIN_ROOT}/skills/workflow/scripts/validate_workflow.py <workflow.json>`) — it performs the AST syntax check, forbidden-import check, and cross-field rules — then verify behavior with a platform test run.
- **Incremental behaviors:** `rerun`, `no_op`, `append`, and `preserve_edits` are all accepted for this step type.

### See also

- `custom_step` — the sandboxed replacement for ALL new custom code.
- `submission_summary_generator` / `agentic_guideline_check` / `extract_from_multiple_sources` — first-class steps that forbidden-import checks will point you to.
- `decision` — branching logic with operator-based conditions; cleaner than a function step returning a branch key.
