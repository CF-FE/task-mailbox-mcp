import asyncio
from contextlib import closing, redirect_stderr
import io
import json
from pathlib import Path
import sqlite3
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mac'))
import collector
from codex_hook import handle_hook
import observability
from observability_store import MAX_BATCH_BYTES, ObservationStore, canonical, envelope_bytes


def receipt(batch):
    return {'batch_id': batch['batch_id'], 'source_id': batch['source_id'], 'persisted': True,
            'accepted_event_ids': [], 'duplicate_event_ids': [event['event_id'] for event in batch['events']],
            'watermark': '1'}


class ObservationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.settings = {
            'schema_version': 1, 'api_base': 'https://example.invalid/mailbox/api/v1',
            'source_id': 'source-test', 'project_id': 'project-test',
            'token_file': str(self.root / 'token'),
            'jobs': {'job-test': {'task_id': 'task-test'}},
            'sessions': {'session-test': {'cwd': str(self.root), 'task_id': 'task-test',
                                          'role_id': 'role-test', 'role_binding_id': 'binding-test',
                                          'role_kind': 'worker'}},
        }
        settings_path = self.root / observability.SETTINGS_NAME
        settings_path.write_text(json.dumps(self.settings))
        settings_path.chmod(0o600)
        self.store = ObservationStore(self.root / observability.BUFFER_NAME)
        self.source = self.settings['source_id']

    def test_lost_post_response_reconciles_exact_batch_after_reopen(self):
        self.store.enqueue(self.source, {'event_id': 'first'})
        calls, persisted = [], {}

        async def disconnected(method, suffix, body=None):
            calls.append(method)
            if method == 'POST':
                persisted[suffix] = receipt(body)
                raise OSError('lost response after commit')
            return None

        with self.assertRaises(OSError):
            asyncio.run(observability.flush_one(self.store, self.settings, disconnected))
        self.store.enqueue(self.source, {'event_id': 'second'})
        reopened = ObservationStore(self.root / observability.BUFFER_NAME)

        async def lookup(method, suffix, body=None):
            calls.append(method)
            self.assertTrue(suffix.endswith(persisted['']['batch_id'].replace(':', '%3A')))
            return persisted['']

        self.assertTrue(asyncio.run(observability.flush_one(reopened, self.settings, lookup)))
        self.assertEqual(calls, ['GET', 'POST', 'GET'])
        self.assertEqual(reopened.next_batch(self.source)['events'], [{'event_id': 'second'}])

    def test_receipt_mismatch_preserves_original_batch(self):
        for name in ('a', 'b'):
            self.store.enqueue(self.source, {'event_id': name})
        batch = self.store.next_batch(self.source)
        for delta in ({'persisted': False}, {'source_id': 'other'}, {'batch_id': 'other'},
                      {'duplicate_event_ids': ['a']}, {'accepted_event_ids': ['a']},
                      {'duplicate_event_ids': ['a', 'c']}):
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                self.store.acknowledge(batch, {**receipt(batch), **delta})
            self.assertEqual(batch, self.store.next_batch(self.source))
        self.assertTrue(self.store.acknowledge(batch, receipt(batch)))
        self.assertIsNone(self.store.next_batch(self.source))

    def test_page_budget_can_recover_after_real_sqlite_full(self):
        path = self.root / 'small.sqlite3'
        store = ObservationStore(path, max_bytes=65536)
        queued = []
        for i in range(50):
            event = {'event_id': f'e{i}', 'body': 'x' * 5000}
            try:
                store.enqueue(self.source, event)
            except sqlite3.OperationalError as error:
                self.assertEqual(error.sqlite_errorcode, sqlite3.SQLITE_FULL)
                break
            queued.append(event)
        else:
            self.fail('SQLite did not reject writes at the page limit')
        self.assertLessEqual(path.stat().st_size, 65536)
        batch = store.next_batch(self.source)
        self.assertEqual(batch['events'], queued)
        self.assertTrue(store.acknowledge(batch, receipt(batch)))
        self.assertTrue(store.enqueue(self.source, {'event_id': 'after-full', 'body': 'x' * 5000}))

    def test_event_count_byte_cap_and_source_isolation(self):
        for i in range(102):
            self.store.enqueue(self.source, {'event_id': f'e{i}'})
        self.store.enqueue('source-other', {'event_id': 'e0'})
        batch = self.store.next_batch(self.source)
        self.assertEqual(len(batch['events']), 100)
        self.assertTrue(self.store.acknowledge(batch, receipt(batch)))
        self.assertEqual(len(self.store.next_batch(self.source)['events']), 2)
        self.assertEqual(self.store.next_batch('source-other')['events'], [{'event_id': 'e0'}])
        for i in range(3):
            self.store.enqueue('byte-budget', {'event_id': f'e{i}', 'body': '界' * 130000})
        sized = self.store.next_batch('byte-budget')
        self.assertEqual(len(sized['events']), 2)
        self.assertLessEqual(len(canonical(sized).encode()), MAX_BATCH_BYTES)

    def test_single_event_at_exact_wire_limit_drains_before_next_event(self):
        event = {'event_id': 'boundary', 'body': ''}
        payload_bytes = MAX_BATCH_BYTES - envelope_bytes(self.source) - len(canonical(event).encode())
        event['body'] = 'x' * payload_bytes
        self.assertTrue(self.store.enqueue(self.source, event))
        with self.assertRaises(ValueError):
            self.store.enqueue(self.source, {**event, 'body': event['body'] + 'x'})
        self.store.enqueue(self.source, {'event_id': 'small'})
        batch = self.store.next_batch(self.source)
        self.assertEqual(len(canonical(batch).encode()), MAX_BATCH_BYTES)
        self.assertEqual(batch['events'], [event])
        self.assertTrue(self.store.acknowledge(batch, receipt(batch)))
        self.assertEqual(self.store.next_batch(self.source)['events'], [{'event_id': 'small'}])

    def test_invalid_monitor_configuration_preserves_original_hook_output(self):
        settings_path = self.root / observability.SETTINGS_NAME
        settings_path.write_text(json.dumps({**self.settings, 'api_base': 3}))

        class Original:
            def record_hook(self, _event):
                return {'duplicate': False, 'binding': None,
                        'session': {'observed_at': '2026-01-01T00:00:00+00:00'}}

            def snapshot(self):
                return {'counts': {}}

        with redirect_stderr(io.StringIO()):
            output = handle_hook({'state_dir': str(self.root), 'allowed_roots': [str(self.root)]},
                                 {'hook_event_name': 'SessionStart', 'cwd': str(self.root),
                                  'session_id': 'session-test'}, Original())
        self.assertEqual(output['hookSpecificOutput']['hookEventName'], 'SessionStart')
        self.assertTrue((self.root / observability.GAP_NAME).exists())

    def test_local_event_identity_conflict_is_rejected(self):
        event = {'event_id': 'original', 'payload': 'first'}
        self.assertTrue(self.store.enqueue(self.source, event))
        self.assertFalse(self.store.enqueue(self.source, event))
        with self.assertRaises(ValueError):
            self.store.enqueue(self.source, {**event, 'payload': 'replacement'})
        self.assertEqual(self.store.next_batch(self.source)['events'], [event])

    def test_hook_keeps_only_mapped_binding_time_and_action(self):
        event = {'hook_event_name': 'PostToolUse', 'session_id': 'session-test', 'turn_id': 'turn-test',
                 'tool_use_id': 'call-test', 'tool_name': 'Bash', 'cwd': str(self.root),
                 'tool_response': 'never upload'}
        state = {'duplicate': False, 'binding': {'job_id': 'job-test'},
                 'session': {'session_id': 'session-test', 'state': 'ACTIVE',
                             'observed_at': '2026-01-01T00:00:00+00:00'}}
        observability.capture_hook(self.root, event, state)
        observability.capture_hook(self.root, event, {**state, 'duplicate': True})
        rows = self.store.next_batch(self.source)['events']
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['observed_at'], state['session']['observed_at'])
        self.assertIsNone(row['occurred_at'])
        self.assertEqual(row['role_binding_id'], 'binding-test')
        self.assertEqual(row['task_id'], 'task-test')
        self.assertEqual(row['payload']['latest_action'], 'PostToolUse: Bash')
        self.assertNotIn(str(self.root), canonical(row))
        self.assertNotIn('never upload', canonical(row))

    def test_same_job_second_session_cannot_claim_role(self):
        observed = {'duplicate': False, 'binding': {'job_id': 'job-test'},
                    'session': {'observed_at': '2026-01-01T00:00:00+00:00'}}
        for session_id in ('session-test', 'another-session'):
            observability.capture_hook(self.root, {
                'hook_event_name': 'Stop', 'session_id': session_id, 'turn_id': 'turn-test',
                'cwd': str(self.root),
            }, observed)
        role, task_observation = self.store.next_batch(self.source)['events']
        self.assertEqual(role['entity_id'], 'role-test')
        self.assertEqual(task_observation['entity_type'], 'source')
        self.assertEqual(task_observation['task_id'], 'task-test')
        self.assertNotIn('role_binding_id', task_observation)

    def test_exact_session_outside_bridge_scope_never_opens_original_ledger(self):
        outside = self.root / 'outside'
        self.settings['sessions'] = {'session-test': {'cwd': str(outside), 'role_id': 'role-test',
                                                       'role_kind': 'worker', 'role_binding_id': 'original-binding'}}
        (self.root / observability.SETTINGS_NAME).write_text(json.dumps(self.settings))
        config = self.root / 'bridge-config.json'
        config.write_text(json.dumps({
            'schema_version': 1, 'endpoint': 'https://example.invalid/mcp',
            'source_id': 'mailbox-test', 'consumer_id': 'consumer-test',
            'state_dir': str(self.root), 'allowed_roots': [str(self.root / 'inside')],
        }))
        path = Path(__file__).resolve().parents[1] / 'mac' / 'codex_hook.py'
        event = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'session-test',
                 'turn_id': 'turn-test', 'cwd': str(outside), 'prompt': 'private prompt'}
        for item in ({**event, 'session_id': 'other'}, {**event, 'cwd': str(outside / 'child')}, event):
            response = subprocess.run([sys.executable, '-B', str(path), '--config', str(config)],
                                      input=json.dumps(item), capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(response.stdout), {})
            self.assertEqual(response.stderr, '')
        self.assertFalse((self.root / 'bridge.sqlite3').exists())
        rows = self.store.next_batch(self.source)['events']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['role_binding_id'], 'original-binding')
        self.assertIsNone(rows[0]['payload']['task_id'])
        self.assertNotIn('private prompt', canonical(rows))
        self.assertNotIn(str(outside), canonical(rows))
        batch = self.store.next_batch(self.source)
        subprocess.run([sys.executable, '-B', str(path), '--config', str(config)],
                       input=json.dumps(event), capture_output=True, text=True, check=True)
        self.assertEqual(self.store.next_batch(self.source), batch)
        self.assertTrue(self.store.acknowledge(batch, receipt(batch)))
        subprocess.run([sys.executable, '-B', str(path), '--config', str(config)],
                       input=json.dumps(event), capture_output=True, text=True, check=True)
        self.assertIsNone(self.store.next_batch(self.source))

    def test_exact_session_can_observe_unbound_bridge_role(self):
        self.settings['sessions'] = {'session-test': {
            'cwd': str(self.root), 'role_id': 'role-test', 'role_kind': 'owner',
            'role_binding_id': 'original-binding', 'task_id': 'task-test',
        }}
        (self.root / observability.SETTINGS_NAME).write_text(json.dumps(self.settings))
        event = {'hook_event_name': 'Stop', 'session_id': 'session-test', 'turn_id': 'turn-test',
                 'cwd': str(self.root)}
        observed = {'duplicate': False, 'binding': None,
                    'session': {'observed_at': '2026-01-01T00:00:00+00:00'}}
        observability.capture_hook(self.root, event, observed)
        row = self.store.next_batch(self.source)['events'][0]
        self.assertEqual(row['task_id'], 'task-test')
        self.assertEqual(row['payload']['observed_state'], 'IDLE')

    def test_monitor_failure_leaves_original_stop_report_queued(self):
        class Original:
            reports = []

            def record_hook(self, _event):
                return {'duplicate': False, 'binding': {'job_id': 'job-test', 'job': {'tracking': {}}},
                        'session': {'session_id': 'session-test', 'state': 'IDLE',
                                    'observed_at': '2026-01-01T00:00:00+00:00'}}

            def queue_report(self, *args):
                self.reports.append(args)

        original = Original()
        config = {'state_dir': str(self.root), 'allowed_roots': [str(self.root)]}
        from codex_hook import draft_path
        draft = draft_path(config, 'session-test')
        draft.parent.mkdir()
        draft.write_text(json.dumps({'job_id': 'job-test', 'session_id': 'session-test', 'turn_id': 'turn',
                                     'event_id': 'old-report', 'status': 'UNKNOWN', 'result': {}}))
        event = {'hook_event_name': 'Stop', 'session_id': 'session-test', 'turn_id': 'turn',
                 'cwd': str(self.root)}
        # Patch the module actually imported by the original hook.
        with patch.object(observability.ObservationStore, 'enqueue_native_hook', side_effect=sqlite3.OperationalError), redirect_stderr(io.StringIO()):
            self.assertEqual(handle_hook(config, event, original), {})
        self.assertEqual(original.reports, [('job-test', 'old-report', 'UNKNOWN', {})])
        self.assertFalse(draft.exists())
        self.assertTrue((self.root / observability.GAP_NAME).exists())

    def test_legacy_summary_and_gap_preserve_unknown_historical_facts(self):
        with closing(sqlite3.connect(self.root / 'bridge.sqlite3')) as db:
            db.executescript("""
                CREATE TABLE hooks(hook_key TEXT, body TEXT);
                INSERT INTO hooks VALUES ('old-key', 'private historic content');
                CREATE TABLE sessions(session_id TEXT, cwd TEXT, job_id TEXT, state TEXT, updated_at TEXT);
            """)
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                       ('session-test', str(self.root), 'job-test', 'IDLE', '2026-01-01T00:00:00+00:00'))
            db.commit()
        stamp = '2026-01-02T00:00:00+00:00'
        historical = observability.import_legacy_summary(self.root, self.settings, stamp)
        self.assertTrue(historical['historical'])
        self.assertIsNone(historical['occurred_at'])
        self.assertIsNone(historical['evidence'][0]['observed_at'])
        self.assertNotIn('task_id', historical)
        self.assertNotIn('current-session', canonical(historical))
        self.assertNotIn('private historic content', canonical(historical))
        observability.note_gap(self.root)
        observability.capture_recovery(self.root, self.store, self.settings)
        rows = self.store.next_batch(self.source)['events']
        self.assertEqual([row['entity_type'] for row in rows], ['source', 'role'])
        self.assertEqual(rows[1]['payload']['last_observed_at'], '2026-01-01T00:00:00+00:00')
        self.assertFalse((self.root / observability.GAP_NAME).exists())

    def test_monitor_failure_keeps_mailbox_round_running(self):
        async def verify():
            stop = asyncio.Event()
            original_ran = asyncio.Event()

            async def mailbox(*_args, **_kwargs):
                original_ran.set()
                await stop.wait()

            store = type('OriginalStore', (), {'path': self.root / 'bridge.sqlite3'})()
            monitor = AsyncMock(side_effect=OSError('synthetic source unavailable'))
            with patch.object(observability, 'monitor_once', monitor), patch.object(collector, '_run_mailbox', mailbox):
                runner = asyncio.create_task(collector.run_collector(store, None, stop))
                await asyncio.wait_for(original_ran.wait(), 1)
                await asyncio.sleep(0)
                self.assertFalse(runner.done())
                monitor.assert_awaited()
                stop.set()
                await asyncio.wait_for(runner, 1)

        with redirect_stderr(io.StringIO()):
            asyncio.run(verify())

    def test_mailbox_auth_stop_keeps_monitor_live(self):
        async def verify():
            monitor_ran = asyncio.Event()
            stop = asyncio.Event()

            async def monitor(*_):
                monitor_ran.set()
                await stop.wait()

            mailbox = AsyncMock()
            store = type('OriginalStore', (), {'path': self.root / 'bridge.sqlite3'})()
            with patch.object(observability, 'run_monitor', monitor), patch.object(collector, '_run_mailbox', mailbox):
                runner = asyncio.create_task(collector.run_collector(store, None, stop))
                await asyncio.wait_for(monitor_ran.wait(), 1)
                self.assertFalse(runner.done())
                mailbox.assert_awaited_once()
                stop.set()
                await asyncio.wait_for(runner, 1)

        asyncio.run(verify())


if __name__ == '__main__':
    unittest.main()
