"""Verify score rendering, comment ownership, and stale-result protection."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from scripts import evaluation_comment as subject


@pytest.fixture
def update() -> subject.Update:
    return subject.Update(
        pr=13, commit="a" * 40, state="success", run=100, attempt=1, backend_run=456
    )


@pytest.fixture
def summary(update: subject.Update) -> subject.Summary:
    return subject.Summary(
        commit=update.commit,
        status="passed",
        experiment_url="https://www.braintrust.dev/app/FurtherAI/p/Skills%20Evaluation/experiments/test",
        scores={
            "file_validity": subject.Score(mean=1, assessed=5),
            "completeness": subject.Score(mean=0.75, assessed=3),
        },
    )


def test_table_includes_experiment_partial_coverage_and_unassessed_scores(
    update: subject.Update, summary: subject.Summary
) -> None:
    text = subject.render(update, summary)
    assert summary.experiment_url is not None
    assert summary.experiment_url in text
    assert "100.0% (5/5 assessed)" in text
    assert "75.0% (3/5 assessed)" in text
    assert "Unassessed (0/5)" in text
    assert "Passed" in text and "456" in text


@pytest.mark.parametrize("state", ["failure", "cancelled", "skipped", "success"])
def test_missing_summary_never_reports_a_pass(update: subject.Update, state: str) -> None:
    text = subject.render(update.model_copy(update={"state": state}), None)
    assert "Passed" not in text
    assert "Unassessed" in text


def test_pending_update_invalidates_old_scores(update: subject.Update) -> None:
    text = subject.render(update.model_copy(update={"state": "running"}), None)
    assert "Previous results are outdated" in text
    assert "Pending" in text


def test_summary_for_another_commit_is_rejected(
    update: subject.Update, summary: subject.Summary
) -> None:
    with pytest.raises(ValueError, match="different commit"):
        subject.render(update, summary.model_copy(update={"commit": "b" * 40}))


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/results",
        "https://www.braintrust.dev/x)|@everyone",
        "http://braintrust.dev/x",
    ],
)
def test_summary_rejects_links_outside_braintrust(url: str) -> None:
    with pytest.raises(ValidationError):
        subject.Summary(commit="a" * 40, status="passed", experiment_url=url, scores={})


@pytest.mark.parametrize("existing", [False, True])
def test_publisher_creates_or_updates_its_own_comment(
    update: subject.Update, summary: subject.Summary, existing: bool
) -> None:
    comments = [{"id": 8, "body": subject.render(update, None), "user": {"login": "human"}}]
    if existing:
        comments.append(
            {
                "id": 9,
                "body": "<!-- skills-evaluation run=99 attempt=1 phase=final -->",
                "user": {"login": "github-actions[bot]"},
            }
        )
    with patch.object(
        subject,
        "github",
        side_effect=[
            json.dumps([[], comments]),
            json.dumps({"state": "open", "head": {"sha": update.commit}}),
            "{}",
        ],
    ) as api:
        subject.publish(update, summary)
    call = api.call_args
    assert call.kwargs["method"] == ("PATCH" if existing else "POST")
    assert call.args[0].endswith("comments/9" if existing else "13/comments")
    assert "Passed" in call.kwargs["body"]


@pytest.mark.parametrize("state,sha", [("closed", "a" * 40), ("open", "b" * 40)])
def test_changed_or_closed_pr_receives_no_update(
    update: subject.Update, state: str, sha: str
) -> None:
    with patch.object(
        subject, "github", side_effect=["[[]]", json.dumps({"state": state, "head": {"sha": sha}})]
    ) as api:
        subject.publish(update, None)
    assert all(call.kwargs.get("method", "GET") == "GET" for call in api.call_args_list)


@pytest.mark.parametrize(
    "run,attempt,phase,state",
    [(101, 1, "running", "success"), (100, 2, "final", "success"), (100, 1, "final", "running")],
)
def test_older_update_cannot_replace_newer_or_final_comment(
    update: subject.Update, run: int, attempt: int, phase: str, state: str
) -> None:
    comment = {
        "id": 9,
        "body": f"<!-- skills-evaluation run={run} attempt={attempt} phase={phase} -->",
        "user": {"login": "github-actions[bot]"},
    }
    with patch.object(subject, "github", return_value=json.dumps([[comment]])) as api:
        subject.publish(update.model_copy(update={"state": state}), None)
    api.assert_called_once()


def test_api_writes_body_as_json_without_shell_interpolation() -> None:
    body = "Literal `code` and $(not a command)\nNext line"
    with patch.object(subject.subprocess, "check_output", return_value="{}") as command:
        subject.github("repos/Further-AI/skills/issues/13/comments", method="POST", body=body)
    assert json.loads(command.call_args.kwargs["input"]) == {"body": body}
    assert command.call_args.args[0][-2:] == ["--input", "-"]


def test_cli_reads_summary_and_passes_validated_identity(
    tmp_path: Path, summary: subject.Summary
) -> None:
    path = tmp_path / "summary.json"
    path.write_text(summary.model_dump_json())
    with (
        patch(
            "sys.argv",
            [
                "comment",
                "--pr",
                "13",
                "--commit",
                "a" * 40,
                "--state",
                "success",
                "--run",
                "100",
                "--attempt",
                "1",
                "--backend-run",
                "456",
                "--summary",
                str(path),
            ],
        ),
        patch.object(subject, "publish") as publish,
    ):
        subject.main()
    assert publish.call_args.args[0].backend_run == 456
    assert publish.call_args.args[1] == summary
