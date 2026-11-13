"""Run in an isolated temporary schema in a dedicated *_test PostgreSQL database."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from threading import Event
from time import monotonic
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.auth.providers.github import GitHubProvider
from key_value.aio.stores.memory import MemoryStore
import httpx2 as httpx
from sqlalchemy import event
from uuid import uuid4

from mailbox_store import Store, _hash, _json
from platform_service.access import Access, AuthorizedSource, PlatformRoutes, Policy
from platform_service.database import PostgresStore, database_engine
from platform_service.http import build_api
from platform_service.migrate import copy_mailbox, reconcile_to_sqlite, upgrade
from platform_service.observations import Observations
from server import PublicPrefix, create_server
from smoke_mcp import run_http

PUBLIC = "https://example.test/mailbox"
API = "/mailbox/api/v1"
SOURCE_TOKEN = "synthetic-smoke-token"
KINDS = ["project", "task", "role", "workflow", "delegation", "blocker", "source"]
LEGACY = {"whoami", "mailbox_usage", "submit_job", "get_job", "list_events", "publish_result",
          "get_result", "request_cancel", "ack_result_read"}
PLATFORM = {"get_project_overview", "list_role_activity", "get_task_trace", "get_workflow_run",
            "list_blockers", "publish_observation_batch", "get_observation_receipt"}


def payload(content="报告" * 40000):
    return {"schema_version": 1, "origin_task_id": "legacy", "sender": "a",
            "recipient": "b\0c", "report_route": "mailbox:a",
            "target": {"intent": "data-only"}, "content": content}


def mailbox_checks(engine, directory):
    source = Path(directory) / "source.sqlite3"
    sqlite = Store(source)
    job_payload = payload()
    digest = _hash(_json(job_payload))
    job = sqlite.submit_job("historical", job_payload, digest)
    result = sqlite.publish_result("historical", "result1", "REPORTED_COMPLETED", {"result": "回执"})
    sqlite.request_cancel("historical", "cancel1")
    sqlite.ack_result_read("historical", "ack1", 1, result["result_hash"])
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE sqlite_sequence SET seq=90 WHERE name='events'")
        conn.execute("UPDATE events SET created_at='2026-11-13T00:00:00.500+00:00' WHERE cursor=1")
    report = copy_mailbox(engine, source)
    assert report["cursor_high_watermark"] == 90
    assert not report["verified_existing"]
    assert copy_mailbox(engine, source)["verified_existing"]
    store = PostgresStore(engine)
    assert store.get_job(job["job_id"]) == sqlite.get_job(job["job_id"])
    assert store.get_result(job["job_id"]) == sqlite.get_result(job["job_id"])
    assert store.list_events(recipient="b\0c") == sqlite.list_events(recipient="b\0c")
    assert store.publish_result("historical", "result1", "REPORTED_COMPLETED", {"result": "回执"}) == result
    physical_connections = []

    def on_connect(_connection, _record):
        physical_connections.append(True)

    event.listen(engine, "connect", on_connect)
    try:
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda index: store.publish_result(
                "historical", f"result-{index+2}", "UNKNOWN", {"index": index}), range(24)))
    finally:
        event.remove(engine, "connect", on_connect)
    assert not physical_connections, "serialized mailbox writes should reuse the warm connection"
    events = store.list_events(cursor=90)["events"]
    assert [e["cursor"] for e in events] == list(range(91, 115))
    assert [e["data"]["version"] for e in events] == list(range(2, 26))
    assert sorted(r["version"] for r in results) == list(range(2, 26))
    try:
        copy_mailbox(engine, source)
    except ValueError:
        pass
    else:
        raise AssertionError("migration retry must refuse divergence after PG writes")
    rollback = reconcile_to_sqlite(engine, source)
    assert rollback["cursor_high_watermark"] == 114
    assert sqlite.list_events() == store.list_events()
    assert sqlite.get_result("historical") == store.get_result("historical")
    assert copy_mailbox(engine, source)["verified_existing"]
    usage = store.stats()
    assert usage["usage_schema_version"] == 2 and usage["page_count"] is None
    assert usage["jobs"] == 1 and usage["results"] == 25 and usage["database_size_bytes"] > 0
    cross_store_cursor_order(engine, store)
    return store


def cross_store_cursor_order(engine, first):
    second = PostgresStore(engine)
    waiter_started = Event()
    waiter_pids = []

    def record_waiter(conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("SELECT value FROM platform_cursors") and "FOR UPDATE" in statement:
            waiter_pids.append(conn.connection.driver_connection.info.backend_pid)
            waiter_started.set()

    # Warm both sessions sequentially before the first takes the cursor lock.
    with engine.connect(), engine.connect():
        pass
    with ThreadPoolExecutor(1) as pool:
        with first._transaction() as locked:
            conn = locked.connection
            first_event = first._append_event(locked, "cross-store-first", "historical",
                                              "CANCEL_REQUESTED",
                                              _hash(_json({"job_id": "historical",
                                                           "event_type": "CANCEL_REQUESTED"})), {})
            event.listen(engine, "before_cursor_execute", record_waiter)
            try:
                pending = pool.submit(second.request_cancel, "historical", "cross-store-second")
                assert waiter_started.wait(5), "second Store did not request the cursor row lock"
                deadline = monotonic() + 3
                while monotonic() < deadline:
                    blocked_by_first = conn.exec_driver_sql(
                        "SELECT pg_backend_pid() = ANY(pg_blocking_pids(%s))", (waiter_pids[0],)
                    ).scalar_one()
                    if blocked_by_first:
                        break
                else:
                    raise AssertionError("PostgreSQL did not block the second Store on the first transaction")
                assert not pending.done()
            finally:
                event.remove(engine, "before_cursor_execute", record_waiter)
        second_event = pending.result(timeout=15)
    assert second_event["cursor"] == first_event["cursor"] + 1
    assert second.list_events(cursor=first_event["cursor"] - 1)["events"] == [first_event, second_event]


def observation(event_id, entity_type="task", entity_id="task", values=None,
                authority="native_observation", project="project", **kwargs):
    now = datetime.now(timezone.utc).isoformat()
    evidence = {"source_type": "synthetic", "ref": "evidence:synthetic",
                "observed_at": now}
    if authority is not None:
        evidence["authority"] = authority
    return {"event_id": event_id, "project_id": project, "entity_type": entity_type,
            "entity_id": entity_id, "event_type": "snapshot", "observed_at": now,
            "payload": values or {}, "evidence": [evidence], **kwargs}


def batch(batch_id, *events, source="trusted"):
    return {"schema_version": 1, "batch_id": batch_id, "source_id": source, "events": events}


def test_platform(engine, store):
    policy = Policy.model_validate({
        "viewers": {"legacy": ["project"], "agent": ["project"], "unrelated": ["other"]},
        "sources": {
            "trusted": {"projects": ["project"], "entity_types": KINDS,
                        "authorities": ["native_observation", "owner_decision", "self_report"],
                        "token_sha256": hashlib.sha256(SOURCE_TOKEN.encode()).hexdigest()},
            "agent": {"projects": ["project"], "entity_types": ["task", "role", "blocker", "workflow", "delegation"],
                      "mcp_login": "agent"},
            "private-other": {"projects": ["other"], "entity_types": ["task"], "mcp_login": "unrelated"},
            "peer": {"projects": ["project"], "entity_types": ["task", "blocker"],
                     "authorities": ["native_observation"]},
        },
    })
    access = Access(engine, policy, PUBLIC, "synthetic-app", "synthetic-secret", "z" * 64)
    access.github_identity = lambda code, verifier: ("legacy", "42")
    service = Observations(engine, policy)
    auth = StaticTokenVerifier(tokens={
        "legacy-token": {"client_id": "legacy", "scopes": [], "login": "legacy", "sub": "42"},
        "agent-token": {"client_id": "agent", "scopes": [], "login": "agent", "sub": "43"},
        "other-token": {"client_id": "other", "scopes": [], "login": "unrelated", "sub": "44"},
    })
    server = create_server(store, auth, "legacy", (service, access))
    app = PublicPrefix(PlatformRoutes(
        server.http_app(path="/mcp", stateless_http=True, json_response=True),
        build_api(service, access)), "/mailbox")
    headers = {"Authorization": f"Bearer {SOURCE_TOKEN}"}
    first = batch("b1", observation("e1", values={"title": "Latest", "followup_status": "PENDING",
                                                 "followup_revision": 38}, source_revision=38),
                  observation("owner", "task", "accepted", {"acceptance_status": "ACCEPTED"},
                              authority="owner_decision"),
                  observation("role", "role", "worker", {"kind": "worker"}, task_id="task"),
                  observation("flow", "workflow", "run", {"kind": "observed", "nodes": [
                      {"id": "one", "label": "One", "measured_duration_seconds": 4.026718375,
                       "measured_duration_basis": "Wrapper monotonic elapsed",
                       "evidence": [{"source_type": "synthetic", "ref": "evidence:timer", "observed_at": None,
                                     "authority": "native_observation", "digest": "a" * 64}]}]}, task_id="task"),
                  observation("block", "blocker", "block", {"state": "OPEN"}, task_id="task"),
                  observation("delegation", "delegation", "handoff", {"from_role_id": "owner"},
                              task_id="task"))
    github = GitHubProvider(client_id="synthetic-app", client_secret="synthetic-secret", base_url=PUBLIC,
                            issuer_url=PUBLIC, resource_base_url=PUBLIC, redirect_path="/auth/callback",
                            jwt_signing_key="z" * 64, client_storage=MemoryStore(), required_scopes=["read:user"])
    github_mcp = create_server(store, github, "legacy", (service, access))
    combined = PublicPrefix(PlatformRoutes(
        github_mcp.http_app(path="/mcp", stateless_http=True, json_response=True),
        build_api(service, access)), "/mailbox")
    with TestClient(combined, base_url=PUBLIC.removesuffix("/mailbox")) as client:
        discovery = client.get("/.well-known/oauth-authorization-server/mailbox")
        assert discovery.status_code == 200 and discovery.json()["issuer"] == PUBLIC
        protected = client.get("/.well-known/oauth-protected-resource/mailbox/mcp")
        assert protected.status_code == 200 and protected.json()["resource"] == PUBLIC + "/mcp"
        callback = client.get("/mailbox/auth/callback?state=legacy-test&code=test", follow_redirects=False)
        assert "OAUTH_STATE_INVALID" not in callback.text
        assert callback.status_code in (400, 302, 303)
    with TestClient(app, base_url=PUBLIC.removesuffix("/mailbox")) as client:
        missing = client.get(API + "/session")
        assert missing.status_code == 401 and missing.json()["login_url"] == API + "/session/login"
        assert client.get(API + "/projects/project/events").status_code == 401
        posted = client.post(API + "/observations/batches", json=first, headers=headers)
        assert posted.status_code == 200, posted.json()
        receipt = posted.json()
        assert len(receipt["accepted_event_ids"]) == 6
        assert client.post(API + "/observations/batches", json=first, headers=headers).json() == receipt
        assert client.get(API + "/observations/batches/b1", headers=headers).json() == receipt
        assert client.post(API + "/observations/batches",
                           json={**first, "events": [observation("diff")]}, headers=headers).status_code == 409
        replay = client.post(API + "/observations/batches",
                             json=batch("b2", first["events"][0]), headers=headers).json()
        assert replay["duplicate_event_ids"] == ["e1"] and replay["watermark"] == receipt["watermark"]
        assert client.post(API + "/observations/batches", headers=headers,
                           json=batch("bad", observation("bad", project="other"))).status_code == 403
        conflict = batch("atomic", observation("rollback", "task", "no-such-task"),
                         observation("e1", values={"title": "conflict"}))
        assert client.post(API + "/observations/batches", json=conflict, headers=headers).status_code == 409
        assert client.get(API + "/observations/batches/atomic", headers=headers).status_code == 404
        assert client.post(API + "/observations/batches", headers=headers,
                           content=b" " * (1048576 + 1)).status_code == 413
        login = client.get(API + "/session/login", follow_redirects=False)
        assert login.status_code == 302 and "Secure" in login.headers["set-cookie"]
        query = parse_qs(urlsplit(login.headers["location"]).query)
        assert query["redirect_uri"] == [PUBLIC + "/auth/callback"]
        assert client.get("/mailbox/auth/callback?state=web.invalid&code=test").status_code == 400
        callback = client.get("/mailbox/auth/callback",
                              params={"state": query["state"][0], "code": "test"}, follow_redirects=False)
        assert callback.status_code == 303 and callback.headers["location"] == "/mailbox/ui/"
        session = client.get(API + "/session").json()
        assert session["allowed_projects"] == ["project"] and session["subject"] == "42"
        assert client.get(API + "/projects/other/overview").status_code == 404
        assert client.get(API + "/tasks/no-such-task/trace").status_code == 404
        initial = client.get(API + "/projects/project/overview").json()
        assert initial["watermark"] == receipt["watermark"]
        assert initial["data"]["accepted_count"] == 1 and initial["data"]["blocked_count"] == 1
        session_cookie = client.cookies.get(access.cookie)
        assert client.get(API + "/projects/project/roles").json()["data"][0]["kind"] == "worker"
        node = client.get(API + "/workflows/run").json()["data"]["nodes"][0]
        assert node["id"] == "one" and node["measured_duration_seconds"] == 4.026718375
        assert node["measured_duration_basis"] == "Wrapper monotonic elapsed"
        assert node["started_at"] is None and node["finished_at"] is None
        assert node["evidence"][0]["digest"] == "a" * 64
        assert client.post(API + "/observations/batches", headers=headers, json=batch(
            "unverified-measurement", observation("bad-timer", "workflow", "run", {"kind": "observed",
                "nodes": [{"id": "n", "label": "N", "measured_duration_seconds": 4,
                           "measured_duration_basis": "Unverified"}]}))).status_code == 403
        assert client.get(API + "/projects/project/blockers").json()["data"][0]["task_id"] == "task"
        assert client.post(API + "/observations/batches", headers=headers, json=batch(
            "later", observation("older", values={"title": "Older"}, source_revision=37),
            observation("archive", values={"title": "Historical"}, historical=True, source_revision=100),
        )).status_code == 200
        trace = client.get(API + "/tasks/task/trace?limit=1").json()
        assert trace["data"]["title"] == "Latest" and trace["next_cursor"] is not None
        assert trace["data"]["delegations"][0]["task_id"] == "task"
        hints = service.events({"project"}, "project", initial["watermark"])
        assert [hint.entity_id for hint in hints] == ["task", "task"]
        assert client.post(API + "/session/logout").status_code == 403
        assert client.get(API + "/session").status_code == 200
        prior_session = session_cookie
        assert client.post(API + "/session/logout", headers={
            "Origin": PUBLIC.removesuffix("/mailbox"), "X-CSRF-Token": session["csrf_token"],
        }).status_code == 204
        assert client.get(API + "/session").status_code == 401

    async def check_mcp(origin):
        endpoint = origin + "/mailbox/mcp"
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO web_sessions VALUES (%s, 'legacy', '42', now()+interval '1 hour')",
                (access.digest(prior_session),))
        async with httpx.AsyncClient(timeout=5, headers={"Cookie": f"{access.cookie}={prior_session}"}) as http:
            path = origin + API + "/projects/project/events?after=" + initial["watermark"]
            async with http.stream("GET", path) as response:
                assert response.status_code == 200 and "text/event-stream" in response.headers["content-type"]
                lines = response.aiter_lines()
                first_id = (await anext(lines)).removeprefix("id: ")
                assert await anext(lines) == "event: observation"
                assert json.loads((await anext(lines)).removeprefix("data: "))["watermark"] == first_id
            async with http.stream("GET", path, headers={"Last-Event-ID": first_id}) as response:
                assert int((await anext(response.aiter_lines())).removeprefix("id: ")) == int(first_id) + 1
        async with Client(endpoint, auth="legacy-token") as legacy:
            assert {tool.name for tool in await legacy.list_tools()} == LEGACY | PLATFORM
            identity = (await legacy.call_tool("whoami")).data
            assert identity["github_login"] == "legacy" and identity["subject"] == "42"
            assert identity["service"] == "task-mailbox" and identity["execution_enabled"] is False
            assert identity["platform_access"] == {"allowed_projects": ["project"], "reporting_sources": []}
            assert (await legacy.call_tool("mailbox_usage")).data["usage_schema_version"] == 2
            assert (await legacy.call_tool("get_job", {"job_id": "historical"})).data["job_id"] == "historical"
            assert (await legacy.call_tool("list_events", {"cursor": 90})).data["events"][0]["cursor"] == 91
            content = payload()
            args = {"job_id": "mcp-test", "payload": content, "payload_hash": _hash(_json(content))}
            assert (await legacy.call_tool("submit_job", args)).data["payload"] == content
            result = (await legacy.call_tool("publish_result", {"job_id": "mcp-test",
                "event_id": "mcp-result", "status": "REPORTED_COMPLETED", "result": {"x": "y"}})).data
            assert (await legacy.call_tool("get_result", {"job_id": "mcp-test"})).data == result
            assert (await legacy.call_tool("request_cancel", {"job_id": "mcp-test",
                    "event_id": "mcp-cancel"})).data["event_type"] == "CANCEL_REQUESTED"
            assert (await legacy.call_tool("ack_result_read", {"job_id": "mcp-test", "event_id": "mcp-ack",
                    "result_hash": result["result_hash"], "version": 1})).data["event_type"] == "RESULT_READ_ACKNOWLEDGED"
        async with Client(endpoint, auth="agent-token") as agent:
            identity = (await agent.call_tool("whoami")).data
            assert identity["platform_access"] == {
                "allowed_projects": ["project"],
                "reporting_sources": [{"source_id": "agent", "projects": ["project"],
                                       "entity_types": ["blocker", "delegation", "role", "task", "workflow"],
                                       "authorities": ["self_report"]}],
            }
            assert (await agent.call_tool("get_job", {"job_id": "historical"}, raise_on_error=False)).is_error
            args = {"batch": batch("self", observation("report", values={
                "title": "Self", "reported_status": "REPORTED_COMPLETED", "current_handler_role_id": "fake",
                "accepted_at": "2026-11-14T00:00:00Z", "committed_deadline_at": "2026-11-15T00:00:00Z"},
                authority=None), observation("fake-blocker", "blocker", "fake-blocker",
                                              {"state": "RESOLVED"}, authority=None), source="agent")}
            receipt = (await agent.call_tool("publish_observation_batch", args)).data
            assert (await agent.call_tool("get_observation_receipt", {"batch_id": "self",
                    "source_id": "agent"})).data == receipt
            forged = {"batch": batch("forged", observation("forged", values={
                "acceptance_status": "ACCEPTED"}, authority="owner_decision"), source="agent")}
            assert (await agent.call_tool("publish_observation_batch", forged, raise_on_error=False)).is_error
            trace = (await agent.call_tool("get_task_trace", {"task_id": "task"})).data["data"]
            assert trace["title"] == "Latest" and trace["acceptance_status"] is None
            assert trace["events"][-1]["evidence"][0]["authority"] == "self_report"
            assert trace["accepted_at"] is trace["current_handler_role_id"] is trace["committed_deadline_at"] is None
            assert [item["id"] for item in (await agent.call_tool("list_blockers",
                    {"project_id": "project"})).data["data"]] == ["block"]
            peer = AuthorizedSource("peer", policy.sources["peer"])
            service.ingest(peer, _json(batch("peer", observation("peer", values={"title": "Conflicting"}),
                                            observation("peer-block", "blocker", "block", {"state": "RESOLVED"},
                                                        task_id="task"), source="peer")).encode())
            disputed = (await agent.call_tool("get_task_trace", {"task_id": "task"})).data["data"]
            assert disputed["title"] is None and disputed["unknown_reasons"]["title"] == "conflicting_source_observations"
            project = (await agent.call_tool("get_project_overview", {"project_id": "project"})).data["data"]
            assert project["blocked_count"] == 0 and project["unresolved_blocker_count"] == 1
        async with Client(endpoint, auth="other-token") as other:
            assert (await other.call_tool("get_task_trace", {"task_id": "task"}, raise_on_error=False)).is_error
    asyncio.run(run_http(app, check_mcp))


def main():
    engine = database_engine(os.environ["TASK_PLATFORM_TEST_DATABASE_URL"])
    try:
        if not engine.url.database or not engine.url.database.endswith("_test"):
            raise SystemExit("Use a dedicated PostgreSQL database with a _test suffix")
        schema = f"smoke_{uuid4().hex}"
        with engine.begin() as conn:
            conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
        engine.dispose()

        @event.listens_for(engine, "connect")
        def select_test_schema(dbapi_connection, _record):
            dbapi_connection.autocommit = True
            try:
                dbapi_connection.execute(f"SET search_path TO {schema}")
            finally:
                dbapi_connection.autocommit = False

        try:
            upgrade(engine)
            with TemporaryDirectory() as directory:
                store = mailbox_checks(engine, directory)
                test_platform(engine, store)
        finally:
            engine.dispose()
            event.remove(engine, "connect", select_test_schema)
            with engine.begin() as conn:
                conn.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
    finally:
        engine.dispose()
    print("PASS: PostgreSQL migration, legacy nine, observation replay and scopes, Web session flow with mocked GitHub")


if __name__ == "__main__":
    main()
