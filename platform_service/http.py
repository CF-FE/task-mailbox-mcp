"""Versioned browser and source APIs shared with the MCP query service."""

import asyncio
import json
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from .models import (
    FIELDS, Blocker, Cursor, ErrorEnvelope, EventHint, Id, ObservationBatch,
    Overview, Project, Receipt, RoleActivity, Session, Snapshot, TaskTrace, WorkflowRun,
)

Limit = Annotated[int, Query(ge=1, le=200)]
MAX_BATCH_BYTES = 1048576


class APIError(Exception):
    def __init__(self, status, code, message, retryable=False, login_url=None):
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.login_url = login_url


def build_api(service, access):
    app = FastAPI(title="Task Collaboration Platform", version="1.0.0",
                  openapi_url="/api/v1/openapi.json", docs_url=None, redoc_url=None,
                  responses={401: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope},
                             404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope},
                             503: {"model": ErrorEnvelope}})

    def error_response(error, request_id):
        body = {"error": {"code": error.code, "message": error.message,
                          "retryable": error.retryable}, "request_id": request_id}
        if error.login_url:
            body["login_url"] = error.login_url
        return JSONResponse(body, status_code=error.status)

    @app.middleware("http")
    async def response_metadata(request, call_next):
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(APIError)
    def domain_error(request, error):
        return error_response(error, request.state.request_id)

    @app.exception_handler(SQLAlchemyError)
    def storage_error(request, error):
        return error_response(APIError(503, "STORAGE_UNAVAILABLE",
                                       "Storage unavailable. Reconcile using the original IDs.", True),
                              request.state.request_id)

    @app.exception_handler(RequestValidationError)
    def validation_error(request, error):
        return error_response(APIError(422, "INVALID_REQUEST", "Invalid request fields"),
                              request.state.request_id)

    @app.exception_handler(HTTPException)
    def http_error(request, error):
        return error_response(APIError(error.status_code, "HTTP_ERROR", str(error.detail)),
                              request.state.request_id)

    @app.get("/api/v1/session", response_model=Session)
    def session(request: Request):
        return access.session(request)

    @app.get("/api/v1/session/login", status_code=302)
    def login(request: Request):
        return access.login(request)

    @app.post("/api/v1/session/logout", status_code=204)
    def logout(request: Request):
        return access.logout(request)

    @app.get("/auth/callback", status_code=303)
    def callback(request: Request):
        return access.callback(request)

    @app.get("/api/v1/projects", response_model=Snapshot[list[Project]])
    def projects(request: Request, cursor: Cursor = "0", limit: Limit = 50):
        return service.projects(access.projects(request), cursor, limit)

    @app.get("/api/v1/projects/{project_id}/overview", response_model=Snapshot[Overview])
    def overview(request: Request, project_id: Id, cursor: Cursor = "0", limit: Limit = 50):
        return service.overview(access.projects(request), project_id, cursor, limit)

    @app.get("/api/v1/projects/{project_id}/roles", response_model=Snapshot[list[RoleActivity]])
    def roles(request: Request, project_id: Id, cursor: Cursor = "0", limit: Limit = 50):
        return service.roles(access.projects(request), project_id, cursor, limit)

    @app.get("/api/v1/tasks/{task_id}/trace", response_model=Snapshot[TaskTrace])
    def trace(request: Request, task_id: Id, project_id: Id | None = None,
              cursor: Cursor = "0", limit: Limit = 50):
        return service.trace(access.projects(request), task_id, project_id, cursor, limit)

    @app.get("/api/v1/workflows/{run_id}", response_model=Snapshot[WorkflowRun])
    def workflow(request: Request, run_id: Id, project_id: Id | None = None):
        return service.workflow(access.projects(request), run_id, project_id)

    @app.get("/api/v1/projects/{project_id}/blockers", response_model=Snapshot[list[Blocker]])
    def blockers(request: Request, project_id: Id, cursor: Cursor = "0", limit: Limit = 50):
        return service.blockers(access.projects(request), project_id, cursor, limit)

    @app.get("/api/v1/projects/{project_id}/events", response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {
                 "schema": {"type": "string"}}}}},
             description="SSE event observation. Its JSON data is EventHint. "
                         "Resume via Last-Event-ID or after.")
    async def events(request: Request, project_id: Id, after: Cursor = "0"):
        allowed = await run_in_threadpool(access.projects, request)
        resume = request.headers.get("Last-Event-ID", after)
        first = await run_in_threadpool(service.events, allowed, project_id, resume)

        async def stream():
            batch = first
            cursor = resume
            idle = 0
            while not await request.is_disconnected():
                for hint in batch:
                    cursor = hint.watermark
                    yield f"id: {cursor}\nevent: observation\ndata: {hint.model_dump_json()}\n\n"
                idle = 0 if batch else idle + 1
                if idle >= 15:
                    yield ": heartbeat\n\n"
                    idle = 0
                if len(batch) < 100:
                    await asyncio.sleep(1)
                try:
                    allowed_now = await run_in_threadpool(access.projects, request)
                    batch = await run_in_threadpool(service.events, allowed_now, project_id, cursor)
                except (APIError, SQLAlchemyError):
                    return

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.post("/api/v1/observations/batches", response_model=Receipt,
              responses={409: {"model": ErrorEnvelope}, 413: {"model": ErrorEnvelope}},
              openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
                  "schema": {"$ref": "#/components/schemas/ObservationBatch"}}}},
                  "security": [{"SourceBearer": []}]})
    async def batch(request: Request):
        source = access.source(request)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BATCH_BYTES:
                raise APIError(413, "BATCH_TOO_LARGE", "Maximum batch size is 1 MiB")
            body.extend(chunk)
        return await run_in_threadpool(service.ingest, source, bytes(body))

    @app.get("/api/v1/observations/batches/{batch_id}", response_model=Receipt,
             openapi_extra={"security": [{"SourceBearer": []}]})
    def receipt(request: Request, batch_id: Id):
        return service.receipt(access.source(request), batch_id)

    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        schemas = schema["components"]["schemas"]
        for model in (ObservationBatch, EventHint):
            definition = model.model_json_schema(ref_template="#/components/schemas/{model}")
            for name, component in definition.pop("$defs", {}).items():
                schemas.setdefault(name, component)
            schemas[model.__name__] = definition
        schema["components"]["securitySchemes"] = {
            "SourceBearer": {"type": "http", "scheme": "bearer"},
            "WebSession": {"type": "apiKey", "in": "cookie", "name": "mailbox_web"},
        }
        for path, methods in schema["paths"].items():
            if path.startswith("/api/v1/") and not (
                "/observations/" in path or path.endswith("/login")
            ):
                for operation in methods.values():
                    operation["security"] = [{"WebSession": []}]
        return schema

    app.openapi = openapi
    return app


