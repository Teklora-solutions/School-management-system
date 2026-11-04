#!/usr/bin/env python3
"""Prepare user-owned bind mounts and a root .env before Docker starts."""

import os
import re
import secrets
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
PLACEHOLDERS = {
    "SECRET_KEY": "GENERATE_DJANGO_SECRET",
    "POSTGRES_PASSWORD": "GENERATE_ADMIN_PASSWORD",
    "APP_DB_PASSWORD": "GENERATE_DATABASE_PASSWORD",
}


def assignments(content):
    """Keep raw dotenv values (including quoting); never evaluate or print them."""
    return {
        match[1]: match[2]
        for line in content.splitlines()
        if (match := ASSIGNMENT.match(line.strip()))
    }


def prepare_environment():
    env_path = ROOT / ".env"
    if env_path.is_symlink():
        raise SystemExit("Refusing to replace a symlink at .env.")
    original = env_path.read_text() if env_path.exists() else ""
    existing = assignments(original)
    example = (ROOT / ".env.example").read_text()
    defaults = assignments(example)

    # One-time compatibility with the earlier deployment layout. Import missing
    # values instead of generating new passwords for an already initialized DB.
    legacy_path = ROOT / "data/compose.env"
    legacy = assignments(legacy_path.read_text()) if legacy_path.is_file() else {}
    if original:
        additions = [f"{key}={legacy.get(key, value)}" for key, value in defaults.items() if key not in existing]
        content = original
        if additions:
            content = content.rstrip("\n") + "\n\n# Added by scripts/init_docker.py\n" + "\n".join(additions) + "\n"
    else:
        content = example
        for key, value in defaults.items():
            if key in legacy:
                content = content.replace(f"{key}={value}\n", f"{key}={legacy[key]}\n")

    lines = []
    for line in content.splitlines(keepends=True):
        match = ASSIGNMENT.match(line.strip())
        if match and match[1] in PLACEHOLDERS and match[2] == PLACEHOLDERS[match[1]]:
            line = f"{match[1]}={secrets.token_urlsafe(64)}\n"
        lines.append(line)
    content = "".join(lines)
    if content != original:
        # Atomic replacement, private permissions from the moment of creation.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=ROOT, prefix=".env.setup-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            temporary.replace(env_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print("Prepared root .env; existing values and database passwords were preserved.")
    else:
        print("Kept existing root .env unchanged.")
    env_path.chmod(0o600)


def main():
    if os.geteuid() == 0:
        raise SystemExit("Run this script as your normal user, NOT with sudo/root.")
    data = ROOT / "data"
    if data.exists() and (not data.is_dir() or data.stat().st_uid != os.getuid()):
        raise SystemExit("data/ must be a directory owned by your user. Fix only its top-level ownership first; do not recursively chown service data.")
    # This happens on the host, before Docker is invoked. Existing service-owned
    # subdirectories are intentionally not chowned or chmodded on repeated runs.
    data.mkdir(mode=0o700, exist_ok=True)
    for name in ("postgres", "redis", "media", "static", "backups", "secrets"):
        # PostgreSQL must traverse the mounted parent after dropping privileges;
        # its actual pgdata directory is separately restricted to its service UID.
        (data / name).mkdir(mode=0o755 if name in ("postgres", "media", "static") else 0o700, exist_ok=True)

    prepare_environment()
    token_path = data / "secrets/cloudflared-token"
    if not token_path.exists():
        token_path.touch(mode=0o644)
        token_path.chmod(0o644)  # Also support callers with a restrictive umask.
        print("Created empty data/secrets/cloudflared-token; paste your tunnel token there.")
    else:
        print("Kept existing cloudflared token file unchanged.")

    print("Set PUBLIC_HOSTNAME in .env, then run: docker compose up -d --build")
    print("No database was modified and no tunnel was connected.")


if __name__ == "__main__":
    main()
