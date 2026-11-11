"""
apps/core/models.py
-------------------
BaseModel provides created_at, updated_at timestamps on every model
that inherits from it. Optional created_by FK is added by subclasses
that need it.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


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

    ``deliver_after`` is set when quiet hours are active at the moment of
    creation. The bell-count query and the notification inbox filter on
    ``deliver_after <= now()`` so held notifications are invisible until the
    window ends.  ``deliver_after=None`` means deliver immediately.
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
    deliver_after = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "When set, the notification is hidden until this datetime passes. "
            "Used by quiet-hours deferral. Null means deliver immediately."
        ),
    )

    class Meta:
        db_table = "core_notification"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.recipient_id}: {self.title}"


def _validate_hour(value: int) -> None:
    """Reusable validator: 0–23 inclusive."""
    from django.core.exceptions import ValidationError

    if not (0 <= value <= 23):
        raise ValidationError(f"Hour must be between 0 and 23, got {value}.")


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
    learning_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Turn on to open the Learning module: online classrooms, lessons "
            "and quizzes for teachers, students and parents."
        ),
    )
    engagement_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Turn on to show progress bars, lesson streaks and the teacher "
            "class-progress view inside the Learning module. Requires Learning "
            "to also be on. Has no effect until you also turn it on per class "
            "in Class Engagement Settings."
        ),
    )
    quizzes_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Turn on to allow teachers to build quizzes and students to take "
            "them. Requires Learning to also be on. Each quiz can be published "
            "or kept as draft independently."
        ),
    )
    live_classes_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Turn on to allow teachers to schedule live classes (Google Meet, Zoom, Teams) "
            "and show the shared class calendar. Requires Learning to also be on."
        ),
    )
    feed_community_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Turn on the class community feed: replies, best answers, new post "
            "types, filters, live updates and lesson/quiz links. Requires "
            "Learning. Each teacher also turns it on for their own class."
        ),
    )
    notification_quiet_hours_enabled = models.BooleanField(
        default=False,
        help_text=(
            "When on, in-app notifications created during quiet hours are held "
            "back and delivered at the start of the next active window. "
            "Default off — nothing changes until the school switches this on."
        ),
    )
    quiet_hours_start = models.PositiveSmallIntegerField(
        default=20,
        validators=[_validate_hour],
        help_text=(
            "Hour (0–23, Nairobi time) when quiet hours begin. Default: 20 (8 pm). "
            "Notifications created after this hour are held until quiet_hours_end."
        ),
    )
    quiet_hours_end = models.PositiveSmallIntegerField(
        default=6,
        validators=[_validate_hour],
        help_text=(
            "Hour (0–23, Nairobi time) when quiet hours end. Default: 6 (6 am). "
            "Held notifications are delivered from this hour."
        ),
    )
    feed_teacher_starters = models.TextField(
        blank=True,
        help_text=(
            "Question starters teachers can tap when replying on the class feed, "
            "one per line. Leave blank to use the built-in set."
        ),
    )
    feed_student_starters = models.TextField(
        blank=True,
        help_text=(
            "Reply starters pupils can tap when helping a classmate, one per line. "
            "Leave blank to use the built-in set."
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

    # ── Website appearance ────────────────────────────────────────────────
    theme = models.CharField(
        max_length=20,
        blank=True,
        default="dark",
        help_text='Website colour theme: "dark" or "light".',
    )

    # ── Home page hero ────────────────────────────────────────────────────
    hero_eyebrow = models.CharField(
        max_length=120,
        blank=True,
        help_text='Small label above the headline, e.g. "Welcome to Springfield Academy".',
    )
    hero_title = models.CharField(
        max_length=200,
        blank=True,
        help_text="Main headline on the home hero. Leave blank to use the default.",
    )
    hero_title_highlight = models.CharField(
        max_length=100,
        blank=True,
        help_text="The highlighted word(s) inside the headline (shown in accent colour).",
    )
    hero_text = models.TextField(
        blank=True,
        help_text="Body text below the hero headline on the home page.",
    )

    # ── Home CTA band ─────────────────────────────────────────────────────
    cta_title = models.CharField(
        max_length=200,
        blank=True,
        help_text="Headline in the home-page call-to-action band.",
    )
    cta_text = models.TextField(
        blank=True,
        help_text="Body text in the home-page call-to-action band.",
    )

    # ── Social links ──────────────────────────────────────────────────────
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)

    # ── Footer ────────────────────────────────────────────────────────────
    footer_about = models.TextField(
        blank=True,
        help_text="Short blurb shown in the footer's brand column.",
    )

    # ── Contact page extras ───────────────────────────────────────────────
    office_hours = models.CharField(
        max_length=200,
        blank=True,
        help_text='e.g. "Monday – Friday, 8:00 AM – 4:00 PM".',
    )
    map_embed_url = models.URLField(
        blank=True,
        help_text="Google Maps embed URL shown on the Contact page.",
    )
    admissions_email = models.EmailField(
        max_length=254,
        blank=True,
        help_text="Separate admissions email shown on the Contact page.",
    )

    # ── Values card text ──────────────────────────────────────────────────
    values = models.TextField(
        blank=True,
        help_text="Our Values text shown on the About page.",
    )

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


class NotificationPreference(BaseModel):
    """
    Per-user opt-in/opt-out for notification categories.

    Created lazily (get_or_create on first use). A missing row means
    all categories are ON — consistent with the default=True values here.

    Teacher digests and safety notices are NOT here because they cannot
    be turned off by the recipient. This table is for student/parent
    social notifications only.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preference",
    )
    notify_feed_reply = models.BooleanField(
        default=True,
        help_text="Notify me when someone first replies to my feed post.",
    )
    notify_feed_best_answer = models.BooleanField(
        default=True,
        help_text="Notify me when my question gets a Best Answer.",
    )
    notify_level_up = models.BooleanField(
        default=True,
        help_text="Notify me when I reach a new level.",
    )

    class Meta:
        db_table = "core_notification_preference"

    def __str__(self) -> str:
        return f"Notification prefs for {self.user}"

    @classmethod
    def for_user(cls, user) -> "NotificationPreference":
        """Return the preference row, creating defaults if it doesn't exist yet."""
        pref, _ = cls.objects.get_or_create(user=user)
        return pref


class WorkerHeartbeat(BaseModel):
    """
    One row, written by ``apps.core.tasks.worker_heartbeat`` on a schedule.

    The readiness probe reads it so a stopped background worker is visible
    without taking the whole site down. A heartbeat means the scheduler and
    its worker are alive; it does not say anything about individual jobs.
    """

    SINGLETON_PK = 1

    last_run_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "core_worker_heartbeat"
        verbose_name = "worker heartbeat"
        verbose_name_plural = "worker heartbeats"

    def __str__(self) -> str:
        return f"worker last ran at {self.last_run_at or 'never'}"

    @classmethod
    def record(cls):
        heartbeat, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        heartbeat.last_run_at = timezone.now()
        heartbeat.save(update_fields=["last_run_at", "updated_at"])
        return heartbeat.last_run_at
