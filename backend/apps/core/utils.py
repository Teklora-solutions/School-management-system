"""
apps/core/utils.py
------------------
Small utility functions shared across the project.
"""

from __future__ import annotations

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render


def axes_lockout_response(request, credentials, *args, **kwargs) -> HttpResponse:
    """
    Called by django-axes when a client is locked out.

    A browser posting a login form gets a readable page: replacing the form with
    a bare JSON 403 is not something a head teacher can act on. Programmatic
    callers still get structured JSON.
    """
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
        or request.content_type.startswith("application/json")
    )
    if wants_json:
        return JsonResponse(
            {"error": "Too many failed login attempts. Please try again in 1 hour."},
            status=403,
        )
    return render(request, "auth/lockout.html", status=403)


def get_client_ip(request) -> str | None:
    """Extract the real client IP, accounting for reverse proxies."""
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
