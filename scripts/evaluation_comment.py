"""Update one PR comment with aggregate evaluation results for its current head.

Only trusted workflow code runs this script. Downloaded JSON is validated as data;
source files, judge prose, and workbook contents never enter the public comment.
Workflow concurrency serializes updates, and head/run checks reject stale results.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

REPOSITORY = "Further-AI/skills"
MARKER = re.compile(r"<!-- skills-evaluation run=(\d+) attempt=(\d+) phase=(running|final) -->")


class Score(BaseModel):
    """A mean and its assessed count, with missing dimensions left unassessed."""

    model_config = ConfigDict(allow_inf_nan=False)
    mean: float = Field(ge=0, le=1)
    assessed: int = Field(ge=0, le=5)


class Summary(BaseModel):
    """The backend's intentionally small, public-safe score artifact."""

    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    status: Literal["passed", "failed", "not_applicable"]
    experiment_url: str | None = Field(
        default=None,
        pattern=r"^https://(?:www\.)?braintrust\.dev/[A-Za-z0-9%._~:/?&=+\-]+$",
    )
    scores: dict[str, Score]


class Head(BaseModel):
    """The commit currently under review."""

    sha: str


class PullRequest(BaseModel):
    """The PR identity needed to suppress outdated or closed-PR updates."""

    state: str
    head: Head


class Author(BaseModel):
    """Comment ownership prevents editing a user's copied table."""

    login: str


class Comment(BaseModel):
    """One existing PR conversation comment."""

    id: int
    body: str | None
    user: Author


class Update(BaseModel):
    """Validated identity and state of this workflow's comment update."""

    pr: int = Field(gt=0)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    state: Literal["running", "success", "failure", "cancelled", "skipped"]
    backend_run: int | None = Field(default=None, gt=0)
    run: int = Field(gt=0)
    attempt: int = Field(gt=0)


def score_text(summary: Summary | None, dimension: str, *, running: bool) -> str:
    """Format one score without presenting missing evidence as zero.

    Args:
        summary: Available backend results.
        dimension: Metric to display.
        running: Whether judging has yet to finish.

    Returns:
        Percentage and assessment count, or an explicit unavailable state.
    """
    if running:
        return "Pending"
    score = summary.scores.get(dimension) if summary else None
    if score is None or score.assessed == 0:
        return "Unassessed (0/5)"
    return f"{score.mean * 100:.1f}% ({score.assessed}/5 assessed)"


def render(update: Update, summary: Summary | None) -> str:
    """Build the results table using only safe links and aggregate scores.

    Args:
        update: Workflow identity and outcome.
        summary: Validated results belonging to the same commit.

    Returns:
        Markdown for the single bot-owned evaluation comment.
    """
    if summary is not None and summary.commit != update.commit:
        raise ValueError("Summary belongs to a different commit")
    running = update.state == "running"
    outcome = "Running"
    if not running:
        outcome = "Failed — evaluation incomplete"
        if update.state == "cancelled":
            outcome = "Cancelled"
        elif summary is not None and summary.status == "failed":
            outcome = "Failed"
        elif summary is not None and update.state == "success":
            outcome = {"passed": "Passed", "failed": "Failed", "not_applicable": "Not applicable"}[
                summary.status
            ]
    experiment = (
        f"[Braintrust]({summary.experiment_url})" if summary and summary.experiment_url else "—"
    )
    cells = [
        score_text(summary, metric, running=running)
        for metric in ("file_validity", "completeness", "functional_correctness")
    ]
    commit = f"[{update.commit[:7]}](https://github.com/{REPOSITORY}/commit/{update.commit})"
    phase = "running" if running else "final"
    body = (
        f"<!-- skills-evaluation run={update.run} attempt={update.attempt} phase={phase} -->\n"
        "### Excel generation evaluation\n\n"
        "| Commit | Experiment | File validity | Completeness | Functional correctness | Gate |\n"
        "|---|---|---|---|---|---|\n"
        f"| {commit} | {experiment} | {' | '.join(cells)} | {outcome} |\n\n"
        "Completeness and functional correctness are advisory. Scores are means on a 0–100 scale.\n"
    )
    if running:
        body += "Previous results are outdated; this commit is awaiting evaluation.\n"
    if summary and summary.status == "not_applicable":
        body += "This commit does not contain `excel-generation`; no skill evaluation ran.\n"
    if update.backend_run is not None:
        body += f"\n[Backend run](https://github.com/Further-AI/fai-automation-backend/actions/runs/{update.backend_run})\n"
    return body


def github(
    endpoint: str, *, method: str = "GET", body: str | None = None, pages: bool = False
) -> str:
    """Call GitHub with structured input and propagate API failures.

    Args:
        endpoint: Fixed-repository API endpoint.
        method: HTTP operation.
        body: Comment Markdown for write operations.
        pages: Whether to collect paginated comment arrays.

    Returns:
        JSON response text.
    """
    command = ["gh", "api", "--method", method, endpoint]
    if pages:
        command.extend(["--paginate", "--slurp"])
    if body is not None:
        command.extend(["--input", "-"])
    return subprocess.check_output(
        command,
        input=json.dumps({"body": body}) if body is not None else None,
        text=True,
        timeout=60,
    )


def publish(update: Update, summary: Summary | None) -> None:
    """Create or update our comment only while its result still applies.

    Args:
        update: Validated PR, commit, and workflow identity.
        summary: Optional final score artifact.
    """
    body = render(update, summary)
    pages = TypeAdapter(list[list[Comment]]).validate_json(
        github(f"repos/{REPOSITORY}/issues/{update.pr}/comments?per_page=100", pages=True)
    )
    existing = next(
        (
            comment
            for page in pages
            for comment in page
            if comment.user.login == "github-actions[bot]" and MARKER.match(comment.body or "")
        ),
        None,
    )
    if existing is not None:
        marker = MARKER.match(existing.body or "")
        assert marker is not None
        previous = (int(marker[1]), int(marker[2]))
        if previous > (update.run, update.attempt):
            return
        if (
            previous == (update.run, update.attempt)
            and marker[3] == "final"
            and update.state == "running"
        ):
            return
    # Recheck immediately before writing, while the workflow holds the PR comment lock.
    pr = PullRequest.model_validate_json(github(f"repos/{REPOSITORY}/pulls/{update.pr}"))
    if pr.state != "open" or pr.head.sha != update.commit:
        return
    if existing is None:
        github(f"repos/{REPOSITORY}/issues/{update.pr}/comments", method="POST", body=body)
    else:
        github(f"repos/{REPOSITORY}/issues/comments/{existing.id}", method="PATCH", body=body)


def main() -> None:
    """Read a score artifact if present and update the PR's results table."""
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("pr", "commit", "state", "run", "attempt"):
        parser.add_argument(f"--{argument}", required=True)
    parser.add_argument("--backend-run", default="")
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    update = Update.model_validate(
        {
            "pr": args.pr,
            "commit": args.commit,
            "state": args.state,
            "run": args.run,
            "attempt": args.attempt,
            "backend_run": args.backend_run or None,
        }
    )
    summary = (
        Summary.model_validate_json(args.summary.read_text()) if args.summary.exists() else None
    )
    publish(update, summary)


if __name__ == "__main__":
    main()
