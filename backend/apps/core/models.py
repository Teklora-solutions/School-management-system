"""
apps/core/models.py
-------------------
BaseModel provides created_at, updated_at timestamps on every model
that inherits from it. Optional created_by FK is added by subclasses
that need it.
"""

from django.conf import settings
from django.db import models


class BaseModel(models.Model):
    """Abstract base with automatic timestamps."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditLog(BaseModel):
    """
    Immutable record of sensitive changes (results, fees, user roles).
    Never update or delete rows in this table.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        db_index=True,
    )
    action = models.CharField(max_length=64, db_index=True)
    # Target object
    content_type = models.CharField(max_length=128)
    object_id = models.CharField(max_length=64, blank=True)
    # Human-readable diff stored as JSON
    change_summary = models.JSONField(default=dict)
    # Request metadata
    request_id = models.CharField(max_length=64, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "core_audit_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return (
            f"[{self.created_at:%Y-%m-%d %H:%M}] {self.actor} → "
            f"{self.action} on {self.content_type}#{self.object_id}"
        )


class SystemEvent(BaseModel):
    """
    A business event rendered into the admin activity feed and daily report.

    ``AuditLog`` records *what changed* for a single object and is written by
    the code that changed it. ``SystemEvent`` records *what happened* in the
    school, is allowed to aggregate (one row per invoice run, not one per
    invoice), and is safe to render to humans.

    ``type`` deliberately has no ``choices``: the registry in
    ``core.events.EVENT_TYPES`` supplies the wording, so adding a new event
    type is a code change, never a migration.
    """

    type = models.CharField(max_length=64, db_index=True)
    # Denormalised from the registry at write time so the daily report can
    # group by category in SQL instead of in Python.
    category = models.CharField(max_length=32, blank=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="system_events",
        db_index=True,
    )
    student = models.ForeignKey(
        "students.Student",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="system_events",
        db_index=True,
    )
    # Pre-rendered sentence for the feed. Empty when the registry has no
    # wording for this type yet; the feed then falls back to ``label``.
    message = models.CharField(max_length=255, blank=True)
    data = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        db_table = "core_system_event"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["type", "created_at"]),
            models.Index(fields=["student", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.type}: {self.message}"


class Notification(BaseModel):
    """
    In-app notification. Created via ``apps.core.notifications.notify()``.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    category = models.CharField(max_length=40, blank=True, db_index=True)

    class Meta:
        db_table = "core_notification"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.recipient_id}: {self.title}"


class SchoolSettings(BaseModel):
    """
    Editable school identity. A singleton: the row is always pk=1.

    Every field is blankable, and a blank field falls back to the matching
    ``SCHOOL_*`` constant in settings (see ``core.context_processors.school_settings``),
    so a fresh install still renders correctly before anyone saves this page.
    """

    SINGLETON_PK = 1

    name = models.CharField(max_length=200, blank=True)
    motto = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(max_length=254, blank=True)
    currency = models.CharField(
        max_length=8,
        blank=True,
        help_text="ISO 4217 code shown next to money amounts, e.g. GHS.",
    )
    show_positions = models.BooleanField(
        default=True,
        help_text="Show class ranks/positions on result sheets and broadsheets.",
    )
    fees_enabled = models.BooleanField(
        default=True,
        help_text=(
            "Turn off to hide fee figures everywhere and show "
            '"Available once the Finance module is activated" instead.'
        ),
    )

    # ── Public-site content ───────────────────────────────────────────────
    # These six feed the school's own website (apps/public). They are blankable
    # on purpose: a school that has not written its story yet still gets every
    # page, and the page says what is missing instead of inventing a sentence
    # about a real school. Nothing here is a fallback for an existing field —
    # name, motto, phone and email keep using the values above.
    logo = models.ImageField(
        upload_to="school/logo/",
        null=True,
        blank=True,
        help_text="Shown in the public site's masthead and footer.",
    )
    address = models.TextField(
        blank=True,
        help_text="Postal/physical address shown on the public Contact page.",
    )
    established_on = models.DateField(
        null=True,
        blank=True,
        help_text="Founding date. The public home page shows the year and the "
        "number of years that follows — never a rounded-up claim.",
    )
    about = models.TextField(
        blank=True,
        help_text="Opening paragraphs of the public About page.",
    )
    mission = models.TextField(blank=True)
    vision = models.TextField(blank=True)

    class Meta:
        db_table = "core_school_settings"
        verbose_name = "school settings"
        verbose_name_plural = "school settings"

    def __str__(self) -> str:
        return self.name or "School settings"

    @classmethod
    def load(cls) -> "SchoolSettings":
        """Return the singleton row, creating an empty one if none exists."""
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj
