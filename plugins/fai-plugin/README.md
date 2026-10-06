# fai: the FurtherAI platform plugin

Operate the FurtherAI platform from Codex or Claude Code: build, test, publish,
and investigate workflows; run submissions; manage Eval Studio Test Batches;
triage errors; resolve Linear tickets; and produce branded decks, documents,
screenshots, and accuracy reports. A local account database keeps organization,
workflow, dataset, Linear, Slack, and Notion identifiers together.

Works for GTM, Engagement Managers, Forward Deployed Engineers, and engineers.
Core platform operations need Python 3.9+ and no repository or database access.
Artifact creation and Excel imports have additional dependencies listed in
[`DEPENDENCIES.md`](DEPENDENCIES.md).

## Install

### Claude Code

```
/plugin marketplace add <this-repo-url-or-path>
/plugin install fai@fai
```

### Codex

After a configured Codex marketplace points at this repository:

```bash
codex plugin add fai@<marketplace-name>
```

Start a new conversation after installing or updating the plugin so newly added
skills are available.

Then run `/fai:setup` once. It writes `~/.fai/credentials.env`, validates
your API key against the platform, and offers to seed `~/.fai/accounts.json`.

## Skills

| Skill | What it does |
|---|---|
| `/fai:setup` | One-time credential setup + connectivity doctor |
| `/fai:account` | Account database: add, set, list, lookup, seed |
| `/fai:wb` | Workflow and submission ops: create, clone, download, upload draft, publish, run submissions, retry, results, tables, documents, validate, diff, fleet-publish |
| `/fai:workflow` | Workflow JSON authoring: step types, data flow, safe read/edit tooling, validation |
| `/fai:eval-studio` | Test batches (datasets), ground truth, runs, accuracy reports |
| `/fai:triage` | Investigate a failed/stuck execution or error message |
| `/fai:fix` | Linear ticket to a validated, uploaded draft |
| `/fai:accuracy` | Interactive HTML accuracy report and error investigation |
| `/fai:deck` | FurtherAI-branded PowerPoint decks |
| `/fai:doc` | FurtherAI-branded Word documents and print-quality PDFs |
| `/fai:copy-review` | Review copy against the FurtherAI Writing Style Guide |
| `/fai:screenshots` | Capture and annotate FurtherAI app screens when explicitly requested |

## What can I do with it?

Ask the plugin what it can do for your role, or describe the outcome you want.
Useful compound requests include:

- Moving a representative set of production submissions through a test workflow,
  evaluating them against ground truth, and preparing a customer accuracy deck.
- Preparing a customer implementation check-in from workflow changes, usage,
  accuracy, open issues, blockers, and prior commitments.
- Taking a Linear workflow ticket through investigation, an approved fix plan,
  a validated draft, and an evidence-backed test run without publishing.
- Tracing a failed execution through workflow configuration and backend code to
  produce a reproducible engineering handoff.

These examples do not grant permission to upload, run, publish, send, or update
an external system. Each skill keeps its own approval gates.

## Dependencies

Platform API skills use Python 3.9+ and the standard library. Common optional
requirements are:

| Work | Requirement |
|---|---|
| Build or inspect PowerPoint files | `python-pptx` |
| Build or inspect Word files | `python-docx` |
| Create designed PDFs or capture app screenshots | Chrome, Chromium, or Microsoft Edge |
| Render every PowerPoint slide or convert DOCX to PDF | LibreOffice |
| Crop logos or annotate screenshots | Pillow |
| Upload Excel files to Eval Studio | `pandas` and `openpyxl` |
| Review PDF copy | `pypdf` or `PyPDF2` |

See [`DEPENDENCIES.md`](DEPENDENCIES.md) for exact capability boundaries,
fallbacks, font setup, and optional integrations.

## Agent team

For non-trivial workflow builds, four plugin agents run under the main
conversation as orchestrator: `workflow-designer` (requirements → DESIGN.md),
`workflow-builder` (design → validated JSON), `workflow-validator`
(structural/semantic/scope review), `workflow-tester` (draft test run graded
against the design). The flow, artifacts, and approval gates are documented
in the `workflow` skill.

## Brand assets

`assets/brand/` carries the fonts, logos, deck and document templates, and the
writing style guide that the `deck`, `doc`, and `copy-review` skills build
against. `assets/brand/BRAND.md` is the palette, type ramp, and logo reference —
read it before composing any branded artifact.

## Configuration

Everything lives in `~/.fai/`:

- `credentials.env` — your API key(s), written by `/fai:setup` (chmod 600)
- `accounts.json` — per-account IDs (PropelAuth org, workflows, Linear,
  Slack, Notion, Eval Studio, EM/FDE), managed by `/fai:account`

Two auth flows, picked automatically: a personal API key (default — works
for any org member) or a restricted skill service key (FDE/admin — enables
cross-org operations like fleet-publish).

## Contributing

Read `CONVENTIONS.md` first. All API calls go through `lib/fai_client.py`;
all account resolution through `lib/fai_accounts.py`.
