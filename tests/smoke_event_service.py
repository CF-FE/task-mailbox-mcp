"""Isolated event lifecycle with real encrypted SQLite and a fake HTTPS receiver."""

import asyncio
import base64
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet
from mcp.shared.exceptions import MCPError
from mailbox_store import Store, _hash, _json
from event_store import EventStore
from event_service import EventService, EVENT_NAME, load_policy, signed_headers


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.clock_value = 2000000000.0
        self.clock = lambda: self.clock_value
        self.mailbox = Store(self.path / 'jobs.sqlite3')
        self.cipher = Fernet(Fernet.generate_key())
        self.store = EventStore(self.mailbox, self.cipher, clock=self.clock)
        self.principal = _json({'login': 'test-owner', 'subject': 'native-account'})
        self.payload = {'schema_version': 1, 'origin_task_id': 'test-original',
                        'sender': 'data-alias', 'recipient': 'mac-coordinator', 'report_route': 'original-job',
                        'target': {'service': 'project-coordinator', 'project_id': 'example'},
                        'tracking': {'repository': 'CF-FE/example'}, 'content': 'synthetic query'}
        self.payload_hash = _hash(_json(self.payload))
        self.policy = {'version': 1, 'allowed_callback_hosts': ['receiver.example.com'], 'jobs': [{
            'subject': 'native-account', 'project_id': 'example', 'repository': 'CF-FE/example',
            'job_id': 'job-1', 'payload_hash': self.payload_hash}]}
        self.calls = []
        self.reply_status = 200
        self.bad_challenge = False
        owner = self

        class Receiver:
            def __init__(self, allowed_hosts):
                self.hosts = allowed_hosts

            def post(self, url, body, headers):
                owner.calls.append((url, body, headers))
                data = json.loads(body)
                if data.get('type') == 'verification':
                    return 200, _json({'challenge': 'wrong' if owner.bad_challenge else data['challenge']}).encode()
                return owner.reply_status, b'{}'

        self.service = EventService(self.store, self.mailbox, lambda: self.policy,
                                    poster_factory=Receiver, clock=self.clock)
        self.secret = 'whsec_' + base64.b64encode(b'A' * 32).decode()
        self.params = {'name': EVENT_NAME, 'arguments': {'project_id': 'example', 'job_id': 'job-1'},
                       'delivery': {'mode': 'webhook', 'url': 'https://receiver.example.com/callback/test',
                                    'secret': self.secret}}

    def tearDown(self):
        self.tmp.cleanup()

    async def subscribe(self, params=None):
        return await self.service.handle('events/subscribe', self.principal, params or self.params)

    def publish(self):
        self.mailbox.submit_job('job-1', self.payload, self.payload_hash)
        return self.mailbox.publish_result('job-1', 'result.with-dot', 'REPORTED_COMPLETED', {'private_content': 'not-in-event'})

    async def test_actual_lifecycle_catches_pre_subscription_result_and_does_not_ack(self):
        result = self.publish()
        receipt = await self.subscribe()
        await self.service.tick()
        body = json.loads(self.calls[-1][1])
        self.assertEqual(body['name'], EVENT_NAME)
        self.assertEqual(body['data']['result_hash'], result['result_hash'])
        self.assertEqual(body['data']['payload_hash'], self.payload_hash)
        self.assertNotIn('private_content', self.calls[-1][1].decode())
        self.assertEqual(self.calls[-1][2]['X-MCP-Subscription-Id'], receipt['id'])
        self.assertEqual(body['eventId'], self.calls[-1][2]['webhook-id'])
        self.assertNotIn('.', body['eventId'])
        self.assertEqual(len(self.mailbox.list_events()['events']), 2)
        await self.service.tick()
        self.assertEqual(len(self.calls), 2)

    async def test_future_exact_job_and_duplicate_subscription(self):
        a = await self.subscribe()
        b = await self.subscribe()
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(len(self.calls), 1)
        self.publish()
        await self.service.tick()
        self.assertEqual(len(self.calls), 2)

    async def test_wrong_subject_project_and_job_rejected_before_callback(self):
        for changes in ({'project_id': 'other'}, {'job_id': 'other'}):
            p = json.loads(json.dumps(self.params))
            p['arguments'].update(changes)
            with self.assertRaises(MCPError):
                await self.subscribe(p)
        with self.assertRaises(MCPError):
            await self.service.handle('events/subscribe', _json({'login': 'test-owner', 'subject': 'other'}), self.params)
        self.assertEqual(self.calls, [])

    async def test_bad_challenge_never_activates_subscription(self):
        self.bad_challenge = True
        with self.assertRaises(MCPError):
            await self.subscribe()
        self.assertEqual(self.store.active(), [])

    async def test_explicit_host_policy_and_secret_length_are_enforced(self):
        for value in ('https://unapproved.example/callback', 'http://receiver.example.com/callback',
                      'https://user@receiver.example.com/callback', 'https://receiver.example.com:444/callback'):
            p = json.loads(json.dumps(self.params)); p['delivery']['url'] = value
            with self.assertRaises(MCPError):
                await self.subscribe(p)
        p = json.loads(json.dumps(self.params)); p['delivery']['secret'] = 'whsec_bad'
        with self.assertRaises(MCPError):
            await self.subscribe(p)
        self.assertEqual(self.calls, [])

    async def test_revocation_stops_delivery_and_cached_verification(self):
        await self.subscribe()
        self.publish()
        self.policy['jobs'] = []
        await self.service.tick()
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(MCPError):
            await self.subscribe()

    async def test_retry_uses_same_bytes_id_and_new_signing_time(self):
        await self.subscribe(); self.publish(); self.reply_status = 503
        await self.service.tick()
        first = self.calls[-1]
        self.clock_value += 60
        self.reply_status = 200
        await self.service.tick()
        second = self.calls[-1]
        self.assertEqual(first[1], second[1])
        self.assertEqual(first[2]['webhook-id'], second[2]['webhook-id'])
        self.assertNotEqual(first[2]['webhook-timestamp'], second[2]['webhook-timestamp'])

    async def test_secret_rotation_signs_both_keys_during_window(self):
        await self.subscribe()
        p = json.loads(json.dumps(self.params))
        p['delivery']['secret'] = 'whsec_' + base64.b64encode(b'B' * 32).decode()
        await self.subscribe(p); self.publish()
        await self.service.tick()
        self.assertEqual(len(self.calls[-1][2]['webhook-signature'].split()), 2)

    async def test_unsubscribe_idempotent_and_no_result_delivery(self):
        await self.subscribe(); self.publish()
        p = {k: v for k, v in self.params.items()}
        p['delivery'] = {k: v for k, v in self.params['delivery'].items() if k != 'secret'}
        for _ in range(2):
            self.assertEqual(await self.service.handle('events/unsubscribe', self.principal, p), {})
        await self.service.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_full_delivery_table_still_sends_existing_pending(self):
        self.store.max_deliveries = 1
        sub = await self.subscribe(); self.publish()
        self.store.prepare(sub['id'])
        self.mailbox.publish_result('job-1', 'result-2', 'REPORTED_COMPLETED', {'answer': 'later'})
        await self.service.tick()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(json.loads(self.calls[-1][1])['data']['version'], 1)

    async def test_unsubscribe_during_verification_stays_closed(self):
        started, release = threading.Event(), threading.Event()
        factory = self.service.poster_factory
        class SlowReceiver:
            def __init__(inner, hosts):
                inner.receiver = factory(hosts)
            def post(inner, url, body, headers):
                started.set()
                if not release.wait(3):
                    raise AssertionError('test receiver was not released')
                return inner.receiver.post(url, body, headers)
        self.service.poster_factory = SlowReceiver
        subscribe = asyncio.create_task(self.subscribe())
        for _ in range(100):
            if started.is_set(): break
            await asyncio.sleep(0.01)
        self.assertTrue(started.is_set())
        p = {**self.params, 'delivery': {k: v for k, v in self.params['delivery'].items() if k != 'secret'}}
        stop = asyncio.create_task(self.service.handle('events/unsubscribe', self.principal, p))
        await asyncio.sleep(0.02)
        release.set()
        await subscribe; await stop
        self.assertEqual(self.store.active(), [])

    async def test_authority_is_rechecked_after_callback_verification(self):
        factory = self.service.poster_factory
        class RevokingReceiver:
            def __init__(inner, hosts):
                inner.receiver = factory(hosts)
            def post(inner, url, body, headers):
                result = inner.receiver.post(url, body, headers)
                self.policy['jobs'] = []
                return result
        self.service.poster_factory = RevokingReceiver
        with self.assertRaises(MCPError):
            await self.subscribe()
        self.assertEqual(self.store.active(), [])

    def test_standard_webhooks_signature_exact_known_message(self):
        body = b'{"eventId":"id_1"}'
        headers = signed_headers('id_1', body, self.secret, 'sub_1', 123)
        expected = base64.b64encode(hmac.new(b'A' * 32, b'id_1.123.' + body, hashlib.sha256).digest()).decode()
        self.assertEqual(headers['webhook-signature'], 'v1,' + expected)

    def test_policy_requires_private_file_and_fixed_scope(self):
        self.path.chmod(0o700)
        f = self.path / 'events-policy.json'
        f.write_text(_json(self.policy)); f.chmod(0o600)
        self.assertEqual(load_policy(f), self.policy)
        f.chmod(0o644)
        with self.assertRaises(ValueError):
            load_policy(f)


if __name__ == '__main__':
    unittest.main(verbosity=2)
