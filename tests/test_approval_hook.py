"""Local coordinator subprocess fixtures; no live approval, model or service."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mac'))
from approval_hook import run_approval_hook
from bridge_store import BridgeStore
from codex_hook import draft_path, handle_hook
from config import approval_workflow_settings, load_config


class ApprovalHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.capture = self.root / 'captured-input.json'
        self.script = self.root / 'release/scripts/coordinator_approval_hook.py'
        self.script.parent.mkdir(parents=True)
        self.policy = self.root / 'policy.json'
        self.policy.write_text('{}')
        self.policy.chmod(0o600)
        self.config = {'schema_version': 1, 'endpoint': 'https://example.invalid/mcp',
                       'source_id': 'fixture-source', 'consumer_id': 'fixture-consumer',
                       'state_dir': str(self.root / 'state'), 'allowed_roots': [str(self.root / 'project')],
                       'approval_workflow': {'argv': [sys.executable, '-I', '-B', str(self.script),
                           '--state-dir', str(self.root / 'coordinator-state'), '--policy', str(self.policy)]}}
        self.store = BridgeStore(self.root / 'bridge.sqlite3', 'fixture-source')
        self.projection = {'schema_version': 1, 'system_message': '原申请待决定，请沿原单答复。',
                           'additional_context': '固定审批记录者定位；不授予执行权限。',
                           'intake': {'imported': 0, 'rejected': 0}}
        self.fixture()

    def fixture(self, *, output=None, tail=''):
        output = self.projection if output is None else output
        self.script.write_text('import json,sys\nfrom pathlib import Path\n'
                               'data=json.loads(sys.stdin.read())\n'
                               f'Path({str(self.capture)!r}).write_text(json.dumps(data))\n'
                               f'print({json.dumps(output, ensure_ascii=False)!r})\n' + tail)

    def event(self, name='UserPromptSubmit', **extra):
        return {'hook_event_name': name, 'session_id': 'bound-main', 'turn_id': 'turn-1',
                'cwd': str(self.root / 'project'), 'transcript_path': str(self.root / 'native-main.jsonl'),
                'prompt': 'PRIVATE_USER_WORDS', 'last_assistant_message': 'PRIVATE_ASSISTANT_WORDS',
                'tool_input': {'argv': ['/wrong/task-command']}, **extra}

    def test_fixed_process_receives_only_native_correlation_not_task_or_reply_text(self):
        observed = run_approval_hook(self.config, self.event())
        self.assertEqual(observed, self.projection)
        received = json.loads(self.capture.read_text())
        self.assertEqual(set(received), {'session_id', 'turn_id', 'cwd', 'transcript_path', 'hook_event_name'})
        for secret in ('PRIVATE_USER_WORDS', 'PRIVATE_ASSISTANT_WORDS', '/wrong/task-command'):
            self.assertNotIn(secret, self.capture.read_text())
        self.assertEqual(self.config['approval_workflow']['argv'][3], str(self.script))

    def test_unconfigured_foreign_or_unrelated_hook_never_starts_a_child(self):
        with patch('approval_hook.subprocess.run') as run:
            unconfigured = {key: value for key, value in self.config.items() if key != 'approval_workflow'}
            self.assertEqual(run_approval_hook(unconfigured, self.event(approval_workflow=self.config['approval_workflow'])), {})
            self.assertEqual(run_approval_hook(self.config, self.event(cwd='/other/project')), {})
            self.assertEqual(run_approval_hook(self.config, self.event('PostToolUse')), {})
            self.assertEqual(run_approval_hook(self.config, self.event(transcript_path=None)), {})
            run.assert_not_called()

    def test_context_and_system_message_are_prepared_without_delivery_or_business_job(self):
        output = handle_hook(self.config, self.event(), self.store)
        self.assertEqual(output['systemMessage'], self.projection['system_message'])
        context = output['hookSpecificOutput']['additionalContext']
        self.assertIn('Mac 协调桥', context)
        self.assertIn('台账计数=', context)
        self.assertIn(self.projection['additional_context'], context)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot['counts']['jobs'], 0)
        self.assertNotIn('DELIVERED', json.dumps(snapshot))
        self.assertNotIn('PRIVATE_USER_WORDS', json.dumps(snapshot))

    def test_bound_target_mismatch_is_decided_by_coordinator_without_client_guessing(self):
        self.fixture(output={'schema_version': 1, 'system_message': None, 'additional_context': '',
                             'intake': {'imported': 0}})
        output = handle_hook(self.config, self.event(session_id='other-thread'), self.store)
        self.assertNotIn('systemMessage', output)
        self.assertEqual(json.loads(self.capture.read_text())['session_id'], 'other-thread')

    def test_invalid_projection_and_child_failure_preserve_original_context(self):
        fixtures = [dict(self.projection, native_private_source='/not/exposed'),
                    dict(self.projection, intake={'native_source': 'not-a-count'}),
                    dict(self.projection, schema_version=True),
                    dict(self.projection, system_message='x' * 32769)]
        for projection in fixtures:
            with self.subTest(projection_keys=projection.keys()):
                self.fixture(output=projection)
                output = handle_hook(self.config, self.event(), self.store)
                self.assertNotIn('systemMessage', output)
                self.assertIn('台账计数=', output['hookSpecificOutput']['additionalContext'])
        for failure in (subprocess.TimeoutExpired('fixture', 2.2), OSError('private-path-not-logged')):
            with self.subTest(failure=type(failure).__name__), patch('approval_hook.subprocess.run', side_effect=failure):
                output = handle_hook(self.config, self.event(), self.store)
                self.assertNotIn('systemMessage', output)
                self.assertIn('台账计数=', output['hookSpecificOutput']['additionalContext'])

    def test_stop_collects_once_without_showing_warning_or_creating_mailbox_report(self):
        output = handle_hook(self.config, self.event('Stop'), self.store)
        self.assertEqual(output, {})
        captured = json.loads(self.capture.read_text())
        self.assertEqual(captured['hook_event_name'], 'Stop')
        self.assertEqual(set(captured), {'session_id', 'turn_id', 'cwd', 'transcript_path', 'hook_event_name'})
        self.assertNotIn('PRIVATE_ASSISTANT_WORDS', self.capture.read_text())
        self.assertEqual(self.store.pending_reports(10), [])

    def test_approval_timeout_does_not_prevent_an_existing_stop_report(self):
        from mailbox_store import Store
        remote = Store(self.root / 'remote.sqlite3')
        payload = {'schema_version': 1, 'origin_task_id': 'original', 'sender': 'dot', 'recipient': 'mac',
                   'report_route': 'original-route', 'target': {'project': 'fixture'}, 'content': 'fixture',
                   'tracking': {'repository': 'example/project', 'project_url': 'https://github.com/orgs/example/projects/1',
                                'item_id': 1, 'issue_url': 'https://github.com/example/project/issues/1',
                                'iteration': {'state': 'UNSCHEDULED', 'reason': 'fixture'}}}
        import hashlib
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        self.store.record_job(remote.submit_job('original-job', payload, digest))
        self.store.bind_session('bound-main', 'original-job', self.event()['cwd'])
        path = draft_path(self.config, 'bound-main')
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'session_id': 'bound-main', 'turn_id': 'turn-1', 'job_id': 'original-job',
                                    'event_id': 'original-report', 'status': 'UNKNOWN', 'result': {'ref': 'original'}}))
        with patch('approval_hook.subprocess.run', side_effect=subprocess.TimeoutExpired('fixture', 2.2)):
            self.assertEqual(handle_hook(self.config, self.event('Stop'), self.store), {})
        self.assertEqual([item['event_id'] for item in self.store.pending_reports(10)], ['original-report'])
        self.assertFalse(path.exists())

    def test_fixed_argv_timeout_and_private_config_are_validated_at_configuration_boundary(self):
        invalid = [dict(self.config['approval_workflow'], argv=['/bin/sh', '-c', 'not-allowed']),
                   dict(self.config['approval_workflow'], timeout_seconds=True),
                   dict(self.config['approval_workflow'], timeout_seconds=3),
                   dict(self.config['approval_workflow'], argv=[*self.config['approval_workflow']['argv'], 'collect'])]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                approval_workflow_settings({'approval_workflow': value})
        path = self.root / 'config.json'
        path.write_text(json.dumps(self.config))
        path.chmod(0o644)
        disabled = load_config(path)
        self.assertNotIn('approval_workflow', disabled)
        self.assertEqual(disabled['approval_workflow_error'], 'OPTIONAL_APPROVAL_DISABLED')
        self.assertEqual(disabled['state_dir'], self.config['state_dir'])
        self.assertEqual(json.loads(path.read_text()), self.config)
        path.chmod(0o600)
        self.assertEqual(load_config(path)['approval_workflow'], self.config['approval_workflow'])
        for value in invalid:
            with self.subTest(invalid_optional=value):
                candidate = dict(self.config, approval_workflow=value)
                path.write_text(json.dumps(candidate))
                disabled = load_config(path)
                self.assertNotIn('approval_workflow', disabled)
                output = handle_hook(disabled, self.event(), self.store)
                self.assertIn('台账计数=', output['hookSpecificOutput']['additionalContext'])
                self.assertIn('OPTIONAL_APPROVAL_DISABLED', output['hookSpecificOutput']['additionalContext'])
                self.assertNotIn('systemMessage', output)


if __name__ == '__main__':
    unittest.main()
