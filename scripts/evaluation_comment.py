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
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

REPOSITORY = "Further-AI/skills"
MARKER = re.compile(
    r"<!-- skills-evaluation run=(\d+) attempt=(\d+) phase=(running|final) -->"
)


class Score(BaseModel):
    """A mean and its assessed count, with missing dimensions left unassessed."""

    model_config = ConfigDict(allow_inf_nan=False)
    mean: float = Field(ge=0, le=1)
    assessed: int = Field(ge=0)


type SkillName = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]

type Metric = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
type Threshold = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class SkillResult(BaseModel):
    """One skill's aggregate scores and trusted evaluation policy."""

    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    status: Literal["passed", "failed", "not_configured", "error"]
    scheduled: int | None = Field(default=None, gt=0)
    experiment_url: str | None = Field(
        default=None,
        pattern=r"^https://(?:www\.)?braintrust\.dev/[A-Za-z0-9%._~:/?&=+\-]+$",
    )
    blocking_scores: dict[Metric, Threshold] = Field(default_factory=dict)
    advisory_scores: list[Metric] = Field(default_factory=list)
    scores: dict[Metric, Score] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_coverage(self) -> "SkillResult":
        """Reject impossible assessment counts and passed results without a schedule."""
        if self.status == "passed" and self.scheduled is None:
            raise ValueError("Passed evaluations require a schedule")
        if any(
            self.scheduled is None or score.assessed > self.scheduled
            for score in self.scores.values()
        ):
            raise ValueError("Assessments exceed scheduled trials")
        return self


class Summary(BaseModel):
    """The backend's public-safe results for all affected skills."""

    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    status: Literal["passed", "failed", "not_applicable"]
    skills: list[SkillResult]
    skipped_skills: list[SkillName] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_status(self) -> "Summary":
        """Do not accept an overall pass that hides a failed or missing profile."""
        if self.status == "passed" and (
            not self.skills or any(skill.status != "passed" for skill in self.skills)
        ):
            raise ValueError("Passed summary requires passing skill results")
        if self.status == "not_applicable" and self.skills:
            raise ValueError("Not applicable requires no affected skills")
        return self


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


def score_text(skill: SkillResult, dimension: str) -> str:
    """Distinguish inapplicable, unassessed, and measured dimensions.

    Args:
        skill: Profile policy and recorded results.
        dimension: Metric to display.

    Returns:
        Score and dynamic assessment count, or an explicit unavailable state.
    """
    if skill.status == "not_configured":
        return "—"
    if (
        dimension not in skill.blocking_scores
        and dimension not in skill.advisory_scores
    ):
        return "N/A"
    score = skill.scores.get(dimension)
    scheduled = str(skill.scheduled) if skill.scheduled is not None else "?"
    if score is None or score.assessed == 0:
        return f"Unassessed (0/{scheduled})"
    return f"{score.mean * 100:.1f}% ({score.assessed}/{scheduled} assessed)"


def result_table(skills: list[SkillResult]) -> str:
    """Render one row per skill with only the dimensions selected by its profile.

    Args:
        skills: Validated public results.

    Returns:
        Markdown table and each skill's blocking criteria.
    """
    metrics = sorted(
        {
            metric
            for skill in skills
            for metric in [*skill.blocking_scores, *skill.advisory_scores]
        }
    )
    headers = [
        "Skill",
        "Experiment",
        *(metric.replace("_", " ").capitalize() for metric in metrics),
        "Outcome",
    ]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    outcomes = {
        "passed": "Passed",
        "failed": "Failed",
        "error": "Evaluation incomplete",
        "not_configured": "Enabled without an evaluation profile",
    }
    for skill in skills:
        experiment = (
            f"[Braintrust]({skill.experiment_url})" if skill.experiment_url else "—"
        )
        cells = [
            skill.name,
            experiment,
            *(score_text(skill, metric) for metric in metrics),
            outcomes[skill.status],
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append(
        "\nScores are means on a 0–100 scale; counts show assessed/scheduled trials."
    )
    for skill in skills:
        if skill.status == "not_configured":
            continue
        criteria = ", ".join(
            f"{metric.replace('_', ' ')} ≥ {threshold * 100:g}%"
            for metric, threshold in skill.blocking_scores.items()
        )
        lines.append(
            f"\n`{skill.name}`: all trials must complete without evaluation or cleanup errors. "
            f"Blocking scores require full assessment: {criteria or 'none'}. Other displayed scores are advisory."
        )
    return "\n".join(lines) + "\n"


def render(update: Update, summary: Summary | None) -> str:
    """Build one commit-bound results comment, never treating absent results as a pass.

    Args:
        update: Workflow identity and outcome.
        summary: Validated results belonging to the same commit.

    Returns:
        Markdown for the bot-owned evaluation comment.
    """
    if summary is not None and summary.commit != update.commit:
        raise ValueError("Summary belongs to a different commit")
    running = update.state == "running"
    phase = "running" if running else "final"
    commit = (
        f"[{update.commit[:7]}](https://github.com/{REPOSITORY}/commit/{update.commit})"
    )
    body = (
        f"<!-- skills-evaluation run={update.run} attempt={update.attempt} phase={phase} -->\n"
        f"### Skills evaluation\n\nTested commit: {commit}\n\n"
    )
    if running:
        body += "Pending. Previous results are outdated; this commit is awaiting evaluation.\n"
    else:
        outcome = "Failed — evaluation incomplete"
        if update.state == "cancelled":
            outcome = "Cancelled"
        elif update.state == "success" and summary is not None:
            outcome = {
                "passed": "Passed",
                "failed": "Failed",
                "not_applicable": "No affected skills with evaluation profiles",
            }[summary.status]
        body += f"**Gate: {outcome}**\n\n"
        if summary and summary.skills:
            body += result_table(summary.skills)
        elif summary is None:
            body += "Scores are unassessed because no result summary is available.\n"
    if not running and summary and summary.skipped_skills:
        skipped = ", ".join(f"`{name}`" for name in summary.skipped_skills)
        body += f"\nNot evaluated (disabled, no profile): {skipped}.\n"
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
        github(
            f"repos/{REPOSITORY}/issues/{update.pr}/comments?per_page=100", pages=True
        )
    )
    existing = next(
        (
            comment
            for page in pages
            for comment in page
            if comment.user.login == "github-actions[bot]"
            and MARKER.match(comment.body or "")
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
    pr = PullRequest.model_validate_json(
        github(f"repos/{REPOSITORY}/pulls/{update.pr}")
    )
    if pr.state != "open" or pr.head.sha != update.commit:
        return
    if existing is None:
        github(
            f"repos/{REPOSITORY}/issues/{update.pr}/comments", method="POST", body=body
        )
    else:
        github(
            f"repos/{REPOSITORY}/issues/comments/{existing.id}",
            method="PATCH",
            body=body,
        )


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
        Summary.model_validate_json(args.summary.read_text())
        if args.summary.exists()
        else None
    )
    publish(update, summary)


if __name__ == "__main__":
    main()
