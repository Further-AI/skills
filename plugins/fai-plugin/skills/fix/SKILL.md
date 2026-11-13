---
name: fix
description: Takes a Linear ticket, reads the ticket and its comments, investigates the affected workflow, and resolves the issue end to end — asking the user whenever the ticket is ambiguous. Uses the triage skill to diagnose failing executions, the workflow skill to edit workflow JSON, the wb skill to upload a draft and run a test submission, and the account skill to resolve IDs. Use when the user says "/fix FAI-1234", "fix this ticket", "work this ticket", "resolve this issue", or hands over a Linear ticket ID or URL.
---

# fix — Linear ticket to validated draft

Input: a Linear ticket ID (`FAI-1234`), a Linear URL, or pasted ticket text.

Output: a validated workflow change, uploaded as a **draft** to the org the
user names, plus a per-fix report. This skill never publishes and never
updates the ticket — both are the user's call.

Reading a ticket directly requires a connected Linear MCP. Without it, ask for
the ticket body and comments and continue from the pasted source. See
`${CLAUDE_PLUGIN_ROOT}/DEPENDENCIES.md` for optional integration requirements.

## The shape of a run

| Phase | What happens | Gate |
|---|---|---|
| 1 | Fetch the ticket + every comment | — |
| 2 | Locate the workflow and get it locally | — |
| 3 | Investigate: inspect the workflow, triage any referenced execution | — |
| 4 | Decompose into granular, step-scoped fixes | — |
| 5 | **Plan checkpoint** — tables, then approval | **user gate** |
| 6 | Implement (serial by default) | — |
| 7 | Validate | — |
| 8 | Upload draft | **user gate** |
| 9 | Test submission | **user gate**, optional |
| 10 | Report | — |

Skip nothing. A one-line ticket still gets a checkpoint — it's one row.

---

## Phase 1 — Fetch the ticket

Use the Linear MCP tools: `get_issue` for the ticket, `list_comments` for the
thread. **Read every comment.** The real spec usually lives in the comments —
the reporter's own triage notes, the customer's exact wording, a follow-up
that narrows or reverses the original ask.

Pull out and keep:
- the stated problem and the expected behavior
- any execution ID, submission ID, or app URL
- any attached document or screenshot reference
- the team / project / labels (these hint at the account)
- who reported it, so you know whose words to trust on intent

No Linear MCP connected? Say so once and ask the user to paste the ticket body
and comments. Everything downstream works the same. Do not invent ticket
content, and do not guess at a ticket ID you were not given.

## Phase 2 — Locate the workflow, get it locally

Resolve **account** and **workflow** in this order, stopping at the first that
answers:

1. The conversation — the user already named them.
2. The ticket — project name, title, description, labels, or an app URL
   containing the workflow ID.
3. `~/.fai/accounts.json` via the account skill:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/account/scripts/account.py" list
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/account/scripts/account.py" get <ref>
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/account/scripts/account.py" lookup <uuid>
   ```
   `lookup` turns a bare UUID from the ticket into a named account + workflow.
4. Ask the user, listing the candidates you found.

Never guess a workflow. Two workflows on one account can differ by a single
word in the name, and fixing the wrong one is silent until it ships.

Then get the JSON. Two situations:

**The user has a workflow repo** (a directory holding `workflow.json`, usually
with a `STATE.md`). Use it. Determine it from cwd or ask — never assume a path.
If the repo has its own `CLAUDE.md`, follow it; its conventions (archive rules,
state files, change logs) outrank this skill's defaults.

**No repo** — download into a scratch directory and work there:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/wb/scripts/wb.py" download \
  --account <acct> --workflow-id <uuid|name> --draft --out workflow.json
```
Drop `--draft` to start from the live version. Say which one you took: an
existing draft may already carry someone else's unshipped work, and your upload
will overwrite it. If a draft exists and you didn't author it, ask before
building on top of it.

## Phase 3 — Investigate before you plan

Two tracks, both cheap. Do them before writing the plan, not after.

**The workflow itself** — always:
```bash
TOOLS="${CLAUDE_PLUGIN_ROOT}/skills/workflow/scripts/workflow_tools.py"
python3 "$TOOLS" summary <workflow.json>
python3 "$TOOLS" get-step <workflow.json> "<step name>"
python3 "$TOOLS" deps <workflow.json> "<step name>"
python3 "$TOOLS" search <workflow.json> "<regex>"
```
`summary` first, always. `search` is the fastest way from a ticket phrase to
the step that owns it. **Never open `workflow.json` with Read or Edit** — these
files run to tens of thousands of lines.

