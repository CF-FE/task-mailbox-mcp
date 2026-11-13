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
