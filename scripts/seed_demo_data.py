"""
seed_demo_data.py
-----------------
Thin wrapper around the ``seed_demo_data`` management command so the README's
``python ../scripts/seed_demo_data.py`` works from anywhere, without having to
activate the project first.

    python scripts/seed_demo_data.py [--dry-run] [--force]

Run ``python manage.py seed_demo_data --help`` from backend/ for the same
options.  The seed is idempotent; to start over, delete backend/db.sqlite3 and
re-run ``manage.py migrate`` followed by this script.
"""

from __future__ import annotations

import os
import pathlib
import sys

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django
from django.core.management import call_command
from django.core.management.base import CommandError


def main() -> int:
    django.setup()
    try:
        call_command("seed_demo_data", *sys.argv[1:])
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
