# `web_agent`

**Runs one prompt-driven browser task in a fresh recorded Browserbase session. While it runs, the step row carries a live browser view; the completed output is either a structured table or the run recording.**

## Contract

- Exactly **one** `input_mappings` entry, named `prompt`. `input_type` must be `static` or `dependency`; a static prompt must be a non-empty string.
- **Credentialed sites are not supported yet** — a secret reference (`%name%`) in the prompt is rejected at validation. Author credential-free tasks.
- **Two output modes, selected by whether `config.output_schema` is set** (there is no separate mode field; `output_type` is derived from it at save time — leave `output_type: null` when authoring):
  - **Schema set (non-empty object schema) → table.** Describe the structured business result: nested objects, arrays, dates, string/numeric enums are supported; `file`/`video_file`/object-valued-enum fields are not. Renders as a grid when the schema is a single array-of-objects field, else key-value. No run recording is produced in this mode.
  - **Schema omitted → recording only (video).** For action tasks (e.g. push data into a portal) with no structured result; renders the run recording via document-viewer.

## Config

- `output_schema` — optional; selects the output mode (above).
- `model` — optional; an allowlisted Stagehand model: `anthropic/claude-haiku-4-5` (default), `anthropic/claude-opus-4-8`, `anthropic/claude-sonnet-4-6`, `openai/gpt-5.4`, `openai/gpt-5.4-mini`, `openai/gpt-5.5`. Execution limits are backend-owned.

## Output and downstream mapping

- **Table mode** → a TableV1 envelope `{schema, data, metadata, user_documents}`. Map business fields off **`data`**, like any table step: key-value schema → `data.<field>`; grid (single array-of-objects) schema → `data.0.<column>`. **Do NOT map `result.<field>`** — table mode has no top-level `result`. Session evidence (`session_id`, `recording_url`, `message`, `usage`) lives under `metadata` (e.g. `metadata.session_id`).
- **Video mode** → the recording via document-viewer; `session_id` / `recording_url` / `message` are top-level.
- **Failure** → sanitized `error`, `failure_phase`, `failure_kind`, and session evidence (+ best-effort recording in video mode). Downstream steps that branch on success should handle both shapes.

## Boundaries

- One browser attempt per step. The block does not retry the whole task or pause/resume for CAPTCHA, MFA, or human approval — put those policies in the surrounding workflow (decision/pause steps after verification or reconciliation).
- Treat recordings as sensitive: they may contain page data.
