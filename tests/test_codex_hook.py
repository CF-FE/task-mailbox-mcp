import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mac'))
from bridge_store import BridgeStore
from codex_hook import draft_path, handle_hook
from install import EVENTS, HOOK_LABEL, merged_hooks
from bridge import write_private_json
from mailbox_store import Store


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {'state_dir': str(self.root / 'state'), 'allowed_roots': [str(self.root / 'project')]}
        self.store = BridgeStore(self.root / 'bridge.sqlite3', 'test-source')
        # Use the actual mailbox contract for canonical job/hash construction.
        remote = Store(self.root / 'remote.sqlite3')
        payload = {'schema_version': 1, 'origin_task_id': 'origin', 'sender': 'dot', 'recipient': 'mac',
                   'report_route': 'original', 'target': {'project': 'test'}, 'content': 'UNTRUSTED_BODY',
                   'tracking': {'repository': 'example/source',
                       'project_url': 'https://github.com/orgs/example/projects/1', 'item_id': 1,
                       'issue_url': 'https://github.com/example/source/issues/1',
                       'iteration': {'state': 'UNSCHEDULED', 'reason': 'Local lifecycle test'}}}
        import hashlib
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        self.job = remote.submit_job('job-hook', payload, digest)
        self.store.record_job(self.job)

    def event(self, name='UserPromptSubmit', **extra):
        return {'hook_event_name': name, 'session_id': 'session-1', 'turn_id': 'turn-1',
                'cwd': str(self.root / 'project'), 'prompt': 'SECRET_PROMPT',
                'last_assistant_message': 'SECRET_REPLY', 'transcript_path': '/never/read', **extra}

    def test_foreign_project_has_no_observation_or_context(self):
        before = self.store.snapshot()
        self.assertEqual({}, handle_hook(self.config, self.event(cwd='/other/project'), self.store))
        self.assertEqual(before, self.store.snapshot())

    def test_bound_context_has_correlation_without_transcript_or_body(self):
        self.store.bind_session('session-1', 'job-hook', str(self.root / 'project'))
        output = handle_hook(self.config, self.event(), self.store)
        text = json.dumps(output)
        self.assertIn('job-hook', text)
        for secret in ('SECRET_PROMPT', 'SECRET_REPLY', 'UNTRUSTED_BODY', '/never/read'):
            self.assertNotIn(secret, text + json.dumps(self.store.snapshot()))

    def test_stop_without_structured_draft_creates_no_result(self):
        self.store.bind_session('session-1', 'job-hook', str(self.root / 'project'))
        self.assertEqual({}, handle_hook(self.config, self.event('Stop'), self.store))
        self.assertEqual([], self.store.pending_reports(10))

    def test_repeated_stop_queues_one_original_structured_report(self):
        self.store.bind_session('session-1', 'job-hook', str(self.root / 'project'))
        path = draft_path(self.config, 'session-1')
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'session_id': 'session-1', 'turn_id': 'turn-1', 'job_id': 'job-hook', 'event_id': 'report-1',
                                    'status': 'UNKNOWN', 'result': {'native_reference': 'original-request'}}))
        for _ in range(2):
            self.assertEqual({}, handle_hook(self.config, self.event('Stop'), self.store))
        reports = self.store.pending_reports(10)
        self.assertEqual(1, len(reports))
        self.assertEqual('UNKNOWN', reports[0]['status'])
        self.assertEqual('report-1', reports[0]['event_id'])

    def test_successful_report_consumption_allows_next_report_and_keeps_evidence(self):
        self.store.bind_session('session-1', 'job-hook', str(self.root / 'project'))
        path = draft_path(self.config, 'session-1')
        for version in (1, 2):
            write_private_json(path, {'session_id': 'session-1', 'turn_id': f'turn-{version}',
                'job_id': 'job-hook', 'event_id': f'report-{version}', 'status': 'UNKNOWN', 'result': {'step': version}})
            handle_hook(self.config, self.event('Stop', turn_id=f'turn-{version}'), self.store)
            self.assertFalse(path.exists())
        self.assertEqual(2, len(self.store.pending_reports(10)))
        self.assertEqual(2, len(list((self.root / 'state/report-archive').glob('*.json'))))

    def test_failed_queue_keeps_original_draft_for_reconciliation(self):
        self.store.bind_session('session-1', 'job-hook', str(self.root / 'project'))
        path = draft_path(self.config, 'session-1')
        write_private_json(path, {'session_id': 'session-1', 'turn_id': 'turn-1', 'job_id': 'job-hook',
            'event_id': 'report-1', 'status': 'UNKNOWN', 'result': {'reference': 'original'}})
        with patch.object(self.store, 'queue_report', side_effect=ValueError('storage unavailable')):
            with self.assertRaises(ValueError):
                handle_hook(self.config, self.event('Stop'), self.store)
        self.assertTrue(path.exists())

    def test_large_compact_result_is_not_expanded_past_draft_limit(self):
        self.store.bind_session('session-1', 'job-hook', str(self.root / 'project'))
        path = draft_path(self.config, 'session-1')
        write_private_json(path, {'session_id': 'session-1', 'turn_id': 'turn-1', 'job_id': 'job-hook',
            'event_id': 'large-report', 'status': 'UNKNOWN', 'result': {'values': [0] * 400000}})
        self.assertLess(path.stat().st_size, 1048576)
        handle_hook(self.config, self.event('Stop'), self.store)
        self.assertEqual(1, len(self.store.pending_reports(10)))

    def test_old_stop_cannot_consume_new_turn_report(self):
        self.store.bind_session('session-1', 'job-hook', str(self.root / 'project'))
        path = draft_path(self.config, 'session-1')
        write_private_json(path, {'session_id': 'session-1', 'turn_id': 'turn-new', 'job_id': 'job-hook',
            'event_id': 'new-report', 'status': 'UNKNOWN', 'result': {'reference': 'new-turn'}})
        handle_hook(self.config, self.event('Stop', turn_id='turn-old'), self.store)
        self.assertTrue(path.exists())
        self.assertEqual([], self.store.pending_reports(10))

    def test_wrong_job_draft_is_rejected(self):
        self.store.bind_session('session-1', 'job-hook', str(self.root / 'project'))
        path = draft_path(self.config, 'session-1')
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'session_id': 'session-1', 'job_id': 'another-job'}))
        with self.assertRaises(ValueError):
            handle_hook(self.config, self.event('Stop'), self.store)
        self.assertEqual([], self.store.pending_reports(10))

    def test_additive_install_preserves_other_hooks_and_is_idempotent(self):
        other = {'type': 'command', 'command': 'existing-command'}
        original = {'description': 'keep me', 'hooks': {'Stop': [{'hooks': [other]}], 'PreCompact': [{'hooks': [other]}]}}
        first = merged_hooks(original, 'reviewed-command')
        second = merged_hooks(first, 'reviewed-command')
        self.assertEqual(first, second)
        self.assertEqual('keep me', second['description'])
        self.assertEqual(original['hooks']['PreCompact'], second['hooks']['PreCompact'])
        self.assertEqual(other, second['hooks']['Stop'][0]['hooks'][0])
        for event in EVENTS:
            own = [h for g in second['hooks'][event] for h in g['hooks'] if h.get('statusMessage') == HOOK_LABEL]
            self.assertEqual(1, len(own))
            self.assertNotIn('async', own[0])
            self.assertLessEqual(own[0]['timeout'], 3)


if __name__ == '__main__':
    unittest.main()
