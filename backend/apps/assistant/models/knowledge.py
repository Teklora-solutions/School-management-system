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
    knowledge_storage,
    knowledge_upload_path,
    roles_key,
    slugify,
    validate_knowledge_file,
)
from .settings_models import AssistantSettings

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


