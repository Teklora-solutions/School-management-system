"""
apps/assistant/middleware.py
----------------------------
Checks overdue safeguarding alerts whenever an admin or director loads a page,
and prepares the banner state from the same two queries.

The background worker is not trusted with this. If the worker is down, the
dashboard check still escalates — and it runs at most once a minute, so the
banner costs an ordinary page load no query at all.
"""

from __future__ import annotations

import logging

from django.core.cache import cache

log = logging.getLogger(__name__)

CHECK_KEY = "assistant:escalation-check"
CHECK_INTERVAL = 60


class EscalationCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        role = getattr(user, "role", "") if user is not None else ""
        acts_as_admin = user is not None and user.is_authenticated and (
            role in ("admin", "director") or (user.is_superuser and not role)
        )
        if acts_as_admin:
            from . import alerts

            if cache.add(CHECK_KEY, 1, CHECK_INTERVAL):
                try:
                    request.assistant_notice_state = alerts.refresh_page_state()
                except Exception:  # never break a page over the check
                    log.exception("Assistant safeguarding escalation check failed")
            else:
                request.assistant_notice_state = cache.get(alerts.PAGE_STATE_KEY)
        return self.get_response(request)
