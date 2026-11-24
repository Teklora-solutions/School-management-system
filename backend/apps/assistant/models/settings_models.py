from __future__ import annotations

import datetime
from datetime import timedelta
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from ._base import (
    BaseModel,
    DEFAULT_WIDGET_OPENING_EN,
    DEFAULT_WIDGET_OPENING_SW,
    WIDGET_GATE_CACHE_KEY,
    WIDGET_GATE_SECONDS,
    cache,
)

class AssistantSettings(BaseModel):
    """The assistant's single configuration row. Every switch starts OFF."""

    SINGLETON_PK = 1

    # ── Switches ─────────────────────────────────────────────────────────────
    enabled = models.BooleanField(
        default=False,
        help_text="The assistant is live. Off stops the public and signed-in assistant.",
    )
    kill_switch = models.BooleanField(
        default=False,
        help_text=(
            "Stop everything now, including the admin answer desk, the test console "
            "and background reading. Always wins over every other switch."
        ),
    )
    public_enabled = models.BooleanField(
        default=False,
        help_text="The website assistant for visitors. Requires the global switch too.",
    )
    signed_in_enabled = models.BooleanField(
        default=False,
        help_text="The assistant for signed-in parents, students and staff.",
    )
    student_tutor_enabled = models.BooleanField(
        default=False,
        help_text="The student tutor mode. Requires a safeguarding contact and the signed-in switch.",
    )

    # ── Provider and rules ───────────────────────────────────────────────────
    default_provider = models.ForeignKey(
        "assistant.ProviderConfig",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Tried first. Remaining providers follow in fallback order.",
    )
    house_rules = models.TextField(
        blank=True,
        help_text="Standing instructions for the assistant (tone, what it must never say).",
    )

    # ── Money ────────────────────────────────────────────────────────────────
    monthly_budget_ksh = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Site-wide monthly ceiling. 0 means no spending is allowed.",
    )
    usd_to_ksh = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Used to turn provider prices (USD) into the KSh budget. Set this before enabling.",
    )

    # ── Limits ───────────────────────────────────────────────────────────────
    daily_message_limit = models.PositiveSmallIntegerField(
        default=20,
        help_text="Per signed-in person, per day. 0 means no messages are allowed.",
    )
    public_visitor_daily_limit = models.PositiveSmallIntegerField(
        default=10,
        help_text="Questions per visitor per day (signed cookie), per day. 0 disables the public assistant.",
    )
    public_ip_daily_limit = models.PositiveSmallIntegerField(
        default=30,
        help_text="Questions per IP address per day. Shared networks must sit above the visitor limit.",
    )
    public_site_daily_limit = models.PositiveIntegerField(
        default=300,
        help_text="Questions across the whole public assistant per day.",
    )
    console_daily_limit = models.PositiveSmallIntegerField(
        default=50,
        help_text=(
            "Admin test tools only: requests per day across the answer desk and the "
            "test console. 0 stops the test tools."
        ),
    )
    console_monthly_budget_ksh = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=500,
        help_text=(
            "Admin test tools only: their own monthly ceiling in KSh. Separate from "
            "the site-wide budget. 0 stops the test tools."
        ),
    )

    # ── Safeguarding ─────────────────────────────────────────────────────────
    safeguarding_contact_name = models.CharField(max_length=120, blank=True)
    safeguarding_contact_phone = models.CharField(max_length=40, blank=True)
    safeguarding_contact_email = models.EmailField(blank=True)
    escalation_minutes = models.PositiveSmallIntegerField(
        default=15,
        help_text="How long a safety alert may wait unacknowledged before escalation.",
    )
    working_hours_start = models.TimeField(default=datetime.time(7, 0))
    working_hours_end = models.TimeField(default=datetime.time(17, 0))
    alert_retention_months = models.PositiveSmallIntegerField(
        default=12,
        help_text=(
            "DRAFT — proposal awaiting approval. Months after an alert is closed before its "
            "masked excerpt is erased. The minimal record (category, dates, outcome) stays. "
            "0 keeps excerpts indefinitely."
        ),
    )

    # ── Public widget (Module 3) ─────────────────────────────────────────────
    widget_enabled = models.BooleanField(
        default=False,
        help_text=(
            "The chat bubble on the public website. Needs the global and public "
            "switches as well, and the stop switch off."
        ),
    )
    widget_opening_en = models.TextField(
        blank=True,
        default=DEFAULT_WIDGET_OPENING_EN,
        help_text="The first message the widget shows in English.",
    )
    widget_opening_sw = models.TextField(
        blank=True,
        default=DEFAULT_WIDGET_OPENING_SW,
        help_text="The first message the widget shows in Kiswahili.",
    )
    widget_max_message_chars = models.PositiveSmallIntegerField(
        default=1000,
        help_text="Messages longer than this are refused before any provider call.",
    )
    widget_feedback_enabled = models.BooleanField(
        default=True, help_text="Ask the visitor whether each answer helped."
    )
    widget_pow_enabled = models.BooleanField(
        default=True,
        help_text=(
            "A short proof-of-work check when a conversation starts. A real phone "
            "spends a fraction of a second; a bot paying for many attempts does not."
        ),
    )

    # ── Handoff ──────────────────────────────────────────────────────────────
    handoff_email = models.EmailField(
        blank=True,
        help_text="Where the assistant sends conversations that need a human.",
    )

    # Service-level targets, in minutes after the handoff is created.
    sla_normal_minutes = models.PositiveIntegerField(
        default=480, help_text="Normal priority target. 480 minutes is one working day."
    )
    sla_urgent_minutes = models.PositiveIntegerField(
        default=240, help_text="Urgent priority target."
    )
    sla_critical_minutes = models.PositiveIntegerField(
        default=15, help_text="Critical priority target. Critical items are never dropped."
    )
    sla_reminder_percent = models.PositiveSmallIntegerField(
        default=75,
        help_text="Remind the assignee when this percentage of the target has passed.",
    )

    # ── Retention (DRAFT until the school approves) ──────────────────────────
    conversation_retention_days = models.PositiveSmallIntegerField(
        default=90,
        help_text=(
            "DRAFT — awaiting approval. Days after the last activity before a chat "
            "with no handoff is deleted. Only daily counts are kept."
        ),
    )
    handoff_retention_months = models.PositiveSmallIntegerField(
        default=12,
        help_text=(
            "DRAFT — awaiting approval. Months after resolution before a handoff "
            "conversation, its notes and packet are deleted."
        ),
    )
    callback_retention_days = models.PositiveSmallIntegerField(
        default=30,
        help_text=(
            "DRAFT — awaiting approval. Days before a callback name and contact "
            "details are deleted if the callback is not completed."
        ),
    )
    feedback_reason_retention_days = models.PositiveSmallIntegerField(
        default=90,
        help_text=(
            "DRAFT — awaiting approval. Days before the free-text reason on feedback "
            "and problem reports is deleted. The rating itself stays."
        ),
    )

    # ── Consent gate ─────────────────────────────────────────────────────────
    # The wording in apps/assistant/consent.py is DRAFT. No user-facing screen
    # may show it until an admin records approval with a name and a date here.
    consent_approved = models.BooleanField(default=False)
    consent_approved_by = models.CharField(max_length=120, blank=True)
    consent_approved_on = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "assistant settings"

    def __str__(self) -> str:
        return "Assistant settings"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete(WIDGET_GATE_CACHE_KEY)

    @classmethod
    def load(cls) -> AssistantSettings:
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj

    @classmethod
    def read(cls) -> AssistantSettings:
        """Read-only view for page-load paths: never writes, never creates the row."""
        return cls.objects.filter(pk=cls.SINGLETON_PK).first() or cls()

    @property
    def has_safeguarding_contact(self) -> bool:
        return bool(
            self.safeguarding_contact_name.strip()
            and (self.safeguarding_contact_phone.strip() or self.safeguarding_contact_email.strip())
        )

    @property
    def consent_ready(self) -> bool:
        return bool(
            self.consent_approved
            and self.consent_approved_by.strip()
            and self.consent_approved_on
        )

    def within_working_hours(self, moment=None) -> bool:
        moment = moment or timezone.localtime()
        current = moment.time()
        start, end = self.working_hours_start, self.working_hours_end
        if start == end:
            return False
        if start < end:
            return start <= current < end
        return current >= start or current < end


