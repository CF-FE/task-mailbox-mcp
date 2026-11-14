"""Storage failure diagnostics expose only bounded fields."""

import importlib.util
import json
import unittest


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in
                         ("fastapi", "sqlalchemy", "psycopg")), "requires platform dependencies")
class StorageDiagnosticsTest(unittest.TestCase):
    def test_receipt_storage_error_logs_operation_and_valid_sqlstate_only(self):
        from psycopg.errors import ConnectionTimeout
        from fastapi.testclient import TestClient
        from sqlalchemy.exc import OperationalError, TimeoutError
        from platform_service.http import build_api

        class DriverError(Exception):
            pass

        class Access:
            def source(self, request):
                return None

        class Service:
            def receipt(self, source, batch_id):
                raise error

        with TestClient(build_api(Service(), Access())) as client:
            for original, invalidated, state, expected in (
                (DriverError("private DSN"), False, "55P03", "database_sqlstate"),
                (DriverError("private DSN"), False, "PRIVATE", "driver_error"),
                (DriverError("private DSN"), True, None, "connection_invalidated"),
                (ConnectionTimeout("private DSN"), True, None, "connection_timeout"),
                (None, False, None, "pool_timeout"),
            ):
                with self.subTest(expected=expected):
                    if original is None:
                        error = TimeoutError("private pool details")
                    else:
                        original.sqlstate = state
                        error = OperationalError("private SQL", {"value": "private params"}, original,
                                                 connection_invalidated=invalidated)
                    with self.assertLogs("task_mailbox.storage", level="WARNING") as records:
                        response = client.get("/api/v1/observations/batches/private-batch?query=private-query",
                                              headers={"Authorization": "Bearer private-token"})
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()["error"]["code"], "STORAGE_UNAVAILABLE")
                    retry_expected = expected in {"connection_invalidated", "connection_timeout"}
                    self.assertEqual(len(records.records), 2 if retry_expected else 1)
                    if retry_expected:
                        retry_record = json.loads(records.records[0].getMessage())
                        self.assertRegex(retry_record.pop("request_id"), r"^[0-9a-f]{32}$")
                        self.assertEqual(retry_record, {
                            "operation": "receipt", "category": expected, "sqlstate": None,
                            "next_attempt": 2,
                        })
                    record = json.loads(records.records[-1].getMessage())
                    self.assertEqual(record, {
                        "operation": "receipt", "request_id": response.json()["request_id"],
                        "category": expected, "sqlstate": "55P03" if expected == "database_sqlstate" else None,
                    })

    def test_mcp_receipt_sqlalchemy_failure_preserves_original_machine_code(self):
        import asyncio

        from fastmcp import Client, FastMCP
        from sqlalchemy.exc import OperationalError
        from platform_service.mcp import add_tools

        class LockFailure(Exception):
            sqlstate = "55P03"

        class Service:
            overview = roles = trace = workflow = blockers = None

            def receipt(self, source, batch_id):
                raise OperationalError("private SQL", {"param": "private parameter"},
                                       LockFailure("private driver message"))

        class Access:
            def mcp_source(self, login, source_id):
                return None

        async def request():
            server = FastMCP("diagnostic-test")
            add_tools(server, Service(), Access(), {}, {})
            async with Client(server, mode="legacy") as client:
                return await client.call_tool("get_observation_receipt", {
                    "source_id": "private-source", "batch_id": "private-batch",
                }, raise_on_error=False)

        with self.assertLogs("task_mailbox.storage", level="WARNING") as records:
            result = asyncio.run(request())
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0].text, "STORAGE_UNAVAILABLE: reconcile using the original IDs")
        self.assertEqual(len(records.records), 1)
        record = json.loads(records.records[0].getMessage())
        self.assertRegex(record.pop("request_id"), r"^[0-9a-f]{32}$")
        self.assertEqual(record, {"operation": "get_observation_receipt",
                                  "category": "database_sqlstate", "sqlstate": "55P03"})

    def test_legacy_list_events_logs_sanitized_storage_failure_and_preserves_wire_error(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        from fastmcp import Client
        from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
        from sqlalchemy.exc import OperationalError, TimeoutError
        from server import create_server

        class DriverError(Exception):
            def __init__(self, sqlstate):
                super().__init__("private DSN and driver details")
                self.sqlstate = sqlstate

        class Store:
            def list_events(self, cursor, limit, recipient):
                raise error

        async def request():
            auth = StaticTokenVerifier(tokens={})
            server = create_server(Store(), auth, "owner", (None, object()))
            token = SimpleNamespace(claims={"login": "owner"})
            with patch("server.get_access_token", return_value=token):
                async with Client(server, mode="legacy") as client:
                    return await client.call_tool("list_events", {
                        "cursor": 24, "limit": 10, "recipient": "private recipient",
                    }, raise_on_error=False)

        for sqlstate, invalidated, category, code in (
            ("53100", False, "database_sqlstate", "STORAGE_FULL"),
            ("55P03", False, "database_sqlstate", "STORAGE_UNAVAILABLE"),
            (None, True, "connection_invalidated", "STORAGE_UNAVAILABLE"),
            (None, False, "pool_timeout", "STORAGE_UNAVAILABLE"),
        ):
            with self.subTest(category=category, sqlstate=sqlstate):
                if category == "pool_timeout":
                    error = TimeoutError("private pool details")
                else:
                    error = OperationalError("private SQL", {"recipient": "private parameter"},
                                             DriverError(sqlstate), connection_invalidated=invalidated)
                with self.assertLogs("task_mailbox.storage", level="WARNING") as records:
                    result = asyncio.run(request())
                self.assertTrue(result.is_error)
                self.assertEqual(result.content[0].text,
                                 f"{code}: no success acknowledged; query original job/event IDs")
                self.assertEqual(len(records.records), 1)
                self.assertIsNone(records.records[0].exc_info)
                record = json.loads(records.records[0].getMessage())
                self.assertRegex(record.pop("request_id"), r"^[0-9a-f]{32}$")
                self.assertEqual(record, {"operation": "list_events", "category": category,
                                          "sqlstate": sqlstate})

    def test_startup_still_exits_tempfail_and_logs_driver_category(self):
        import os
        import runpy
        from unittest.mock import patch

        from sqlalchemy.exc import OperationalError, SQLAlchemyError

        class ConnectionTimeout(Exception):
            pass

        env = {
            "TASK_MAILBOX_PUBLIC_BASE_URL": "https://example.test/mailbox",
            "TASK_MAILBOX_GITHUB_CLIENT_ID": "synthetic-app",
            "TASK_MAILBOX_GITHUB_CLIENT_SECRET": "synthetic-secret",
            "TASK_MAILBOX_ALLOWED_GITHUB_USER": "owner",
            "TASK_MAILBOX_SIGNING_KEY": "z" * 64,
            "FASTMCP_HOME": "/tmp/synthetic-oauth",
            "TASK_MAILBOX_DATA_DIR": "/tmp/synthetic-data",
            "TASK_MAILBOX_STORAGE_ENGINE": "postgresql",
            "TASK_PLATFORM_DATABASE_URL": "synthetic",
            "TASK_PLATFORM_ACCESS_FILE": "synthetic",
        }
        for error, status, category in (
            (OperationalError("private query", {}, ConnectionTimeout("private DSN")), 75, "connection_timeout"),
            (SQLAlchemyError("private failure"), 78, "sqlalchemy_error"),
        ):
            with (
                self.subTest(category=category),
                patch.dict(os.environ, env, clear=True),
                patch("os.umask"),
                patch("pathlib.Path.mkdir"),
                patch("fastmcp.server.auth.providers.github.GitHubProvider"),
                patch("oauth_storage.build_oauth_storage"),
                patch("platform_service.database.configured_engine", side_effect=error),
                self.assertLogs("task_mailbox.storage", level="WARNING") as records,
                self.assertRaises(SystemExit) as raised,
            ):
                runpy.run_module("server", run_name="__main__")
            self.assertEqual(raised.exception.code, status)
            self.assertEqual(len(records.records), 1)
            record = json.loads(records.records[0].getMessage())
            self.assertRegex(record.pop("request_id"), r"^[0-9a-f]{32}$")
            self.assertEqual(record, {"operation": "startup", "category": category, "sqlstate": None})
