"""Local tracking lifecycle against the actual ledger, CLI, hooks and mailbox."""

from contextlib import closing, redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mac"))
import bridge
from bridge_store import BridgeStore
from codex_hook import draft_path, handle_hook
from mailbox_store import Store


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def tracking(draft=False, unscheduled=False):
    references = {"repository": "source-owner/project-source",
        "project_url": "https://github.com/orgs/planning-owner/projects/6", "item_id": 123,
        "iteration": {"field_id": 456, "iteration_id": "iteration-current"}}
    if draft:
        references["draft_id"] = "DI_originalDraft"
    else:
        references["issue_url"] = "https://github.com/source-owner/project-source/issues/7"
    if unscheduled:
        references["iteration"] = {"state": "UNSCHEDULED", "reason": "FREE_FORM_REASON_NOT_HOOK_CONTEXT"}
    return references


class BridgeTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = (self.root / "project").resolve()
        self.project.mkdir()
        self.config = {"schema_version": 1, "endpoint": "https://mcp.example.com/mailbox/mcp",
            "source_id": "tracking-test", "consumer_id": "mac-coordinator",
            "state_dir": str(self.root / "state"), "allowed_roots": [str(self.project)]}
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps(self.config))
        self.store = BridgeStore(self.root / "state/bridge.sqlite3", "tracking-test")
        self.remote = Store(self.root / "mailbox.sqlite3")

    def job(self, references=None, job_id="job-original"):
        payload = {"schema_version": 1, "origin_task_id": "original-chat",
            "sender": "dot", "recipient": "mac", "report_route": "original-route",
            "target": {"session": "explicit-session"}, "content": "UNTRUSTED_TASK_BODY"}
        if references is not None:
            payload["tracking"] = deepcopy(references)
        return self.remote.submit_job(job_id, payload, digest(payload))

    def cli(self, *arguments):
        output = io.StringIO()
        with patch.object(sys, "argv", ["bridge", "--config", str(self.config_path), *arguments]):
            with redirect_stdout(output):
                code = bridge.main()
        return code, json.loads(output.getvalue())

    def hook(self, name="UserPromptSubmit"):
        return {"hook_event_name": name, "session_id": "session-1", "turn_id": "turn-1",
            "cwd": str(self.project), "prompt": "SECRET_PROMPT", "last_assistant_message": "SECRET_TRANSCRIPT"}

    def test_issue_and_draft_references_survive_restart_binding_and_snapshot(self):
        for is_draft in (False, True):
            refs = tracking(draft=is_draft)
            job_id = "job-draft" if is_draft else "job-issue"
            original = self.job(refs, job_id)
            # VPS v1 carries additional payload fields without a service change.
            self.assertEqual(refs, self.remote.get_job(job_id)["payload"]["tracking"])
            self.store.record_job(original)
            session_id = "session-draft" if is_draft else "session-issue"
            self.store.bind_session(session_id, job_id, str(self.project))
            reopened = BridgeStore(self.store.path, "tracking-test")
            bound = reopened.session_binding(session_id)["job"]
            self.assertEqual(refs, bound["tracking"])
            self.assertTrue(bound["tracking_is_caller_supplied"])
            self.assertEqual("UNVERIFIED", bound["tracking_verification"])
            snapshot = reopened.snapshot()
            session = next(item for item in snapshot["sessions"] if item["session_id"] == session_id)
            self.assertEqual(refs, session["tracking"])
            self.assertEqual("UNVERIFIED", session["tracking_verification"])
            self.assertNotIn("UNTRUSTED_TASK_BODY", json.dumps(snapshot))

    def test_changed_association_cannot_overwrite_original_job(self):
        original = self.job(tracking())
        self.store.record_job(original)
        changed = deepcopy(original)
        changed["payload"]["tracking"]["item_id"] += 1
        changed["payload_hash"] = digest(changed["payload"])
        with self.assertRaises(ValueError):
            self.store.record_job(changed)
        self.assertEqual(tracking(), self.store.snapshot()["jobs"][0]["tracking"])

    def test_invalid_tracking_does_not_create_a_bindable_record(self):
        malformed = tracking()
        malformed["issue_url"] = "https://github.com/other-owner/project-source/issues/7"
        with self.assertRaises(ValueError):
            self.store.record_job(self.job(malformed))
        with self.assertRaises(KeyError):
            self.store.bind_session("session-1", "job-original", str(self.project))
        self.assertEqual([], self.store.snapshot()["jobs"])

    def test_queue_preserves_tracking_and_original_event_hash_on_replay(self):
        self.store.record_job(self.job(tracking(draft=True)))
        result = {"native_reference": "native-turn-1"}
        first = self.store.queue_report("job-original", "report-stable", "UNKNOWN", result)
        second = self.store.queue_report("job-original", "report-stable", "UNKNOWN", first["result"])
        self.assertEqual(first, second)
        self.assertNotIn("task_tracking", result)
        self.assertEqual(tracking(draft=True), first["result"]["task_tracking"])
        self.assertEqual(digest(first["result"]), first["result_hash"])
        wrong = deepcopy(first["result"])
        wrong["task_tracking"]["draft_id"] = "DI_otherDraft"
        with self.assertRaises(ValueError):
            self.store.queue_report("job-original", "report-stable", "UNKNOWN", wrong)
        self.assertEqual([first], self.store.pending_reports())

    def test_bind_stage_stop_outbox_and_original_result_keep_the_same_refs(self):
        refs = tracking(draft=True)
        self.store.record_job(self.job(refs))
        code, bound = self.cli("bind", "--session-id", "session-1", "--job-id", "job-original", "--cwd", str(self.project))
        self.assertEqual(0, code)
        self.assertEqual(refs, bound["job"]["tracking"])
        result_file = self.root / "result.json"
        result_file.write_text(json.dumps({"output_reference": "saved-original-result"}))
        arguments = ("stage-report", "--session-id", "session-1", "--turn-id", "turn-1",
            "--event-id", "report-original", "--status", "UNKNOWN", "--result-file", str(result_file))
        self.assertEqual(0, self.cli(*arguments)[0])
        staged = json.loads(draft_path(self.config, "session-1").read_text())
        self.assertEqual(refs, staged["result"]["task_tracking"])
        handle_hook(self.config, self.hook("Stop"), self.store)
        queued = self.store.pending_reports()[0]
        receipt = self.remote.publish_result(queued["job_id"], queued["event_id"], queued["status"], queued["result"])
        self.store.mark_report_sent(queued["event_id"], receipt)
        returned = self.remote.get_result("job-original", receipt["version"])
        self.assertEqual(("job-original", "report-original", 1), (returned["job_id"], returned["event_id"], returned["version"]))
        self.assertEqual(refs, returned["result"]["task_tracking"])
        self.assertEqual(queued["result_hash"], returned["result_hash"])

    def test_stage_report_rejects_an_explicit_conflicting_association(self):
        self.store.record_job(self.job(tracking()))
        self.store.bind_session("session-1", "job-original", str(self.project))
        wrong = tracking()
        wrong["iteration"]["iteration_id"] = "different-iteration"
        result_file = self.root / "result.json"
        result_file.write_text(json.dumps({"task_tracking": wrong}))
        with self.assertRaises(ValueError):
            self.cli("stage-report", "--session-id", "session-1", "--turn-id", "turn-1",
                "--event-id", "wrong-report", "--status", "UNKNOWN", "--result-file", str(result_file))
        self.assertFalse(draft_path(self.config, "session-1").exists())
        self.assertEqual([], self.store.pending_reports())

    def test_hook_shows_caller_supplied_refs_without_free_form_context_or_execution(self):
        refs = tracking(draft=True, unscheduled=True)
        self.store.record_job(self.job(refs))
        self.store.bind_session("session-1", "job-original", str(self.project))
        with patch.object(self.store, "queue_report", side_effect=AssertionError("no automatic report")):
            output = handle_hook(self.config, self.hook(), self.store)
        text = output["hookSpecificOutput"]["additionalContext"]
        for expected in (refs["repository"], refs["project_url"], refs["draft_id"], "UNSCHEDULED", "未经Projects核验"):
            self.assertIn(expected, text)
        for secret in ("FREE_FORM_REASON_NOT_HOOK_CONTEXT", "UNTRUSTED_TASK_BODY", "SECRET_PROMPT", "SECRET_TRANSCRIPT"):
            self.assertNotIn(secret, text)
        self.assertEqual([], self.store.pending_reports())
        self.assertEqual(refs, self.store.snapshot()["jobs"][0]["tracking"])

    def test_old_jobs_and_data_only_results_keep_their_existing_shape(self):
        self.store.record_job(self.job())
        with self.assertRaisesRegex(ValueError, "requires explicit GitHub tracking"):
            self.store.bind_session("session-1", "job-original", str(self.project))
        raw = {"output": "legacy", "task_tracking": "legacy-data-field"}
        report = self.store.queue_report("job-original", "legacy-report", "UNKNOWN", raw)
        self.assertEqual(raw, report["result"])
        snapshot = self.store.snapshot()
        self.assertNotIn("tracking", snapshot["jobs"][0])
        self.assertEqual([], snapshot["sessions"])

    def test_existing_untracked_binding_cannot_stage_or_consume_new_business_reports(self):
        self.store.record_job(self.job())
        # Represents a binding persisted by the earlier policy, not a new bind.
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute("INSERT INTO sessions VALUES (?,?,?,'UNKNOWN','','','')",
                ("session-1", "job-original", str(self.project)))
            connection.commit()
        result_file = self.root / "result.json"
        result_file.write_text(json.dumps({"output_reference": "original-work"}))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            self.cli("stage-report", "--session-id", "session-1", "--turn-id", "turn-1",
                "--event-id", "untracked-report", "--status", "UNKNOWN", "--result-file", str(result_file))
        self.assertEqual(2, stopped.exception.code)
        self.assertFalse(draft_path(self.config, "session-1").exists())
        bridge.write_private_json(draft_path(self.config, "session-1"), {
            "session_id": "session-1", "job_id": "job-original", "turn_id": "turn-1",
            "event_id": "legacy-draft", "status": "UNKNOWN", "result": {"original": "saved"}})
        with self.assertRaisesRegex(ValueError, "no GitHub tracking"):
            handle_hook(self.config, self.hook("Stop"), self.store)
        self.assertTrue(draft_path(self.config, "session-1").exists())
        self.assertEqual([], self.store.pending_reports())


if __name__ == "__main__":
    unittest.main()
