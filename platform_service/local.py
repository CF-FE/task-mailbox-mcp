"""Loopback-only task monitor backed by read-only PostgreSQL queries."""

import argparse
from pathlib import Path
import sys

from pydantic import Field, ValidationError
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
import uvicorn

from .access import Policy
from .database import REVISION, database_engine
from .http import build_api
from .models import DTO, Id, LocalSession
from .observations import Observations

HOST = "127.0.0.1"
PORT = 8767
AUTHORITY = f"{HOST}:{PORT}"
ORIGIN = f"http://{AUTHORITY}"
PREFIX = "/mailbox"


class LocalConfig(DTO):
    database_url: str
    policy_path: Path
    ui_root: Path
    projects: set[Id] = Field(min_length=1)
    stale_seconds: int = Field(default=120, ge=1, strict=True)


class LocalAccess:
    def __init__(self, projects):
        self.allowed = frozenset(projects)

    def projects(self, request):
        return self.allowed

    def session(self, request):
        return LocalSession(allowed_projects=sorted(self.projects(request)))


class LoopbackAccess:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope["headers"]
        peer = scope.get("client")
        host = [value for name, value in headers if name == b"host"]
        origin = [value for name, value in headers if name == b"origin"]
        fetch_site = [value for name, value in headers if name == b"sec-fetch-site"]
        # A direct browser link to the UI is a top-level navigation. Sensitive
        # JSON, SSE, assets and any iframe remain unavailable to other origins.
        document = (scope["path"] in (PREFIX + "/ui", PREFIX + "/ui/")
                    and (b"sec-fetch-mode", b"navigate") in headers
                    and (b"sec-fetch-dest", b"document") in headers)
        if (not peer or peer[0] != HOST or scope["scheme"] != "http"
                or host != [AUTHORITY.encode("ascii")]):
            response = PlainTextResponse("Local monitor requires the loopback address", status_code=403)
        elif (origin not in ([], [ORIGIN.encode("ascii")])
              or (fetch_site not in ([], [b"none"], [b"same-origin"]) and not document)):
            response = PlainTextResponse("Cross-origin requests are forbidden", status_code=403)
        elif scope["method"] not in ("GET", "HEAD"):
            response = PlainTextResponse("Only read requests are supported", status_code=405,
                                         headers={"Allow": "GET, HEAD"})
        else:
            response = self.app

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [
                    *(item for item in message["headers"] if item[0] not in (b"cache-control", b"referrer-policy")),
                    (b"cache-control", b"no-store"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"content-security-policy", b"frame-ancestors 'none'"),
                ]}
            await send(message)

        await response(scope, receive, secure_send)


def build_local_app(service, projects, ui_root):
    root = Path(ui_root)
    if not root.is_absolute() or not (root / "index.html").is_file():
        raise ValueError("Local monitor requires an absolute UI directory containing index.html")

    async def redirect_to_ui(request):
        return RedirectResponse(PREFIX + "/ui/", status_code=308)

    routes = [
        Route("/ui", redirect_to_ui),
        Mount("/ui", StaticFiles(directory=root, html=True)),
        Mount("/", build_api(service, LocalAccess(projects), local=True)),
    ]
    return LoopbackAccess(Starlette(routes=[Mount(PREFIX, routes=routes)]))


def configured_local(config_path):
    try:
        config = LocalConfig.model_validate_json(Path(config_path).read_bytes())
        if not config.ui_root.is_absolute() or not config.policy_path.is_absolute():
            raise ValueError("Local monitor requires absolute policy and UI paths")
        policy = Policy.model_validate_json(config.policy_path.read_bytes())
        engine = database_engine(config.database_url, require_tls=True)
    except (ArgumentError, OSError, ValidationError, ValueError):
        raise ValueError("Invalid local monitor configuration") from None

    try:
        engine.update_execution_options(postgresql_readonly=True)
        with engine.connect() as conn:
            revision = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        if revision != REVISION:
            raise ValueError("PostgreSQL platform schema revision does not match the application")
        service = Observations(engine, policy, config.stale_seconds)
        return build_local_app(service, config.projects, config.ui_root), engine
    except BaseException:
        engine.dispose()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        app, engine = configured_local(args.config)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 78
    except SQLAlchemyError:
        print("Local monitor could not connect to PostgreSQL or validate its schema", file=sys.stderr)
        return 1

    try:
        uvicorn.run(app, host=HOST, port=PORT, proxy_headers=False, access_log=False, ws="none")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
