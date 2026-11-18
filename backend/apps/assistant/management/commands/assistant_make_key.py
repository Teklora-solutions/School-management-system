"""
Print a fresh Fernet key for ASSISTANT_ENCRYPTION_KEY.

The key is printed to the terminal only. It is never written to the database,
a log or a file by this command; the operator puts it in the environment file.
"""

from __future__ import annotations

from cryptography.fernet import Fernet
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate a new encryption key for ASSISTANT_ENCRYPTION_KEY."

    def handle(self, *args, **options):
        self.stdout.write(Fernet.generate_key().decode())
