"""
apps/assistant/tests/test_key_scan.py
-------------------------------------
A guard against a real key ever being committed. Scans the files this module
owns (assistant code, production settings, assistant docs and the env example)
for key-shaped strings and for a non-empty ASSISTANT_ENCRYPTION_KEY.

Test files are excluded because they deliberately contain obvious fake keys;
the fake providers never contact anything.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

REPO = Path(__file__).resolve().parents[4]

KEY_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
)

# A literal Fernet key: 32 bytes url-safe base64 with padding.
FERNET_LITERAL = re.compile(r"^[A-Za-z0-9_-]{43}=$")

SCAN_TARGETS = [
    REPO / "backend" / "apps" / "assistant",
    REPO / "backend" / "config" / "settings" / "base.py",
    REPO / "backend" / "config" / "settings" / "dev.py",
    REPO / "backend" / "config" / "settings" / "prod.py",
    REPO / "backend" / "config" / "settings" / "test.py",
    REPO / "docs" / "ai-assistant",
    REPO / ".env.example",
]

SKIP_PARTS = {"__pycache__", "tests", "migrations"}


def iter_files(target: Path):
    if target.is_file():
        yield target
        return
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        if SKIP_PARTS.intersection(path.parts):
            continue
        if path.suffix.lower() in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".db", ".sqlite3"}:
            continue
        yield path


class KeyScanTests(SimpleTestCase):
    def test_no_key_shaped_strings_in_assistant_owned_files(self):
        offenders = []
        for target in SCAN_TARGETS:
            if not target.exists():
                continue
            for path in iter_files(target):
                text = path.read_text(encoding="utf-8", errors="ignore")
                for pattern in KEY_PATTERNS:
                    for match in pattern.finditer(text):
                        offenders.append(f"{path}: {match.group(0)[:8]}…")
        self.assertEqual(offenders, [], "A key-shaped string was found in tracked files.")

    def test_no_non_empty_encryption_key_is_committed(self):
        offenders = []
        for target in SCAN_TARGETS:
            if not target.exists():
                continue
            for path in iter_files(target):
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if "ASSISTANT_ENCRYPTION_KEY" not in line:
                        continue
                    if "=" not in line:
                        continue
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if FERNET_LITERAL.match(value):
                        offenders.append(f"{path}: {line.strip()[:60]}")
        self.assertEqual(
            offenders, [], "An encryption key value is present in a committed file."
        )

    def test_the_test_key_is_generated_and_never_committed(self):
        # Proof that the suite runs on a throwaway key generated in memory.
        from cryptography.fernet import Fernet

        Fernet(settings.ASSISTANT_ENCRYPTION_KEY.encode())
        self.assertNotIn(
            settings.ASSISTANT_ENCRYPTION_KEY,
            "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for target in SCAN_TARGETS
                if target.exists()
                for path in iter_files(target)
            ),
        )
