"""Production configuration must work in web, worker and compose-exec processes."""

import os
import subprocess
import sys

from django.test import SimpleTestCase


class ProductionSettingsTests(SimpleTestCase):
    def test_separate_database_password_and_persistent_paths(self):
        environment = dict(
            os.environ,
            SECRET_KEY="deployment-settings-test-only",
            DATABASE_URL="postgres://school@db:5432/school",
            APP_DB_PASSWORD="raw@password:with/reserved#characters",
            ALLOWED_HOSTS="school.example.com",
            CSRF_TRUSTED_ORIGINS="https://school.example.com",
            MEDIA_ROOT="/data/media",
            STATIC_ROOT="/data/static",
        )
        result = subprocess.run(
            [sys.executable, "-c", """
import os
from pathlib import Path
from config.settings import prod
assert prod.DATABASES['default']['PASSWORD'] == os.environ['APP_DB_PASSWORD']
assert prod.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql'
assert prod.MEDIA_ROOT == Path('/data/media')
assert prod.STATIC_ROOT == Path('/data/static')
assert prod.SECURE_PROXY_SSL_HEADER == ('HTTP_X_FORWARDED_PROTO', 'https')
assert prod.CSRF_TRUSTED_ORIGINS == ['https://school.example.com']
assert not prod.DEBUG
"""],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
