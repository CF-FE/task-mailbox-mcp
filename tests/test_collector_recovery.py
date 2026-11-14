"""Recovery regressions: temporary ledgers, fake RPCs, no external network."""

import asyncio
import json
import socket
import ssl
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mac.bridge_store import BridgeStore
from mac.collector import AuthFailed, AuthRequired, Limits, ProtocolError, collect_once, run_collector, _client_open_detail
from test_collector import FakeTransport, event, job


class FastBackoffEvent(asyncio.Event):
    async def wait(self):
        if self.is_set():
            return True
        # Simulate the poller's timer expiring without sleeping 60 seconds.
        raise asyncio.TimeoutError()


class CollectorRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = BridgeStore(Path(self.temp.name) / 'bridge.sqlite3', 'test-source')

    def queue_report(self):
        self.store.record_job(job('out-job'))
        return self.store.queue_report('out-job', 'saved-report', 'UNKNOWN', {'output': 'saved'})

    def queue_incoming(self, count=1):
        events = [event(i, job_id=f'in-job-{i}') for i in range(1, count + 1)]
        self.store.ingest_page({'events': events, 'next_cursor': count, 'has_more': False})
        return events

    async def test_pending_query_timeout_does_not_starve_saved_report(self):
        self.queue_report()
        self.queue_incoming()
        transport = FakeTransport(jobs={'in-job-1': job('in-job-1')})
        transport.failures['get_job'] = TimeoutError('private transport text')
        result = await collect_once(self.store, transport, limits=Limits(rpc=4))
        self.assertEqual(self.store.pending_reports(), [])
        self.assertEqual(result['state'], 'NETWORK_FAILED')
        self.assertEqual(result['pending_events'], 1)
        self.assertEqual(result['sent'], 1)
        self.assertLessEqual(result['calls'], 4)
        self.assertFalse(any(h['component'] == 'collector_last_success'
                             for h in self.store.snapshot()['health']))

    async def test_upload_timeout_does_not_starve_new_page_and_job(self):
        self.queue_report()
        transport = FakeTransport([event(job_id='in-job-1')], {'in-job-1': job('in-job-1')})
        transport.failures['publish_result'] = TimeoutError('private upload text')
        result = await collect_once(self.store, transport, limits=Limits(rpc=4))
        self.assertEqual((self.store.cursor(), result['pending_events']), (1, 0))
        self.assertEqual((result['state'], result['pending_reports']), ('NETWORK_FAILED', 1))
        self.assertEqual(len(result['verified']), 1)
        self.assertLessEqual(result['calls'], 4)

    async def test_pending_backlog_leaves_an_upload_opportunity_inside_total_budget(self):
        self.queue_report()
        self.queue_incoming(6)
        transport = FakeTransport(jobs={f'in-job-{i}': job(f'in-job-{i}') for i in range(1, 7)})
        result = await collect_once(self.store, transport, limits=Limits(rpc=4))
        self.assertEqual(self.store.pending_reports(), [])
        self.assertGreater(len(result['verified']), 0)
        self.assertGreater(result['pending_events'], 0)
        self.assertEqual(result['calls'], len(transport.calls))
        self.assertLessEqual(result['calls'], 4)

    async def test_two_slow_directions_share_one_real_round_deadline(self):
        self.queue_report()
        self.queue_incoming()
        calls = []
        async def slow(name, args):
            calls.append(name)
            await asyncio.sleep(5)
        started = time.monotonic()
        result = await asyncio.wait_for(collect_once(self.store, slow,
            limits=Limits(rpc=4, call_timeout=0.025, round_timeout=0.04)), 0.25)
        self.assertLess(time.monotonic() - started, 0.20)
        self.assertIn('get_job', calls)
        self.assertIn('publish_result', calls)
        self.assertEqual(result['state'], 'NETWORK_FAILED')
        self.assertEqual(result['pending_reports'], 1)
        self.assertLessEqual(result['calls'], 4)

    async def test_one_rpc_worker_alternates_opportunity_across_failed_rounds(self):
        self.queue_report()
        self.queue_incoming()
        stop = FastBackoffEvent()
        calls = []
        class Client:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def call_tool(self, name, args):
                calls.append(name)
                if len(calls) >= 2:
                    stop.set()
                raise TimeoutError('fake directional outage')
        async def factory():
            return Client()
        await asyncio.wait_for(run_collector(self.store, factory, stop,
            limits=Limits(rpc=1), interval=1), 0.25)
        self.assertEqual(set(calls), {'get_job', 'publish_result'})
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.store.pending_reports()), 1)
        self.assertEqual(len(self.store.pending_events()), 1)

    async def test_safe_auth_cause_and_creation_stage_survive_wrapping(self):
        async def factory():
            try:
                raise AuthFailed('CLIENT_REGISTRATION_EXPIRED')
            except AuthFailed as error:
                raise RuntimeError('PRIVATE_WRAPPER https://private.invalid/?token=secret') from error
        await run_collector(self.store, factory, asyncio.Event())
        health = next(h for h in self.store.snapshot()['health'] if h['component'] == 'collector')
        self.assertEqual(health['state'], 'AUTH_FAILED')
        self.assertIn('CLIENT_REGISTRATION_EXPIRED', health['detail'])
        self.assertIn('stage:CLIENT_CREATE', health['detail'])
        self.assertIn('type:RuntimeError', health['detail'])
        self.assertNotIn('PRIVATE_WRAPPER', json.dumps(health))
        self.assertNotIn('private.invalid', json.dumps(health))
        self.assertTrue(health['updated_at'])
        self.assertLessEqual(len(health['detail']), 240)

    async def test_auth_required_cause_is_retained_without_retry(self):
        calls = []
        async def factory():
            calls.append('factory')
            raise AuthRequired('LOGIN_REQUIRED')
        await run_collector(self.store, factory, asyncio.Event())
        health = next(h for h in self.store.snapshot()['health'] if h['component'] == 'collector')
        self.assertEqual(calls, ['factory'])
        self.assertEqual(health['state'], 'AUTH_REQUIRED')
        self.assertIn('LOGIN_REQUIRED', health['detail'])
        self.assertIn('stage:CLIENT_CREATE', health['detail'])
        self.assertIn('type:AuthRequired', health['detail'])

    async def test_client_open_records_safe_cause_chain_and_duration(self):
        stop = asyncio.Event()

        class Client:
            _bridge_open_attempt = ("MCP_INITIALIZE", 123, None)

            async def __aenter__(self):
                stop.set()
                try:
                    raise socket.gaierror("private.example?token=secret")
                except socket.gaierror as error:
                    raise RuntimeError("secret wrapper") from error

            async def __aexit__(self, *args):
                return False

        async def factory():
            return Client()

        await run_collector(self.store, factory, stop)
        health = {h['component']: h for h in self.store.snapshot()['health']}
        detail = health['collector_open_failure']['detail']
        self.assertIn('category:DNS phase:MCP_INITIALIZE phase_ms:123 open_ms:', detail)
        self.assertIn('chain:RuntimeError-gaierror', detail)
        self.assertNotIn('secret', detail)
        self.assertEqual(health['collector']['state'], 'NETWORK_FAILED')
        self.assertIn('category:TLS', _client_open_detail(ssl.SSLError('secret'), 1, None))

    async def test_auth_open_failure_keeps_safe_cause_and_stage(self):
        class Client:
            async def __aenter__(self):
                raise AuthFailed('REFRESH_REJECTED')
            async def __aexit__(self, *args):
                return False
        async def factory():
            return Client()
        await run_collector(self.store, factory, asyncio.Event())
        health = next(h for h in self.store.snapshot()['health'] if h['component'] == 'collector')
        self.assertEqual(health['state'], 'AUTH_FAILED')
        self.assertIn('REFRESH_REJECTED', health['detail'])
        self.assertIn('stage:CLIENT_OPEN', health['detail'])

    async def test_untrusted_auth_text_and_exception_type_are_not_persisted(self):
        private_type = type('PRIVATE_SENTINEL_EXCEPTION', (AuthFailed,), {})
        async def factory():
            raise private_type('PRIVATE_SENTINEL https://private.invalid/?code=PRIVATE_SENTINEL')
        await run_collector(self.store, factory, asyncio.Event())
        health = next(h for h in self.store.snapshot()['health'] if h['component'] == 'collector')
        self.assertEqual(health['state'], 'AUTH_FAILED')
        self.assertIn('UNKNOWN_CAUSE', health['detail'])
        self.assertIn('type:UNKNOWN', health['detail'])
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(self.store.snapshot()))
        self.assertNotIn('private.invalid', json.dumps(self.store.snapshot()))

    async def test_close_failure_keeps_safe_cause_and_close_stage(self):
        transport = FakeTransport()
        class Client:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                raise AuthFailed('AUTH_REJECTED')
            async def call_tool(self, name, args):
                return await transport(name, args)
        async def factory():
            return Client()
        await run_collector(self.store, factory, asyncio.Event())
        health = next(h for h in self.store.snapshot()['health'] if h['component'] == 'collector')
        self.assertEqual(health['state'], 'AUTH_FAILED')
        self.assertIn('AUTH_REJECTED', health['detail'])
        self.assertIn('stage:CLIENT_CLOSE', health['detail'])

    async def test_later_context_close_error_does_not_erase_authentication_stop(self):
        stop = asyncio.Event()
        class Client:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                raise OSError('PRIVATE_CLOSE_TEXT')
            async def call_tool(self, name, args):
                stop.set()
                raise AuthFailed('AUTH_REJECTED')
        async def factory():
            return Client()
        await run_collector(self.store, factory, stop)
        health = next(h for h in self.store.snapshot()['health'] if h['component'] == 'collector')
        self.assertEqual(health['state'], 'AUTH_FAILED')
        self.assertIn('AUTH_REJECTED', health['detail'])
        self.assertNotIn('PRIVATE_CLOSE_TEXT', json.dumps(health))

    async def test_successful_report_count_survives_a_later_upload_timeout(self):
        self.queue_report()
        self.store.queue_report('out-job', 'saved-report-2', 'UNKNOWN', {'output': 'second'})
        transport = FakeTransport()
        transport.failures['publish_result'] = [None, TimeoutError('second ACK unavailable')]
        result = await collect_once(self.store, transport, limits=Limits(rpc=4))
        self.assertEqual(result['state'], 'NETWORK_FAILED')
        self.assertEqual(result['sent'], 1)
        self.assertEqual(result['pending_reports'], 1)
        self.assertEqual(self.store.pending_reports()[0]['event_id'], 'saved-report-2')

    async def test_wrapped_authentication_error_is_not_swallowed_as_held_record(self):
        self.queue_report()
        self.queue_incoming()
        async def call(name, args):
            try:
                raise AuthFailed('REFRESH_REJECTED')
            except AuthFailed as error:
                raise ValueError('PRIVATE_WRAPPED_AUTH') from error
        result = await collect_once(self.store, call)
        self.assertEqual(result['state'], 'AUTH_FAILED')
        self.assertEqual(result['calls'], 1)
        self.assertEqual(result['pending_events'], 1)
        self.assertEqual(result['pending_reports'], 1)
        self.assertEqual(self.store.snapshot()['counts']['held_events'], 0)
        self.assertNotIn('PRIVATE_WRAPPED_AUTH', json.dumps(self.store.snapshot()))

    async def test_unknown_protocol_exception_text_is_not_written_to_held_reason(self):
        self.queue_incoming()
        transport = FakeTransport()
        transport.failures['get_job'] = ProtocolError('PRIVATE_SENTINEL_PROTOCOL')
        result = await collect_once(self.store, transport)
        self.assertEqual(result['state'], 'OK')
        self.assertEqual(self.store.snapshot()['counts']['held_events'], 1)
        self.assertFalse(b'PRIVATE_SENTINEL_PROTOCOL' in self.store.path.read_bytes(), 'raw protocol text was persisted')

    async def test_known_protocol_reason_remains_available_for_reconciliation(self):
        self.queue_incoming()
        transport = FakeTransport()
        transport.failures['get_job'] = ProtocolError('RESULT_EVENT_MISSING')
        result = await collect_once(self.store, transport)
        self.assertEqual(result['state'], 'OK')
        self.assertEqual(self.store.snapshot()['counts']['held_events'], 1)
        self.assertTrue(b'RESULT_EVENT_MISSING' in self.store.path.read_bytes())

    async def test_client_configuration_failure_stops_instead_of_network_retry(self):
        calls = []
        stop = FastBackoffEvent()
        async def factory():
            calls.append('factory')
            if len(calls) >= 2:
                stop.set()
            raise ValueError('PRIVATE_CONFIG https://private.invalid/')
        await asyncio.wait_for(run_collector(self.store, factory, stop), 0.25)
        health = next(h for h in self.store.snapshot()['health'] if h['component'] == 'collector')
        self.assertEqual(calls, ['factory'])
        self.assertEqual(health['state'], 'PROTOCOL_FAILED')
        self.assertIn('stage:CLIENT_CREATE', health['detail'])
        self.assertNotIn('PRIVATE_CONFIG', json.dumps(health))


if __name__ == '__main__':
    unittest.main()
