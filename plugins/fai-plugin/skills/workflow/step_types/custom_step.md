# custom_step

### What this step does

A `custom_step` runs **inline Python code in an isolated E2B microVM sandbox** — the **recommended step type for all new custom code** (transformations, aggregations, formatting, API calls). It uses the same authoring contract as `function` (`config.code` defining `run(**kwargs)`, inputs resolved from `input_mappings`, return a dict matching `config.output_schema`) but the code does NOT run on the platform worker: it has an empty `os.environ`, no database handle, no backend imports, and reaches platform capabilities **only** through the `fai_sandbox` SDK.

The in-process `function` type is LEGACY, EDIT-ONLY: keep existing ones working, but never author a new one and never convert another step into one. If something genuinely cannot be done in a `custom_step`, that is a missing platform capability — tell the user you can't do it yet rather than reaching for `function`.

### Required inputs

Same as `function`: no fixed required inputs; required inputs are derived per-step from `config.input_schema`. Every property in `input_schema` is user-mappable via `input_mappings`.

### Config keys

| key            | type                  | default  | notes |
| -------------- | --------------------- | -------- | ----- |
| `code`         | `str`                 | required | Inline Python defining `run(**kwargs)`. Sync or `async def`. There is **no `func` field** — inline only. |
| `kwargs`       | `dict[str, Any]`      | `{}`     | Static kwargs merged into the call. |
| `input_schema` | JSON Schema           | required | The step errors at save time without it. Use named-type tags (`{"type": "file"}`) for document params. |
| `output_schema`| JSON Schema           | required | The step errors at save time without it. Declare file outputs as `{"type": "file"}`. |
| `packages`     | `list[str]` or `None` | `None`   | Third-party packages the code imports. **Each must be in the sandbox allowlist** — anything else is rejected at save time (not a runtime ImportError): `pandas`, `numpy`, `python-dateutil`, `pytz`, `dateparser`, `openpyxl`, `python-docx`, `pypdf`, `pypdfium2`, `lxml`, `beautifulsoup4`, `Pillow`, `rapidfuzz`, `glom`, `httpx`, `pydantic`. Packages are pre-installed in the sandbox image — declaring them installs nothing at run time. The stdlib is always available and needs no declaration. |

### Differences from `function` (what your code can and cannot do)

