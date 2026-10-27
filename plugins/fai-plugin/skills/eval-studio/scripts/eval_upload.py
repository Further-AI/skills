#!/usr/bin/env python3
"""Bulk sample upload for Eval Studio test batches.

Converts ad-hoc sources (CSV / Excel / JSON / folder of per-sample subfolders)
into a normalized manifest, validates it against the live test batch, and
uploads samples + documents + answers through a scoped upload session.

Flow: normalize -> dry-run (mandatory) -> upload. Uploads are strictly
add-only: existing samples and answers are never modified or deleted.
See SKILL.md ("Upload samples in bulk") and references/bulk_upload.md.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import get_close_matches
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_client import FaiClient, FaiError  # noqa: E402
from fai_accounts import AccountError, default_env, resolve_org_id  # noqa: E402

BASE = "/api/v1/evaluation"
SESSIONS = f"{BASE}/upload-sessions"
OBJECT_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

SESSION_TTL_MINUTES = 480  # backend upload-session TTL (~8h)
UPLOAD_URL_BATCH = 500  # backend cap per upload-urls request
GT_UPLOAD_RETRIES = 3
GT_FILE_NAMES = ("gt.json", "ground_truth.json", "answers.json")
IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
IGNORED_DIRS = {"__MACOSX", "__pycache__"}

# Pre-bootstrap function-step template heuristic: template with a handful of
# generic wrapper keys while the manifest GT has many more fields means the
# backend would silently project the upload down to the wrapper keys.
PRE_BOOTSTRAP_TEMPLATE_KEY_CEILING = 5
PRE_BOOTSTRAP_EXTRA_FLOOR = 10
PRE_BOOTSTRAP_EXTRA_RATIO = 3

TABULAR_SUFFIXES = {".csv", ".xlsx", ".xls", ".xlsm"}
EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}
SUBMISSION_COLUMN_CANDIDATES = ["submission_name", "sample_name", "sample", "name", "id"]
FILES_COLUMN_CANDIDATES = ["files", "file", "documents", "document",
                           "document_path", "document_paths", "filename"]


def fail(message: str, code: int = 1):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


def require_object_id(value: str, label: str) -> str:
    if not OBJECT_ID_RE.fullmatch(value or ""):
        fail(f"{label} {value!r} is not a 24-char hex ObjectId "
             f"(dataset IDs come from the /eval/<id> URL or list-datasets)")
    return value


def read_json_file(path: Path, label: str):
    if not path.exists():
        fail(f"{label}: file not found: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        fail(f"{label} is not valid JSON: {e}")


def write_json_file(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(data) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def print_table(headers, rows) -> None:
    if not rows:
        print("(none)")
        return
    cells = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    for row in cells:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


# --------------------------------------------------------------------- client

def build_client(args) -> FaiClient:
    env = args.env or default_env()
    org_id = args.org_id
    if org_id and not UUID_RE.fullmatch(org_id):
        fail(f"--org-id {org_id!r} is not a UUID")
    if not org_id and args.account:
        org_id = resolve_org_id(args.account, env)
    if not org_id:
        fail("no org context: pass --account <slug> (from ~/.fai/accounts.json) or --org-id <uuid>")
    return FaiClient(env=env, org_id=org_id, skill_name="eval-studio")


# ------------------------------------------------------------------ templates

def template_steps_of(template_data: dict) -> dict:
    """Accept the gt-template content payload or a bare {step: shape} dict."""
    steps = template_data.get("steps") if isinstance(template_data, dict) else None
    if isinstance(steps, dict):
        return steps
    if isinstance(template_data, dict):
        return template_data
    fail("answers template must be a JSON object")


def field_names(template_shape) -> set:
    if isinstance(template_shape, dict):
        return set(template_shape.keys())
    if isinstance(template_shape, list) and template_shape and isinstance(template_shape[0], dict):
        return set(template_shape[0].keys())
    return set()


def is_grid_template(template_shape) -> bool:
    return isinstance(template_shape, list)


def load_template(args) -> dict:
    """Template from --template <file>, or live from --dataset via the API."""
    if getattr(args, "template", None):
        return read_json_file(Path(args.template).expanduser(), "--template")
    if getattr(args, "dataset", None):
        require_object_id(args.dataset, "--dataset")
        client = build_client(args)
        return client.get(f"{BASE}/datasets/{args.dataset}/gt-template/content")
    fail("this input needs the answers template: pass --dataset <dataset-id> "
         "(fetched live) or --template <file> (saved gt-template content)")


# ------------------------------------------------------------------ normalize

def coerce_cell(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bool) or isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    text = str(value).strip()
    if text == "":
        return None
    if text[:1] in ("{", "["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


def split_paths(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [str(v).strip() for v in parsed if str(v).strip()]
    return [p.strip() for p in re.split(r"[|;]", text) if p.strip()]


def is_source_artifact(path: Path) -> bool:
    if path.name.startswith(".") or path.name in IGNORED_NAMES:
        return True
    return bool(set(path.parts) & IGNORED_DIRS)


def read_tabular(path: Path) -> list:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    try:
        import pandas as pd  # noqa: F401
    except ImportError:
        fail(
            f"Excel input {path.name} needs pandas and openpyxl: "
            "python3 -m pip install pandas openpyxl"
        )
    frame = pd.read_excel(path, dtype=object)
    frame = frame.where(frame.notna(), None)
    return frame.to_dict(orient="records")


def find_column(columns, explicit, candidates):
    if explicit:
        if explicit not in columns:
            fail(f"column {explicit!r} not found; available: {', '.join(columns)}")
        return explicit
    lower = {c.lower().strip(): c for c in columns}
    for candidate in candidates:
        if candidate in lower:
            return lower[candidate]
    return None


def infer_gt_columns(columns, template_steps, mapping) -> dict:
    """column -> (step, field). Explicit mapping wins; else 'Step.field' columns,
    else field names unique to exactly one template step."""
    explicit = mapping.get("gt_columns")
    if isinstance(explicit, dict):
        result = {}
        for col, target in explicit.items():
            if col not in columns:
                fail(f"mapping gt_columns column {col!r} not found in the input")
            if isinstance(target, str) and "." in target:
                step, field = target.split(".", 1)
            elif isinstance(target, dict) and target.get("step") and target.get("field"):
                step, field = target["step"], target["field"]
            else:
                fail(f"mapping gt_columns[{col!r}] must be 'Step.field' or "
                     '{"step": ..., "field": ...}')
            result[col] = (str(step), str(field))
        return result
    field_to_steps = {}
    for step, shape in template_steps.items():
        for name in field_names(shape):
            field_to_steps.setdefault(name, []).append(step)
    result = {}
    for col in columns:
        if "." in col:
            maybe_step, maybe_field = col.split(".", 1)
            if maybe_step in template_steps:
                result[col] = (maybe_step, maybe_field)
                continue
        if col in field_to_steps and len(field_to_steps[col]) == 1:
            result[col] = (field_to_steps[col][0], col)
    return result


def new_submission(name: str) -> dict:
    return {"submission_name": name, "files": [], "metadata": {}, "gt": {"steps": {}}}


def resolve_doc_path(value: str, documents_dir) -> str:
    path = Path(value).expanduser()
    if documents_dir and not path.is_absolute():
        path = Path(documents_dir).expanduser() / path
    return str(path.resolve())


def normalize_tabular(path: Path, args, report: dict) -> list:
    template_steps = template_steps_of(load_template(args))
    rows = read_tabular(path)
    if not rows:
        fail(f"{path.name} has no data rows")
    mapping = read_json_file(Path(args.mapping).expanduser(), "--mapping") if args.mapping else {}
    columns = list(rows[0].keys())
    submission_col = find_column(columns, args.submission_column or mapping.get("submission_column"),
                                 SUBMISSION_COLUMN_CANDIDATES)
    files_col = find_column(columns, args.files_column or mapping.get("files_column"),
                            FILES_COLUMN_CANDIDATES)
    if not submission_col:
        fail("could not infer the sample-name column; pass --submission-column "
             "or mapping.submission_column")
    if not files_col:
        fail("could not infer the files column; pass --files-column or mapping.files_column")
    ignored = set(mapping.get("ignore_columns") or [])
    gt_columns = infer_gt_columns(columns, template_steps, mapping)
    control = {submission_col, files_col} | ignored

    grouped: dict = {}
    for row_index, row in enumerate(rows):
        name = str(row.get(submission_col) or "").strip()
        if not name:
            fail(f"row {row_index + 1} has no sample name in column {submission_col!r}")
        sub = grouped.setdefault(name, new_submission(name))
        for value in split_paths(row.get(files_col)):
            resolved = resolve_doc_path(value, args.documents_dir)
            if resolved not in sub["files"]:
                sub["files"].append(resolved)
        for col, value in row.items():
            if col in control:
                continue
            if col not in gt_columns:
                if value not in (None, ""):
                    sub["metadata"].setdefault("unmapped_columns", {})[col] = coerce_cell(value)
                    report["unmapped_columns"].add(col)
                continue
            step, field = gt_columns[col]
            if is_grid_template(template_steps.get(step)):
                grid = sub["gt"]["steps"].setdefault(step, [])
                if not grid or grid[-1].get("__row") != row_index:
                    grid.append({"__row": row_index})
                grid[-1][field] = coerce_cell(value)
            else:
                coerced = coerce_cell(value)
                if coerced in (None, ""):
                    continue
                kv = sub["gt"]["steps"].setdefault(step, {})
                if field in kv and kv[field] != coerced:
                    fail(f"conflicting repeated value for sample {name!r} step {step!r} "
                         f"field {field!r}: {kv[field]!r} != {coerced!r}")
                kv[field] = coerced
    for sub in grouped.values():
        for payload in sub["gt"]["steps"].values():
            if isinstance(payload, list):
                for row in payload:
                    row.pop("__row", None)
    report["gt_columns_mapped"] = {c: f"{s}.{f}" for c, (s, f) in gt_columns.items()}
    return list(grouped.values())


def normalize_json_input(path: Path, args) -> list:
    data = read_json_file(path, "--input")
    if isinstance(data, dict) and isinstance(data.get("submissions"), list):
        submissions = data["submissions"]
    elif isinstance(data, list):
        submissions = data
    elif isinstance(data, dict) and "steps" in data:
        sub = new_submission(path.stem)
        sub["gt"] = {"steps": data["steps"] if isinstance(data["steps"], dict) else {}}
        submissions = [sub]
    else:
        fail("JSON input must be a manifest with submissions[], an array of "
             "submission objects, or a single {\"steps\": ...} answers object")
    for sub in submissions:
        if isinstance(sub, dict):
            sub["files"] = [resolve_doc_path(v, args.documents_dir)
                            for v in (sub.get("files") or [])]
    return submissions


def normalize_folder(input_dir: Path, report: dict) -> list:
    """One subfolder per sample: documents are the folder's files (or its
    documents/ subdir); answers come from an optional gt.json in the folder."""
    submissions = []
    for child in sorted(input_dir.iterdir()):
        if not child.is_dir() or is_source_artifact(child):
            continue
        sub = new_submission(child.name)
        gt_path = next((child / n for n in GT_FILE_NAMES if (child / n).exists()), None)
        if gt_path:
            gt_data = read_json_file(gt_path, str(gt_path))
            steps = gt_data.get("steps") if isinstance(gt_data, dict) else None
            if not isinstance(steps, dict):
                steps = gt_data if isinstance(gt_data, dict) else {}
            sub["gt"] = {"steps": steps}
        docs_root = child / "documents" if (child / "documents").is_dir() else child
        for path in sorted(docs_root.rglob("*")):
            if path.is_file() and not is_source_artifact(path) and path != gt_path:
                sub["files"].append(str(path.resolve()))
        if not sub["files"] and not sub["gt"]["steps"]:
            report["skipped_folders"].append(child.name)
            continue
        submissions.append(sub)
    if not submissions:
        fail(f"no sample subfolders found under {input_dir} "
             "(expected one subfolder per sample; see references/bulk_upload.md)")
    return submissions


def cmd_normalize(args) -> None:
    input_path = Path(args.input).expanduser()
    if not input_path.exists():
        fail(f"--input not found: {input_path}")
    out_dir = Path(args.out_dir).expanduser()
    report = {"input": str(input_path), "unmapped_columns": set(), "skipped_folders": []}

    if input_path.is_dir():
        report["format"] = "folder"
        submissions = normalize_folder(input_path, report)
    elif input_path.suffix.lower() == ".json":
        report["format"] = "json"
        submissions = normalize_json_input(input_path, args)
    elif input_path.suffix.lower() in TABULAR_SUFFIXES:
        report["format"] = "tabular"
        submissions = normalize_tabular(input_path, args, report)
    else:
        fail("--input must be a directory, .json, .csv, .xlsx, .xlsm, or .xls")

    manifest = {
        "version": "1",
        "source": {"path": str(input_path),
                   "sha256": sha256_file(input_path) if input_path.is_file() else None},
        "submissions": submissions,
    }
    report["unmapped_columns"] = sorted(report["unmapped_columns"])
    report["submission_count"] = len(submissions)
    report["file_count"] = sum(len(s.get("files") or []) for s in submissions)
    report["gt_step_upload_count"] = sum(
        len((s.get("gt") or {}).get("steps") or {}) for s in submissions)

    manifest_path = out_dir / "manifest.json"
    write_json_file(manifest_path, manifest)
    write_json_file(out_dir / "normalize_report.json", report)
    print(f"normalized {report['format']} input: {report['submission_count']} sample(s), "
          f"{report['file_count']} document(s), {report['gt_step_upload_count']} answer step upload(s)")
    if report["unmapped_columns"]:
        print(f"  unmapped columns (kept in metadata, NOT uploaded as answers): "
              f"{', '.join(report['unmapped_columns'])}")
    if report["skipped_folders"]:
        print(f"  skipped empty folders: {', '.join(report['skipped_folders'])}")
    print(f"  manifest: {manifest_path}")
    print(f"next: dry-run <dataset-id> --manifest {manifest_path}")


# ----------------------------------------------------------------- validation

def add_issue(issues, severity, code, message, **kw) -> None:
    issues.append({"severity": severity, "code": code, "message": message,
                   **{k: v for k, v in kw.items() if v is not None}})


def validate_manifest(manifest, dataset, template_data, existing_names, flags) -> list:
    issues = []
    submissions = manifest.get("submissions")
    if not isinstance(submissions, list) or not submissions:
        add_issue(issues, "error", "MANIFEST_EMPTY", "manifest has no submissions[]")
        return issues

    selected = set(dataset.get("selected_steps") or [])
    template_steps = template_steps_of(template_data)
    template_names = set(template_steps.keys())
    if not selected:
        add_issue(issues, "error", "NO_SELECTED_STEPS",
                  "test batch has no selected steps; configure it (update-dataset) before uploading")
    for step in sorted(selected - template_names):
        add_issue(issues, "error", "SELECTED_STEP_NO_TEMPLATE",
                  "selected step has no answers template yet — run one representative "
                  "sample first (or select the underlying extraction steps); "
                  "answers cannot be uploaded for it", step=step)

    seen = set()
    for index, sub in enumerate(submissions):
        if not isinstance(sub, dict):
            add_issue(issues, "error", "SUBMISSION_TYPE", f"submission {index} must be an object")
            continue
        name = str(sub.get("submission_name") or "").strip()
        if not name:
            add_issue(issues, "error", "SUBMISSION_NAME", f"submission {index} has no submission_name")
            name = f"<submission {index}>"
        if name in seen:
            add_issue(issues, "error", "DUPLICATE_NAME", "duplicate submission_name in manifest",
                      submission=name)
        seen.add(name)
        if name in existing_names:
            add_issue(issues, "warning" if flags.get("allow_duplicate_names") else "error",
                      "NAME_COLLISION",
                      "a sample with this name already exists in the test batch "
                      "(upload is add-only and would create a second sample with the "
                      "same name; pass --allow-duplicate-names to proceed anyway)",
                      submission=name)

        files = sub.get("files")
        if not isinstance(files, list) or not files:
            add_issue(issues, "error", "FILES_MISSING",
                      "submission must include at least one document file", submission=name)
        else:
            for value in files:
                path = Path(str(value))
                if not path.is_file():
                    add_issue(issues, "error", "FILE_NOT_FOUND", f"file not found: {path}",
                              submission=name)
                elif path.stat().st_size == 0:
                    add_issue(issues, "warning", "FILE_EMPTY", f"file is empty: {path}",
                              submission=name)

        steps = ((sub.get("gt") or {}).get("steps")
                 if isinstance(sub.get("gt"), dict) else None)
        if not isinstance(steps, dict) or not steps:
            add_issue(issues, "warning" if flags.get("allow_partial_gt") else "error",
                      "GT_MISSING",
                      "submission has no answers (gt.steps); pass --allow-partial-gt "
                      "for a documents-only upload", submission=name)
            steps = {}
        for step in sorted(set(steps) - template_names):
            hint = get_close_matches(step, sorted(template_names), n=3, cutoff=0.4)
            add_issue(issues, "error", "GT_UNKNOWN_STEP",
                      "step is not in the answers template"
                      + (f"; closest template steps: {', '.join(hint)}" if hint else ""),
                      submission=name, step=step)
        for step in sorted((set(steps) & template_names) - selected):
            add_issue(issues, "error", "GT_NON_SELECTED_STEP",
                      "step is not selected on this test batch", submission=name, step=step)
        if steps and not flags.get("allow_partial_gt"):
            for step in sorted((template_names & selected) - set(steps)):
                add_issue(issues, "error", "GT_MISSING_STEP",
                          "no answers for a selected step (pass --allow-partial-gt to allow)",
                          submission=name, step=step)
        for step, payload in steps.items():
            shape = template_steps.get(step)
            if shape is None:
                continue
            validate_step_payload(issues, name, step, payload, shape, flags)

    issues.extend(pre_bootstrap_warnings(submissions, selected, template_steps))
    return issues


def validate_step_payload(issues, name, step, payload, shape, flags) -> None:
    expected = field_names(shape)
    if is_grid_template(shape):
        if not isinstance(payload, list):
            add_issue(issues, "error", "GT_STEP_TYPE",
                      "grid step answers must be a list of row objects",
                      submission=name, step=step)
            return
        rows = payload
    else:
        if not isinstance(payload, dict):
            add_issue(issues, "error", "GT_STEP_TYPE",
                      "key-value step answers must be an object", submission=name, step=step)
            return
        rows = [payload]
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            add_issue(issues, "error", "GT_ROW_TYPE",
                      f"answers row {row_index} must be an object", submission=name, step=step)
            continue
        if not flags.get("allow_missing_fields"):
            for field in sorted(expected - set(row)):
                add_issue(issues, "error", "GT_MISSING_FIELD",
                          "missing answers field (pass --allow-missing-fields to allow)",
                          submission=name, step=step, field=field)
        if not flags.get("allow_extra_fields"):
            for field in sorted(set(row) - expected):
                add_issue(issues, "error", "GT_EXTRA_FIELD",
                          "field is not in the answers template (pass --allow-extra-fields "
                          "to allow; extras are dropped by the backend template)",
                          submission=name, step=step, field=field)


def pre_bootstrap_warnings(submissions, selected, template_steps) -> list:
    issues = []
    for step in sorted(selected):
        shape = template_steps.get(step)
        if shape is None:
            continue
        expected = field_names(shape)
        if len(expected) > PRE_BOOTSTRAP_TEMPLATE_KEY_CEILING:
            continue
        worst = 0
        for sub in submissions:
            if not isinstance(sub, dict):
                continue
            payload = ((sub.get("gt") or {}).get("steps") or {}).get(step)
            row = (payload if isinstance(payload, dict)
                   else payload[0] if isinstance(payload, list) and payload
                   and isinstance(payload[0], dict) else None)
            if row is not None:
                worst = max(worst, len(set(row) - expected))
        floor = max(PRE_BOOTSTRAP_EXTRA_FLOOR,
                    PRE_BOOTSTRAP_EXTRA_RATIO * max(1, len(expected)))
        if worst >= floor:
            add_issue(issues, "warning", "PRE_BOOTSTRAP_TEMPLATE",
                      f"template has only {len(expected)} generic field(s) but manifest "
                      f"answers carry up to {worst} extra fields — likely a pre-bootstrap "
                      "function-step template. The backend would silently drop the extra "
                      "fields. Upload documents only (--allow-partial-gt, no answers for "
                      "this step), run one sample, then upload answers once the template "
                      "reflects the real output shape.", step=step)
    return issues


def file_fingerprints(manifest) -> list:
    files = []
    for sub in manifest.get("submissions") or []:
        if not isinstance(sub, dict):
            continue
        for value in sub.get("files") or []:
            path = Path(str(value))
            ok = path.is_file()
            files.append({
                "submission": sub.get("submission_name"),
                "path": str(path),
                "exists": ok,
                "size": path.stat().st_size if ok else None,
                "sha256": sha256_file(path) if ok else None,
            })
    return sorted(files, key=lambda f: (str(f["submission"]), f["path"]))


def dataset_contract(dataset) -> dict:
    return {
        "id": dataset.get("id"),
        "workflow_id": dataset.get("workflow_id"),
        "selected_steps": dataset.get("selected_steps"),
        "pinned_workflow_version": dataset.get("pinned_workflow_version"),
        "submission_count": dataset.get("submission_count"),
    }


def validation_flags(args) -> dict:
    return {
        "allow_partial_gt": bool(args.allow_partial_gt),
        "allow_missing_fields": bool(args.allow_missing_fields),
        "allow_extra_fields": bool(args.allow_extra_fields),
        "allow_duplicate_names": bool(args.allow_duplicate_names),
    }


def existing_submission_names(client, dataset_id) -> set:
    data = client.get(f"{BASE}/datasets/{dataset_id}/submissions")
    subs = data if isinstance(data, list) else (data or {}).get("submissions") or []
    return {str(s.get("submission_name") or "").strip() for s in subs}


def print_issues(issues) -> None:
    for issue in issues:
        where = " ".join(f"{k}={issue[k]!r}" for k in ("submission", "step", "field")
                         if k in issue)
        print(f"  {issue['severity'].upper()} {issue['code']} {where}: {issue['message']}")


# -------------------------------------------------------------------- dry-run

def cmd_dry_run(args) -> None:
    require_object_id(args.dataset_id, "dataset_id")
    manifest_path = Path(args.manifest).expanduser()
    manifest = read_json_file(manifest_path, "--manifest")
    client = build_client(args)

    try:
        dataset = client.get(f"{BASE}/datasets/{args.dataset_id}")
    except FaiError as e:
        if e.status == 404:
            fail("test batch not found — bulk upload requires an EXISTING test batch; "
                 "create it first (create-dataset in eval_studio.py) and retry")
        raise
    template_data = client.get(f"{BASE}/datasets/{args.dataset_id}/gt-template/content")
    existing = existing_submission_names(client, args.dataset_id)

    flags = validation_flags(args)
    issues = validate_manifest(manifest, dataset, template_data, existing, flags)
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]

    submissions = manifest.get("submissions") or []
    report = {
        "mode": "dry-run",
        "env": client.env,
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": sha256_json(manifest),
        "dataset": dataset_contract(dataset),
        "planned_writes": {
            "submissions_to_add": len(submissions),
            "files_to_upload": sum(len(s.get("files") or []) for s in submissions
                                   if isinstance(s, dict)),
            "ground_truth_uploads": sum(len(((s.get("gt") or {}).get("steps") or {}))
                                        for s in submissions if isinstance(s, dict)),
            "add_only": True,
            "will_run_workflow": False,
        },
        "files": file_fingerprints(manifest),
        "validation_flags": flags,
        "validation": {"error_count": len(errors), "warning_count": len(warnings),
                       "issues": issues},
        "upload_allowed": not errors,
    }
    report_path = Path(args.report).expanduser() if args.report else manifest_path.parent / "dry_run_report.json"
    write_json_file(report_path, report)

    planned = report["planned_writes"]
    print(f"DRY RUN — test batch {dataset.get('name')!r} ({args.dataset_id}), env {client.env}")
    print(f"  would add: {planned['submissions_to_add']} sample(s), "
          f"{planned['files_to_upload']} document(s), "
          f"{planned['ground_truth_uploads']} answer step upload(s)")
    print("  add-only: existing samples and answers are never modified or deleted")
    rows = [[s.get("submission_name"), len(s.get("files") or []),
             len(((s.get("gt") or {}).get("steps") or {})),
             "COLLIDES" if str(s.get("submission_name") or "").strip() in existing else ""]
            for s in submissions if isinstance(s, dict)]
    print_table(["SAMPLE", "DOCS", "ANSWER_STEPS", "NOTE"], rows)
    if issues:
        print(f"validation: {len(errors)} error(s), {len(warnings)} warning(s)")
        print_issues(issues)
    print(f"report: {report_path}")
    if errors:
        print("upload_allowed: false — fix the manifest (or adjust flags) and rerun dry-run")
        sys.exit(2)
    print(f"upload_allowed: true — next: upload {args.dataset_id} --manifest {manifest_path}")


# --------------------------------------------------------------------- upload

def sanitize_gt(steps: dict, template_steps: dict, allow_extra_fields: bool) -> dict:
    clean = {}
    for step, payload in steps.items():
        shape = template_steps.get(step)
        if shape is None:
            continue
        expected = field_names(shape)
        if allow_extra_fields:
            clean[step] = payload
        elif isinstance(payload, dict):
            clean[step] = {k: v for k, v in payload.items() if k in expected}
        elif isinstance(payload, list):
            clean[step] = [{k: v for k, v in row.items() if k in expected}
                           if isinstance(row, dict) else row for row in payload]
    return {"steps": clean}


def put_file_to_sas(sas_url: str, path: Path, timeout: int, retries: int) -> None:
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    last = ""
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(sas_url, data=path.read_bytes(), method="PUT")
        req.add_header("x-ms-blob-type", "BlockBlob")
        req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=timeout):
                return
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code == 403:
                raise FaiError(f"blob PUT for {path.name} got 403 — the upload URL "
                               "expired; the session is stale, start a new upload",
                               status=403)
            if e.code not in (429, 500, 502, 503, 504):
                raise FaiError(f"blob PUT failed for {path.name}: HTTP {e.code}", status=e.code)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = str(e)[:200]
        time.sleep(2 * attempt)
    raise FaiError(f"blob PUT failed for {path.name} after {retries} attempts: {last}")


def post_gt_multipart(client: FaiClient, session: dict, submission_id: str, gt_payload: dict) -> dict:
    """Session-scoped answers upload. Multipart (FaiClient is JSON-only), so it
    posts directly with the session token — the only credential this route uses."""
    boundary = uuid.uuid4().hex
    payload = json.dumps(gt_payload).encode()
    body = (
        (f"--{boundary}\r\n"
         f'Content-Disposition: form-data; name="gt_file"; filename="ground_truth.json"\r\n'
         f"Content-Type: application/json\r\n\r\n").encode()
        + payload + f"\r\n--{boundary}--\r\n".encode()
    )
    url = (f"{client.api_url}{SESSIONS}/{session['session_id']}"
           f"/submissions/{submission_id}/gt")
    last: Exception = FaiError("answers upload not attempted")
    for attempt in range(1, GT_UPLOAD_RETRIES + 1):
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("X-Eval-Upload-Session-Token", session["session_token"])
        req.add_header("X-FAI-Skill-Name", client.skill_name)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            last = FaiError(f"answers upload HTTP {e.code}: {detail}", status=e.code)
            if e.code < 500:
                raise last
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = FaiError(f"answers upload failed: {e}")
        time.sleep(2 * attempt)
    raise last


def session_headers(session: dict) -> dict:
    return {"X-Eval-Upload-Session-Token": session["session_token"]}


def close_session(client: FaiClient, session: dict) -> dict:
    try:
        return client.post(f"{SESSIONS}/{session['session_id']}/close",
                           extra_headers=session_headers(session)) or {}
    except FaiError as e:
        if e.status == 403 and "session is closed" in (e.body or "").lower():
            return {"session_id": session["session_id"], "already_closed": True}
        raise


def verify_dry_run_report(report, manifest, dataset_id, flags) -> None:
    if report.get("mode") != "dry-run":
        fail("--dry-run-report is not a dry-run report")
    if not report.get("upload_allowed"):
        fail("dry-run reported upload_allowed: false — fix and rerun dry-run first")
    if (report.get("dataset") or {}).get("id") != dataset_id:
        fail("--dry-run-report is for a different test batch; rerun dry-run")
    if report.get("manifest_sha256") != sha256_json(manifest):
        fail("--manifest changed since dry-run; rerun dry-run")
    if report.get("files") != file_fingerprints(manifest):
        fail("manifest document files changed on disk since dry-run; rerun dry-run")
    if report.get("validation_flags") != flags:
        fail("upload flags differ from the flags used at dry-run; rerun dry-run "
             "with matching --allow-* flags")


def cmd_upload(args) -> None:
    require_object_id(args.dataset_id, "dataset_id")
    manifest_path = Path(args.manifest).expanduser()
    manifest = read_json_file(manifest_path, "--manifest")
    submissions = manifest.get("submissions") or []
    flags = validation_flags(args)

    if args.force_no_dry_run:
        print("!! --force-no-dry-run: skipping the dry-run freshness checks. "
              "Validation still runs, but you are uploading without a reviewed plan.",
              file=sys.stderr)
    else:
        report_path = (Path(args.dry_run_report).expanduser() if args.dry_run_report
                       else manifest_path.parent / "dry_run_report.json")
        if not report_path.exists():
            fail(f"no dry-run report at {report_path} — dry-run is mandatory before "
                 "upload (or pass --force-no-dry-run to override, not recommended)")
        verify_dry_run_report(read_json_file(report_path, "--dry-run-report"),
                              manifest, args.dataset_id, flags)

    client = build_client(args)
    dataset = client.get(f"{BASE}/datasets/{args.dataset_id}")
    template_data = client.get(f"{BASE}/datasets/{args.dataset_id}/gt-template/content")
    existing = existing_submission_names(client, args.dataset_id)
    issues = validate_manifest(manifest, dataset, template_data, existing, flags)
    errors = [i for i in issues if i["severity"] == "error"]
    if errors:
        print("validation failed at upload time (test batch changed since dry-run?):")
        print_issues(errors)
        fail("fix the manifest or rerun dry-run")
    template_steps = template_steps_of(template_data)

    # Create the scoped upload session (the only auth the write routes accept).
    resp = client.post(SESSIONS, {
        "dataset_id": args.dataset_id,
        "ttl_minutes": SESSION_TTL_MINUTES,
        "reason": args.reason or "fai plugin eval-studio bulk upload",
    })
    if not isinstance(resp, dict) or not resp.get("session_id") or not resp.get("session_token"):
        fail(f"upload session response missing session_id/session_token: {str(resp)[:300]}")
    session = {
        "session_id": resp["session_id"],
        "session_token": resp["session_token"],
        "expires_at": resp.get("expires_at"),
        "env": client.env,
        "org_id": client.org_id,
        "dataset_id": args.dataset_id,
    }
    audit_path = (Path(args.audit).expanduser() if args.audit
                  else manifest_path.parent / "upload_audit.json")
    session_path = audit_path.parent / "upload_session.json"
    write_json_file(session_path, session)
    session_path.chmod(0o600)
    print(f"upload session: {session['session_id']} (expires {session.get('expires_at')})")
    print(f"  session file (contains the session token — do not share): {session_path}")

    audit = {"session_id": session["session_id"], "dataset_id": args.dataset_id,
             "env": client.env, "validation_flags": flags, "submissions": [], "failed": []}
    exit_code = 0
    try:
        preflight = client.get(f"{SESSIONS}/{session['session_id']}/preflight",
                               extra_headers=session_headers(session))
        pf_dataset = (preflight or {}).get("dataset") or {}
        for key in ("workflow_id", "selected_steps", "pinned_workflow_version"):
            if pf_dataset.get(key) != dataset.get(key):
                raise FaiError(f"session preflight disagrees with the test batch on {key} — "
                               "the batch changed mid-flight; rerun dry-run")

        # Allocate samples + document upload URLs (batched; backend cap 500).
        for start in range(0, len(submissions), UPLOAD_URL_BATCH):
            batch = submissions[start:start + UPLOAD_URL_BATCH]
            alloc = client.post(
                f"{SESSIONS}/{session['session_id']}/submissions/upload-urls",
                {"submissions": [
                    {"submission_name": s["submission_name"],
                     "files": [{"filename": Path(str(p)).name} for p in s["files"]]}
                    for s in batch]},
                extra_headers=session_headers(session), timeout=300)
            allocated = (alloc or {}).get("submissions") or []
            if len(allocated) != len(batch):
                raise FaiError(f"upload-urls returned {len(allocated)} submissions "
                               f"for {len(batch)} requested")
            for sub, got in zip(batch, allocated):
                audit["submissions"].append({
                    "submission_name": sub["submission_name"],
                    "submission_id": got["submission_id"],
                    "files": [{"local_path": str(p), "filename": Path(str(p)).name,
                               "document_id": f.get("document_id"),
                               "sas_url": f["sas_url"], "uploaded": False}
                              for p, f in zip(sub["files"], got["files"])],
                    "gt_upload": None,
                })
        write_json_file(audit_path, {**audit, "submissions": [
            {**s, "files": [{k: v for k, v in f.items() if k != "sas_url"}
                            for f in s["files"]]} for s in audit["submissions"]]})

        # Upload document bytes to blob storage.
        jobs = [(entry, f) for entry in audit["submissions"] for f in entry["files"]]
        failures = []
        with ThreadPoolExecutor(max_workers=max(1, min(args.document_concurrency, len(jobs) or 1))) as pool:
            futures = {pool.submit(put_file_to_sas, f["sas_url"], Path(f["local_path"]),
                                   args.document_timeout, args.document_retries): (entry, f)
                       for entry, f in jobs}
            for future in as_completed(futures):
                entry, f = futures[future]
                try:
                    future.result()
                    f["uploaded"] = True
                except FaiError as e:
                    failures.append(f"{entry['submission_name']}/{f['filename']}: {e}")
                    audit["failed"].append({"stage": "document_upload",
                                            "submission": entry["submission_name"],
                                            "filename": f["filename"], "error": str(e)})
        if failures:
            raise FaiError(f"{len(failures)} document upload(s) failed; first: {failures[0]}")

        # Mark samples complete (required before answers can attach).
        submission_ids = [s["submission_id"] for s in audit["submissions"]]
        complete = client.post(
            f"{SESSIONS}/{session['session_id']}/submissions/complete-upload",
            {"submission_ids": submission_ids},
            extra_headers=session_headers(session), timeout=300)
        audit["complete_upload"] = complete
        if (complete or {}).get("failed_submissions"):
            raise FaiError(f"complete-upload reported failures: {complete['failed_submissions']}")

        # Attach answers per sample.
        for sub, entry in zip(submissions, audit["submissions"]):
            steps = ((sub.get("gt") or {}).get("steps") or {})
            if not steps:
                entry["gt_upload"] = {"status": "skipped", "steps_uploaded": {}}
                continue
            payload = sanitize_gt(steps, template_steps, flags["allow_extra_fields"])
            try:
                entry["gt_upload"] = post_gt_multipart(
                    client, session, entry["submission_id"], payload)
            except FaiError as e:
                entry["gt_upload"] = {"status": "failed", "error": str(e)}
                audit["failed"].append({"stage": "gt_upload",
                                        "submission": entry["submission_name"],
                                        "error": str(e)})
    except (FaiError, KeyError) as e:
        audit["upload_error"] = str(e)
        print(f"upload FAILED: {e}", file=sys.stderr)
        print("  samples not yet marked complete are cleaned up when the session closes; "
              "completed samples remain (add-only). Check the audit before retrying.",
              file=sys.stderr)
        print(f"  note: upload sessions expire after ~{SESSION_TTL_MINUTES} minutes.",
              file=sys.stderr)
        exit_code = 2
    finally:
        # Close the session no matter what — it also cleans up allocations that
        # never reached complete-upload.
        try:
            close = close_session(client, session)
            audit["session_closed"] = True
            print("session closed"
                  + (" (was already closed)" if close.get("already_closed") else ""))
        except FaiError as e:
            audit["session_closed"] = False
            print(f"session close FAILED: {e}\n"
                  f"  close it manually: eval_upload.py close-session --session-file {session_path}\n"
                  f"  (sessions self-expire after ~{SESSION_TTL_MINUTES} minutes)",
                  file=sys.stderr)
            exit_code = exit_code or 2
        for entry in audit["submissions"]:
            for f in entry["files"]:
                f.pop("sas_url", None)
        write_json_file(audit_path, audit)

    print()
    print("upload audit summary (add-only — nothing pre-existing was touched):")
    uploaded = skipped = failed = 0
    rows = []
    for entry in audit["submissions"]:
        docs_ok = sum(1 for f in entry["files"] if f.get("uploaded"))
        gt = entry.get("gt_upload") or {}
        gt_steps = len(gt.get("steps_uploaded") or {})
        if docs_ok < len(entry["files"]) or gt.get("status") == "failed":
            status, bump = "FAILED", "failed"
        elif gt.get("status") == "skipped":
            status, bump = "uploaded (no answers)", "skipped"
        else:
            status, bump = "uploaded", "uploaded"
        uploaded += bump == "uploaded"
        skipped += bump == "skipped"
        failed += bump == "failed"
        rows.append([entry["submission_name"], entry["submission_id"],
                     f"{docs_ok}/{len(entry['files'])}", gt_steps, status])
    print_table(["SAMPLE", "SUBMISSION_ID", "DOCS", "ANSWER_STEPS", "STATUS"], rows)
    planned = len(submissions)
    print(f"  samples: {uploaded} uploaded, {skipped} uploaded without answers, "
          f"{failed} failed, {planned - len(audit['submissions'])} never allocated "
          f"(of {planned} planned)")
    print(f"  audit: {audit_path}")
    if exit_code == 0 and failed:
        exit_code = 2
    sys.exit(exit_code)


def cmd_close_session(args) -> None:
    session = read_json_file(Path(args.session_file).expanduser(), "--session-file")
    for key in ("session_id", "session_token"):
        if not session.get(key):
            fail(f"--session-file is missing {key}")
    if not args.env and session.get("env"):
        args.env = session["env"]
    if not args.org_id and not args.account and session.get("org_id"):
        args.org_id = session["org_id"]
    client = build_client(args)
    result = close_session(client, session)
    print("session closed"
          + (" (was already closed)" if result.get("already_closed") else ""))
    print(f"  session_id: {session['session_id']}")


# ----------------------------------------------------------------------- main

def add_common(parser) -> None:
    parser.add_argument("--env", choices=("prod", "staging", "local"),
                        help="default from ~/.fai/accounts.json defaults.env, else prod")
    parser.add_argument("--account", help="account slug/name/domain from ~/.fai/accounts.json")
    parser.add_argument("--org-id", help="explicit PropelAuth org UUID (wins over --account)")


def add_relax_flags(parser) -> None:
    parser.add_argument("--allow-partial-gt", action="store_true",
                        help="allow samples without answers / with answers for only some selected steps")
    parser.add_argument("--allow-missing-fields", action="store_true",
                        help="allow answers rows missing template fields")
    parser.add_argument("--allow-extra-fields", action="store_true",
                        help="allow answers fields not in the template (backend may drop them)")
    parser.add_argument("--allow-duplicate-names", action="store_true",
                        help="downgrade existing-sample name collisions from error to warning")


def main(argv=None) -> None:
    top = argparse.ArgumentParser(
        prog="eval_upload.py",
        description="Bulk sample upload for Eval Studio test batches: "
                    "normalize -> dry-run -> upload (add-only).")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("normalize",
                       help="convert CSV/Excel/JSON/folder input into a normalized manifest")
    add_common(p)
    p.add_argument("--input", required=True,
                   help="source: .csv/.xlsx/.json file, or a folder of per-sample subfolders")
    p.add_argument("--out-dir", default="eval_upload_staging",
                   help="staging directory for manifest.json + normalize_report.json")
    p.add_argument("--dataset", help="dataset ID — fetches the live answers template "
                                     "(needed for CSV/Excel column inference)")
    p.add_argument("--template", help="saved gt-template content JSON (offline alternative to --dataset)")
    p.add_argument("--mapping", help="JSON mapping file (see references/bulk_upload.md)")
    p.add_argument("--documents-dir", help="base directory for relative document paths")
    p.add_argument("--submission-column", help="explicit sample-name column for tabular input")
    p.add_argument("--files-column", help="explicit files column for tabular input")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("dry-run",
                       help="MANDATORY before upload: validate the manifest against the "
                            "live test batch; prints what would be uploaded, writes no data")
    add_common(p)
    p.add_argument("dataset_id")
    p.add_argument("--manifest", required=True, help="normalized manifest from normalize")
    p.add_argument("--report", help="report path (default: dry_run_report.json next to the manifest)")
    add_relax_flags(p)
    p.set_defaults(func=cmd_dry_run)

    p = sub.add_parser("upload",
                       help="execute the plan: session -> preflight -> documents -> answers; "
                            "add-only; requires a matching dry-run report")
    add_common(p)
    p.add_argument("dataset_id")
    p.add_argument("--manifest", required=True)
    p.add_argument("--dry-run-report",
                   help="dry-run report (default: dry_run_report.json next to the manifest)")
    p.add_argument("--force-no-dry-run", action="store_true",
                   help="skip the dry-run freshness check (NOT recommended; validation still runs)")
    p.add_argument("--reason", help="audit reason recorded on the upload session")
    p.add_argument("--audit", help="audit file path (default: upload_audit.json next to the manifest)")
    p.add_argument("--document-concurrency", type=int, default=8,
                   help="parallel blob uploads (default 8)")
    p.add_argument("--document-timeout", type=int, default=900,
                   help="per-document upload timeout seconds (default 900)")
    p.add_argument("--document-retries", type=int, default=3,
                   help="per-document retry attempts (default 3)")
    add_relax_flags(p)
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("close-session",
                       help="manually close an orphaned upload session (upload closes its own)")
    add_common(p)
    p.add_argument("--session-file", required=True,
                   help="upload_session.json written by upload")
    p.set_defaults(func=cmd_close_session)

    args = top.parse_args(argv)
    try:
        args.func(args)
    except AccountError as e:
        fail(str(e))
    except FaiError as e:
        detail = f" | body: {e.body[:400]}" if e.body else ""
        fail(f"{e}{detail}")
    except KeyboardInterrupt:
        fail("interrupted", code=130)


if __name__ == "__main__":
    main()
