"""Local hook projection and bounded, receipt-driven source uploads."""

import argparse
import asyncio
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from urllib.parse import quote, urlsplit

if __package__:
    from .observability_store import MAX_BATCH_BYTES, ObservationStore, canonical, stable_id, valid_id
else:
    from observability_store import MAX_BATCH_BYTES, ObservationStore, canonical, stable_id, valid_id

SETTINGS_NAME = 'observations.json'
BUFFER_NAME = 'observations.sqlite3'
GAP_NAME = 'observations.gap'
ROUND_TIMEOUT = 20
INTERVAL = 5
SETTINGS_KEYS = {'schema_version', 'api_base', 'source_id', 'project_id', 'token_file', 'jobs'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def private_bytes(path, max_bytes):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('source configuration must be a private owner file')
        content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError('source configuration exceeds its read limit')
    return content


def load_settings(path):
    settings = json.loads(private_bytes(path, 64 * 1024))
    if (not isinstance(settings, dict) or not SETTINGS_KEYS <= settings.keys() <= SETTINGS_KEYS | {'sessions'}
            or type(settings['schema_version']) is not int or settings['schema_version'] != 1):
        raise ValueError('invalid observation settings')
    base = settings['api_base']
    if not isinstance(base, str):
        raise ValueError('observation API base must be a string')
    url = urlsplit(base)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or not url.path.endswith('/api/v1')):
        raise ValueError('observation API requires an explicit HTTPS /api/v1 base')
    if not isinstance(settings['token_file'], str) or not Path(settings['token_file']).is_absolute():
        raise ValueError('observation token_file must be absolute')
    if not all(valid_id(settings[key]) for key in ('source_id', 'project_id')):
        raise ValueError('invalid observation scope identity')
    jobs = settings['jobs']
    if not isinstance(jobs, dict) or len(jobs) > 99:
        raise ValueError('observation jobs must be an object with at most 99 mappings')
    for job_id, row in jobs.items():
        if (not valid_id(job_id) or not isinstance(row, dict)
                or set(row) != {'task_id'} or not valid_id(row['task_id'])):
            raise ValueError('invalid observation task or role mapping')
    sessions = settings.get('sessions', {})
    if not isinstance(sessions, dict) or len(sessions) > 99:
        raise ValueError('invalid observation sessions')
    role_fields = {'role_id', 'role_kind', 'cwd'}
    for session_id, row in sessions.items():
        if (not valid_id(session_id) or not isinstance(row, dict)
                or not role_fields <= row.keys() <= role_fields | {'role_binding_id', 'task_id'}
                or not all(valid_id(row[key]) for key in row.keys() - {'cwd'})
                or row['role_kind'] not in {'owner', 'coordinator', 'router', 'worker'}
                or not isinstance(row['cwd'], str) or not Path(row['cwd']).is_absolute()):
            raise ValueError('invalid exact session observation mapping')
    return settings


