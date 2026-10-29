"""
apps/core/audit_log.py
-----------------------
Helper to write immutable audit log entries.
Import and call `log_action()` from any service that needs auditing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .middleware import get_request_id

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.accounts.models import User


def log_action(
    *,
    actor: User | None,
    action: str,
    content_type: str,
    object_id: str | int = "",
    change_summary: dict[str, Any] | None = None,
    request: HttpRequest | None = None,
) -> None:
    """
    Write an immutable audit log entry.

    Args:
        actor:          The user performing the action (None for system actions).
        action:         Short verb string, e.g. "result.confirmed", "fee.reversed".
        content_type:   Model label, e.g. "results.Result".
        object_id:      PK of the affected object.
        change_summary: Dict describing before/after values.
        request:        Optional request for IP extraction.
    """
    # Import here to avoid circular dependency at module load time
    from .models import AuditLog

    ip = None
    if request is not None:
        x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded:
            ip = x_forwarded.split(",")[0].strip()
        else:
            ip = request.META.get("REMOTE_ADDR")

    AuditLog.objects.create(
        actor=actor,
        action=action,
        content_type=content_type,
        object_id=str(object_id),
        change_summary=change_summary or {},
        request_id=get_request_id(),
        ip_address=ip,
    )
