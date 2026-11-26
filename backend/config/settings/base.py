"""
config/settings/base.py
-----------------------
Settings shared across ALL environments (dev, prod).
Environment-specific overrides live in dev.py / prod.py.
Never import this file directly — import dev.py or prod.py.
"""

from pathlib import Path

import environ

# ─── Paths ────────────────────────────────────────────────────────────────────
# BASE_DIR = .../backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Project root = .../school-management-system/
PROJECT_ROOT = BASE_DIR.parent

# Frontend lives outside the Django package
FRONTEND_DIR = PROJECT_ROOT / "frontend"

# ─── Environment ──────────────────────────────────────────────────────────────
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DATABASE_URL=(str, f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
    CACHE_URL=(str, "locmemcache://"),
    SENTRY_DSN=(str, ""),
    SCHOOL_NAME=(str, "My School"),
    SCHOOL_MOTTO=(str, "Excellence in Education"),
    SCHOOL_PHONE=(str, ""),
    SCHOOL_EMAIL=(str, ""),
    SECURE_SSL_REDIRECT=(bool, False),
    SESSION_COOKIE_SECURE=(bool, False),
    CSRF_COOKIE_SECURE=(bool, False),
)

# Read the .env file (silently skipped if absent — prod injects real env vars)
environ.Env.read_env(PROJECT_ROOT / ".env")

# ─── Core Django ──────────────────────────────────────────────────────────────
SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ─── Application definition ───────────────────────────────────────────────────
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "django_q",
    "axes",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.academics",
    "apps.students",
    "apps.teachers",
    "apps.results",
    "apps.finance",
    "apps.homework",
    "apps.gradebook",
    "apps.learning",
    "apps.attendance",
    "apps.communications",
    "apps.dashboards",
    "apps.public",
    "apps.community",
    "apps.assistant",
    "apps.api",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ─── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.ForcePasswordChangeMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "apps.core.middleware.RequestTimingMiddleware",
    "apps.assistant.middleware.EscalationCheckMiddleware",
]

ROOT_URLCONF = "config.urls"

# ─── Templates ────────────────────────────────────────────────────────────────
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [FRONTEND_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.base_template",
                "apps.core.context_processors.school_settings",
                "apps.core.context_processors.navigation",
                "apps.core.context_processors.parent_children",
                "apps.core.context_processors.unread_count",
                "apps.core.context_processors.unread_notifications",
                "apps.assistant.context_processors.assistant_notices",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ─── Database ─────────────────────────────────────────────────────────────────
_db_url = env("DATABASE_URL")
_db_default = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
DATABASES = {"default": env.db_url_config(_db_url if _db_url else _db_default)}
DATABASES["default"]["ATOMIC_REQUESTS"] = True  # wrap every request in a transaction

# ─── Auth ─────────────────────────────────────────────────────────────────────
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard_redirect"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "apps.accounts.backends.EmailOrAdmissionBackend",
]

# ─── Cache ────────────────────────────────────────────────────────────────────
_cache_url = env("CACHE_URL")
if _cache_url:
    CACHES = {"default": env.cache_url_config(_cache_url)}
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "school-mgmt",
        }
    }

# ─── Internationalisation ─────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"  # EAT (UTC+3) — Kenya. Day boundaries for streaks depend on this.
USE_I18N = True
USE_TZ = True

# ─── Static & Media ───────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATICFILES_DIRS = [FRONTEND_DIR / "static"]
STATIC_ROOT = Path(env("STATIC_ROOT", default=str(BASE_DIR / "staticfiles")))

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))

# Class-feed photos and files live outside MEDIA_ROOT so the dev-only
# ``/media/`` route can never serve them. Every read goes through a
# permission-checked view in apps.learning.views.
FEED_MEDIA_ROOT = Path(
    env("FEED_MEDIA_ROOT", default=str(BASE_DIR / "private_feed"))
)

# Knowledge documents (the assistant's source files) live outside MEDIA_ROOT
# too. Every read goes through the admin download view in
# apps.assistant.knowledge; url() raises so no template can print a direct link.
KNOWLEDGE_MEDIA_ROOT = Path(
    env("KNOWLEDGE_MEDIA_ROOT", default=str(BASE_DIR / "private_knowledge"))
)

