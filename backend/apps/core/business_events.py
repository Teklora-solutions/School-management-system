"""
apps/core/business_events.py
-----------------------------
Structured business-event logging.

Every significant domain action emits a JSON log record to the
"apps.events" logger at INFO level.  The log is separate from
the Django request/response log so it can be routed to a
dedicated sink (file, ELK, CloudWatch, etc.) in production.

Usage::

    from apps.core.business_events import log_event

    log_event(
        event="payment_posted",
        actor_id=request.user.id,
        data={"amount": str(payment.amount), "reference": payment.reference},
        request_id=getattr(request, "request_id", ""),
    )

Callers
-------
Prefer ``apps.core.events.record_event()``, which emits this record and stores
a ``SystemEvent`` row for the admin activity feed. It is the only caller today;
add calls anywhere a business-critical state change occurs.
"""

from __future__ import annotations

import logging
from typing import Any

_event_logger = logging.getLogger("apps.events")


def log_event(
    *,
    event: str,
    actor_id: int | None = None,
    data: dict[str, Any] | None = None,
    request_id: str = "",
) -> None:
    """
    Emit a structured business-event log record.

    Parameters
    ----------
    event       Short snake_case name, e.g. ``"payment_posted"``.
    actor_id    PK of the user who triggered the event (None for system events).
    data        Arbitrary context dict (amounts, IDs, status labels, etc.).
    request_id  From ``request.request_id``; empty string if unavailable.
    """
    _event_logger.info(
        event,
        extra={
            "event": event,
            "actor_id": actor_id,
            "request_id": request_id,
            "data": data or {},
        },
    )
