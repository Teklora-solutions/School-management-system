"""
apps/core/notifications.py
--------------------------
Reusable in-app notification helpers. Call from any service.

Quiet hours
-----------
When ``SchoolSettings.notification_quiet_hours_enabled`` is True, any
notification whose category is NOT in ``ALWAYS_IMMEDIATE_CATEGORIES`` is
held by setting ``deliver_after`` to the start of the next active window.
The bell-count context processor and the notification inbox filter on
``deliver_after <= now()`` so held notifications are invisible in the bell
until the window ends.

A note about the recommend-and-build choice:
  Quiet hours apply only to in-app notifications (the bell and inbox).
  They have no effect on future push or SMS channels.  The simplest
  meaningful behaviour is: create the row immediately, but set deliver_after
  so the bell suppresses it until morning.  No cron job needed; the
  deliver_after index makes the filter cheap.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from apps.accounts.models import User

#: Categories that always deliver immediately, even during quiet hours.
#: Digest notifications and safety notices are never held.
ALWAYS_IMMEDIATE_CATEGORIES = {"digest", "safety", "level_up"}


def _quiet_deliver_after():
    """
    If quiet hours are active right now, return the datetime when the next
    active window begins.  Otherwise return None (deliver immediately).

    Handles windows that cross midnight (e.g. 20:00 → 06:00).
    """
    from django.utils import timezone
    from .selectors import school_settings_values

    settings = school_settings_values()
    if not settings.get("notification_quiet_hours_enabled", False):
        return None

    start_h = settings.get("quiet_hours_start", 20)
    end_h   = settings.get("quiet_hours_end", 6)

    # An empty window (same start and end) means "never quiet".
    if start_h == end_h:
        return None

    now_local = timezone.localtime(timezone.now())
    current_h = now_local.hour

    # Determine whether we are inside quiet hours.
    # Window crosses midnight when start_h >= end_h (e.g. 20 → 06).
    if start_h >= end_h:
        in_quiet = current_h >= start_h or current_h < end_h
    else:
        in_quiet = start_h <= current_h < end_h

    if not in_quiet:
        return None

    # Build the deliver_after timestamp: today (or tomorrow if end_h < current_h)
    # at end_h:00:00 local time, converted to UTC for storage.
    import datetime
    today = now_local.date()
    if end_h <= current_h and start_h < end_h:
        # Same-day window and we're past the end — shouldn't happen in quiet,
        # but guard anyway.
        return None

    # Next window open: today at end_h if still in future, else tomorrow.
    candidate = now_local.replace(
        hour=end_h, minute=0, second=0, microsecond=0
    )
    if candidate <= now_local:
        candidate += datetime.timedelta(days=1)

    return candidate


def notify(
    *,
    recipient: "User",
    title: str,
    message: str = "",
    link: str = "",
    category: str = "",
) -> None:
    """
    Create a single in-app notification for *recipient*.

    If quiet hours are active and the category is not in
    ``ALWAYS_IMMEDIATE_CATEGORIES``, ``deliver_after`` is set to the start
    of the next active window so the bell suppresses the notification until
    morning.
    """
    from .models import Notification

    deliver_after = None
    if category not in ALWAYS_IMMEDIATE_CATEGORIES:
        deliver_after = _quiet_deliver_after()

    Notification.objects.create(
        recipient=recipient,
        title=title,
        message=message,
        link=link,
        category=category,
        deliver_after=deliver_after,
    )


def notify_many(
    *,
    recipients: Iterable["User"],
    title: str,
    message: str = "",
    link: str = "",
    category: str = "",
) -> int:
    """Bulk-create notifications. Returns count created."""
    from django.utils import timezone as _tz
    from .models import Notification

    deliver_after = None
    if category not in ALWAYS_IMMEDIATE_CATEGORIES:
        deliver_after = _quiet_deliver_after()

    objs = [
        Notification(
            recipient=user,
            title=title,
            message=message,
            link=link,
            category=category,
            deliver_after=deliver_after,
        )
        for user in recipients
    ]
    if not objs:
        return 0
    Notification.objects.bulk_create(objs)
    return len(objs)


def delivered_notifications(queryset):
    """
    Filter a Notification queryset to only those that are ready for delivery
    (deliver_after is NULL or in the past).  Called by the notification list
    view and the unread-count context processor so held notifications are
    invisible in the bell until the quiet-hours window ends.
    """
    from django.utils import timezone as _tz
    return queryset.filter(
        models.Q(deliver_after__isnull=True) | models.Q(deliver_after__lte=_tz.now())
    )
