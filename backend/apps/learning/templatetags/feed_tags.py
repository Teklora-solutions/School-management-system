"""
Feed template helpers.

Times are always shown in Nairobi time (the project's ``TIME_ZONE``), never
in the reader's device timezone, so a parent abroad and a pupil in class see
the same clock.
"""

from __future__ import annotations

from datetime import timedelta

from django import template
from django.utils import timezone
from django.utils.formats import date_format

register = template.Library()


def _local(value):
    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value)


@register.filter
def feed_relative(value) -> str:
    """Friendly age: "Just now", "5m ago", "2h ago", "Yesterday", "3d ago", "12 Nov"."""
    local = _local(value)
    if local is None:
        return ""
    now = timezone.localtime(timezone.now())
    if local > now:
        return "Scheduled"

    seconds = (now - local).total_seconds()
    if seconds < 60:
        return "Just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"

    days = (now.date() - local.date()).days
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days}d ago"
    if local.year == now.year:
        return date_format(local, "j M")
    return date_format(local, "j M Y")


@register.filter
def feed_exact(value) -> str:
    """Exact Nairobi time, for the tap-to-reveal label."""
    local = _local(value)
    if local is None:
        return ""
    return date_format(local, "j M Y, g:i a")


@register.filter
def feed_iso(value) -> str:
    local = _local(value)
    return local.isoformat() if local else ""


@register.filter
def feed_schedule_label(value) -> str:
    """A scheduled post's label: "Scheduled for 16 Nov, 8:00 am"."""
    local = _local(value)
    if local is None:
        return ""
    now = timezone.localtime(timezone.now())
    delta = local - now
    if delta <= timedelta(hours=24):
        return f"Scheduled for {date_format(local, 'g:i a')}"
    return f"Scheduled for {date_format(local, 'j M, g:i a')}"
