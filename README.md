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

PR checks run tests and type checks. The backend packages and evaluates only
changed skills with evaluation profiles, then updates the PR comment with scores
and Braintrust links. Shared changes evaluate all profiled skills present in the
candidate. Disabled skills without profiles are listed as skipped.

Releases compare against the active staging catalog so changes from failed
releases are checked again. After `validate` and `evaluate` pass, publication
packages the complete repository, uploads every bundle, and activates the catalog
atomically. Full packaging belongs to publication, not PR checks.

Download `skill-bundles` from the publication job in
[Actions](https://github.com/Further-AI/skills/actions/workflows/validate.yml)
for the complete catalog and its ZIPs. Failed uploads, stale commits, and conflicting
releases leave the active catalog unchanged. Removing a skill retires its saved
pins; historical bundles remain stored.

### Choose which skills are available

Edit [`availability.yaml`](availability.yaml) to enable skills independently:

```yaml
staging:
  enabled_skills: []
production:
  enabled_skills: []
```

Add a skill's directory name to the desired list once it exists in `skills/`.
For example, add `excel-generation` to staging after PR #13 lands. Empty lists
expose no FurtherAI skills. Unknown names and duplicate entries fail validation.

The publisher saves the selected environment's list with the catalog. Disabled
skills are hidden from the picker, blocked for saved selections and downloads,
and removed from cached sandboxes before the next agent turn. Disabling does not
delete bundles or interrupt a running turn.

Logfire's `flag_enable_furtherai_skills` remains the global off switch.
Organization permissions and personal opt-outs still apply. Custom skills are
unaffected. Older catalogs retain their existing behavior until republished.

The workflow publishes **staging only**, using `--environment staging`.
A production release must publish to the production backend with
`--environment production`; changing the production list alone does not deploy it.

### Evaluation requirements

Backend profiles define each skill's dataset, repetitions, and required scores.
Profiles select CI coverage independently of `availability.yaml`. Enabling a skill
in either environment requires a profile; disabled skills without profiles do not
block publication. Incomplete required assessments, evaluation errors, and cleanup
failures still block publication.

| Current profile | Required | Advisory |
| --- | --- | --- |
| `excel-generation` | File validity | Completeness, functional correctness |

The runner currently checks XLSX outputs. New formats need an inspector and a
profile. Paper `xlsx` has no CI profile. The first release evaluates all profiled
skills present in the candidate.

To package the bundles and availability settings locally:

```sh
uv run python -m scripts.skill_catalog skills --output dist/release
```

## Configure the backend evaluation gate

Deploy the evaluation workflows to both repositories' `main` branches, then set:

| Repository | Setting | Value |
| --- | --- | --- |
| Skills | `SKILLS_EVAL_APP_ID` | App installed on the backend with Actions read/write and Checks read |
| Skills | `SKILLS_EVAL_APP_PRIVATE_KEY` (secret) | That App's private key |
| Backend | `SKILLS_EVAL_JUDGE_TEMPLATE` | Staging judge image |
| Backend | `SKILLS_EVAL_BRAINTRUST_API_KEY` (secret) | Access to evaluation datasets and project |

For an existing App, approve the added Checks permission on its backend
installation before running evaluation. `gh run watch` requires this access.

The backend needs its staging evaluation services. Missing configuration,
failures, and timeouts block publishing.

PR evaluation uses trusted `main` workflow code and runs for branches in this
repository; forks require a maintainer-run evaluation. PR comments show scores
and experiment links. Documents, workbooks, and full reports stay private.

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
