"""
apps/assistant/models.py
------------------------
Three tables, all additive to the new ``assistant`` app:

``AssistantSettings``  one row (pk=1) holding the on/off switch, the default
                       provider and the rules and ceilings the admin sets.
``ProviderConfig``     one row per AI provider the admin has configured. The
                       API key is stored encrypted and only its last four
                       characters are kept in the clear.
``UsageEvent``         one row per call the gateways makes, so budgets and
                       incidents have something to count later.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class AssistantSettings(BaseModel):
    """
    The assistant's single configuration row.

    Every switch starts OFF. A fresh install has the assistant switched off and
    no provider, which is also what the admin screens assume.
    """

    SINGLETON_PK = 1

    enabled = models.BooleanField(
        default=False,
        help_text="Off until an admin deliberately switches the assistant on.",
    )
    default_provider = models.ForeignKey(
        "assistant.ProviderConfig",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The provider every new conversation uses.",
    )
    house_rules = models.TextField(
        blank=True,
        help_text="Standing instructions for the assistant (tone, what it must never say).",
    )
    monthly_budget_ksh = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="0 means no spending is allowed.",
    )
    daily_message_limit = models.PositiveSmallIntegerField(
        default=20,
        help_text="Per person, per day. 0 means no messages are allowed.",
    )
    handoff_email = models.EmailField(
        blank=True,
        help_text="Where the assistant sends conversations that need a human.",
    )

    class Meta:
        verbose_name_plural = "assistant settings"

    def __str__(self) -> str:
        return "Assistant settings"

    @classmethod
    def load(cls) -> AssistantSettings:
        """Return the singleton row, creating an empty one if none exists."""
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj


class ProviderConfig(BaseModel):
    """
    One configured AI provider.

    ``kind`` names the adapter the gateway builds. In this build only the fake
    provider exists; real provider adapters arrive with the knowledge module,
    which is why the choices list stops there.
    """

    class Kind(models.TextChoices):
        FAKE = "fake", "Fake provider (development and tests)"

    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.FAKE)
    model_name = models.CharField(max_length=120, blank=True)
    base_url = models.URLField(
        blank=True,
        help_text="Used by real providers, which are not available yet.",
    )
    api_key_ciphertext = models.TextField(blank=True, editable=False)
    api_key_last4 = models.CharField(max_length=4, blank=True, editable=False)
    is_active = models.BooleanField(default=True)
    timeout_seconds = models.PositiveSmallIntegerField(default=30)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def has_key(self) -> bool:
        return bool(self.api_key_last4)

    @property
    def masked_key(self) -> str:
        """What every screen shows: never the key itself."""
        if not self.has_key:
            return "Not set"
        return f"\u2022\u2022\u2022\u2022 {self.api_key_last4}"


class UsageEvent(BaseModel):
    """One gateway call. Safe to read: it never contains a key or a prompt."""

    class Source(models.TextChoices):
        ADMIN_TEST = "admin_test", "Admin connection test"
        CHAT = "chat", "Assistant conversation"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        ERROR = "error", "Error"

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
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    estimated_cost_ksh = models.DecimalField(max_digits=10, decimal_places=4, default=0)
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
