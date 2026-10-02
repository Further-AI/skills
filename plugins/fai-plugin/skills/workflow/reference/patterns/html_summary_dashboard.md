# Pattern: HTML Summary Dashboard (UI-only)

A workflow's final step is often a custom-rendered HTML "dashboard" the user views in the workflow UI. It is **display-only** — the actual outbound email (if any) is a separate `email` step. This separation lets you iterate on the dashboard without re-sending email and lets the dashboard render rich HTML the email client wouldn't handle.

## The two-step shape

```
[ all upstream extraction / analysis ]
  → Compose HTML Summary    (custom_step, display_step: false — returns {email_body: "<html>"})
  → Submission Summary      (email, display_step: true — UI-only render, no recipients)
```

## Step 1: Compose HTML Summary (custom_step)

Composes the full HTML by string concatenation. No template engines — keep it inline so it's editable in the builder UI. New custom code steps are always `custom_step` (sandboxed); `function` is legacy, edit-only.

```jsonc
{
  "name": "Compose HTML Summary",
  "type": "custom_step",
  "input_mappings": [
    { "input_parameter_name": "standardized_data",
      "input_type": "dependency",
      "value": { "dependency_step_outputs": [
                   { "step_name": "Standardize Extracted Data", "output_attribute": null }],
                 "resolution_operator": null, "resolution_config": null } },
    { "input_parameter_name": "guideline_results",
      "input_type": "dependency",
      "value": { "dependency_step_outputs": [
                   { "step_name": "Guideline Check", "output_attribute": "data" }],
                 "resolution_operator": null, "resolution_config": null } }
  ],
  "dependencies": [],
  "config": {
    "code": "async def run(standardized_data=None, guideline_results=None, **kwargs):\n    import html as html_module\n    def esc(v): return html_module.escape(str(v)) if v else ''\n    # ... build h string ...\n    h = '<!DOCTYPE html><html>...</html>'\n    return { 'email_body': h }",
    "kwargs": {},
    "input_schema": {
      "type": "object",
      "properties": {
        "standardized_data": { "type": "object" },
        "guideline_results": { "type": "array" }
      }
    },
    "output_schema": {
      "type": "object",
      "properties": { "email_body": { "type": "string" } }
    }
  },
  "display_step": false
}
```

**Hide it (`display_step: false`)** — only the email step shows in the UI.

## Step 2: Submission Summary (email — UI-only render)

```jsonc
{
  "name": "Submission Summary",
  "type": "email",
  "input_mappings": [
    { "input_parameter_name": "email_body",
      "input_type": "dependency",
      "value": { "dependency_step_outputs": [
                   { "step_name": "Compose HTML Summary", "output_attribute": "email_body" }],
                 "resolution_operator": null, "resolution_config": null } }
  ],
  "dependencies": [],
  "config": {
    "reply_to_original": false,
    "email_body": "",
    "use_compose_function": false
  },
  "output_config": { "view_output_button_text": "View Summary" }
}
```

- **No `to_email` / `from_email` / etc.** — empty routing makes the email UI-only. Nothing is actually sent.
- `reply_to_original: false` so the routing fields aren't auto-populated from incoming email.
- The email step's renderer treats this as a display panel.

## Development workflow

**Always create a standalone HTML preview file first**, then convert to step code:

1. Create `<workflow_dir>/<name>_dashboard_preview.html` with hardcoded sample data.
2. Open in Chrome (`open <file>`) for instant feedback.
3. Iterate on layout / sections with the user.
4. Once finalized, convert to the custom_step:
   - Replace hardcoded values with variable interpolation.
   - Move CSS into a single string concatenated into the HTML.
   - Add the `extract_value` / `get_field` helpers if reading extraction cells.
5. Validate the workflow.

This iteration loop is much faster than editing the workflow JSON each time.

## FurtherAI dashboard style guide

Use these shared design tokens for consistency across dashboards:

| Element | Value |
|---|---|
| Fonts (display) | `Fraunces` |
| Fonts (labels/caps) | `Instrument Sans` |
| Fonts (body) | `Wix Madefor Text` |
| Hero background | `radial-gradient(circle at 92% 14%, rgba(251,150,8,.18), rgba(251,150,8,0) 42%), linear-gradient(135deg, #0b0b12 0%, #14161c 58%, #074b40 100%)` |
| Cards | white bg, 10px border-radius, subtle box-shadow |
| Sections | collapsible `<details>` with chevron toggle icons |
| Status: Pass | `#059669` (green) |
| Status: Fail | `#dc2626` (red) |
| Status: Needs Info / Review | `#d97706` (amber) |
| Page background | `#f4f3f0` |
| Card background | `#ffffff` |
| Footer | centered, "FurtherAI · {Workflow Name}" with link to full execution |
| Badges | rounded pills, status-colored text on tinted backgrounds |
| Guideline checks | collapsible per-carrier sub-sections, Fail-first sort, More/Less buttons |
| Takeaway cards | 3-column (Strengths green, Watch Items red, Missing Info gray), top border accent |

A working reference of all of these tokens is in `reference/examples/submission_intake_es_umbrella.json` (the "Compose HTML Summary" step).

## Common mistakes

- **Putting recipients on the email step** — accidentally sends the dashboard via email. Leave routing fields empty.
- **Returning HTML in a key other than `email_body`** — the email step expects this exact key.
- **Forgetting `display_step: false` on the compose step** — clutters the UI with the raw HTML output.
- **Hardcoded execution dates / IDs** — in a `custom_step`, read run identity from `fai_sandbox`'s `context.execution_log()`; never hardcode dates.
- **Missing `input_schema`/`output_schema` on the custom_step** — both are required in `config` at save time.

## When you also want to send the email

If the same dashboard should be rendered AND sent to underwriters:

```
Compose HTML Summary (custom_step, display_step: false)
  → Submission Summary  (email, UI-only — display_step: true, no recipients)
  → Notify UW Team       (email, display_step: true, with to_email/from_email/etc.)
```

Two email steps. Both consume `email_body` from the same compose step. The "Notify UW Team" step has full routing config; "Submission Summary" has none.
