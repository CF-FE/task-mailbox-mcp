"""Dependency smoke: pinned HTTP transport recovery, no real network or tokens."""

import asyncio
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mac'))
import httpx2
from metadata_transport import metadata_client_factory


class MetadataTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_headers_disconnect_retries_once(self):
        for path in ('/.well-known/oauth-authorization-server/mailbox',
                     '/.well-known/oauth-protected-resource/mailbox/mcp'):
            request = httpx2.Request('GET', 'https://mcp.example.com' + path)
            reply = httpx2.Response(200, request=request, json={'test': 'metadata'})
            sender = AsyncMock(side_effect=[httpx2.RemoteProtocolError('test disconnect'), reply])
            async with metadata_client_factory('https://mcp.example.com/mailbox/mcp')() as client:
                with patch.object(httpx2.AsyncClient, '_send_single_request', sender):
                    self.assertEqual(200, (await client.send(request)).status_code)
            self.assertEqual(2, sender.await_count)

    async def test_one_extra_attempt_is_shared_by_client_factory(self):
        factory = metadata_client_factory('https://mcp.example.com/mailbox/mcp')
        request = httpx2.Request('GET', 'https://mcp.example.com/.well-known/oauth-authorization-server/mailbox')
        sender = AsyncMock(side_effect=httpx2.RemoteProtocolError('test disconnect'))
        for _ in range(2):
            async with factory() as client:
                with patch.object(httpx2.AsyncClient, '_send_single_request', sender):
                    with self.assertRaises(httpx2.RemoteProtocolError):
                        await client.send(request)
        self.assertEqual(3, sender.await_count)

    async def test_writes_other_urls_queries_and_timeout_are_never_replayed(self):
        cases = [('POST', 'https://mcp.example.com/mailbox/token'),
                 ('POST', 'https://mcp.example.com/mailbox/register'),
                 ('POST', 'https://mcp.example.com/mailbox/mcp'),
                 ('GET', 'https://mcp.example.com/mailbox/mcp'),
                 ('GET', 'https://other.example.com/.well-known/oauth-authorization-server/mailbox'),
                 ('GET', 'https://mcp.example.com/.well-known/oauth-authorization-server/mailbox?extra=1')]
        for method, url in cases:
            sender = AsyncMock(side_effect=httpx2.RemoteProtocolError('test disconnect'))
            async with metadata_client_factory('https://mcp.example.com/mailbox/mcp')() as client:
                with patch.object(httpx2.AsyncClient, '_send_single_request', sender):
                    with self.assertRaises(httpx2.RemoteProtocolError):
                        await client.request(method, url)
            self.assertEqual(1, sender.await_count)
        sender = AsyncMock(side_effect=httpx2.ConnectTimeout('test timeout'))
        async with metadata_client_factory('https://mcp.example.com/mailbox/mcp')() as client:
            with patch.object(httpx2.AsyncClient, '_send_single_request', sender):
                with self.assertRaises(httpx2.ConnectTimeout):
                    await client.get('https://mcp.example.com/.well-known/oauth-authorization-server/mailbox')
        self.assertEqual(1, sender.await_count)

    async def test_http_status_and_redirect_are_returned_without_retry_or_following(self):
        for status in (401, 403, 429, 500, 302):
            request = httpx2.Request('GET', 'https://mcp.example.com/.well-known/oauth-authorization-server/mailbox')
            sender = AsyncMock(return_value=httpx2.Response(status, request=request, headers={'location': 'https://other.example.com'}))
            async with metadata_client_factory('https://mcp.example.com/mailbox/mcp')(follow_redirects=True) as client:
                with patch.object(httpx2.AsyncClient, '_send_single_request', sender):
                    self.assertEqual(status, (await client.send(request)).status_code)
            self.assertEqual(1, sender.await_count)

    async def test_body_disconnect_after_headers_is_not_replayed(self):
        class BrokenBody(httpx2.AsyncByteStream):
            async def __aiter__(self):
                raise httpx2.RemoteProtocolError('test body disconnect')
                yield b''
        request = httpx2.Request('GET', 'https://mcp.example.com/.well-known/oauth-authorization-server/mailbox')
        reply = httpx2.Response(200, request=request, stream=BrokenBody())
        sender = AsyncMock(return_value=reply)
        async with metadata_client_factory('https://mcp.example.com/mailbox/mcp')() as client:
            with patch.object(httpx2.AsyncClient, '_send_single_request', sender):
                with self.assertRaises(httpx2.RemoteProtocolError):
                    await client.send(request)
        self.assertEqual(1, sender.await_count)

    async def test_deadline_includes_body_and_closes_without_retry(self):
        class SlowBody(httpx2.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(0.05)
                yield b'{}'
        request = httpx2.Request('GET', 'https://mcp.example.com/.well-known/oauth-authorization-server/mailbox')
        reply = httpx2.Response(200, request=request, stream=SlowBody())
        sender = AsyncMock(return_value=reply)
        original_timeout = asyncio.timeout
        async with metadata_client_factory('https://mcp.example.com/mailbox/mcp')() as client:
            with patch.object(httpx2.AsyncClient, '_send_single_request', sender):
                with patch('metadata_transport.asyncio.timeout', lambda _: original_timeout(0.02)):
                    with self.assertRaises(TimeoutError):
                        await client.send(request)
        self.assertEqual(1, sender.await_count)
        self.assertTrue(reply.is_closed)

    async def test_bridge_oauth_preflight_and_mcp_share_narrow_factory(self):
        from collector import create_client
        with tempfile.TemporaryDirectory() as temporary:
            bridge = await create_client('https://mcp.example.com/mailbox/mcp', Path(temporary) / 'oauth', allow_login=True)
            factory = bridge.transport.httpx_client_factory
            self.assertIs(factory, bridge.transport.auth.httpx_client_factory)
            request = httpx2.Request('GET', 'https://mcp.example.com/.well-known/oauth-authorization-server/mailbox')
            sender = AsyncMock(side_effect=[httpx2.RemoteProtocolError('test disconnect'), httpx2.Response(200, request=request)])
            async with bridge.transport.auth.httpx_client_factory() as client:
                with patch.object(httpx2.AsyncClient, '_send_single_request', sender):
                    self.assertEqual(200, (await client.send(request)).status_code)
            self.assertEqual(2, sender.await_count)

    async def test_login_hands_authorization_to_browser_without_headless_get(self):
        from collector import create_client
        with tempfile.TemporaryDirectory() as temporary:
            bridge = await create_client('https://mcp.example.com/mailbox/mcp', Path(temporary) / 'oauth', allow_login=True)
            url = 'https://mcp.example.com/mailbox/authorize?state=TEST_STATE'
            with patch('browser_authorization.webbrowser.open', return_value=True) as browser:
                with patch.object(httpx2.AsyncClient, '_send_single_request', AsyncMock(side_effect=AssertionError('headless authorization request'))) as sender:
                    with contextlib.redirect_stdout(io.StringIO()):
                        await bridge.transport.auth.redirect_handler(url)
            browser.assert_called_once_with(url)
            self.assertEqual(0, sender.await_count)

    async def test_default_proxy_tls_and_timeout_inputs_are_preserved(self):
        timeout = httpx2.Timeout(30, read=10)
        with patch.dict('os.environ', {'HTTPS_PROXY': 'http://127.0.0.1:9'}):
            async with httpx2.AsyncClient(timeout=timeout) as baseline:
                async with metadata_client_factory('https://mcp.example.com/mailbox/mcp')(timeout=timeout) as client:
                    self.assertEqual(baseline.trust_env, client.trust_env)
                    self.assertEqual(baseline.timeout, client.timeout)
                    self.assertEqual(type(baseline._transport_for_url(httpx2.URL('https://mcp.example.com'))),
                                     type(client._transport_for_url(httpx2.URL('https://mcp.example.com'))))
                    self.assertEqual(type(baseline._transport_for_url(httpx2.URL('https://mcp.example.com'))._pool),
                                     type(client._transport_for_url(httpx2.URL('https://mcp.example.com'))._pool))
                    self.assertFalse(client.follow_redirects)


if __name__ == '__main__':
    unittest.main()
