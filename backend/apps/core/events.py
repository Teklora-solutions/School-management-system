"""
apps/core/events.py
-------------------
The activity registry and the only write path for ``core.SystemEvent``.

``apps.core.business_events.log_event`` already emitted a JSON record for every
business-critical action, but nothing could read those records back: they lived
only in the log sink. This module keeps that sink working *and* stores the same
event so the admin live feed and daily report can render it.

Usage::

    from apps.core.events import record_event

    record_event(
        event_type="finance.payment_posted",
        actor=recorded_by,
        student=student,
        data={"amount": str(amount), "receipt_number": payment.receipt_number},
        request=request,
    )

Registry
--------
``EVENT_TYPES`` maps a dotted event type — the same ``app.action`` spelling
``AuditLog.action`` uses — to the label, category and sentence the feed shows.
``SystemEvent.type`` has no model ``choices``, so adding an entry here is a
one-file change; an unregistered type still records, under category "other",
with its type string as the wording.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db import transaction

from . import business_events
from .models import SystemEvent

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.accounts.models import User
    from apps.students.models import Student

logger = logging.getLogger(__name__)

#: Category for an event type that is not in the registry.
UNKNOWN_CATEGORY = "other"

#: Category slug → label, for the feed filter and the daily report headings.
CATEGORIES: dict[str, str] = {
    "attendance": "Attendance",
    "finance": "Finance",
    "results": "Results",
    UNKNOWN_CATEGORY: "Other",
}

EVENT_TYPES: dict[str, dict[str, str]] = {
    "attendance.register_recorded": {
        "label": "Attendance recorded",
        "category": "attendance",
        "message": "Attendance for {class} on {date}: {present}/{total} present",
    },
    "finance.approval_decided": {
        "label": "Approval decided",
        "category": "finance",
        "message": "{kind} ask {decision} ({amount})",
    },
    "finance.approval_requested": {
        "label": "Approval asked",
        "category": "finance",
        "message": "Second signature asked for a {kind} ({amount})",
    },
    "finance.arrears_carried_forward": {
        "label": "Arrears carried forward",
        "category": "finance",
        "message": "{pupils_owing} pupil(s) carrying {total} after the move",
    },
    "finance.budget_set": {
        "label": "Budget set",
        "category": "finance",
        "message": "{account} budget for {term}: {amount}",
    },
    "finance.credit_applied": {
        "label": "Credit applied",
        "category": "finance",
        "message": "{amount} of held credit put against invoice {invoice}",
    },
    "finance.discount_rule_created": {
        "label": "Discount rule created",
        "category": "finance",
        "message": "Recurring reduction {name} published",
    },
    "finance.expense_posted": {
        "label": "Expense claim paid",
        "category": "finance",
        "message": "{reference} posted: {amount} to {payee}",
    },
    "finance.expense_requested": {
        "label": "Expense claim queued",
        "category": "finance",
        "message": "Claim of {amount} for {payee} awaiting approval",
    },
    "finance.fee_election_set": {
        "label": "Optional fee item set",
        "category": "finance",
        "message": "{item} {state} for {student_admission}",
    },
    "finance.fee_structure_copied": {
        "label": "Fee structure copied",
        "category": "finance",
        "message": "{from} copied to {to} ({class})",
    },
    "finance.invoices_posted": {
        "label": "Invoices posted",
        "category": "finance",
        "message": "{count} invoice(s) posted for {term} ({class})",
    },
    "finance.payment_plan_created": {
        "label": "Payment plan agreed",
        "category": "finance",
        "message": "Plan of {installments} installment(s) for {total} agreed",
    },
    "finance.payment_plan_withdrawn": {
        "label": "Payment plan withdrawn",
        "category": "finance",
        "message": "Plan for {term} called off: {reason}",
    },
    "finance.payment_posted": {
        "label": "Payment recorded",
        "category": "finance",
        "message": "Payment {receipt_number}: {amount} from {student_admission}",
    },
    "finance.payment_reversed": {
        "label": "Payment reversed",
        "category": "finance",
        "message": "Payment {original_receipt} reversed ({amount})",
    },
    "finance.period_closed": {
        "label": "Period closed",
        "category": "finance",
        "message": "{kind} period {start} to {end} closed",
    },
    "finance.period_reopened": {
        "label": "Period reopened",
        "category": "finance",
        "message": "{kind} period {start} to {end} reopened",
    },
    "finance.write_off_requested": {
        "label": "Write-off queued",
        "category": "finance",
        "message": "{amount} write-off for {student_admission} awaiting approval",
    },
    "results.published": {
        "label": "Results published",
        "category": "results",
        "message": "Results published - {subject} for {class}",
        # The JSON sink has always called this event "results_published";
        # deriving it from the type string would rename it.
        "sink": "results_published",
    },
}

_MESSAGE_MAX_LENGTH = 255


class _MissingAsDash(dict):
    """Format mapping that renders an absent key as an em dash, never raises."""

    def __missing__(self, key: str) -> str:
        return "—"


def event_spec(event_type: str) -> dict[str, str]:
    """Registry entry for ``event_type``, with safe defaults if unregistered."""
    spec = EVENT_TYPES.get(event_type)
    if spec:
        return spec
    return {"label": event_type, "category": UNKNOWN_CATEGORY, "message": ""}


def render_message(event_type: str, data: dict[str, Any]) -> str:
    """
    The sentence the feed shows for this event.

    A missing ``data`` key renders as an em dash rather than dropping the whole
    line, and the result is clipped to the column width.
    """
    spec = event_spec(event_type)
    template = spec.get("message", "")
    if not template:
        return _clip(spec["label"])
    return _clip(template.format_map(_MissingAsDash(data)))


def _clip(text: str) -> str:
    return text[:_MESSAGE_MAX_LENGTH]


def record_event(
    *,
    event_type: str,
    actor: User | None = None,
    student: Student | None = None,
    data: dict[str, Any] | None = None,
    request: HttpRequest | None = None,
    message: str = "",
) -> SystemEvent | None:
    """
    Emit one business event: the structured log record, then the feed row.

    Never raises. Every caller sits inside an ``@transaction.atomic`` business
    write, and losing a feed entry is not a reason to roll that write back.
    Returns the row, or ``None`` when it could not be stored.
    """
    payload = dict(data or {})
    spec = event_spec(event_type)
    request_id = getattr(request, "request_id", "") if request else ""

    # The log sink first: it still receives the event when the write below fails.
    business_events.log_event(
        event=spec.get("sink") or event_type.rsplit(".", 1)[-1],
        actor_id=getattr(actor, "id", None),
        data=payload,
        request_id=request_id,
    )

    try:
        # Entering atomic() inside the caller's transaction opens a savepoint,
        # so a failed insert rolls back only this row.
        with transaction.atomic():
            return SystemEvent.objects.create(
                type=event_type,
                category=spec["category"],
                actor=actor,
                student=student,
                message=_clip(message) or render_message(event_type, payload),
                data=payload,
                request_id=request_id,
            )
    except Exception:
        # A feed row must never break the business write around it.
        logger.warning("Could not store system event %s", event_type, exc_info=True)
        return None
