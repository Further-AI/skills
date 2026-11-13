# Bulk upload input shapes

Reference for `eval_upload.py normalize` / `dry-run` / `upload`. All inputs
converge on one normalized manifest; anything the normalizer doesn't
understand can be hand-written in this format and fed straight to `dry-run`.

## Normalized manifest

```json
{
  "version": "1",
  "source": {"path": "labels.csv", "sha256": "..."},
  "submissions": [
    {
      "submission_name": "sample-001",
      "files": ["/data/acme/sample-001/application.pdf"],
      "metadata": {},
      "gt": {
        "steps": {
          "Basic Fields": {
            "insured_name": "Acme Inc",
            "policy_number": "P-123"
          },
          "Loss Runs": [
            {"claim_number": "C-1", "claim_amount": "1200"},
            {"claim_number": "C-2", "claim_amount": "300"}
          ]
        }
      }
    }
  ]
}
```

Rules:

- `submission_name` must be unique within the manifest, and (by default) must
  not collide with an existing sample in the target test batch.
- `files` are local paths (normalize resolves them to absolute). Every real
  attachment belongs here regardless of extension — images, spreadsheets,
  text files. Only OS/tool artifacts are excluded (`.DS_Store`, `__MACOSX/`,
  dotfiles).
- `gt.steps` keys must be **selected** steps of the test batch, matching the
  answers template verbatim (`gt-template` in eval_studio.py shows it).
- Key-value step answers are an object; grid/table step answers are a list of
  row objects. Field names must match the template (extras/missing block the
  dry-run unless explicitly relaxed).
- Steps inside a document loop use the opaque `__fai_eval_loop_doc__:` keys —
  see the document-loop rules in SKILL.md. Those keys only exist after a run,
  so for loop steps upload documents first (`--allow-partial-gt`), run the
  samples, then add answers.
- `metadata` is free-form and not uploaded as answers; normalize parks
  unmapped spreadsheet columns under `metadata.unmapped_columns`.

## Folder input

One subfolder per sample:

```
acme-golden-set/
  sample-001/
    gt.json                 # optional answers: {"steps": {...}} (or bare steps object)
    application.pdf         # documents = all files in the folder...
  sample-002/
    documents/              # ...or in a documents/ subdir when present
      submission.pdf
      loss_runs.xlsx
    ground_truth.json       # gt.json, ground_truth.json, or answers.json
```

The folder name becomes `submission_name`. Folders with no documents and no
answers are skipped (listed in `normalize_report.json`). For any other layout,
write the manifest directly instead of forcing it through the normalizer.

## CSV / Excel input

Needs the answers template for column inference: pass `--dataset <id>` (live
fetch) or `--template <file>` (saved `gt-template` content). CSV is
stdlib-only; `.xlsx/.xls/.xlsm` need
`python3 -m pip install pandas openpyxl`.

```csv
submission_name,files,insured_name,Loss Runs.claim_number,Loss Runs.claim_amount
sample-001,sample-001.pdf,Acme Inc,C-1,1200
sample-001,sample-001.pdf,Acme Inc,C-2,300
sample-002,"a.pdf|b.pdf",Globex,C-9,50
```

- Sample column inferred from: `submission_name`, `sample_name`, `sample`,
  `name`, `id` — or pass `--submission-column`.
- Files column inferred from: `files`, `file`, `documents`, `document`,
  `document_path(s)`, `filename` — or pass `--files-column`. Multiple files
  split on `|` or `;` (or a JSON list). Relative paths resolve against
  `--documents-dir`.
- Answer columns: a bare field name that exists in exactly one template step,
  or an explicit `Step Name.field_name`. Anything else lands in
  `metadata.unmapped_columns` and is reported — unmapped data is never
  silently uploaded.
- Grid steps: repeat the sample name across rows; each row becomes one grid
  row. Key-value fields must not conflict across those repeated rows.
- Cells that parse as JSON (`[...]`/`{...}`) are kept structured; numbers and
  booleans become strings; empty cells are omitted.

Ambiguous columns? Use a mapping file (`--mapping map.json`):

```json
{
  "submission_column": "Case ID",
  "files_column": "PDFs",
  "ignore_columns": ["Notes", "Reviewer"],
  "gt_columns": {
    "Applicant": "Basic Fields.insured_name",
    "Policy #": {"step": "Basic Fields", "field": "policy_number"}
  }
}
```

## JSON input

`normalize --input x.json` accepts three shapes:

1. A normalized manifest (passed through; file paths resolved).
2. A bare array of submission objects.
3. A single `{"steps": {...}}` answers object — becomes one sample named
   after the file; add its `files` to the manifest before dry-run.

## Attaching answers: what actually lands

At upload time answers are filtered to the template's field set (unless
`--allow-extra-fields`, and even then the backend projects to the template —
extras never survive a pre-bootstrap template). The dry-run report is the
source of truth for what will land: check `planned_writes`,
`validation.issues`, and any `PRE_BOOTSTRAP_TEMPLATE` warning before
uploading.
