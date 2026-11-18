"""
apps/assistant/tasks.py
-----------------------
Assistant background jobs.

Rule for this app only: exceptions are NOT swallowed. A failure is recorded in
``AssistantJobFailure`` with the reason and attempt count, and a retry is
queued with a limit. Existing jobs in other apps keep their behaviour.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

from .models import AssistantJobFailure, SafeguardingAlert

log = logging.getLogger(__name__)

MAX_EMAIL_ATTEMPTS = 3


def _record_failure(task_name: str, reason: str, attempts: int) -> AssistantJobFailure:
    return AssistantJobFailure.objects.create(
        task_name=task_name,
        reason=(reason or "unknown error")[:300],
        attempts=attempts,
    )


def _alert_recipients(settings_row, alert) -> list[str]:
    from apps.accounts.models import User

    addresses = {
        (settings_row.safeguarding_contact_email or "").strip(),
        (settings_row.handoff_email or "").strip(),
    }
    addresses.update(
        User.objects.filter(role__in=["admin", "director"], is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    return sorted(addr for addr in addresses if addr)


def _alert_body(alert, settings_row) -> str:
    from django.urls import reverse

    from .alerts import category_label

    link = reverse("assistant:alerts")
    lines = [
        f"A safeguarding alert was raised ({category_label(alert.category)}).",
        f"Rule: {alert.rule_id or 'custom'}",
        f"Excerpt (masked): {alert.excerpt or '(none)'}",
        "",
        "Open the assistant alerts page to acknowledge it:",
        link,
    ]
    if settings_row.escalation_minutes:
        lines.append(
            f"If it is not acknowledged within {settings_row.escalation_minutes} minutes "
            "during working hours, the director and admin are alerted again."
        )
    return "\n".join(lines)


def send_alert_email(alert: SafeguardingAlert, attempt: int = 1) -> bool:
    """
    Send immediately. On failure, record it and queue a limited retry.

    Returns True when sent. Never raises for the caller: the alert row and the
    in-app notifications are the guaranteed channels, and the failure is
    visible to the admin either way.
    """
    from .models import AssistantSettings

    settings_row = AssistantSettings.load()
    recipients = _alert_recipients(settings_row, alert)
    task_name = "assistant.safeguarding_email"
    if not recipients:
        _record_failure(task_name, "No email recipients are configured.", attempt)
        return False
    try:
        send_mail(
            "School safeguarding alert — action needed",
            _alert_body(alert, settings_row),
            getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@school.example"),
            recipients,
            fail_silently=False,
        )
        return True
    except Exception as exc:  # SMTP or configuration failure
        log.warning("Assistant alert email failed on attempt %s: %s", attempt, exc)
        _record_failure(task_name, str(exc), attempt)
        if attempt < MAX_EMAIL_ATTEMPTS:
            try:
                from django_q.tasks import async_task

                async_task(
                    "apps.assistant.tasks.send_alert_email_task",
                    alert.pk,
                    attempt + 1,
                )
            except Exception as queue_exc:  # worker unavailable
                _record_failure(task_name, f"Could not queue retry: {queue_exc}", attempt)
        return False


def send_alert_email_task(alert_id: int, attempt: int) -> None:
    """
    Retry task. On final failure it re-raises so django-q records the failure
    instead of reporting a silent success.
    """
    try:
        alert = SafeguardingAlert.objects.get(pk=alert_id)
    except SafeguardingAlert.DoesNotExist:
        return
    settings_row = None
    from .models import AssistantSettings

    settings_row = AssistantSettings.load()
    recipients = _alert_recipients(settings_row, alert)
    if not recipients:
        _record_failure("assistant.safeguarding_email", "No email recipients are configured.", attempt)
        return
    try:
        send_mail(
            "School safeguarding alert — action needed",
            _alert_body(alert, settings_row),
            getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@school.example"),
            recipients,
            fail_silently=False,
        )
    except Exception as exc:
        _record_failure("assistant.safeguarding_email", str(exc), attempt)
        if attempt < MAX_EMAIL_ATTEMPTS:
            from django_q.tasks import async_task

            async_task("apps.assistant.tasks.send_alert_email_task", alert_id, attempt + 1)
            return
        raise
