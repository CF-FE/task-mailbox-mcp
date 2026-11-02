"""One bounded metadata retry using the pinned httpx2 2.16 private send hook.

The lower transport is deliberately untouched: ambient proxy and TLS behavior
remain the native client's. Never replay token, registration or mailbox writes.
"""

import asyncio

import httpx2


def metadata_client_factory(endpoint):
    url = httpx2.URL(endpoint)
    prefix = url.path.removesuffix('/mcp')
    origin = (url.scheme, url.host, url.port)
    paths = {'/.well-known/oauth-authorization-server' + prefix,
             '/.well-known/oauth-protected-resource' + prefix + '/mcp'}
    retry_available = True

    class MetadataRetryClient(httpx2.AsyncClient):
        async def _send_single_request(self, request):
            nonlocal retry_available
            target = request.url
            eligible = (request.method == 'GET' and not target.query and
                        (target.scheme, target.host, target.port) == origin and target.path in paths)
            if not eligible:
                return await super()._send_single_request(request)
            # Both attempts share this deadline; caller budgets may be shorter.
            async with asyncio.timeout(10):
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
