"""Local coordinator ledger behavior; no HTTP, hooks installation, or execution."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / "mac" / "bridge_store.py"
sys.path.insert(0, str(PATH.parent))
SPEC = importlib.util.spec_from_file_location("local_bridge_store", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
BridgeStore = MODULE.BridgeStore


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class BridgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "bridge.sqlite3"
        self.store = BridgeStore(self.path, "source-1")

    def event(self, cursor=1, event_id="event-1", kind="JOB_PERSISTED", data=None):
        return {"cursor": cursor, "event_id": event_id, "job_id": "job-1",
                "event_type": kind, "created_at": "2026-11-02T00:00:00+00:00",
                "data": {} if data is None else data}

    def page(self, *events, more=False):
        return {"events": list(events), "next_cursor": events[-1]["cursor"] if events else self.store.cursor(),
                "has_more": more}

    def job(self, **updates):
        payload = {"schema_version": 1, "origin_task_id": "origin-1", "sender": "dot",
                   "recipient": "mac", "report_route": "mailbox:dot",
                   "target": {"pane": "explicit"}, "content": "PRIVATE_TASK_BODY"}
        return {"job_id": "job-1", "payload": payload, "payload_hash": digest(payload),
                "created_at": "2026-11-02T00:00:00+00:00", "status": "PERSISTED",
                "cancel_requested": False, "latest_result_version": 0, **updates}

    def tracked_job(self):
        record = self.job()
        record["payload"]["tracking"] = {"repository": "example/source",
            "project_url": "https://github.com/orgs/example/projects/1", "item_id": 1,
            "issue_url": "https://github.com/example/source/issues/1",
            "iteration": {"state": "UNSCHEDULED", "reason": "Local lifecycle test"}}
        record["payload_hash"] = digest(record["payload"])
        return record

    def test_page_and_cursor_are_durable_and_exact_replays_are_idempotent(self):
        first = self.event()
        second = self.event(5, "event-5", "RESULT_REPORTED", {"version": 1})
        receipt = self.store.ingest_page(self.page(first, second, more=True))
        self.assertEqual(receipt, {"received": 2, "duplicates": 0, "cursor": 5, "has_more": True})
        reopened = BridgeStore(self.path, "source-1")
        self.assertEqual(reopened.cursor(), 5)
        self.assertEqual(len(reopened.pending_events()), 2)
        repeated = reopened.ingest_page(self.page(first, second, more=True))
        self.assertEqual((repeated["received"], repeated["duplicates"], repeated["cursor"]), (0, 2, 5))
        self.assertEqual(reopened.ingest_page({"events": [], "next_cursor": 5, "has_more": False})["cursor"], 5)

    def test_conflicting_event_rolls_back_entire_page_and_cursor(self):
        self.store.ingest_page(self.page(self.event()))
        changed = self.event(1, data={"changed": True})
        with self.assertRaises(ValueError):
            self.store.ingest_page(self.page(changed, self.event(2, "event-2")))
        self.assertEqual(self.store.cursor(), 1)
        self.assertEqual(len(self.store.pending_events()), 1)
        with self.assertRaises(ValueError):
            self.store.ingest_page(self.page(self.event(1, "different-id")))
        self.assertEqual(self.store.cursor(), 1)

    def test_invalid_page_never_partially_advances(self):
        for page in ({"events": [self.event()], "next_cursor": 8, "has_more": False},
                     self.page(self.event(2), self.event(1, "other")),
                     {"events": [], "next_cursor": 99, "has_more": False},
                     {"events": [], "next_cursor": 0, "has_more": True}):
            with self.subTest(page=page), self.assertRaises(ValueError):
                self.store.ingest_page(page)
        self.assertEqual(self.store.cursor(), 0)
        self.assertEqual(self.store.pending_events(), [])

    def test_pending_limit_refuses_whole_page_but_allows_replay_and_handled_progress(self):
        limited = BridgeStore(self.path, "source-1", max_pending=1)
        with self.assertRaises(ValueError):
            limited.ingest_page(self.page(self.event(), self.event(2, "event-2")))
        self.assertEqual(limited.cursor(), 0)
        limited.ingest_page(self.page(self.event()))
        self.assertEqual(limited.ingest_page(self.page(self.event()))["duplicates"], 1)
        limited.finish_event("event-1", "HELD", "UNKNOWN_EVENT")
        self.assertEqual(limited.pending_events(), [])
        with self.assertRaises(ValueError):
            limited.ingest_page(self.page(self.event(2, "event-2")))
        limited.finish_event("event-1", "HANDLED")
        limited.ingest_page(self.page(self.event(2, "event-2")))
        self.assertEqual(limited.cursor(), 2)

    def test_ledger_cannot_silently_switch_source_or_consumer(self):
        with self.assertRaises(ValueError):
            BridgeStore(self.path, "replacement-source")
        with self.assertRaises(ValueError):
            BridgeStore(self.path, "source-1", consumer_id="another-consumer")
        self.assertEqual(self.store.cursor(), 0)

    def test_job_hash_validation_prevents_binding_unverified_content(self):
        bad = self.job(payload_hash="0" * 64)
        with self.assertRaises(ValueError):
            self.store.record_job(bad)
        with self.assertRaises(KeyError):
            self.store.bind_session("session-1", "job-1", "/approved/project")
        self.assertIsNone(self.store.session_binding("session-1"))
        summary = self.store.record_job(self.job())
        self.assertNotIn("payload", summary)
        self.assertEqual(summary["payload_hash"], self.job()["payload_hash"])
        different = self.job()
        different["payload"]["content"] = "different"
        different["payload_hash"] = digest(different["payload"])
        with self.assertRaises(ValueError):
            self.store.record_job(different)

    def test_job_updates_are_monotonic_and_cancel_does_not_claim_stop(self):
        self.store.record_job(self.job())
        updated = self.store.record_job(self.job(cancel_requested=True, status="UNKNOWN", latest_result_version=1))
        self.assertTrue(updated["cancel_requested"])
        self.assertEqual(updated["status"], "UNKNOWN")
        with self.assertRaises(ValueError):
            self.store.record_job(self.job())
        snapshot = self.store.snapshot()
        self.assertIn("job-1", snapshot["cancel_requested"])
        self.assertIn("job-1", snapshot["unknown"])

    def test_project_routing_is_visible_as_caller_data_without_storing_content(self):
        job = self.job()
        job["payload"]["target"] = {"project": "task-mailbox", "directory": "/approved/project"}
        job["payload_hash"] = digest(job["payload"])
        summary = self.store.record_job(job)
        self.assertEqual(summary["origin_task_id"], "origin-1")
        self.assertEqual(summary["recipient"], "mac")
        self.assertEqual(summary["report_route"], "mailbox:dot")
        self.assertEqual(summary["target"]["project"], "task-mailbox")
        self.assertTrue(summary["routing_is_caller_supplied"])
        self.assertEqual(self.store.snapshot()["jobs"][0]["target"], job["payload"]["target"])
        with sqlite3.connect(self.path) as connection:
            self.assertNotIn("PRIVATE_TASK_BODY", " ".join(connection.iterdump()))

    def test_lifecycle_without_turn_refreshes_observation_but_old_stop_does_not(self):
        start = {"session_id": "session-1", "cwd": "/approved/project", "hook_event_name": "SessionStart"}
        with patch.object(MODULE, "_now", return_value="2026-11-02T00:00:00+00:00"):
            first = self.store.record_hook(start)
        with patch.object(MODULE, "_now", return_value="2026-11-02T00:01:00+00:00"):
            resumed = self.store.record_hook(start)
        self.assertTrue(resumed["duplicate"])
        self.assertNotEqual(first["session"]["observed_at"], resumed["session"]["observed_at"])
        stop = {**start, "turn_id": "turn-1", "hook_event_name": "Stop"}
        self.store.record_hook(stop)
        self.store.record_hook({**start, "turn_id": "turn-2", "hook_event_name": "UserPromptSubmit"})
        self.assertEqual(self.store.record_hook(stop)["session"]["state"], "ACTIVE")
        end = {**start, "hook_event_name": "SessionEnd"}
        self.store.record_hook(end)
        with patch.object(MODULE, "_now", return_value="2026-11-02T00:02:00+00:00"):
            repeated_end = self.store.record_hook(end)
        self.assertTrue(repeated_end["duplicate"])
        self.assertEqual(repeated_end["session"]["observed_at"], "2026-11-02T00:02:00+00:00")
        self.assertEqual(self.store.record_hook(start)["session"]["state"], "IDLE")

    def test_bound_hook_tracks_session_without_copying_prompt_or_claiming_completion(self):
        self.store.record_job(self.tracked_job())
        binding = self.store.bind_session("session-1", "job-1", "/approved/project")
        self.assertEqual(binding["job"]["job_id"], "job-1")
        for name, state in (("UserPromptSubmit", "ACTIVE"), ("Stop", "IDLE"),
                            ("Interrupt", "INTERRUPTED"), ("SessionEnd", "CLOSED")):
            result = self.store.record_hook({"session_id": "session-1", "turn_id": "turn-1",
                "cwd": "/approved/project", "hook_event_name": name,
                "prompt": "PRIVATE_PROMPT", "last_assistant_message": "PRIVATE_REPLY",
                "transcript_path": "PRIVATE_TRANSCRIPT", "tool_input": object()})
            self.assertEqual(result["session"]["state"], state)
            self.assertEqual(result["binding"]["job_id"], "job-1")
        encoded = json.dumps(self.store.snapshot())
        for private in ("PRIVATE_TASK_BODY", "PRIVATE_PROMPT", "PRIVATE_REPLY", "PRIVATE_TRANSCRIPT"):
            self.assertNotIn(private, encoded)
        self.assertEqual(self.store.pending_reports(), [])
        with sqlite3.connect(self.path) as connection:
            serialized = " ".join(str(row) for row in connection.iterdump())
        self.assertNotIn("PRIVATE_PROMPT", serialized)
        self.assertNotIn("PRIVATE_REPLY", serialized)

    def test_unbound_hook_only_records_safe_awareness_and_deduplicates(self):
        event = {"session_id": "unbound", "turn_id": "turn-1", "cwd": "/approved/project",
                 "hook_event_name": "UserPromptSubmit"}
        first = self.store.record_hook(event)
        repeated = self.store.record_hook(event)
        self.assertTrue(first["recorded"])
        self.assertTrue(repeated["duplicate"])
        self.assertIsNone(first["binding"])
        self.assertEqual(self.store.snapshot()["sessions"][0]["state"], "ACTIVE")
        self.assertEqual(self.store.pending_reports(), [])

    def test_hook_cwd_mismatch_and_missing_turn_do_not_change_bound_state(self):
        self.store.record_job(self.tracked_job())
        self.store.bind_session("session-1", "job-1", "/approved/project")
        for event in ({"session_id": "session-1", "turn_id": "t", "cwd": "/other", "hook_event_name": "Stop"},
                      {"session_id": "session-1", "cwd": "/approved/project", "hook_event_name": "Stop"}):
            with self.assertRaises(ValueError):
                self.store.record_hook(event)
        self.assertEqual(self.store.session_binding("session-1")["cwd"], "/approved/project")

    def test_concurrent_hooks_do_not_duplicate_or_corrupt_session_records(self):
        event = {"session_id": "session-1", "turn_id": "t", "cwd": "/approved/project",
                 "hook_event_name": "Stop"}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: BridgeStore(self.path, "source-1").record_hook(event), range(8)))
        self.assertEqual(sum(not item["duplicate"] for item in results), 1)
        self.assertEqual(len(self.store.snapshot()["sessions"]), 1)

    def test_outbox_stable_id_hash_receipt_and_restart(self):
        self.store.record_job(self.job())
        first = self.store.queue_report("job-1", "report-1", "UNKNOWN", {"observation": "pending"})
        self.assertEqual(self.store.queue_report("job-1", "report-1", "UNKNOWN", {"observation": "pending"}), first)
        with self.assertRaises(ValueError):
            self.store.queue_report("job-1", "report-1", "UNKNOWN", {"observation": "changed"})
        reopened = BridgeStore(self.path, "source-1")
        self.assertEqual(reopened.pending_reports(), [first])
        receipt = {"job_id": "job-1", "event_id": "report-1", "status": "UNKNOWN",
                   "version": 1, "result_hash": first["result_hash"]}
        with self.assertRaises(ValueError):
            reopened.mark_report_sent("report-1", {**receipt, "result_hash": "0" * 64})
        self.assertEqual(len(reopened.pending_reports()), 1)
        sent = reopened.mark_report_sent("report-1", receipt)
        self.assertEqual(sent["state"], "SENT")
        self.assertEqual(reopened.mark_report_sent("report-1", receipt), sent)
        self.assertEqual(reopened.pending_reports(), [])
        self.assertEqual(reopened.queue_report("job-1", "report-1", "UNKNOWN", {"observation": "pending"})["state"], "SENT")

    def test_database_full_keeps_old_records_and_cursor(self):
        limited = BridgeStore(self.path, "source-1", max_database_bytes=131072)
        limited.record_job(self.job())
        limited.ingest_page(self.page(self.event()))
        with self.assertRaises(sqlite3.DatabaseError):
            limited.queue_report("job-1", "large", "UNKNOWN", {"text": "x" * 200000})
        self.assertEqual(limited.cursor(), 1)
        self.assertEqual(limited.pending_reports(), [])
        self.assertEqual(len(limited.pending_events()), 1)

    def test_database_full_during_ingest_rolls_back_whole_page_and_cursor(self):
        limited = BridgeStore(self.path, "source-1", max_database_bytes=131072)
        limited.ingest_page(self.page(self.event()))
        large = {"hint": "x" * 40000}
        with self.assertRaises(sqlite3.DatabaseError):
            limited.ingest_page(self.page(self.event(2, "event-2", data=large),
                                          self.event(3, "event-3", data=large)))
        self.assertEqual(limited.cursor(), 1)
        self.assertEqual([event["event_id"] for event in limited.pending_events()], ["event-1"])

    def test_tool_hooks_are_observed_active_and_binding_alone_is_unknown(self):
        self.store.record_job(self.tracked_job())
        self.store.bind_session("session-1", "job-1", "/approved/project")
        initial = self.store.snapshot()["sessions"][0]
        self.assertEqual(initial["state"], "UNKNOWN")
        self.assertIsNone(initial["observed_at"])
        for name in ("PreToolUse", "PostToolUse"):
            observed = self.store.record_hook({"session_id": "session-1", "turn_id": "turn-1",
                "hook_event_name": name, "cwd": "/approved/project", "tool_name": "Bash",
                "tool_use_id": "call-1", "tool_input": "PRIVATE_INPUT", "tool_response": "PRIVATE_OUTPUT"})
            self.assertEqual(observed["session"]["state"], "ACTIVE")
            self.assertIsNotNone(observed["session"]["observed_at"])
        self.assertNotIn("PRIVATE_", json.dumps(self.store.snapshot()))

    def test_latest_handled_result_can_resolve_earlier_unknown(self):
        self.store.record_job(self.job())
        first = self.event(1, "result-1", "RESULT_REPORTED", {"version": 1, "status": "UNKNOWN"})
        self.store.ingest_page(self.page(first))
        self.store.finish_event("result-1")
        self.assertIn("job-1", self.store.snapshot()["unknown"])
        later = self.event(2, "result-2", "RESULT_REPORTED", {"version": 2, "status": "REPORTED_COMPLETED"})
        self.store.ingest_page(self.page(later))
        self.store.finish_event("result-2")
        self.assertNotIn("job-1", self.store.snapshot()["unknown"])

    def test_confirmed_unknown_report_remains_visible_until_a_newer_known_result(self):
        self.store.record_job(self.job())
        report = self.store.queue_report("job-1", "report-1", "UNKNOWN", {})
        self.store.mark_report_sent("report-1", {"job_id": "job-1", "event_id": "report-1",
            "status": "UNKNOWN", "result_hash": report["result_hash"], "version": 1})
        self.assertIn("job-1", self.store.snapshot()["unknown"])
        self.store.record_job(self.job(status="REPORTED_COMPLETED", latest_result_version=2))
        self.assertNotIn("job-1", self.store.snapshot()["unknown"])

    def test_health_and_snapshot_are_bounded_safe_metadata(self):
        self.store.set_health("transport", "AUTH_REQUIRED", "TOKEN_EXCHANGE_REQUEST_FAILED")
        with self.assertRaises(ValueError):
            self.store.set_health("transport", "ERROR", "https://private.invalid?token=secret")
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["health"][0]["state"], "AUTH_REQUIRED")
        self.assertNotIn(str(self.path), json.dumps(snapshot))
        self.assertIn("counts", snapshot)


if __name__ == "__main__":
    unittest.main()
