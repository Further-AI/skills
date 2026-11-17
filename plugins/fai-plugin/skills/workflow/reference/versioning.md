# Workflow Versioning & Deployment

How workflow versions, drafts, and publishing work.

---

## Versioning Model

Workflows use a **draft → publish** model:

1. **Draft** — A mutable working copy. Only one draft exists at a time per workflow.
2. **Published version** — An immutable snapshot. Each publish increments the version number. Published versions cannot be edited.
3. **Live version** — The most recently published version; what executes when the workflow is triggered.

Versions are sequential integers starting from 1. Any past version can be viewed, and an old version can be promoted back to live.

---

## Lifecycle (all operations via `/fai:wb`)

1. **Create** (`wb create`) — makes a never-published shell with an active draft; nothing is live yet. Creation sends only `name` and `steps`, so options (`file_access_config`, `feature_flags`, `enable_parallel_execution`, ...) are missing afterwards — **always follow a create with a full-JSON draft upload** (`wb upload`) before running anything; file uploads fail with a 500 until `file_access_config` is set.
2. **Iterate** (`wb upload`) — replaces the active draft with the full workflow JSON, options included. Never publishes.
3. **Test** (`wb execute --draft-version N`) — draft test run.
4. **Publish** (`wb publish`) — gated on a successful draft test run after the latest edit; `--allow-override` bypasses only that gate, publish-time validation always runs. The draft becomes the new live version.
5. **Roll back** (`wb publish --promote-version N`) — re-publishes an old version as the new live one.

---

## Workflow URLs

| Environment | App URL (`{APP}`) |
|---|---|
| **Prod** | `https://app.furtherai.com` |
| **Staging** | `https://app-staging.furtherai.com` |

| Page | URL Pattern |
|------|-------------|
| **Workflow page** | `{APP}/workflows/<workflow_id>` |
| **Single execution** | `{APP}/workflow-execution/<execution_id>` |
| **Eval dataset** | `{APP}/eval/<dataset_id>` |

---

## Key Config Flags

| Flag | Where | Notes |
|------|-------|-------|
| `use_normal_azure_function` | options | `false` for local execution |
| `enable_parallel_execution` | options | `true` for concurrent steps |
| `reducto_version` | options (global) | `v2` default; steps can override (e.g. loss runs → `v3`) |
| `feature_flags.use_temporal` | options | `{"enabled_for": ["all"]}` required on staging |
| `model` | step config | See [model_compatibility.md](model_compatibility.md) for current model + reasoning-effort guidance |

---

## Common Iteration Loop

```
1. Edit workflow JSON (workflow skill tooling)
2. Validate: python3 ${CLAUDE_PLUGIN_ROOT}/skills/workflow/scripts/validate_workflow.py <file.json>
3. Upload draft via /fai:wb upload
4. Draft test run on test documents
5. Check the execution for errors (/fai:triage for failures)
6. If errors → fix JSON → go to 1
7. If good → publish, run more submissions, or create an eval test batch
```
