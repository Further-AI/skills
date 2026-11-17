# classify_documents

### What this step does

`classify_documents` is the routing layer between raw normalized documents and per-category processing. It receives the `documents` array from `prepare_documents` (or any step that outputs `documents`), runs each document through an LLM-based classifier (or a vision agent), and emits a dict where each key is a class name and each value is the subset of documents assigned to that class.

By default each document lands in exactly one class. Optional config knobs change the unit of classification (whole file vs. per page vs. per sheet) and whether sheets may belong to multiple classes. Documents that the classifier cannot place can be sent to a `fallback_class`.

This is the highest-leverage accuracy step in most workflows — downstream extraction quality is gated by clean, mutually exclusive class definitions.

### Required inputs

| Parameter | Source | Notes |
|---|---|---|
| `documents` | `prepare_documents.documents` (or any step output of type `array<file>`) | Hard-required for the step to do anything useful. The bundled validator and save-time validation both flag a missing wiring. |
| `system_prompt` | static (recommended) — domain-specific classification rubric | Not strictly required at save time, but always wire one. Without it, the classifier falls back to a generic insurance prompt and accuracy drops on niche document types. |

`system_prompt` MUST go through `input_mappings` with `input_type: "static"`, NOT in `config.system_prompt`.

### Config keys

`classes` is the only required key. Everything else has a sensible default.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `classes` | `list[{name, description}]` | — (required) | The categories the classifier may assign. `name` is the key used in the output dict; `description` is what the LLM sees. Make them mutually exclusive. |
| `fallback_class` | str or null | `null` | Name of a class to use when the classifier is unsure. Must be one of the `classes[].name` values. A common pattern is an explicit `"Other"` class with `fallback_class: "Other"`. |
| `split_pages` | bool | `false` | When `true`, PDFs are classified per page (each page may go to a different class). Useful when one PDF contains multiple document types stapled together. |
| `split_sheets` | bool | `true` | When `true`, Excel sheets are classified individually. Leave on for most workflows; turn off only if the workbook is logically a single document. |
| `allow_multiple_categories_per_sheet` | bool | `false` | Permits a sheet (or page when combined with `split_pages`) to land in more than one class. Use sparingly — multi-class output complicates downstream filtering. |
| `output_empty_classes` | bool | `true` | When `true`, the output dict always contains a key for every declared class (empty list if no docs landed there). Keep `true` so downstream wirings never KeyError. |
| `knowledge_source_category` | str or null | `null` | Name of a class to which documents originating from a knowledge source are auto-assigned (skips AI classification for them). Used in workflows that mix uploads + KB-attached reference docs. |
| `split_rules` | str or null | `null` | Free-text rules appended to the page-splitting prompt. Only meaningful when `split_pages: true`. Example: `"Pages CAN be classified into MULTIPLE categories when they contain multiple coverage sections."` |
| `include_hidden_sheets` | bool | `false` | Whether hidden Excel sheets are sent to the classifier. Default `false` (opposite of `prepare_documents.include_hidden_sheets`) because hidden sheets are usually reference tables that confuse the classifier. |
| `use_vision_for_classification` | bool | `false` | When `true`, PDFs and images are rendered to images and sent to a vision-capable model alongside extracted text. Useful for visually distinctive documents (ACORD vs. quote letter) where text alone is ambiguous. |
| `use_agent` | bool | `false` | When `true`, uses the visual classification agent (renders page images and sends submission context to a reasoning model). Stronger than `use_vision_for_classification` but slower / more expensive. |
| `model` | enum | `DEFAULT_AI_MODEL` | LLM to use. `"gpt-5.1"` suits this high-volume step; `"gpt-5.5"` when classification accuracy is the bottleneck. Never `gpt-4o`/`gpt-4o-mini` (legacy). |
| `reasoning_effort` | enum or null | `null` | Reasoning models only. Cross-validated against `model` — effort settings on non-reasoning models are rejected. |
| `verbosity` | enum or null | `null` | GPT-5 models only. |

Keys NOT recognized — the save-time validator and the bundled validator both reject these common mistakes:

