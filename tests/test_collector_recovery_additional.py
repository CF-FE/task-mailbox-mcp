"""Worker fairness and failure isolation with temporary ledgers and fake RPCs."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mac.bridge_store import BridgeStore
from mac.collector import AuthFailed, Limits, ProtocolError, collect_once, run_collector
from test_collector import FakeTransport, digest, event, job


class ImmediatePollEvent(asyncio.Event):
    async def wait(self):
        if self.is_set():
            return True
        await asyncio.sleep(0)
        raise asyncio.TimeoutError()


class CollectorRecoveryAdditionalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="collector-additional-")
        self.addCleanup(self.temp.cleanup)
        self.store = BridgeStore(Path(self.temp.name) / "ledger.sqlite3", "test-source")

    def queue_report(self, number=1):
        job_id, event_id = f"outgoing-{number}", f"saved-report-{number}"
        self.store.record_job(job(job_id))
        report = {"job_id": job_id, "event_id": event_id, "status": "UNKNOWN", "result": {"saved_round": number}}
        self.store.queue_report(**report)
        return report

    def queue_incoming(self):
        incoming = event(job_id="incoming")
        self.store.ingest_page({"events": [incoming], "next_cursor": 1, "has_more": False})

    async def worker_with_continuous_outbox(self, rpc):
        body = {"reply": "original-result"}
        incoming = job("incoming")
        incoming.update(status="UNKNOWN", latest_result_version=1)
        result = {"job_id": "incoming", "event_id": "event-1", "version": 1,
            "status": "UNKNOWN", "result": body, "result_hash": digest(body)}
        incoming_event = event(1, "RESULT_REPORTED", {"version": 1, "status": "UNKNOWN",
            "result_hash": digest(body)}, job_id="incoming")
        self.store.ingest_page({"events": [incoming_event], "next_cursor": 1, "has_more": False})
        transport = FakeTransport(jobs={"incoming": incoming}, results={("incoming", 1): result})
        stop, rounds, queued, published = ImmediatePollEvent(), [], {}, {}
        limits = Limits(rpc=rpc, call_timeout=0.1, round_timeout=0.12)
        store, enqueue = self.store, self.queue_report

        class Client:
            def __init__(self, number):
                self.number, self.calls = number, []

            async def __aenter__(self):
                self.started = time.monotonic()
                return self

            async def __aexit__(self, *args):
                rounds.append({"number": self.number, "calls": list(self.calls),
                    "duration": time.monotonic() - self.started,
                    "pending_events": len(store.pending_events())})
                if len(rounds) == 4:
                    stop.set()
                return False

            async def call_tool(self, name, arguments):
                self.calls.append(name)
                if name in {"get_job", "get_result"}:
                    await asyncio.sleep(0.04)
                response = await transport(name, arguments)
                if name == "publish_result":
                    identifier = arguments["event_id"]
                    if arguments != queued[identifier] or identifier in published:
                        raise AssertionError("saved report identity changed or successful report replayed")
                    published[identifier] = deepcopy(arguments)
                return response

        async def factory():
            number = len(rounds) + 1
            if number > 4:
                stop.set()
                raise AssertionError("worker exceeded test round bound")
            report = enqueue(number)
            queued[report["event_id"]] = deepcopy(report)
            return Client(number)

        # Run the actual worker; it owns every round's direction and budget.
        await asyncio.wait_for(run_collector(store, factory, stop, limits=limits, interval=1), 2)
        self.assertEqual(4, len(rounds))
        self.assertTrue(all(len(row["calls"]) <= rpc for row in rounds), rounds)
        self.assertTrue(all(row["duration"] < 0.22 for row in rounds), rounds)
        self.assertGreater(len(published), 0, "saved outbox never progressed")
        self.assertTrue(all(queued[key] == value for key, value in published.items()))
        self.assertEqual(4, len(published) + len(store.pending_reports()), "saved reports were lost")
        self.assertEqual([], store.pending_events(),
            "two-RPC RESULT_REPORTED starved with a legitimate persisted report in every worker round: " + str(rounds))
        snapshot = store.snapshot()
        self.assertEqual(0, snapshot["counts"]["held_events"])
        self.assertEqual(1, snapshot["cursor"])
        self.assertTrue(any(row["job_id"] == "incoming" for row in snapshot["jobs"]))

    async def test_real_worker_does_not_starve_slow_two_rpc_result_with_continuous_outbox(self):
        await self.worker_with_continuous_outbox(rpc=10)

    async def test_two_rpc_worker_eventually_advances_both_directions(self):
        await self.worker_with_continuous_outbox(rpc=2)

    async def assert_unknown_inbound_error_does_not_publish(self, error):
        self.queue_incoming()
        self.queue_report()
        transport = FakeTransport(jobs={"incoming": job("incoming")})
        transport.failures["get_job"] = error
        result = await collect_once(self.store, transport)
        self.assertFalse(any(name == "publish_result" for name, _ in transport.calls),
            "an unknown inbound failure incorrectly released another side-effect lane")
        self.assertEqual(1, result["pending_events"])
        self.assertEqual(1, result["pending_reports"])
        self.assertEqual(0, self.store.snapshot()["counts"]["held_events"])
        self.assertNotIn("PRIVATE_FAILURE", json.dumps(self.store.snapshot()))

    async def test_unknown_runtime_error_does_not_release_outbox_in_same_round(self):
        await self.assert_unknown_inbound_error_does_not_publish(RuntimeError("PRIVATE_FAILURE"))

    async def test_unknown_type_error_does_not_release_outbox_in_same_round(self):
        await self.assert_unknown_inbound_error_does_not_publish(TypeError("PRIVATE_FAILURE"))

    async def test_unknown_database_error_does_not_release_outbox_in_same_round(self):
        await self.assert_unknown_inbound_error_does_not_publish(sqlite3.DatabaseError("PRIVATE_FAILURE"))

    async def test_known_transport_timeout_still_allows_other_direction_to_progress(self):
        self.queue_incoming()
        self.queue_report()
        transport = FakeTransport(jobs={"incoming": job("incoming")})
        transport.failures["get_job"] = TimeoutError("PRIVATE_FAILURE")
        result = await collect_once(self.store, transport)
        self.assertEqual("NETWORK_FAILED", result["state"])
        self.assertEqual(1, result["sent"])
        self.assertEqual(1, result["pending_events"])
        self.assertEqual([], self.store.pending_reports())

    async def test_known_outer_protocol_code_keeps_inner_authentication_stop_cause(self):
        self.queue_incoming()
        self.queue_report()
        calls = []

        async def call(name, arguments):
            calls.append(name)
            try:
                raise AuthFailed("CLIENT_REGISTRATION_EXPIRED")
            except AuthFailed as error:
                raise ProtocolError("RESULT_EVENT_MISSING") from error

        result = await collect_once(self.store, call)
        health = next(row for row in self.store.snapshot()["health"] if row["component"] == "collector")
        self.assertEqual("AUTH_FAILED", result["state"])
        self.assertIn("CLIENT_REGISTRATION_EXPIRED", health["detail"])
        self.assertEqual(["get_job"], calls)
        self.assertEqual(1, result["pending_events"])
        self.assertEqual(1, result["pending_reports"])
        self.assertEqual(0, self.store.snapshot()["counts"]["held_events"])


if __name__ == "__main__":
    unittest.main()
