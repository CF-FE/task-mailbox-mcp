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
            self.assertEqual(conflicts, {"task_id"})

        unbound_old = role("fanren-r17-case", "2026-11-13T23:00:00Z", "UNKNOWN",
                           "Archived action", binding_id=None)
        unbound_new = role("mac-fanren-session-hooks", "2026-11-14T00:00:01Z", "ACTIVE",
                           "PreToolUse: Read", binding_id=None)
        fields, _, _, conflicts = Observations._merge([unbound_old, unbound_new])[("fanren", "role", "coordinator")]
        self.assertEqual(fields["observed_state"], "ACTIVE")
        self.assertFalse(conflicts)

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
