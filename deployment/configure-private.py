#!/usr/bin/env python3
"""Interactive host-only configuration. Never send OAuth secrets through chat."""

import getpass
import os
from pathlib import Path
import re
import secrets
import stat
from urllib.parse import urlsplit


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run as root on the deployment host.")
    path = Path("/etc/task-mailbox/service.env")
    if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.stat().st_mode)):
        raise SystemExit("The private configuration must be a regular file.")
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                values[name] = value
    base = input("Public HTTPS base URL, including /mailbox but without /mcp: ").strip().rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != "/mailbox"):
        raise SystemExit("Use https://<your-public-host>/mailbox.")
    login = input("Permitted GitHub login: ").strip()
    if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login) is None:
        raise SystemExit("Invalid GitHub login.")
    print("Create a dedicated GitHub OAuth application with this callback:", base + "/auth/callback")
    print("Use the original browser sign-in flow; do not paste credentials into chat.")
    client_id = input("Dedicated GitHub OAuth application client ID: ").strip()
    client_secret = getpass.getpass("Dedicated GitHub OAuth application client secret: ").strip()
    if not client_id or not client_secret or any("\n" in value or "\r" in value
                                                for value in (client_id, client_secret)):
        raise SystemExit("Both OAuth application fields are required.")
    signing = values.get("TASK_MAILBOX_SIGNING_KEY", "")
    if not signing or signing.startswith("REPLACE_"):
        signing = secrets.token_urlsafe(48)
    values.update({
        "TASK_MAILBOX_PUBLIC_BASE_URL": base,
        "TASK_MAILBOX_GITHUB_CLIENT_ID": client_id,
        "TASK_MAILBOX_GITHUB_CLIENT_SECRET": client_secret,
        "TASK_MAILBOX_ALLOWED_GITHUB_USER": login,
        "TASK_MAILBOX_SIGNING_KEY": signing,
        "TASK_MAILBOX_DATA_DIR": "/var/lib/task-mailbox",
        "FASTMCP_HOME": "/var/lib/task-mailbox/oauth",
        "TASK_MAILBOX_PORT": "8766",
        "TASK_MAILBOX_MAX_PAYLOAD_BYTES": "1048576",
        "TASK_MAILBOX_MAX_DATABASE_BYTES": "268435456",
        "TASK_MAILBOX_MAX_JOBS": "10000",
        "TASK_MAILBOX_MAX_OAUTH_BYTES": "8388608",
        "TASK_MAILBOX_MAX_OAUTH_FILES": "512",
    })
    lines = []
    for name, value in values.items():
        if not name.startswith(("TASK_MAILBOX_", "FASTMCP_")):
            continue
        encoded = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{name}="{encoded}"')
    os.umask(0o077)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".env.new")
    with temporary.open("x") as output:
        output.write("\n".join(lines) + "\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    path.chmod(0o600)
    print("Private configuration saved. No service or public route has been started.")


if __name__ == "__main__":
    main()