**A failing execution** — whenever the ticket names one:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/triage/scripts/inspect.py" inspect <exec_id_or_url> \
  --account <acct>
python3 "${CLAUDE_PLUGIN_ROOT}/skills/triage/scripts/inspect.py" step <exec_id_or_url> "<step name>" \
  --account <acct>
```
Triage gives you the failing step, its real type, and the exact error. That
error text belongs in the fix description — a plan written without it is a
guess.

If the ticket is about wrong *output* rather than a failure, pull the actual
step output instead:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/wb/scripts/wb.py" results \
  --account <acct> --exec-id <id> --step "<substr>"
```

## Phase 4 — Decompose

Split the ticket into granular, step-scoped fixes. A one-ask ticket stays one
fix. A ten-bullet ticket becomes ten.

**The detail bar:** every fix names the exact step, the exact field / prompt
key / config key / schema property, and the expected behavior after the change.

> Not: "Update the name prompt."
> Yes: "In the `Extract Underwriter Data` step, change the `first_name` field
> instruction in `config.extraction_schema` to pull the underwriter's first
> name specifically, not the broker contact's."

You cannot hit that bar from the ticket alone — Phase 3 is what makes it
possible. Things that routinely trip people up:

- An extraction step's driving schema is `config.extraction_schema`, not the
  output schema.
- `system_prompt` lives in `input_mappings`, never in `config`.
- Function/code steps need their inputs synced in three places — the code
  signature, the input mappings, and whatever calls them downstream.
- Step names are case-sensitive and are referenced by string across mappings.

Classify each fix:

- **Ready** — the ticket plus your investigation is enough to implement it.
- **Needs info** — it turns on something only the user knows.

Never convert a Needs info into a Ready by picking the likelier reading. Ask.

## Phase 5 — Plan checkpoint (required gate)

**Two turns. The tables get a turn of their own.**

**Turn A** — emit both tables, fully populated, as the entire text of a turn
that makes **zero tool calls**, then end the turn. The harness can hide
assistant text that shares a turn with a tool call, and this message is the
user's only complete view of the plan. `AskUserQuestion` option labels truncate
and cannot substitute for it.

Fixes — one row per ready fix:

| # | Title (≤5 words) | Step | What changes (1–2 sentences, states the actual edit) |

Needs info — one row per open question (omit the table only when there are none):

| # | Title (≤5 words) | Question (≤2 sentences) |

**Turn B** — now ask. `AskUserQuestion`: approve the list, or edit it. Reference
fixes by `#`. Fold the needs-info questions into the same call when there are
three or fewer (the tool caps at four questions: one approval plus three).
More than three → ask the user to answer inline. Duplicate the Fixes table into
the approve option's `preview` field as redundancy.

If the user's reply to Turn A already approves or edits the plan in words,
honor it and skip `AskUserQuestion`.

Answered questions move to Ready. Unanswered ones are **excluded from this
run** and listed in the final report. Do not proceed without approval.

## Phase 6 — Implement

Default is **serial, in the main working copy** — it is simpler, it is easier
to review, and for most tickets it is also faster.

Per fix:
1. Read the current state: `get-step` / `get-config` / `get-prompts` /
   `get-code` / `get-input-mappings` / `get-output-schema`.
2. Write the new content to a temp file.
3. Apply with the most surgical command that fits — `set-prompt`, `set-code`,
   `set-config`, `set-input-mappings`, `set-output-schema`, `set-step`,
   `add-step`, `remove-step`, `rename-step`.
4. Validate (Phase 7) and move on.

Back up once before the first change: `cp workflow.json workflow.json.bak`.
If the repo has archive conventions, follow those instead.

**Parallel fan-out** is available for large tickets — read
`references/parallel_fixes.md` only when *all* of these hold: a git repo, six
or more ready fixes, fixes that group into three or more **disjoint** step
sets, and the user opts in. Below that bar it costs more than it saves.

