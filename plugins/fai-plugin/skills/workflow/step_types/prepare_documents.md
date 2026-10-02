# prepare_documents

### What this step does

`prepare_documents` is the normalization layer between the raw execution request and the rest of the workflow. The Workflow Dispatcher hands it a list of `user_document_ids`, and it:

1. Fetches each UserDocument from storage.
2. If any input is an email (`.eml` / `.msg`), parses it into `email_body`, `headers`, and attachments — the attachments become first-class documents in the output `documents` array, and the original email is exposed via `eml_user_document_id` (plus per-email bundles in `emails`).
3. Unpacks ZIP archives so the inner files become individual `documents` entries.
4. Deduplicates documents by content hash so a file uploaded twice does not get processed twice downstream.
5. Optionally splits multi-sheet Excel workbooks into one document per sheet (`split_excel_sheets`).
6. Optionally detects non-English files and translates them to `target_language` (`translate_documents`).

Nearly mandatory in every workflow — without this step, the rest of the workflow has nothing to read. It also auto-fills any pending ImageRight placeholder documents among its inputs before processing.

### Required inputs

Exactly one input must be wired and it is non-negotiable:

| Parameter | Source | Notes |
|---|---|---|
| `user_document_ids` | `Workflow Dispatcher.user_document_ids` | Array of string IDs. Dependency-only — no static values, no prompt input. |

The platform's save validator does **not** flag a missing `user_document_ids` wiring, but the bundled validator does — because without it, the step runs against an empty list and every downstream step silently produces empty output. Treat this as a hard rule.

### Config keys

All four keys are optional; defaults match the most common shape.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `split_excel_sheets` | bool | `false` | Split multi-sheet workbooks into one document per sheet. Turn on when downstream classification/extraction is sheet-level (most SOV / loss-run workflows leave it off and let `classify_documents.split_sheets` handle splitting). |
| `translate_documents` | bool | `false` | Detect-and-translate non-English documents. English files pass through untouched. |
| `target_language` | str | `"en"` | ISO language code for translation. Only meaningful when `translate_documents` is `true`. |
| `include_hidden_sheets` | bool | `true` | Whether hidden Excel sheets are kept when unpacking workbooks. Default differs from `classify_documents.include_hidden_sheets` (`false`) — prepare keeps the raw shape, classify filters reference data. |

Keys NOT recognized: `documents`, `language`, `source_language`, `dedupe`, `unzip`. Unknown keys are silently ignored — the workflow looks valid but the option does nothing. Stick to the four above.

### Output schema

```
{
  "documents": [ WorkflowDocument, ... ],    // always present; empty list if no docs survive
  "email_body":            "string | null",  // HTML or plain text; pinned to the FIRST uploaded email; null when no email
  "eml_user_document_id":  "string | null",  // ID of the first .eml/.msg UserDocument; null when no email
  "headers": {                               // null when no email; first email's headers
    "from": "string", "to": "string", "cc": "string",
    "subject": "string", "date": "string"
  },
  "emails": [                                // one entry per .eml/.msg — per-email bundles
    { "eml_user_document_id": "...", "filename": "...", "email_body": "...", "headers": {...}, "attachments": [...] }
  ],
  "eml_files": [ WorkflowDocument, ... ]     // flat list of the .eml/.msg files as workflow-document refs
}
```

The scalar email fields (`email_body`, `headers`, `eml_user_document_id`) are pinned to the first uploaded email for back-compat; the grouped `emails` list carries every email with its own attachments (scoped post-dedup). `eml_files` has the same shape as a classify bucket — merge it via `concat_lists` to feed `agentic_extraction.documents` alongside classified docs when the emails themselves should be searchable.

Each `WorkflowDocument` exposes at least `user_document_id`, `filename`, `file_hash`, and (after classify) `category`. Typical downstream wiring:

- `classify_documents`, `knowledge_base`, `sov_mapping` → `documents`
- `extract_from_multiple_sources` (email side) → `emails`, or the scalars `eml_user_document_id` + `headers` + `email_body`

### Input wiring (input_mappings vs dependencies)