def backend_contract():
    return {
        "contract_version": 1,
        "public_base_path_example": "/mailbox",
        "ui_path_relative_to_public_prefix": "/ui/",
        "login": {
            "start": "GET /api/v1/session/login",
            "callback": "GET /auth/callback (shared registered GitHub redirect)",
            "success_redirect": "/ui/ relative to public prefix",
            "session": "GET /api/v1/session",
            "logout": "POST /api/v1/session/logout with X-CSRF-Token from session",
            "unauthenticated": "401 with top-level login_url. Navigate browser to login_url.",
        },
        "observation": {
            "authorization": "Authorization: Bearer <source-specific token>",
            "receipt": "GET /api/v1/observations/batches/{batch_id} with the same source token",
            "max_bytes": MAX_BATCH_BYTES,
            "event_id_scope": "source_id",
            "historical": "true appends evidence without changing current views",
            "snapshots": "full entity snapshot per source; explicit null means unknown; source_revision is an integer",
            "activity": "append-only evidence, does not update entity snapshot",
            "accepted_values": ["ACCEPTED", "PENDING", "REJECTED", "UNKNOWN"],
        },
        "mcp": {
            "get_project_overview": ["project_id", "cursor", "limit"],
            "list_role_activity": ["project_id", "cursor", "limit"],
            "get_task_trace": ["task_id", "project_id", "cursor", "limit"],
            "get_workflow_run": ["run_id", "project_id"],
            "list_blockers": ["project_id", "cursor", "limit"],
            "publish_observation_batch": ["batch"],
            "get_observation_receipt": ["batch_id", "source_id"],
        },
        "pagination": "pass next_cursor to cursor on the same query; default 50 and maximum 200",
        "sse": {"event": "observation", "data_schema": "EventHint", "resume": "Last-Event-ID or after"},
        "observation_payload_schemas": {
            kind: model.model_json_schema() for kind, model in FIELDS.items()
        },
        "openapi": build_api(None, None).openapi(),
    }


if __name__ == "__main__":
    print(json.dumps(backend_contract(), ensure_ascii=False, indent=2))