- **No injected `execution_log_id` / backend context.** Read the run's execution log as a read-only snapshot via `from fai_sandbox import context; context.execution_log()` (its `id` key is the execution id for deep links).
- **Files use the file contract, not a storage API.** A param declared `{"type": "file"}` in `input_schema` arrives as a `pathlib.Path` (arrays as `list[Path]`) — the platform stages the document's bytes onto the sandbox FS before your code runs. To output a document: declare `{"type": "file"}` in `output_schema`, write the file into the injected `work_dir` kwarg (accept `work_dir=None` or `**kwargs`), and return the `Path` — the platform persists it and downstream steps receive the standard `{user_document_id, filename}` reference. Your code never sees blob URLs or storage credentials.
- **Excel filling** goes through `from fai_sandbox import excel` + `excel.fill(template=<in-VM path>, operations=[...], output_filename=...)` — the platform runs fill + formula recalc + upload and returns `{"user_document_id", "filename"}`. Never save a workbook with `openpyxl` and return it expecting computed formulas without recalc — `excel.fill` is the path that guarantees recalc'd values downstream. `template` must be a path inside the VM (a staged file input `Path`, or a workbook your code wrote under `work_dir`). Operations use the SDK dataclasses (`excel.SheetFillOperation`, `excel.FillType.CELL/ROW/COLUMN`, `excel.Cell(value=..., highlight=True)`, `excel.CellFillData`, `excel.RowFillData`, `excel.ColumnFillData`); plain dicts of the same shape also work.
- **Secrets** via `from fai_sandbox import secrets` → `secrets.get("key")` / `secrets.get_optional("key")`. The key must be declared in the workflow's `options.control_plane_mappings`; an undeclared key is rejected. Workflow-authored keys are org-slug-prefixed `<org>_<vendor>_<what>` (e.g. `acme_cotality_password`); managed integration credential keys (`riskmeter_client_id`, `sharepoint_tenant_id`, …) are fixed names — never prefix or rename those. Never hardcode credentials in `code`, and never log or return a fetched secret.
- **Runtime document/table access is execution-scoped.** For ids the code only discovers at runtime: `from fai_sandbox import files` → `files.stage(user_document_id)` returns the document as an in-VM `Path`; `from fai_sandbox import documents` → `documents.share_link(id, expiry_hours=24)` mints a read-only time-boxed download URL (max 7 days — put it in the outbound email/webhook it exists for; don't persist it) and `documents.html_to_pdf(html=... | html_path=..., filename=...)` converts your HTML into an in-VM PDF `Path` (persist it by returning it under a declared `{"type": "file"}` output field); `context.table(table_v1_log_id)` reads a table output of THIS execution (2000 rows/call, page with `cursor`). All of them reject anything not attached to the current execution — they are NOT a storage API. Statically-known documents/tables should still be wired through file-typed params / `input_mappings`.
- **Logging**: `from fai_sandbox import log` → `log.info("msg", key=value)` lands in the platform's observability under this step's execution span — capped per run, secret values redacted host-side. `print()` and `logging` output are captured as bounded tails too, but prefer `fai_sandbox.log` for anything you'd want to query later. `context.traceparent()` exposes the step span's W3C traceparent for correlating outbound HTTP calls.
- **No LLM capability** — put LLM work in a first-class step (`agentic_extraction`, `agentic_guideline_check`, `multi_column_qa`, …), never in step code.
- **No arbitrary platform reach** — no backend imports (they don't exist in the sandbox), no DB writes, no direct blob storage.

### Side effects — return data, never perform them in code

The step's job is to compute and **return** a dict; persistence is declared via the step's `output_type` and executed by the platform:

- Set the execution title → return `{"title": ...}` + `output_type: "set_title"`.
- Update execution-log fields (e.g. `column_data`) → return them + `output_type: "update_workflow_log"` (atomic host-side `$set`); to merge rather than replace, read the current value from `context.execution_log()` and return the merged dict.
- Produce a document → file contract (above). Excel → `excel.fill`.
- Table for the canvas → return `{"data": [...], "schema": {...}}` + `output_type: "table"` and `output_config.display_type: "grid"`.
- An external system with a first-class integration step (`nhtsa`, `cotality_valuation`, `google_maps`, `web_search`, the connectors — see `integrations.md`) → use that step type instead of hand-rolling HTTP calls in code.

### Output schema

Identical conventions to `function`: return a dict; table-shaped results are `{"data": [...], "schema": {...}}`; document results are declared `{"type": "file"}` (never a generic object with `user_document_id`). The return value crosses the sandbox boundary as JSON — return JSON-serializable values (plus `pathlib.Path` only in declared file-output fields).

### Complete example

```json
{
  "name": "Post to External API",
  "type": "custom_step",
  "input_mappings": [
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Extract Account Data", "output_attribute": null }
        ],
        "resolution_operator": null,
        "resolution_config": null
      },
      "input_parameter_name": "account_data"
    }
  ],
  "dependencies": [],
  "config": {
    "code": "async def run(\n    account_data=None,\n    **kwargs,\n) -> dict:\n    import httpx\n    import json\n    # ... your code here ...\n    return {\"status\": \"success\"}\n",
    "packages": ["httpx"],
    "kwargs": {},
    "input_schema": {
      "type": "object",
      "properties": {
        "account_data": { "type": "object" }
      }
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "status": { "type": "string" }
      }
    }
  },
  "display_step": true,
  "start_title": "Posting to API",
  "end_title": "Posted to API",
  "output_type": null,
  "output_config": null,
  "output_schema": {
    "type": "object",
    "properties": {
      "status": { "type": "string" }
    }
  },
  "enable_rerun": true,
  "incremental_config": { "behavior": "rerun" },
  "parent_conditions": []
}
```

Excel template fill (the template arrives as a staged `{"type": "file"}` input `Path`):

```python
def run(template=None, extracted_data=None, work_dir=None, **kwargs):
    from fai_sandbox import excel

    operations = [
        excel.SheetFillOperation(
            sheet_name="Sheet1",
            fill_type=excel.FillType.CELL,
            cell_data=[
                excel.CellFillData(coordinate="A1", cell=excel.Cell(value="Hello")),
                excel.CellFillData(coordinate="B2", cell=excel.Cell(value=42, highlight=True)),
            ],
        ),
    ]
    ref = excel.fill(template=template, operations=operations, output_filename="filled_output.xlsx")
    return {"generated_document_id": ref["user_document_id"]}
```

### Common gotchas

- `config.input_schema` AND `config.output_schema` are both **required** — the validator errors on a `custom_step` missing either.
- Declaring a package outside the allowlist is a **save-time error**, not a runtime ImportError. Importing an allowlisted package without declaring it in `packages` may work today but declare it anyway — the declaration is the contract.
- Give every explicit parameter a `=None` default and accept `**kwargs`; name the entry function `run`. All imports go INSIDE the function body.
- Avoid bare `print(...)` of JSON-shaped lines (a lone number, a `json.dumps` string); step stdout shares a protocol channel and such lines can be misparsed. Prefer `logging` / `fai_sandbox.log`.
- The `rich` library is NOT in the sandbox — use `logging`/`print`, not `from rich import print`.
- To modify a template before filling: `shutil.copy` the staged template `Path` into `work_dir`, edit the copy with `openpyxl`, then pass the copy to `excel.fill`.

### See also

- `function` — the legacy in-process sibling; shared code conventions (signature, schemas, data-unwrapping helpers) are documented there.
- `integrations.md` — first-class connector steps that replace hand-rolled HTTP for supported vendors.
- `../reference/output_types.md` — output_type decision tree (custom_step is author-controlled like `function`).
