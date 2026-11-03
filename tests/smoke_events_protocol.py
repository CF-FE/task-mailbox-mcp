"""Loopback events protocol checks; no production OAuth, Dot, or callbacks."""
import asyncio
import json
from pathlib import Path
import socket
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
import httpx2 as httpx
import uvicorn

from event_protocol import install_events
from mailbox_store import Store
from server import PublicPrefix, create_server


class EventService:
    def __init__(self):
        self.calls = []

    async def handle(self, method, principal, params):
        self.calls.append((method, principal, params))
        if method == "events/list":
            return {"events": [{"name": "result.available", "delivery": ["webhook"],
                "inputSchema": {"type": "object"}, "payloadSchema": {"type": "object"}}]}
        if method == "events/subscribe":
            return {"id": "sub-test", "refreshBefore": "2026-11-04T00:00:00Z", "cursor": None, "truncated": False}
        return {}


async def main():
    with tempfile.TemporaryDirectory() as directory:
        service = EventService()
        auth = StaticTokenVerifier(tokens={
            "owner-test": {"client_id": "event-loopback", "scopes": [], "login": "TEST-OWNER", "sub": "github-user-1"},
            "wrong-test": {"client_id": "event-loopback", "scopes": [], "login": "other-owner", "sub": "github-user-2"},
            "missing-sub": {"client_id": "event-loopback", "scopes": [], "login": "test-owner"},
        })
        server = create_server(Store(Path(directory) / "mailbox.sqlite3"), auth, "test-owner")
        install_events(server, service, "test-owner")
        app = PublicPrefix(server.http_app(path="/mcp", stateless_http=True, json_response=True), "/mailbox")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0)); sock.listen()
        runtime = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="critical"))
        task = asyncio.create_task(runtime.serve(sockets=[sock]))
        try:
            for _ in range(500):
                if runtime.started: break
                if task.done(): await task
                await asyncio.sleep(0.01)
            assert runtime.started, "loopback startup timed out"
            url = "http://127.0.0.1:" + str(sock.getsockname()[1]) + "/mailbox/mcp"
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                async def call(method, params=None, token="owner-test", version="2025-11-25"):
                    request = {"jsonrpc": "2.0", "id": "event-smoke", "method": method}
                    if version == "2026-07-28":
                        params = {**(params or {})}
                        params["_meta"] = {**params.get("_meta", {}),
                            "io.modelcontextprotocol/protocolVersion": version,
                            "io.modelcontextprotocol/clientCapabilities": {}}
                    if params is not None: request["params"] = params
                    response = await client.post(url, json=request, headers={
                        "Authorization": "Bearer " + token, "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": version, "MCP-Method": method})
                    assert response.status_code == 200, (response.status_code, response.text)
                    return response.json()

                initialized = await call("initialize", {"protocolVersion": "2025-11-25",
                    "capabilities": {}, "clientInfo": {"name": "event-smoke", "version": "1.0"}})
                assert initialized["result"]["capabilities"].get("events") == {}, "initialize must advertise top-level events"
                modern = await call("server/discover", {}, version="2026-07-28")
                assert modern["result"]["capabilities"].get("events") == {}, "discover must advertise top-level events"
                assert modern["result"]["resultType"] == "complete"
                for version in ("2025-11-25", "2026-07-28"):
                    catalog = await call("events/list", {}, version=version)
                    assert catalog["result"]["events"][0]["name"] == "result.available"
                    params = {"name": "result.available", "arguments": {"job_id": "job-test"},
                        "delivery": {"mode": "webhook", "url": "https://callback.example.test/events", "secret": "synthetic-test-value"},
                        "ttlMs": None, "cursor": None, "_meta": {"example/test": "transport-only"}}
                    subscribed = await call("events/subscribe", params, version=version)
                    assert subscribed["result"]["id"] == "sub-test"
                    unsubscribed = await call("events/unsubscribe", {"name": params["name"],
                        "arguments": params["arguments"], "delivery": {"mode": "webhook", "url": params["delivery"]["url"]}}, version=version)
                    assert "error" not in unsubscribed
                    previous = len(service.calls)
                    for method in ("events/list", "events/subscribe", "events/unsubscribe"):
                        for token in ("wrong-test", "missing-sub"):
                            denied = await call(method, {}, token, version)
                            assert denied["error"]["code"] == -32001
                            assert "AUTHORIZATION_DENIED" in denied["error"]["message"]
                    assert len(service.calls) == previous, "denied accounts must not reach event service"
                expected_principal = json.dumps({"login": "test-owner", "subject": "github-user-1"}, sort_keys=True, separators=(",", ":"))
                assert all(principal == expected_principal for _, principal, _ in service.calls)
                assert all("_meta" not in params for _, _, params in service.calls)
                assert service.calls[1][2]["delivery"]["secret"] == "synthetic-test-value", "service owns subscription validation"
                assert service.calls[1][2]["ttlMs"] is None and service.calls[1][2]["cursor"] is None, "explicit null subscription options must remain distinct from omission"
                usage = await call("tools/call", {"name": "mailbox_usage", "arguments": {}})
                assert usage["result"].get("isError") is not True
                wrong_tool = await call("tools/call", {"name": "mailbox_usage", "arguments": {}}, "wrong-test")
                assert wrong_tool["result"]["isError"] is True
                unknown = await call("events/unknown", {})
                assert unknown["error"]["code"] == -32601
                response = await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "events/list", "params": {}}, headers={"Accept": "application/json, text/event-stream"})
                assert response.status_code == 401
        finally:
            runtime.should_exit = True
            await asyncio.wait_for(task, timeout=10)
            sock.close()
    print("PASS: legacy initialize and MCP 2 discover advertise top-level events; all three authenticated event methods, account/subject denial, original tool guard, unauthed 401")
    print("NOT_RUN: callback verification/delivery, production OAuth, cloud Dot subscription/read ACK")


if __name__ == "__main__":
    asyncio.run(main())
