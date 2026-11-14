"""Pinned SDK smoke: mock HTTPS and browser, real loopback OAuth callback."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx2
from mcp import MCPError
from mcp.client import session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mac'))
from collector import AuthFailed, create_client
from fastmcp import Client


ORIGIN = 'https://mailbox.example.test'
BASE = ORIGIN + '/mailbox'
ENDPOINT = BASE + '/mcp'
RESOURCE_METADATA = '/.well-known/oauth-protected-resource/mailbox/mcp'
AUTH_METADATA = '/.well-known/oauth-authorization-server/mailbox'


class LoginFixture:
    def __init__(self):
        self.requests = []
        self.methods = []
        self.callback = None
        self.reject_tokens = False
        self.callback_task = None

    async def response(self, request):
        path = request.url.path
        assert request.url.host == 'mailbox.example.test'
        self.requests.append((request.method, path))
        if path == '/mailbox/mcp':
            if request.method == 'GET':
                return httpx2.Response(405)
            rpc = json.loads(request.content)
            method = rpc['method']
            self.methods.append(method)
            if self.reject_tokens or request.headers.get('authorization') != 'Bearer test-token':
                return httpx2.Response(401, headers={'WWW-Authenticate':
                    f'Bearer resource_metadata="{ORIGIN}{RESOURCE_METADATA}"'})
            if method == 'server/discover':
                return httpx2.Response(404)
            if method == 'initialize':
                return httpx2.Response(200, json={'jsonrpc': '2.0', 'id': rpc['id'], 'result': {
                    'protocolVersion': '2025-11-25', 'capabilities': {},
                    'serverInfo': {'name': 'test', 'version': '1'}}})
            if method == 'notifications/initialized':
                return httpx2.Response(202)
        if path == RESOURCE_METADATA:
            await asyncio.sleep(0.4)
            return httpx2.Response(200, json={'resource': ENDPOINT, 'authorization_servers': [BASE],
                'scopes_supported': ['read:user']})
        if path == AUTH_METADATA:
            await asyncio.sleep(0.45)
            return httpx2.Response(200, json={'issuer': BASE, 'authorization_endpoint': BASE + '/authorize',
                'token_endpoint': BASE + '/token', 'registration_endpoint': BASE + '/register',
                'response_types_supported': ['code'], 'code_challenge_methods_supported': ['S256'],
                'scopes_supported': ['read:user']})
        if path == '/mailbox/register':
            return httpx2.Response(201, json={**json.loads(request.content), 'client_id': 'test-client',
                'token_endpoint_auth_method': 'none'})
        if path == '/mailbox/token':
            assert parse_qs(request.content.decode())['code'] == ['test-code']
            return httpx2.Response(200, json={'access_token': 'test-token', 'token_type': 'Bearer',
                'expires_in': 600, 'scope': 'read:user'})
        raise AssertionError('unexpected fake-server request')

    def client_factory(self, endpoint):
        assert endpoint == ENDPOINT

        def build(**kwargs):
            return httpx2.AsyncClient(transport=httpx2.MockTransport(self.response), trust_env=False, **kwargs)

        return build

    def browser(self, endpoint, authorization_url):
        assert endpoint == ENDPOINT
        args = parse_qs(urlsplit(authorization_url).query)
        self.callback = args['redirect_uri'][0]
        parsed = urlsplit(self.callback)
        assert (parsed.scheme, parsed.hostname, parsed.path) == ('http', '127.0.0.1', '/callback')
        assert self.callback_task is None

        async def authorize():
            await asyncio.sleep(0.65)
            async with httpx2.AsyncClient(timeout=1, trust_env=False) as browser:
                return await browser.get(self.callback, params={'code': 'test-code', 'state': args['state'][0]})

        self.callback_task = asyncio.create_task(authorize())


class BridgeLoginSmoke(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = LoginFixture()
        self.start_patch('metadata_transport.metadata_client_factory', self.fixture.client_factory)
        self.start_patch('browser_authorization.open_browser_authorization', self.fixture.browser)
        # Compress the SDK's probe deadline for the negative control. Login
        # must avoid it; no SDK timeout setting is changed outside this test.
        self.start_patch(session, 'DISCOVER_TIMEOUT_SECONDS', 0.5)
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        self.cache = Path(cache.name)

    def start_patch(self, target, *args):
        mock = patch(target, *args) if isinstance(target, str) else patch.object(target, *args)
        self.addCleanup(mock.stop)
        mock.start()

    async def asyncTearDown(self):
        task = self.fixture.callback_task
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_old_auto_negotiation_times_out_after_browser_opens(self):
        def old_client(transport, **_kwargs):
            return Client(transport, timeout=0.5, init_timeout=3.3)

        with patch('fastmcp.Client', old_client), self.assertRaises(MCPError) as error:
            async with asyncio.timeout(5), await create_client(ENDPOINT, self.cache, allow_login=True):
                self.fail('short auto negotiation unexpectedly completed')
        self.assertEqual(-32001, error.exception.code)
        self.assertEqual("Request 'initialize' timed out", str(error.exception))
        self.assertEqual('server/discover', self.fixture.methods[0])
        self.assertIsNotNone(self.fixture.callback)
        self.assertNotIn(('POST', '/mailbox/token'), self.fixture.requests)

    async def test_login_survives_slow_discovery_and_worker_never_prompts(self):
        def scaled_client(transport, *, timeout, init_timeout, **kwargs):
            # Model discovery whose duration exceeds the former 10-second
            # request budget. Keep the real login/worker timeout ratio.
            return Client(transport, timeout=timeout / 20, init_timeout=init_timeout / 20, **kwargs)

        with patch('fastmcp.Client', scaled_client):
            async with asyncio.timeout(5), await create_client(ENDPOINT, self.cache, allow_login=True) as client:
                self.assertTrue(client.is_connected())
        self.assertEqual(200, (await self.fixture.callback_task).status_code)
        self.assertEqual('initialize', self.fixture.methods[0])
        self.assertNotIn('server/discover', self.fixture.methods)
        self.assertEqual(1, self.fixture.requests.count(('POST', '/mailbox/token')))

        worker = await create_client(ENDPOINT, self.cache)
        self.fixture.reject_tokens = True
        self.fixture.requests.clear()
        # Call the native OAuth transport directly so a deliberately rejected
        # worker request does not wait for the JSON-RPC response timeout.
        async with worker.transport.httpx_client_factory(auth=worker.transport.auth) as http:
            with self.assertRaisesRegex(AuthFailed, '^AUTH_REJECTED$'):
                await http.post(ENDPOINT, json={'method': 'initialize'})
        self.assertEqual([('POST', '/mailbox/mcp')], self.fixture.requests)


if __name__ == '__main__':
    unittest.main()
