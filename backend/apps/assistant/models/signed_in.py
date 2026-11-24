from __future__ import annotations

import datetime
from datetime import timedelta
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from ._base import BaseModel


# ── Module 4 — Signed-in assistant, tools and memory ─────────────────────────


class SignedInConversation(BaseModel):
    """
    One signed-in user's chat session.  Keyed by the authenticated user; a
    new row is created for each session (or when the user starts a new chat).

    ``role_snapshot`` and ``class_ids_snapshot`` are taken at the start of
    each conversation and do not change mid-session, so a retrieval cache key
    is always consistent even if the user's class changes during the day.

    For parents, ``active_child_id`` records which child the conversation is
    about.  It can be switched mid-session; the assistant always states which
    child it is describing.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assistant_signed_in_conversations",
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN
    )
    language = models.CharField(max_length=8, default="en")
    role_snapshot = models.CharField(
        max_length=20,
        blank=True,
        help_text="Role at conversation start; cache keys depend on this.",
    )
    class_ids_snapshot = models.CharField(
        max_length=200,
        blank=True,
        help_text="Comma-separated sorted class ids at conversation start.",
    )
    active_child_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="For parents: which child this conversation is currently about.",
    )
    message_count = models.PositiveIntegerField(default=0)
    last_activity_at = models.DateTimeField(default=timezone.now)
    is_test = models.BooleanField(
        default=False,
        help_text="Created from an admin test tool; never a real user session.",
    )

    class Meta:
        db_table = "assistant_signed_in_conversation"
        ordering = ["-last_activity_at"]
        indexes = [
            models.Index(fields=["user", "-last_activity_at"]),
            models.Index(fields=["status", "-last_activity_at"]),
        ]

    def __str__(self) -> str:
        return f"SignedIn conv {self.pk} ({self.user_id}, {self.status})"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN


class SignedInMessage(BaseModel):
    """
    One turn in a signed-in conversation.

    ``body_masked`` is exactly what was sent to the provider.  Sensitive
    identifiers are stripped before anything leaves the server.
    Tool results are summarised in ``tool_summary`` (never the raw queryset).
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System notice"

    conversation = models.ForeignKey(
        SignedInConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=12, choices=Role.choices)
    body = models.TextField()
    body_masked = models.TextField(
        blank=True,
        help_text="The exact text sent to the provider, PII stripped.",
    )
    language = models.CharField(max_length=8, blank=True)
    citations = models.JSONField(
        default=list,
        blank=True,
        help_text="Knowledge-base passages used, with document and section.",
    )
    tool_summary = models.JSONField(
        default=list,
        blank=True,
        help_text="One entry per tool call: tool name, fetch time, field names returned.",
    )
    model_name = models.CharField(max_length=120, blank=True)
    provider_label = models.CharField(max_length=100, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    blocked_reason = models.CharField(max_length=120, blank=True)
    is_streamed = models.BooleanField(default=False)
    is_tutor = models.BooleanField(
        default=False,
        help_text="True when this turn ran in student-tutor mode.",
    )

    class Meta:
        db_table = "assistant_signed_in_message"
        ordering = ["created_at", "pk"]
        indexes = [models.Index(fields=["conversation", "created_at"])]

    def __str__(self) -> str:
        return f"{self.role} message {self.pk or 'new'}"


class AssistantMemory(BaseModel):
    """
    Per-user lightweight memory.  Only stores what the user agreed to:
    preferred language, preferred tone, and a short plain-text summary of
    recurring topics (written by the assistant, never raw messages).

    ``prefer_language``: 'en' or 'sw'.
    ``prefer_tone``: 'formal' | 'casual' | '' (blank = default).
    ``recurring_summary``: at most 200 chars, plain English, written and
    updated by the assistant after the user opts in.  Never contains names,
    fees, marks or other sensitive facts — only topic patterns like
    "usually asks about homework deadlines".

    The school must approve personalisation before any memory is written.
    Default OFF (opted_in=False).  The user can see the full record and
    delete it at any time.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assistant_memory",
    )
    opted_in = models.BooleanField(
        default=False,
        help_text="The user has opted into memory. Nothing is stored until this is True.",
    )
    prefer_language = models.CharField(
        max_length=8, blank=True, help_text="'en' or 'sw'."
    )
    prefer_tone = models.CharField(
        max_length=20, blank=True, help_text="'formal', 'casual', or blank for default."
    )
    recurring_summary = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Short, non-sensitive summary of recurring topics. "
            "Never stores names, fees, marks or health details."
        ),
    )
    opted_in_at = models.DateTimeField(null=True, blank=True)
    last_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "assistant_memory"

    def __str__(self) -> str:
        return f"Memory for user {self.user_id}"

    def forget(self) -> None:
        """Clear everything and opt out."""
        self.opted_in = False
        self.prefer_language = ""
        self.prefer_tone = ""
        self.recurring_summary = ""
        self.opted_in_at = None
        self.last_updated_at = None
        self.save()


class ToolCallLog(BaseModel):
    """
    Audit entry for every tool call.  Never stores the full result — only the
    tool name, the user, the conversation, the outcome, the field names
    returned and the latency.  PII is never logged here.
    """

    class Outcome(models.TextChoices):
        SUCCESS = "ok", "Success"
        DENIED = "denied", "Permission denied"
        INVALID = "invalid", "Invalid arguments"
        ERROR = "error", "Error"

    conversation = models.ForeignKey(
        SignedInConversation,
        on_delete=models.CASCADE,
        related_name="tool_calls",
    )
    tool_name = models.CharField(max_length=80, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    outcome = models.CharField(max_length=10, choices=Outcome.choices, default=Outcome.SUCCESS)
    # Only the field *names* of the result, not the values.
    fields_returned = models.JSONField(default=list, blank=True)
    # Denial or error reason — safe to log, no PII.
    reason = models.CharField(max_length=200, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "assistant_tool_call_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["tool_name", "outcome"]),
        ]

    def __str__(self) -> str:
        return f"{self.tool_name} ({self.outcome}) for user {self.actor_id}"
