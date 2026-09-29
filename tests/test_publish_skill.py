"""Complete artifacts activate once; upload and transport failures never promote."""

import json
import sys
from collections.abc import Iterator
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Literal
from unittest.mock import Mock, patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest
from pydantic import ValidationError

from scripts import publish_skill
from scripts.skill_catalog import package_catalog

DIGEST = "a" * 64
PREVIOUS = "b" * 64
API_URL = "https://backend.example"
AUDIENCE = "furtherai-skills-us-staging"


@pytest.fixture
def context() -> publish_skill.CIContext:
    """Provide GitHub's environment shape without real credentials."""
    return publish_skill.CIContext.model_validate(
        {
            "GITHUB_REPOSITORY": "Further-AI/furtherai-skills",
            "GITHUB_SHA": "c" * 40,
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "push",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://github.example/token?request=1&audience=old",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
        }
    )


@pytest.fixture
def artifact(tmp_path: Path) -> Path:
    """Package two real skills to exercise partial upload failures."""
    source = tmp_path / "skills"
    for name in ["document-extraction", "policy-comparison"]:
        skill = source / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Extract fields.\n---\nExtract.\n")
    (tmp_path / "availability.yaml").write_text("staging: {enabled_skills: []}\nproduction: {enabled_skills: []}\n")
    output = tmp_path / "dist"
    package_catalog(source, output)
    return output


def release(skills: dict[str, str]) -> bytes:
    """Return the backend's catalog shape, including its concurrency revision."""
    return json.dumps(
        {
            "revision": "11111111-1111-4111-8111-111111111111",
            "commit_sha": "c" * 40,
            "skills": skills,
            "enabled_skills": [],
        }
    ).encode()


@pytest.fixture
def request_mock() -> Iterator[Mock]:
    """Keep external transport isolated while preserving the real publisher flow."""
    with patch.object(publish_skill, "_request") as mocked:
        mocked.side_effect = [
            b'{"value":"identity-token"}',
            b"null",
            json.dumps(
                {
                    "name": "document-extraction",
                    "content_digest": DIGEST,
                    "stable_digest": None,
                }
            ).encode(),
            json.dumps(
                {
                    "name": "policy-comparison",
                    "content_digest": PREVIOUS,
                    "stable_digest": None,
                }
            ).encode(),
            release({"document-extraction": DIGEST, "policy-comparison": PREVIOUS}),
        ]
        yield mocked


def test_publish_catalog_uploads_every_artifact_before_one_activation(
    context: publish_skill.CIContext,
    artifact: Path,
    request_mock: Mock,
) -> None:
    result = publish_skill.publish_catalog(
        artifact,
        api_url=API_URL,
        audience=AUDIENCE,
        context=context,
        environment="staging",
    )
    assert result.skills == {
        "document-extraction": DIGEST,
        "policy-comparison": PREVIOUS,
    }
    token, current, first, second, activation = [call.args[0] for call in request_mock.call_args_list]
    assert parse_qs(urlsplit(token.full_url).query) == {
        "request": ["1"],
        "audience": [AUDIENCE],
    }
    assert token.get_header("Authorization") == "Bearer request-token"
    assert current.full_url == f"{API_URL}/api/v1/internal/skills/catalog"
    assert current.get_method() == "GET"
    for upload, name in [(first, "document-extraction"), (second, "policy-comparison")]:
        assert upload.method == "POST"
        assert upload.full_url == f"{API_URL}/api/v1/internal/skills/{name}/versions"
        assert upload.get_header("Authorization") == "Bearer identity-token"
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {upload.get_header('Content-type')}\r\n\r\n".encode() + upload.data
        )
        parts = {
            part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
            for part in message.iter_parts()
        }
        assert parts == {
            "repository": context.repository.encode(),
            "commit_sha": context.commit_sha.encode(),
            "bundle": (artifact / f"{name}.zip").read_bytes(),
        }
    assert activation.method == "PUT"
    assert activation.full_url == current.full_url
    assert json.loads(activation.data) == {
        "skills": result.skills,
        "expected_revision": None,
        "enabled_skills": [],
    }


