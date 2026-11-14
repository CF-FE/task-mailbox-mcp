"""One bounded metadata retry using the pinned httpx2 2.16 private send hook.

The lower transport is deliberately untouched: ambient proxy and TLS behavior
remain the native client's. Never replay token, registration or mailbox writes.
"""

import asyncio
import json
import time

import httpx2


def metadata_client_factory(endpoint, *, observe=None, token_endpoint=None, metadata_timeout=30):
    url = httpx2.URL(endpoint)
    prefix = url.path.removesuffix('/mcp')
    origin = (url.scheme, url.host, url.port)
    paths = {'/.well-known/oauth-authorization-server' + prefix,
             '/.well-known/oauth-protected-resource' + prefix + '/mcp'}
    retry_available = True
    token_url = httpx2.URL(token_endpoint) if token_endpoint else None

    class MetadataRetryClient(httpx2.AsyncClient):
        async def _send_single_request(self, request):
            target = request.url
            eligible = (request.method == 'GET' and not target.query and
                        (target.scheme, target.host, target.port) == origin and target.path in paths)
            phase = None
            if observe is not None:
                if eligible:
                    phase = "OAUTH_METADATA"
                elif request.method == "POST" and target == token_url:
                    phase = "OAUTH_REFRESH"
                elif request.method == "POST" and target == url:
                    phase = "MCP_POST"
                    try:
                        body = request.content
                        if len(body) <= 4096:
                            rpc = json.loads(body)
                            if isinstance(rpc, dict):
                                phase = {"initialize": "MCP_INITIALIZE", "server/discover": "MCP_DISCOVER"}.get(
                                    rpc.get("method") if isinstance(rpc.get("method"), str) else "", phase)
                    except (ValueError, RecursionError, httpx2.RequestNotRead):
                        pass
            if phase is None:
                return await self._send_with_metadata_retry(request, eligible)
            start = time.monotonic()
            response = None
            try:
                response = await self._send_with_metadata_retry(request, eligible)
                return response
            finally:
                observe(phase, int((time.monotonic() - start) * 1000),
                        None if response is None else response.status_code)

        async def _send_with_metadata_retry(self, request, eligible):
            nonlocal retry_available
            if not eligible:
                return await super()._send_single_request(request)
            # Both attempts share this deadline; caller budgets may be shorter.
            async with asyncio.timeout(metadata_timeout):
                try:
                    response = await super()._send_single_request(request)
                except httpx2.RemoteProtocolError:
                    if not retry_available:
                        raise
                    retry_available = False
                    response = await super()._send_single_request(request)
                # Metadata is finite JSON. Reading it here includes the body in
                # the same deadline, outside the retry catch: never replay it.
                try:
                    await response.aread()
                except BaseException:
                    await response.aclose()
                    raise
                return response

    def factory(**kwargs):
        # FastMCP passes True here; preserve this service's SDK default False.
        kwargs.pop('follow_redirects', None)
        return MetadataRetryClient(follow_redirects=False, **kwargs)

    return factory