- **NEW steps**: use `input_mappings` only. Set `dependencies: []`.
- **EXISTING steps**: preserve whatever pattern is already there — do not flip a legacy `dependencies`-based step opportunistically.
- **Migration** `dependencies` → `input_mappings` on explicit request only. The reverse is forbidden.

The canonical `input_mappings` entry:

```json
{
  "input_type": "dependency",
  "input_parameter_name": "user_document_ids",
  "value": {
    "dependency_step_outputs": [
      {"step_name": "Workflow Dispatcher", "output_attribute": "user_document_ids"}
    ],
    "resolution_operator": null,
    "resolution_config": null
  }
}
```

### Common patterns

#### Pattern 1 — Modern, full config (most common in newer workflows)

```json
{
  "name": "Prepare Documents",
  "type": "prepare_documents",
  "input_mappings": [
    {
      "input_type": "dependency",
      "input_parameter_name": "user_document_ids",
      "value": {
        "dependency_step_outputs": [
          {"step_name": "Workflow Dispatcher", "output_attribute": "user_document_ids"}
        ],
        "resolution_operator": null,
        "resolution_config": null
      }
    }
  ],
  "dependencies": [],
  "config": {
    "split_excel_sheets": false,
    "translate_documents": false,
    "target_language": "en",
    "include_hidden_sheets": true
  },
  "display_step": true,
  "start_title": "Receiving submission",
  "end_title": "Submission received"
}
```

#### Pattern 2 — Legacy, minimal config (older workflows; preserve as-is)

```json
{
  "name": "Read Data",
  "type": "prepare_documents",
  "dependencies": [{"step_name": "Workflow Dispatcher", "field_selector": null}],
  "input_mappings": [],
  "config": {},
  "display_step": true,
  "start_title": "Receiving submission"
}
```

Empty `config: {}` is valid — all four keys default. Do not migrate this unless the user asks.

#### Pattern 3 — Translation enabled

Same as Pattern 1 with `"translate_documents": true, "target_language": "en"`.

### Common validation errors and fixes

| Error | Cause | Fix |
|---|---|---|
| `prepare_documents MUST wire 'user_document_ids' from 'Workflow Dispatcher.user_document_ids'` | No input_mapping (or `dependencies` entry) supplying `user_document_ids`. | Add the canonical `input_mappings` entry above. |
| Downstream step output is empty even though the user uploaded files | Same root cause — the platform save validator didn't catch the missing wiring, and at runtime `user_document_ids = []`. | Re-validate with the bundled validator; add the missing wiring. |
| `target_language` set without `translate_documents: true` | Harmless dead config. | Either turn on `translate_documents` or drop `target_language`. |
| Unknown config key accepted but ignored (e.g. `language`, `dedupe`, `unzip`) | Extras are silently dropped. | Use only the four documented keys. |

### Common gotchas

- **Do NOT set `output_type`** to `"table"` / `"text"` / anything else on prepare_documents — the system handles its own display (`email` render when the first step visualizes an inbound email). Leave it `null`.
- **`enable_rerun: false`** — re-running prepare_documents in isolation resets every downstream step. Leave the default.
- **Excel splitting belongs in *one* place, not both** — if `classify_documents.split_sheets = true` (the classifier default), do NOT also set `prepare_documents.split_excel_sheets = true`. Double-splitting produces empty/duplicate sheet documents and confuses the classifier.
- **Email-only inputs still produce `documents`** — attachments are unpacked into the `documents` list. Downstream extraction can wire `documents` AND email fields from the same prepare step.
- **Hidden sheets default differs from classify_documents** — intentional: prepare keeps the raw shape, classify filters reference data.
- **Corrupted files surface as a structured failure** that short-circuits the rest of the workflow — downstream steps report as skipped, not "succeeded with empty output". If a user reports "everything ran but the table is empty," the cause is almost always the missing-wiring bug above, not a corrupted file.

### See also

- `workflow_dispatcher` — the only valid source of `user_document_ids`.
- `classify_documents` — typical next step; consumes `documents`.
- `knowledge_base` — consumes `documents` and/or `emails`.
- `extract_from_multiple_sources` — direct downstream consumer; wires `documents` and/or the email fields per its at-least-one-source rule.