@pytest.mark.parametrize("failure_step", range(5))
def test_publish_catalog_stops_on_failure_without_retrying(
    context: publish_skill.CIContext,
    artifact: Path,
    request_mock: Mock,
    failure_step: int,
) -> None:
    responses = list(request_mock.side_effect)
    request_mock.side_effect = [
        *responses[:failure_step],
        publish_skill.PublishingError("HTTP 409"),
    ]
    with pytest.raises(publish_skill.PublishingError, match="HTTP 409"):
        publish_skill.publish_catalog(
            artifact,
            api_url=API_URL,
            audience=AUDIENCE,
            context=context,
            environment="staging",
        )
    assert request_mock.call_count == failure_step + 1


@pytest.mark.parametrize("invalid", [b"not JSON", b"{}", b'{"name":"wrong","content_digest":"invalid"}'])
def test_invalid_upload_response_blocks_activation(
    context: publish_skill.CIContext,
    artifact: Path,
    request_mock: Mock,
    invalid: bytes,
) -> None:
    request_mock.side_effect = [b'{"value":"identity-token"}', b"null", invalid]
    with pytest.raises((ValidationError, publish_skill.PublishingError)):
        publish_skill.publish_catalog(
            artifact,
            api_url=API_URL,
            audience=AUDIENCE,
            context=context,
            environment="staging",
        )
    assert request_mock.call_count == 3


def test_empty_catalog_retires_all_at_observed_revision(
    context: publish_skill.CIContext,
    tmp_path: Path,
    request_mock: Mock,
) -> None:
    source = tmp_path / "empty"
    source.mkdir()
    artifact = tmp_path / "dist"
    (tmp_path / "availability.yaml").write_text("staging: {enabled_skills: []}\nproduction: {enabled_skills: []}\n")
    package_catalog(source, artifact)
    request_mock.side_effect = [
        b'{"value":"identity-token"}',
        release({"old": DIGEST}),
        release({}),
    ]
    assert (
        publish_skill.publish_catalog(
            artifact,
            api_url=API_URL,
            audience=AUDIENCE,
            context=context,
            environment="staging",
        ).skills
        == {}
    )
    activation = request_mock.call_args.args[0]
    assert json.loads(activation.data) == {
        "skills": {},
        "enabled_skills": [],
        "expected_revision": "11111111-1111-4111-8111-111111111111",
    }
    assert request_mock.call_count == 3


@pytest.mark.parametrize(
    "problem",
    ["missing_zip", "extra_zip", "missing_manifest", "invalid_name", "duplicate"],
)
def test_incomplete_artifact_is_rejected_before_network(
    context: publish_skill.CIContext,
    artifact: Path,
    request_mock: Mock,
    problem: str,
) -> None:
    if problem == "missing_zip":
        (artifact / "policy-comparison.zip").unlink()
    elif problem == "extra_zip":
        (artifact / "extra.zip").write_bytes(b"extra")
    elif problem == "missing_manifest":
        (artifact / "catalog.json").unlink()
    else:
        names = ["../bad"] if problem == "invalid_name" else ["same", "same"]
        (artifact / "catalog.json").write_text(json.dumps({"skills": names}))
    with pytest.raises((OSError, ValidationError, publish_skill.PublishingError)):
        publish_skill.publish_catalog(
            artifact,
            api_url=API_URL,
            audience=AUDIENCE,
            context=context,
            environment="staging",
        )
    request_mock.assert_not_called()


