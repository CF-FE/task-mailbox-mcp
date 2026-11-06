"""Private local bridge configuration; no credential or transcript loading."""

import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

DEFAULT_CONFIG = Path.home() / 'Library/Application Support/MacCoordinationBridge/config.json'


def approval_workflow_settings(config):
    """An operator-owned fixed process interface, never a task-selected command."""
    value = config.get('approval_workflow')
    if value is None:
        return None
    if not isinstance(value, dict) or not {'argv'} <= value.keys() <= {'argv', 'timeout_seconds'}:
        raise ValueError('invalid approval_workflow settings')
    argv = value['argv']
    if (not isinstance(argv, list) or len(argv) != 8 or
            any(not isinstance(item, str) or not item or '\0' in item for item in argv) or
            argv[1:3] != ['-I', '-B'] or argv[4] != '--state-dir' or argv[6] != '--policy' or
            re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?', Path(argv[0]).name) is None or
            Path(argv[3]).name != 'coordinator_approval_hook.py' or
            any(not Path(argv[index]).is_absolute() or '..' in Path(argv[index]).parts for index in (0, 3, 5, 7))):
        raise ValueError('approval_workflow needs the fixed coordinator hook argv')
    timeout = value.get('timeout_seconds', 2.2)
    if type(timeout) not in (int, float) or not 0.1 <= timeout <= 2.4:
        raise ValueError('approval_workflow timeout must be 0.1 through 2.4 seconds')
    return argv, timeout


def load_config(path=DEFAULT_CONFIG):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('bridge configuration schema_version must be 1')
    endpoint = urlsplit(value.get('endpoint', ''))
    if (endpoint.scheme != 'https' or not endpoint.netloc or endpoint.username or
            endpoint.password or endpoint.query or endpoint.fragment):
        raise ValueError('endpoint must be a credential-free HTTPS MCP URL')
    for key in ('source_id', 'consumer_id'):
        item = value.get(key)
        if not isinstance(item, str) or not item or len(item) > 128:
            raise ValueError(f'{key} must be a stable nonempty identifier')
    state = value.get('state_dir')
    roots = value.get('allowed_roots')
    if not isinstance(state, str) or not Path(state).is_absolute():
        raise ValueError('state_dir must be absolute')
    if (not isinstance(roots, list) or not roots or
            any(not isinstance(p, str) or not Path(p).is_absolute() for p in roots)):
        raise ValueError('allowed_roots must contain explicit absolute project paths')
    try:
        if approval_workflow_settings(value) is not None:
            info = Path(path).lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('approval_workflow configuration must be private and owned by this account')
    except (OSError, ValueError):
        # This opt-in process interface cannot take down the existing bridge.
        # Keep the on-disk candidate for reconciliation and disable only this
        # load's optional wiring, without exposing private validation details.
        value = dict(value)
        value.pop('approval_workflow', None)
        value['approval_workflow_error'] = 'OPTIONAL_APPROVAL_DISABLED'
    return value


def in_scope(config, cwd):
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        return False
    target = Path(cwd).resolve()
    return any(target.is_relative_to(Path(root).resolve()) for root in config['allowed_roots'])


def open_store(config):
    from bridge_store import BridgeStore
    state = Path(config['state_dir'])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    return BridgeStore(state / 'bridge.sqlite3', config['source_id'], config['consumer_id'])
