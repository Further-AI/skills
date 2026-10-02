# Workflow `options` Block & Custom `columns`

The workflow-level `options` field (`WorkflowOptionsV1`) controls cross-cutting workflow behavior: file access, webhooks, parallel execution, ask-AI, incremental config, and more. The top-level `columns` field configures custom columns shown on the workflow execution list.

This file is for the workflow-level config. For per-step settings, see each `step_types/<type>.md`.

---

## Full `options` shape

```jsonc
{
  "options": {
    "assistant_email_id":      "string | null",
    "zapier_reply_url":        "string | null",
    "allowed_email_domains":   ["acme.com", ...],
    "retrigger_on_thread":     true,
    "force_background_tasks":  false,
    "use_normal_azure_function": false,
    "settings":                null,
    "dropdown_config":         null,
    "workflow_title_question": "What is the insured name?",

    "api_response_steps": {
      "steps": [{ "step_name": "Extract Tower Data" }, ...]   // null = include all compatible
    },

    "file_access_config": {
      "type": "multipart | public_urls | oauth2 | box",
      "box":    { /* BoxConfig */ },
      "oauth2": { /* OAuth2Config */ }
    },

    "webhook_config": {
      "completion_webhook_urls": ["https://..."],
      "auth_type": "none | oauth2 | api_key",
      "oauth2":    { /* WebhookOAuth2Config */ },
      "api_key":   { "api_key": "ENV_VAR_NAME", "api_key_header": "x-api-key" },
      "response_format": "url | inline",
      "inline_max_size_bytes": 1000000
    },

    "enable_knowledge_sources":  false,
    "enable_parallel_execution": true,         // run independent steps in parallel
    "enable_submission_merging": false,
    "enable_agent_step_context": false,
    "enable_related_queries":    false,

    "ask_ai_config": {
      "enable_reasoning":  false,
      "reasoning_model":   "gpt-5.1",
      "reasoning_effort":  "medium",
      "reasoning_summary": "concise"
    },

    "reducto_version": "v2",                   // "v2" | "v3"

    "incremental_config": {
      "add_documents":      { "behavior": "overwrite | timeline" },
      "submission_merging": { "behavior": "timeline | overwrite" }
    },

    "hide_output_button_if_empty": false,
    "enable_downloads":            null,       // null = enabled (default)
    "feature_flags":               null,

    "control_plane_mappings": [                // org secrets this workflow declares
      { "key": "acme_vendor_password", "stickiness": "latest" }
    ]
  }
}
```

## Field-by-field

### Email & dispatch
- **`assistant_email_id`** — the workflow's dedicated assistant email ID; submissions arriving at this address are routed here.
- **`zapier_reply_url`** — global webhook URL for `email` steps (each email step can override).
- **`allowed_email_domains`** — gate which sender domains can trigger this workflow.
- **`retrigger_on_thread`** — re-run the workflow if a new email lands in an existing thread.

### Workflow title
- **`workflow_title_question`** — natural-language question; the engine runs an LLM Q&A against the docs to populate the execution title (e.g., `"What is the insured name?"`).

### API & webhooks
- **`api_response_steps`** — filter which steps appear in the GET-execution API response. `null` → include all compatible steps (`output_type` is api-renderable).
- **`file_access_config.type`** — how files arrive:
  - `multipart`: file upload via API multipart form
  - `public_urls`: caller passes URLs the engine fetches
  - `oauth2`: OAuth2-protected URLs (set `oauth2` sub-config)
  - `box`: Box shared links (set `box` sub-config)
- **`webhook_config`** — completion webhooks. Choose `auth_type` and provide either `oauth2` or `api_key` sub-config. `response_format: "inline"` embeds step data directly (capped at `inline_max_size_bytes`); `"url"` sends SAS URLs to blob storage.

### Execution behavior
- **`enable_parallel_execution`** — `true` runs independent DAG branches concurrently. **Default to `true` for production workflows.**
- **`enable_knowledge_sources`** — opt into knowledge-source documents (separate doc upload mechanism).
- **`enable_submission_merging`** — opt into the merge-executions API.
- **`enable_agent_step_context`** — feed completed-step output as context to subsequent agent steps.
- **`enable_related_queries`** — generate follow-up queries after workflow completion.

### Reducto + Ask AI
- **`reducto_version`** — `"v2"` (default, cheaper) or `"v3"` (deeper extraction; use sparingly — significantly more expensive). Step-level overrides exist.
- **`ask_ai_config`** — controls the `/ask-ai/stream` endpoint behavior for this workflow's UI.

### Incremental processing
- **`incremental_config.add_documents.behavior`** — what happens when documents are added to a completed execution: `"overwrite"` (default — replaces step results) or `"timeline"` (keeps history as a timeline).
- **`incremental_config.submission_merging.behavior`** — same axis for execution-merge operations: `"timeline"` (default) or `"overwrite"`.

