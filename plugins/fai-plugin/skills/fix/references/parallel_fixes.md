# Parallel fix fan-out

Read this only when Phase 6 of the `fix` skill qualifies for fan-out:

- the workflow lives in a **git repo**, and
- there are **six or more** ready fixes, and
- they group into **three or more disjoint step sets**, and
- the user opted in.

Below that bar, serial editing wins. Fan-out costs a worktree per agent, a
merge pass, and a class of failure (half-merged state) that serial editing
does not have.

## Why disjoint step sets

Two agents editing the same step in two worktrees produce two divergent copies
of that step, and the step-level transplant in the merge phase has no way to
reconcile them. Grouping is therefore not a scheduling nicety — it is the
correctness condition. Multiple fixes on one step always go to the same agent.

Cap concurrency at 4. More groups than that: queue them.

## Setup (orchestrator, in the main repo)

1. Back up the workflow once — `cp workflow.json workflow.json.bak`, or the
   repo's own archive convention if it has one. **Agents must not archive**;
   parallel archive writes collide on filenames.
2. Record the baseline: step count from `workflow_tools.py summary`, and
   `git diff --stat` of the workflow directory.
3. Create the working dirs in the session scratchpad:
   `fanout/status/`, `fanout/prompts/`, `fanout/worktrees/`.

## Per agent

```bash
git worktree add <scratch>/fanout/worktrees/agent-N HEAD
```

`HEAD` of the current branch — **not** `origin/main`. Branching from the remote
gives agents a stale base and silently drops uncommitted work.

Write `fanout/prompts/agent-N.md` from the template below, then launch it as a
background Bash call:

```bash
cd <worktree> && claude -p --dangerously-skip-permissions "$(cat <prompt-path>)"
```

`--dangerously-skip-permissions` is what makes headless agents viable, and it
is also why the worktree boundary matters: the agent can write anything inside
it. Never point a headless agent at the main working copy.

### Agent prompt template

Normal prose, not compressed. Fill every placeholder.

```
You are resolving a scoped set of fixes in a workflow config repo worktree.

Worktree root (your ONLY writable area): {worktree_abs_path}
Target file: {worktree_abs_path}/{workflow_rel_path}
Status file (append-only): {status_dir}/agent-N.status
Workflow tools: {plugin_root}/skills/workflow/scripts/workflow_tools.py
Validator:      {plugin_root}/skills/workflow/scripts/validate_workflow.py

Rules:
- Work ONLY inside the worktree path above. Never write to the main repo.
- Edit ONLY these steps: {step_names}. Touch nothing else.
- Use workflow_tools.py for every read and write (summary, get-step, set-prompt,
  set-code, set-config, set-step, ...). Never open workflow.json with a file
  reader or editor.
- Do NOT archive the workflow. Do NOT edit STATE.md or any change log.
  Do NOT commit. Do NOT push.
- If a fix touches a spreadsheet template, mapping doc, or sidecar script,
  update it in the same edit.
- After EACH fix, append one line to the status file:
    {fix_title} :: IN PROGRESS | DONE | FAILED: <one-line reason>
  Write IN PROGRESS when you start a fix, then append its DONE/FAILED line when
  it resolves. The last line for a given title wins.
- When all fixes are done, run the validator on the target file and append:
    VALIDATION :: PASS
  or
    VALIDATION :: FAIL: <reason>
- Finally append:
    MANIFEST :: <comma-separated step names you actually modified>
    AGENT DONE

Fixes assigned to you:
{numbered list — the full description from the approved plan table, plus the
relevant ticket and comment excerpts, plus any answers the user gave}
```

## Progress reporting

Every ~3 minutes until every agent task exits, read `fanout/status/*.status`
(last line per fix title wins) and print one consolidated block — one line per
fix, exactly:

```
{Fix Title}: {Status}
```

Statuses: `Queued` / `In Progress` / `Done` / `Failed: <reason>`. Nothing else
between reports; a wall of narration defeats the point of running in the
background. Background task exit notifications also trigger a report.

An agent task that exits nonzero without an `AGENT DONE` line is dead: mark its
remaining fixes Failed and carry on with the others.

## Merge

Structured, step-level transplant — **not `git merge`**. The workflow JSON is
one enormous file, the main tree is usually dirty, and the step sets are
disjoint by construction, so transplanting is both deterministic and
collision-free where a textual merge is neither.

1. Per agent: `git -C <worktree> diff --name-only` → the files it actually changed.
2. For `workflow.json`, for each step in that agent's `MANIFEST`:
   ```bash
   python3 "$TOOLS" get-step <worktree>/<workflow.json> "<step>" > <scratch>/step.json
   python3 "$TOOLS" set-step <main>/<workflow.json> "<step>" <scratch>/step.json
   ```
3. Verify the `MANIFEST` matches the diff. On a mismatch, **trust the diff** and
   report the discrepancy — a manifest is what the agent claims, a diff is what
   it did.
4. Other changed files: copy worktree → main when the main copy is untouched
   this session. When both changed, stop and surface the conflict to the user.
5. `git worktree remove` every worktree and delete stray branches — on success
   and on failure alike.

## Post-merge validation

- Run the validator on the merged file. Compare the error count to the
  pre-fan-out baseline, not to zero.
- Step count matches baseline, unless a fix deliberately changed it.
- Spot-check one transplanted step per agent with `get-step`.
- Run `deps` on every transplanted step — a step edited in isolation can still
  break a mapping that another agent's step feeds.

If a transplant breaks validation, revert that step from the pre-fan-out
backup, report it, and offer to redo that agent's fixes serially in the main
repo.

Then return to Phase 7 of the skill and continue as normal.
