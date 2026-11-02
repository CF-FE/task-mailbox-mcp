"""Private local bridge configuration; no credential or transcript loading."""

import json
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_CONFIG = Path.home() / 'Library/Application Support/TaskMailbox/config.json'


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
