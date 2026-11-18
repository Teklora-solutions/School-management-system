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
"""

from __future__ import annotations

import datetime
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import BaseModel

from .knowledge.roles import roles_key
from .knowledge.storage import knowledge_storage, knowledge_upload_path
from .knowledge.validators import validate_knowledge_file


class AssistantSettings(BaseModel):
    """The assistant's single configuration row. Every switch starts OFF."""

    SINGLETON_PK = 1

    # ── Switches ─────────────────────────────────────────────────────────────
    enabled = models.BooleanField(
        default=False,
        help_text="Global kill switch. Off until an admin deliberately switches it on.",
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

    # ── Handoff ──────────────────────────────────────────────────────────────
    handoff_email = models.EmailField(
        blank=True,
        help_text="Where the assistant sends conversations that need a human.",
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
