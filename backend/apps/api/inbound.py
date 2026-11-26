"""
apps/api/inbound.py
--------------------
Inbound webhook receiver — for external systems that push events to us
(e.g. a payment gateway confirming a transaction, or a government EMIS
portal pushing enrolment data).

Security model
--------------
Every registered inbound source has a shared secret.  The sender MUST
include the header:
    X-Webhook-Signature: sha256=<hmac-sha256-of-raw-body>

We verify this before touching the payload.  If verification fails we
return 401 and log a warning — we never expose why it failed.

Processing model
----------------
1. Parse and verify the signature  → 401 on failure
2. Return 200 immediately          → the caller gets a quick ACK
3. Queue the actual work           → django-q2 handles it asynchronously

This guarantees delivery even if our processing is slow, and prevents
the sender from timing out and retrying with duplicate events.

Registered inbound sources live in InboundWebhookSource (see models).
Each source has its own secret, an event_type prefix filter, and a
handler path (dotted Python path to the function that processes the event).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django_q.tasks import async_task

from .models import WebhookEndpoint  # import guard so models are ready

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inbound source registry
# ---------------------------------------------------------------------------
# Map slug → {"secret": str, "handler": "dotted.path.to.function"}
# In production these come from the database (InboundWebhookSource model,
# added in a future migration).  For now we support a simple settings-driven
# registry that can be overridden without a migration.

def _get_source_registry() -> dict:
    from django.conf import settings
    return getattr(settings, "INBOUND_WEBHOOK_SOURCES", {})


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def _verify_signature(body: bytes, secret: str, header_value: str) -> bool:
    """
    Verify the X-Webhook-Signature header value.

    Expected format: ``sha256=<hex-digest>``
    """
    if not header_value.startswith("sha256="):
        return False
    received = header_value[7:]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name="dispatch")
class InboundWebhookView(View):
    """
    POST /api/v1/inbound/<source_slug>/

    Accepts a signed JSON payload from a registered external source,
    verifies the signature, ACKs immediately, and queues processing.
    """

    def post(self, request, source_slug: str):
        registry = _get_source_registry()

        if source_slug not in registry:
            # Return generic 404 — don't reveal which slugs are valid.
            logger.warning("Inbound webhook: unknown source slug %r", source_slug)
            return HttpResponse(status=404)

        source_config = registry[source_slug]
        secret = source_config.get("secret", "")
        handler_path = source_config.get("handler", "")

        if not secret or not handler_path:
            logger.error(
                "Inbound webhook: source %r is misconfigured (missing secret or handler)",
                source_slug,
            )
            return HttpResponse(status=500)

        # --- Signature verification -----------------------------------------
        sig_header = request.META.get("HTTP_X_WEBHOOK_SIGNATURE", "")
        if not sig_header:
            # Also accept GitHub-style X-Hub-Signature-256
            sig_header = request.META.get("HTTP_X_HUB_SIGNATURE_256", "")

        body = request.body
        if not _verify_signature(body, secret, sig_header):
            logger.warning(
                "Inbound webhook: signature verification failed for source %r",
                source_slug,
            )
            return HttpResponse(status=401)

        # --- Parse JSON -------------------------------------------------------
        content_type = request.content_type or ""
        if "application/json" not in content_type:
            return JsonResponse(
                {"error": "Content-Type must be application/json"},
                status=415,
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        # --- ACK immediately, process in background --------------------------
        async_task(
            handler_path,
            source_slug,
            payload,
            task_name=f"inbound:{source_slug}:{id(payload)}",
        )

        logger.info(
            "Inbound webhook accepted from source %r — queued handler %s",
            source_slug,
            handler_path,
        )
        return JsonResponse({"received": True}, status=200)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


# ---------------------------------------------------------------------------
# Built-in handlers (called by django-q2 workers)
# ---------------------------------------------------------------------------

def handle_payment_gateway_webhook(source_slug: str, payload: dict) -> None:
    """
    Example handler for inbound payment confirmation from a gateway
    (e.g. Paystack, Flutterwave, M-Pesa Daraja).

    Replace / extend this with your gateway's actual payload structure.
    Wire it up in settings.py:
        INBOUND_WEBHOOK_SOURCES = {
            "paystack": {
                "secret": "your-paystack-webhook-secret",
                "handler": "apps.api.inbound.handle_payment_gateway_webhook",
            }
        }
    """
    event = payload.get("event", "")
    logger.info("Payment gateway event from %r: %s", source_slug, event)

    if event in ("charge.success", "transfer.success"):
        data = payload.get("data", {})
        reference = data.get("reference") or data.get("trxref", "")
        amount_kobo = data.get("amount", 0)  # Paystack sends in kobo/pesewas
        amount = round(amount_kobo / 100, 2)

        logger.info(
            "Payment confirmed: reference=%s amount=%s source=%s",
            reference,
            amount,
            source_slug,
        )
        # TODO: call apps.finance.services.confirm_external_payment(reference, amount)
        # once that service function is built.


def handle_emis_webhook(source_slug: str, payload: dict) -> None:
    """
    Example handler for government EMIS (Education Management Information
    System) data pushes — enrolment lists, official class assignments, etc.
    """
    logger.info("EMIS webhook received from %r: keys=%s", source_slug, list(payload.keys()))
    # TODO: implement EMIS data reconciliation logic.
