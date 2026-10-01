# FurtherAI Skills

This repository stores, validates, and packages skills for FurtherAI agents. A
skill is a folder of instructions and optional supporting files that an agent
uses to perform a task.

## Available skills

| Skill | Purpose |
| --- | --- |
| [Document extraction](skills/document-extraction/SKILL.md) | Extract structured fields from insurance documents. |
| [Policy comparison](skills/policy-comparison/SKILL.md) | Compare policies, quotes, and renewal changes. |
| [Loss-run analysis](skills/loss-run-analysis/SKILL.md) | Summarize claims history, losses, and trends. |
| [Submission intake](skills/submission-intake/SKILL.md) | Summarize submission documents, missing information, and risk flags. |
| [Coverage advisory](skills/coverage-advisory/SKILL.md) | Explain coverage, policy terms, and insurance requirements. |
| [docx](skills/docx/SKILL.md) | Edit and inspect `.docx` documents with the `paper-docx` distribution. |
| [pptx](skills/pptx/SKILL.md) | Edit and inspect `.pptx` decks with the `paper-pptx` distribution. |

The `docx` and `pptx` skills are vendored from
[paper-instruments/skills](https://github.com/paper-instruments/skills) (MIT —
each skill directory carries its LICENSE) and assume the matching `paper-*`
distributions are installed in the agent's environment. They document
`paper-docx==0.2.0` and `paper-pptx==0.2.0`.

## Getting started

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
from the repository root:

```sh
uv sync --locked
uv run python scripts/skill_bundle.py skills/document-extraction --output dist/document-extraction.zip
```

This validates the skill and writes `dist/document-extraction.zip`. Omit
`--output` and its path to validate without creating a ZIP. The `dist/` directory
is gitignored.

The ZIP contains `SKILL.md` at its root and preserves resource paths, file contents,
and executable flags. Unchanged inputs produce identical ZIPs.

## Where bundles go

On pull requests and pushes to `main`, CI runs tests, checks types, and packages
every directory under `skills/`. Open a completed run in
[Actions](https://github.com/Further-AI/furtherai-skills/actions/workflows/validate.yml)
and download `skill-bundles` under **Artifacts** to get one ZIP per skill and a
`catalog.json` listing the complete repository snapshot.

When publishing is enabled, a successful run on the current `main` commit also:

1. Authenticates with GitHub OIDC and reads the current backend catalog revision.
2. Uploads every validated ZIP to US staging as an immutable Azure bundle.
3. Activates the complete name-to-digest mapping in one conditional catalog write.

Additions, updates, and deletions take effect together. A missing artifact or failed
upload leaves the previous catalog unchanged. The artifact manifest distinguishes
an intentionally empty repository from an incomplete download; an empty manifest
retires all FurtherAI skills. Releases run one at a time, and stale commits or
conflicting catalog revisions fail without retrying activation.

Existing pins keep their exact versions while the skill remains active. Removing
a skill blocks future retrieval and resolution of that name, including saved pins;
already staged content is not recalled. Historical bundles remain stored.
Agent defaults and common skill selection stay in the backend. Publishing does not
change rollout flags or organization/member preferences. These checks validate
packaging; they do not evaluate task quality.

To package a complete local artifact, use a fresh output directory:

```sh
uv run python -m scripts.skill_catalog skills --output dist/release
```

## Enable staging publishing

Deploy the backend with `GET/PUT /api/v1/internal/skills/catalog` before enabling
this publisher. An older backend rejects the new flow before any upload. Then:

- Create a GitHub environment named `us-staging`, restricted to `main`.
- In that environment, set `SKILLS_API_URL` to the backend's HTTPS base URL,
  without `/api/v1`. The publisher calls `/api/v1/internal/skills`.
- Configure the backend's `SKILLS_PUBLISH_AUDIENCE` as `furtherai-skills-us-staging`
  and its FurtherAI ownership and Azure storage settings.
- Set the **repository variable** `SKILLS_PUBLISH_ENABLED` to `true`.

Run **Validate and publish** manually on the current `main` commit to publish the
skills. Later merges publish automatically. No Azure credentials or long-lived
publishing token are needed in this repository. A catalog conflict stops the run;
check the competing release before rerunning the current `main` commit.

## Adding a skill

Each skill follows the [Agent Skills format](https://agentskills.io/specification):
create `skills/<name>/SKILL.md` with YAML `name` and `description` between `---`
delimiters, followed by the instructions. The name must match the folder.
Use an existing skill as an example.

Optional `scripts/`, `references/`, `assets/`, and other resource files are included
recursively. Only the skill's root `tests/` directory is excluded. Run the same
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
uv run ty check scripts tests
```
