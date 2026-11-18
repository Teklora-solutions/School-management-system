"""Template context for the assistant warning banner.

The state is computed once per minute by ``alerts.refresh_page_state`` (from the
escalation middleware) and cached, so an ordinary page load costs no query.
"""

from __future__ import annotations

from . import alerts


def assistant_notices(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    role = getattr(user, "role", "")
    if role not in ("admin", "director") and not (user.is_superuser and not role):
        return {}

    state = getattr(request, "assistant_notice_state", None)
    if state is None:
        state = alerts.page_state()

    data: dict = {}
    is_admin = role == "admin" or (user.is_superuser and not role)
    if is_admin and not state.get("has_safeguarding_contact"):
        data["assistant_admin_warning"] = (
            "No safeguarding contact is set. The signed-in assistant and student tutor "
            "cannot be switched on until a named contact is saved."
        )

    open_count = state.get("open_alert_count", 0)
    if open_count:
        data["assistant_open_alert_count"] = open_count
    return data
