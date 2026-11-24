"""Enforce must_change_password across active authenticated sessions."""

from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse


class ForcePasswordChangeMiddleware:
    """Redirect any signed-in user with ``must_change_password=True`` to change it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and getattr(user, "must_change_password", False):
            path = request.path
            static_url = getattr(settings, "STATIC_URL", "/static/")
            media_url = getattr(settings, "MEDIA_URL", "/media/")
            if not (path.startswith(static_url) or path.startswith(media_url)):
                allowed = set()
                for name in (
                    "accounts:force_password_change",
                    "logout",
                    "liveness",
                    "readiness",
                ):
                    try:
                        allowed.add(reverse(name))
                    except NoReverseMatch:
                        pass
                if path not in allowed:
                    return redirect("accounts:force_password_change")
        return self.get_response(request)
