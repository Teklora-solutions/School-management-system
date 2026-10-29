"""
config/settings/prod.py
-----------------------
Production overrides. All secrets come from environment variables.
Set DJANGO_SETTINGS_MODULE=config.settings.prod
"""

from .base import *
from .base import LOGGING, env  # noqa: F401

DEBUG = False

# Must be set in production environment
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

# ── Database (PostgreSQL required in production) ──────────────────────────────
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DATABASES["default"]["CONN_MAX_AGE"] = 60  # persistent connections
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True  # discard stale connections

# ── Caching ───────────────────────────────────────────────────────────────────
# Use Redis in production so all Gunicorn workers share the KPI cache.
# Example: CACHE_URL=redis://localhost:6379/1
# Falls back to locmem (in-process) if CACHE_URL is not set.

# ── Security ──────────────────────────────────────────────────────────────────
SECURE_SSL_REDIRECT = True
# Required when Django is behind a TLS-terminating reverse proxy (nginx/load-balancer).
# Without this, SECURE_SSL_REDIRECT loops infinitely on HTTP → HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
# Required in Django 4+ for HTTPS domains — must match the actual domain(s).
# Set via environment: CSRF_TRUSTED_ORIGINS=https://school.example.com
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

SECURE_HSTS_SECONDS = 31_536_000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"

# ── Logging — switch to JSON in production ────────────────────────────────────
import os as _os

_os.environ.setdefault("LOG_FORMAT", "json")

# ── Sentry error tracking ─────────────────────────────────────────────────────
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import logging as _logging

    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(
                transaction_style="url",
                middleware_spans=True,
                signals_spans=False,
            ),
            # Capture ERROR+ log records as Sentry events automatically
            LoggingIntegration(
                level=_logging.INFO,  # breadcrumb level
                event_level=_logging.ERROR,  # send as Sentry event at ERROR+
            ),
        ],
        # Performance tracing: 10% of requests
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.1),
        # Never send PII (passwords, email addresses in request bodies)
        send_default_pii=False,
        # Tag every event with the app release (set via CI: SENTRY_RELEASE=git-sha)
        release=env("SENTRY_RELEASE", default=""),
        environment="production",
    )
