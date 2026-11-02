"""Behavior checks for the durable mailbox, without MCP or external execution."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "mailbox_store.py"
SPEC = importlib.util.spec_from_file_location("task_mailbox_store", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Store = MODULE.Store


def digest(value):
    content = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class MailboxStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "mailbox.sqlite3"
        self.store = Store(self.path)
        self.payload = {
            "schema_version": 1,
            "origin_task_id": "origin-1",
            "sender": "dot",
            "recipient": "mac-work",
            "report_route": "mailbox:dot",
            "target": {"session": "test-session", "pane": "explicit-pane"},
            "content": "只保存，不执行",
            "authorization": {"tools": []},
        }

    def submit(self, job_id="job-1", payload=None):
        payload = self.payload if payload is None else payload
        return self.store.submit_job(job_id, payload, digest(payload))

    def test_job_and_results_survive_new_store_instance(self):
        job = self.submit()
        result = self.store.publish_result(
            "job-1", "result-1", "UNKNOWN", {"observation": "no execution receipt"})
        reopened = Store(self.path)
        self.assertEqual(reopened.get_job("job-1")["payload"], self.payload)
        self.assertEqual(reopened.get_job("job-1")["created_at"], job["created_at"])
        self.assertEqual(reopened.get_result("job-1"), result)
        self.assertEqual(reopened.get_result("job-1", 1), result)
        self.assertEqual(reopened.get_job("job-1")["status"], "UNKNOWN")
        self.assertEqual(len(reopened.list_events()["events"]), 2)

    def test_duplicate_submission_returns_original_and_conflict_preserves_it(self):
        original = self.submit()
        self.assertEqual(self.submit(), original)
        changed = dict(self.payload, content="different content")
        with self.assertRaises(ValueError):
            self.submit(payload=changed)
        self.assertEqual(self.store.get_job("job-1"), original)
        self.assertEqual(len(self.store.list_events()["events"]), 1)

    def test_payload_hash_and_contract_are_checked_before_persistence(self):
        for payload in (
            dict(self.payload, schema_version=True),
            dict(self.payload, schema_version=2),
            dict(self.payload, recipient=" "),
            dict(self.payload, target={}),
            dict(self.payload, content=[]),
            dict(self.payload, extra=float("nan")),
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.store.submit_job("bad", payload, "0" * 64)
        with self.assertRaises(ValueError):
            self.store.submit_job("bad", self.payload, "0" * 64)
        for job_id in ("", "a" * 129, "含中文", "space id", "a/b"):
            with self.subTest(job_id=job_id), self.assertRaises(ValueError):
                self.store.submit_job(job_id, self.payload, digest(self.payload))
        self.assertEqual(self.store.list_events()["events"], [])

    def test_payload_and_result_size_are_bounded(self):
        small_store = Store(self.path, max_payload_bytes=600)
        too_big = dict(self.payload, content="中" * 600)
        with self.assertRaises(ValueError):
            small_store.submit_job("large", too_big, digest(too_big))
        self.submit()
        with self.assertRaises(ValueError):
            small_store.publish_result("job-1", "large-result", "UNKNOWN",
                                       {"text": "x" * 600})
        self.assertEqual(self.store.get_job("job-1")["latest_result_version"], 0)
        self.assertEqual(len(self.store.list_events()["events"]), 1)

    def test_events_have_durable_ordered_pagination_and_recipient_filter(self):
        self.submit()
        self.submit("job-2", dict(self.payload, recipient="another-mac"))
        self.store.request_cancel("job-1", "cancel-1")
        first = self.store.list_events(limit=1, recipient="mac-work")
        self.assertEqual(len(first["events"]), 1)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_cursor"], first["events"][0]["cursor"])
        second = Store(self.path).list_events(
            cursor=first["next_cursor"], limit=1, recipient="mac-work")
        self.assertEqual(second["events"][0]["event_type"], "CANCEL_REQUESTED")
        self.assertGreater(second["next_cursor"], first["next_cursor"])
        self.assertFalse(second["has_more"])
        empty = self.store.list_events(second["next_cursor"], recipient="mac-work")
        self.assertEqual(empty["events"], [])
        self.assertEqual(empty["next_cursor"], second["next_cursor"])
        self.assertEqual(len(self.store.list_events(recipient="another-mac")["events"]), 1)

    def test_retransmitted_result_event_does_not_add_result_versions(self):
        self.submit()
        first = self.store.publish_result("job-1", "result-1", "REPORTED_COMPLETED",
                                          {"output": "reported output"})
        repeated = self.store.publish_result("job-1", "result-1", "REPORTED_COMPLETED",
                                             {"output": "reported output"})
        self.assertEqual(first, repeated)
        self.assertEqual(self.store.get_job("job-1")["latest_result_version"], 1)
        self.assertEqual(first["result_hash"], digest(first["result"]))
        with self.assertRaises(ValueError):
            self.store.publish_result("job-1", "result-1", "UNKNOWN", first["result"])
        with self.assertRaises(ValueError):
            self.store.publish_result("job-1", "result-1", "REPORTED_COMPLETED",
                                      {"output": "changed"})
        second = self.store.publish_result("job-1", "result-2", "UNKNOWN",
                                           {"output": "later report"})
        self.assertEqual(second["version"], 2)
        self.assertEqual(self.store.get_result("job-1", 1), first)
        self.assertEqual(self.store.get_result("job-1"), second)
        self.assertEqual(len(self.store.list_events()["events"]), 3)

    def test_ack_requires_exact_result_version_hash_and_is_not_acceptance(self):
        self.submit()
        result = self.store.publish_result("job-1", "result-1", "REPORTED_COMPLETED", {})
        with self.assertRaises(ValueError):
            self.store.ack_result_read("job-1", "ack-1", 1, "0" * 64)
        with self.assertRaises(KeyError):
            self.store.ack_result_read("job-1", "ack-1", 2, result["result_hash"])
        before = self.store.get_job("job-1")
        ack = self.store.ack_result_read("job-1", "ack-1", 1, result["result_hash"])
        self.assertEqual(ack["event_type"], "RESULT_READ_ACKNOWLEDGED")
        self.assertEqual(ack["data"], {"version": 1, "result_hash": result["result_hash"]})
        self.assertEqual(self.store.ack_result_read(
            "job-1", "ack-1", 1, result["result_hash"]), ack)
        self.assertEqual(self.store.get_job("job-1"), before)
        self.assertEqual(len(self.store.list_events()["events"]), 3)

    def test_cancel_is_a_durable_request_and_does_not_claim_stopped(self):
        self.submit()
        before = self.store.get_job("job-1")
        event = self.store.request_cancel("job-1", "cancel-1")
        self.assertEqual(event["event_type"], "CANCEL_REQUESTED")
        self.assertEqual(self.store.request_cancel("job-1", "cancel-1"), event)
        after = Store(self.path).get_job("job-1")
        self.assertTrue(after["cancel_requested"])
        self.assertEqual(after["status"], before["status"])
        self.assertEqual(after["latest_result_version"], 0)
        self.assertEqual(len(self.store.list_events()["events"]), 2)
        with self.assertRaises(ValueError):
            self.store.publish_result("job-1", "cancel-1", "UNKNOWN", {})

    def test_event_id_is_global_and_cannot_be_rebound_to_another_job(self):
        self.submit()
        self.submit("job-2")
        original = self.store.request_cancel("job-1", "global-event")
        with self.assertRaises(ValueError):
            self.store.request_cancel("job-2", "global-event")
        self.assertEqual(self.store.request_cancel("job-1", "global-event"), original)
        self.assertFalse(self.store.get_job("job-2")["cancel_requested"])

    def test_client_events_cannot_reserve_a_future_submission_event_id(self):
        self.submit()
        result = self.store.publish_result("job-1", "result-1", "UNKNOWN", {})
        reserved = "submitted:" + hashlib.sha256(b"future-job").hexdigest()
        with self.assertRaises(ValueError):
            self.store.request_cancel("job-1", reserved)
        with self.assertRaises(ValueError):
            self.store.publish_result("job-1", reserved, "UNKNOWN", {})
        with self.assertRaises(ValueError):
            self.store.ack_result_read("job-1", reserved, 1, result["result_hash"])
        self.assertEqual(len(self.store.list_events()["events"]), 2)
        future = self.submit("future-job")
        self.assertEqual(future["job_id"], "future-job")
        self.assertEqual(future["status"], "PERSISTED")
        self.assertEqual(len(self.store.list_events()["events"]), 3)

    def test_concurrent_submission_commits_only_one_job_and_event(self):
        def submit_once(_):
            return Store(self.path).submit_job("concurrent", self.payload, digest(self.payload))

        with ThreadPoolExecutor(max_workers=6) as pool:
            receipts = list(pool.map(submit_once, range(12)))
        self.assertTrue(all(receipt == receipts[0] for receipt in receipts))
        self.assertEqual(len(self.store.list_events()["events"]), 1)

    def test_invalid_queries_and_status_do_not_mutate_state(self):
        self.submit()
        with self.assertRaises(KeyError):
            self.store.get_job("missing")
        with self.assertRaises(KeyError):
            self.store.get_result("job-1")
        for status in ("COMPLETED", "CANCELLED", "STOPPED", "RUNNING"):
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.store.publish_result("job-1", "result-1", status, {})
        for kwargs in ({"cursor": -1}, {"cursor": True}, {"cursor": 2**63}, {"limit": 0},
                       {"limit": 1001}, {"recipient": " "}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.list_events(**kwargs)
        with self.assertRaises(ValueError):
            self.store.get_result("job-1", 2**63)
        with self.assertRaises(ValueError):
            self.store.ack_result_read("job-1", "ack-1", 2**63, "0" * 64)
        self.assertEqual(len(self.store.list_events()["events"]), 1)

    def test_job_limit_preserves_existing_jobs_and_duplicate_receipts(self):
        limited = Store(self.path, max_jobs=1, max_database_bytes=65536)
        first = limited.submit_job("job-1", self.payload, digest(self.payload))
        with self.assertRaisesRegex(ValueError, "max_jobs reached"):
            limited.submit_job("job-2", self.payload, digest(self.payload))
        self.assertEqual(limited.submit_job("job-1", self.payload, digest(self.payload)), first)
        self.assertEqual(limited.get_job("job-1"), first)
        self.assertEqual(len(limited.list_events()["events"]), 1)
        self.assertEqual(limited.stats()["jobs"], 1)
        with self.assertRaises(KeyError):
            limited.get_job("job-2")

    def test_database_full_rolls_back_result_and_event_without_losing_prior_data(self):
        limited = Store(self.path, max_database_bytes=65536)
        original = limited.submit_job("job-1", self.payload, digest(self.payload))
        before = limited.stats()
        with self.assertRaises(sqlite3.DatabaseError):
            limited.publish_result("job-1", "too-large-for-database", "UNKNOWN",
                                   {"text": "x" * 60000})
        self.assertEqual(limited.get_job("job-1"), original)
        self.assertEqual(limited.submit_job("job-1", self.payload, digest(self.payload)), original)
        after = Store(self.path, max_database_bytes=65536).stats()
        self.assertEqual(after["jobs"], before["jobs"])
        self.assertEqual(after["results"], 0)
        self.assertEqual(after["events"], before["events"])
        self.assertLessEqual(after["allocated_bytes"], after["database_limit_bytes"])
        self.assertLessEqual(self.path.stat().st_size, 65536)
        result = limited.publish_result("job-1", "small-result", "UNKNOWN", {"text": "saved"})
        self.assertEqual(result["version"], 1)
        self.assertEqual(limited.get_result("job-1"), result)

    def test_default_budget_accepts_large_utf8_reports_and_preserves_them_on_overflow(self):
        payload = dict(self.payload, content="报告" * 40000)
        job = self.submit(payload=payload)
        result = self.store.publish_result("job-1", "large-report", "REPORTED_COMPLETED",
                                           {"text": "响应" * 60000})
        self.assertEqual(self.store.get_job("job-1")["payload"], job["payload"])
        self.assertEqual(self.store.get_job("job-1")["payload_hash"], job["payload_hash"])
        self.assertEqual(self.store.get_result("job-1"), result)
        with self.assertRaises(ValueError):
            self.store.publish_result("job-1", "oversized-report", "UNKNOWN",
                                      {"text": "x" * (1024 * 1024)})
        self.assertEqual(self.store.get_result("job-1"), result)
        self.assertEqual(self.store.stats()["results"], 1)

    def test_stats_report_only_usage_and_limits(self):
        initial = self.store.stats()
        self.assertEqual(initial["jobs"], 0)
        self.assertEqual(initial["results"], 0)
        self.assertEqual(initial["events"], 0)
        self.submit()
        result = self.store.publish_result("job-1", "result-1", "UNKNOWN", {})
        self.store.ack_result_read("job-1", "ack-1", 1, result["result_hash"])
        stats = self.store.stats()
        self.assertEqual(set(stats), {
            "max_payload_bytes", "database_limit_bytes", "max_jobs", "page_count",
            "page_size", "allocated_bytes", "jobs", "results", "events",
        })
        self.assertTrue(all(type(value) is int for value in stats.values()))
        self.assertEqual((stats["jobs"], stats["results"], stats["events"]), (1, 1, 3))
        self.assertEqual(stats["allocated_bytes"], stats["page_count"] * stats["page_size"])
        self.assertEqual(stats["database_limit_bytes"], 256 * 1024 * 1024)
        self.assertEqual(stats["max_jobs"], 10000)
        self.assertEqual(stats["max_payload_bytes"], 1048576)

    def test_invalid_quotas_and_smaller_than_existing_database_are_rejected(self):
        for kwargs in ({"max_jobs": 0}, {"max_jobs": True},
                       {"max_database_bytes": 65535}, {"max_database_bytes": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Store(self.path, **kwargs)
        self.submit()
        original_result = self.store.publish_result(
            "job-1", "result-1", "UNKNOWN", {"text": "x" * 60000})
        self.assertGreater(self.store.stats()["allocated_bytes"], 65536)
        with self.assertRaisesRegex(ValueError, "existing database exceeds"):
            Store(self.path, max_database_bytes=65536)
        self.assertEqual(self.store.get_result("job-1"), original_result)


if __name__ == "__main__":
    unittest.main()
