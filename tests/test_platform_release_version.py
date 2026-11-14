"""MCP initialization and OpenAPI identify the same application release."""

import asyncio
import importlib.util
import unittest


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in
                         ("fastmcp", "fastapi", "sqlalchemy", "psycopg")),
                     "requires service dependencies")
class ReleaseVersionTest(unittest.TestCase):
    def test_initialize_and_openapi_use_application_version(self):
        from fastmcp import Client
        from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
        from platform_service import APP_VERSION
        from platform_service.http import build_api
        from server import create_server

        async def probe():
            server = create_server(None, StaticTokenVerifier(tokens={}), "owner")
            async with Client(server, mode="legacy") as client:
                return client.initialize_result.server_info.version

        self.assertEqual(asyncio.run(probe()), APP_VERSION)
        self.assertEqual(build_api(None, None).openapi()["info"]["version"], APP_VERSION)
