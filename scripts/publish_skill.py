"""Upload all validated bundles, then activate the complete repository catalog."""

import argparse
import os
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Annotated, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from scripts.skill_catalog import SkillCatalog, SkillName

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CIContext(BaseModel):
    """GitHub's identity and OIDC credentials for a release from main."""

    model_config = ConfigDict(strict=True)

    repository: Literal["Further-AI/furtherai-skills", "Further-AI/skills"] = Field(alias="GITHUB_REPOSITORY")
    commit_sha: str = Field(alias="GITHUB_SHA", pattern=r"^[0-9a-f]{40}$")
    ref: Literal["refs/heads/main"] = Field(alias="GITHUB_REF")
    event: Literal["push", "workflow_dispatch"] = Field(alias="GITHUB_EVENT_NAME")
    token_url: str = Field(alias="ACTIONS_ID_TOKEN_REQUEST_URL", min_length=1)
    request_token: SecretStr = Field(alias="ACTIONS_ID_TOKEN_REQUEST_TOKEN", min_length=1)

    @field_validator("repository")
    @classmethod
    def storage_repository(cls, repository: str) -> Literal["Further-AI/furtherai-skills"]:
        """Keep existing bundle provenance stable across the GitHub repository rename."""
        return "Further-AI/furtherai-skills"


class IdentityToken(BaseModel):
    """The short-lived token returned by GitHub's OIDC endpoint."""

    value: SecretStr = Field(min_length=1)


class PublishedVersion(BaseModel):
    """The immutable version and stable digest observed by the publishing API."""

    name: SkillName
    content_digest: Digest
    stable_digest: Digest | None


class CatalogRelease(BaseModel):
    """The complete mapping confirmed by the backend after atomic activation."""

    revision: UUID
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    skills: dict[SkillName, Digest]
    enabled_skills: list[SkillName] | None = None


class CatalogActivationRequest(BaseModel):
    """Activate every bundle together at the revision observed before uploading."""

    skills: dict[SkillName, Digest]
    enabled_skills: list[SkillName]
    expected_revision: UUID | None


class PublishingError(Exception):
    """A release failed before it could confirm the requested stable version."""


class _NoRedirects(HTTPRedirectHandler):
    """Keep bearer credentials at their intended endpoint."""

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        return None


def _request(request: Request, operation: str) -> bytes:
    """Send one request, reporting failures without tokens or response bodies."""
    try:
        with build_opener(_NoRedirects()).open(request, timeout=120) as response:
            return response.read()
    except HTTPError as exc:
        raise PublishingError(f"{operation} failed (HTTP {exc.code}).") from None
    except (URLError, TimeoutError) as exc:
        raise PublishingError(f"{operation} could not reach the server ({type(exc).__name__}).") from None


def _check_url(url: str) -> None:
    """Require HTTPS before sending credentials."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise PublishingError("Publishing and OIDC URLs must use HTTPS without credentials or fragments.")


def _multipart(bundle: Path, context: CIContext) -> tuple[bytes, str]:
    """Encode provenance and the exact ZIP bytes produced by validation."""
    boundary = uuid4().hex
    parts = []
    for name, value in {
        "repository": context.repository,
        "commit_sha": context.commit_sha,
    }.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="bundle"; filename="skill.zip"\r\n'
        "Content-Type: application/zip\r\n\r\n".encode()
        + bundle.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _identity_token(context: CIContext, audience: str) -> str:
    """Request a GitHub token scoped to the publishing backend."""
    token_parts = urlsplit(context.token_url)
    query = [(key, value) for key, value in parse_qsl(token_parts.query) if key != "audience"]
    token_url = urlunsplit(token_parts._replace(query=urlencode([*query, ("audience", audience)])))
    token_response = _request(
        Request(
            token_url,
            headers={
                "Authorization": f"Bearer {context.request_token.get_secret_value()}",
            },
        ),
        "OIDC authentication",
    )
    return IdentityToken.model_validate_json(token_response).value.get_secret_value()


def publish_catalog(
    directory: Path,
    *,
    api_url: str,
    audience: str,
    context: CIContext,
) -> CatalogRelease:
    """Upload a complete artifact and activate it once; conflicts stop the release.

    A manifest distinguishes an empty repository from a missing download. Failed
    uploads leave the current catalog intact. Never retry with a newer revision:
    doing so could overwrite a competing publisher's release.

    Args:
        directory: Complete packaged catalog artifact.
        api_url: Target environment's backend URL.
        audience: OIDC audience configured for that backend.
        context: Verified GitHub job configuration.

    Returns:
        The release confirmed by the backend, with every packaged skill enabled.
    """
    _check_url(api_url)
    _check_url(context.token_url)
    if urlsplit(api_url).query or not audience:
        raise PublishingError("Set the API base URL without a query and a nonempty OIDC audience.")
    catalog = SkillCatalog.model_validate_json((directory / "catalog.json").read_bytes())
    expected_files = {"catalog.json", *(f"{name}.zip" for name in catalog.skills)}
    if {path.name for path in directory.iterdir()} != expected_files:
        raise PublishingError("Artifact files do not match the complete catalog manifest.")
    enabled_skills = catalog.skills
    token = _identity_token(context, audience)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"{api_url.rstrip('/')}/api/v1/internal/skills"
    current = TypeAdapter(CatalogRelease | None).validate_json(
        _request(Request(f"{base_url}/catalog", headers=headers), "Reading catalog")
    )
    skills: dict[str, str] = {}
    for name in catalog.skills:
        body, content_type = _multipart(directory / f"{name}.zip", context)
        response = _request(
            Request(
                f"{base_url}/{name}/versions",
                data=body,
                headers={**headers, "Content-Type": content_type},
                method="POST",
            ),
            "Publishing",
        )
        published = PublishedVersion.model_validate_json(response)
        if published.name != name:
            raise PublishingError("Publishing returned a different skill name.")
        skills[name] = published.content_digest
    activation = CatalogActivationRequest(
        skills=skills,
        enabled_skills=enabled_skills,
        expected_revision=current.revision if current else None,
    )
    response = _request(
        Request(
            f"{base_url}/catalog",
            data=activation.model_dump_json().encode(),
            headers={**headers, "Content-Type": "application/json"},
            method="PUT",
        ),
        "Catalog activation",
    )
    release = CatalogRelease.model_validate_json(response)
    if release.skills != skills or release.commit_sha != context.commit_sha or release.enabled_skills != enabled_skills:
        raise PublishingError("Activation returned a different repository snapshot.")
    return release


def main() -> None:
    """Publish from GitHub Actions using its short-lived OIDC identity."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--audience", required=True)
    args = parser.parse_args()
    try:
        context = CIContext.model_validate(dict(os.environ))
        release = publish_catalog(
            args.directory,
            api_url=args.api_url,
            audience=args.audience,
            context=context,
        )
    except ValidationError:
        parser.exit(1, "Invalid CI configuration or publishing API response.\n")
    except (PublishingError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"Activated {len(release.skills)} skills from {release.commit_sha}: {release.revision}")


if __name__ == "__main__":
    main()