# ─── AI Assistant ─────────────────────────────────────────────────────────────
# One or more Fernet keys, comma-separated, newest first. The first key encrypts;
# every key is tried when decrypting, which is how rotation works. See
# docs/ai-assistant/security-and-privacy.md. Empty means no provider key can be
# saved until an operator sets it.
ASSISTANT_ENCRYPTION_KEY = env("ASSISTANT_ENCRYPTION_KEY", default="")

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# ─── Default primary key ──────────────────────────────────────────────────────
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── Email ────────────────────────────────────────────────────────────────────
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@school.edu")

# ─── School identity (available in all templates via context processor) ────────
SCHOOL_NAME = env("SCHOOL_NAME")
SCHOOL_MOTTO = env("SCHOOL_MOTTO")
SCHOOL_PHONE = env("SCHOOL_PHONE")
SCHOOL_EMAIL = env("SCHOOL_EMAIL")
SCHOOL_CURRENCY = env("SCHOOL_CURRENCY", default="GHS")  # ISO 4217 currency code

# ─── Django REST Framework ────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.api.authentication.APIKeyAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "api": "1000/hour",
        "webhooks": "200/minute",
        "auth": "20/minute",
    },
    "EXCEPTION_HANDLER": "apps.api.exceptions.api_exception_handler",
}

# ─── SimpleJWT ────────────────────────────────────────────────────────────────
from datetime import timedelta  # noqa: E402

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# ─── DRF Spectacular (OpenAPI schema) ─────────────────────────────────────────
SPECTACULAR_SETTINGS = {
    "TITLE": env("SCHOOL_NAME", default="School Management System") + " API",
    "DESCRIPTION": (
        "REST API for external integrations. "
        "Authenticate with an API key (Authorization: Token sk_…) "
        "or a JWT (Authorization: Bearer …)."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1/",
}

# ─── Django-Q2 (background tasks via ORM broker) ─────────────────────────────
Q_CLUSTER = {
    "name": "school_management",
    "workers": 2,
    "timeout": 300,
    "retry": 360,
    "queue_limit": 50,
    "bulk": 10,
    "orm": "default",
    "catch_up": False,
}

# ─── Axes (login rate limiting) ───────────────────────────────────────────────
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # 1 hour
AXES_LOCKOUT_CALLABLE = "apps.core.utils.axes_lockout_response"
AXES_RESET_ON_SUCCESS = True
# Axes defaults to locking out by client IP alone. With one shared login form
# that was tolerable; now every portal is a page on the same address, and a
# single staff room or family NAT would lock the whole building out of every
# portal for an hour because one person mistyped five times. Keying on the
# credential *and* the address confines a lockout to the account being guessed.
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]

# ─── Logging (structured JSON via python-json-logger) ─────────────────────────
# In production all log records are JSON objects so they can be ingested by
# ELK / CloudWatch / Loki without a parsing step.
# In development the "verbose" formatter is used instead for human readability.
# Switch formatters by setting LOG_FORMAT=json in the environment.
import os as _os

_LOG_FORMAT = _os.environ.get("LOG_FORMAT", "verbose")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
            "rename_fields": {
                "levelname": "level",
                "asctime": "timestamp",
                "name": "logger",
            },
        },
        "verbose": {
            "format": "[%(asctime)s] %(levelname)-8s %(name)s | rid=%(request_id)s uid=%(user_id)s | %(message)s",
            "defaults": {"request_id": "", "user_id": ""},
        },
    },
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": _LOG_FORMAT,
        },
        "console_errors": {
            "class": "logging.StreamHandler",
            "formatter": _LOG_FORMAT,
            "level": "ERROR",
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        # Business-events stream — route to a dedicated sink in production
        "apps.events": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        # Suppress noisy DB query logging unless explicitly enabled
        "django.db.backends": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

# ─── Security headers (overridden in prod.py) ─────────────────────────────────
SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT")
SESSION_COOKIE_SECURE = env("SESSION_COOKIE_SECURE")
CSRF_COOKIE_SECURE = env("CSRF_COOKIE_SECURE")
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
