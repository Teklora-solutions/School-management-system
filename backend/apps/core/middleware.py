"""
apps/core/middleware.py
-----------------------
RequestIDMiddleware   — attaches a UUID request_id to every request.
RequestTimingMiddleware — structured JSON log of every request, warns on slow requests.
ForcePasswordChangeMiddleware — redirects users with must_change_password to the
password-change screen until they change it (covers admin password resets that
happen while the user already has a session).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid

logger = logging.getLogger("apps.core.middleware")

_local = threading.local()


def get_request_id() -> str:
    """Return the request ID for the current thread (empty string if none)."""
    return getattr(_local, "request_id", "")


class RequestIDMiddleware:
    """Attach a unique request ID to every request and store in thread-local."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = str(uuid.uuid4())
        request.request_id = request_id
        _local.request_id = request_id

        response = self.get_response(request)
        response["X-Request-ID"] = request_id

        # Clean up so thread-locals don't leak in long-lived worker threads
        _local.request_id = ""
        return response


class RequestTimingMiddleware:
    """
    Log every request as a structured JSON record.
    Requests above SLOW_REQUEST_THRESHOLD_MS are emitted at WARNING level;
    all others at DEBUG level.
    The `extra` dict is picked up by the JSON formatter in settings.
    """

    SLOW_REQUEST_THRESHOLD_MS: int = 500

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        user = getattr(request, "user", None)
        user_id = (
            getattr(user, "id", None)
            if user and getattr(user, "is_authenticated", False)
            else None
        )

        log_record = {
            "event": "http_request",
            "request_id": getattr(request, "request_id", ""),
            "user_id": user_id,
            "method": request.method,
            "path": request.path,
            "status_code": response.status_code,
            "elapsed_ms": round(elapsed_ms, 1),
        }

        if elapsed_ms > self.SLOW_REQUEST_THRESHOLD_MS:
            logger.warning("slow_request", extra=log_record)
        else:
            logger.debug("http_request", extra=log_record)

        return response


class ForcePasswordChangeMiddleware:
    """
    Redirect any authenticated user whose must_change_password flag is set
    to the forced password-change screen. The login view already sends them
    there; this catches users who were already logged in when an admin
    reset their password.
    """

    EXEMPT_PATHS = (
        "/change-password/",  # accounts app is mounted at root, not /accounts/
        "/logout/",
        "/login/",
        "/static/",
        "/media/",
        "/healthz/",
        "/readyz/",
        "/django-admin/",  # allow Django admin access
        "/accounts/",  # built-in Django auth views (password reset, etc.)
        "/__debug__/",  # debug toolbar
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
            and not request.path.startswith(self.EXEMPT_PATHS)
        ):
            from django.shortcuts import redirect

            return redirect("accounts:force_password_change")
        return self.get_response(request)
