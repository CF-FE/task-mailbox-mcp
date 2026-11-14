"""Exercise the VPS Web switch without a database, secrets or real network."""

from pathlib import Path
import sys
import tempfile
import unittest

from starlette.responses import Response
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from platform_service.access import PlatformRoutes


class WebDisabledSmoke(unittest.TestCase):
    def setUp(self):
        self.calls = []

        async def api(scope, receive, send):
            self.calls.append(('api', scope['path']))
            await Response(status_code=401)(scope, receive, send)

        async def mcp(scope, receive, send):
            self.calls.append(('mcp', scope['path']))
            await Response(status_code=202)(scope, receive, send)

        self.api, self.mcp = api, mcp

    def test_web_disabled_does_not_query_or_serve_files(self):
        app = PlatformRoutes(self.mcp, self.api, web_enabled=False, ui_root='/unused-build')
        client = TestClient(app, raise_server_exceptions=True)
        for path in ('/ui', '/ui/', '/ui/assets/app.js', '/api/v1/session',
                     '/api/v1/session/login', '/api/v1/projects',
                     '/api/v1/projects/fanren-work/events',
                     '/auth/callback?state=web.example&code=test'):
            self.assertEqual(404, client.get(path).status_code, path)
        self.assertEqual(404, client.post('/api/v1/session/logout').status_code)
        self.assertEqual([], self.calls)

    def test_mcp_oauth_and_source_auth_are_still_owned_by_original_apps(self):
        app = PlatformRoutes(self.mcp, self.api, web_enabled=False)
        client = TestClient(app)
        for path in ('/mcp', '/authorize', '/token', '/auth/callback?state=mcp-example'):
            self.assertEqual(202, client.get(path).status_code)
        self.assertEqual(401, client.post('/api/v1/observations/batches', json={}).status_code)
        self.assertEqual(401, client.get('/api/v1/observations/batches/test').status_code)
        self.assertEqual(['mcp'] * 4 + ['api'] * 2, [kind for kind, _ in self.calls])

    def test_enabled_default_preserves_web_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'index.html').write_text('existing Web')
            client = TestClient(PlatformRoutes(self.mcp, self.api, ui_root=directory))
            self.assertEqual('existing Web', client.get('/ui/').text)
            self.assertEqual(401, client.get('/api/v1/session').status_code)
            self.assertEqual(401, client.get('/auth/callback?state=web.example').status_code)


if __name__ == '__main__':
    unittest.main()
