"""
apps/api/models.py
------------------
Models for the external integration layer.

Three concerns live here:

APIClient
  A machine identity — not a User. Each external system (payment gateway,
  EMIS portal, mobile app) gets its own row with a unique API key and a
  list of allowed scopes. Revoking one client never touches anything else.

WebhookEndpoint
  A URL registered by an admin that this system calls when a business event
  fires (student enrolled, fee paid, result published). Each endpoint
  subscribes to a specific list of event types and carries a signing secret
  so the receiver can verify the payload is genuine.

WebhookDelivery
  An append-only log of every outbound HTTP call. Failures are retried with
  exponential back-off via django-q2. The row is created before the HTTP
  call so a crash between insert and send still produces a retriable record.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


# ---------------------------------------------------------------------------
# Scope registry
# ---------------------------------------------------------------------------

#: All valid scope strings.  Add entries here when new API surface is exposed;
#: ``APIClient.has_scope()`` checks against this set at runtime so a typo in
#: code is caught before it silently grants access.
VALID_SCOPES: frozenset[str] = frozenset(
    [
        # Students
        "students:read",
        "students:write",
        # Academic structure
        "academics:read",
        # Staff / teachers
        "staff:read",
        # Results (published only — raw marks are never exposed externally)
        "results:read",
        # Finance — read is balances + invoices; write is inbound payment confirmation
        "finance:read",
        "finance:write",
        # Attendance
        "attendance:read",
        "attendance:write",
        # Webhooks — a client with this scope can register its own endpoint
        # via the API; without it only an admin console user can do so.
        "webhooks:manage",
    ]
)


def _generate_api_key() -> str:
    """Return a 48-character URL-safe random key prefixed with 'sk_'."""
    return "sk_" + secrets.token_urlsafe(36)


def _generate_webhook_secret() -> str:
    """Return a 32-byte hex secret for HMAC-SHA256 signing."""
    return secrets.token_hex(32)


# ---------------------------------------------------------------------------
# APIClient
# ---------------------------------------------------------------------------


class APIClient(BaseModel):
    """
    A machine identity for one external integration.

    ``key`` is generated once and shown to the admin once.  It is stored as a
    SHA-256 hash so a database breach does not leak working credentials.
    Use ``APIClient.objects.create_client(name, scopes, created_by)`` to
    create a row and retrieve the plain-text key in a single call.
    """

    name = models.CharField(
        max_length=120,
        unique=True,
        help_text='Human label for this integration, e.g. "Paystack Gateway".',
    )
    key_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 hex digest of the plain-text API key.",
    )
    # Prefix is stored separately so we can display it in the admin without
    # storing the full key (the prefix is "sk_" + first 8 chars of the token).
    key_prefix = models.CharField(
        max_length=12,
        help_text="First 11 characters of the key (safe to display).",
    )
    scopes = models.JSONField(
        default=list,
        help_text="List of scope strings this client is authorised for.",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="api_clients_created",
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    # Optional IP allowlist — empty means any IP is accepted.
    allowed_ips = models.JSONField(
        default=list,
        blank=True,
        help_text="Allowlist of IPv4/IPv6 addresses. Empty = unrestricted.",
    )
    description = models.TextField(
        blank=True,
        help_text="Notes about what this client is used for.",
    )

    class Meta:
        db_table = "api_client"
        ordering = ["name"]

    def __str__(self) -> str:
        status = "active" if self.is_active else "revoked"
        return f"{self.name} ({self.key_prefix}…) [{status}]"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def hash_key(plain_key: str) -> str:
        return hashlib.sha256(plain_key.encode()).hexdigest()

    def has_scope(self, scope: str) -> bool:
        """Return True if this client holds *scope* and is active."""
        return self.is_active and scope in self.scopes

    def touch(self) -> None:
        """Update last_used_at without triggering updated_at side-effects."""
        APIClient.objects.filter(pk=self.pk).update(last_used_at=timezone.now())

    # ------------------------------------------------------------------
    # Manager-level factory
    # ------------------------------------------------------------------

    @classmethod
    def create_client(
        cls,
        *,
        name: str,
        scopes: list[str],
        created_by,
        description: str = "",
        allowed_ips: list[str] | None = None,
    ) -> tuple["APIClient", str]:
        """
        Create a new APIClient and return (instance, plain_text_key).

        The plain-text key is returned exactly once and never stored.
        Callers must show it to the user immediately.
        """
        invalid = set(scopes) - VALID_SCOPES
        if invalid:
            raise ValueError(f"Unknown scopes: {sorted(invalid)}")

        plain_key = _generate_api_key()
        client = cls.objects.create(
            name=name,
            key_hash=cls.hash_key(plain_key),
            key_prefix=plain_key[:11],
            scopes=scopes,
            created_by=created_by,
            description=description,
            allowed_ips=allowed_ips or [],
        )
        return client, plain_key

    @classmethod
    def authenticate(cls, plain_key: str) -> "APIClient | None":
        """
        Look up an active client by plain-text key.
        Returns None (never raises) so callers decide the HTTP response.
        """
        try:
            return cls.objects.get(
                key_hash=cls.hash_key(plain_key),
                is_active=True,
            )
        except cls.DoesNotExist:
            return None


# ---------------------------------------------------------------------------
# WebhookEndpoint
# ---------------------------------------------------------------------------


class WebhookEndpoint(BaseModel):
    """
    A URL that this system POSTs to when a matching event fires.

    ``events`` is a list of dot-separated event type strings, e.g.:
        ["student.enrolled", "payment.received", "result.published"]

    Use "*" as the sole element to subscribe to all events.
    """

    name = models.CharField(
        max_length=120,
        help_text='Friendly label, e.g. "EMIS Reporting Portal".',
    )
    url = models.URLField(
        max_length=500,
        help_text="HTTPS endpoint that will receive POST requests.",
    )
    # Signing secret: stored in plain text (it is not a user credential —
    # the receiver published it to us so we can sign; they already know it).
    secret = models.CharField(
        max_length=64,
        default=_generate_webhook_secret,
        help_text="HMAC-SHA256 signing secret.  Share this with the receiver.",
    )
    events = models.JSONField(
        default=list,
        help_text='List of event types to deliver, e.g. ["student.enrolled"].  Use ["*"] for all.',
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="webhook_endpoints_created",
    )
    # Back-off state — updated by the delivery worker, not by user action.
    consecutive_failures = models.PositiveIntegerField(default=0)
    disabled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set by the worker after too many consecutive failures.",
    )

    class Meta:
        db_table = "api_webhook_endpoint"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} → {self.url}"

    def subscribes_to(self, event_type: str) -> bool:
        """Return True if this endpoint wants to receive *event_type*."""
        if not self.is_active or self.disabled_at:
            return False
        return "*" in self.events or event_type in self.events

    def record_success(self) -> None:
        WebhookEndpoint.objects.filter(pk=self.pk).update(
            consecutive_failures=0,
            disabled_at=None,
        )

    def record_failure(self) -> None:
        """Increment failure count and auto-disable after 10 consecutive failures."""
        new_count = self.consecutive_failures + 1
        updates: dict = {"consecutive_failures": new_count}
        if new_count >= 10:
            updates["disabled_at"] = timezone.now()
        WebhookEndpoint.objects.filter(pk=self.pk).update(**updates)
        self.consecutive_failures = new_count


# ---------------------------------------------------------------------------
# WebhookDelivery
# ---------------------------------------------------------------------------


class WebhookDelivery(BaseModel):
    """
    One attempted (or pending) outbound webhook call.

    Rows are created by ``dispatch_event`` before the HTTP call is made.
    Status transitions:
        pending → delivered  (HTTP 2xx received)
        pending → failed     (non-2xx or network error)
        failed  → pending    (queued for retry by the worker)

    After ``MAX_ATTEMPTS`` failures the row is marked ``abandoned`` and no
    further retries are scheduled.
    """

    MAX_ATTEMPTS = 5

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"
        ABANDONED = "abandoned", "Abandoned"

    endpoint = models.ForeignKey(
        WebhookEndpoint,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    event_type = models.CharField(max_length=100, db_index=True)
    # idempotency_key lets callers deduplicate: same key → same delivery row,
    # never a second POST.
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    payload = models.JSONField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Response captured from the last attempt
    response_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    # Error detail for the last failed attempt
    error_detail = models.TextField(blank=True)

    class Meta:
        db_table = "api_webhook_delivery"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_retry_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} → {self.endpoint.name} [{self.status}]"

    @property
    def is_terminal(self) -> bool:
        return self.status in (self.Status.DELIVERED, self.Status.ABANDONED)

    def mark_delivered(self, status_code: int, body: str) -> None:
        now = timezone.now()
        WebhookDelivery.objects.filter(pk=self.pk).update(
            status=self.Status.DELIVERED,
            response_status_code=status_code,
            response_body=body[:2000],
            last_attempted_at=now,
            delivered_at=now,
            attempts=models.F("attempts") + 1,
        )

    def mark_failed(self, status_code: int | None, body: str, error: str) -> None:
        now = timezone.now()
        new_attempts = self.attempts + 1
        if new_attempts >= self.MAX_ATTEMPTS:
            new_status = self.Status.ABANDONED
            next_retry = None
        else:
            new_status = self.Status.FAILED
            # Exponential back-off: 5 min, 15 min, 45 min, 135 min
            delay_minutes = 5 * (3 ** new_attempts)
            next_retry = now + timezone.timedelta(minutes=delay_minutes)

        WebhookDelivery.objects.filter(pk=self.pk).update(
            status=new_status,
            response_status_code=status_code,
            response_body=(body or "")[:2000],
            error_detail=error[:1000],
            last_attempted_at=now,
            next_retry_at=next_retry,
            attempts=new_attempts,
        )
        self.attempts = new_attempts
        self.status = new_status
