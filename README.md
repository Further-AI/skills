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

Each PR commit runs our `excel-generation` evaluation and updates one PR comment
with its experiment link, scores, and assessment counts. Merges and manual main
runs evaluate the exact main commit again before publication. Publishing waits for both `validate`
and `evaluate` to succeed. When publishing is enabled, the current `main` commit then:

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
change rollout flags or organization/member preferences. Repository validation checks packaging. Backend evaluation uses the existing
Assistant and LLM judge for Excel generation: file validity and completed,
error-free evaluation block publication; completeness and functional correctness
are advisory. Only our `excel-generation` skill is evaluated. Other
skill types are not quality-certified by this check.

To package a complete local artifact, use a fresh output directory:

```sh
uv run python -m scripts.skill_catalog skills --output dist/release
```

## Configure the backend evaluation gate

Deploy `.github/workflows/skills-evaluation.yml` and its evaluator to backend
`main` before merging this workflow change. In this repository, configure:

- Variable `SKILLS_EVAL_APP_ID`: a GitHub App installed on
  `Further-AI/fai-automation-backend` with Actions read/write permission.
- Secret `SKILLS_EVAL_APP_PRIVATE_KEY`: that App's private key.

In the backend, set `SKILLS_EVAL_JUDGE_TEMPLATE` to a staging judge image and
`SKILLS_EVAL_BRAINTRUST_API_KEY` to a credential with access to the pinned
datasets and evaluation project. The existing staging evaluation services must
be available.
Missing configuration, failed evaluation, or a timeout blocks publishing.
The PR comment includes aggregate scores and the Braintrust experiment link;
source documents, workbooks, and detailed reports stay in the private backend.

PR evaluation runs on opening and each new commit. Its trigger and comment code
come from trusted main, never the candidate branch; these workflows must land on
main before they become active. Automatic PR runs cover branches in this repository;
forks need a maintainer-controlled evaluation. Existing required checks remain
unchanged. Failed post-merge evaluation leaves the active catalog unchanged.

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
