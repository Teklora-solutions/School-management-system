"""
apps/assistant/alerts.py
------------------------
Safeguarding alerts: create, acknowledge, escalate.

Guarantees
  * the alert row is always created before notifications are attempted;
  * in-app notifications go to admin + director through the existing
    ``category="safety"`` path, which bypasses quiet hours;
  * escalation is checked from the dashboard middleware, not only from a
    background job, so a dead worker cannot hide an unacknowledged alert;
  * nothing is deleted automatically.
"""

from __future__ import annotations

import datetime

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.audit_log import log_action
from apps.core.notifications import notify_many

from .guardrails import mask_excerpt
from .models import AssistantSettings, CustomGuardrailRule, SafeguardingAlert

SAFETY_ROLES = ("admin", "director")
CATEGORY_LABELS = dict(CustomGuardrailRule.Category.choices)

# The banner and the escalation check share one short-lived state so an ordinary
# page load costs no more than two queries a minute, not two per page.
PAGE_STATE_KEY = "assistant:page-state"
PAGE_STATE_TTL = 60


def invalidate_page_state() -> None:
    cache.delete(PAGE_STATE_KEY)


def page_state() -> dict:
    state = cache.get(PAGE_STATE_KEY)
    if state is None:
        state = refresh_page_state()
    return state


def refresh_page_state(*, now=None) -> dict:
    """Read settings and open alerts once, escalate what is due, and cache the banner state."""
    now = now or timezone.now()
    settings_row = AssistantSettings.read()
    open_alerts = list(
        SafeguardingAlert.objects.filter(status=SafeguardingAlert.Status.OPEN)
    )
    overdue = [
        alert
        for alert in open_alerts
        if alert.escalated_at is None and due_at(alert, settings_row) <= now
    ]
    _escalate(overdue, settings_row, now)
    state = {
        "has_safeguarding_contact": settings_row.has_safeguarding_contact,
        "open_alert_count": len(open_alerts),
    }
    cache.set(PAGE_STATE_KEY, state, PAGE_STATE_TTL)
    return state


def category_label(key: str) -> str:
    return CATEGORY_LABELS.get(key, key.replace("_", " ").title())


def _staff_recipients():
    return User.objects.filter(role__in=SAFETY_ROLES, is_active=True)


def create_alert(
    *,
    profile: str,
    category: str,
    rule_id: str = "",
    raw_text: str = "",
    user=None,
    ip_address: str | None = None,
    dry_run: bool = False,
) -> SafeguardingAlert:
    alert = SafeguardingAlert.objects.create(
        profile=profile,
        category=category,
        rule_id=rule_id,
        excerpt=mask_excerpt(raw_text, limit=300),
        user=user if getattr(user, "is_authenticated", False) else None,
        ip_address=ip_address or None,
    )
    invalidate_page_state()
    log_action(
        actor=user if getattr(user, "is_authenticated", False) else None,
        action="assistant.safeguarding_alert_created",
        content_type="assistant.SafeguardingAlert",
        object_id=alert.pk,
        change_summary={"category": category, "rule_id": rule_id, "profile": profile},
    )
    if dry_run:
        return alert

    link = reverse("assistant:alerts")
    notify_many(
        recipients=_staff_recipients(),
        title="Safeguarding alert — action needed",
        message=(
            f"A safeguarding concern was raised ({category_label(alert.category)}). "
            "Open the assistant alerts page to acknowledge it."
        ),
        link=link,
        category="safety",
    )
    from . import tasks

    tasks.send_alert_email(alert, attempt=1)
    return alert


def close_alert(
    alert: SafeguardingAlert, *, outcome: str, actor, request=None
) -> SafeguardingAlert:
    """Close an alert with the outcome recorded. Closing starts the retention clock."""
    outcome = (outcome or "").strip()
    if not outcome:
        raise ValueError("Record what was done before closing the alert.")
    if alert.status == SafeguardingAlert.Status.CLOSED:
        return alert
    now = timezone.now()
    alert.status = SafeguardingAlert.Status.CLOSED
    alert.outcome = outcome[:2000]
    alert.closed_at = now
    alert.closed_by = actor
    fields = ["status", "outcome", "closed_at", "closed_by", "updated_at"]
    if not alert.acknowledged_at:
        alert.acknowledged_at = now
        alert.acknowledged_by = actor
        fields += ["acknowledged_at", "acknowledged_by"]
    alert.save(update_fields=fields)
    invalidate_page_state()
    log_action(
        actor=actor,
        action="assistant.safeguarding_alert_closed",
        content_type="assistant.SafeguardingAlert",
        object_id=alert.pk,
        change_summary={"category": alert.category, "outcome_recorded": True},
        request=request,
    )
    return alert