def test_mismatched_activation_is_not_reported_as_success(
    context: publish_skill.CIContext,
    artifact: Path,
    request_mock: Mock,
) -> None:
    responses = list(request_mock.side_effect)
    request_mock.side_effect = [*responses[:-1], release({})]
    with pytest.raises(publish_skill.PublishingError, match="different repository snapshot"):
        publish_skill.publish_catalog(
            artifact,
            api_url=API_URL,
            audience=AUDIENCE,
            context=context,
            environment="staging",
        )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://backend.example",
        "https://user:pass@backend.example",
        "https://backend.example/#fragment",
        "https://backend.example/?query=1",
    ],
)
def test_invalid_api_url_never_sends_credentials(
    context: publish_skill.CIContext,
    artifact: Path,
    request_mock: Mock,
    url: str,
) -> None:
    with pytest.raises(publish_skill.PublishingError):
        publish_skill.publish_catalog(
            artifact,
            api_url=url,
            audience=AUDIENCE,
            context=context,
            environment="staging",
        )
    request_mock.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("GITHUB_REPOSITORY", "someone/skills"),
        ("GITHUB_SHA", "main"),
        ("GITHUB_REF", "refs/heads/feature"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("ACTIONS_ID_TOKEN_REQUEST_TOKEN", ""),
    ],
)
def test_context_rejects_untrusted_identity(context: publish_skill.CIContext, field: str, value: str) -> None:
    values = context.model_dump(by_alias=True)
    values[field] = value
    with pytest.raises(ValidationError):
        publish_skill.CIContext.model_validate(values)


@pytest.mark.parametrize(
    "token_url",
    ["http://github.example/token", "https://token:secret@github.example/token"],
)
def test_invalid_oidc_url_never_sends_credentials(
    context: publish_skill.CIContext,
    artifact: Path,
    request_mock: Mock,
    token_url: str,
) -> None:
    context.token_url = token_url
    with pytest.raises(publish_skill.PublishingError):
        publish_skill.publish_catalog(
            artifact,
            api_url=API_URL,
            audience=AUDIENCE,
            context=context,
            environment="staging",
        )
    request_mock.assert_not_called()


@pytest.mark.parametrize("failure", [False, True])
def test_main_reports_result_without_tokens(
    context: publish_skill.CIContext,
    artifact: Path,
    request_mock: Mock,
    capsys: pytest.CaptureFixture[str],
    failure: bool,
) -> None:
    environment = context.model_dump(by_alias=True)
    environment["ACTIONS_ID_TOKEN_REQUEST_TOKEN"] = "secret-token"
    if failure:
        request_mock.side_effect = [b'{"value":"secret-token"}', b"secret-token"]
    with (
        patch.dict(publish_skill.os.environ, environment, clear=True),
        patch.object(
            sys,
            "argv",
            [
                "publish_skill.py",
                str(artifact),
                "--api-url",
                API_URL,
                "--environment",
                "staging",
                "--audience",
                AUDIENCE,
            ],
        ),
    ):
        if failure:
            with pytest.raises(SystemExit) as error:
                publish_skill.main()
            assert error.value.code == 1
        else:
            publish_skill.main()
    output = capsys.readouterr()
    assert "secret-token" not in output.err + output.out
    assert ("Invalid CI configuration" in output.err) if failure else ("Activated 2 skills" in output.out)


@pytest.fixture
def http_server() -> Iterator[tuple[str, list[str]]]:
    """Serve controlled responses to exercise urllib's actual redirect handling."""
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            paths.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/secret")
            else:
                self.send_response(int(self.path.removeprefix("/")))
            self.end_headers()
            self.wfile.write(b"secret-response-token")

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", paths
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("status", [200, 201])
def test_request_returns_successful_response(http_server: tuple[str, list[str]], status: int) -> None:
    url, paths = http_server
    assert publish_skill._request(Request(f"{url}/{status}"), "Publishing") == b"secret-response-token"
    assert paths == [f"/{status}"]


