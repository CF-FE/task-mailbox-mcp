"""Standard-library discovery for the versioned platform contract."""

import importlib.util
import unittest

HAS_PLATFORM = all(importlib.util.find_spec(name) for name in ("fastapi", "sqlalchemy", "psycopg"))


@unittest.skipUnless(HAS_PLATFORM, "install requirements-platform.txt for platform tests")
class ContractTest(unittest.TestCase):
    def test_openapi_references_resolve(self):
        from platform_service.http import backend_contract

        contract = backend_contract()
        self.assertEqual(contract["mcp"]["publish_observation_batch"], ["batch"])
        self.assertEqual(contract["mcp"]["get_observation_receipt"], ["batch_id", "source_id"])
        spec = contract["openapi"]

        def check(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    target = spec
                    for key in value["$ref"].removeprefix("#/").split("/"):
                        target = target[key]
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        check(spec)

    def test_payload_is_closed_and_aware(self):
        from pydantic import ValidationError
        from platform_service.models import Observation

        base = {"event_id": "event", "project_id": "project", "entity_type": "task",
                "entity_id": "task", "event_type": "snapshot", "observed_at": "2026-11-14T00:00:00Z",
                "evidence": [], "payload": {"followup_status": "PENDING", "followup_revision": 38}}
        Observation.model_validate(base)
        with self.assertRaises(ValidationError):
            Observation.model_validate({**base, "payload": {"transcript": "private text"}})
        with self.assertRaises(ValidationError):
            Observation.model_validate({**base, "observed_at": "2026-11-14T00:00:00"})

    def test_production_dsn_requires_host_verification(self):
        from platform_service.database import database_engine

        for dsn in ("postgresql://localhost/db", "postgresql://localhost/db?sslmode=require"):
            with self.assertRaises(ValueError):
                database_engine(dsn, require_tls=True)
        engine = database_engine("postgresql://example.test/db?sslmode=verify-full&sslrootcert=/private/ca.pem",
                                 require_tls=True)
        self.assertEqual(engine.url.query["gssencmode"], "disable")
        engine.dispose()

    def test_service_connection_limits_do_not_change_local_or_migration_defaults(self):
        from unittest.mock import patch
        from platform_service.database import configured_engine

        dsn = "postgresql://example.test/db?sslmode=verify-full&sslrootcert=/fixture/ca.pem"
        with (
            patch.dict("os.environ", {"TASK_PLATFORM_DATABASE_URL": dsn}),
            patch("platform_service.database.create_engine") as create,
        ):
            for service, expected in ((True, (20, 5)), (False, (15, 4))):
                configured_engine(service_timeouts=service)
                kwargs = create.call_args.kwargs
                self.assertEqual((kwargs["connect_args"]["connect_timeout"], kwargs["pool_timeout"]),
                                 expected)
                self.assertTrue(kwargs["pool_pre_ping"])

    def test_operation_retry_reconciles_using_the_same_write_arguments(self):
        import json
        from unittest.mock import patch
        from psycopg.errors import ConnectionTimeout
        from sqlalchemy.exc import OperationalError
        from platform_service.database import retry_storage_operation

        stored = {}
        invocations = []

        def publish(job_id, event_id, payload):
            invocations.append((job_id, event_id, payload))
            if event_id not in stored:
                stored[event_id] = payload
                raise OperationalError(None, None, ConnectionTimeout("simulated lost reply"),
                                       connection_invalidated=True)
            return stored[event_id]

        payload = {"result": "same input"}
        with (
            patch("platform_service.database.sleep") as backoff,
            self.assertLogs("task_mailbox.storage", level="WARNING") as records,
        ):
            result = retry_storage_operation("publish_result", publish, "original-job", "original-event", payload)
        self.assertEqual(len(records.records), 1)
        self.assertIsNone(records.records[0].exc_info)
        record = json.loads(records.records[0].getMessage())
        self.assertRegex(record.pop("request_id"), r"^[0-9a-f]{32}$")
        self.assertEqual(record, {"operation": "publish_result", "category": "connection_timeout",
                                  "sqlstate": None, "next_attempt": 2})
        self.assertIs(result, payload)
        self.assertEqual(invocations, [("original-job", "original-event", payload)] * 2)
        self.assertEqual(stored, {"original-event": payload})
        backoff.assert_called_once_with(0.25)

    def test_operation_retry_stops_on_nontransient_errors_and_budget_exhaustion(self):
        from unittest.mock import Mock, patch
        from psycopg.errors import ConnectionTimeout, InvalidPassword, UniqueViolation
        from sqlalchemy.exc import DBAPIError, OperationalError, TimeoutError
        from platform_service.database import retry_storage_operation

        disconnect = OperationalError(None, None, ConnectionTimeout("first attempt"))
        for error, elapsed, expected_attempts in (
            (disconnect, 0, 2),
            (disconnect, 30, 1),
            (OperationalError(None, None, InvalidPassword(), connection_invalidated=True), 0, 1),
            (DBAPIError(None, None, UniqueViolation(), connection_invalidated=True), 0, 1),
            (TimeoutError("pool exhausted"), 0, 1),
        ):
            with self.subTest(error=type(error.orig if isinstance(error, DBAPIError) else error),
                              elapsed=elapsed):
                operation = Mock(side_effect=error)
                with (
                    patch("platform_service.database.monotonic", side_effect=[0, elapsed]),
                    patch("platform_service.database.sleep") as backoff,
                    self.assertRaises(type(error)) as caught,
                ):
                    retry_storage_operation("list_events", operation, 24, 10)
                self.assertIs(caught.exception, error)
                self.assertEqual(operation.call_count, expected_attempts)
                self.assertEqual(backoff.call_count, expected_attempts - 1)

    def test_optional_ui_mount_preserves_api_and_mcp_routes(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from fastapi.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from platform_service.access import PlatformRoutes
        from server import PublicPrefix

        def old_app(request):
            return PlainTextResponse("existing route")

        def api(request):
            return PlainTextResponse("platform API")

        legacy = Starlette(routes=[Route(path, old_app) for path in (
            "/mcp", "/auth/callback", "/.well-known/oauth-authorization-server"
        )])
        http = Starlette(routes=[Route("/api/v1/session", api)])
        with TemporaryDirectory() as directory:
            root = Path(directory) / "dist"
            root.mkdir()
            with self.assertRaises(ValueError):
                PlatformRoutes(legacy, http, ui_root=root)
            (root / "index.html").write_text("<title>Task platform</title>")
            (root / "assets").mkdir()
            (root / "assets" / "bundle.js").write_text("export {};")
            (root.parent / "private.txt").write_text("private fixture")
            (root / "symlink.txt").symlink_to(root.parent / "private.txt")
            app = PublicPrefix(PlatformRoutes(legacy, http, ui_root=root, prefix="/mailbox"), "/mailbox")
            with TestClient(app, base_url="https://example.test") as client:
                redirect = client.get("/mailbox/ui", follow_redirects=False)
                self.assertEqual(redirect.status_code, 308)
                self.assertEqual(redirect.headers["location"], "/mailbox/ui/")
                self.assertIn("Task platform", client.get("/mailbox/ui/").text)
                self.assertEqual(client.get("/mailbox/ui/assets/bundle.js").text, "export {};")
                self.assertEqual(client.get("/mailbox/ui/assets/missing.js").status_code, 404)
                self.assertEqual(client.get("/mailbox/ui/symlink.txt").status_code, 404)
                self.assertEqual(client.get("/mailbox/ui/%2e%2e/private.txt").status_code, 404)
                self.assertEqual(client.get("/mailbox/api/v1/session").text, "platform API")
                for path in ("/mailbox/mcp", "/mailbox/auth/callback?state=legacy",
                             "/.well-known/oauth-authorization-server/mailbox"):
                    self.assertEqual(client.get(path).text, "existing route")
                self.assertEqual(client.get("/ui/").status_code, 404)
            with TestClient(PublicPrefix(PlatformRoutes(legacy, http), "/mailbox")) as client:
                self.assertEqual(client.get("/mailbox/ui/").status_code, 404)

    def test_workflow_measurement_requires_finite_duration_and_basis(self):
        from pydantic import ValidationError
        from platform_service.models import WorkflowNode

        base = {"id": "step", "label": "Step"}
        self.assertIsNone(WorkflowNode.model_validate(base).measured_duration_seconds)
        for seconds, basis in ((-1, "timer"), (float("inf"), "timer"), (float("nan"), "timer"),
                               ("4", "timer"), (True, "timer"), (4, " "), (4, None), (None, "timer")):
            with self.subTest(seconds=seconds, basis=basis), self.assertRaises(ValidationError):
                WorkflowNode.model_validate({**base, "measured_duration_seconds": seconds,
                                             "measured_duration_basis": basis})

    def test_sqlite_whoami_has_original_response_shape(self):
        from fastmcp import Client
        from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
        from server import create_server
        from smoke_mcp import run_http

        import asyncio

        auth = StaticTokenVerifier(tokens={
            "synthetic-token": {"client_id": "synthetic", "scopes": [], "login": "owner", "sub": "42"},
        })
        server = create_server(None, auth, "owner")

        async def check(origin):
            async with Client(origin + "/mcp", auth="synthetic-token") as client:
                identity = (await client.call_tool("whoami")).data
                self.assertEqual(identity, {"github_login": "owner", "subject": "42",
                                            "service": "task-mailbox", "execution_enabled": False})

        asyncio.run(run_http(server.http_app(path="/mcp", stateless_http=True, json_response=True), check))

    def test_role_activity_merges_only_matching_native_session_and_binding(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace

        from platform_service.observations import Observations

        def role(source, at, state, action, **overrides):
            return SimpleNamespace(
                project_id="fanren", entity_type="role", entity_id="coordinator", source_id=source,
                observed_at=datetime(2026, 11, 14, tzinfo=timezone.utc),
                body={"payload": {"kind": "coordinator", "binding_id": "role-binding",
                                  "session_ref": "native-session", "task_id": "archived-task",
                                  "observed_state": state, "latest_action": action,
                                  "last_observed_at": at, **overrides},
                      "evidence": [{"authority": "native_observation"}]},
            )

        old = role("fanren-r17-case", "2026-11-13T23:00:00Z", "UNKNOWN", "Archived action")
        new = role("mac-fanren-session-hooks", "2026-11-14T08:00:01+08:00", "ACTIVE",
                   "PreToolUse: Read", task_id="mapped-task")
        for rows in ([old, new], [new, old]):
            fields, _, _, conflicts = Observations._merge(rows)[("fanren", "role", "coordinator")]
            self.assertEqual(fields["observed_state"], "ACTIVE")
            self.assertEqual(fields["latest_action"], "PreToolUse: Read")
            self.assertEqual(fields["last_observed_at"], "2026-11-14T08:00:01+08:00")
            self.assertEqual(fields["task_id"], "mapped-task")
            self.assertFalse(conflicts)

        unassigned = role("mac-fanren-session-hooks", "2026-11-14T00:00:01Z", "ACTIVE",
                          "PreToolUse: Read", task_id=None)
        fields, _, _, conflicts = Observations._merge([old, unassigned])[("fanren", "role", "coordinator")]
        self.assertIsNone(fields["task_id"])
        self.assertFalse(conflicts)
        del unassigned.body["payload"]["task_id"]
        fields, _, _, _ = Observations._merge([old, unassigned])[("fanren", "role", "coordinator")]
        self.assertEqual(fields["task_id"], "archived-task")

        unbound_old = role("fanren-r17-case", "2026-11-13T23:00:00Z", "UNKNOWN",
                           "Archived action", binding_id=None)
        unbound_new = role("mac-fanren-session-hooks", "2026-11-14T00:00:01Z", "ACTIVE",
                           "PreToolUse: Read", binding_id=None)
        fields, _, _, conflicts = Observations._merge([unbound_old, unbound_new])[("fanren", "role", "coordinator")]
        self.assertEqual(fields["observed_state"], "ACTIVE")
        self.assertFalse(conflicts)

        foreign = role("mac-fanren-session-hooks", "2026-11-14T00:00:01Z", "ACTIVE",
                       "PreToolUse: Read", session_ref="other-session", task_id=None)
        fields, _, _, conflicts = Observations._merge([old, foreign])[("fanren", "role", "coordinator")]
        self.assertNotIn("task_id", fields)
        self.assertIn("task_id", conflicts)

        for field, value in (("session_ref", "other-session"), ("binding_id", "other-binding"),
                             ("binding_id", None), ("last_observed_at", "2026-11-13T23:00:00Z")):
            distinct = role("mac-fanren-session-hooks", "2026-11-14T00:00:01Z", "ACTIVE",
                            "PreToolUse: Read", **{field: value})
            fields, _, _, conflicts = Observations._merge([old, distinct])[("fanren", "role", "coordinator")]
            self.assertNotIn("observed_state", fields)
            self.assertIn("latest_action", conflicts)

    def test_reconcile_cli_passes_configured_budget_and_reports_receipt(self):
        from contextlib import redirect_stdout
        from io import StringIO
        import json
        from pathlib import Path
        from unittest.mock import patch

        from platform_service import migrate

        target = Path("mailbox.sqlite3")
        receipt = {"tables": {}, "cursor_high_watermark": 7}
        stdout = StringIO()
        with (
            patch("sys.argv", ["migrate", "reconcile-to-sqlite", "--target", str(target)]),
            patch.dict("os.environ", {"TASK_MAILBOX_MAX_DATABASE_BYTES": "131072"}),
            patch.object(migrate, "configured_engine") as configure,
            patch.object(migrate, "reconcile_to_sqlite", return_value=receipt) as reconcile,
            redirect_stdout(stdout),
        ):
            self.assertEqual(migrate.main(), 0)

        reconcile.assert_called_once_with(configure.return_value, target, 131072)
        configure.return_value.dispose.assert_called_once_with()
        self.assertEqual(json.loads(stdout.getvalue()), receipt)

    def test_configured_mailbox_and_platform_modes(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import MagicMock, patch

        from mailbox_store import Store
        from platform_service.database import PostgresStore
        from server import configured_app

        with TemporaryDirectory() as directory:
            base = {
                "TASK_MAILBOX_PUBLIC_BASE_URL": "https://example.test/mailbox",
                "TASK_MAILBOX_GITHUB_CLIENT_ID": "synthetic-app",
                "TASK_MAILBOX_GITHUB_CLIENT_SECRET": "synthetic-secret",
                "TASK_MAILBOX_ALLOWED_GITHUB_USER": "owner",
                "TASK_MAILBOX_SIGNING_KEY": "z" * 64,
                "FASTMCP_HOME": str(Path(directory) / "oauth"),
                "TASK_MAILBOX_DATA_DIR": str(Path(directory) / "mailbox"),
            }
            complete = {"TASK_PLATFORM_DATABASE_URL": "synthetic",
                        "TASK_PLATFORM_ACCESS_FILE": "synthetic"}
            cases = [
                ({}, Store, False),
                ({"TASK_MAILBOX_STORAGE_ENGINE": "sqlite", **complete}, Store, True),
                ({"TASK_MAILBOX_STORAGE_ENGINE": "postgresql", **complete}, PostgresStore, True),
            ]
            for settings, store_type, enabled in cases:
                with (
                    self.subTest(settings=settings),
                    patch.dict(os.environ, {**base, **settings}, clear=True),
                    patch("server.GitHubProvider"),
                    patch("server.create_server") as create,
                    patch("server.configured_platform") as platform,
                ):
                    platform.return_value = MagicMock(), (None, None)
                    configured_app()
                    store, _, _, platform_args = create.call_args.args
                    self.assertIsInstance(store, store_type)
                    self.assertEqual(platform.called, enabled)
                    self.assertEqual(platform_args is not None, enabled)
            for settings in (
                {"TASK_PLATFORM_UI_ROOT": "/example/ui"},
                {"TASK_PLATFORM_DATABASE_URL": "synthetic"},
                {"TASK_PLATFORM_ACCESS_FILE": "synthetic"},
                {"TASK_PLATFORM_DATABASE_URL": "", "TASK_PLATFORM_ACCESS_FILE": "synthetic"},
                {"TASK_MAILBOX_STORAGE_ENGINE": "postgresql"},
            ):
                with (
                    self.subTest(settings=settings),
                    patch.dict(os.environ, {**base, **settings}, clear=True),
                    patch("server.GitHubProvider"),
                    self.assertRaisesRegex(ValueError, "complete PostgreSQL and access policy"),
                ):
                    configured_app()
