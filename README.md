# FurtherAI Skills

This repository stores, validates, and packages skills for FurtherAI agents. A
skill is a folder of instructions and optional supporting files that an agent
uses to perform a task.

## Available skills

| Skill | Purpose |
| --- | --- |
| [Spreadsheet](skills/excel-generation/SKILL.md) | Build and check Excel workbooks from insurance documents and spreadsheets. |
| [Documents](skills/docx/SKILL.md) | Edit and inspect `.docx` documents with the `paper-docx` distribution. |
| [Presentation](skills/pptx/SKILL.md) | Edit and inspect `.pptx` decks with the `paper-pptx` distribution. |

The `docx` and `pptx` skills are vendored from
[paper-instruments/skills](https://github.com/paper-instruments/skills) (MIT —
each skill directory carries its LICENSE) and assume the matching `paper-*`
distributions are installed in the agent's environment. They document
`paper-docx==0.2.0` and `paper-pptx==0.2.0`.

## Product names

Keep `name` equal to the skill folder and use optional metadata for its UI label:

```yaml
name: pptx
metadata:
  furtherai-display-name: Presentation
```

The label changes how the skill appears in FurtherAI. Identifiers, permissions,
and saved selections still use `name`. Skills without a label keep their current
sentence-case name. Publishing preserves this metadata inside the skill bundle.

## Getting started

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
from the repository root:

```sh
uv sync --locked
uv run python scripts/skill_bundle.py skills/excel-generation --output dist/excel-generation.zip
```

This validates the skill and writes `dist/excel-generation.zip`. Omit
`--output` and its path to validate without creating a ZIP. The `dist/` directory
is gitignored.

The ZIP contains `SKILL.md` at its root and preserves resource paths, file contents,
and executable flags. Unchanged inputs produce identical ZIPs.

## Automatic publication

Merging to `main` publishes every skill to staging and production after tests,
type checks, and bundle validation pass. Each environment uploads the complete
catalog and activates it atomically. Evaluation scores do not block merges or
publication. The two publishing jobs run independently; a failure in one does
not cancel the other.

The catalog contains Documents, Spreadsheet, and Presentation. Every published
skill is enabled in the destination catalog, including newly added folders. There
is no separate availability list to update. Removing a folder retires that skill
from future use; historical bundles remain stored.
Failed uploads, stale commits, and catalog conflicts leave that environment's
active catalog unchanged.

Logfire's `flag_enable_furtherai_skills` remains the global off switch.
With backend support for catalog defaults deployed, published skills turn on
automatically for organizations and members with no saved setting. Explicit admin
disables and personal opt-outs remain in effect. UAA includes enabled skills in
its automatic roster; other agent surfaces keep their own selection rules.

Download each environment's `skill-bundles` artifact from
[Actions](https://github.com/Further-AI/skills/actions/workflows/validate.yml).
To package the complete catalog locally:

```sh
uv run python -m scripts.skill_catalog skills --output dist/release
```

## Configure publishing

Both backends must support `GET/PUT /api/v1/internal/skills/catalog`, including
`enabled_skills`, and have their FurtherAI ownership and Azure storage settings
configured.

Create these GitHub environments, restricted to `main`, and set `SKILLS_API_URL`
in each to its backend's HTTPS base URL without `/api/v1`:

| GitHub environment | Destination | Backend `SKILLS_PUBLISH_AUDIENCE` |
| --- | --- | --- |
| `us-staging` | US staging backend | `furtherai-skills-us-staging` |
| `us-production` | US production backend | `furtherai-skills-production` |

Set the repository variable `SKILLS_PUBLISH_ENABLED` to `true`. The publisher uses
GitHub OIDC; no GitHub App, Braintrust key, Azure credential, or long-lived
publishing token is required in this repository for publication.

Run **Validate and publish** manually on the current `main` commit to verify both
environments. Later merges publish automatically. Missing configuration fails
the affected job. A catalog conflict stops publication; inspect the competing
release before rerunning the current `main` commit.

## Evaluation

Run quality comparisons separately using the backend evaluation suite. This
repository does not trigger evaluations or require their results to publish.
Only `validate` is a required branch check.

## Adding a skill

Each skill follows the [Agent Skills format](https://agentskills.io/specification):
create `skills/<name>/SKILL.md` with YAML `name` and `description` between `---`
delimiters, followed by the instructions. The name must match the folder.
Use an existing skill as an example.

Optional `scripts/`, `references/`, `assets/`, and other resource files are included
recursively. The skill's root `tests/` and all `__pycache__/` directories are
excluded. Run the same
packaging command with your skill's path; CI discovers it automatically.

Packaging rejects symlinks, special files, and unsafe paths. Limits per skill:

| Item | Maximum |
| --- | --- |
| Individual file | 10 MiB |
| Total file contents and final ZIP (each) | 30 MiB |
| Included files | 1,024 |
| YAML frontmatter | 64 KiB |
| Name / description / compatibility | 64 / 1,024 / 500 characters |

File restrictions, size caps, and file-count limits match FurtherAI's backend
([bundle validation](https://github.com/Further-AI/fai-automation-backend/blob/769a1f66a1cd87ddef9cbb7020ed79462d0966ce/src/backend/skills/storage.py),
[frontmatter validation](https://github.com/Further-AI/fai-automation-backend/blob/769a1f66a1cd87ddef9cbb7020ed79462d0966ce/src/backend/skills/manifest.py)).
Metadata field lengths follow the [Agent Skills specification](https://agentskills.io/specification#frontmatter).

## Development

Bundle validation lives in [scripts/skill_bundle.py](scripts/skill_bundle.py), complete
packaging in [scripts/skill_catalog.py](scripts/skill_catalog.py), and publishing
lives in [scripts/publish_skill.py](scripts/publish_skill.py). Tests are in `tests/`.

```sh
uv run pytest -x --tb=short
uv run ty check scripts tests skills/excel-generation/scripts
```