class ProviderConfig(BaseModel):
    """One configured AI provider. The key is ciphertext plus its last four."""

    class Kind(models.TextChoices):
        FAKE = "fake", "Fake provider (development and tests)"
        OPENAI = "openai", "OpenAI"
        DEEPSEEK = "deepseek", "DeepSeek"
        GEMINI = "gemini", "Google Gemini"
        OPENAI_COMPATIBLE = "openai_compatible", "OpenAI-compatible server"

    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.FAKE)
    model_name = models.CharField(max_length=120, blank=True, help_text="Main chat model.")
    fast_model_name = models.CharField(
        max_length=120, blank=True, help_text="Cheaper model for simple questions and routing."
    )
    embedding_model_name = models.CharField(
        max_length=120, blank=True, help_text="Embedding model. Changing it requires re-indexing."
    )
    base_url = models.URLField(
        blank=True,
        help_text="Required for OpenAI-compatible servers; optional otherwise.",
    )
    api_key_ciphertext = models.TextField(blank=True, editable=False)
    api_key_last4 = models.CharField(max_length=4, blank=True, editable=False)
    is_active = models.BooleanField(default=True)
    timeout_seconds = models.PositiveSmallIntegerField(default=30)

    # Fallback order: lower number is tried earlier.
    priority = models.PositiveSmallIntegerField(
        default=100, help_text="Lower is tried earlier in the fallback list."
    )

    # Prices are entered in USD per million tokens, as providers publish them.
    input_usd_per_1m = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    output_usd_per_1m = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    embedding_usd_per_1m = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    monthly_budget_ksh = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional ceiling for this provider alone. Blank means only the site-wide budget applies.",
    )
    region_note = models.CharField(
        max_length=200,
        blank=True,
        help_text="Where this provider processes data, as stated in its documentation.",
    )

    class Meta:
        ordering = ["priority", "name"]

    def __str__(self) -> str:
        return self.name

    @property
    def has_key(self) -> bool:
        return bool(self.api_key_last4)

    @property
    def masked_key(self) -> str:
        if not self.has_key:
            return "Not set"
        return f"\u2022\u2022\u2022\u2022 {self.api_key_last4}"


