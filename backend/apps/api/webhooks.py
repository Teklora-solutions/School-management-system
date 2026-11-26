"""
apps/api/webhooks.py
--------------------
Outbound webhook engine.

``dispatch_event(event_type, payload)``
  Called from service layer code after a business action completes.
  Creates WebhookDelivery rows for every active, subscribed endpoint and
  queues each one as a django-q2 background task.

``deliver_webhook(delivery_id)``
  The background task.  Makes one HTTPS POST, signs the payload with
  HMAC-SHA256, and updates the delivery row with the outcome.
  On failure it reschedules itself via django-q2 at the next_retry_at time.

Signing
  Every outbound request includes the header:
      X-Webhook-Signature: sha256=<hex-digest>
  The digest is computed over the raw JSON body using the endpoint's secret.
  Receivers verify by computing the same digest and comparing with
  hmac.compare_digest().

Event type registry
  EVENT_TYPES maps event type strings to a human description.
  It is the authoritative list; dispatch_event logs a warning for
  unregistered types but still delivers them so callers never silently drop.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid

import httpx
from django.utils import timezone
from django_q.tasks import async_task

from .models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event type registry
# ---------------------------------------------------------------------------

EVENT_TYPES: dict[str, str] = {
    # Students
    "student.enrolled": "A new student has been enrolled.",
    "student.updated": "A student's record was updated.",
    "student.status_changed": "A student's status changed (e.g. withdrawn, graduated).",
    "student.promoted": "A student was promoted to the next class.",
    # Academics
    "academic_year.created": "A new academic year was created.",
    "term.started": "A term was marked as current.",
    # Results
    "result.published": "A result sheet was published.",
    # Finance
    "invoice.created": "A fee invoice was generated.",
    "payment.received": "A fee payment was recorded.",
    "payment.reversed": "A payment reversal was posted.",
    # Attendance
    "attendance.recorded": "An attendance register was completed.",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def dispatch_event(
    event_type: str,
    payload: dict,
    *,
    idempotency_key: uuid.UUID | None = None,
) -> list[WebhookDelivery]:
    """
    Create delivery rows for every subscribed endpoint and queue the HTTP calls.

    Returns the list of created WebhookDelivery rows (may be empty if no
    endpoints subscribe to this event type).
    """
    if event_type not in EVENT_TYPES:
        logger.warning("dispatch_event: unregistered event type %r", event_type)

    endpoints = WebhookEndpoint.objects.filter(is_active=True, disabled_at=None)
    deliveries: list[WebhookDelivery] = []

    for endpoint in endpoints:
        if not endpoint.subscribes_to(event_type):
            continue

        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint,
            event_type=event_type,
            idempotency_key=idempotency_key or uuid.uuid4(),
            payload={
                "event": event_type,
                "timestamp": timezone.now().isoformat(),
                "data": payload,
            },
        )
        deliveries.append(delivery)

        # Queue the HTTP call as a background task.
        # django-q2 will run this in one of the 2 ORM-broker workers.
        async_task(
            "apps.api.webhooks.deliver_webhook",
            delivery.pk,
            task_name=f"webhook:{event_type}:{delivery.pk}",
        )
        logger.debug(
            "Queued webhook delivery %s for endpoint %s",
            delivery.pk,
            endpoint.name,
        )

    return deliveries


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


def deliver_webhook(delivery_id: int) -> None:
    """
    django-q2 task: make one HTTPS POST for a WebhookDelivery row.

    Signs the payload, posts it, records the outcome.  On failure, marks the
    row and (if retries remain) schedules another attempt via async_task.
    """
    try:
        delivery = WebhookDelivery.objects.select_related("endpoint").get(
            pk=delivery_id
        )
    except WebhookDelivery.DoesNotExist:
        logger.error("deliver_webhook: delivery %s not found", delivery_id)
        return

    if delivery.is_terminal:
        logger.info("deliver_webhook: delivery %s already terminal, skipping", delivery_id)
        return

    endpoint = delivery.endpoint
    body = json.dumps(delivery.payload, default=str).encode()
    signature = _sign(body, endpoint.secret)

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": f"sha256={signature}",
        "X-Webhook-Event": delivery.event_type,
        "X-Delivery-ID": str(delivery.idempotency_key),
        "User-Agent": "SchoolMS-Webhook/1.0",
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(endpoint.url, content=body, headers=headers)

        if response.is_success:
            delivery.mark_delivered(response.status_code, response.text)
            endpoint.record_success()
            logger.info(
                "Webhook delivered: %s → %s (%s)",
                delivery.event_type,
                endpoint.name,
                response.status_code,
            )
        else:
            _handle_failure(
                delivery,
                endpoint,
                status_code=response.status_code,
                body=response.text,
                error=f"HTTP {response.status_code}",
            )

    except httpx.TimeoutException as exc:
        _handle_failure(delivery, endpoint, error=f"Timeout: {exc}")
    except httpx.RequestError as exc:
        _handle_failure(delivery, endpoint, error=f"Request error: {exc}")


# ---------------------------------------------------------------------------
# Retry scheduling
# ---------------------------------------------------------------------------


def retry_failed_webhooks() -> int:
    """
    Scheduled job: pick up all FAILED deliveries whose next_retry_at is due
    and re-queue them.  Run this every 5 minutes via a django-q2 schedule.

    Returns the number of deliveries re-queued.
    """
    now = timezone.now()
    due = WebhookDelivery.objects.filter(
        status=WebhookDelivery.Status.FAILED,
        next_retry_at__lte=now,
    ).select_related("endpoint")

    count = 0
    for delivery in due:
        # Reset to pending so the worker picks it up cleanly
        WebhookDelivery.objects.filter(pk=delivery.pk).update(
            status=WebhookDelivery.Status.PENDING
        )
        async_task(
            "apps.api.webhooks.deliver_webhook",
            delivery.pk,
            task_name=f"webhook:retry:{delivery.pk}",
        )
        count += 1

    if count:
        logger.info("Scheduled %d webhook retries", count)
    return count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _handle_failure(
    delivery: WebhookDelivery,
    endpoint: WebhookEndpoint,
    *,
    status_code: int | None = None,
    body: str = "",
    error: str = "",
) -> None:
    delivery.mark_failed(status_code, body, error)
    endpoint.record_failure()
    logger.warning(
        "Webhook failed: %s → %s (attempt %d/%d): %s",
        delivery.event_type,
        endpoint.name,
        delivery.attempts,
        WebhookDelivery.MAX_ATTEMPTS,
        error,
    )
    # If not abandoned and retry is due in the future, the retry scheduler
    # (retry_failed_webhooks cron) picks it up — no immediate re-queue here
    # to avoid thundering-herd on a flaky endpoint.