@pytest.mark.parametrize("path", ["/redirect", "/403", "/409", "/500"])
def test_request_rejects_redirects_and_hides_error_body(http_server: tuple[str, list[str]], path: str) -> None:
    url, paths = http_server
    with pytest.raises(publish_skill.PublishingError, match="Publishing failed \\(HTTP") as error:
        publish_skill._request(
            Request(f"{url}{path}", headers={"Authorization": "Bearer secret"}),
            "Publishing",
        )
    assert "secret" not in str(error.value)
    assert paths == [path]


@pytest.mark.parametrize("failure", [URLError("secret-host"), TimeoutError("secret-host")])
def test_request_network_failure_hides_connection_details(failure: Exception) -> None:
    with patch.object(publish_skill, "build_opener") as opener:
        opener.return_value.open.side_effect = failure
        with pytest.raises(publish_skill.PublishingError, match="could not reach") as error:
            publish_skill._request(Request(API_URL), "Publishing")
    assert "secret-host" not in str(error.value)


@pytest.mark.parametrize("environment,enabled", [("staging", ["document-extraction"]), ("production", [])])
def test_publish_uses_only_target_environment_availability(
    context: publish_skill.CIContext,
    artifact: Path,
    request_mock: Mock,
    environment: Literal["staging", "production"],
    enabled: list[str],
) -> None:
    manifest = json.loads((artifact / "catalog.json").read_text())
    manifest["availability"]["staging"]["enabled_skills"] = ["document-extraction"]
    (artifact / "catalog.json").write_text(json.dumps(manifest))
    responses = list(request_mock.side_effect)
    response = json.loads(responses[-1])
    response["enabled_skills"] = enabled
    request_mock.side_effect = [*responses[:-1], json.dumps(response).encode()]

    result = publish_skill.publish_catalog(
        artifact,
        api_url=API_URL,
        audience=AUDIENCE,
        context=context,
        environment=environment,
    )

    assert result.enabled_skills == enabled
    assert json.loads(request_mock.call_args.args[0].data)["enabled_skills"] == enabled
    assert len(result.skills) == 2


def test_publish_rejects_backend_ignoring_availability(
    context: publish_skill.CIContext, artifact: Path, request_mock: Mock
) -> None:
    responses = list(request_mock.side_effect)
    response = json.loads(responses[-1])
    del response["enabled_skills"]
    request_mock.side_effect = [*responses[:-1], json.dumps(response).encode()]
    with pytest.raises(publish_skill.PublishingError, match="different repository snapshot"):
        publish_skill.publish_catalog(
            artifact,
            api_url=API_URL,
            audience=AUDIENCE,
            context=context,
            environment="staging",
        )


@pytest.mark.parametrize(
    "availability",
    [
        {},
        {
            "staging": {"enabled_skills": ["missing"]},
            "production": {"enabled_skills": []},
        },
        {
            "staging": {"enabled_skills": ["document-extraction", "document-extraction"]},
            "production": {"enabled_skills": []},
        },
        {"staging": {"enabled_skills": []}, "production": {"enabled_skils": []}},
    ],
)
def test_invalid_availability_fails_before_network(
    context: publish_skill.CIContext,
    artifact: Path,
    request_mock: Mock,
    availability: dict[str, object],
) -> None:
    manifest = json.loads((artifact / "catalog.json").read_text())
    manifest["availability"] = availability
    (artifact / "catalog.json").write_text(json.dumps(manifest))
    with pytest.raises(ValidationError):
        publish_skill.publish_catalog(
            artifact,
            api_url=API_URL,
            audience=AUDIENCE,
            context=context,
            environment="staging",
        )
    request_mock.assert_not_called()


@pytest.mark.parametrize("repository", ["Further-AI/skills", "Further-AI/furtherai-skills"])
def test_context_keeps_storage_identity_across_repository_rename(
    context: publish_skill.CIContext, repository: str
) -> None:
    values = context.model_dump(by_alias=True)
    values["GITHUB_REPOSITORY"] = repository
    assert publish_skill.CIContext.model_validate(values).repository == "Further-AI/furtherai-skills"
