"""Persistent MCP event delivery behavior; no network or production identity."""

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('event_test_mailbox', ROOT / 'mailbox_store.py')
mailbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mailbox)
spec = importlib.util.spec_from_file_location('event_test_store', ROOT / 'event_store.py')
events = importlib.util.module_from_spec(spec)
spec.loader.exec_module(events)


class FakeCipher:
    """Test-only substitution; production supplies authenticated encryption."""
    def encrypt(self, value):
        return b'fixture-encrypted:' + base64.b64encode(value)

    def decrypt(self, value):
        return base64.b64decode(value.removeprefix(b'fixture-encrypted:'))


class EventStoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'mailbox.sqlite3'
        self.mailbox = mailbox.Store(self.path)
        self.now = 10000.0
        self.events = events.EventStore(self.mailbox, FakeCipher(), clock=lambda: self.now)
        self.owner = '{"login":"owner","subject":"fixture-subject"}'
        self.args = {'project_id': 'example', 'job_id': 'job-a'}
        self.url = 'https://callback.example/secret-callback-token'
        self.secret = 'whsec_' + base64.b64encode(b'a' * 32).decode()
        self.payload = {'schema_version': 1, 'origin_task_id': 'original', 'sender': 'untrusted-alias',
                        'recipient': 'mac', 'report_route': 'original',
                        'target': {'project_id': 'example'}, 'content': 'private job content'}
        self.digest = hashlib.sha256(mailbox._json(self.payload).encode()).hexdigest()
        self.mailbox.submit_job('job-a', self.payload, self.digest)

    def subscribe(self, **kw):
        return self.events.save(self.owner, 'mailbox.result_ready', self.args,
                                self.url, self.secret, **kw)

    def publish(self, suffix='1', job='job-a'):
        return self.mailbox.publish_result(job, 'result-' + suffix, 'REPORTED_COMPLETED',
                                           {'private_result': 'never in notification'})

    def ready(self):
        sub = self.subscribe()
        self.publish()
        self.events.prepare(sub['id'])
        return sub, self.events.pending()[0]

    def test_subscription_and_pending_survive_restart_without_plaintext_secrets(self):
        sub, delivery = self.ready()
        reopened = events.EventStore(mailbox.Store(self.path), FakeCipher(), clock=lambda: self.now)
        self.assertEqual(reopened.get(self.owner, 'mailbox.result_ready', self.args, self.url)['id'], sub['id'])
        self.assertEqual(reopened.pending()[0]['body'], delivery['body'])
        data = self.path.read_bytes()
        self.assertNotIn(self.secret.encode(), data)
        self.assertNotIn(self.url.encode(), data)

    def test_notification_is_metadata_only_and_preserves_result_identity(self):
        _, delivery = self.ready()
        body = json.loads(delivery['body'])
        result = self.mailbox.get_result('job-a', 1)
        self.assertEqual(body['eventId'], 'mbe_' + hashlib.sha256(b'result-1').hexdigest())
        self.assertEqual(delivery['source_event_id'], 'result-1')
        self.assertEqual(body['name'], 'mailbox.result_ready')
        self.assertEqual(body['data'], {'project_id': 'example', 'job_id': 'job-a',
                          'payload_hash': self.digest, 'version': 1,
                          'result_hash': result['result_hash'], 'status': 'REPORTED_COMPLETED'})
        self.assertNotIn(b'private', delivery['body'])
        self.assertNotIn('type', body)

    def test_same_subscription_and_result_do_not_create_duplicate_deliveries(self):
        sub, first = self.ready()
        self.assertEqual(self.subscribe()['id'], sub['id'])
        self.events.prepare(sub['id'])
        self.assertEqual([r['delivery_id'] for r in self.events.pending()], [first['delivery_id']])

    def test_other_job_and_non_result_events_never_notify(self):
        sub = self.subscribe()
        self.mailbox.submit_job('job-b', self.payload, self.digest)
        self.publish('other', job='job-b')
        self.mailbox.request_cancel('job-a', 'cancel-a')
        self.events.prepare(sub['id'])
        self.assertEqual(self.events.pending(), [])

    def test_prepare_catches_result_that_precedes_subscription(self):
        self.publish()
        sub = self.subscribe()
        self.events.prepare(sub['id'])
        self.assertEqual(self.events.pending()[0]['source_event_id'], 'result-1')

    def test_attempt_is_durable_before_send_and_crash_retries_same_event(self):
        _, first = self.ready()
        self.events.mark_attempt(first['delivery_id'])
        reopened = events.EventStore(mailbox.Store(self.path), FakeCipher(), clock=lambda: self.now)
        self.assertEqual(reopened.pending(), [])
        self.now += 31
        recovered = reopened.pending()[0]
        self.assertEqual(recovered['event_id'], first['event_id'])
        self.assertEqual(recovered['body'], first['body'])
        self.assertEqual(recovered['attempts'], 1)

    def test_success_is_not_retried_and_cursor_does_not_skip_pending(self):
        sub, delivery = self.ready()
        self.assertLess(int(self.events.resume_cursor(sub['id'])), int(json.loads(delivery['body'])['cursor']))
        self.events.mark_attempt(delivery['delivery_id'])
        self.events.finish(delivery['delivery_id'], 204, 'HTTP_2XX')
        self.assertEqual(self.events.pending(), [])
        self.assertEqual(self.events.resume_cursor(sub['id']), json.loads(delivery['body'])['cursor'])

    def test_transient_failure_backs_off_and_eight_attempts_exhaust(self):
        _, delivery = self.ready()
        for attempt in range(1, 9):
            self.events.mark_attempt(delivery['delivery_id'])
            self.events.finish(delivery['delivery_id'], 503, 'HTTP_5XX')
            self.assertEqual(self.events.pending(), [])
            self.now += 1000
            if attempt < 8:
                self.assertEqual(self.events.pending()[0]['attempts'], attempt)
        self.assertEqual(self.events.pending(), [])

    def test_terminal_responses_stop_without_retry(self):
        for status in (400, 401, 403, 404, 410, 413):
            with self.subTest(status=status):
                sub = self.subscribe()
                self.publish(str(status))
                self.events.prepare(sub['id'])
                for delivery in self.events.pending():
                    self.events.mark_attempt(delivery['delivery_id'])
                    self.events.finish(delivery['delivery_id'], status, 'HTTP_4XX')
                self.now += 1000
                self.assertEqual(self.events.pending(), [])

    def test_expiration_and_unsubscribe_preserve_history_without_delivery(self):
        sub, delivery = self.ready()
        self.now += 86401
        self.assertEqual(self.events.active(), [])
        self.assertEqual(self.events.pending(), [])
        self.subscribe()
        self.events.close(self.owner, 'mailbox.result_ready', self.args, self.url)
        self.events.close(self.owner, 'mailbox.result_ready', self.args, self.url)
        self.assertEqual(self.events.pending(), [])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM event_deliveries').fetchone()[0], 1)

    def test_refresh_rotates_secret_and_preserves_id_and_cursor(self):
        sub, _ = self.ready()
        self.now += 100
        newer = 'whsec_' + base64.b64encode(b'b' * 32).decode()
        changed = self.events.save(self.owner, 'mailbox.result_ready', self.args, self.url, newer)
        self.assertEqual(changed['id'], sub['id'])
        self.assertEqual(changed['old_secret'], self.secret)
        self.assertEqual(changed['secret'], newer)
        self.assertEqual(changed['rotation_until'], self.now + 300)
        self.assertGreater(changed['after_cursor'], sub['after_cursor'])

    def test_capacity_refusal_preserves_original_subscription_and_queue(self):
        limited = events.EventStore(self.mailbox, FakeCipher(), clock=lambda: self.now,
                                   max_subscriptions=1, max_deliveries=1)
        sub = limited.save(self.owner, 'mailbox.result_ready', self.args, self.url, self.secret)
        with self.assertRaises(ValueError):
            limited.save(self.owner, 'mailbox.result_ready', self.args, 'https://callback.example/other', self.secret)
        self.publish()
        limited.prepare(sub['id'])
        self.publish('2')
        with self.assertRaises(ValueError):
            limited.prepare(sub['id'])
        self.assertEqual(len(limited.pending()), 1)
        self.assertEqual(limited.get(self.owner, 'mailbox.result_ready', self.args, self.url)['after_cursor'], 2)

    def test_cursor_validation_and_canonical_identity(self):
        sub = self.subscribe()
        reverse = {'job_id': 'job-a', 'project_id': 'example'}
        self.assertEqual(self.events.identity(self.owner, 'mailbox.result_ready', reverse, self.url), sub['id'])
        with self.assertRaises(ValueError):
            self.subscribe(cursor='100')
        for value in (True, 0, -1, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.subscribe(ttl_ms=value)

    def test_delivery_capacity_refusal_does_not_prevent_sending_existing_work(self):
        limited = events.EventStore(self.mailbox, FakeCipher(), clock=lambda: self.now,
                                   max_subscriptions=1, max_deliveries=1)
        sub = limited.save(self.owner, 'mailbox.result_ready', self.args, self.url, self.secret)
        first = self.publish()
        limited.prepare(sub['id'])
        self.publish('2')
        try:
            limited.prepare(sub['id'])
        except ValueError as error:
            self.assertEqual(type(error).__name__, 'EventCapacityError')
        else:
            self.fail('full delivery queue was not refused')
        delivery = limited.pending()[0]
        self.assertEqual(delivery['source_event_id'], first['event_id'])
        claimed = limited.mark_attempt(delivery['delivery_id'])
        self.assertIsNotNone(claimed)
        limited.finish(delivery['delivery_id'], 204, 'HTTP_2XX')
        self.assertEqual(limited.pending(), [])
        self.assertEqual(self.mailbox.get_result('job-a', 2)['event_id'], 'result-2')
        self.assertEqual(limited.get(self.owner, 'mailbox.result_ready', self.args, self.url)['after_cursor'], 2)

    def test_two_workers_claim_only_one_attempt(self):
        _, delivery = self.ready()
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(lambda _: self.events.mark_attempt(delivery['delivery_id']), range(2)))
        self.assertEqual(sum(row is not None for row in claimed), 1)
        row = next(row for row in claimed if row is not None)
        self.assertEqual(row['attempts'], 1)
        for key in ('owner', 'arguments', 'url', 'secret', 'body', 'event_id', 'subscription_id'):
            self.assertEqual(row[key], delivery[key])

    def test_stored_notification_drift_stops_before_send(self):
        _, delivery = self.ready()
        body = json.loads(delivery['body'])
        body['data']['job_id'] = 'wrong-job'
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE event_deliveries SET body=? WHERE delivery_id=?',
                       (json.dumps(body).encode(), delivery['delivery_id']))
        with self.assertRaisesRegex(ValueError, 'identity conflicts'):
            self.events.pending()

    def test_unsubscribe_cannot_close_another_subject(self):
        sub, _ = self.ready()
        self.events.close('different-owner', 'mailbox.result_ready', self.args, self.url)
        self.assertEqual(len(self.events.pending()), 1)
        self.events.close(self.owner, 'mailbox.result_ready', self.args, self.url)
        self.assertEqual(self.events.pending(), [])


if __name__ == '__main__':
    unittest.main()