| Wrong name | Use instead |
|---|---|
| `categories` | `classes` |
| `default_category` | `fallback_class` |
| `split_excel_sheets` | `split_sheets` |
| `split_pages_pdf` | `split_pages` |
| `allow_multiple_categories` | `allow_multiple_categories_per_sheet` |

Any other unknown key is also rejected — the config model is strict about extras.

### Output schema

```
{
  "documents": {
    "<class_name_1>": [ WorkflowDocument, ... ],
    "<class_name_2>": [ WorkflowDocument, ... ],
    ...
  }
}
```

When `output_empty_classes: true` (the default), every key in `config.classes` appears in the output dict, even if its array is empty. Downstream wirings can therefore safely reference any declared class name without runtime KeyError.

### Input wiring (input_mappings vs dependencies)

- NEW steps: use `input_mappings` only; `dependencies` always `[]`.
- EXISTING steps: preserve whatever pattern is already there.
- Migration `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

Canonical modern wiring (documents from `prepare_documents`, system prompt as a static input):

```json
{
  "input_mappings": [
    {
      "input_type": "dependency",
      "input_parameter_name": "documents",
      "value": {
        "dependency_step_outputs": [
          {"step_name": "Prepare Documents", "output_attribute": "documents"}
        ],
        "resolution_operator": null,
        "resolution_config": null
      }
    },
    {
      "input_type": "static",
      "input_parameter_name": "system_prompt",
      "value": "You are classifying documents attached to ... <domain rubric> ..."
    }
  ],
  "dependencies": []
}
```

### Common patterns

#### Pattern 1 — Modern, four-bucket claims intake

Typical claims/reinsurance intake: cover note vs. billing vs. historical loss runs vs. junk. Uses lowercase snake_case class names so downstream glom paths stay simple.

```json
{
  "name": "Classify Documents",
  "type": "classify_documents",
  "input_mappings": [
    {
      "input_type": "dependency",
      "input_parameter_name": "documents",
      "value": {
        "dependency_step_outputs": [
          {"step_name": "Prepare Documents", "output_attribute": "documents"}
        ]
      }
    },
    {
      "input_type": "static",
      "input_parameter_name": "system_prompt",
      "value": "You are classifying documents attached to a claims-intake email. Categories must be mutually exclusive at the document level. Junk / uninterpretable / unrelated content goes to 'other'. ..."
    }
  ],
  "dependencies": [],
  "config": {
    "classes": [
      {"name": "primary_claims_documents", "description": "Cover note describing the loss/update."},
      {"name": "billings", "description": "Invoices and billing transmittals."},
      {"name": "policy_losses_to_date", "description": "Loss runs, scheduled losses, historical claim/policy loss lists."},
      {"name": "other", "description": "Junk, signature blocks, unrelated marketing material."}
    ],
    "output_empty_classes": true,
    "split_pages": false,
    "split_sheets": true,
    "allow_multiple_categories_per_sheet": false,
    "model": "gpt-5.1"
  }
}
```

#### Pattern 2 — Submission intake with explicit fallback

Property/casualty submission with ACORD + loss run + SOV + appointment letter + Other. `fallback_class` ensures uncertain documents are routed to a reviewable bucket rather than the dominant class.

```json
{
  "config": {
    "classes": [
      {"name": "ACORD", "description": "ACORD application forms (125, 126, 140, etc.)."},
      {"name": "Loss Run", "description": "Carrier-issued loss run reports."},
      {"name": "NKLL", "description": "No-known-loss letter signed by the insured."},
      {"name": "SOV", "description": "Statement of Values / location schedule."},
      {"name": "Broker Appointment Letter", "description": "Broker of record / appointment letter."},
      {"name": "Other", "description": "Anything that does not match the above."}
    ],
    "fallback_class": "Other",
    "split_pages": true,
    "split_sheets": false,
    "split_rules": "Pages CAN be classified into MULTIPLE categories when they contain multiple coverage sections.",
    "output_empty_classes": true,
    "model": "gpt-5.1"
  }
}
```

#### Pattern 3 — Single-class filter (loss run vs. everything else)

When the workflow cares about exactly one document type, use a two-class config plus `fallback_class` so unmatched docs land in `Other` without polluting the positive bucket.

```json
{
  "config": {
    "classes": [
      {"name": "Loss Run Report", "description": "Carrier-issued loss run for the named insured."},
      {"name": "Other", "description": "Anything not a loss run report."}
    ],
    "fallback_class": "Other",
    "split_sheets": true,
    "output_empty_classes": true,
    "model": "gpt-5.1"
  }
}
```

### Common validation errors and fixes

| Error | Cause | Fix |
|---|---|---|
| `classify_documents requires a non-empty config.classes list of {name, description} entries.` | `classes` missing or empty. | Add at least one `{name, description}` entry. |
| `config.<wrong_key> is not a valid classify_documents field. Use config.<correct_key> instead.` | One of the five known wrong names (see table above). | Rename per the table. |
| `unknown classify_documents config key '<x>'` | Strict-extras rejection. | Drop the key or check it against the valid list. |
| `Parameter 'documents' expects type 'array' but step 'Classify Documents' outputs type 'object'` | A downstream step wired `Classify Documents.documents` (bare) instead of `Classify Documents.documents.<ClassName>`. | Add the class-name suffix: `output_attribute: "documents.Loss_Run"`. |
| Downstream step produces empty output despite documents being uploaded | Downstream `output_attribute` references a class name that does not exist in `config.classes` (typo / case mismatch). The lookup misses and returns `[]` at runtime — no error. | The bundled validator catches this; fix the typo OR add the class to `config.classes`. |
| `reasoning_effort` / `verbosity` set on a non-reasoning model | Cross-field model validation. | Either change `model` to a reasoning model OR drop the effort/verbosity fields. |

### Common gotchas

- **Downstream wirings use glom-path `<step_name>.documents.<category>`** — always include the class name in `output_attribute` when feeding a downstream extraction / SOV / Q&A step. Bare `documents` resolves to an `object` and is rejected where `array<file>` is expected.
- **Class names are exact-match keys** — `"Loss_Run"` and `"Loss Run"` are different categories. Pick a convention and use it consistently in both `config.classes` and downstream `output_attribute` references. The bundled validator catches mismatches that runtime would silently empty.
- **`fallback_class` must be one of the declared `classes[].name`** — setting `fallback_class: "Other"` while no class is named `"Other"` means the fallback never fires.
- **`split_sheets` defaults to `true`, `prepare_documents.split_excel_sheets` defaults to `false`** — do not enable both. Double-splitting produces empty/duplicate per-sheet documents and confuses the classifier. Pick one place.
- **`include_hidden_sheets` defaults to `false`** here (opposite of `prepare_documents`). Hidden sheets are typically reference lookups — keep them out of classification unless you have a specific reason.
- **Empty `classes` description** — the classifier sees only the `description` text; a class named `"Loss Run"` with `description: ""` will be mis-assigned. Treat descriptions as part of the prompt.
- **Vision vs. agent** — `use_vision_for_classification: true` is the cheap upgrade (text + page images, single LLM call per doc). `use_agent: true` is the heavyweight option — enable only when accuracy on visually distinctive docs justifies the cost.
- **System prompt belongs in `input_mappings`, not `config.system_prompt`** — the canonical pattern is `input_type: "static"` so prompt edits flow through the standard input-mapping surface.
- **`output_empty_classes: true` is almost always what you want** — turning it off means downstream wirings that reference unused classes fail with `KeyError`. Only disable when every declared class is guaranteed a document on every run.

### See also

- `prepare_documents` — canonical predecessor; produces the `documents` array consumed here.
- `extract_from_multiple_sources` — typical successor; wires `Classify Documents.documents.<ClassName>` to scope extraction to one category at a time.
- `extract_rows_from_multiple_sources`, `sov_mapping`, `agentic_extraction`, `multi_column_qa` — other downstream consumers that benefit from per-class filtering.
- `knowledge_base` — when both classify and KB are present, use `knowledge_source_category` to auto-route KB-attached docs to a known class instead of letting the classifier guess.
