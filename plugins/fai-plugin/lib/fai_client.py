"""Shared FurtherAI platform HTTP client for all fai plugin skills.

Python 3.9+ stdlib only — no pip installs required. Import from a skill script:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
    from fai_client import FaiClient

Credentials come from ~/.fai/credentials.env (written by the fai:setup skill).
Environment variables of the same names override the file. Never print secrets.

Auth — two flows, chosen automatically per environment:
  1. Restricted skill service key (FAI_SKILL_{ENV}_PERSONAL_API_KEY set):
     POST /api/v1/oauth2/skill_access_token {"client_secret": key}
     Org scoping happens per-request via the X-FAI-Target-Org-Id header.
     Cross-org capable, but only for provisioned restricted service users.
  2. Personal API key (FAI_{ENV}_PERSONAL_API_KEY + FAI_{ENV}_USER_ID set):
     POST /api/v1/oauth2/access_token
       {"client_id": user_id, "client_secret": key, "active_org_id": org_id}
     Token is org-scoped at mint time; acts only as its owner and only in
     orgs the owner is a member of. Default path for most users.

Environment gotchas this module encodes so callers don't have to:
  - Staging and prod are SEPARATE PropelAuth instances. Keys, user IDs, and
    org IDs never cross environments.
  - Prod API is reached via https://app.furtherai.com (Front Door routes
    /api/v1/* to the prod backend). The prod backend's raw Azure hostname
    contains the word "staging" — never infer environment from hostnames.
  - app-staging.furtherai.com serves the SPA for /api/v1 paths; staging API
    calls must go to the staging backend host directly.
  - Executions have FOUR terminal statuses, not two.
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

CONFIG_DIR = Path(os.environ.get("FAI_CONFIG_DIR", str(Path.home() / ".fai")))
CREDENTIALS_FILE = CONFIG_DIR / "credentials.env"

DEFAULT_URLS = {
    "prod": {
        "api": "https://app.furtherai.com",
        "app": "https://app.furtherai.com",
    },
    "staging": {
        "api": "https://fai-dev-backend.azurewebsites.net",
        "app": "https://app-staging.furtherai.com",
    },
    "local": {
        "api": "http://localhost:8000",
        "app": "http://localhost:3000",
    },
}

ENVIRONMENTS = tuple(DEFAULT_URLS)

# LROStatus — the full enum, wider than the public docs admit.
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "terminated"})
NON_TERMINAL_STATUSES = frozenset({"not_started", "running", "paused", "blocked", "suspended"})

TOKEN_MINT_ATTEMPTS = 6  # mint endpoint 500s under load; linear backoff


class FaiError(RuntimeError):
    """API or configuration error with an operator-readable hint."""

    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def load_credentials() -> dict[str, str]:
    """KEY=VALUE file parser; process env vars override file values."""
    creds: dict[str, str] = {}
    if CREDENTIALS_FILE.exists():
        for line in CREDENTIALS_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            creds[key.strip()] = value.strip().strip('"').strip("'")
    for key in list(os.environ):
        if key.startswith(("FAI_", "PROD_", "STAGING_", "LOCAL_")):
            creds[key] = os.environ[key]
    return creds


def _hint_for(status: int, env: str, org_id: Optional[str]) -> str:
    if status == 400:
        return ("400 can mean a bad/missing token on public builder endpoints "
                "(they return 400, not 401) or a malformed X-FAI-Target-Org-Id.")
    if status == 401:
        return "Token expired or invalid — re-minting once is automatic; if this persists, run fai:setup."
    if status == 403:
        return ("Route not on the restricted-skill allowlist, or the user lacks fai_admin "
                "in the target org. Authoring (create/upload/publish) needs fai_admin.")
    if status == 404:
        return (f"Wrong ID, wrong org (org_id={org_id!r}), or caller not a member of the org. "
                f"Remember: staging and prod are separate PropelAuth instances — "
                f"a {env} ID does not exist in the other environment.")
    if status == 422:
        return "Malformed request body — check field names against the skill's documented shapes."
    if status == 429:
        return "Rate limited — honor Retry-After."
    return ""


class FaiClient:
    def __init__(
        self,
        env: str = "prod",
        org_id: Optional[str] = None,
        skill_name: str = "fai",
        timeout: int = 60,
    ):
        if env not in ENVIRONMENTS:
            raise FaiError(f"unknown env {env!r}; expected one of {sorted(ENVIRONMENTS)}")
        self.env = env
        self.org_id = org_id
        self.skill_name = skill_name
        self.timeout = timeout
        self._token: Optional[str] = None
        self._token_org: Optional[str] = None  # org the personal token was minted for

        creds = load_credentials()
        prefix = env.upper()
        self.api_url = creds.get(f"{prefix}_API_URL") or DEFAULT_URLS[env]["api"]
        self.app_url = creds.get(f"{prefix}_APP_URL") or DEFAULT_URLS[env]["app"]
        self._skill_key = creds.get(f"FAI_SKILL_{prefix}_PERSONAL_API_KEY") or ""
        self._personal_key = (creds.get(f"FAI_{prefix}_PERSONAL_API_KEY")
                              or creds.get(f"{prefix}_PERSONAL_API_KEY") or "")
        self._user_id = (creds.get(f"FAI_{prefix}_USER_ID")
                         or creds.get(f"{prefix}_USER_ID") or "")

    # ------------------------------------------------------------------ auth

    @property
    def restricted(self) -> bool:
        return bool(self._skill_key)

    def _attribution(self) -> dict[str, str]:
        return {
            "X-FAI-Skill-Name": self.skill_name,
            "X-FAI-Skill-Hostname": socket.gethostname(),
            "X-FAI-Skill-Invocation-Id": str(uuid.uuid4()),
        }

    def _mint(self) -> str:
        if self.restricted:
            url = f"{self.api_url}/api/v1/oauth2/skill_access_token"
            body = {"client_secret": self._skill_key}
        else:
            if not (self._personal_key and self._user_id):
                raise FaiError(
                    f"No credentials for env {self.env!r}. Run the fai:setup skill to write "
                    f"{CREDENTIALS_FILE} (needs FAI_{self.env.upper()}_PERSONAL_API_KEY and "
                    f"FAI_{self.env.upper()}_USER_ID, or a FAI_SKILL_* restricted key)."
                )
            if not self.org_id:
                raise FaiError(
                    "org_id is required for personal-key auth (token is org-scoped at mint). "
                    "Pass --org-id or --account so it can be resolved from accounts.json."
                )
            url = f"{self.api_url}/api/v1/oauth2/access_token"
            body = {
                "client_id": self._user_id,
                "client_secret": self._personal_key,
                "active_org_id": self.org_id,
            }
        last = ""
        for attempt in range(TOKEN_MINT_ATTEMPTS):
            try:
                data = self._raw_json_request("POST", url, body, headers=self._attribution())
                token = data["access_token"] if isinstance(data, dict) else data
                self._token = token
                self._token_org = self.org_id
                return token
            except FaiError as e:
                if e.status in (400, 401, 403):
                    raise FaiError(
                        f"token mint rejected (HTTP {e.status}) for env {self.env!r}: "
                        f"key invalid, wrong environment, or (restricted flow) user not "
                        f"provisioned as a restricted skill service user. {e.body[:200]}",
                        status=e.status,
                    )
                last = f"HTTP {e.status}: {e.body[:120]}"
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last = str(e)[:120]
            time.sleep(2 * (attempt + 1))
        raise FaiError(f"token mint failed after {TOKEN_MINT_ATTEMPTS} attempts: {last}")

    def _ensure_token(self) -> str:
        # Personal tokens are org-scoped: re-mint if org changed.
        if self._token and (self.restricted or self._token_org == self.org_id):
            return self._token
        return self._mint()

    def verify_auth(self) -> str:
        """Mint (or reuse) a token; returns the flow used ('restricted' or
        'personal'). Public hook for setup/doctor checks."""
        self._ensure_token()
        return "restricted" if self.restricted else "personal"

    # ------------------------------------------------------------- transport

    def _raw_json_request(self, method: str, url: str, body: Any = None,
                          headers: Optional[dict] = None, timeout: Optional[int] = None) -> Any:
        payload = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=payload, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                ctype = resp.headers.get("Content-Type", "")
                return json.loads(raw) if "json" in ctype or raw[:1] in (b"{", b"[") else raw.decode()
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")[:1000]
            raise FaiError(
                f"{method} {url} -> HTTP {e.code}. {_hint_for(e.code, self.env, self.org_id)}",
                status=e.code, body=body_text,
            )

    def request(self, method: str, path: str, body: Any = None,
                params: Optional[dict] = None, timeout: Optional[int] = None,
                extra_headers: Optional[dict] = None, _retried: bool = False) -> Any:
        """Call an API path (e.g. '/api/v1/workflows/{id}') with auth + attribution.

        extra_headers: additional request headers (e.g. the eval upload-session
        token header); they override the defaults on key collision.
        """
        token = self._ensure_token()
        url = f"{self.api_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean, doseq=True)
        headers = {**self._attribution(), "Authorization": f"Bearer {token}"}
        if self.restricted:
            if not self.org_id:
                raise FaiError("org_id required: restricted tokens scope per-request "
                               "via X-FAI-Target-Org-Id.")
            headers["X-FAI-Target-Org-Id"] = self.org_id
        if extra_headers:
            headers.update(extra_headers)
        try:
            return self._raw_json_request(method, url, body, headers, timeout)
        except FaiError as e:
            if e.status == 401 and not _retried:
                self._token = None
                return self.request(method, path, body, params, timeout,
                                    extra_headers, _retried=True)
            raise

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("POST", path, body, **kw)

    def put(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("PUT", path, body, **kw)

    def patch(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("PATCH", path, body, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)

    # ------------------------------------------------------------- documents

    def upload_document(self, file_path: str | Path) -> str:
        """Register + upload one local file; returns user_document_id.

        owner_uid/owner_oid MUST be empty strings — the API derives them from
        the token and 400s otherwise.
        """
        path = Path(file_path)
        reg = self.post(
            "/api/v1/user-documents-upload",
            {
                "filename": path.name,
                "blob_url": "",
                "category": "",
                "origin": "upload",
                "run_id": "",
                "owner_uid": "",
                "owner_oid": "",
                "description": "",
            },
        )
        sas_url = reg["sas_url"]
        doc_id = reg.get("user_document_id") or reg.get("id")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        req = urllib.request.Request(sas_url, data=path.read_bytes(), method="PUT")
        req.add_header("x-ms-blob-type", "BlockBlob")
        req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=300):
                pass
        except urllib.error.HTTPError as e:
            raise FaiError(f"blob PUT failed for {path.name}: HTTP {e.code}", status=e.code)
        return doc_id

    def download_document(self, user_document_id: str, out_path: str | Path) -> Path:
        """Resolve a ~24h SAS URL and stream the document to out_path."""
        resp = self.post("/api/v1/user-documents-download",
                         {"user_document_id": user_document_id})
        sas_url = resp["sas_url"] if isinstance(resp, dict) else resp
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(sas_url, timeout=300) as r, open(out, "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
        return out

    # ------------------------------------------------------------ executions

    def execution_log(self, execution_id: str) -> dict:
        return self.get(f"/api/v1/workflow-execution/logs/{execution_id}")

    def poll_execution(self, execution_id: str, interval: int = 10,
                       timeout: int = 3600, quiet: bool = False) -> dict:
        """Poll until a TERMINAL status (all four of them) or timeout."""
        deadline = time.monotonic() + timeout
        while True:
            log = self.execution_log(execution_id)
            status = log.get("status")
            if status in TERMINAL_STATUSES:
                return log
            if time.monotonic() > deadline:
                raise FaiError(f"execution {execution_id} still {status!r} after {timeout}s")
            if not quiet:
                print(f"  status={status} ...", file=sys.stderr)
            time.sleep(interval)

    # ----------------------------------------------------------------- links

    def workflow_url(self, workflow_id: str) -> str:
        return f"{self.app_url}/workflows/{workflow_id}"

    def execution_url(self, execution_id: str) -> str:
        return f"{self.app_url}/workflow-execution/{execution_id}"

    def dataset_url(self, dataset_id: str) -> str:
        return f"{self.app_url}/eval/{dataset_id}"
