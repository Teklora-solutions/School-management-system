"""
Re-encrypt every stored provider key with the current (first) encryption key.

Rotation procedure: put the new key first in ASSISTANT_ENCRYPTION_KEY, keep the
old key after it, run this command with ``--apply``, then remove the old key.
Old ciphertext keeps decrypting until it has been re-saved, which is why the
key list is tried in order.

Never prints a key or any part of one. Every row is rewritten on each run
because Fernet uses a fresh random IV; that is expected, not an error.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.assistant import crypto
from apps.assistant.models import ProviderConfig


class Command(BaseCommand):
    help = "Re-encrypt saved provider keys with the first configured encryption key."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the new ciphertext. Without this flag the command only reports.",
        )

    def handle(self, *args, **options):
        if not crypto.is_configured():
            raise CommandError(
                "No ASSISTANT_ENCRYPTION_KEY is configured; nothing can be re-encrypted."
            )

        providers = list(ProviderConfig.objects.exclude(api_key_ciphertext=""))
        unreadable = 0
        rewritten = 0

        for provider in providers:
            try:
                plaintext = crypto.decrypt(provider.api_key_ciphertext)
            except crypto.DecryptionFailed:
                unreadable += 1
                self.stderr.write(
                    f"{provider.name}: cannot be read with any configured key "
                    "(the old key may already have been removed)."
                )
                continue

            if options["apply"]:
                provider.api_key_ciphertext = crypto.encrypt(plaintext)
                provider.save(update_fields=["api_key_ciphertext", "updated_at"])
                rewritten += 1

        if options["apply"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Re-encrypted {rewritten} of {len(providers)} provider key(s); "
                    f"{unreadable} could not be read."
                )
            )
        else:
            self.stdout.write(
                f"{len(providers)} provider key(s) would be re-encrypted; "
                f"{unreadable} cannot be read. Run again with --apply to write."
            )
