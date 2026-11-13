#!/usr/bin/env python3
"""Export the existing Fanren R17 evidence as one private, replayable batch."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def evidence(name, raw, at, kind):
    if datetime.fromisoformat(at).utcoffset() is None:
        raise ValueError('evidence requires a source timestamp with timezone')
    source_type = 'coordinator_ledger' if name.startswith('coordinator-role-followup') else 'fanren_case_record'
    return {'source_type': source_type, 'ref': f'fanren-r17:{name}', 'observed_at': at,
            'digest': digest(raw), 'authority': kind}


def collect(case_dir, source_id, *, ledger=None, followup_key=None, ledger_observed_at=None):
    if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', source_id):
        raise ValueError('source ID must match the source grant')
    def read(name, time_field, kind):
        raw = (case_dir / name).read_bytes()
        data = json.loads(raw)
        return data, evidence(name, raw, data[time_field], kind)

    decision, accepted = read('owner-r17-r4-actual-acceptance-and-business-next.json', 'decided_at_BJT', 'owner_decision')
    actual, reported = read('S1-R4-ACTUAL-RESULT-RECEIVED.json', 'observed_at_BJT', 'native_observation')
    resume, checkpoint = read('fanren-r17-resume-checkpoint-20261113.retain-intent.json', 'timestamp', 'native_observation')
    dispatch, dispatched = read('COORDINATOR-MAIN-DISPATCH.json', 'prepared_at_BJT', 'native_observation')
    intake, received = read('OWNER-R17-R4-ACTUAL-AND-BUSINESS-NEXT-INPUT.json', 'received_at_BJT', 'native_observation')
    contact, submitted = read('owner-r17-contact-receipt.json', 'returned_at', 'native_observation')
    raw = (case_dir / 'CONTINUE-HERE.md').read_bytes()
    continuation = evidence('CONTINUE-HERE.md', raw, resume['metadata']['observed_at'], 'native_observation')
    project_id = resume['metadata']['project_id']
    acceptance = decision['actual_acceptance']
    if acceptance['choice'] != 'accept' or actual['attempt_id'] != acceptance['attempt']:
        raise ValueError('owner decision does not identify the observed R4 attempt')
    task_id = acceptance['task_id']
    owner = decision['owner']
    writer = decision['business_next']['same_original_writer']
    s2_id = decision['business_next']['S2_release_after_verified_Sol_intake']['unit_id']
    coordinator_id = 'coordinator:' + dispatch['sole_dispatcher']['thread']
    worker_id = 'worker:' + digest(actual['native_proof']['thread'].encode())
    run_id = actual['attempt_id']
    evidence_rows = [reported, accepted, continuation]
    role_evidence = [dispatched, continuation]
    continuation_text = raw.decode()
    state = re.search(r'S1 台账仍为 ([A-Z_]+) rev(\d+)', continuation_text)
    if not state:
        raise ValueError('continuation does not identify the historical ledger revision')
    followup_status, revision = state.group(1), int(state.group(2))
    check = re.search(r'(\d{2}:\d{2}) coordinator internal', decision['internal_checkpoint'])
    def on_source_date(clock, day):
        return datetime.combine(datetime.fromisoformat(day).date(), clock,
                                datetime.fromisoformat(day).tzinfo).isoformat()

    internal_check_at = (on_source_date(datetime.strptime(check.group(1), '%H:%M').time(),
                                        accepted['observed_at']) if check else None)
    coordinator_clock = re.search(r'原生记录仍停在 (\d{2}:\d{2}:\d{2})', continuation_text)
    writer_clock = re.search(r'原编剧最新仍是 (\d{2}:\d{2}:\d{2}) 结束的旧', continuation_text)

    def recorded_clock(match):
        return (on_source_date(datetime.strptime(match.group(1), '%H:%M:%S').time(),
                               continuation['observed_at']) if match else None)
    c001_review = None
    c001_review_key = 'fanren-C001-direct-impact-20261113'
    if ledger is not None:
        if not followup_key or not ledger_observed_at:
            raise ValueError('ledger read requires the exact followup key and observation time')
        with closing(sqlite3.connect(ledger.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            query = ('SELECT state,revision,next_check_at,action_id FROM role_followups '
                     'WHERE project_id=? AND followup_key=?')
            row = db.execute(query, (project_id, followup_key)).fetchone()
            c001_review = db.execute(query, (project_id, c001_review_key)).fetchone()
        if row is None or row[1] < revision:
            raise ValueError('the exact original followup is missing or older than the case snapshot')
        followup_status, revision, internal_check_at, _ = row
        evidence_rows.append(evidence('coordinator-role-followup', encode(row), ledger_observed_at, 'native_observation'))
    events = []

    def event(entity_type, entity_id, payload, references, *, task=None, run=None, binding=None,
              event_type='snapshot', occurred_at=None, source_revision=None):
        observed_at = max((ref['observed_at'] for ref in references), key=datetime.fromisoformat)
        row = {'project_id': project_id, 'entity_type': entity_type, 'entity_id': entity_id,
               'event_type': event_type, 'observed_at': observed_at, 'occurred_at': occurred_at,
               'payload': payload, 'evidence': references}
        if event_type == 'activity':
            row['historical'] = True
        elif ledger_observed_at:
            row['observed_at'] = ledger_observed_at
        for key, value in (('task_id', task), ('run_id', run), ('role_binding_id', binding),
                           ('source_revision', source_revision)):
            if value is not None:
                row[key] = value
        row['event_id'] = 'ev:' + digest(encode(row))
        events.append(row)

    event('project', project_id, {'name': '凡人《入界筹码》', 'owner_role_id': owner['role'],
          'summary': 'R4 限定验收与协调台账状态分别保留。S2 接单及 C001 控制链路仍待核。',
          'scope_revision': None, 'total_tasks': None}, [checkpoint])
    event('task', task_id, {'title': 'R4 四项输入身份取证', 'owner_role_id': owner['role'],
          'reported_status': 'REVIEW_REQUIRED', 'acceptance_status': 'ACCEPTED',
          'formal_status': None, 'followup_status': followup_status,
          'followup_revision': revision,
          'followup_ref': 'fanren-r17:coordinator-role-followup' if ledger is not None else continuation['ref'],
          'next_action': '沿原协调员入口核对 R17 验收与协调台账',
          'committed_deadline_at': None, 'internal_check_at': internal_check_at,
          'workflow_run_id': run_id, 'last_progress_at': accepted['observed_at']}, evidence_rows, task=task_id, run=run_id,
          source_revision=revision)
    event('task', task_id, {'title': 'R4 原回件', 'reported_status': 'REVIEW_REQUIRED'},
          [reported], task=task_id, run=run_id, event_type='activity', occurred_at=reported['observed_at'])
    event('task', task_id, {'title': 'R17 负责人限定验收', 'acceptance_status': 'ACCEPTED'},
          [accepted], task=task_id, run=run_id, binding=owner['role'], event_type='activity',
          occurred_at=accepted['observed_at'])
    event('task', s2_id, {'title': 'C155／C177 S2 离线实现', 'owner_role_id': owner['role'],
          'reported_status': 'UNKNOWN', 'acceptance_status': None, 'formal_status': None,
          'next_action': '由原协调员核实同一编剧入口并取得实际 Sol 接单回执',
          'committed_deadline_at': None, 'internal_check_at': None}, [continuation], task=s2_id)

    def role(identifier, name, kind, refs, *, task=None, session=None, state='UNKNOWN',
             action=None, progress=None, next_action=None, binding=None, last_activity_at=None):
        event('role', identifier, {'name': name, 'kind': kind, 'binding_id': binding,
              'host_ref': None, 'session_ref': session, 'task_id': task, 'observed_state': state,
              'latest_action': action, 'last_observed_at': last_activity_at,
              'last_progress_at': progress, 'next_action': next_action},
              refs, task=task, binding=binding)

    role(owner['role'], owner['role'], 'owner', [accepted], task=task_id, session=owner['thread'],
         state='ACCEPTED', action='负责人 R17 接受 R4 限定结果', progress=accepted['observed_at'],
         binding=owner['role'], last_activity_at=accepted['observed_at'])
    role(coordinator_id, '原协调员', 'coordinator', role_evidence, task=task_id,
         session=dispatch['sole_dispatcher']['thread'], state='UNKNOWN', action=f'协调台账 {followup_status} rev{revision}',
         next_action='沿原协调台账核对并记录负责人验收', last_activity_at=recorded_clock(coordinator_clock))
    role(worker_id, 'R4 原执行回件方', 'worker', [reported], task=task_id,
         session=actual['native_proof']['thread'], state='REVIEW_REQUIRED',
         action='回传四项身份观察',
         progress=reported['observed_at'], last_activity_at=reported['observed_at'])
    role(writer['name'], writer['name'], 'worker', [continuation], task=s2_id,
         session=writer['thread'], action='历史编剧入口已记录，S2 接单仍无证据',
         next_action='原协调员核实原入口并取得 Sol 接单', last_activity_at=recorded_clock(writer_clock))
    role('fanren-codex-router', 'Codex 路由', 'router', [continuation],
         action='本轮实际调用者与路由派发证据待核', last_activity_at=None)
    event('source', 'fanren-codex-router', {'kind': 'codex_router', 'state': 'UNKNOWN',
          'gap_reason': '未取得本次实际路由调用与 Worker 派发的原生记录'}, [continuation])
    event('source', source_id, {'kind': 'fanren_case_record', 'state': 'STALE',
          'gap_reason': 'R17 原件提供历史证据。S2 新回合、实际路由及现场工作流状态缺当前记录。'}, [continuation])
    if not (contact['action_id'] == decision['CONTACT_ACTION_ID'] == intake['CONTACT_ACTION_ID']
            and contact['identity']['name'] == owner['role']
            and intake['actual_attempt'] == run_id and contact['state'] == 'SUBMITTED'):
        raise ValueError('R17 native contact does not match the observed attempt and owner decision')
    event('delegation', contact['action_id'],
          {'task_id': task_id, 'from_role_id': coordinator_id, 'to_role_id': owner['role'],
           'actual_caller_role_id': None, 'state': 'SUBMITTED', 'requested_at': contact['intent_at'],
           'accepted_at': None, 'evidence': [received, submitted, accepted]},
          [received, submitted, accepted], task=task_id)
    event('workflow', run_id, {'task_id': task_id, 'definition_version': None, 'kind': 'observed',
          'nodes': [
              {'id': 'r4-original-read', 'label': 'R4 四文件单次读取', 'state': 'REVIEW_REQUIRED',
               'measured_duration_seconds': actual['actual_result']['local_elapsed_seconds'],
               'measured_duration_basis': '本地读取包装器单次测量，不含审批、等待或整项工作',
               'started_at': None, 'finished_at': None, 'wait_started_at': None, 'evidence': [reported]},
              {'id': 'r17-owner-review', 'label': 'R17 负责人限定验收', 'state': 'ACCEPTED',
               'started_at': None, 'finished_at': accepted['observed_at'], 'wait_started_at': None,
               'evidence': [received, submitted, accepted]},
          ],
          'edges': [{'source': 'r4-original-read', 'target': 'r17-owner-review', 'evidence': [received, accepted]}]},
          [reported, received, accepted], task=task_id, run=run_id)
    event('blocker', s2_id + ':intake', {'state': 'OPEN', 'reason': '原编剧尚无新 Sol 回合或 S2 接单',
          'handler_role_id': None, 'waiting_on': '原协调员核实同一编剧入口与真实接单',
          'opened_at': None, 'resolved_at': None, 'next_action': '沿原协调入口接续',
          'committed_deadline_at': None}, [continuation], task=s2_id)
    parent_task_id = 'fanren-episode-next-20261105'
    event('task', parent_task_id, {'title': '凡人《入界筹码》本集后续',
          'owner_role_id': owner['role'],
          'formal_status': None, 'next_action': '由原协调员确认 C001 非中断提案交办'},
          [accepted, continuation], task=parent_task_id)
    if c001_review is not None:
        old_state, old_revision, _, _ = c001_review
        old_ref = evidence('coordinator-role-followup-c001-static-review',
                           encode([c001_review_key, c001_review]), ledger_observed_at, 'native_observation')
        event('task', c001_review_key, {'title': 'C001 原静态影响复核',
              'followup_status': old_state, 'followup_revision': old_revision,
              'followup_ref': old_ref['ref'], 'formal_status': None},
              [old_ref], task=c001_review_key, source_revision=old_revision)
    # Keep the existing observation ID for correction of the association on replay.
    # This identifies the blocker, independent of either followup's native task ID.
    event('blocker', c001_review_key + ':admission', {'state': 'OPEN',
          'reason': 'C001 缺当前已核实的 Session1 控制入口。后继非中断提案待交办，无独立原生任务 ID。',
          'handler_role_id': None, 'waiting_on': '原协调员正式交办，随后由原负责人审查独立方案',
          'opened_at': None, 'resolved_at': None, 'next_action': '仅准备离线提案，现场动作待原授权',
          'committed_deadline_at': None}, [accepted, continuation], task=parent_task_id)
    return {'schema_version': 1, 'batch_id': 'batch:' + digest(encode([source_id, events])),
            'source_id': source_id, 'events': events}


def write_private(path, value):
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encode(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-dir', type=Path, required=True)
    parser.add_argument('--source-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ledger', type=Path)
    parser.add_argument('--followup-key')
    args = parser.parse_args()
    if (args.output.resolve().is_relative_to(args.case_dir.resolve())
            or (args.ledger and args.output.resolve() == args.ledger.resolve())):
        parser.error('output must be outside the read-only case directory')
    observed_at = datetime.now(timezone.utc).isoformat() if args.ledger else None
    batch = collect(args.case_dir, args.source_id, ledger=args.ledger,
                    followup_key=args.followup_key, ledger_observed_at=observed_at)
    write_private(args.output, batch)


if __name__ == '__main__':
    main()