def note_gap(state_dir):
    # This sidecar remains writable while the database itself is full or busy.
    fd = os.open(state_dir / GAP_NAME, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        os.utime(fd)
    finally:
        os.close(fd)


def project_event(settings, entity_type, entity_id, event_type, payload, ref, at, *,
                  task=None, binding=None, key=None, historical=False):
    event = {'project_id': settings['project_id'], 'entity_type': entity_type, 'entity_id': entity_id,
             'event_type': event_type, 'observed_at': at, 'occurred_at': None, 'payload': payload,
             'historical': historical,
             'evidence': [{'source_type': 'mac_hook', 'authority': 'native_observation',
                           'ref': ref, 'observed_at': None if historical else at}]}
    if task is not None:
        event['task_id'] = task
    if binding is not None:
        event['role_binding_id'] = binding
    event['event_id'] = stable_id('ev', [settings['source_id'], key if key is not None else event])
    return event


def capture_hook(state_dir, event, observed=None):
    """Observe a committed bridge hook or one independently allowed native session."""
    if not isinstance(event, dict):
        return
    settings_path = state_dir / SETTINGS_NAME
    if not os.path.lexists(settings_path):
        return
    try:
        settings = load_settings(settings_path)
        session_id = event.get('session_id')
        if not valid_id(session_id):
            return
        mapping = settings.get('sessions', {}).get(session_id)
        cwd = event.get('cwd')
        if mapping is not None and (not isinstance(cwd, str) or not Path(cwd).is_absolute()
                or Path(cwd).resolve() != Path(mapping['cwd']).resolve()):
            mapping = None
        name = event.get('hook_event_name')
        states = {'SessionStart': 'IDLE', 'UserPromptSubmit': 'ACTIVE', 'PreToolUse': 'ACTIVE',
                  'PostToolUse': 'ACTIVE', 'Stop': 'IDLE', 'Interrupt': 'INTERRUPTED', 'SessionEnd': 'CLOSED'}
        if not isinstance(name, str) or name not in states:
            return
        lifecycle = name in {'SessionStart', 'SessionEnd'} and not event.get('turn_id')
        if observed is not None:
            if observed['duplicate'] and not lifecycle:
                return
            binding = observed.get('binding')
            job_mapping = settings['jobs'].get(binding['job_id']) if binding else None
            if mapping is None:
                mapping = job_mapping
            elif binding and (job_mapping is None or job_mapping.get('task_id') != mapping.get('task_id')):
                # A different explicit bridge binding supersedes the observation's task association.
                mapping = {key: value for key, value in mapping.items() if key != 'task_id'}
            at = observed['session']['observed_at']
        else:
            at = utc_now()
        if mapping is None:
            return
        turn = event.get('turn_id')
        if not lifecycle and not valid_id(turn):
            return
        tool_use_id = ''
        action = name
        if name in {'PreToolUse', 'PostToolUse'}:
            tool_use_id, tool_name = event.get('tool_use_id'), event.get('tool_name')
            if not valid_id(tool_use_id) or not valid_id(tool_name):
                return
            action += ': ' + tool_name
        key = stable_id('hook', [session_id, turn or '', name, tool_use_id]).split(':', 1)[1]
        ref = f'mac-hook:{key}'
        # A lifecycle event without a turn has no unique native delivery key.
        identity = [key, at] if lifecycle else key
        task = mapping.get('task_id')
        role_binding = mapping.get('role_binding_id')
        if 'role_id' in mapping:
            row = project_event(
                settings, 'role', mapping['role_id'], 'snapshot',
                {'kind': mapping['role_kind'], 'observed_state': states[name], 'latest_action': action,
                 'last_observed_at': at, 'session_ref': session_id,
                 'binding_id': role_binding, 'task_id': task},
                ref, at, task=task, binding=role_binding, key=identity,
            )
        else:
            row = project_event(
                settings, 'source', settings['source_id'], 'activity',
                {'kind': 'mac_hook'}, ref, at, task=task, key=identity,
            )
        store = ObservationStore(state_dir / BUFFER_NAME)
        if 'role_id' in mapping and not lifecycle:
            store.enqueue_native_hook(settings['source_id'], row)
        else:
            store.enqueue(settings['source_id'], row)
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, RuntimeError) as error:
        try:
            note_gap(state_dir)
        except OSError:
            pass
        print(f'Mac monitor hook capture unavailable ({type(error).__name__}).', file=sys.stderr)


def import_legacy_summary(state_dir, settings, observed_at):
    """Summarize old rows at a fixed read watermark without assigning historic tasks or times."""
    ledger = state_dir / 'bridge.sqlite3'
    with closing(sqlite3.connect(ledger.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.25)) as db:
        count, last = db.execute('SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM hooks').fetchone()
    event = project_event(
        settings, 'source', settings['source_id'], 'activity',
        {'kind': 'mac_hook', 'state': 'UNKNOWN',
         'gap_reason': f'{count} 原台账 hook（本次读到 rowid {last}）缺逐条历史时间／当时绑定'},
        f'mac-bridge:legacy-summary:{last}', observed_at, historical=True,
    )
    return event


def capture_recovery(state_dir, store, settings):
    flag = state_dir / GAP_NAME
    pending = state_dir / (GAP_NAME + '.pending')
    # A concurrent hook can publish the next flag while this one is being drained.
    if not pending.exists():
        try:
            flag.replace(pending)
        except FileNotFoundError:
            return
    at = datetime.fromtimestamp(pending.stat().st_mtime, tz=timezone.utc).isoformat()
    source_id = settings['source_id']
    rows = [project_event(
        settings, 'source', source_id, 'snapshot',
        {'kind': 'mac_hook',
         'gap_reason': '本地采集曾失败，缺失期间的历史事件未知'},
        'mac-bridge:capture-gap', at,
    )]
    ledger = state_dir / 'bridge.sqlite3'
    if ledger.exists():
        snapshot_at = utc_now()
        # Read only current, explicitly mapped bindings. Never backfill old events.
        with closing(sqlite3.connect(ledger.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.25)) as db:
            db.execute('BEGIN')
            for session_id, mapping in settings.get('sessions', {}).items():
                session = db.execute(
                    'SELECT cwd,job_id,state,updated_at FROM sessions WHERE session_id=?', (session_id,)
                ).fetchone()
                if session is None or Path(session[0]).resolve() != Path(mapping['cwd']).resolve():
                    continue
                _, job_id, state, observed_at = session
                task = mapping.get('task_id')
                if job_id and settings['jobs'].get(job_id, {}).get('task_id') != task:
                    task = None
                binding = mapping.get('role_binding_id')
                rows.append(project_event(
                    settings, 'role', mapping['role_id'], 'snapshot',
                    {'kind': mapping['role_kind'], 'binding_id': binding,
                     'session_ref': session_id, 'task_id': task, 'observed_state': state,
                     'last_observed_at': observed_at or None},
                    'mac-bridge:current-session-snapshot', snapshot_at, task=task, binding=binding,
                ))
    store.enqueue_many(source_id, rows)
    pending.unlink()


