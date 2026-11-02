#!/usr/bin/env bash
# Run from a reviewed checkout on the target host. Does not start or expose it.
set -euo pipefail
if [[ "$(id -u)" != 0 ]]; then
    echo 'Run as root on the deployment host.' >&2
    exit 1
fi
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
account="$(getent passwd task-mailbox || true)"
if [[ -z "$account" ]]; then
    useradd --system --home-dir /var/lib/task-mailbox --shell /usr/sbin/nologin task-mailbox
else
    IFS=: read -r _ _ service_uid _ _ service_home service_shell <<< "$account"
    if [[ "$service_uid" == 0 || "$service_home" != /var/lib/task-mailbox || "$service_shell" != /usr/sbin/nologin ]]; then
        echo 'Existing task-mailbox account does not match this dedicated service. Inspect before proceeding.' >&2
        exit 1
    fi
fi
install -d -m 0755 /opt/task-mailbox
install -d -m 0700 /etc/task-mailbox
install -d -m 0700 -o task-mailbox -g task-mailbox /var/lib/task-mailbox
install -m 0644 "$source_dir/server.py" "$source_dir/mailbox_store.py" "$source_dir/oauth_storage.py" \
    "$source_dir/requirements.txt" "$source_dir/requirements.lock.txt" /opt/task-mailbox/
install -m 0644 "$source_dir/deployment/configure-private.py" /opt/task-mailbox/configure-private.py
if [[ ! -x /opt/task-mailbox/.venv/bin/python ]]; then
    python3 -m venv /opt/task-mailbox/.venv
fi
/opt/task-mailbox/.venv/bin/python -m pip install --no-cache-dir --disable-pip-version-check \
    -r /opt/task-mailbox/requirements.lock.txt
if [[ ! -e /etc/task-mailbox/service.env ]]; then
    install -m 0600 "$source_dir/.env.example" /etc/task-mailbox/service.env
    python3 - <<'PY'
from pathlib import Path
import secrets
path = Path('/etc/task-mailbox/service.env')
text = path.read_text()
text = text.replace('REPLACE_INDEPENDENT_RANDOM_SIGNING_KEY', secrets.token_urlsafe(48))
path.write_text(text)
path.chmod(0o600)
PY
fi
install -m 0644 "$source_dir/deployment/task-mailbox.service" /etc/systemd/system/
systemctl daemon-reload
echo 'Prepared. Complete private OAuth configuration, test, then explicitly start this service.'