class UsageEvent(BaseModel):
    """One gateway attempt. Safe to read: never a key or a prompt."""

    class Source(models.TextChoices):
        ADMIN_TEST = "admin_test", "Admin connection test or test console"
        CHAT = "chat", "Assistant conversation"
        INGESTION = "ingestion", "Knowledge document reading"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        ERROR = "error", "Error"
        BLOCKED = "blocked", "Blocked before the provider was called"

    class Profile(models.TextChoices):
        CONSOLE = "console", "Admin test console"
        PUBLIC = "public", "Public assistant"
        SIGNED_IN = "signed_in", "Signed-in assistant"
        INGESTION = "ingestion", "Knowledge ingestion"

    provider = models.ForeignKey(
        ProviderConfig,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="usage_events",
    )
    provider_label = models.CharField(max_length=100, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assistant_usage_events",
    )
    source = models.CharField(max_length=20, choices=Source.choices)
    profile = models.CharField(max_length=20, choices=Profile.choices, default=Profile.CONSOLE)
    model_name = models.CharField(max_length=120, blank=True)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    estimated_cost_ksh = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    detail = models.CharField(
        max_length=200,
        blank=True,
        help_text="A short, safe explanation. Never a key or a user's message.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.source} / {self.provider_label or 'unknown'} / {self.outcome}"


class CustomGuardrailRule(BaseModel):
    """
    A rule an admin ADDED. Mandatory rules are code, not rows, so they cannot be
    edited or deleted here. Deactivating a custom rule keeps the row for history.
    """

    class Category(models.TextChoices):
        SELF_HARM = "self_harm", "Self-harm or suicide"
        ABUSE = "abuse", "Abuse or exploitation"
        NEGLECT = "neglect", "Neglect or no safe home"
        THREAT = "threat", "Threat or weapon"
        MEDICAL = "medical", "Medical emergency"
        OTHER = "other", "Other concern"

    name = models.CharField(max_length=120)
    category = models.CharField(max_length=20, choices=Category.choices)
    patterns = models.TextField(help_text="One phrase per line. Matched on word boundaries.")
    note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def pattern_list(self) -> list[str]:
        return [line.strip() for line in self.patterns.splitlines() if line.strip()]


class SafeguardingAlert(BaseModel):
    """One safeguarding hit. Kept until acknowledged and closed by a person."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        CLOSED = "closed", "Closed"

    profile = models.CharField(max_length=20, choices=UsageEvent.Profile.choices)
    category = models.CharField(max_length=40)
    rule_id = models.CharField(max_length=80, blank=True)
    excerpt = models.CharField(
        max_length=300,
        blank=True,
        help_text="Masked excerpt only. Never the full message.",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assistant_safeguarding_alerts",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assistant_alerts_acknowledged",
    )
    escalated_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assistant_alerts_closed",
    )
    outcome = models.TextField(
        blank=True, help_text="What was done, recorded when a person closes the alert."
    )
    excerpt_erased_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.category} alert ({self.status})"

    @property
    def category_label(self) -> str:
        from .alerts import category_label

        return category_label(self.category)

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN

    @property
    def is_closed(self) -> bool:
        return self.status == self.Status.CLOSED


class AssistantJobFailure(BaseModel):
    """An assistant background job that failed. Visible in the admin screens."""

    task_name = models.CharField(max_length=120)
    reason = models.CharField(max_length=300)
    attempts = models.PositiveSmallIntegerField(default=1)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.task_name} failed ({self.attempts} attempt(s))"


