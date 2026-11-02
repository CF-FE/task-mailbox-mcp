"""Standard-library collector evidence using the real local ledger, no network."""

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mac.bridge_store import BridgeStore
from mac.collector import (AuthFailed, AuthRequired, Limits, collect_once,
                           create_client, run_collector)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def job(job_id="job-1"):
    payload = {"schema_version": 1, "origin_task_id": "origin-1", "sender": "cloud",
        "recipient": "mac", "report_route": "cloud", "target": {"session": "test"},
        "content": "Data only, do not execute"}
    return {"job_id": job_id, "payload": payload, "payload_hash": digest(payload),
        "status": "PERSISTED", "cancel_requested": False,
        "latest_result_version": 0, "created_at": "2026-11-02T00:00:00Z"}


def event(cursor=1, kind="JOB_PERSISTED", data=None, job_id="job-1"):
    if data is None:
        data = {"payload_hash": job(job_id)["payload_hash"], "status": "PERSISTED"}
    return {"cursor": cursor, "event_id": f"event-{cursor}", "job_id": job_id,
        "event_type": kind, "created_at": "2026-11-02T00:00:00Z", "data": data}


class FakeTransport:
    def __init__(self, events=(), jobs=None, results=None):
        self.events, self.jobs = list(events), jobs or {"job-1": job()}
        self.results, self.calls, self.reports, self.failures = results or {}, [], {}, {}

    async def __call__(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        failure = self.failures.get(name)
        if failure is not None:
            if isinstance(failure, list):
                failure = failure.pop(0) if failure else None
            if failure is not None:
                raise failure
        if name == "list_events":
            if "recipient" in arguments:
                raise AssertionError("The collector must consume the global stream")
            remaining = [item for item in self.events if item["cursor"] > arguments["cursor"]]
            page = remaining[:arguments["limit"]]
            return {"events": deepcopy(page), "has_more": len(remaining) > len(page),
                "next_cursor": page[-1]["cursor"] if page else arguments["cursor"]}
        if name == "get_job":
            return deepcopy(self.jobs[arguments["job_id"]])
        if name == "get_result":
            return deepcopy(self.results[(arguments["job_id"], arguments["version"])])
        if name == "publish_result":
            key = arguments["event_id"]
            receipt = {key: arguments[key] for key in ("job_id", "event_id", "status")}
            receipt.update(version=1, result_hash=digest(arguments["result"]))
            if key in self.reports and self.reports[key] != receipt:
                raise AssertionError("Conflicting event ID")
            self.reports[key] = receipt
            return deepcopy(receipt)
        raise AssertionError(name)


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = BridgeStore(Path(self.temp.name) / "bridge.sqlite3", "test-source")

    def tearDown(self):
        self.temp.cleanup()

    async def test_global_pagination_and_restart_deduplicate(self):
        events = [event(index, job_id=f"job-{index}") for index in range(1, 4)]
        transport = FakeTransport(events, {f"job-{i}": job(f"job-{i}") for i in range(1, 4)})
        result = await collect_once(self.store, transport, limits=Limits(page_size=1))
        self.assertEqual((result["state"], result["cursor"], len(result["verified"])), ("OK", 3, 3))
        self.assertEqual(sum(name == "list_events" for name, _ in transport.calls), 3)
        transport.calls.clear()
        again = await collect_once(self.store, transport)
        self.assertEqual(again["verified"], [])
        self.assertEqual([name for name, _ in transport.calls], ["list_events"])
        self.assertEqual(self.store.snapshot()["counts"]["jobs"], 3)

    async def test_failed_fetch_does_not_advance_cursor(self):
        transport = FakeTransport([event()])
        transport.failures["list_events"] = OSError("private raw URL must not be stored")
        result = await collect_once(self.store, transport)
        self.assertEqual((result["state"], self.store.cursor()), ("NETWORK_FAILED", 0))
        self.assertNotIn("private", str(self.store.snapshot()["health"]))

    async def test_received_page_survives_later_reconciliation_failure(self):
        transport = FakeTransport([event()])
        transport.failures["get_job"] = [OSError("offline"), None]
        first = await collect_once(self.store, transport)
        self.assertEqual((first["state"], first["cursor"], first["pending_events"]), ("NETWORK_FAILED", 1, 1))
        restarted = BridgeStore(self.store.path, "test-source")
        second = await collect_once(restarted, transport)
        self.assertEqual((second["state"], second["pending_events"]), ("OK", 0))

    async def test_unknown_event_is_held_without_model_or_retry(self):
        transport = FakeTransport([event(kind="FUTURE_MESSAGE", data={})])
        result = await collect_once(self.store, transport)
        self.assertEqual(result["state"], "OK")
        self.assertEqual(self.store.snapshot()["counts"]["held_events"], 1)
        self.assertFalse(any(name == "get_job" for name, _ in transport.calls))
        transport.calls.clear()
        await collect_once(self.store, transport)
        self.assertEqual([name for name, _ in transport.calls], ["list_events"])

    async def test_hash_mismatch_is_held(self):
        wrong = job()
        wrong["payload"]["content"] = "tampered"
        result = await collect_once(self.store, FakeTransport([event()], {"job-1": wrong}))
        self.assertEqual(result["state"], "OK")
        self.assertEqual(result["verified"], [])
        self.assertEqual(self.store.snapshot()["counts"]["held_events"], 1)

    async def test_result_and_ack_read_exact_version_without_auto_ack(self):
        raw = {"reply": "real output"}
        details = {"version": 2, "result_hash": digest(raw), "status": "UNKNOWN"}
        result_event = event(1, "RESULT_REPORTED", details)
        ack_event = event(2, "RESULT_READ_ACKNOWLEDGED", {"version": 2, "result_hash": digest(raw)})
        record = job()
        record.update(status="UNKNOWN", latest_result_version=2)
        result_record = {"job_id": "job-1", "event_id": "event-1", "version": 2,
            "result_hash": digest(raw), "result": raw, "status": "UNKNOWN"}
        transport = FakeTransport([result_event, ack_event], {"job-1": record}, {("job-1", 2): result_record})
        result = await collect_once(self.store, transport)
        self.assertEqual(len(result["verified"]), 2)
        queries = [args for name, args in transport.calls if name == "get_result"]
        self.assertEqual(queries, [{"job_id": "job-1", "version": 2}] * 2)
        self.assertEqual(self.store.pending_reports(), [])
        self.assertTrue(all(name in {"list_events", "get_job", "get_result"} for name, _ in transport.calls))

    async def test_result_identity_mismatch_is_held(self):
        raw = {"reply": "output"}
        record = job()
        record.update(status="UNKNOWN", latest_result_version=1)
        data = {"version": 1, "result_hash": digest(raw), "status": "UNKNOWN"}
        result_record = {"job_id": "other-job", "version": 1, "result_hash": digest(raw),
            "result": raw, "status": "UNKNOWN", "event_id": "event-1"}
        transport = FakeTransport([event(1, "RESULT_REPORTED", data)], {"job-1": record}, {("job-1", 1): result_record})
        result = await collect_once(self.store, transport)
        self.assertEqual(result["verified"], [])
        self.assertEqual(self.store.snapshot()["counts"]["held_events"], 1)

    async def test_ack_loss_retransmits_same_saved_event_once(self):
        self.store.record_job(job())
        self.store.queue_report("job-1", "saved-report-1", "UNKNOWN", {"output": "saved"})
        transport = FakeTransport()
        lose_ack = True

        async def call(name, arguments):
            nonlocal lose_ack
            receipt = await transport(name, arguments)
            if name == "publish_result" and lose_ack:
                lose_ack = False
                raise OSError("ACK lost after server committed")
            return receipt

        first = await collect_once(self.store, call)
        self.assertEqual((first["state"], len(self.store.pending_reports())), ("NETWORK_FAILED", 1))
        second = await collect_once(self.store, call)
        self.assertEqual((second["state"], second["sent"], len(self.store.pending_reports())), ("OK", 1, 0))
        publishes = [args for name, args in transport.calls if name == "publish_result"]
        self.assertEqual(publishes[0], publishes[1])
        self.assertEqual(len(transport.reports), 1)

    async def test_rpc_and_page_budgets_preserve_pending(self):
        transport = FakeTransport([event()])
        first = await collect_once(self.store, transport, limits=Limits(rpc=1))
        self.assertEqual((first["state"], first["calls"], first["pending_events"]), ("BUDGET_EXHAUSTED", 1, 1))
        second = await collect_once(self.store, transport)
        self.assertEqual(second["pending_events"], 0)
        more = FakeTransport([event(2, "FUTURE_MESSAGE", {}), event(3, "FUTURE_MESSAGE", {})])
        third = await collect_once(self.store, more, limits=Limits(page_size=1, pages=1))
        self.assertEqual((third["state"], third["cursor"]), ("BUDGET_EXHAUSTED", 2))

    async def test_timeout_is_bounded_and_does_not_persist_cursor(self):
        async def slow(name, arguments):
            await asyncio.sleep(5)
        result = await collect_once(self.store, slow, limits=Limits(call_timeout=0.01, round_timeout=0.02))
        self.assertEqual((result["state"], result["cursor"]), ("NETWORK_FAILED", 0))

    async def test_auth_failure_stops_worker_without_relogin(self):
        calls = []
        async def factory():
            calls.append("factory")
            raise AuthFailed("revoked")
        await run_collector(self.store, factory, asyncio.Event())
        self.assertEqual(calls, ["factory"])
        self.assertEqual(self.store.snapshot()["health"][0]["state"], "AUTH_FAILED")
        for failure, expected in ((AuthRequired("missing"), "AUTH_REQUIRED"),
                                  (RuntimeError("AUTHORIZATION_DENIED: account"), "AUTH_FAILED")):
            transport = FakeTransport()
            transport.failures["list_events"] = failure
            result = await collect_once(self.store, transport)
            self.assertEqual(result["state"], expected)

    async def test_worker_missing_own_cache_never_imports_framework_or_logs_in(self):
        state = Path(self.temp.name) / "missing-oauth"
        with self.assertRaises(AuthRequired):
            await create_client("https://example.test/mailbox/mcp", state)
        self.assertFalse(state.exists())

    async def test_http_auth_errors_are_terminal_and_keep_cursor(self):
        for status in (401, 403):
            failure = RuntimeError("rejected")
            failure.response = SimpleNamespace(status_code=status)
            transport = FakeTransport()
            transport.failures["list_events"] = failure
            result = await collect_once(self.store, transport)
            self.assertEqual((result["state"], result["cursor"], len(transport.calls)), ("AUTH_FAILED", 0, 1))

    async def test_known_tool_permission_denial_is_terminal_without_fuzzy_match(self):
        denied = "AUTHORIZATION_DENIED: this account cannot access the mailbox"
        replies = [SimpleNamespace(is_error=True, content=[SimpleNamespace(type="text", text=denied)]),
                   {"isError": True, "content": [{"type": "text", "text": denied}]}]
        for reply in replies:
            async def call(name, arguments):
                return reply
            result = await collect_once(self.store, call)
            self.assertEqual((result["state"], result["calls"], result["cursor"]), ("AUTH_FAILED", 1, 0))
        async def unrelated(name, arguments):
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(type="text",
                text="Other failure mentioning AUTHORIZATION_DENIED: in its detail")])
        self.assertEqual((await collect_once(self.store, unrelated))["state"], "PROTOCOL_FAILED")

    async def test_native_structured_result_is_used_even_with_empty_data(self):
        transport = FakeTransport()
        async def call(name, args):
            return SimpleNamespace(structured_content=await transport(name, args), data=None, is_error=False)
        result = await collect_once(self.store, call)
        self.assertEqual(result["state"], "OK")

    async def test_stop_interrupts_backoff(self):
        stop = asyncio.Event()
        async def factory():
            stop.set()
            raise OSError("offline")
        await asyncio.wait_for(run_collector(self.store, factory, stop), 0.2)
        self.assertEqual(self.store.snapshot()["health"][0]["state"], "NETWORK_FAILED")

    async def test_cancellation_remains_request_and_no_execution(self):
        record = job()
        record["cancel_requested"] = True
        result = await collect_once(self.store, FakeTransport([event(kind="CANCEL_REQUESTED", data={})], {"job-1": record}))
        self.assertEqual(result["state"], "OK")
        self.assertEqual(self.store.snapshot()["cancel_requested"], ["job-1"])
        self.assertEqual(self.store.snapshot()["jobs"][0]["status"], "PERSISTED")


if __name__ == "__main__":
    unittest.main()
