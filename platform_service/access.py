"""GitHub web sessions and project-scoped source grants."""

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from typing import Literal
from urllib.parse import urlencode, urlsplit

import httpx2 as httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi.responses import RedirectResponse, Response
from pydantic import Field, ValidationError, model_validator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .http import APIError
from .models import DTO, Entity, Id, Session


class ExecutionBinding(DTO):
    project_id: Id
    task_id: Id
    run_id: Id
    execution_id: Id


class SourceGrant(DTO):
    projects: set[Id] = Field(min_length=1)
    entity_types: set[Entity] = Field(min_length=1)
    authorities: set[Literal["self_report", "native_observation", "owner_decision", "formal_record"]] = Field(
        default_factory=lambda: {"self_report"}, min_length=1)
    token_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mcp_login: str | None = None
    execution: ExecutionBinding | None = None

    @model_validator(mode="after")
    def execution_credential(self):
        if self.execution is not None and (
            self.mcp_login or self.authorities != {"self_report"}
            or self.projects != {self.execution.project_id}
            or not self.entity_types <= {"task", "blocker"}
        ):
            raise ValueError("execution binding requires a dedicated HTTP self-report grant")
        return self


class Policy(DTO):
    viewers: dict[str, set[Id]] = Field(default_factory=dict)
    sources: dict[Id, SourceGrant] = Field(default_factory=dict)
    workflow_authors: dict[Id, dict[Id, Id]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def workflow_author_grants(self):
        for project, runs in self.workflow_authors.items():
            for source_id in runs.values():
                grant = self.sources.get(source_id)
                if (grant is None or project not in grant.projects or "workflow" not in grant.entity_types
                        or "native_observation" not in grant.authorities):
                    raise ValueError("workflow author requires a native source for the project")
        return self


@dataclass(frozen=True)
class AuthorizedSource:
    id: str
    grant: SourceGrant


class Access:
    cookie = "mailbox_web"
    flow_cookie = "mailbox_web_flow"
    state_prefix = "web."
    session_seconds = 8 * 60 * 60

    def __init__(self, engine, policy, public, client_id, client_secret, signing_key):
        self.engine = engine
        self.policy = policy
        self.public = public
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = public + "/auth/callback"
        base = urlsplit(public)
        self.origin = f"{base.scheme}://{base.netloc}"
        self.prefix = base.path
        self.login_url = self.prefix + "/api/v1/session/login"
        self.cookie_path = self.prefix + "/api/v1/"
        self.callback_path = self.prefix + "/auth/callback"
        key = hmac.digest(signing_key.encode(), b"mailbox-web-oauth-flow-v1", "sha256")
        self.fernet = Fernet(base64.urlsafe_b64encode(key))
        self.csrf_key = hmac.digest(signing_key.encode(), b"mailbox-web-csrf-v1", "sha256")

    @classmethod
    def from_file(cls, engine, policy_path, public, client_id, client_secret, signing_key):
        try:
            policy = Policy.model_validate_json(Path(policy_path).read_bytes())
        except (OSError, ValidationError):
            raise ValueError("invalid platform access policy file") from None
        policy.viewers = {key.casefold(): value for key, value in policy.viewers.items()}
        digests = [item.token_sha256 for item in policy.sources.values() if item.token_sha256]
        if len(digests) != len(set(digests)):
            raise ValueError("platform source token digests must be unique")
        for item in policy.sources.values():
            if item.mcp_login and item.authorities != {"self_report"}:
                raise ValueError("MCP source grants are limited to self-reported observations")
        if any(grant.execution is not None and not grant.token_sha256 for grant in policy.sources.values()):
            raise ValueError("execution grant requires a dedicated HTTP credential on the writer")
        if any(not policy.sources[source_id].token_sha256
               for runs in policy.workflow_authors.values() for source_id in runs.values()):
            raise ValueError("workflow author requires a native HTTP credential on the writer")
        return cls(engine, policy, public, client_id, client_secret, signing_key)

    def viewer_projects(self, login):
        return self.policy.viewers.get(login.casefold(), set()) if isinstance(login, str) else set()

    def mcp_access(self, login):
        return {
            "allowed_projects": sorted(self.viewer_projects(login)),
            "reporting_sources": [
                {"source_id": source_id, "projects": sorted(grant.projects),
                 "entity_types": sorted(grant.entity_types), "authorities": sorted(grant.authorities)}
                for source_id, grant in sorted(self.policy.sources.items())
                if self._matches_mcp_login(grant, login)
            ],
        }

    @staticmethod
    def _matches_mcp_login(grant, login):
        return bool(grant and grant.mcp_login and isinstance(login, str)
                    and grant.mcp_login.casefold() == login.casefold())

    def mcp_source(self, login, source_id):
        grant = self.policy.sources.get(source_id) if isinstance(source_id, str) else None
        if not self._matches_mcp_login(grant, login):
            raise APIError(403, "SOURCE_FORBIDDEN", "No source grant for this account")
        return AuthorizedSource(source_id, grant)

    def source(self, request):
        scheme, _, credential = request.headers.get("Authorization", "").partition(" ")
        if scheme.casefold() == "bearer" and credential:
            digest = self.digest(credential)
            for source_id, grant in self.policy.sources.items():
                if grant.token_sha256 and hmac.compare_digest(digest, grant.token_sha256):
                    return AuthorizedSource(source_id, grant)
        raise APIError(401, "SOURCE_AUTH_REQUIRED", "A valid source token is required")

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def csrf(self, token):
        return hmac.digest(self.csrf_key, token.encode(), "sha256").hex()

    def session(self, request):
        token = request.cookies.get(self.cookie)
        if token and len(token) == 43:
            with self.engine.connect() as conn:
                row = conn.exec_driver_sql(
                    "SELECT github_login, subject FROM web_sessions "
                    "WHERE token_hash=%s AND expires_at > now()", (self.digest(token),)
                ).one_or_none()
            if row and row.github_login.casefold() in self.policy.viewers:
                return Session(github_login=row.github_login, subject=row.subject,
                               allowed_projects=sorted(self.viewer_projects(row.github_login)),
                               csrf_token=self.csrf(token))
        raise APIError(401, "LOGIN_REQUIRED", "Sign in with GitHub", login_url=self.login_url)

    def projects(self, request):
        return set(self.session(request).allowed_projects)

    def login(self, request):
        state = self.state_prefix + secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        flow = self.fernet.encrypt(json.dumps([state, verifier]).encode()).decode()
        params = urlencode({
            "client_id": self.client_id, "redirect_uri": self.callback_url, "state": state,
            "scope": "read:user", "code_challenge": challenge, "code_challenge_method": "S256",
        })
        response = RedirectResponse("https://github.com/login/oauth/authorize?" + params, status_code=302)
        response.set_cookie(self.flow_cookie, flow, max_age=600, path=self.callback_path,
                            secure=True, httponly=True, samesite="lax")
        return response

    def callback(self, request):
        code = request.query_params.get("code")
        supplied = request.query_params.get("state", "")
        try:
            state, verifier = json.loads(self.fernet.decrypt(
                request.cookies.get(self.flow_cookie, "").encode(), ttl=600))
        except InvalidToken:
            raise APIError(400, "OAUTH_STATE_INVALID", "Start a new sign-in") from None
        if not supplied.isascii() or not hmac.compare_digest(state, supplied):
            raise APIError(400, "OAUTH_STATE_INVALID", "Start a new sign-in")
        if not code:
            raise APIError(400, "OAUTH_SIGN_IN_INCOMPLETE", "GitHub sign-in did not complete")
        login, subject = self.github_identity(code, verifier)
        if login.casefold() not in self.policy.viewers:
            raise APIError(403, "PROJECT_ACCESS_DENIED", "This account has no Web access grant")
        token = secrets.token_urlsafe(32)
        with self.engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM web_sessions WHERE expires_at <= now()")
            conn.exec_driver_sql(
                "INSERT INTO web_sessions (token_hash, github_login, subject, expires_at) VALUES (%s,%s,%s,%s)",
                (self.digest(token), login, subject,
                 datetime.now(timezone.utc) + timedelta(seconds=self.session_seconds)),
            )
        response = RedirectResponse(self.prefix + "/ui/", status_code=303)
        response.delete_cookie(self.flow_cookie, path=self.callback_path, secure=True, httponly=True)
        response.set_cookie(self.cookie, token, max_age=self.session_seconds, path=self.cookie_path,
                            secure=True, httponly=True, samesite="lax")
        return response

    def github_identity(self, code, verifier):
        try:
            with httpx.Client(timeout=5.0) as client:
                token_response = client.post("https://github.com/login/oauth/access_token", data={
                    "client_id": self.client_id, "client_secret": self.client_secret,
                    "redirect_uri": self.callback_url, "code": code, "code_verifier": verifier,
                }, headers={"Accept": "application/json"})
                token_response.raise_for_status()
                token = token_response.json().get("access_token")
                if not isinstance(token, str) or not token:
                    raise APIError(400, "OAUTH_CODE_REJECTED", "Start a new GitHub sign-in")
                identity = client.get("https://api.github.com/user", headers={
                    "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                })
                identity.raise_for_status()
                user = identity.json()
                if not isinstance(user.get("login"), str) or type(user.get("id")) is not int:
                    raise ValueError("missing GitHub login or user id")
                return user["login"], str(user["id"])
        except (httpx.HTTPError, ValueError, AttributeError):
            raise APIError(502, "OAUTH_UNAVAILABLE", "GitHub identity verification failed", True) from None

    def logout(self, request):
        session = self.session(request)
        supplied = request.headers.get("X-CSRF-Token", "")
        if (request.headers.get("Origin") != self.origin or not supplied.isascii()
                or not hmac.compare_digest(supplied, session.csrf_token)):
            raise APIError(403, "CSRF_REJECTED", "Use the same-origin session and CSRF token")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM web_sessions WHERE token_hash=%s",
                                 (self.digest(request.cookies[self.cookie]),))
        response = Response(status_code=204)
        response.delete_cookie(self.cookie, path=self.cookie_path, secure=True, httponly=True)
        return response


class PlatformRoutes:
    """Dispatch the new API and leave the existing MCP ASGI lifespan intact."""

    def __init__(self, mcp, api, *, ui_root=None, prefix="", web_enabled=True):
        self.mcp, self.api = mcp, api
        self.web_enabled = web_enabled
        self.ui = None
        if ui_root and web_enabled:
            root = Path(ui_root)
            if not root.is_absolute() or not (root / "index.html").is_file():
                raise ValueError("TASK_PLATFORM_UI_ROOT must be an absolute build directory with index.html")

            async def redirect_to_ui(request):
                return RedirectResponse(prefix + "/ui/", status_code=308)

            self.ui = Starlette(routes=[
                Route("/ui", redirect_to_ui),
                Mount("/ui", StaticFiles(directory=root, html=True)),
            ])


    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            if path.startswith("/api/v1/"):
                source_route = (
                    path == "/api/v1/observations/batches" and scope["method"] == "POST"
                    or path.startswith("/api/v1/observations/batches/") and scope["method"] == "GET"
                )
                if not self.web_enabled and not source_route:
                    await Response(status_code=404, headers={"Cache-Control": "no-store"})(scope, receive, send)
                    return
                await self.api(scope, receive, send)
                return
            if not self.web_enabled and (path == "/ui" or path.startswith("/ui/")):
                await Response(status_code=404, headers={"Cache-Control": "no-store"})(scope, receive, send)
                return
            if self.ui is not None and (path == "/ui" or path.startswith("/ui/")):
                await self.ui(scope, receive, send)
                return
            if path == "/auth/callback":
                if Request(scope).query_params.get("state", "").startswith(Access.state_prefix):
                    if not self.web_enabled:
                        await Response(status_code=404, headers={"Cache-Control": "no-store"})(scope, receive, send)
                        return
                    await self.api(scope, receive, send)
                    return
        await self.mcp(scope, receive, send)
