"""Mac Coordination Bridge CLI. No business executor or arbitrary command handler."""

import argparse
from contextlib import AsyncExitStack
import asyncio
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile

from config import DEFAULT_CONFIG, in_scope, load_config, open_store
from bridge_store import read_held_events
from codex_hook import draft_lock, draft_path
from tracking import attach_tracking


def write_private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def network(args, config, store):
    from collector import Limits, collect_once, create_client, run_collector
    auth_dir = Path(config['state_dir']) / 'oauth'
    async def factory():
        return await create_client(config['endpoint'], auth_dir, allow_login=args.command == 'login')
    if args.command == 'login':
        async with await factory() as client:
            await client.call_tool('whoami', {})
        store.set_health('authentication', 'READY', 'Dedicated bridge OAuth and whoami succeeded')
        return {'authentication': 'READY', 'whoami_readback': True}
    if args.command == 'once':
        async with await factory() as client:
            result = await collect_once(store, client.call_tool, limits=Limits())
        # Keep task bodies and result text out of automatic CLI/log output.
        result['verified'] = [{'event_id': r['event']['event_id'], 'job_id': r['event']['job_id'],
                               'event_type': r['event']['event_type']} for r in result['verified']]
        return result
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with AsyncExitStack() as stack:
        port = None
        policy = Path(args.config).with_name('coordinator-port.json')
        if os.path.lexists(policy):
            try:
                from coordinator_port import CoordinatorPort
                candidate = CoordinatorPort(policy, store)
                await stack.enter_async_context(candidate.listening())
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                store.set_health('coordinator_port', 'UNAVAILABLE', 'LOCAL_PORT_REFUSED')
            else:
                port = candidate
                store.set_health('coordinator_port', 'READY', 'LOCAL_PORT_LISTENING')
        try:
            await run_collector(store, factory, stop, limits=Limits(), interval=30, port=port)
        finally:
            if port is not None:
                store.set_health('coordinator_port', 'STOPPED', 'COLLECTOR_STOPPED')
    state = next((h['state'] for h in store.snapshot()['health'] if h['component'] == 'collector'), 'STOPPED')
    return {'collector': state, 'execution_enabled': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'login', 'once', 'run'):
        sub.add_parser(name)
    held = sub.add_parser('held', help='read bounded HELD event metadata without network or state changes')
    held.add_argument('--cursor', type=int, default=0)
    held.add_argument('--limit', type=int, default=25)
    bind = sub.add_parser('bind')
    bind.add_argument('--session-id', required=True)
    bind.add_argument('--job-id', required=True)
    bind.add_argument('--cwd', required=True)
    stage = sub.add_parser('stage-report')
    stage.add_argument('--session-id', required=True)
    stage.add_argument('--turn-id', required=True)
    stage.add_argument('--event-id', required=True)
    stage.add_argument('--status', choices=('REPORTED_COMPLETED', 'REPORTED_FAILED', 'UNKNOWN'), required=True)
    stage.add_argument('--result-file', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    config = load_config(args.config)
    if args.command == 'held':
        try:
            output = read_held_events(Path(config['state_dir']) / 'bridge.sqlite3',
                config['source_id'], config['consumer_id'], cursor=args.cursor, limit=args.limit)
        except ValueError as error:
            parser.error(str(error))
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    store = open_store(config)
    if args.command == 'status':
        output = store.snapshot()
        output['execution_enabled'] = False
        output['held_query'] = {'command': 'held', 'cursor': 0, 'limit': 25}
        output['session_evidence'] = 'Last observed lifecycle signals; missing or old signals do not prove a process stopped.'
    elif args.command == 'bind':
        if not in_scope(config, args.cwd):
            parser.error('cwd is outside the configured projects')
        output = store.bind_session(args.session_id, args.job_id, str(Path(args.cwd).resolve()))
    elif args.command == 'stage-report':
        if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', args.event_id) or args.event_id.startswith('submitted:'):
            parser.error('event-id must be a stable client event identifier')
        if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', args.turn_id):
            parser.error('turn-id must be an explicit native turn identifier')
        binding = store.session_binding(args.session_id)
        if not binding or not in_scope(config, binding['cwd']):
            parser.error('session must first be bound within a configured project')
        if 'tracking' not in binding['job']:
            parser.error('bound job has no GitHub tracking; preserve its payload/hash and reconcile the original task association without redispatching work')
        result_file = Path(args.result_file)
        if not result_file.is_file() or result_file.stat().st_size > 1024 * 1024:
            parser.error('result file must be a JSON object up to 1 MiB')
        result = json.loads(result_file.read_text())
        if not isinstance(result, dict):
            parser.error('result must be a JSON object')
        result = attach_tracking(result, binding['job'].get('tracking'))
        if len(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()) > 1024 * 1024:
            parser.error('canonical result exceeds 1 MiB')
        draft = {'session_id': args.session_id, 'turn_id': args.turn_id, 'job_id': binding['job_id'], 'event_id': args.event_id, 'status': args.status, 'result': result}
        path = draft_path(config, args.session_id)
        with draft_lock(config):
            if path.exists() and json.loads(path.read_text()) != draft:
                parser.error('a different staged report already exists; reconcile the original event before replacing it')
            write_private_json(path, draft)
        output = {'staged': True, 'job_id': binding['job_id'], 'event_id': args.event_id, 'upload': 'WAITING_FOR_BOUND_SESSION_STOP'}
    else:
        lock_path = Path(config['state_dir']) / 'collector.lock'
        with lock_path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.error('another bridge network owner is active')
            try:
                output = asyncio.run(network(args, config, store))
            except Exception as error:
                from collector import _auth_error
                state = _auth_error(error) or 'ERROR'
                store.set_health('network', state, type(error).__name__)
                print(json.dumps({'status': state, 'error_type': type(error).__name__, 'execution_enabled': False}))
                return 1
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return int(output.get('state', output.get('collector')) in
               ('AUTH_REQUIRED', 'AUTH_FAILED', 'PROTOCOL_FAILED', 'NETWORK_FAILED'))


if __name__ == '__main__':
    raise SystemExit(main())
