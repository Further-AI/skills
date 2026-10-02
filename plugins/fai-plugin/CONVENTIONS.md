# fai plugin conventions

Contract for every skill in this plugin. Read before writing or editing a skill.

## Audience

The plugin serves the whole org: engineers, Forward Deployed Engineers,
Engagement Managers, and GTM/sales. Assume the user has Codex or Claude Code,
Python 3.9+, and network access. Assume the user does NOT have the backend repo,
the webapp repo, database access, uv, optional Python packages, or pre-existing
`.env` files.

## Layout

```
skills/<name>/SKILL.md          # instructions; frontmatter: name, description
skills/<name>/scripts/*.py      # runnable helpers (stdlib only for core skills)
skills/<name>/references/*.md   # deep reference docs loaded on demand
lib/fai_client.py               # THE ONLY way to call the platform API
lib/fai_accounts.py             # THE ONLY way to read/write accounts.json
lib/fai_browser.py              # THE ONLY way to drive a browser (screenshots skill)
```

Skills invoke as `/fai:<name>`. Scripts import lib via:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from fai_client import FaiClient, FaiError
from fai_accounts import resolve_account, resolve_org_id, default_env
```

(`parents[3]` = plugin root when the script sits at `skills/<name>/scripts/x.py`.)

## Configuration — ~/.fai/ only

- `~/.fai/credentials.env` — written by `fai:setup`, chmod 600. Keys:
  `PROD_API_URL`, `PROD_APP_URL`, `STAGING_API_URL`, `STAGING_APP_URL`
  (optional; sensible defaults built into lib), `FAI_PROD_PERSONAL_API_KEY`,
  `FAI_PROD_USER_ID`, `FAI_STAGING_PERSONAL_API_KEY`, `FAI_STAGING_USER_ID`,
  and optionally `FAI_SKILL_PROD_PERSONAL_API_KEY` /
  `FAI_SKILL_STAGING_PERSONAL_API_KEY` (restricted service key — FDE/admin
  power feature, enables cross-org operations via X-FAI-Target-Org-Id).
- `~/.fai/accounts.json` — the account database. Schema in `lib/fai_accounts.py`.

Never read `~/.claude/skills/wb/.env`, `~/.agent-config/*`, repo `.env` files,
or any other legacy credential location. Never print secrets. Never write
credentials anywhere except `~/.fai/credentials.env`.

Two sanctioned exceptions to the paths-outside-the-plugin rule:
- `fai:setup import-legacy` performs the single sanctioned READ of the two
  legacy credential files above, solely to migrate values into
  `~/.fai/credentials.env` (values never printed).
- `fai:account seed` scans a user-supplied folder of account/workflow
  directories to bootstrap the account database.

## Standard flags on every API-touching skill

- `--env prod|staging|local` — default from accounts.json `defaults.env`, else prod.
- `--account <slug|name|domain>` — resolves org/workflow IDs via lib.
- `--org-id <uuid>` — explicit override, always wins over --account.
- IDs (workflow, execution, dataset) accepted raw; workflow may also be a
  name resolved against the account record.

## API ground rules

- API-first, always. No MongoDB, no direct DB reads, no blob-pointer tricks.
  The audience has no DB access; a skill that needs it is broken by design.
- All HTTP goes through `FaiClient` — it handles token minting (both flows),
  org scoping, attribution headers, 401 re-mint, and error hints.
- Pass a specific `skill_name` to `FaiClient(skill_name="wb")` so the
  X-FAI-Skill-Name attribution header identifies the calling skill.
- Restricted-token users can only reach the ~70 allowlisted routes
  (`src/backend/skill_service_auth.py` in the backend repo is the contract).
  Notable absences: workflow delete, dataset delete, org listing.

## Encoded guardrails — repeat these in any skill they touch

Each has a documented incident behind it:

1. **Hostnames lie.** Prod backend's Azure hostname contains "staging".
   Environment comes from config, never from a hostname.
2. **Separate PropelAuth instances.** Staging and prod share nothing: keys,
   user IDs, org IDs. A 404 across environments is expected, not a bug.
3. **owner_uid/owner_oid must be empty strings** on document registration —
   the API derives them from the token and 400s otherwise. (lib handles it.)
4. **Publish `override: true` bypasses ONLY the draft-test gate** — publish
   validation still runs and cannot be bypassed. 400 detail.error is one of
   TEST_RUN_REQUIRED / TEST_RUN_IN_PROGRESS / TEST_RUN_FAILED.
5. **`step_type` is a lossy display bucket.** Use `step_type_raw` (may be
   null on old executions; fall back to the version's step config).
   Key off `input.step_name`, not `title`.
6. **Four terminal statuses**: completed, failed, cancelled, terminated.
   Polling only for completed/failed hangs forever. (lib handles it.)
7. **Headline accuracy is micro-F1**, classification steps only — function/
   SOV/custom steps can regress without moving it. Always show
   `overall_accuracy_source`, `headline.coverage`, per-step accuracy.
8. **Public builder endpoints return 400 (not 401) for bad tokens.**
9. **`POST .../validate` returning `is_valid: false` is a successful call**
   (HTTP 200) — HTTP 404 there means "no draft exists", not "invalid".
10. **No retry endpoint exists.** Retry = list old execution's documents →
    new execution with same user_document_ids. New execution ID, runs from step 1.

## Vocabulary — match the UI, not the API

| API name | UI label (use in user-facing output) |
|---|---|
| Dataset | Test Batch |
| Submission (eval) | Sample |
| ground_truth | Ground Truth / Answers |
| output_data / predicted | AI-Generated |
| evaluation run | Test run |
| execution (in workflow overview) | Submission |

Execution success status is `completed` (never "succeeded"). Version terms:
"Draft vN", "Live", "Publish", "Promote".

## Design tokens (for generated HTML artifacts)

These are the **webapp/report** tokens — they apply to the `accuracy` report and
`doc`'s HTML→PDF path, served from `lib/assets/tokens.css` via `lib/fai_render.py`.
Branded marketing and document artifacts (`deck`'s .pptx, `doc`'s .docx) use the
separate palette and templates in `assets/brand/BRAND.md`; do not mix the two.

Anchor: webapp `src/styles/fai-tailwind.css`. Fonts: Wix Madefor Text (body),
Wix Madefor Display (headings). Core palette: mango `#fb9608` (brand),
navy `#425c86` (actions/links), pinehurst `#2b785d` (success),
sinopia `#b53b18` (error), mango-1100 `#bc6d06` (warning text),
text `#161611`/`#6f6d64`, surfaces `#ffffff`/`#f9f9f9`, border `#e8e8e8`.
Chart palette: `#E8972C #3481C2 #34A853 #8B5CF6 #EC4899 #14B8A6 #F59E0B
#EF4444 #6366F1 #10B981`. Status badges follow the webapp Badge component
(color x emphasis). Accuracy thresholds: >=80 good, >=50 medium, <50 poor.

## SKILL.md style

- Frontmatter: `name`, `description` (third person, includes trigger phrases —
  what a user would say to want this skill).
- Lead with a command table or decision table, not prose.
- Document exact request/response shapes for anything the model must construct.
- State failure modes with the fix next to them.
- Keep it self-contained: no references to the backend repo, accounts repo,
  or any path outside the plugin and ~/.fai/.
- Write scripts for anything mechanical (>2 API calls in a fixed order);
  keep the model in the loop for anything judgment-shaped.

## Python style

Use the standard library for core platform operations. Scripts print
human-scannable output, put IDs and URLs on labeled lines, exit non-zero on
failure, and never print secrets.

Artifact creation and specialized import formats may use optional dependencies
when the capability genuinely requires them. Every optional dependency must:

- be documented in `DEPENDENCIES.md` and in the skill that needs it;
- fail with the exact missing package or application and an installation path;
- preserve a useful fallback when one exists;
- never be installed without the user's approval.

Do not make platform API operations depend on artifact packages such as
`python-pptx`, `python-docx`, Pillow, pandas, or openpyxl.