This is the **workflow-level** counterpart to per-step `incremental_config.behavior`. For per-step rules and step-type compatibility, see `validation_errors.md`.

### UI
- **`hide_output_button_if_empty`** — hide the "View Output" button on steps whose data is empty.
- **`enable_downloads`** — `null` or `true` to enable file downloads in the UI; `false` to disable.

### Secrets — `control_plane_mappings`
- Declares which org-control secrets this workflow's code steps may resolve. `secrets.get("key")` in a `custom_step` (and `secret()` in a legacy `function` step) only resolves keys declared here — an undeclared key fails at runtime even when the credential exists.
- **Workflow-authored keys are org-slug-prefixed**: `<org>_<vendor>_<what>`, e.g. `acme_cotality_password` — never a bare `cotality_password` — so the owning org is identifiable from the key alone.
- **Fixed managed integration keys** (`riskmeter_client_id`, `sharepoint_tenant_id`, `integration_pitchbook_api_key`, `imageright_credentials`, …) are looked up by the integration blocks by exact name — never prefix or rename those. Most managed integrations resolve from the org integration directly and need no mapping here; `pitchbook` is the exception (its keys must be declared — see `step_types/integrations.md`).
- `stickiness: "latest"` is the normal value. Secrets are sensitive: never log them or return them from step code.

### Misc
- **`force_background_tasks`** — force long-running steps onto the background-task queue.
- **`use_normal_azure_function`** — environment routing flag (rarely set).
- **`settings`**, **`dropdown_config`** — free-form custom configs.
- **`feature_flags`** — `FeatureFlags` object for gated features per owner.

---

## Sub-config shapes

### `BoxConfig`
```jsonc
{ "subject_id": "...",
  "client_id": "...",
  "client_secret": "ENV_VAR_NAME",
  "auth_token_url": "https://..." }
```

### `OAuth2Config` (for file access)
```jsonc
{ "auth_url": "https://oauth-provider.com/token",
  "client_id": "...",
  "client_secret": "ENV_VAR_NAME",
  "scope": "files.read" }
```

### `WebhookOAuth2Config`
Same shape as `OAuth2Config`.

### `WebhookAPIKeyConfig`
```jsonc
{ "api_key": "ENV_VAR_NAME_FOR_API_KEY",
  "api_key_header": "x-api-key" }
```

### `AskAiConfig`
```jsonc
{ "enable_reasoning": false,
  "reasoning_model":  "gpt-5.1 | gpt-5.2 | gpt-5.4 | claude-sonnet-4-6 | claude-opus-4-6",
  "reasoning_effort": "none | low | medium | high | xhigh",
  "reasoning_summary":"auto | concise | detailed" }
```

---

## Top-level `columns` — custom execution-list columns

Every execution shows up as a row in the workflow-runs list. `columns` defines custom columns shown there, with values populated from step outputs (or a static / variable / prompt / function / dependency source).

```jsonc
{
  "columns": [
    {
      "name":           "triage_score",
      "label":          "Triage Score",
      "data_type":      "string | money | number | date | datetime | enum",
      "enum_options":   [{"id":"pending","name":"Pending","value":"pending"}],
      "enum_colors":    { "pending": "#ffcc00" },
      "value_source":   { /* WorkflowField — see input_mappings.md */ },
      "default_visible": true,
      "source":         "code | builder | user",
      "description":    "Human-readable description"
    }
  ]
}
```

### Field semantics

| Field | Effect |
|---|---|
| `name` | Internal identifier |
| `label` | Display label in the list |
| `data_type` | How values are formatted (currency, dates, dropdowns) |
| `enum_options` / `enum_colors` | Required when `data_type: "enum"` |
| `value_source` | A `WorkflowField` (`{input_type, value}`) — same shape used in input mappings. Most common: `dependency` pointing at a step output. |
| `default_visible` | Initial visibility in the execution list |
| `source` | `"code"` (existing code-managed), `"builder"` (configured here), `"user"` (added by user from history page) |

### Common patterns

```jsonc
// Pull from a step's output
{ "name": "effective_date",
  "label": "Effective Date",
  "data_type": "date",
  "value_source": {
    "input_type": "dependency",
    "value": {
      "dependency_step_outputs": [
        { "step_name": "Extract Account Data", "output_attribute": "data.effective_date" }
      ],
      "resolution_operator": null,
      "resolution_config": null
    }
  } }

// Static value (rare)
{ "name": "carrier",
  "label": "Carrier",
  "data_type": "string",
  "value_source": { "input_type": "static", "value": "Acme Insurance" } }

// AI-prompted
{ "name": "summary_status",
  "label": "Status",
  "data_type": "enum",
  "enum_options": [{"id":"green","name":"Clean","value":"clean"}, {"id":"red","name":"Issues","value":"issues"}],
  "value_source": { "input_type": "prompt",
                    "value": "Return 'clean' if no guideline failures, otherwise 'issues'." } }
```

For full `WorkflowField` semantics, see `input_mappings.md`.
