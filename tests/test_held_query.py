"""Bounded read-only diagnostics using temporary real ledgers and the actual CLI."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mac'))
import bridge_store


class HeldQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state ?#'
        self.path = self.state / 'bridge.sqlite3'
        self.store = bridge_store.BridgeStore(self.path, 'source-1', max_pending=9)
        events = [{'cursor': n, 'event_id': f'event-{n}', 'job_id': f'job-{n}',
            'event_type': 'FUTURE_EVENT', 'created_at': '2026-11-03T00:00:00Z',
            'data': {'content': 'PRIVATE_SENTINEL', 'url': 'https://private.example/secret'}}
            for n in range(1, 10)]
        events[-1]['created_at'] = 'PRIVATE_SENTINEL https://private.example/secret'
        self.store.ingest_page({'events': events, 'next_cursor': 9, 'has_more': False})
        for n in (1, 3, 5, 7, 9):
            self.store.finish_event(f'event-{n}', 'HELD', f'HELD_REASON_{n}')
        for n in (2, 4):
            self.store.finish_event(f'event-{n}')

    def query(self, **kwargs):
        return bridge_store.read_held_events(self.path, 'source-1', **kwargs)

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as db:
            tables = [x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall() for table in tables}

    def test_pages_cover_held_only_in_cursor_order_without_bodies_or_writes(self):
        before = self.snapshot()
        file_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        cursor, seen = 0, []
        for expected in ([1, 3], [5, 7], [9]):
            page = self.query(cursor=cursor, limit=2)
            self.assertEqual(expected, [x['cursor'] for x in page['events']])
            self.assertEqual(page, self.query(cursor=cursor, limit=2))
            self.assertLessEqual(len(page['events']), 2)
            for event in page['events']:
                self.assertEqual({'cursor', 'event_id', 'job_id', 'event_type', 'bridge_state', 'reason', 'created_at'}, set(event))
                self.assertEqual('HELD', event['bridge_state'])
                self.assertEqual(f"HELD_REASON_{event['cursor']}", event['reason'])
            seen += [x['event_id'] for x in page['events']]
            self.assertNotIn('PRIVATE_SENTINEL', json.dumps(page))
            self.assertNotIn('https://', json.dumps(page))
            self.assertEqual(expected[-1] < 9, page['has_more'])
            cursor = page['next_cursor']
            self.assertEqual(before, self.snapshot())
            self.assertEqual(file_hash, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual([f'event-{n}' for n in (1, 3, 5, 7, 9)], seen)
        self.assertIsNone(page['events'][0]['created_at'])
        self.assertEqual({'events': [], 'next_cursor': 9, 'has_more': False}, self.query(cursor=9))
        self.assertEqual(9, self.store.cursor())
        self.assertEqual(5, self.store.snapshot()['counts']['held_events'])
        self.assertEqual(0, self.store.snapshot()['counts']['jobs'])

    def test_rejected_boundaries_leave_every_table_unchanged(self):
        before = self.snapshot()
        for args in ({'cursor': -1}, {'cursor': True}, {'cursor': 2**63},
                     {'limit': 0}, {'limit': True}, {'limit': 101}, {'limit': 1.5}):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    self.query(**args)
                self.assertEqual(before, self.snapshot())

    def test_identity_mismatch_and_missing_ledger_do_not_initialize_or_change_data(self):
        before = self.snapshot()
        for identity in [('other-source', 'mac-coordinator'), ('source-1', 'other-consumer')]:
            with self.assertRaises(ValueError):
                bridge_store.read_held_events(self.path, *identity)
        missing = self.root / 'absent' / 'bridge.sqlite3'
        with self.assertRaises(sqlite3.OperationalError):
            bridge_store.read_held_events(missing, 'source-1')
        self.assertFalse(missing.parent.exists())
        self.assertEqual(before, self.snapshot())

    def test_query_rejects_a_symlink_ledger(self):
        alias = self.root / 'alias.sqlite3'
        alias.symlink_to(self.path)
        with self.assertRaises(ValueError):
            bridge_store.read_held_events(alias, 'source-1')

    def test_reopened_ledger_and_readonly_cli_match_without_oauth_or_state_changes(self):
        restarted = bridge_store.BridgeStore(self.path, 'source-1')
        before = self.snapshot()
        expected = bridge_store.read_held_events(restarted.path, restarted.source_id, limit=2)
        config = self.root / 'config.json'
        config.write_text(json.dumps({'schema_version': 1, 'endpoint': 'https://example.test/mailbox/mcp',
            'source_id': 'source-1', 'consumer_id': 'mac-coordinator', 'state_dir': str(self.state),
            'allowed_roots': [str(self.root)]}))
        proc = subprocess.run([sys.executable, '-B', str(ROOT / 'mac/bridge.py'), '--config', str(config),
            'held', '--cursor', '0', '--limit', '2'], capture_output=True, text=True, timeout=5)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(expected, json.loads(proc.stdout))
        self.assertFalse((self.state / 'oauth').exists())
        self.assertEqual(before, self.snapshot())
        self.assertNotIn('PRIVATE_SENTINEL', proc.stdout)

    def test_status_points_to_the_bounded_query(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps({'schema_version': 1, 'endpoint': 'https://example.test/mailbox/mcp',
            'source_id': 'source-1', 'consumer_id': 'mac-coordinator', 'state_dir': str(self.state),
            'allowed_roots': [str(self.root)]}))
        proc = subprocess.run([sys.executable, '-B', str(ROOT / 'mac/bridge.py'), '--config', str(config),
            'status'], capture_output=True, text=True, timeout=5)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual({'command': 'held', 'cursor': 0, 'limit': 25}, json.loads(proc.stdout)['held_query'])


if __name__ == '__main__':
    unittest.main()
