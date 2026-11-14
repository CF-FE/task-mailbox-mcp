"""Publish one frozen, explicitly reported checkpoint through an execution-scoped source."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
import re
from urllib.parse import quote

if __package__:
    from .observability import REQUEST_TIMEOUT, ROUND_TIMEOUT, load_settings, private_bytes, request
    from .observability_store import MAX_BATCH_BYTES, stable_id, valid_id
else:
    from observability import REQUEST_TIMEOUT, ROUND_TIMEOUT, load_settings, private_bytes, request
    from observability_store import MAX_BATCH_BYTES, stable_id, valid_id


def checkpoint_batch(settings, session_id, checkpoint):
    mapping = settings.get('sessions', {}).get(session_id)
    if not mapping or not {'task_id', 'run_id', 'execution_id'} <= mapping.keys():
        raise ValueError('checkpoint requires an exact execution mapping')
    required = {'checkpoint_id', 'observed_at', 'entity_type', 'payload', 'evidence_ref'}
    if not isinstance(checkpoint, dict) or not required <= checkpoint.keys() <= required | {'entity_id', 'occurred_at'}:
        raise ValueError('invalid checkpoint input')
    entity_type = checkpoint['entity_type']
    payload = checkpoint['payload']
    allowed_fields = {'task': {'next_action', 'reported_status'}, 'blocker': {'reason', 'waiting_on', 'next_action'}}
    if not isinstance(entity_type, str) or entity_type not in allowed_fields:
        raise ValueError('checkpoint supports task and blocker activities')
    if not isinstance(payload, dict) or not payload or not payload.keys() <= allowed_fields[entity_type]:
        raise ValueError('invalid checkpoint payload fields')
    task_id = mapping['task_id']
    entity_id = checkpoint.get('entity_id', task_id)
    if (not valid_id(entity_id) or not valid_id(checkpoint['checkpoint_id'])
            or (entity_type == 'task' and entity_id != task_id)
            or (entity_type == 'blocker' and ('entity_id' not in checkpoint or not payload.get('reason')))):
        raise ValueError('checkpoint requires a stable entity and checkpoint identity')
    at = checkpoint['observed_at']
    occurred = checkpoint.get('occurred_at')
    for stamp in (at,) if occurred is None else (at, occurred):
        if not isinstance(stamp, str) or datetime.fromisoformat(stamp).utcoffset() is None:
            raise ValueError('checkpoint timestamps require a timezone')
    if any(value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 2048)
           for value in payload.values()):
        raise ValueError('checkpoint values must be bounded text or null')
    ref = checkpoint['evidence_ref']
    if not isinstance(ref, str) or not ref or len(ref) > 2048:
        raise ValueError('checkpoint requires a bounded evidence reference')
    event_id = stable_id('checkpoint', [settings['source_id'], mapping['execution_id'], checkpoint['checkpoint_id']])
    event = {'event_id': event_id, 'project_id': settings['project_id'],
             'entity_type': entity_type, 'entity_id': entity_id, 'event_type': 'activity',
             'task_id': task_id, 'run_id': mapping['run_id'], 'execution_id': mapping['execution_id'],
             'observed_at': at, 'occurred_at': checkpoint.get('occurred_at'),
             'payload': payload,
             'evidence': [{'source_type': 'agent_checkpoint', 'authority': 'self_report',
                           'ref': ref, 'observed_at': at}]}
    return {'schema_version': 1, 'source_id': settings['source_id'],
            'batch_id': stable_id('batch', [settings['source_id'], event]), 'events': [event]}


async def publish(settings, batch):
    import httpx2

    token = private_bytes(Path(settings['token_file']), 4096).decode('ascii').strip()
    if not re.fullmatch(r'[!-~]+', token):
        raise ValueError('invalid execution source token')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
               'Content-Type': 'application/json'}
    try:
        async with asyncio.timeout(ROUND_TIMEOUT), httpx2.AsyncClient(
            headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=False,
        ) as client:
            suffix = '/' + quote(batch['batch_id'], safe='')
            receipt = await request(client, settings, 'GET', suffix)
            if receipt is None:
                receipt = await request(client, settings, 'POST', '', batch)
    except httpx2.HTTPError as error:
        raise OSError('checkpoint transport failed') from error
    # Compare the submitted event id and both batch identities without logging the body.
    if not isinstance(receipt, dict) or receipt.get('persisted') is not True:
        raise ValueError('checkpoint receipt is unconfirmed')
    accepted, duplicates = receipt.get('accepted_event_ids'), receipt.get('duplicate_event_ids')
    if (receipt.get('batch_id') != batch['batch_id'] or receipt.get('source_id') != batch['source_id']
            or not isinstance(accepted, list) or not isinstance(duplicates, list)
            or accepted + duplicates != [batch['events'][0]['event_id']]):
        raise ValueError('checkpoint receipt does not match the batch')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--checkpoint', type=Path, required=True,
                        help='immutable private JSON, retained for same-ID receipt lookup')
    args = parser.parse_args()
    try:
        settings = load_settings(args.settings)
        checkpoint = json.loads(private_bytes(args.checkpoint, MAX_BATCH_BYTES))
        batch = checkpoint_batch(settings, args.session_id, checkpoint)
        receipt = asyncio.run(publish(settings, batch))
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f'Checkpoint receipt unavailable ({type(error).__name__}). Keep the original input for reconciliation.\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
