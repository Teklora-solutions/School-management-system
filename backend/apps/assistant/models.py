"""
apps/assistant/models.py
------------------------
The assistant's own tables. Everything here is additive to the ``assistant``
app; no existing app is modified.

Configuration
  AssistantSettings   one row (pk=1): switches, providers, rules, budgets,
                      limits, the safeguarding contact and the consent gate.
  ProviderConfig      one row per provider: models, encrypted key, prices,
                      fallback order and the data-region note.

Ledger and safety
  UsageEvent          one row per gateway attempt: tokens, cost, latency,
                      outcome. Never contains a key or message text.
  SafeguardingAlert   one row per safeguarding hit. Acknowledged by an admin
                      or director; escalated when overdue. Never deleted
                      automatically.
  CustomGuardrailRule rules an admin ADDS. The mandatory pack lives in code
                      (``guardrails/rules.py``) and has no database rows, so it
                      cannot be edited or removed from any screen.
  AssistantJobFailure assistant background jobs that failed, with the reason
                      and attempt count. Existing jobs keep their behaviour.

Knowledge base (Module 2)
  KnowledgeCollection one shelf of documents (Admissions, Fees, …) with the
                      broadest audience its documents may have.
  KnowledgeDocument   one source document: a private file, a URL, pasted text
                      or a set of Q&A pairs. Only Approved documents in
                      Approved collections are ever retrievable.
  KnowledgeDocumentVersion  a snapshot taken when the title or source changes.
  KnowledgeQAPair     one question and its approved answer inside a FAQ
                      document.
  KnowledgeChunk      one readable passage of an Approved document, with its
                      embedding. Replaced as a set; never edited in place.
  KnowledgeIngestRun  one reading attempt of a document: what happened, how
                      many chunks and tokens, and the plain reason if it failed.
  KnowledgeGap        a question the assistant could not answer reliably, kept
                      in masked form so the school can add the missing wording.

Public assistant and handoff (Module 3)
  HandoffTeam         a group of staff a conversation can be routed to.
  HandoffRule         an admin-defined routing rule: triggers, priority,
                      destination, working hours, out-of-hours wording and SLA.
                      Rules marked required (the safeguarding route) cannot be
                      deleted or switched off.
  SuggestedQuestion   an admin-editable opening suggestion in the widget.
  ChatConversation    one visitor's conversation, keyed by a hash of the signed
                      visitor cookie. No raw IP is stored here.
  ChatMessage         one turn: what the visitor wrote, what was sent to the
                      provider (masked), citations and usage.
  AnswerFeedback      helpful / not helpful, or a problem report, for one answer.
  CallbackRequest     optional name and phone/email left for a return call.
  Handoff             a conversation passed to a person or team: priority,
                      status, assignee, SLA deadline and a frozen packet.
  HandoffNote         an internal note on a handoff. Never shown to the visitor.
  CannedReply         an admin-editable reply staff can insert.
  HandoffEvent        an audit trail of handoff actions; ``dedupe_key`` stops a
                      reminder or alert being sent twice.
"""

from __future__ import annotations

import datetime
import re

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import BaseModel

from .knowledge.roles import roles_key
from .knowledge.storage import knowledge_storage, knowledge_upload_path
from .knowledge.validators import validate_knowledge_file

# Editable defaults for the public widget. These are a friendly opening, not the
# consent wording: consent is read through ``consent.visible_consent()`` only.
DEFAULT_WIDGET_OPENING_EN = (
    "Hello. I'm the school's AI assistant. I answer from the school's approved "
    "information. I'm not a person — you can ask to talk to one at any time."
)
DEFAULT_WIDGET_OPENING_SW = (
    "Habari. Mimi ni msaidizi wa AI wa shule. Najibu kutoka taarifa zilizoidhinishwa "
    "na shule. Mimi si mtu — unaweza kuomba kuzungumza na mtu wakati wowote."
)

# Whether the public site should render the widget at all. Public pages are read
# by everyone, so the answer is cached for a minute and cleared whenever these
# settings are saved (``AssistantSettings.save``).
WIDGET_GATE_CACHE_KEY = "assistant:widget_gate"
WIDGET_GATE_SECONDS = 60


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


# ── Knowledge base (Module 2) ────────────────────────────────────────────────


class KnowledgeAudience(models.TextChoices):
    PUBLIC = "public", "Public (anyone on the website)"
    SIGNED_IN = "signed_in", "Signed-in users"


class KnowledgeStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    APPROVED = "approved", "Approved"
    ARCHIVED = "archived", "Archived"


class KnowledgeCollection(BaseModel):
    """
    A shelf of documents. Its audience is a ceiling: a document on this shelf
    may be narrower (for example signed-in only on a public shelf) but never
    broader.
    """

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    audience = models.CharField(
        max_length=20,
        choices=KnowledgeAudience.choices,
        default=KnowledgeAudience.SIGNED_IN,
        help_text="The widest audience any document on this shelf may have.",
    )
    roles = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Canonical role list such as |parent|teacher|. Empty means every "
            "signed-in role. Use the checkboxes on the screen, not this raw value."
        ),
    )
    classes = models.ManyToManyField(
        "academics.SchoolClass",
        blank=True,
        related_name="assistant_knowledge_collections",
        help_text="Optional class limit. Empty means every class.",
    )
    status = models.CharField(
        max_length=20, choices=KnowledgeStatus.choices, default=KnowledgeStatus.DRAFT
    )
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The person accountable for this shelf.",
    )

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["status", "audience"])]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.roles = roles_key(self.roles)
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.name)[:120] or "collection"
        candidate = base
        index = 2
        while (
            KnowledgeCollection.objects.filter(slug=candidate)
            .exclude(pk=self.pk)
            .exists()
        ):
            candidate = f"{base}-{index}"
            index += 1
        return candidate

    def clean(self):
        errors: dict[str, str] = {}
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            errors["effective_to"] = "The end date must be on or after the start date."
        if self.audience == KnowledgeAudience.PUBLIC and self.roles:
            errors["roles"] = "A public shelf cannot be limited to specific roles."
        if errors:
            raise ValidationError(errors)

    @property
    def is_approved(self) -> bool:
        return self.status == KnowledgeStatus.APPROVED

    def effective_on(self, day: datetime.date | None = None) -> bool:
        day = day or timezone.localdate()
        if self.effective_from and day < self.effective_from:
            return False
        if self.effective_to and day > self.effective_to:
            return False
        return True

    def audience_summary(self) -> str:
        from .knowledge.scope import audience_summary

        return audience_summary(
            self.audience,
            self.roles,
            [school_class.name for school_class in self.classes.all()] if self.pk else [],
        )


