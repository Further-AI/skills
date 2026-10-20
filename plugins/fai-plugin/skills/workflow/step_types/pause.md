# pause

### What this step does

When the executor reaches a `pause` step, it:

1. Waits for every step in its dependency closure to finish (the dependency graph applies normally).
2. Flips the execution status from `running` → `paused`.
3. Persists the step's own output as `{"status": "paused"}` and stops the workflow at this node.
4. Surfaces a "Resume" affordance in the execution UI. When a user clicks it, the step's output flips to `{"status": "completed"}`, the execution goes back to `running`, and downstream steps unblock.

`pause` has **no business logic**: its config is empty, it does not transform data, and downstream steps cannot read any field off it other than `status`. If a downstream step needs the actual reviewed data, it must depend on the *upstream* step the human was reviewing — not on the pause.

### Required inputs

No required input parameters in the conventional sense. A single optional `steps_before_pause` slot exists whose value is never read at runtime — it exists only so the UI can render the wiring panel and so the dependency graph picks up which upstream steps must complete before the pause fires.

| Param                | Type  | Required | Source                                                                                                |
| -------------------- | ----- | -------- | ------------------------------------------------------------------------------------------------------ |
| `steps_before_pause` | `any` | NO       | Map upstream steps via `input_mappings` using `concat_lists` — OR list them inline in `dependencies`. |

The schema's `type` is deliberately `"any"` (not `"array"`) — single-step wirings whose upstream output is an object were tripping the type-compat check when this was an `array`. Any source shape is legal here; the runtime ignores the value.

### Config keys

None. `PauseConfig` is empty by design. When authoring a `type="pause"` step, pass `config={}`. Anything else is rejected at save.

Step-level fields you DO commonly set on a pause (on the step, not in `config`):

| Field                   | Notes                                                                                                                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `start_title`           | Optional header rendered above the pause card while it is still `paused` (e.g. `"Paused for handler review"`). Defaults to the step name.                              |
| `end_title`             | Optional header rendered after the user resumes (e.g. `"Reviewed — outputs approved"`).                                                                                |
| `display_step`          | Keep `true` — a hidden pause is not useful; the human has to see it to resume.                                                                                          |
| `enable_rerun`          | Set `true` if you want the user to be able to re-trigger the upstream branch before resuming. Most pauses leave this `false`.                                            |
| `resume_button_text`    | Optional custom label for the "Resume" button (e.g. `"Approve & Continue"`). Falls back to the default UI label when unset. Only valid on `pause` steps — rejected on any other step type. |
| `incremental_config`    | Default `{"behavior": "rerun"}` is correct — on re-run, the pause should re-pause so the human re-approves.                                                              |
| `parent_conditions`     | Auto-populated at save time. Don't author by hand.                                                                                                                      |

### Output schema

```json
{
  "type": "object",
  "properties": {
    "status": { "type": "string", "description": "Pause step status: 'paused' or 'completed'" }
  }
}
```

That's the whole contract. Downstream steps wiring off a pause can only read `status` — and there is almost never a reason to. Pause is a gate, not a data source. If you find yourself wiring `<PauseStep>.status` into a decision, you probably want to gate via the upstream review step instead.

### Input wiring (input_mappings vs dependencies)

- **NEW steps**: use `input_mappings` only.
- **EXISTING steps**: preserve whatever pattern is already there — don't churn a stable workflow.
- Migration `dependencies` → `input_mappings` on explicit request; the reverse is forbidden.

For pause specifically, the wiring serves only to anchor the step in the dependency graph. Either:

- Add a single `input_mappings` entry named `steps_before_pause` with `input_type: "dependency"` and `dependency_step_outputs` listing every upstream step the human is reviewing — use `resolution_operator: "concat_lists"` when there is more than one — OR
- Leave `input_mappings: []` and list the upstream steps directly in `dependencies: [{"step_name": "<upstream>", "field_selector": null}, ...]`. This is what most existing workflows look like and is fine to preserve.

Do NOT wire a pause to its own siblings or to a decision's `branches_to_execute`. Pause should sit on the *output* of the step being reviewed.

### Common patterns

#### Pattern A — single-upstream review gate (legacy dependency wiring)

A pause after a single extraction/selection step so the human eyeballs the result before downstream automation fires. The dominant existing shape — used in AMS-write workflows, service-team verification, customer/policy selection, etc.

