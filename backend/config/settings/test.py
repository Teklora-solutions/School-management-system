"""
config/settings/test.py
------------------------
Settings overrides for the automated test suite.

Key changes from dev.py:
  - StaticFilesStorage (no manifest) so template rendering works without
    running collectstatic.
  - LocMemCache so cache tests are fast and isolated.
  - Disable axes rate-limiting so login tests aren't throttled.
  - Silence django-q and debug_toolbar.
  - Use in-memory SQLite (TEST block) via pytest-django.
"""

from .dev import *
from .dev import INSTALLED_APPS, MIDDLEWARE

# ── Static files ──────────────────────────────────────────────────────────────
# Bypass whitenoise's ManifestStaticFilesStorage — no collectstatic needed.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# ── Cache — fresh in-memory per process ───────────────────────────────────────
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-school-mgmt",
    }
}

# ── Axes — disable lockouts during tests ─────────────────────────────────────
AXES_ENABLED = False

# ── Password hashing — MD5 is fast enough for tests ──────────────────────────
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# ── Silence unnecessary logging noise ────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"]},
}

# ── Remove debug_toolbar from test middleware (it adds queries) ───────────────
MIDDLEWARE = [m for m in MIDDLEWARE if "debug_toolbar" not in m]
INSTALLED_APPS = [a for a in INSTALLED_APPS if a != "debug_toolbar"]

# ── Director KPI cache TTL — keep short so invalidation tests are fast ────────
DIRECTOR_KPI_CACHE_TTL = 60
