# hold

### What this step does

When the executor reaches a `hold` step, it:

1. Waits for every step in its dependency closure to finish.
2. **Passes its input through verbatim as its own output** — every `input_mapping` value lands in the step's result dict under its `input_parameter_name`. Downstream steps can wire off these passthrough fields (e.g. `<HoldStep>.markdown_content`).
3. Flips the execution status from `running` → `paused`.
4. Renders a "View Output" affordance on the hold card so the reviewer can read the passthrough content, and stops the workflow until a human resumes. **Do NOT set `output_type` or `output_config` on a hold step** — the builder doesn't expose them, the backend doesn't auto-fill them for hold, and setting them risks a save-time rejection. Use the step's top-level `view_output_button_text` for a custom button label.

**Hold vs pause — the key distinction:** `pause` outputs only `{"status": "paused" | "completed"}` and is a pure gate. `hold` outputs `{"data": <input passthrough>}` (plus any other wired input params) and is a gate that DOUBLES as a display surface. If a downstream step needs the held artifact, it can wire off the hold; with pause, it must wire off the original upstream.

### Required inputs

| Param  | Type     | Required | Source                                                                 |
| ------ | -------- | -------- | ---------------------------------------------------------------------- |
| `data` | `object` | YES      | Map a dependency step's output here via an `input_mappings` entry.     |

`data` is the one declared required slot. In practice, workflows often wire a second named input (e.g. `markdown_content`) so a text renderer's `content_key` can resolve directly off the step's result — see Pattern A. Both wirings are legal; the runtime spreads every `input_parameter_name` into the output dict.

### Config keys

None. `HoldConfig` is empty. When authoring a `type="hold"` step, pass `config={}`. Anything else is rejected at save.

Step-level fields you DO commonly set on a hold (these live on the step, not in `config`):

| Field                   | Notes                                                                                                                                                                                                          |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `start_title`           | Header rendered while the hold is still blocking (e.g. `"Checking for violations"`).                                                                                                                           |
| `end_title`             | Header rendered after the user resumes (e.g. `"Workflow blocked due to field violations"`).                                                                                                                    |
| `display_step`          | Keep `true` — a hidden hold defeats the purpose; the human has to see the card to review and resume.                                                                                                           |
| `enable_rerun`          | Set `true` if the user should be able to re-trigger the upstream branch from the hold card. Typically left `false`.                                                                                             |
| `incremental_config`    | Default `{"behavior": "rerun"}` is correct — on workflow re-run, the hold should re-hold so the human re-approves the latest artifact.                                                                          |
| `parent_conditions`     | Auto-populated at save time. Don't author by hand.                                                                                                                                                              |

### Output schema

```json
{
  "type": "object",
  "description": "Hold step output — passthrough of input data",
  "properties": {
    "data": {
      "type": "object",
      "description": "Passthrough data from the input dependency"
    }
  }
}
```

In practice the runtime spreads every `input_parameter_name` from `input_mappings` into the result, so a downstream step can wire off `<HoldStep>.markdown_content` (or whatever name you used) even though only `data` is declared in the static schema. Treat the declared `data` field as the canonical handle.

### Input wiring (input_mappings vs dependencies)

- **NEW steps**: use `input_mappings` only; `dependencies` always `[]`.
- **EXISTING steps**: preserve whatever pattern is already there.
- Migration `dependencies` → `input_mappings` on explicit request; the reverse is forbidden.

For hold specifically, you almost always want at least one `input_mappings` entry — the whole point of `hold` over `pause` is to render an upstream artifact, and the passthrough only happens for inputs that flow through `input_mappings`. Pure-`dependencies` wiring is legal but defeats the value: the card has nothing to display, and you should have used `pause` instead.

Wire the upstream artifact via `input_type: "dependency"` with `dependency_step_outputs` pointing at the producing step (`output_attribute` set to the specific field if the upstream output is a dict). Use `resolution_operator: null` for a single source; `concat_lists` only if fanning in multiples.

### Common patterns

