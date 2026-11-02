"""Loopback MCP contract checks; not production OAuth or actual Dot evidence."""

import asyncio
import hashlib
import json
from pathlib import Path
import secrets
import socket
import resource
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import Client
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from key_value.aio.stores.memory import MemoryStore
import uvicorn

from mailbox_store import Store
from server import PublicPrefix, create_server


def read_http(url, mcp_request=False):
    request = url
    if mcp_request:
        request = Request(url, data=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "unauthenticated-smoke", "version": "0.1.0"}},
        }).encode(), headers={"Content-Type": "application/json",
                              "Accept": "application/json, text/event-stream"})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers), error.read()


async def run_http(app, check):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(500):
            if server.started:
                break
            if task.done():
                await task
                raise AssertionError("test HTTP server exited before startup")
            await asyncio.sleep(0.01)
        assert server.started, "test HTTP server startup timed out"
        await check(f"http://127.0.0.1:{port}")
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        sock.close()


async def check_transport(directory):
    token = secrets.token_urlsafe(32)
    auth = StaticTokenVerifier(tokens={
        token: {"client_id": "loopback-test", "scopes": [], "login": "test-owner",
                "sub": "test-subject"},
        "wrong-account": {"client_id": "other", "scopes": [], "login": "other"},
    })
    mcp = create_server(Store(directory / "transport.sqlite3"), auth, "test-owner")
    app = PublicPrefix(mcp.http_app(path="/mcp", stateless_http=True,
                                    json_response=True), "/mailbox")

    async def check(origin):
        status, _, _ = await asyncio.to_thread(read_http, origin + "/mailbox/mcp", True)
        assert status == 401, status
        status, _, _ = await asyncio.to_thread(read_http, origin + "/mcp")
        assert status == 404, status
        url = origin + "/mailbox/mcp"
        async with Client(url, auth=token) as client_a:
            tools = await client_a.list_tools()
            assert {tool.name for tool in tools} == {
                "whoami", "submit_job", "get_job", "list_events", "publish_result",
                "get_result", "request_cancel", "ack_result_read", "mailbox_usage",
            }
            identity = (await client_a.call_tool("whoami")).data
            assert identity["github_login"] == "test-owner"
            assert identity["execution_enabled"] is False
            usage = (await client_a.call_tool("mailbox_usage")).data
            assert usage["database_limit_bytes"] == 268435456
            assert usage["max_jobs"] == 10000
            assert "path" not in usage
            payload = {"schema_version": 1, "origin_task_id": "loopback-test",
                       "sender": "simulated-client-a", "recipient": "simulated-client-b",
                       "report_route": "mailbox:simulated-client-a",
                       "target": {"test": "data-only"},
                       "content": secrets.token_hex(8) + "-" + "报告" * 40000}
            encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False).encode()
            arguments = {"job_id": "transport-test", "payload": payload,
                         "payload_hash": hashlib.sha256(encoded).hexdigest()}
            job = (await client_a.call_tool("submit_job", arguments)).data
            assert (await client_a.call_tool("submit_job", arguments)).data == job
        async with Client(url, auth=token) as client_b:
            original = (await client_b.call_tool("get_job", {"job_id": "transport-test"})).data
            assert original["payload"] == payload
            result_args = {"job_id": "transport-test", "event_id": "reply-1",
                           "status": "REPORTED_COMPLETED",
                           "result": {"nonce_reversed": payload["content"][::-1]}}
            result = (await client_b.call_tool("publish_result", result_args)).data
            assert (await client_b.call_tool("publish_result", result_args)).data == result
        async with Client(url, auth=token) as client_a:
            received = (await client_a.call_tool("get_result", {"job_id": "transport-test"})).data
            assert received["result"]["nonce_reversed"] == payload["content"][::-1]
            ack = (await client_a.call_tool("ack_result_read", {
                "job_id": "transport-test", "event_id": "read-1", "version": 1,
                "result_hash": received["result_hash"],
            })).data
            assert ack["event_type"] == "RESULT_READ_ACKNOWLEDGED"
            events = (await client_a.call_tool("list_events", {"cursor": 0})).data
            assert len(events["events"]) == 3
        async with Client(url, auth="wrong-account") as unauthorized:
            response = await unauthorized.call_tool("get_job", {"job_id": "transport-test"},
                                                    raise_on_error=False)
            assert response.is_error
    await run_http(app, check)


async def check_oauth_discovery(directory):
    public = "https://mcp.example.com/mailbox"
    auth = GitHubProvider(client_id="not-a-real-app", client_secret="not-a-real-secret",
                          base_url=public, issuer_url=public,
                          resource_base_url=public, redirect_path="/auth/callback",
                          jwt_signing_key="x" * 64, client_storage=MemoryStore(),
                          required_scopes=["read:user"])
    server = create_server(Store(directory / "discovery.sqlite3"), auth, "test-owner")
    app = PublicPrefix(server.http_app(path="/mcp", stateless_http=True,
                                       json_response=True), "/mailbox")

    async def check(origin):
        status, headers, _ = await asyncio.to_thread(read_http, origin + "/mailbox/mcp", True)
        assert status == 401, status
        challenge = next(value for key, value in headers.items()
                         if key.lower() == "www-authenticate")
        assert public.removesuffix("/mailbox") + "/.well-known/oauth-protected-resource/mailbox/mcp" in challenge
        status, _, raw = await asyncio.to_thread(
            read_http, origin + "/.well-known/oauth-authorization-server/mailbox")
        assert status == 200, (status, raw)
        metadata = json.loads(raw)
        assert metadata["issuer"] == public
        assert metadata["authorization_endpoint"] == public + "/authorize"
        assert metadata["token_endpoint"] == public + "/token"
        status, _, raw = await asyncio.to_thread(
            read_http, origin + "/.well-known/oauth-protected-resource/mailbox/mcp")
        assert status == 200, (status, raw)
        resource = json.loads(raw)
        assert resource["resource"] == public + "/mcp"
        assert resource["authorization_servers"] == [public]
    await run_http(app, check)


async def main():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        await check_transport(path)
        await check_oauth_discovery(path)
    print("PASS: loopback MCP catalog, two simulated clients, dedup, result/hash ACK, account guard, OAuth discovery")
    print("PROCESS_PEAK_RSS_KIB", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    print("NOT_RUN: real GitHub consent, VPS public ingress, cloud registration, actual Dot/local-client nonce, Herdr execution")


if __name__ == "__main__":
    asyncio.run(main())