## Phase 7 — Validate

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/workflow/scripts/validate_workflow.py" <workflow.json>
```

After **every** edit, not once at the end. Exit 0 = valid, exit 1 = errors.

Then check the things the validator can't:
- Step count matches the baseline, unless a fix deliberately added or removed one.
- `deps` on each edited step — no dangling references, no orphaned downstream input.
- Re-read one edited step with `get-step` and confirm the change is what you meant.

Compare error counts against the pre-edit baseline rather than reading the
validator's output cold — a workflow may carry pre-existing warnings that are
not yours to fix on this ticket.

## Phase 8 — Upload draft (user gate)

Uploading is a **write to a real org** and it overwrites that workflow's active
draft. Confirm immediately before the call, naming the environment, the org /
account, the workflow, and the fact that it replaces the current draft.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/wb/scripts/wb.py" upload \
  --account <acct> --env <env> --workflow-id <uuid> --json <workflow.json> \
  --summary "<TICKET-ID>: <short summary>"
```

Ask where it should go. Many teams stage fixes in a sandbox org before the
customer org — if the user has one, they'll say so, and it's worth saving:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/account/scripts/account.py" add-workflow <ref> \
  --name <workflow-name> --prod-id <uuid>
```
so the next ticket resolves it without asking. Never assume a sandbox exists,
and never route a fix to one org because a previous run used it.

**Never publish from this skill**, with or without `--allow-override`.
Publishing is a separate, explicit decision the user makes with `/fai:wb`.

Record the draft version the upload returns.

## Phase 9 — Test submission (optional, user gate)

Ask whether to run a test submission against the new draft. If yes, get the
documents from the user — a repo's sample submissions folder, files they name,
or documents pulled from the original failing execution:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/wb/scripts/wb.py" documents list --exec-id <id> --account <acct>
python3 "${CLAUDE_PLUGIN_ROOT}/skills/wb/scripts/wb.py" documents download-all \
  --exec-id <id> --input-only --out ./docs --account <acct>
```

Then run it against the draft:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/wb/scripts/wb.py" execute \
  --account <acct> --workflow-id <uuid> --draft-version <v> --docs <files...> --wait
```

Executions running against a customer prod org consume real quota and appear in
that org's submission list — confirm before running there.

When it finishes, check the step that the ticket was about, not just the overall
status. A `completed` execution with the same wrong value is not a fix:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/wb/scripts/wb.py" status --exec-id <new-id> --account <acct>
python3 "${CLAUDE_PLUGIN_ROOT}/skills/triage/scripts/inspect.py" step <new-id> "<step>" --account <acct>
```

Terminal statuses are `completed`, `failed`, `cancelled`, `terminated`. Success
is `completed` — never "succeeded". `cancelled` and `terminated` never resume.

## Phase 10 — Report

```
## <TICKET-ID> — <title>

| # | Fix | Step | Result |
|---|---|---|---|
| 1 | ... | ... | Done / Failed: <reason> / Excluded: unanswered |

Validation: <pass | N errors, baseline was M>
Draft:      v<N> on <account> (<env>) — <workflow app URL>
Test run:   <execution URL + verdict, or "not run">
Open:       <needs-info items still unanswered>
```

State plainly what did not get done and why. A ticket that is 80% resolved is
reported as 80% resolved.

Do **not** post back to Linear. Offer to draft a comment if the user wants one.

---

## Failure handling

| Situation | Do this |
|---|---|
| Validation fails after a fix | Revert that fix from the backup, report it, keep the others |
| A fix turns out to need info mid-implementation | Stop that fix, finish the rest, surface the question in the report |
| `wb upload` returns 4xx | Read the error; check org, workflow ID, env, and `fai_admin` in the target org. Do not retry blindly |
| Test run fails on an unrelated step | Report it separately — it is a new finding, not this ticket's regression |
| The ticket turns out to be a platform bug, not a workflow bug | Say so, with the triage evidence. Do not force a workflow-side workaround without asking |

## Don't

- Don't publish. Ever, from this skill.
- Don't read or edit `workflow.json` with Read/Edit — use `workflow_tools.py`.
- Don't skip the plan checkpoint, however small the ticket.
- Don't implement a "Needs info" fix on your best guess.
- Don't write to a customer org without a confirmation naming that org.
- Don't update the Linear ticket, change its status, or assign it.
- Don't hardcode an account, org, sandbox, or repo path across runs — resolve
  it every time.