class SourceHTTPError(OSError):
    def __init__(self, status_code):
        super().__init__('observation service rejected the request')
        self.status_code = status_code


async def request(client, settings, method, suffix, body=None):
    url = settings['api_base'] + '/observations/batches' + suffix
    content = None if body is None else canonical(body).encode()
    async with client.stream(method, url, content=content) as response:
        missing_candidate = method == 'GET' and response.status_code == 404
        if not response.is_success and not missing_candidate:
            raise SourceHTTPError(response.status_code)
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            if len(raw) + len(chunk) > MAX_BATCH_BYTES:
                raise ValueError('observation receipt exceeds the read budget')
            raw.extend(chunk)
    if not missing_candidate:
        return json.loads(raw)
    try:
        envelope = json.loads(raw)
    except (ValueError, RecursionError):
        raise SourceHTTPError(404) from None
    error = envelope.get('error') if isinstance(envelope, dict) else None
    if (isinstance(error, dict) and error.get('code') == 'NOT_FOUND'
            and isinstance(error.get('message'), str) and error.get('retryable') is False
            and isinstance(envelope.get('request_id'), str) and envelope['request_id']):
        return None
    raise SourceHTTPError(404)


async def flush_one(store, settings, transport):
    batch = await asyncio.to_thread(store.next_batch, settings['source_id'])
    if batch is None:
        return False
    # Lookup first, including after a crash between POST and local acknowledgement.
    receipt = await transport('GET', '/' + quote(batch['batch_id'], safe=''))
    if receipt is None:
        receipt = await transport('POST', '', batch)
    return await asyncio.to_thread(store.acknowledge, batch, receipt)


@contextmanager
def upload_lock(state_dir):
    fd = os.open(state_dir / 'observations.lock', os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


async def monitor_once(state_dir):
    import httpx2

    settings = await asyncio.to_thread(load_settings, state_dir / SETTINGS_NAME)
    raw_token = await asyncio.to_thread(private_bytes, Path(settings['token_file']), 4096)
    token = raw_token.decode('ascii').strip()
    if not re.fullmatch(r'[!-~]+', token):
        raise ValueError('invalid observation source token')
    store = await asyncio.to_thread(ObservationStore, state_dir / BUFFER_NAME)
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
               'Content-Type': 'application/json'}
    with upload_lock(state_dir):
        async with httpx2.AsyncClient(headers=headers, timeout=ROUND_TIMEOUT, follow_redirects=False) as client:
            async def send(method, suffix, body=None):
                return await request(client, settings, method, suffix, body)

            try:
                result = await asyncio.wait_for(flush_one(store, settings, send), ROUND_TIMEOUT)
            except httpx2.HTTPError as error:
                raise OSError('observation transport failed') from error
        # Drain before writing recovery evidence so full buffers can free space.
        await asyncio.to_thread(capture_recovery, state_dir, store, settings)
    return result


async def run_monitor(state_dir, stop_event):
    previous = None
    failures = 0
    while not stop_event.is_set():
        try:
            await monitor_once(state_dir)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
            state = ('UNAVAILABLE', type(error).__name__, getattr(error, 'status_code', None))
            failures += 1
        else:
            state = ('READY',)
            failures = 0
        if state != previous:
            print('Mac monitor: ' + ' '.join(str(value) for value in state if value is not None), file=sys.stderr)
            previous = state
        try:
            await asyncio.wait_for(stop_event.wait(), min(60, INTERVAL * (2 ** min(failures, 4))))
        except asyncio.TimeoutError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('once')
    legacy = commands.add_parser('legacy-summary')
    legacy.add_argument('--observed-at', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'once':
            print(json.dumps({'acknowledged': asyncio.run(monitor_once(args.state_dir))}))
        else:
            at = args.observed_at
            if datetime.fromisoformat(at).utcoffset() is None:
                raise ValueError('summary requires an observation time with timezone')
            settings = load_settings(args.state_dir / SETTINGS_NAME)
            event = import_legacy_summary(args.state_dir, settings, at)
            stored = ObservationStore(args.state_dir / BUFFER_NAME).enqueue(settings['source_id'], event)
            print(json.dumps({'queued': stored}))
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f'Mac monitor unavailable ({type(error).__name__}).\n')


if __name__ == '__main__':
    main()
