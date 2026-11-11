"""
apps/core/selectors.py
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Count, QuerySet

from .events import CATEGORIES
from .models import AuditLog, SchoolSettings, SystemEvent

logger = logging.getLogger(__name__)


def audit_log_list(
    *,
    user_id: int | None = None,
    content_type: str = "",
    action: str = "",
    date_from=None,
    date_to=None,
) -> QuerySet:
    qs = AuditLog.objects.select_related("actor")
    if user_id:
        qs = qs.filter(actor_id=user_id)
    if content_type:
        qs = qs.filter(content_type__icontains=content_type)
    if action:
        qs = qs.filter(action__icontains=action)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


def recent_audit_events(limit: int = 10) -> QuerySet:
    return AuditLog.objects.select_related("actor")[:limit]


# ---------------------------------------------------------------------------
# Business activity (SystemEvent)
# ---------------------------------------------------------------------------

#: Rows the daily report lists before it says "and N more".
DAILY_REPORT_ROWS = 100


def system_event_list(
    *,
    event_type: str = "",
    category: str = "",
    student_id: int | None = None,
    date_from=None,
    date_to=None,
) -> QuerySet:
    qs = SystemEvent.objects.select_related("actor", "student__user")
    if event_type:
        qs = qs.filter(type=event_type)
    if category:
        qs = qs.filter(category=category)
    if student_id:
        qs = qs.filter(student_id=student_id)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


def daily_activity(day) -> dict:
    """
    One-day digest for the admin daily report.

    Three queries: the category counts, the payment amounts to total, and the
    rows to list. The total is summed from the counts rather than re-counted.
    """
    of_day = SystemEvent.objects.filter(created_at__date=day)
    counts = {
        row["category"]: row["count"]
        for row in of_day.values("category").annotate(count=Count("id"))
    }
    total = sum(counts.values())
    events = list(
        of_day.select_related("actor", "student__user")[:DAILY_REPORT_ROWS]
    )
    return {
        "day": day,
        "events": events,
        "total": total,
        "collected": _collected_on(day),
        "truncated": total > len(events),
        "by_category": [
            {
                "slug": slug,
                "label": CATEGORIES.get(slug, slug),
                "count": count,
            }
            for slug, count in sorted(counts.items(), key=lambda item: -item[1])
        ],
    }


def _collected_on(day) -> Decimal:
    """
    Sum of the amounts in the day's recorded payments.

    The amounts live in the JSON ``data`` column, so this totals the day's rows
    in Python instead of relying on a portable JSON cast. A row whose amount
    cannot be read is skipped — it must not silently report a smaller total
    than what it actually added.
    """
    rows = SystemEvent.objects.filter(
        type="finance.payment_posted", created_at__date=day
    ).values_list("data", flat=True)
    total = Decimal(0)
    for data in rows:
        raw = (data or {}).get("amount")
        if raw is None:
            continue
        try:
            total += Decimal(str(raw))
        except InvalidOperation:
            logger.warning("Ignoring unreadable amount in payment event: %r", raw)
    return total


# SchoolSettings field -> settings constant used when the field is left blank.
_ENV_FALLBACKS = {
    "name": "SCHOOL_NAME",
    "motto": "SCHOOL_MOTTO",
    "phone": "SCHOOL_PHONE",
    "email": "SCHOOL_EMAIL",
    "currency": "SCHOOL_CURRENCY",
}


#: Distinct from ``None``, which is the legitimate answer "no row saved yet".
_UNREAD = object()


def school_settings_row(request=None):
    """
    The singleton settings row, or ``None`` before the school saves one.

    Memoised on the request so the context processor, a view and the public
    site's identity selector all share one primary-key read. ``None`` is
    memoised too, so a fresh install does not repeat an empty read.
    """
    if request is not None:
        memo = getattr(request, "_school_settings_row", _UNREAD)
        if memo is not _UNREAD:
            return memo
    row = SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).first()
    if request is not None:
        request._school_settings_row = row
    return row


def school_settings_values(request=None) -> dict:
    """
    Effective school identity: the SchoolSettings row, with any blank field
    falling back to the corresponding ``SCHOOL_*`` setting from the environment.

    One primary-key read per *request*, memoised on the request itself. The
    context processor and a view that wants the currency for a table header are
    asking the same question about the same row; without the memo a page pays
    twice for one fact, which is how the Director home's query budget crept.
    """
    if request is not None:
        memo = getattr(request, "_school_settings_values", None)
        if memo is not None:
            return memo
    row = school_settings_row(request)
    values = {}
    for field, env_key in _ENV_FALLBACKS.items():
        stored = str(getattr(row, field, "") or "").strip() if row else ""
        values[field] = stored or str(getattr(settings, env_key, "") or "")
    values["show_positions"] = row.show_positions if row else True
    values["fees_enabled"] = row.fees_enabled if row else True
    values["learning_enabled"] = row.learning_enabled if row else False
    values["engagement_enabled"] = row.engagement_enabled if row else False
    values["quizzes_enabled"] = row.quizzes_enabled if row else False
    values["live_classes_enabled"] = row.live_classes_enabled if row else False
    values["feed_community_enabled"] = (
        row.feed_community_enabled if row else False
    )
    values["notification_quiet_hours_enabled"] = (
        row.notification_quiet_hours_enabled if row else False
    )
    values["quiet_hours_start"] = row.quiet_hours_start if row else 20
    values["quiet_hours_end"] = row.quiet_hours_end if row else 6
    if request is not None:
        request._school_settings_values = values
    return values
