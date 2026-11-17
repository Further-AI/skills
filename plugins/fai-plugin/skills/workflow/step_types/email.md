# email

### What this step does

Renders the email body (either a static HTML string from `email_body`, an upstream step's HTML output mapped to `email_body`, or — when `use_compose_function=True` — a fresh body composed by an LLM from the dependency-step outputs) and POSTs to a Zapier webhook that performs the actual send.

The send path only executes when `zapier_reply_url` and a thread id are present. Without `zapier_reply_url` the step still "succeeds" with `sent=False` — compose-only mode, useful for previewing a draft in the UI (this is how UI-only "display email" summary steps work: no recipients, no webhook, the email renders on the canvas only). When `zapier_reply_url` IS set, missing `thread_id` / `from_email` / `to_email` / `subject` raises `ValueError` at runtime; the save-time validators catch these first.

Attachments come in via two parameters:

- `attachment_document_ids` — list of `UserDocument` IDs. `fill_docx` cannot be wired here directly: its output is `generated_document.user_document_id` (a single ID nested in an object) but this input expects `array<string>`. Standard pattern: a small `custom_step` downstream of `fill_docx` that bundles the singular ID into a list (see Pattern B). Other valid sources: a `classify_documents` bucket, or any inline-code step that yields a `user_document_ids` array.
- `attachment_data_list` — list of step outputs (e.g. an `extract_rows_from_multiple_sources` table) that the executor converts to Excel/CSV files before attaching.

Set `send_attachments_in_email=False` to keep the attachments visible in the UI canvas for the user to download, but skip uploading them to Zapier. Useful when the body has signed download links already, or when total attachment size approaches Zapier's silent-drop ceiling (~1MB).

The step has no structured downstream output other than a side effect — `output_type="email"`. Downstream steps should not depend on this step except as a sequencing fence (rare).

### Required inputs

Every parameter is optional at the schema layer — cross-field rules enforce what must actually be present given the chosen mode (`reply_to_original` + `use_compose_function` + `zapier_reply_url` presence).

| Param                     | Type           | Notes                                                                                                                          |
| ------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `email_body`              | string (HTML)  | Static config OR mapped from an upstream HTML-producing step (commonly a `custom_step` that builds a summary).                 |
| `subject`                 | string         | Static config OR mapped. Required at runtime when sending (see validator rules below).                                          |
| `attachment_document_ids` | array<string>  | `input_mapping` from a step that yields a `user_document_ids` array. `fill_docx` cannot be mapped here directly (shape mismatch — see Pattern B). |
| `attachment_data_list`    | array          | `input_mapping` from a step output you want attached as an Excel/CSV file (e.g. a table-shaped output).                        |
| `zapier_reply_url`        | string         | Webhook URL. Set via config OR mapped from incoming trigger metadata. Step is compose-only without it.                          |
| `from_email`              | string         | Required when `reply_to_original=False` AND `zapier_reply_url` is set. Otherwise auto-wired from the incoming email data.       |
| `to_email`                | string         | Same rule as `from_email`.                                                                                                      |
| `cc_emails`               | array<string>  | Optional. CC list.                                                                                                              |
| `thread_id`               | string         | Email thread ID. Auto-wired when `reply_to_original=True`; required as a static/mapped value when `reply_to_original=False`.    |

### Config keys

From `EmailStepConfig`:

| Key                          | Type     | Default                                                                                | Notes                                                                                                                                                                                                                              |
| ---------------------------- | -------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `reply_to_original`          | bool     | `true`                                                                                 | When `true`, routing fields (`from_email`, `from_name`, `to_email`, `to_names`, `cc_emails`, `cc_names`, `thread_id`) are auto-wired from the incoming email data at runtime. When `false`, configure them manually (config or input_mapping). |
| `send_attachments_in_email`  | bool     | `true`                                                                                 | `false` keeps attachments in the UI canvas but skips the Zapier upload. Use for >1MB attachment totals.                                                                                                                             |
| `email_body`                 | string   | `""`                                                                                   | HTML body. Often left empty and supplied via `input_mappings` from an upstream HTML-builder step.                                                                                                                                  |
| `use_compose_function`       | bool     | `false`                                                                                | `true` has an LLM summarize the dependency-step outputs into a body. Requires at least one upstream dependency input (validator rule).                                                                                              |
| `compose_prompt`             | string   | `"Summarize the workflow results and create a professional email body."`               | Custom prompt for composition. For HTML-only output, instruct the model explicitly (e.g. `"Output raw HTML only. NO markdown code fences. Start directly with <h2> or <p>."`).                                                     |
| `model`                      | enum     | `gpt-5.4`                                                                              | Composition model. `"gpt-5.5"` when summary quality matters; `"gpt-5.1"` for cost-sensitive paths. Only used with `use_compose_function=true`.                                                                                      |
| `reasoning_effort`           | enum?    | `null`                                                                                 | Reasoning models only; must be `null` otherwise (validated at save).                                                                                                                                                                 |
| `verbosity`                  | enum?    | `null`                                                                                 | Same validator gate as `reasoning_effort`.                                                                                                                                                                                          |
| `subject`                    | string?  | `null`                                                                                 | Subject line override. Map via `input_mappings` for per-execution subjects.                                                                                                                                                          |
| `zapier_reply_url`           | string?  | `null`                                                                                 | Webhook URL. Without this the step is compose-only (`sent=False`).                                                                                                                                                                   |
| `from_email` / `from_name`   | string?  | `null`                                                                                 | Conditional — meaningful only when `reply_to_original=false`.                                                                                                                                                                        |
| `to_email` / `to_names`      | string? / array? | `null`                                                                          | Conditional — meaningful only when `reply_to_original=false`.                                                                                                                                                                        |
| `cc_emails` / `cc_names`     | array?   | `null`                                                                                 | Conditional — meaningful only when `reply_to_original=false`.                                                                                                                                                                        |
| `thread_id`                  | string?  | `null`                                                                                 | Conditional — meaningful only when `reply_to_original=false`.                                                                                                                                                                        |

### Output schema

```json
{
  "type": "object",
  "properties": {
    "email_body": { "type": "string", "description": "Email body HTML" },
    "headers":    { "type": "object", "description": "Email headers" },
    "attachments":{ "type": "array",  "description": "Email attachments" }
  }
}
```

Downstream consumption is rare — usually nothing depends on an EMAIL step. The body/headers fields exist mostly so the UI can render a "View Email" preview (`output_config.view_output_button_text`).

### Input wiring (input_mappings vs dependencies)

- **NEW steps**: use `input_mappings` only; `dependencies` always `[]`. Wire `email_body` from an upstream HTML-producing step, wire `attachment_document_ids` from a `custom_step` that arrays-up a `fill_docx` output, etc.
- **EXISTING steps**: preserve whatever pattern is there. Many production email steps still use bare `dependencies` (especially when `use_compose_function=true` — see Pattern C). Do not silently rewrite them.
- **Migration** `dependencies` → `input_mappings` on explicit request only; the reverse is forbidden.

When `use_compose_function=true`, the validator requires at least one DEPENDENCY-typed mapping (or, equivalently, a legacy `dependencies` entry) so the LLM has something to summarize. Pure static input_mappings won't satisfy this rule.

### Common patterns

#### Pattern A — Notification-only, reply-to-original (auto-wired routing)

The dominant pattern for email-triggered triage workflows. An upstream `custom_step` builds the HTML summary; the email step replies on the same thread with that body. No routing fields are set — the executor auto-wires them from the incoming email data.

```json
{
  "name": "AI Submission Summary",
  "type": "email",
  "input_mappings": [
    {
      "input_type": "dependency",
      "input_parameter_name": "email_body",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Compose HTML Summary", "output_attribute": "email_body" }
        ]
      }
    }
  ],
  "config": {
    "reply_to_original": true,
    "send_attachments_in_email": true,
    "use_compose_function": false,
    "email_body": "",
    "zapier_reply_url": "<set-at-runtime-or-org-level>"
  }
}
```

Notice `subject` is left null — the reply inherits the original thread's subject.

#### Pattern B — Fresh outbound with explicit subject and an attached doc

Used when the workflow generates a document (via `fill_docx` or similar) and sends it to a fixed recipient. `reply_to_original=false`, so `from_email` / `to_email` / `subject` / `thread_id` must be set in config OR mapped (validator-enforced when `zapier_reply_url` is also set). Attachment IDs are wired from the doc-generation wrapper step.

```json
{
  "name": "Validation Report Email",
  "type": "email",
  "input_mappings": [
    {
      "input_type": "dependency",
      "input_parameter_name": "email_body",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Required Field Validation Report", "output_attribute": "html_content" }
        ]
      }
    },
    {
      "input_type": "dependency",
      "input_parameter_name": "attachment_document_ids",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Generate Output File", "output_attribute": "user_document_ids" }
        ]
      }
    },
    { "input_type": "static", "input_parameter_name": "subject",    "value": "Validation Report" },
    { "input_type": "static", "input_parameter_name": "to_email",   "value": "<recipient@example.com>" },
    { "input_type": "static", "input_parameter_name": "from_email", "value": "<sender@example.com>" },
    { "input_type": "static", "input_parameter_name": "thread_id",  "value": "<thread-id-or-empty>" }
  ],
  "config": {
    "reply_to_original": false,
    "send_attachments_in_email": true,
    "use_compose_function": false,
    "zapier_reply_url": "<set-via-input-mapping-or-config>"
  }
}
```

#### Pattern C — LLM-composed body with `use_compose_function=true` (legacy `dependencies`)

The compose-function mode summarizes every dependency step's output into an HTML body. Common for "AI Submission Summary" steps where many extracted-data steps feed in. Most existing instances use legacy `dependencies` — leave them as-is unless the user asks for migration.

```json
{
  "name": "AI Submission Summary",
  "type": "email",
  "input_mappings": [],
  "dependencies": [
    { "step_name": "Extract Basic Account Information", "field_selector": null },
    { "step_name": "Calculate Financial Ratios",        "field_selector": null },
    { "step_name": "Extract Broker Information",        "field_selector": null }
  ],
  "config": {
    "reply_to_original": true,
    "send_attachments_in_email": true,
    "use_compose_function": true,
    "compose_prompt": "**CRITICAL: Output raw HTML only. NO markdown code fences. Start directly with an <h2> or <p> tag.** Summarize the submission for the underwriter.",
    "model": "gpt-5.5"
  }
}
```

If you build a NEW step using compose-function mode, prefer `input_mappings` with `input_type: "dependency"` entries — but you still need at least one DEPENDENCY mapping for the compose-function validator rule to pass.

### Common validation errors and fixes

#### `reply_to_original=False` with `zapier_reply_url` set, missing routing

> `Step '<name>': set ['thread_id', 'from_email', 'to_email', 'subject'] in config or input_mappings.`

Triggered when `reply_to_original=False`, `zapier_reply_url` is effectively set (static config OR an input_mapping that resolves to a value), and any of `thread_id` / `from_email` / `to_email` / `subject` is neither in config nor effectively mapped. **Fix**: set each missing field as a static value in `config`, or wire it via `input_mappings` — both count as "effectively set".

If you genuinely want compose-only behavior (preview without sending), leave `zapier_reply_url` empty — the runtime short-circuits to `sent=False` and the routing-fields rule does not apply.

#### `use_compose_function=true` without any upstream dependency

> `Step '<name>': wire at least one upstream step as input when 'use_compose_function' is on.`

**Fix**: add at least one DEPENDENCY-typed `input_mappings` entry pointing at the upstream steps you want the LLM to summarize, OR keep the legacy `dependencies` list if migrating an existing step incrementally.

#### Model/effort/verbosity combinations

`reasoning_effort` and `verbosity` are reasoning-model-only. Setting them on an incompatible `model` raises at construction time. **Fix**: leave them `null`.

### Common gotchas

- **`reply_to_original=False` routing rules** — set `from_email`, `to_email`, `thread_id`, AND `subject` whenever `zapier_reply_url` is present. Empty strings count as "not set" in the effective-value check; use a real value or leave the field genuinely null.
- **`use_compose_function=True` always needs an input dependency** — static-only `input_mappings` (e.g. just `subject`) don't count toward the rule.
- **`zapier_reply_url` absence ≠ error** — compose-only mode is legal and useful (UI preview only, no send). A UI-only "display email" summary step is exactly this: no recipients, no webhook.
- **`send_attachments_in_email=False` does NOT remove attachments from the UI** — they still render in the canvas for download. The flag only gates the Zapier upload payload. Use when total attachment size approaches 1MB to avoid Zapier's silent drop.
- **`compose_prompt` must produce raw HTML, not Markdown-fenced HTML** — the body is interpolated directly into the outgoing email. Instruct the model explicitly: "Output raw HTML only. NO markdown code fences." Without this, recipients see literal code fences in the email.
- **Routing fields silently overwritten by `reply_to_original=True`** — even if you set `to_email` / `from_email` in config, with `reply_to_original=True` they get replaced by the incoming email data at runtime. For explicit control, flip to `reply_to_original=False`.
- **Empty-dependency `input_mappings` entries are common in saved workflows** — the builder UI pre-creates routing/attachment input-mapping slots with empty dependency arrays as scaffolding. They're no-ops at runtime. New steps should omit them unless actually wired.

### See also

- `fill_docx` — generates a `.docx` and exposes `generated_document.user_document_id` (singular, nested). To use as an attachment, place a `custom_step` between it and the email step that wraps the singular ID into a `user_document_ids` array.
- `custom_step` — composing the email body programmatically (HTML string) is usually a `custom_step` whose `email_body` output is wired into the email step's `email_body` input. Deterministic alternative to `use_compose_function=True`.
- `extract_from_email` — the inbound counterpart (deprecated; new workflows use `extract_from_multiple_sources` with email inputs).
- `pause` — if a human should review/approve before sending, place a `pause` step before the email step rather than abusing compose-only mode.
- `../reference/patterns/html_summary_dashboard.md` — the UI-only HTML dashboard pattern built on a compose `custom_step` + display-only email step.
