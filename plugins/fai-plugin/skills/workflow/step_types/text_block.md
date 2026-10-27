# `text_block`

**Render a markdown block in the workflow UI. Pure display — no processing.**

## When to use it

- Show a markdown summary wired from a function step's output.
- Display web_search results directly (its output already is `markdown_content`).
- Any time you need a dedicated markdown viewer separate from other step types.

## Config

No `*Config` model. Content comes purely from input mappings.

## Inputs

| Name | Required | Source |
|---|---|---|
| `markdown_content` | yes | Any step that returns a `markdown_content` field or a custom code step returning `{markdown_content: "..."}` |

## Outputs

| Key | Shape |
|---|---|
| `markdown_content` | `str` |
| `view_output_button_text` | `str` |
| `editable` | `bool` |

## Defaults

- `output_type`: `text` with `TextOutputConfig(content_key: "markdown_content", editable: true)`
- Incremental: `rerun` / `no_op` only.

## Best practices

- Simplest content rendering: wire `markdown_content` from a prior step and leave defaults.
- Override `view_output_button_text` in `output_config` for custom button labels.
- `editable: false` in `TextOutputConfig` if you don't want user edits.

## Common gotchas

- Forgetting to wire `markdown_content` → empty text block.
- Using for HTML content — wrong step type. Use a `custom_step` + `output_type: "html"` instead.
- Rarely used; the builder UI may not offer it. If a platform save rejects the step type, substitute a `custom_step` that returns `{markdown_content: "..."}` with `output_type: "text"`.

## Minimal example

```json
{
  "name": "Research Summary",
  "type": "text_block",
  "input_mappings": [
    { "input_parameter_name": "markdown_content",
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "News Check — Insured", "output_attribute": "markdown_content" }
        ],
        "resolution_operator": null, "resolution_config": null
      } }
  ],
  "dependencies": [],
  "config": {}
}
```
