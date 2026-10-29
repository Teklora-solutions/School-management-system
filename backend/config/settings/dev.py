"""
config/settings/dev.py
----------------------
Development overrides. Activates debug toolbar and verbose logging.
Set DJANGO_SETTINGS_MODULE=config.settings.dev
"""

from .base import *
from .base import INSTALLED_APPS, MIDDLEWARE

DEBUG = True

# Django Debug Toolbar
INSTALLED_APPS += ["debug_toolbar"]
MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware"] + MIDDLEWARE
INTERNAL_IPS = ["127.0.0.1"]

# Emails printed to console in dev
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Relax password validators in dev for easy seeding
AUTH_PASSWORD_VALIDATORS = []

# Show all SQL queries in tests
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {"level": "DEBUG", "handlers": ["console"]},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"]},
        "apps": {"level": "DEBUG", "handlers": ["console"], "propagate": False},
    },
}