```json
{
  "name": "Verify Service Team",
  "type": "pause",
  "input_mappings": [],
  "dependencies": [
    { "step_name": "Extract Service Team Fields", "field_selector": null }
  ],
  "config": {},
  "display_step": true,
  "enable_rerun": false,
  "incremental_config": { "behavior": "rerun" }
}
```

#### Pattern B — review gate inside a decision-branch tree

A pause that sits on a branch of an upstream decision. The save-time compiler stamps `parent_conditions` so the pause skips cleanly when its branch isn't selected — authors don't write them.

```json
{
  "name": "AMS360: Verify Customer Selection",
  "type": "pause",
  "input_mappings": [],
  "dependencies": [
    { "step_name": "AMS360: Select Customer", "field_selector": null }
  ],
  "config": {},
  "start_title": "AMS360: Review Customer Selection",
  "display_step": true,
  "enable_rerun": true,
  "incremental_config": { "behavior": "rerun" }
}
```

#### Pattern C — fan-in review after multiple upstream steps (modern input_mappings)

A pause that waits for several upstream artifacts (e.g. a dashboard plus an OFAC check) before the human reviews everything in one card. Use `concat_lists` so the type-compat check tolerates a mix of array/object upstreams.

```json
{
  "name": "Pause for Handler Review",
  "type": "pause",
  "input_mappings": [
    {
      "input_type": "dependency",
      "value": {
        "dependency_step_outputs": [
          { "step_name": "Intake Triage Dashboard", "output_attribute": null }
        ],
        "resolution_operator": null,
        "resolution_config": null
      },
      "input_parameter_name": "stepsBeforePause"
    }
  ],
  "dependencies": [],
  "config": {},
  "start_title": "Paused for handler review",
  "end_title": "Reviewed — outputs approved"
}
```

The `input_parameter_name` is conventionally `steps_before_pause` (matching the schema) or `stepsBeforePause` — both appear in the wild and both pass validation because the runtime never reads the value.

### Common validation errors and fixes

- **`Step '<pause>' has unknown config keys: [...]`** — `PauseConfig` is empty; any key in `config` is rejected. Move the field to the step level or delete it.
- **`Parameter 'steps_before_pause' expects type 'array' but step '<X>' outputs type 'object'`** — happens on older platform versions where the slot was typed `array`. Wrap the single upstream in `resolution_operator: "concat_lists"` so it resolves to a list.
- **Pause with neither input_mappings nor dependencies** — executes immediately at the start of the workflow, which is almost never what you want. Anchor it to the step you're reviewing.
- **`parent_conditions reference unknown decision step`** — don't hand-author `parent_conditions`; leave `[]`.

### Common gotchas

- **Pause is not a data step.** Wiring a downstream step to `<Pause>.status` is almost always a bug — the only values are `"paused"` and `"completed"`, and after resume every downstream step runs regardless. To gate downstream work on "user pressed Reject", use a `manual_input` (collect a boolean) feeding a `decision`, not a `pause`.
- **Pause does NOT pass its input through.** Unlike `hold`, the pause's output is just `{"status": ...}`. Downstream steps that need the reviewed artifact must depend on the original upstream step; the dependency graph orders things correctly as long as their closure includes both the upstream and the pause.
- **Re-run behavior.** `behavior: "rerun"` re-pauses on workflow re-run — a previously-approved pause should NOT auto-pass because the underlying data may have changed.
- **No auto-resume / SLA timeouts.** A pause sits indefinitely until a human resumes (or the execution is cancelled). Timeouts are a workflow-level concern, not pause config.
- **Multiple pauses in series** is legal but rarely useful. Prefer a single pause covering the full review surface, or pause → manual_input → pause if you actually need a form between them.
- **Pause inside a fan-out.** When a decision fans out to N branches each ending in its own pause, the workflow stays `paused` until ALL active pauses are resumed. Resume order does not matter.

### See also

- [hold.md](hold.md) — use when the review card should **show** the upstream artifact (hold passes input through as output). Pause is opaque; hold is transparent.
- [manual_input.md](manual_input.md) — use when the human must fill a form, not just review-and-resume.
- [decision.md](decision.md) — pair with `manual_input` (not `pause`) to branch on the user's answer. Decision reads variables; pause has none.