The dominant real-world shape sits on a decision branch ("field validations failed → show the violation markdown → block for human correction").

#### Pattern A — block on a decision branch and surface markdown for review (the dominant shape)

A `hold` parented by a `decision` step's "violations found" branch. The held artifact is the markdown produced by the upstream validation step. Some instances wire both `markdown_content` and `data` to the same source, others wire only `markdown_content` — prefer the double-wire because `data` is the declared-required slot.

```json
{
  "name": "Validation Block",
  "type": "hold",
  "input_mappings": [
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Field Validations", "output_attribute": "markdown_content" }
        ],
        "resolution_operator": null, "resolution_config": null
      },
      "input_parameter_name": "markdown_content"
    },
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Field Validations", "output_attribute": "markdown_content" }
        ],
        "resolution_operator": null, "resolution_config": null
      },
      "input_parameter_name": "data"
    }
  ],
  "dependencies": [],
  "config": {},
  "start_title": "Checking for violations",
  "end_title": "Workflow blocked due to field violations",
  "display_step": true,
  "enable_rerun": false,
  "incremental_config": { "behavior": "rerun" },
  "parent_conditions": []
}
```

#### Pattern B — minimal: wire only `markdown_content`

Same shape as A but with only `markdown_content` wired (no separate `data` slot). It can pass save validation when `markdown_content` resolves to a non-null value, but Pattern A is more forward-compatible — recognize this shape when reading existing workflows, prefer A when authoring new ones.

#### Pattern C — hold rendering a table

```json
{
  "name": "Review Extracted SOV",
  "type": "hold",
  "input_mappings": [
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Extract SOV Rows", "output_attribute": null }
        ],
        "resolution_operator": null,
        "resolution_config": null
      },
      "input_parameter_name": "data"
    }
  ],
  "dependencies": [],
  "config": {},
  "display_step": true
}
```

### Common validation errors and fixes

- **`Step '<hold>' is missing required input 'data'`** — add an `input_mappings` entry with `input_parameter_name: "data"` pointing at the upstream artifact. If you've already wired `markdown_content` only, either rename it to `data` or add a second entry (Pattern A).
- **`Step '<hold>' has unknown config keys: [...]`** — `HoldConfig` is empty; any key in `config` is rejected. Move display-shaping fields (`start_title`, `end_title`, `display_step`, `enable_rerun`) to the step's top level, not `config`.
- **`parent_conditions reference unknown decision step`** — don't hand-author `parent_conditions`. Leave `[]`; save-time compilation recomputes it from the dependency graph.

### Common gotchas

- **Hold vs pause — the central decision.** `pause` output is opaque (`{"status": ...}`); `hold` output is a passthrough of its input. Pick `hold` only when (a) the reviewer needs to SEE the upstream artifact rendered on the card AND/OR (b) a downstream step needs to wire off the held artifact via `<HoldStep>.<field>`. Otherwise use `pause`.
- **Hold is not a form.** It collects nothing. Inline edits in an editable text renderer don't flow back through `input_mappings`. If downstream needs the corrected text, use `manual_input` with a text field instead.
- **Downstream must read the passthrough, not `.status`.** A `decision` or `custom_step` downstream of a hold reads `<HoldStep>.data` (or the named passthrough field). Hold doesn't emit `status`.
- **Re-run behavior.** `incremental_config.behavior = "rerun"` re-holds on workflow re-run so the human re-approves the latest artifact — almost always the right default.
- **Rare step.** Existing usage sits on decision branches with markdown display. When in doubt, default to `pause` and only reach for `hold` if you can articulate "the card NEEDS to render upstream X for the reviewer."

### See also

- [pause.md](pause.md) — sibling. Use when the review card does NOT need to display the upstream artifact — pause is opaque and cheaper.
- [manual_input.md](manual_input.md) — use when the human must FILL a form, not just review-and-resume. `manual_input` produces named output fields per its `input_schema`; `hold` only re-emits what was wired in.
- [decision.md](decision.md) — `hold` typically sits on a decision branch; the decision routes only the "needs review" branch into the hold.