class KnowledgeDocument(BaseModel):
    """
    One source document. File, URL, pasted text and FAQ are the four ways in;
    exactly the field for ``source_type`` is used.

    Retrieval (K3) only ever reads Approved documents whose collection is
    Approved and whose effective dates include today.
    """

    class SourceType(models.TextChoices):
        PDF = "pdf", "PDF file"
        DOCX = "docx", "Word document"
        TEXT = "txt", "Plain text file"
        URL = "url", "Web page"
        PASTED = "pasted", "Pasted text"
        FAQ = "faq", "Questions and answers"

    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        KISWAHILI = "sw", "Kiswahili"

    class IngestionState(models.TextChoices):
        PENDING = "pending", "Waiting to be read"
        RUNNING = "running", "Reading now"
        READY = "ready", "Ready to answer from"
        FAILED = "failed", "Could not be read"
        SKIPPED = "skipped", "Not read (draft or archived)"

    collection = models.ForeignKey(
        KnowledgeCollection, on_delete=models.PROTECT, related_name="documents"
    )
    title = models.CharField(max_length=200)
    source_type = models.CharField(max_length=12, choices=SourceType.choices)
    file = models.FileField(
        upload_to=knowledge_upload_path,
        storage=knowledge_storage,
        blank=True,
        null=True,
        validators=validate_knowledge_file,
    )
    source_url = models.URLField(blank=True)
    body_text = models.TextField(
        blank=True,
        help_text=(
            "Pasted text, the introduction of a questions-and-answers document, "
            "or the text fetched from a web page."
        ),
    )
    language = models.CharField(
        max_length=8, choices=Language.choices, default=Language.ENGLISH
    )
    audience = models.CharField(
        max_length=20,
        choices=KnowledgeAudience.choices,
        default=KnowledgeAudience.SIGNED_IN,
    )
    roles = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Canonical role list such as |parent|. Empty means every signed-in "
            "role. Use the checkboxes on the screen, not this raw value."
        ),
    )
    classes = models.ManyToManyField(
        "academics.SchoolClass",
        blank=True,
        related_name="assistant_knowledge_documents",
        help_text="Optional class limit. Empty means every class.",
    )
    status = models.CharField(
        max_length=20, choices=KnowledgeStatus.choices, default=KnowledgeStatus.DRAFT
    )
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    version = models.PositiveIntegerField(default=1)
    content_sha256 = models.CharField(max_length=64, blank=True, editable=False)
    file_sha256 = models.CharField(max_length=64, blank=True, editable=False)
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicates",
        help_text="An earlier document with the same file or pasted content.",
    )
    is_stale = models.BooleanField(
        default=False,
        help_text="Set when the embedding model changed after this was indexed.",
    )
    ingestion_state = models.CharField(
        max_length=12,
        choices=IngestionState.choices,
        default=IngestionState.PENDING,
    )
    indexed_model = models.CharField(max_length=120, blank=True)
    indexed_at = models.DateTimeField(null=True, blank=True)
    chunk_count = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)

    class PIIState(models.TextChoices):
        UNSCANNED = "unscanned", "Not scanned yet"
        CLEAN = "clean", "No personal data detected"
        FOUND = "found", "Personal data detected — needs a decision"
        CONFIRMED = "confirmed", "Admin accepted sending it to the provider"

    pii_state = models.CharField(
        max_length=12, choices=PIIState.choices, default=PIIState.UNSCANNED
    )
    pii_findings = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Where personal data was detected: kind, heading and passage number. "
            "The matching value itself is never stored here."
        ),
    )
    pii_scanned_at = models.DateTimeField(null=True, blank=True)
    pii_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    pii_confirmed_at = models.DateTimeField(null=True, blank=True)
    pii_confirmation_text = models.CharField(
        max_length=300,
        blank=True,
        help_text="The exact wording the admin accepted, kept as the record of consent.",
    )

    class Meta:
        ordering = ["collection", "title"]
        indexes = [
            models.Index(fields=["status", "audience"]),
            models.Index(fields=["collection", "status"]),
            models.Index(fields=["content_sha256"]),
            models.Index(fields=["file_sha256"]),
            models.Index(fields=["ingestion_state"]),
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self):
        errors: dict[str, str] = {}
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            errors["effective_to"] = "The end date must be on or after the start date."
        if self.audience == KnowledgeAudience.PUBLIC and self.roles:
            errors["roles"] = "A public document cannot be limited to specific roles."
        if self.collection_id:
            problems = self.scope_problems()
            if problems:
                errors["audience"] = " ".join(problems)
        if errors:
            raise ValidationError(errors)

    def scope_problems(self, *, roles=None, class_ids=None) -> list[str]:
        from .knowledge import scope

        if roles is None:
            roles = self.roles
        if class_ids is None:
            class_ids = (
                list(self.classes.values_list("pk", flat=True)) if self.pk else []
            )
        return scope.scope_problems(
            audience=self.audience,
            roles=roles,
            class_ids=class_ids,
            parent_audience=self.collection.audience,
            parent_roles=self.collection.roles,
            parent_class_ids=(
                list(self.collection.classes.values_list("pk", flat=True))
                if self.collection_id
                else []
            ),
        )

    def save(self, *args, **kwargs):
        self.roles = roles_key(self.roles)
        if not self.content_sha256:
            self.content_sha256 = self._content_digest()
        super().save(*args, **kwargs)

    def _content_digest(self) -> str:
        import hashlib

        if self.source_type == self.SourceType.URL:
            payload = f"url\n{self.source_url.strip()}"
        elif self.source_type in (self.SourceType.PASTED, self.SourceType.FAQ):
            payload = f"text\n{self.body_text.strip()}"
        else:
            return ""
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def is_approved(self) -> bool:
        return self.status == KnowledgeStatus.APPROVED

    @property
    def has_file(self) -> bool:
        return bool(self.file)

    @property
    def file_name(self) -> str:
        if not self.file:
            return ""
        return re.sub(r"[^A-Za-z0-9._-]+", "-", self.file.name.rsplit("/", 1)[-1])

    @property
    def pii_blocks_embeddings(self) -> bool:
        """Personal data was found and the admin has not accepted sending it."""
        return self.pii_state == self.PIIState.FOUND and self.pii_confirmed_at is None

    def effective_on(self, day: datetime.date | None = None) -> bool:
        day = day or timezone.localdate()
        if self.effective_from and day < self.effective_from:
            return False
        if self.effective_to and day > self.effective_to:
            return False
        return True

    def audience_summary(self) -> str:
        from .knowledge.scope import audience_summary

        return audience_summary(
            self.audience,
            self.roles,
            [school_class.name for school_class in self.classes.all()] if self.pk else [],
        )


