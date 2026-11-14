"""Local monitor ASGI smoke, with an isolated query fixture and no production database."""

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from platform_service.http import APIError, build_api
from platform_service.local import AUTHORITY, HOST, ORIGIN, build_local_app
from platform_service.models import EventHint, Project, Session, Snapshot
from platform_service.observations import require_project


class QueryFixture:
    def __init__(self):
        self.seen = []

    def projects(self, allowed, cursor, limit):
        return Snapshot(data=[Project(id=project, accepted_count=0, pending_acceptance_count=0,
                                      blocked_count=0, unresolved_blocker_count=0)
                              for project in sorted(allowed)], sources=[],
                        observed_at=datetime.now(timezone.utc), watermark="7")

    def overview(self, allowed, project_id, cursor, limit):
        require_project(allowed, project_id)
        raise AssertionError("unexpected authorized overview in this fixture")

    def events(self, allowed, project_id, cursor):
        require_project(allowed, project_id)
        self.seen.append(cursor)
        if len(self.seen) > 1:
            raise APIError(503, "TEST_STOP", "Finish finite smoke stream")
        return [EventHint(watermark="7", project_id=project_id, entity_type="task", entity_id="t1")]


class LocalMonitorSmoke(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "dist"
        self.root.mkdir()
        (self.root / "index.html").write_text("<title>Local task monitor</title>")
        (self.root.parent / "private.json").write_text("private sentinel")
        (self.root / "escape.json").symlink_to(self.root.parent / "private.json")
        self.service = QueryFixture()
        self.make_client()

    def make_client(self, host=HOST):
        app = build_local_app(self.service, {"sample-project"}, self.root)
        self.client = TestClient(app, base_url=ORIGIN, client=(host, 40000))
        self.addCleanup(self.client.close)

    def test_local_session_and_project_scope(self):
        session = self.client.get("/mailbox/api/v1/session")
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json(), {"mode": "local", "allowed_projects": ["sample-project"]})
        self.assertNotIn("Set-Cookie", session.headers)
        self.assertEqual(session.headers["Cache-Control"], "no-store")
        self.assertEqual(session.headers["Cross-Origin-Resource-Policy"], "same-origin")
        projects = self.client.get("/mailbox/api/v1/projects", headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(projects.status_code, 200)
        self.assertEqual([p["id"] for p in projects.json()["data"]], ["sample-project"])
        denied = self.client.get("/mailbox/api/v1/projects/other/overview")
        self.assertEqual(denied.status_code, 404)
        with patch("platform_service.http.asyncio.sleep", return_value=None):
            stream = self.client.get("/mailbox/api/v1/projects/sample-project/events?after=1",
                                     headers={"Last-Event-ID": "4"})
        self.assertEqual(stream.status_code, 200)
        self.assertIn("text/event-stream", stream.headers["Content-Type"])
        self.assertIn("id: 7\nevent: observation\n", stream.text)
        self.assertEqual(self.service.seen, ["4", "7"])

    def test_access_boundary_and_static_assets(self):
        for headers in (
            {"Host": "rebind.example.test", "X-Forwarded-Host": AUTHORITY},
            {"Origin": "http://127.0.0.1:9999"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
            {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get("/mailbox/api/v1/session", headers=headers).status_code, 403)
        redirect = self.client.get("/mailbox/ui", follow_redirects=False)
        self.assertEqual((redirect.status_code, redirect.headers["Location"]), (308, "/mailbox/ui/"))
        navigation = self.client.get("/mailbox/ui/", headers={
            "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
        self.assertEqual(navigation.status_code, 200)
        self.assertIn("Local task monitor", navigation.text)
        self.assertEqual(navigation.headers["Content-Security-Policy"], "frame-ancestors 'none'")
        self.assertEqual(self.client.get("/mailbox/ui/escape.json").status_code, 404)
        self.assertEqual(self.client.get("/mailbox/ui/%2e%2e/private.json").status_code, 404)
        self.make_client(host="192.0.2.10")
        self.assertEqual(self.client.get("/mailbox/api/v1/session", headers={
            "X-Forwarded-For": HOST}).status_code, 403)

    def test_no_write_oauth_or_mcp_routes(self):
        for path in (
            "/mailbox/api/v1/session/login", "/mailbox/api/v1/session/logout",
            "/mailbox/auth/callback", "/mailbox/api/v1/observations/batches",
            "/mailbox/api/v1/observations/batches/b1", "/mailbox/mcp",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
                self.assertEqual(self.client.post(path).status_code, 405)
        schema = self.client.get("/mailbox/api/v1/openapi.json").json()
        self.assertNotIn("securitySchemes", schema["components"])
        self.assertTrue(all(set(verbs) == {"get"} for verbs in schema["paths"].values()))

    def test_vps_default_session_and_contract_preserved(self):
        class ExistingAccess:
            def session(self, request):
                return Session(github_login="fixture", subject="42", allowed_projects=["p1"], csrf_token="fixture")

        api = build_api(self.service, ExistingAccess())
        with TestClient(api) as client:
            session = client.get("/api/v1/session")
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json(), {"github_login": "fixture", "subject": "42",
                         "allowed_projects": ["p1"], "csrf_token": "fixture"})
        schema = api.openapi()
        self.assertIn("WebSession", schema["components"]["securitySchemes"])
        self.assertIn("SourceBearer", schema["components"]["securitySchemes"])
        self.assertIn("post", schema["paths"]["/api/v1/observations/batches"])
        self.assertIn("get", schema["paths"]["/api/v1/session/login"])


if __name__ == "__main__":
    unittest.main()
