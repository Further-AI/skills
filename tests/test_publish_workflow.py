"""Exercise CI packaging, publishing failures, and stale-commit protection."""

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
import yaml


def run_workflow_step(
    job: str,
    name: str,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run the workflow's actual step with GitHub Actions' fail-fast shell behavior."""
    workflow = yaml.safe_load((Path(__file__).parents[1] / ".github/workflows/validate.yml").read_text())
    command = next(step["run"] for step in workflow["jobs"][job]["steps"] if step.get("name") == name)
    return subprocess.run(
        ["bash", "-e", "-c", command],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def workflow_environment(tmp_path: Path) -> dict[str, str]:
    """Use the test interpreter while retaining the workflow's real module entry points."""
    executable = tmp_path / "uv"
    executable.write_text('#!/bin/sh\nshift\nshift\nexec "$TEST_PYTHON" "$@"\n')
    executable.chmod(0o755)
    (tmp_path / "scripts").mkdir()
    return {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "TEST_PYTHON": sys.executable,
    }


@pytest.mark.parametrize("invalid", [False, True])
def test_workflow_packages_all_skills_and_rejects_invalid_content(
    tmp_path: Path,
    workflow_environment: dict[str, str],
    invalid: bool,
) -> None:
    for filename in ["skill_bundle.py", "skill_catalog.py"]:
        source = Path(__file__).parents[1] / "scripts" / filename
        (tmp_path / "scripts" / filename).write_bytes(source.read_bytes())
    names = ("first-skill", "second-skill", "third-skill")
    for name in names:
        directory = tmp_path / "skills" / name
        directory.mkdir(parents=True)
        content = f"---\nname: {name}\ndescription: Test instructions.\n---\nRead the input.\n"
        if invalid and name == "second-skill":
            content = "Missing metadata"
        (directory / "SKILL.md").write_text(content)
    result = run_workflow_step("validate", "Package skills", tmp_path, workflow_environment)
    assert result.returncode == (1 if invalid else 0), result.stderr
    expected = ["first-skill.zip"] if invalid else ["catalog.json", *(f"{name}.zip" for name in names)]
    assert sorted(path.name for path in (tmp_path / "dist").iterdir()) == expected


@pytest.mark.parametrize("fail_second", [False, True])
def test_workflow_publishes_catalog_once_and_propagates_failure(
    tmp_path: Path,
    workflow_environment: dict[str, str],
    fail_second: bool,
) -> None:
    (tmp_path / "dist").mkdir()
    for name in ("a", "b", "c"):
        (tmp_path / "dist" / f"{name}.zip").write_bytes(name.encode())
    (tmp_path / "scripts/publish_skill.py").write_text(
        dedent("""\
            import os
            import sys
            from pathlib import Path

            with Path("calls.txt").open("a") as calls:
                calls.write(sys.argv[1] + "\\n")
            if os.environ["FAIL_SECOND"] == "true":
                sys.exit(1)
        """)
    )
    result = run_workflow_step(
        "publish",
        "Publish complete catalog",
        tmp_path,
        env={
            **workflow_environment,
            "FAIL_SECOND": str(fail_second).lower(),
            "SKILLS_API_URL": "https://example.test",
        },
    )
    assert result.returncode == (1 if fail_second else 0), result.stderr
    expected = ["dist"]
    assert (tmp_path / "calls.txt").read_text().splitlines() == expected


@pytest.mark.parametrize("stale", [False, True])
def test_publish_workflow_rejects_stale_main(tmp_path: Path, stale: bool) -> None:
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"

    def git(*args: str, cwd: Path = tmp_path) -> str:
        return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.STDOUT, text=True).strip()

    git("init", "--bare", str(remote))
    git("clone", str(remote), str(checkout))
    git("checkout", "-b", "main", cwd=checkout)
    for revision in ("first", "second"):
        (checkout / "skill.txt").write_text(revision)
        git("add", "skill.txt", cwd=checkout)
        git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            revision,
            cwd=checkout,
        )
    git("push", "origin", "main", cwd=checkout)
    sha = git("rev-parse", "HEAD~1" if stale else "HEAD", cwd=checkout)
    result = run_workflow_step(
        "publish",
        "Require the current main commit",
        checkout,
        env={**os.environ, "GITHUB_SHA": sha},
    )
    assert result.returncode == (1 if stale else 0)
    if stale:
        assert "A newer commit is on main" in result.stdout


def test_workflow_packages_empty_repository_after_last_skill_is_deleted(
    tmp_path: Path,
    workflow_environment: dict[str, str],
) -> None:
    for filename in ["skill_bundle.py", "skill_catalog.py"]:
        source = Path(__file__).parents[1] / "scripts" / filename
        (tmp_path / "scripts" / filename).write_bytes(source.read_bytes())
    result = run_workflow_step("validate", "Package skills", tmp_path, workflow_environment)
    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in (tmp_path / "dist").iterdir()) == ["catalog.json"]
    assert (tmp_path / "dist/catalog.json").read_text() == '{"skills":[]}'


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled", "timed_out"])
def test_evaluation_wait_propagates_backend_result(
    tmp_path: Path, conclusion: str
) -> None:
    """Exercise the actual wait step; unsuccessful backend runs must block release."""
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'test "$1 $2 $3" = "run watch 123" || exit 2\n'
        'case " $* " in *" --exit-status "*) ;; *) exit 3;; esac\n'
        'test "$TEST_CONCLUSION" = success\n'
    )
    gh.chmod(0o755)
    result = run_workflow_step(
        "evaluate",
        "Wait for evaluation to pass",
        tmp_path,
        {
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "RUN_ID": "123",
            "TEST_CONCLUSION": conclusion,
        },
    )
    assert (result.returncode == 0) == (conclusion == "success")


@pytest.mark.parametrize(
    "response, succeeds",
    [
        ('{"workflow_run_id": 123}', True),
        ("{}", False),
        ('{"workflow_run_id": "wrong"}', False),
        ('{"workflow_run_id": 0}', False),
    ],
)
def test_dispatch_requires_the_returned_run_identity(
    tmp_path: Path, response: str, succeeds: bool
) -> None:
    """A missing or malformed dispatch response cannot reuse an older passing run."""
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s" "$TEST_RESPONSE"\n')
    gh.chmod(0o755)
    output = tmp_path / "output"
    result = run_workflow_step(
        "evaluate",
        "Start evaluation for this exact merge",
        tmp_path,
        {
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "SKILLS_COMMIT": "a" * 40,
            "TEST_RESPONSE": response,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        },
    )
    assert (result.returncode == 0) == succeeds
    if succeeds:
        assert output.read_text().strip() == "run_id=123"
    else:
        assert not output.exists()


def test_publishing_requires_validation_and_evaluation() -> None:
    """Neither a failed nor a skipped evaluation may bypass the publish dependency."""
    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / ".github/workflows/validate.yml").read_text()
    )
    jobs = workflow["jobs"]
    assert set(jobs["publish"]["needs"]) == {"validate", "evaluate"}
    assert "always()" not in jobs["publish"]["if"]
    assert "continue-on-error" not in jobs["evaluate"]
    assert jobs["evaluate"]["needs"] == "validate"