def acknowledge(alert: SafeguardingAlert, *, actor, request=None) -> SafeguardingAlert:
    if alert.status == SafeguardingAlert.Status.CLOSED:
        return alert
    if alert.status != SafeguardingAlert.Status.ACKNOWLEDGED:
        alert.status = SafeguardingAlert.Status.ACKNOWLEDGED
        alert.acknowledged_at = timezone.now()
        alert.acknowledged_by = actor
        alert.save(update_fields=["status", "acknowledged_at", "acknowledged_by", "updated_at"])
        invalidate_page_state()
        log_action(
            actor=actor,
            action="assistant.safeguarding_alert_acknowledged",
            content_type="assistant.SafeguardingAlert",
            object_id=alert.pk,
            change_summary={"category": alert.category},
            request=request,
        )
    return alert


def _next_working_start(settings_row, moment):
    local = timezone.localtime(moment)
    start = settings_row.working_hours_start
    today_start = timezone.make_aware(datetime.datetime.combine(local.date(), start))
    if local < today_start:
        return today_start
    return today_start + datetime.timedelta(days=1)


def due_at(alert: SafeguardingAlert, settings_row: AssistantSettings):
    created = alert.created_at
    minutes = settings_row.escalation_minutes or 0
    if settings_row.working_hours_start == settings_row.working_hours_end:
        return created + datetime.timedelta(minutes=minutes)
    if settings_row.within_working_hours(timezone.localtime(created)):
        return created + datetime.timedelta(minutes=minutes)
    return _next_working_start(settings_row, created) + datetime.timedelta(minutes=minutes)


def escalate_due(*, now=None) -> int:
    """Escalate every open, unescalated alert whose due time has passed."""
    now = now or timezone.now()
    settings_row = AssistantSettings.read()
    overdue = [
        alert
        for alert in SafeguardingAlert.objects.filter(
            status=SafeguardingAlert.Status.OPEN, escalated_at__isnull=True
        )
        if due_at(alert, settings_row) <= now
    ]
    _escalate(overdue, settings_row, now)
    return len(overdue)


def _escalate(overdue, settings_row, now) -> None:
    if not overdue:
        return

    link = reverse("assistant:alerts")
    for alert in overdue:
        alert.escalated_at = now
        alert.save(update_fields=["escalated_at", "updated_at"])
        notify_many(
            recipients=_staff_recipients(),
            title="Still unacknowledged: safeguarding alert",
            message=(
                f"A {category_label(alert.category)} alert has not been acknowledged within "
                f"{settings_row.escalation_minutes} minutes. Please open the alerts page now."
            ),
            link=link,
            category="safety",
        )
        log_action(
            actor=None,
            action="assistant.safeguarding_alert_escalated",
            content_type="assistant.SafeguardingAlert",
            object_id=alert.pk,
            change_summary={"category": alert.category},
        )
        from . import tasks

        tasks.send_alert_email(alert, attempt=1)


def open_alert_count() -> int:
    return SafeguardingAlert.objects.filter(status=SafeguardingAlert.Status.OPEN).count()


def _months_before(moment, months: int):
    import calendar

    month_index = moment.month - 1 - months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def erasable_excerpts(*, now=None, alert_id: int | None = None):
    """
    Closed alerts whose masked excerpt is due for erasure.

    ``alert_id`` is for a deletion request: the row must already be closed, and
    the caller decides the erasure; the minimal record still stays.
    """
    now = now or timezone.now()
    rows = SafeguardingAlert.objects.filter(
        status=SafeguardingAlert.Status.CLOSED,
        excerpt_erased_at__isnull=True,
    ).exclude(excerpt="")
    if alert_id is not None:
        return list(rows.filter(pk=alert_id))
    months = AssistantSettings.load().alert_retention_months or 0
    if months <= 0:
        return []
    return list(rows.filter(closed_at__isnull=False, closed_at__lte=_months_before(now, months)))


def erase_excerpts(*, now=None, alert_id: int | None = None, reason: str = "retention"):
    """Erase the masked excerpt and keep the minimal record. Returns the rows changed."""
    now = now or timezone.now()
    erased = []
    for alert in erasable_excerpts(now=now, alert_id=alert_id):
        alert.excerpt = ""
        alert.excerpt_erased_at = now
        alert.save(update_fields=["excerpt", "excerpt_erased_at", "updated_at"])
        log_action(
            actor=None,
            action="assistant.safeguarding_excerpt_erased",
            content_type="assistant.SafeguardingAlert",
            object_id=alert.pk,
            change_summary={"category": alert.category, "reason": reason},
        )
        erased.append(alert)
    return erased
