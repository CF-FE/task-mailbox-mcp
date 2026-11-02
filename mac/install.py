"""Install reviewed Mac bridge files and additive Codex hooks; does not grant trust."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import tempfile

LABEL = 'io.maccoordinationbridge.coordinator'
HOOK_LABEL = 'Mac Coordination Bridge local coordination'
OWNED_HOOK_LABELS = (HOOK_LABEL, 'Task Mailbox local coordination')
EVENTS = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'Interrupt', 'SessionEnd')


def merged_hooks(existing, command):
    if not isinstance(existing, dict) or not isinstance(existing.get('hooks', {}), dict):
        raise ValueError('existing hook configuration is not an object')
    result = json.loads(json.dumps(existing))
    hooks = result.setdefault('hooks', {})
    for name in EVENTS:
        groups = hooks.get(name, [])
        if not isinstance(groups, list):
            raise ValueError('existing hook event must be an array')
        kept = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
                raise ValueError('existing hook group must contain handlers')
            handlers = [handler for handler in group['hooks']
                        if not (isinstance(handler, dict) and handler.get('statusMessage') in OWNED_HOOK_LABELS)]
            if handlers or not group['hooks']:
                kept.append({**group, 'hooks': handlers})
        own = {'hooks': [{'type': 'command', 'command': command, 'timeout': 3,
                          'statusMessage': HOOK_LABEL}]}
        if name in ('PreToolUse', 'PostToolUse'):
            own['matcher'] = r'^(Bash|apply_patch|mcp__task.mailbox__.*)$'
        kept.append(own)
        hooks[name] = kept
    return result


def atomic_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(runtime, state, endpoint, roots, hook_file, launch_file, source_commit, start_at_login=False):
    if not isinstance(start_at_login, bool):
        raise ValueError('start_at_login must be boolean')
    os.umask(0o077)
    runtime, state = Path(runtime).resolve(), Path(state).resolve()
    python = runtime / '.venv/bin/python'
    if not python.is_file():
        raise ValueError('prepare the independent virtual environment before installing')
    from config import load_config
    config_path = runtime / 'config.json'
    config = {'schema_version': 1, 'endpoint': endpoint, 'source_id': 'vps-mailbox-primary',
              'consumer_id': 'mac-coordinator', 'state_dir': str(state),
              'allowed_roots': [str(Path(p).resolve()) for p in roots]}
    # Validate without replacing any existing runtime configuration.
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory() as temporary:
        test_config = Path(temporary) / 'config.json'
        test_config.write_text(json.dumps(config))
        load_config(test_config)
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise ValueError('existing runtime identity/scope differs; reconcile before replacing')
    hook_file, launch_file = Path(hook_file), Path(launch_file)
    app = runtime / 'app'
    plist = {'Label': LABEL, 'ProgramArguments': [str(python), str(app / 'bridge.py'), '--config', str(config_path), 'run'],
             'WorkingDirectory': str(app), 'RunAtLoad': start_at_login, 'KeepAlive': False,
             'ThrottleInterval': 60, 'StandardOutPath': '/dev/null',
             'StandardErrorPath': str(state / 'bootstrap.log')}
    if launch_file.exists():
        previous = plistlib.loads(launch_file.read_bytes())
        if not isinstance(previous, dict) or any(previous.get(key) != plist[key]
                for key in ('Label', 'ProgramArguments', 'WorkingDirectory')):
            raise ValueError('existing launch agent configuration differs; reconcile before replacing')
        prior_start = previous.get('RunAtLoad', False)
        if not isinstance(prior_start, bool):
            raise ValueError('existing launch agent RunAtLoad must be boolean')
        plist['RunAtLoad'] = start_at_login or prior_start
    existing = json.loads(hook_file.read_text()) if hook_file.exists() else {}
    command = ' '.join(shlex.quote(str(p)) for p in (python, runtime / 'app/codex_hook.py')) + ' --config ' + shlex.quote(str(config_path))
    hook_config = merged_hooks(existing, command)
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime.chmod(0o700)
    state.chmod(0o700)
    app.mkdir(exist_ok=True, mode=0o700)
    files = {}
    for source in Path(__file__).resolve().parent.iterdir():
        if source.is_file() and source.suffix in ('.py', '.txt'):
            if source.is_symlink():
                raise ValueError('runtime source must be a regular independent copy')
            data = source.read_bytes()
            atomic_private(app / source.name, data)
            files[source.name] = hashlib.sha256(data).hexdigest()
    atomic_private(config_path, (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode())
    if hook_file.exists() and existing != hook_config:
        backup = state / ('hooks.before-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        atomic_private(backup, hook_file.read_bytes())
    atomic_private(hook_file, (json.dumps(hook_config, ensure_ascii=False, indent=2) + '\n').encode())
    atomic_private(launch_file, plistlib.dumps(plist))
    receipt = {'source_commit': source_commit, 'files': files, 'execution_enabled': False,
               'scope_roots': config['allowed_roots'], 'hooks': 'INSTALLED_REVIEW_REQUIRED',
               'launch_agent': 'PREPARED_NOT_BOOTSTRAPPED', 'start_at_login': plist['RunAtLoad'],
               'installed_at': datetime.now(timezone.utc).isoformat()}
    atomic_private(runtime / 'install-receipt.json', (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode())
    return {'files_copied': len(files), 'hook_events': list(EVENTS), 'hook_trust': 'REVIEW_REQUIRED',
            'launch_agent': 'PREPARED_NOT_BOOTSTRAPPED', 'start_at_login': plist['RunAtLoad'],
            'execution_enabled': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--allowed-root', action='append', required=True)
    parser.add_argument('--hook-file', default=str(Path.home() / '.codex/hooks.json'))
    parser.add_argument('--launch-agent', default=str(Path.home() / f'Library/LaunchAgents/{LABEL}.plist'))
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--start-at-login', action='store_true',
                        help='enable RunAtLoad in the prepared unit; does not load or start it')
    args = parser.parse_args()
    print(json.dumps(install(args.runtime_dir, args.state_dir, args.endpoint, args.allowed_root,
                             args.hook_file, args.launch_agent, args.source_commit,
                             start_at_login=args.start_at_login), ensure_ascii=False))


if __name__ == '__main__':
    main()
