"""Verify the opt-in ASGI diagnostic fields and transport transparency."""

import asyncio
import importlib.util
import json
import unittest


@unittest.skipUnless(importlib.util.find_spec("fastmcp"), "requires service dependencies")
class RequestDiagnosticsTest(unittest.TestCase):
    def test_allowlisted_route_preserves_response_and_excludes_request_data(self):
        from server import RequestDiagnostics

        async def receive():
            raise AssertionError("Middleware must not read the request body")

        async def downstream(scope, receive_body, send):
            self.assertIs(receive_body, receive)
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"www-authenticate", b"Bearer private-header")]})
            await send({"type": "http.response.body", "body": b"private-response"})

        async def exercise():
            outgoing = []

            async def send(message):
                outgoing.append(message)

            app = RequestDiagnostics(downstream, "/mailbox")
            await app({"type": "http", "method": "POST", "path": "/mailbox/mcp",
                       "headers": [(b"authorization", b"Bearer private-token"),
                                   (b"cookie", b"private-cookie")],
                       "query_string": b"code=private-code", "client": ("192.0.2.1", 8888)}, receive, send)
            self.assertEqual(outgoing[0]["headers"], [(b"www-authenticate", b"Bearer private-header")])
            self.assertEqual(outgoing[1]["body"], b"private-response")

        with self.assertLogs("task_mailbox.request", level="INFO") as logs:
            asyncio.run(exercise())
        messages = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual(len({m["request_id"] for m in messages}), 1)
        self.assertEqual([m["phase"] for m in messages],
                         ["arrived", "response_started", "response_finished"])
        self.assertEqual(messages[0]["route"], "/mcp")
        self.assertEqual(messages[1]["status"], 401)
        self.assertGreaterEqual(messages[2]["duration_ms"], messages[1]["duration_ms"])
        self.assertNotIn("private-", str(messages))
        self.assertNotIn("192.0.2.1", str(messages))

    def test_failed_response_records_incomplete_without_swallowing_error(self):
        from server import RequestDiagnostics

        async def downstream(scope, receive, send):
            raise RuntimeError("private-error")

        app = RequestDiagnostics(downstream, "/mailbox")
        with self.assertLogs("task_mailbox.request", level="INFO") as logs:
            with self.assertRaisesRegex(RuntimeError, "private-error"):
                asyncio.run(app({"type": "http", "path": "/mailbox/token", "method": "POST"}, None, None))
        messages = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual(messages[-1]["phase"], "response_incomplete")
        self.assertIsNone(messages[-1]["status"])
        self.assertNotIn("private-error", str(messages))

    def test_unknown_and_web_routes_do_not_emit(self):
        from server import RequestDiagnostics

        async def downstream(scope, receive, send):
            return

        app = RequestDiagnostics(downstream, "/mailbox")
        with self.assertNoLogs("task_mailbox.request", level="INFO"):
            for path in ("/mailbox/ui/", "/mailbox/mcp/private-path", "/unrelated"):
                asyncio.run(app({"type": "http", "path": path, "method": "GET"}, None, None))