class KnowledgeDocumentVersion(BaseModel):
    """A snapshot kept when a document's title or source changes."""

    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    note = models.CharField(
        max_length=200, blank=True, help_text="Why this version was saved."
    )
    content = models.TextField(
        blank=True,
        help_text="Pasted text at this version. Empty for file and URL documents.",
    )
    source_url = models.URLField(blank=True)
    file_sha256 = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-version"]
        unique_together = [("document", "version")]

    def __str__(self) -> str:
        return f"{self.document_id} v{self.version}"


class KnowledgeQAPair(BaseModel):
    """One question and its approved answer inside a FAQ document."""

    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="qa_pairs"
    )
    question = models.CharField(max_length=300)
    answer = models.TextField()
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "pk"]

    def __str__(self) -> str:
        return self.question


class KnowledgeChunk(BaseModel):
    """
    One passage of a document and its embedding.

    The whole set is replaced together after a successful read, so a failed
    re-index never leaves a mix of old and new passages.
    """

    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="chunks"
    )
    ordinal = models.PositiveSmallIntegerField(
        help_text="Position in the document, starting at zero."
    )
    heading = models.CharField(
        max_length=300,
        blank=True,
        help_text="The heading path this passage sits under, if any.",
    )
    text = models.TextField()
    char_count = models.PositiveIntegerField(default=0)
    content_sha256 = models.CharField(max_length=64, blank=True)
    embedding = models.JSONField(
        default=list,
        help_text="The vector as a list of numbers. Portable across SQLite and PostgreSQL.",
    )
    embedding_model = models.CharField(max_length=120, blank=True)
    dimension = models.PositiveSmallIntegerField(default=0)
    embedded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["document", "ordinal"]
        unique_together = [("document", "ordinal")]
        indexes = [
            models.Index(fields=["document", "ordinal"]),
            models.Index(fields=["embedding_model"]),
        ]

    def __str__(self) -> str:
        return f"{self.document_id} #{self.ordinal}"


class KnowledgeIngestRun(BaseModel):
    """One attempt to read a document, kept so an admin can see what happened."""

    class Status(models.TextChoices):
        RUNNING = "running", "Reading now"
        READY = "ready", "Read successfully"
        FAILED = "failed", "Could not be read"
        SKIPPED = "skipped", "Not read (draft or archived)"

    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="ingest_runs"
    )
    status = models.CharField(max_length=12, choices=Status.choices)
    attempt = models.PositiveSmallIntegerField(default=1)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    chunks_written = models.PositiveIntegerField(default=0)
    tokens = models.PositiveIntegerField(default=0)
    error = models.CharField(
        max_length=300,
        blank=True,
        help_text="A plain-language reason. Never document text or a key.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["document", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.document_id} / {self.status}"


class KnowledgeGap(BaseModel):
    """
    A question the assistant could not answer reliably.

    Only the masked question and the audience shape are kept. Nothing that
    identifies the asker is stored: no name, user, IP address or session.
    Console and answer-desk gaps are marked as tests so they never pollute the
    real picture of what the school is missing.
    """

    class Reason(models.TextChoices):
        NO_MATCH = "no_match", "Nothing relevant was found"
        UNVERIFIED = "unverified", "An answer could not be verified"

    question = models.CharField(
        max_length=300, help_text="Masked question. Names, phones and emails are replaced."
    )
    role = models.CharField(
        max_length=20,
        blank=True,
        help_text="The audience role tested. Empty means a visitor.",
    )
    class_signature = models.CharField(
        max_length=120,
        blank=True,
        help_text="Sorted class ids the tested audience belonged to, comma separated.",
    )
    language_hint = models.CharField(
        max_length=8, blank=True, help_text="'en' or 'sw', a guess from the wording."
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    top_score = models.FloatField(
        null=True, blank=True, help_text="Best vector similarity at the time, for calibration."
    )
    is_test = models.BooleanField(
        default=False, help_text="Recorded from the answer desk or test console."
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    resolution_note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["resolved_at", "is_test"]),
        ]

    def __str__(self) -> str:
        return self.question[:60]

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    @property
    def audience_label(self) -> str:
        from .knowledge.roles import ROLE_LABELS

        label = ROLE_LABELS.get(self.role, "Visitor") if self.role else "Visitor"
        if self.class_signature:
            label += f" · classes {self.class_signature}"
        return label


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
        ChatConversation, on_delete=models.CASCADE, related_name="handoff"
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
