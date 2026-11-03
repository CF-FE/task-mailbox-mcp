"""Prepare or rotate a probe-only key without printing it or changing business grants."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile

if __package__:
    from .coordinator_port import digest, load_policy, private_parent, token
else:
    from coordinator_port import digest, load_policy, private_parent, token


def prepare(policy_path, socket_path, key_path, key_id, retire=None):
    policy_path, socket_path, key_path = map(Path, (policy_path, socket_path, key_path))
    paths = (policy_path, socket_path, key_path)
    if len(set(paths)) != 3 or not token(key_id) or not key_id.startswith("probe-"):
        raise ValueError("choose distinct private paths and a dedicated probe- key id")
    if retire is not None and (not token(retire) or not retire.startswith("probe-") or retire == key_id):
        raise ValueError("retire only a different probe key after testing its replacement")
    for path in paths:
        private_parent(path)
    lock_path = policy_path.with_name(policy_path.name + ".lock")
    if lock_path in paths:
        raise ValueError("conflicting lock path")
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600), "rb") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("lock is not an owned private file")
        fcntl.flock(lock, fcntl.LOCK_EX)
        if os.path.lexists(policy_path):
            path, keys, grants = load_policy(policy_path)
            if path != socket_path:
                raise ValueError("existing socket address differs")
        else:
            keys, grants = {}, {}
        if os.path.lexists(key_path):
            fd = os.open(key_path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as f:
                info = os.fstat(f.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise ValueError("client key must be an owned private file")
                secret = f.read(66).decode("ascii").removesuffix("\n")
            if not digest(secret):
                raise ValueError("invalid existing client key")
        else:
            if key_id in keys or retire is not None:
                raise ValueError("existing key was not found; provision a fresh id and verify it before retirement")
            secret = secrets.token_hex(32)
            with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as f:
                f.write(secret + "\n")
                f.flush()
                os.fsync(f.fileno())
        if key_id in keys and keys[key_id] != secret or any(k != key_id and value == secret for k, value in keys.items()):
            raise ValueError("key conflict")
        if retire is not None:
            if key_id not in keys or retire not in keys:
                raise ValueError("registered and verified replacement and retained previous key required")
            del keys[retire]
        keys[key_id] = secret
        policy = {"v": 1, "socket_path": str(socket_path), "keys": keys, "grants": list(grants.values())}
        fd, name = tempfile.mkstemp(dir=policy_path.parent, prefix=".prepare-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(policy, f, allow_nan=False, separators=(",", ":"), ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            load_policy(Path(name))
            os.replace(name, policy_path)
        finally:
            Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--retire-probe")
    args = parser.parse_args()
    try:
        prepare(args.policy, args.socket, args.client_key, args.key_id, args.retire_probe)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        parser.exit(2, "Refused: verify private paths, key identity and the existing policy.\n")
    print('{"prepared":true,"scope":"LOCAL_PROBE"}')
