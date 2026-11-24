from __future__ import annotations

import datetime
from datetime import timedelta
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from ._base import BaseModel, roles_key, slugify
from .settings_models import AssistantSettings
from .knowledge import KnowledgeDocument

# ── Public assistant and handoff (Module 3) ─────────────────────────────────

STAFF_ROLE_CODES = ("admin", "director", "teacher", "finance")


def _pipe(values) -> str:
    """Canonical pipe-wrapped list: |a|b|. Empty stays empty."""
    kept: list[str] = []
    for value in values or []:
        value = (value or "").strip()
        if value and value not in kept:
            kept.append(value)
    return "|" + "|".join(kept) + "|" if kept else ""


def _unpipe(value: str) -> list[str]:
    return [part for part in (value or "").strip("|").split("|") if part]


def within_time_window(start, end, moment=None) -> bool:
    """
    True when *moment* falls inside start..end, treating a window that crosses
    midnight as continuous (22:00 to 06:00). ``start == end`` means never.
    """
    moment = moment or timezone.localtime()
    current = moment.time()
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


class HandoffPriority(models.TextChoices):
    NORMAL = "normal", "Normal"
    URGENT = "urgent", "Urgent"
    CRITICAL = "critical", "Critical"


class HandoffTeam(BaseModel):
    """A group of staff a conversation can be routed to."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="assistant_handoff_teams",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.name)[:110] or "team"
        candidate = base
        index = 2
        while HandoffTeam.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base}-{index}"
            index += 1
        return candidate


class HandoffRuleQuerySet(models.QuerySet):
    def delete(self):
        if self.filter(is_required=True).exists():
            raise ValidationError(
                "A mandatory safeguarding handoff rule cannot be deleted."
            )
        return super().delete()

    def active(self):
        return self.filter(enabled=True)


class HandoffRule(BaseModel):
    """
    An admin-defined routing rule. A rule fires when any of its triggers match;
    ordering and combination logic lives in the handoff service.

    A rule marked ``is_required`` is the safeguarding route. It must stay
    enabled, must have a ``builtin_code``, and cannot be deleted — by an
    instance or through a queryset.
    """

    class Topic(models.TextChoices):
        FEE_DISPUTE = "fee_dispute", "Fee dispute or payment claim"
        COMPLAINT = "complaint", "Complaint about the school or staff"
        BULLYING = "bullying", "Bullying or a child unsafe at school"
        MEDICAL = "medical", "Medical or injury"
        LEGAL_THREAT = "legal_threat", "Legal threat, police or court"
        ADMISSIONS = "admissions", "Admissions interview or visit"
        TECHNICAL = "technical", "Technical problem (login, portal, app)"
        RECORDS = "records", "A request about a specific pupil's records"

    class Signal(models.TextChoices):
        ASKS_FOR_PERSON = "asks_for_person", "The person asks for a human"
        REPEATED_UNHELPFUL = "repeated_unhelpful", "Repeated unhelpful answers"
        LOW_CONFIDENCE = "low_confidence", "Low retrieval confidence"
        NEGATIVE_SENTIMENT = "negative_sentiment", "Strong negative sentiment"
        KEYWORD = "keyword", "Keywords listed below"

    class DestinationType(models.TextChoices):
        PERSON = "person", "One person"
        TEAM = "team", "A team"
        ROLE = "role", "Everyone with a role"

    class Channel(models.TextChoices):
        IN_APP = "in_app", "In-app notification"
        EMAIL = "email", "Email (needs server mail settings)"
        SMS = "sms", "SMS (not configured)"
        WHATSAPP = "whatsapp", "WhatsApp (not configured)"

    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    is_required = models.BooleanField(
        default=False,
        help_text="The safeguarding route. Cannot be deleted or switched off.",
    )
    builtin_code = models.CharField(
        max_length=60,
        unique=True,
        null=True,
        blank=True,
        help_text="Stable code for a required rule, so re-seeding never duplicates it.",
    )
    priority = models.CharField(
        max_length=10, choices=HandoffPriority.choices, default=HandoffPriority.NORMAL
    )

    # ── Triggers ─────────────────────────────────────────────────────────────
    topics = models.CharField(
        max_length=200,
        blank=True,
        help_text="Pipe-wrapped topic codes. Any one may fire the rule.",
    )
    signals = models.CharField(
        max_length=200,
        blank=True,
        help_text="Pipe-wrapped signal codes. Any one may fire the rule.",
    )
    keywords = models.TextField(
        blank=True, help_text="One term per line. Matched case-insensitively."
    )
    any_director = models.BooleanField(
        default=False, help_text="Fire when the writer is the director."
    )
    specific_roles = models.CharField(
        max_length=200,
        blank=True,
        help_text="Pipe-wrapped role codes; any message from that role fires the rule.",
    )
    specific_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="assistant_handoff_rules",
        help_text="Named accounts whose messages fire the rule.",
    )

    # ── Destination and fallback ─────────────────────────────────────────────
    destination_type = models.CharField(
        max_length=10,
        choices=DestinationType.choices,
        default=DestinationType.TEAM,
    )
    destination_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    destination_team = models.ForeignKey(
        HandoffTeam,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    destination_role = models.CharField(max_length=20, blank=True)

    fallback_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    fallback_team = models.ForeignKey(
        HandoffTeam,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    fallback_role = models.CharField(max_length=20, blank=True)

    # ── Hours, wording and service level ─────────────────────────────────────
    working_hours_start = models.TimeField(
        default=datetime.time(8, 0), help_text="Nairobi time."
    )
    working_hours_end = models.TimeField(
        default=datetime.time(17, 0),
        help_text="Nairobi time. Set start and end equal for always out of hours.",
    )
    outside_hours_message_en = models.TextField(
        blank=True, help_text="What the assistant says outside working hours."
    )
    outside_hours_message_sw = models.TextField(blank=True)
    response_target_minutes = models.PositiveIntegerField(
        default=0,
        help_text="This rule's target in minutes. 0 uses the priority default.",
    )
    channels = models.CharField(
        max_length=60,
        default="|in_app|",
        help_text="Pipe-wrapped channel codes. in_app is always used.",
    )
    order = models.PositiveSmallIntegerField(
        default=100, help_text="Lower is checked earlier when several rules match."
    )

    objects = HandoffRuleQuerySet.as_manager()

    class Meta:
        ordering = ["order", "name"]
        indexes = [
            models.Index(fields=["enabled", "order"]),
            models.Index(fields=["priority"]),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.topics = _pipe(_unpipe(self.topics))
        self.signals = _pipe(_unpipe(self.signals))
        self.channels = _pipe(_unpipe(self.channels) or [self.Channel.IN_APP])
        self.specific_roles = roles_key(self.specific_roles)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_required:
            raise ValidationError(
                "A mandatory safeguarding handoff rule cannot be deleted."
            )
        return super().delete(*args, **kwargs)

    @property
    def topic_codes(self) -> list[str]:
        return _unpipe(self.topics)

    @property
    def signal_codes(self) -> list[str]:
        return _unpipe(self.signals)

    @property
    def channel_codes(self) -> list[str]:
        return _unpipe(self.channels) or [self.Channel.IN_APP]

    @property
    def keyword_list(self) -> list[str]:
        return [line.strip() for line in self.keywords.splitlines() if line.strip()]

    @property
    def destination_label(self) -> str:
        if self.destination_type == self.DestinationType.PERSON and self.destination_user:
            return self.destination_user.get_full_name() or self.destination_user.username
        if self.destination_type == self.DestinationType.TEAM and self.destination_team:
            return self.destination_team.name
        if self.destination_type == self.DestinationType.ROLE and self.destination_role:
            labels = dict(self._role_choices())
            return labels.get(self.destination_role, self.destination_role)
        return "Not set"

    @staticmethod
    def _role_choices() -> list[tuple[str, str]]:
        from apps.accounts.models import User

        return [(code, label) for code, label in User.Role.choices]

    @staticmethod
    def _role_choices_values() -> list[str]:
        from apps.accounts.models import User

        return list(User.Role.values)

    def has_any_trigger(self) -> bool:
        return bool(
            self.topic_codes
            or self.signal_codes
            or self.keyword_list
            or self.any_director
            or self.specific_roles
            or (self.pk and self.specific_users.exists())
        )

    def user_matches(self, user) -> bool:
        if not user:
            return False
        if self.any_director and getattr(user, "role", "") == "director":
            return True
        if _unpipe(self.specific_roles) and getattr(user, "role", "") in _unpipe(
            self.specific_roles
        ):
            return True
        if self.pk and self.specific_users.filter(pk=user.pk).exists():
            return True
        return False

    def within_working_hours(self, moment=None) -> bool:
        return within_time_window(self.working_hours_start, self.working_hours_end, moment)

    def clean(self):
        errors: dict[str, str] = {}
        for code in self.topic_codes:
            if code not in self.Topic.values:
                errors["topics"] = f"Unknown topic “{code}”."
        for code in self.signal_codes:
            if code not in self.Signal.values:
                errors["signals"] = f"Unknown signal “{code}”."
        for code in self.channel_codes:
            if code not in self.Channel.values:
                errors["channels"] = f"Unknown channel “{code}”."

        if self.Signal.KEYWORD in self.signal_codes and not self.keyword_list:
            errors["keywords"] = "Add at least one keyword, one per line."

        destination_fields = {
            self.DestinationType.PERSON: self.destination_user_id,
            self.DestinationType.TEAM: self.destination_team_id,
            self.DestinationType.ROLE: self.destination_role,
        }
        if not destination_fields.get(self.destination_type):
            errors["destination_type"] = "Choose who receives this handoff."
        for kind, value in destination_fields.items():
            if kind != self.destination_type and value:
                errors["destination_type"] = (
                    "Choose one destination only (a person, a team or a role)."
                )

        if self.destination_role and self.destination_role not in STAFF_ROLE_CODES:
            errors["destination_role"] = "Choose one of the staff roles."

        fallbacks = [self.fallback_user_id, self.fallback_team_id, self.fallback_role]
        if sum(1 for value in fallbacks if value) > 1:
            errors["fallback_role"] = "Choose at most one fallback destination."
        if self.fallback_role and self.fallback_role not in STAFF_ROLE_CODES:
            errors["fallback_role"] = "Choose one of the staff roles."

        if self.specific_roles:
            unknown = [
                code
                for code in _unpipe(self.specific_roles)
                if code not in self._role_choices_values()
            ]
            if unknown:
                errors["specific_roles"] = f"Unknown role “{unknown[0]}”."

        if not self.has_any_trigger():
            errors["topics"] = (
                "Give the rule at least one trigger: a topic, a signal, keywords "
                "or a user type."
            )

        if self.is_required:
            if not self.builtin_code:
                errors["builtin_code"] = "A required rule needs a stable code."
            if not self.enabled:
                errors["enabled"] = "A mandatory safeguarding rule cannot be switched off."
        elif self.builtin_code:
            errors["builtin_code"] = "Only a required rule carries a built-in code."

        if errors:
            raise ValidationError(errors)

    def response_target_for(self, settings_row=None) -> int:
        """Minutes allowed before this handoff is late."""
        if self.response_target_minutes:
            return self.response_target_minutes
        settings_row = settings_row or AssistantSettings.read()
        return {
            HandoffPriority.NORMAL: settings_row.sla_normal_minutes,
            HandoffPriority.URGENT: settings_row.sla_urgent_minutes,
            HandoffPriority.CRITICAL: settings_row.sla_critical_minutes,
        }.get(self.priority, settings_row.sla_normal_minutes)


class SuggestedQuestion(BaseModel):
    """An admin-editable suggestion shown when the widget opens."""

    text_en = models.CharField(max_length=120)
    text_sw = models.CharField(max_length=120, blank=True)
    prompt = models.CharField(
        max_length=300,
        blank=True,
        help_text="What is sent when clicked. Blank uses the English text.",
    )
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "pk"]

    def __str__(self) -> str:
        return self.text_en

    @property
    def prompt_text(self) -> str:
        return (self.prompt or self.text_en).strip()


class ChatConversation(BaseModel):
    """
    One visitor's conversation.

    ``visitor_key`` is a SHA-256 hash of the signed visitor cookie, never the
    cookie itself, and no raw IP address is stored. That keeps enough to
    continue a conversation and to delete it on request, without keeping an
    address that identifies a household.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    visitor_key = models.CharField(
        max_length=64, db_index=True, help_text="SHA-256 of the signed visitor cookie."
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    language = models.CharField(max_length=8, default="en")
    last_activity_at = models.DateTimeField(default=timezone.now)
    message_count = models.PositiveIntegerField(default=0)
    is_test = models.BooleanField(default=False)

    class Meta:
        ordering = ["-last_activity_at"]
        indexes = [
            models.Index(fields=["visitor_key", "-last_activity_at"]),
            models.Index(fields=["status", "-last_activity_at"]),
        ]

    def __str__(self) -> str:
        return f"Conversation {self.pk or 'new'} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN


class ChatMessage(BaseModel):
    """
    One turn in a conversation.

    ``body`` is what the person wrote (shown back to them and to an assigned
    staff member). ``body_masked`` is exactly what was sent to the provider,
    and is empty when no provider call happened.
    """

    class Role(models.TextChoices):
        VISITOR = "visitor", "Visitor"
        ASSISTANT = "assistant", "Assistant"
        STAFF = "staff", "School staff"
        SYSTEM = "system", "System notice"

    conversation = models.ForeignKey(
        ChatConversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    body = models.TextField()
    body_masked = models.TextField(
        blank=True, help_text="The exact text sent to the provider, if any."
    )
    language = models.CharField(max_length=8, blank=True)
    citations = models.JSONField(
        default=list,
        blank=True,
        help_text="Source documents and passages used, with their scores.",
    )
    model_name = models.CharField(max_length=120, blank=True)
    provider_label = models.CharField(max_length=100, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    blocked_reason = models.CharField(
        max_length=120,
        blank=True,
        help_text="Why no answer was generated, in plain words.",
    )
    is_streamed = models.BooleanField(default=False)
    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who wrote it, when the role is staff.",
    )

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [models.Index(fields=["conversation", "created_at"])]

    def __str__(self) -> str:
        return f"{self.role} message {self.pk or 'new'}"


class AnswerFeedback(BaseModel):
    """A rating or a problem report for one assistant answer."""

    class Kind(models.TextChoices):
        RATING = "rating", "Helpful rating"
        REPORT = "report", "Problem report"

    class Rating(models.TextChoices):
        HELPFUL = "helpful", "Helpful"
        NOT_HELPFUL = "not_helpful", "Not helpful"

    message = models.ForeignKey(
        ChatMessage, on_delete=models.CASCADE, related_name="feedback"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    rating = models.CharField(max_length=12, choices=Rating.choices, blank=True)
    reason = models.TextField(
        blank=True,
        help_text="Optional free text. Deleted after the retention period.",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("message", "kind")]
        indexes = [models.Index(fields=["kind", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.kind} for message {self.message_id}"

    def clean(self):
        if self.kind == self.Kind.RATING and not self.rating:
            raise ValidationError({"rating": "Choose helpful or not helpful."})
        if self.kind == self.Kind.REPORT and self.rating:
            raise ValidationError({"rating": "A problem report does not carry a rating."})


class CallbackRequest(BaseModel):
    """Optional name and contact details left so a person can call back."""

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    conversation = models.ForeignKey(
        ChatConversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="callbacks",
    )
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    purpose = models.TextField(
        help_text="The exact purpose statement shown when the details were left."
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self) -> str:
        return f"Callback for {self.name}"

    def clean(self):
        if not (self.phone.strip() or self.email.strip()):
            raise ValidationError(
                {"phone": "Give a phone number or an email address so the school can reply."}
            )


class Handoff(BaseModel):
    """
    A conversation passed to a human. ``packet`` is frozen when the handoff is
    created so the person sees the conversation, sources and rule exactly as
    they were, even if the visitor keeps typing or a document changes later.
    """

    class Status(models.TextChoices):
        NEW = "new", "New"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        WAITING = "waiting", "Waiting"
        RESOLVED = "resolved", "Resolved"

    conversation = models.OneToOneField(
        ChatConversation,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="handoff",
    )
    signed_in_conversation = models.OneToOneField(
        "SignedInConversation",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="handoff",
    )
    rule = models.ForeignKey(
        HandoffRule, on_delete=models.PROTECT, related_name="handoffs"
    )
    priority = models.CharField(
        max_length=10, choices=HandoffPriority.choices, default=HandoffPriority.NORMAL
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW)
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assistant_handoffs",
    )
    assigned_team = models.ForeignKey(
        HandoffTeam,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="handoffs",
    )
    destination_role = models.CharField(
        max_length=20, blank=True, help_text="Role snapshot from the rule that fired."
    )
    packet = models.JSONField(
        default=dict,
        blank=True,
        help_text="Frozen snapshot: conversation, sources and the rule that fired.",
    )
    sla_due_at = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    resolution_note = models.TextField(blank=True)
    feedback_rating = models.CharField(
        max_length=12, choices=AnswerFeedback.Rating.choices, blank=True
    )
    feedback_at = models.DateTimeField(null=True, blank=True)
    is_test = models.BooleanField(
        default=False, help_text="Created from a test tool; never a real visitor."
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "priority", "-created_at"]),
            models.Index(fields=["assigned_user", "status"]),
            models.Index(fields=["assigned_team", "status"]),
            models.Index(fields=["sla_due_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(conversation__isnull=False) & models.Q(signed_in_conversation__isnull=True))
                    | (models.Q(conversation__isnull=True) & models.Q(signed_in_conversation__isnull=False))
                ),
                name="assistant_handoff_exactly_one_conversation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_priority_display()} handoff {self.pk or 'new'}"

    @property
    def is_resolved(self) -> bool:
        return self.status == self.Status.RESOLVED

    @property
    def is_critical(self) -> bool:
        return self.priority == HandoffPriority.CRITICAL

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.sla_due_at and not self.is_resolved and timezone.now() > self.sla_due_at
        )


class HandoffNote(BaseModel):
    """An internal note. Never sent to the visitor and never in the widget."""

    handoff = models.ForeignKey(
        Handoff, on_delete=models.CASCADE, related_name="notes"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    body = models.TextField()

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"Note on handoff {self.handoff_id}"

    def clean(self):
        if not self.body.strip():
            raise ValidationError({"body": "Write the note before saving."})


class CannedReply(BaseModel):
    """An admin-editable reply staff can insert into a conversation."""

    title = models.CharField(max_length=120, unique=True)
    body = models.TextField()
    category = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "title"]

    def __str__(self) -> str:
        return self.title

    def clean(self):
        if not self.body.strip():
            raise ValidationError({"body": "Write the reply text before saving."})


class HandoffEvent(BaseModel):
    """
    What happened to a handoff. ``dedupe_key`` makes reminders and escalation
    alerts idempotent: the database refuses a second event with the same key
    for the same handoff, so a page load cannot spam the assignee.
    """

    class Kind(models.TextChoices):
        CREATED = "created", "Handoff created"
        NOTIFIED = "notified", "Destination notified"
        ASSIGNED = "assigned", "Assigned"
        REASSIGNED = "reassigned", "Reassigned"
        STATUS_CHANGED = "status_changed", "Status changed"
        STAFF_REPLY = "staff_reply", "Staff replied to the visitor"
        SLA_REMINDER = "sla_reminder", "Reminder sent"
        SLA_ESCALATED = "sla_escalated", "Escalated for a missed target"
        CHANNEL_FAILED = "channel_failed", "A channel could not be used"
        RESOLVED = "resolved", "Resolved"
        FEEDBACK_REQUESTED = "feedback_requested", "Visitor asked for feedback"
        KNOWLEDGE_DRAFT = "knowledge_draft", "Draft answer suggested"

    handoff = models.ForeignKey(
        Handoff, on_delete=models.CASCADE, related_name="events"
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    detail = models.CharField(max_length=200, blank=True)
    dedupe_key = models.CharField(
        max_length=80, blank=True, help_text="Blank means the event is always kept."
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["handoff", "dedupe_key"],
                condition=~models.Q(dedupe_key=""),
                name="assistant_handoff_event_dedupe",
            )
        ]
        indexes = [models.Index(fields=["handoff", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.kind} on handoff {self.handoff_id}"
