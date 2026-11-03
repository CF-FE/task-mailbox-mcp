"""Synthetic transport-only coordinator results through the original local port."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import unittest
from unittest.mock import patch

import test_coordinator_port as existing
import mac.coordinator_port as port_module
from mac.bridge_store import BridgeStore
from mac.collector import collect_once
from mac.coordinator_port import CoordinatorPort, PortError


TARGET = {'service': 'transport-probe', 'operation': 'sha256'}


class TransportProbePortTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = f = existing.CoordinatorPortTests('test_origin_from_attested_record_and_not_payload')
        f.setUp()
        self.addCleanup(f.tmp.cleanup)
        self.nonce = 'synthetic-nonce-0001'
        self.expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        f.j['payload'].pop('coordinator_request')
        f.j['payload'].update(sender='cloud-dot', target=TARGET, content=self.nonce)
        f.j['payload_hash'] = f.grant['payload_hash'] = existing.sha(f.j['payload'])
        f.grant['transport_probe'] = {'nonce': self.nonce, 'expires_at': self.expiry}
        f.transport.jobs['job-1'] = f.j
        f.transport.events[0]['data']['payload_hash'] = f.j['payload_hash']
        self.save()

    def save(self):
        self.f.save()
        self.f.port = CoordinatorPort(self.f.policy, self.f.store)

    def request(self):
        f = self.f
        wire = {'schema_version': 1, 'type': 'TRANSPORT_PROBE_RESULT', 'job_id': 'job-1',
                'payload_hash': f.j['payload_hash'], 'operation': 'sha256',
                'nonce_sha256': hashlib.sha256(self.nonce.encode()).hexdigest(),
                'execution_enabled': False, 'business_accepted': False}
        if 'tracking' in f.j['payload']:
            wire['task_tracking'] = f.j['payload']['tracking']
        return {'v': 1, 'op': 'enqueue_result', 'project_id': f.p, 'job_id': 'job-1',
                'payload_hash': f.j['payload_hash'],
                'event_id': 'transport-probe-' + existing.sha(['job-1', f.j['payload_hash']]),
                'status': 'REPORTED_COMPLETED', 'result': wire}

    async def test_capture_only_then_coordinator_result_enqueues_once_without_ack(self):
        f = self.f
        await f.collect()
        self.assertEqual([], f.store.pending_reports())
        delivered = f.dispatch(f.req)
        self.assertEqual({'origin', 'origin_ref', 'job_id', 'payload_hash', 'task', 'record'}, set(delivered))
        self.assertEqual(f.j, delivered['record'])
        request = self.request()
        first = f.dispatch(request)
        self.assertEqual('PENDING', first['state'])
        self.assertEqual(first, f.dispatch(request))
        await f.collect()
        self.assertEqual(1, len(f.transport.reports))
        self.assertEqual('SENT', f.dispatch({
                'v': 1, 'op': 'lookup_report', 'job_id': 'job-1', 'event_id': request['event_id']})['state'])
        self.assertNotIn('ack_result_read', [name for name, _ in f.transport.calls])

    async def test_wrong_nonce_hash_target_sender_and_business_extra_do_not_capture(self):
        f = self.f
        for key, value in [('content', 'different-nonce-0001'), ('target', {'service': 'project-coordinator'}),
                           ('sender', 'someone-else'), ('coordinator_request', {})]:
            changed = deepcopy(f.j)
            changed['payload'][key] = value
            changed['payload_hash'] = existing.sha(changed['payload'])
            f.grant['payload_hash'] = changed['payload_hash']
            self.save()
            self.assertIsNone(f.port.capture_proof(changed))
        f.grant['payload_hash'] = f.j['payload_hash']
        self.save()
        self.assertIsNotNone(f.port.capture_proof(f.j))
        changed = deepcopy(f.j)
        changed['payload_hash'] = '0' * 64
        self.assertIsNone(f.port.capture_proof(changed))

    async def test_expiry_and_cancel_refuse_first_enqueue_but_preserve_existing(self):
        f = self.f
        await f.collect()
        request = self.request()
        class FutureDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(timezone.utc) + timedelta(days=2)
        with patch.object(port_module, 'datetime', FutureDatetime):
            self.assertIsNone(f.port.capture_proof(f.j))
            self.assertEqual('ACCESS_DENIED', f.dispatch(f.req))
            self.assertEqual('ACCESS_DENIED', f.dispatch(request))
        f.j['cancel_requested'] = True
        self.assertIsNone(f.port.capture_proof(f.j))
        f.store.record_job(f.j, proof_hash=f.port.capture_proof(f.j))
        self.assertEqual('ACCESS_DENIED', f.dispatch(f.req))
        self.assertEqual('ACCESS_DENIED', f.dispatch(request))
        f.j['cancel_requested'] = False
        # Fresh fixture ledger is needed because cancellation is monotonic.
        f.store = BridgeStore(f.store.path.parent / 'fresh', 'test')
        self.save()
        await f.collect()
        pending = f.dispatch(request)
        self.assertEqual('PENDING', pending['state'])
        with patch.object(port_module, 'datetime', FutureDatetime):
            self.assertEqual(pending, f.dispatch(request))
            self.assertEqual(pending, f.dispatch({'v': 1, 'op': 'lookup_report',
                'job_id': 'job-1', 'event_id': request['event_id']}))

    async def test_restart_ack_loss_reuses_exact_original_report(self):
        f = self.f
        await f.collect()
        request = self.request()
        f.dispatch(request)
        lost = True
        async def call(name, args):
            nonlocal lost
            answer = await f.transport(name, args)
            if name == 'publish_result' and lost:
                lost = False
                raise OSError('receipt lost after commit')
            return answer
        await collect_once(f.store, call)
        f.store = BridgeStore(f.store.path, 'test')
        self.save()
        self.assertEqual('PENDING', f.dispatch(request)['state'])
        await collect_once(f.store, call)
        self.assertEqual(1, len(f.transport.reports))
        sent = [args for name, args in f.transport.calls if name == 'publish_result']
        self.assertEqual(sent[0], sent[1])
        self.assertNotIn('ack_result_read', [name for name, _ in f.transport.calls])

    async def test_result_contract_rejects_changed_event_and_true_execution_flags(self):
        f = self.f
        await f.collect()
        request = self.request()
        self.assertEqual('INVALID_REQUEST', f.dispatch({**request, 'event_id': 'wrong'}))
        for key, value in [('nonce_sha256', '0' * 64), ('execution_enabled', True),
                           ('business_accepted', True), ('schema_version', True)]:
            wrong = deepcopy(request)
            wrong['result'][key] = value
            self.assertEqual('CONFLICT', f.dispatch(wrong))
        self.assertEqual([], f.store.pending_reports())

    def test_strict_typed_policy_rejects_bad_schema_and_probe_key(self):
        f = self.f
        for probe in ({'nonce': self.nonce, 'expires_at': self.expiry, 'extra': True},
                      {'nonce': 'short', 'expires_at': self.expiry},
                      {'nonce': self.nonce, 'expires_at': '2030-01-01T00:00:00+08:00'}):
            f.grant['transport_probe'] = probe
            f.save()
            with self.assertRaises(PortError):
                CoordinatorPort(f.policy, f.store)
        f.grant['transport_probe'] = {'nonce': self.nonce, 'expires_at': self.expiry}
        f.grant['key_id'] = 'probe-test'
        f.save()
        with self.assertRaises(PortError):
            CoordinatorPort(f.policy, f.store)


if __name__ == '__main__':
    unittest.main()
