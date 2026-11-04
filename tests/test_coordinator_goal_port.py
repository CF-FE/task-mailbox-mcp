"""Independent goal wire contract tests: real local ledger, synthetic signed producer, no network."""

from copy import deepcopy
import unittest

import test_coordinator_port as existing
from mac.coordinator_port import CoordinatorPort, PortError, proof_digest


class CoordinatorGoalPortTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = f = existing.CoordinatorPortTests('test_origin_from_attested_record_and_not_payload')
        f.setUp()
        self.addCleanup(f.tmp.cleanup)
        self.goal = {
            'objective': 'Coordinate an independent review of two existing synthetic segments.',
            'scope_refs': ['asset:batch-01/a', 'asset:batch-01/b'],
            'acceptance_criteria': ['reviewer accepts both segments', 'deliver independent review evidence'],
            'evidence_refs': ['synthetic:existing-audio'],
            'related_request_ids': ['prior-query'],
            'allowed_actions': ['coordinate_existing_owner', 'plan_independent_review', 'report_progress'],
            'next_check_at': '2098-01-01T00:00:00Z',
            'review_requirement': 'independent_listening',
        }
        self.goal['work_key'] = existing.sha({'task_ref': f.t['issue_url'], 'scope_refs': sorted(self.goal['scope_refs'])})
        f.j['payload']['coordinator_request'] = {
            'schema_version': 1, 'request_id': 'goal-request', 'idempotency_key': 'goal-key',
            'kind': 'DELEGATE_GOAL', 'expected_generation': 1, 'deadline': '2099-01-01T00:00:00Z',
            'authorization_ref': 'synthetic:one-goal-authorization', 'evidence_refs': ['synthetic:source'],
            'budget': {'dispatch_attempts': 1, 'production_writes': 0, 'audio_generations': 0},
            'goal': self.goal,
        }
        f.grant['request_kind'] = 'DELEGATE_GOAL'
        self.freeze()

    def freeze(self):
        f = self.f
        f.j['payload_hash'] = f.grant['payload_hash'] = existing.sha(f.j['payload'])
        f.transport.events[0]['data']['payload_hash'] = f.j['payload_hash']
        f.save()

    def result_request(self, version=1, phase='ACCEPTED'):
        f = self.f
        bind = {'project_id': f.p, 'request_id': 'goal-request', 'job_id': 'job-1', 'payload_hash': f.j['payload_hash']}
        report = {'seq': version, 'phase': phase, 'summary': 'Source-attributed synthetic goal update.',
                  'observed_at': '2026-01-01T00:00:00Z', 'evidence_refs': ['synthetic:owner-update'],
                  'next_owner': 'synthetic:existing-owner'}
        if phase == 'COMPLETED':
            report.update(terminal_condition='Both acceptance criteria have source references.',
                          criteria_evidence=[{'criterion': c, 'evidence_refs': ['synthetic:criterion']}
                                             for c in self.goal['acceptance_criteria']],
                          independent_review={'state': 'VERIFIED', 'reviewer': 'synthetic:reviewer',
                                              'producer': 'synthetic:producer', 'scope_refs': self.goal['scope_refs'],
                                              'evidence_refs': ['synthetic:actual-listening'], 'method': 'human_listening'})
        else:
            # Historical valid updates remain readable after their checkpoint has passed.
            report['next_check_at'] = '2026-02-01T00:00:00Z'
        core = {**bind, 'schema_version': 1, 'kind': 'GOAL_UPDATE', 'task_ref': f.t['issue_url'],
                'work_key': self.goal['work_key'], 'action_id': 'goal-action', 'generation': 1,
                'source_principal': 'synthetic:existing-owner', 'source_evidence_ref': 'synthetic:native-turn',
                'goal_state': phase, 'report': report}
        wire = {**bind, 'schema_version': 1, 'type': 'PROJECT_COORDINATOR_RESULT', 'core_version': version,
                'core_result_hash': existing.sha(core), 'result': core, 'task_tracking': f.t}
        return {'v': 1, 'op': 'enqueue_result', 'job_id': 'job-1', 'project_id': f.p,
                'payload_hash': f.j['payload_hash'],
                'event_id': 'coordinate-' + existing.sha([f.grant['coordinator_id'], f.p, 'goal-request', 'job-1', version]),
                'status': 'REPORTED_COMPLETED', 'result': wire}

    def rehash_result(self, request):
        request['result']['core_result_hash'] = existing.sha(request['result']['result'])
        return request

    async def test_legacy_and_query_grants_do_not_authorize_goal(self):
        f = self.f
        del f.grant['request_kind']
        f.save()
        self.assertIsNone(f.port.capture_proof(f.j))
        f.grant['request_kind'] = 'QUERY_BLOCKER'
        f.save()
        self.assertIsNone(f.port.capture_proof(f.j))
        f.grant['request_kind'] = 'DELEGATE_GOAL'
        f.save()
        self.assertIsNotNone(f.port.capture_proof(f.j))
        await f.collect()
        self.assertEqual(f.dispatch(f.req)['record'], f.j)

    async def test_probe_and_mixed_or_unknown_grants_are_rejected(self):
        f = self.f
        for change in ({'key_id': 'probe-test'}, {'request_kind': 'DELEGATE_ANYTHING'},
                       {'transport_probe': {'nonce': 'synthetic-nonce-0001', 'expires_at': '2099-01-01T00:00:00Z'}}):
            with self.subTest(change=change):
                f.cfg['grants'] = [{**f.grant, **change}]
                f.save()
                with self.assertRaises(PortError):
                    CoordinatorPort(f.policy, f.store)
        f.cfg['grants'] = [f.grant]
        f.save()
        self.assertEqual(f.dispatch({'v': 1, 'op': 'probe'}, 'probe-test')['service'], 'original-bridge')
        self.assertEqual(f.dispatch(f.req, 'probe-test'), 'ACCESS_DENIED')

    async def test_exact_source_hash_and_typed_proof_are_required(self):
        f = self.f
        await f.collect()
        delivered = f.dispatch(f.req)
        self.assertEqual(delivered['origin'], f.grant['origin'])
        self.assertNotEqual(delivered['origin']['subject'], f.j['payload']['sender'])
        changed = deepcopy(f.j)
        changed['payload']['coordinator_request']['goal']['objective'] += ' tampered'
        self.assertIsNone(f.port.capture_proof(changed))
        old = proof_digest(f.grant)
        f.grant['request_kind'] = 'QUERY_BLOCKER'
        self.assertNotEqual(old, proof_digest(f.grant))
        f.grant['request_kind'] = 'DELEGATE_GOAL'
        f.grant['origin']['subject'] = 'different-native-subject'
        f.save()
        self.assertEqual(f.dispatch(f.req), 'UNAVAILABLE')

    async def test_goal_schema_budget_scope_and_checkpoint_are_bounded(self):
        f = self.f
        original = deepcopy(f.j)
        mutations = [
            lambda b: b.update(extra='not allowed'),
            lambda b: b.update(expected_generation=True),
            lambda b: b.update(evidence_refs=['x'] * 63),
            lambda b: b['budget'].update(dispatch_attempts=2),
            lambda b: b['budget'].update(production_writes=True),
            lambda b: b['goal'].update(extra='not allowed'),
            lambda b: b['goal'].update(acceptance_criteria=[]),
            lambda b: b['goal'].update(scope_refs=['asset:a', 'asset:a']),
            lambda b: b['goal'].update(allowed_actions=['generate_audio']),
            lambda b: b['goal'].update(work_key='0' * 64),
            lambda b: b['goal'].update(next_check_at='2100-01-01T00:00:00Z'),
            lambda b: b['goal'].update(next_check_at='2098-01-01T00:00:00'),
            lambda b: b['goal'].update(review_requirement='asr_only'),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(case=i):
                f.j = deepcopy(original)
                mutate(f.j['payload']['coordinator_request'])
                self.freeze()
                self.assertIsNone(f.port.capture_proof(f.j))

    async def test_canonical_task_identity_is_stable_across_issue_alias(self):
        f = self.f
        f.j['payload']['origin_task_id'] = 'github:ExampleOrg/demo#2'
        self.freeze()
        await f.collect()
        self.assertEqual(f.dispatch(f.req)['task']['task_ref'], f.t['issue_url'])
        request = self.result_request()
        self.assertEqual(f.dispatch(request)['state'], 'PENDING')
        changed = deepcopy(request)
        changed['result']['result']['task_ref'] = f.j['payload']['origin_task_id']
        self.assertEqual(f.dispatch(self.rehash_result(changed)), 'CONFLICT')

    async def test_multiple_goal_updates_use_original_job_and_stable_events(self):
        f = self.f
        await f.collect()
        first = self.result_request()
        second = self.result_request(2, 'IN_PROGRESS')
        a, b = f.dispatch(first), f.dispatch(second)
        self.assertEqual((a['state'], b['state']), ('PENDING', 'PENDING'))
        self.assertNotEqual(a['event_id'], b['event_id'])
        self.assertEqual(a, f.dispatch(first))  # lost local receipt, original event only
        self.assertEqual(len(f.store.pending_reports()), 2)
        await f.collect()
        for request in (first, second):
            lookup = {k: request[k] for k in ('v', 'job_id', 'event_id')} | {'op': 'lookup_report'}
            self.assertEqual(f.dispatch(lookup)['state'], 'SENT')
        self.assertEqual(len(f.transport.reports), 2)

    async def test_cross_job_kind_scope_and_forged_success_are_rejected(self):
        f = self.f
        await f.collect()
        request = self.result_request()
        for key, value in [('job_id', 'other-job'), ('request_id', 'other-request'),
                           ('payload_hash', '0' * 64), ('kind', 'BLOCKER_REPLY'),
                           ('work_key', '0' * 64), ('goal_state', 'COMPLETED'),
                           ('source_evidence_ref', ''), ('generation', True)]:
            with self.subTest(key=key):
                changed = deepcopy(request)
                changed['result']['result'][key] = value
                self.assertEqual(f.dispatch(self.rehash_result(changed)), 'CONFLICT')
        self.assertEqual(f.dispatch({**request, 'job_id': 'other-job'}), 'ACCESS_DENIED')
        self.assertEqual(f.store.pending_reports(), [])

    async def test_goal_progress_cannot_be_published_as_legacy_query(self):
        f = existing.CoordinatorPortTests('test_origin_from_attested_record_and_not_payload')
        f.setUp()
        self.addCleanup(f.tmp.cleanup)
        await f.collect()
        request = f.result_request()
        request['result']['result']['kind'] = 'GOAL_UPDATE'
        self.assertEqual(f.dispatch(self.rehash_result(request)), 'CONFLICT')
        request['result']['result']['kind'] = 'BLOCKER_REPLY'
        request['result']['result']['goal_state'] = 'COMPLETED'
        self.assertEqual(f.dispatch(self.rehash_result(request)), 'CONFLICT')

    async def test_progress_report_requires_exact_schema_and_historical_checkpoint(self):
        f = self.f
        await f.collect()
        for key, value in [('seq', True), ('observed_at', '2099-01-01T00:00:00Z'),
                           ('next_check_at', '2025-01-01T00:00:00Z'), ('terminal_condition', 'unproved'),
                           ('evidence_refs', [])]:
            with self.subTest(key=key):
                changed = self.result_request()
                changed['result']['result']['report'][key] = value
                self.assertEqual(f.dispatch(self.rehash_result(changed)), 'CONFLICT')
        self.assertEqual(f.dispatch(self.result_request())['state'], 'PENDING')

    async def test_completion_requires_each_criterion_and_independent_actual_listening(self):
        f = self.f
        await f.collect()
        mutations = [
            lambda r: r.pop('independent_review'),
            lambda r: r.update(criteria_evidence=[]),
            lambda r: r['criteria_evidence'][1].update(criterion=r['criteria_evidence'][0]['criterion']),
            lambda r: r['independent_review'].update(method='ASR'),
            lambda r: r['independent_review'].update(reviewer=r['independent_review']['producer']),
            lambda r: r['independent_review'].update(scope_refs=['asset:batch-01/a']),
            lambda r: r['independent_review'].update(evidence_refs=[]),
            lambda r: r.update(next_check_at='2098-01-01T00:00:00Z'),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(case=i):
                changed = self.result_request(2, 'COMPLETED')
                mutate(changed['result']['result']['report'])
                self.assertEqual(f.dispatch(self.rehash_result(changed)), 'CONFLICT')
        self.assertEqual(f.dispatch(self.result_request(2, 'COMPLETED'))['state'], 'PENDING')

    async def test_no_review_goal_can_complete_without_invented_listening(self):
        f = self.f
        self.goal['review_requirement'] = 'none'
        self.freeze()
        await f.collect()
        request = self.result_request(2, 'COMPLETED')
        self.assertEqual(f.dispatch(request), 'CONFLICT')
        del request['result']['result']['report']['independent_review']
        self.assertEqual(f.dispatch(self.rehash_result(request))['state'], 'PENDING')

    async def test_wait_report_is_a_typed_snapshot_not_an_owner_completion(self):
        f = self.f
        await f.collect()
        request = self.result_request()
        core = request['result']['result']
        for key in ('work_key', 'action_id', 'generation', 'report'):
            del core[key]
        core.update(kind='COORDINATOR_WAIT_REPORT', source_principal=f.grant['coordinator_id'],
                    original_kind='DELEGATE_GOAL', goal_state='ADMITTED', last_seq=0,
                    waiting={'reason': 'Awaiting native owner acceptance'})
        self.rehash_result(request)
        for key, value in [('original_kind', 'QUERY_BLOCKER'), ('last_seq', True), ('goal_state', 'COMPLETED'),
                           ('report', {'phase': 'COMPLETED'}), ('source_principal', 'invented-owner')]:
            changed = deepcopy(request)
            changed['result']['result'][key] = value
            self.assertEqual(f.dispatch(self.rehash_result(changed)), 'CONFLICT')
        self.assertEqual(f.dispatch(request)['state'], 'PENDING')


if __name__ == '__main__':
    unittest.main()
