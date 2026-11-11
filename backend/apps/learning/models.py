"""
apps/learning/models.py
-----------------------
L1 — the course content that lives inside a computed classroom.

  * ``CourseModule``            — a named section of the course (e.g. "Week 1").
  * ``Lesson``                  — one item in a module.
  * ``LessonProgress``          — one pupil's completion record for one lesson.

Engagement layer (Piece 1, gated behind engagement_enabled school switch):

  * ``ClassEngagementSettings`` — per-class on/off switch + grace-day config.
  * ``LearningStreak``          — one row per student, updated each time they
                                   complete a lesson while engagement is on.

Content is scoped to a term; files use UUID storage names and authenticated
streaming views, never MEDIA_URL.
"""

from __future__ import annotations

import os
import uuid

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


def lesson_upload_path(instance: "Lesson", filename: str) -> str:
    """Store a lesson file under learning/<year>/class-<id>/<uuid>.<ext>."""
    ext = os.path.splitext(filename)[1].lower()
    module = instance.module
    return (
        f"learning/{module.created_at.year}/class-{module.school_class_id}/"
        f"{uuid.uuid4().hex}{ext}"
    )


class CourseModule(BaseModel):
    """A section of a classroom's course, ordered by ``position``."""

    school_class = models.ForeignKey(
        "academics.SchoolClass",
        on_delete=models.CASCADE,
        related_name="course_modules",
    )
    subject = models.ForeignKey(
        "academics.Subject",
        on_delete=models.CASCADE,
        related_name="course_modules",
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.CASCADE,
        related_name="course_modules",
    )
    title = models.CharField(max_length=200)
    position = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(
        default=True,
        help_text=(
            "When off, the whole module and all its lessons are hidden from "
            "students. Teachers can still see and edit it."
        ),
    )

    class Meta:
        db_table = "learning_coursemodule"
        ordering = ["position", "id"]
        indexes = [
            models.Index(fields=["school_class", "subject", "term"]),
        ]

    def __str__(self) -> str:
        return self.title


class Lesson(BaseModel):
    """One lesson inside a module: notes, an optional video and attachments."""

    class NotesFormat(models.TextChoices):
        PLAIN    = "plain",    "Plain text"
        MARKDOWN = "markdown", "Markdown"

    module = models.ForeignKey(
        CourseModule,
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    title = models.CharField(max_length=200)
    notes = models.TextField(
        blank=True,
        help_text="Lesson notes. Use Markdown for formatting (headings, bold, lists, links).",
    )
    notes_format = models.CharField(
        max_length=10,
        choices=NotesFormat.choices,
        default=NotesFormat.PLAIN,
        help_text="Controls how notes are rendered to students.",
    )
    video_url = models.URLField(blank=True)
    # Legacy single-attachment field kept for backwards compatibility.
    # New uploads go to LessonAttachment. Do not use this field for new code.
    attachment = models.FileField(
        upload_to=lesson_upload_path, blank=True, null=True
    )
    attachment_name = models.CharField(max_length=255, blank=True)
    position = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        db_table = "learning_lesson"
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return self.title

    @property
    def has_attachment(self) -> bool:
        return bool(self.attachment)

    @property
    def attachment_extension(self) -> str:
        return os.path.splitext(self.attachment_name or self.attachment.name)[1].lower()

    @property
    def attachment_is_pdf(self) -> bool:
        return self.attachment_extension == ".pdf"

    @property
    def attachment_is_image(self) -> bool:
        return self.attachment_extension in {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class LessonProgress(BaseModel):
    """One pupil's record for one lesson (exactly one row per pair)."""

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="lesson_progress",
    )
    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.CASCADE,
        related_name="progress",
    )
    completed = models.BooleanField(default=False)
    last_viewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "learning_lessonprogress"
        unique_together = [("student", "lesson")]
        ordering = ["-last_viewed_at"]

    def __str__(self) -> str:
        return f"{self.student} → {self.lesson.title}"


# ---------------------------------------------------------------------------
# Lesson attachments (multiple files per lesson) — Checkpoint A
# ---------------------------------------------------------------------------

#: Allowed extensions for lesson attachments.
LESSON_ATTACHMENT_ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx",
    ".xls", ".xlsx",
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
    ".mp3",
}

#: Maximum file size for a lesson attachment (20 MB).
LESSON_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024

#: Maximum number of attachments per lesson.
LESSON_ATTACHMENT_MAX_COUNT = 5


def lesson_attachment_upload_path(instance: "LessonAttachment", filename: str) -> str:
    """Store each attachment under learning/<year>/class-<id>/<uuid>.<ext>."""
    ext = os.path.splitext(filename)[1].lower()
    lesson = instance.lesson
    year = lesson.module.created_at.year if lesson.module_id else "unknown"
    class_id = lesson.module.school_class_id if lesson.module_id else 0
    return f"learning/{year}/class-{class_id}/{uuid.uuid4().hex}{ext}"


class LessonAttachment(BaseModel):
    """
    One uploaded file for a lesson.

    Files are stored under UUID names and served only through an
    authenticated streaming view — never via MEDIA_URL.

    Allowed: PDF, Word, PowerPoint, Excel, images, MP3.
    Max size: 20 MB per file.  Max 5 attachments per lesson.
    """

    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.CASCADE,
        related_name="lesson_attachments",
    )
    file = models.FileField(upload_to=lesson_attachment_upload_path)
    original_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)
    ordering = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "learning_lesson_attachment"
        ordering = ["ordering", "pk"]

    def __str__(self) -> str:
        return self.original_name or os.path.basename(self.file.name)

    @property
    def extension(self) -> str:
        return os.path.splitext(self.original_name or self.file.name)[1].lower()

    @property
    def is_image(self) -> bool:
        return self.extension in {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    @property
    def is_pdf(self) -> bool:
        return self.extension == ".pdf"

    @property
    def is_audio(self) -> bool:
        return self.extension == ".mp3"

    @property
    def is_office(self) -> bool:
        return self.extension in {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}

    @property
    def size_kb(self) -> int:
        return max(1, round(self.file_size / 1024)) if self.file_size else 0

    @property
    def icon(self) -> str:
        """Bootstrap Icons class for this file type."""
        if self.is_pdf:
            return "bi-file-earmark-pdf"
        if self.is_image:
            return "bi-file-earmark-image"
        if self.is_audio:
            return "bi-file-earmark-music"
        if self.extension in {".doc", ".docx"}:
            return "bi-file-earmark-word"
        if self.extension in {".ppt", ".pptx"}:
            return "bi-file-earmark-slides"
        if self.extension in {".xls", ".xlsx"}:
            return "bi-file-earmark-spreadsheet"
        return "bi-file-earmark"


class ClassFeedSettings(BaseModel):
    """
    Per-class switch for the class community feed.

    Off by default. When off, the class keeps the original notice-board feed
    (announcements, questions, tips, celebrations, reactions, pinning) and
    none of the community features appear: no replies, new post types,
    filters, live updates or lesson/quiz links. A teacher turns it on for
    their class; the school-wide ``feed_community_enabled`` switch must also
    be on.
    """

    school_class = models.OneToOneField(
        "academics.SchoolClass",
        on_delete=models.CASCADE,
        related_name="feed_settings",
    )
    is_active = models.BooleanField(
        default=False,
        help_text="Turn on the class community feed for this class.",
    )

    class Meta:
        db_table = "learning_class_feed_settings"

    def __str__(self) -> str:
        state = "on" if self.is_active else "off"
        return f"Class feed ({state}) — {self.school_class}"


class ClassEngagementSettings(BaseModel):
    """
    Per-class engagement switch and configuration.

    One row per SchoolClass.  A teacher (or admin) creates this row to turn
    engagement on for that class; it defaults to off so no class is affected
    until the school deliberately enables it.

    ``streak_grace_days`` is the number of *consecutive school days* a student
    may miss without losing their streak.  The school-wide default is 1 (one
    missed day forgiven).  Setting it to 0 means every school day must have at
    least one lesson completion; setting it to 2 gives a long-weekend buffer.
    This never counts weekends, public holidays, or days outside the current
    term — those are always skipped over.
    """

    school_class = models.OneToOneField(
        "academics.SchoolClass",
        on_delete=models.CASCADE,
        related_name="engagement_settings",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Show progress and streaks for this class.",
    )
    streak_grace_days = models.PositiveSmallIntegerField(
        default=1,
        help_text=(
            "How many consecutive school days without a lesson completion are "
            "forgiven before the streak resets. Weekends and holidays never count."
        ),
    )
    show_progress_bars = models.BooleanField(
        default=True,
        help_text="Show per-module and per-course progress bars to students.",
    )
    show_streak = models.BooleanField(
        default=True,
        help_text="Show the daily lesson streak counter to students.",
    )

    class Meta:
        db_table = "learning_class_engagement_settings"

    def __str__(self) -> str:
        state = "on" if self.is_active else "off"
        return f"Engagement ({state}) — {self.school_class}"


class LearningStreak(BaseModel):
    """
    One row per student tracking their current lesson-completion streak.

    ``current_streak``   — consecutive *school* days (Mon–Fri, inside a term,
                           minus grace days) on which the student completed at
                           least one lesson.
    ``longest_streak``   — personal best; never decremented.
    ``last_active_date`` — the school date on which the student last completed
                           a lesson.  Used by refresh_streak() to decide
                           whether the streak is still alive.
    ``streak_broken_at`` — set to today when a reset occurs; cleared on the
                           next completion.  Drives the "welcome back" message
                           rather than a guilt message.

    This row is created lazily (on first lesson completion) and updated by
    ``engagement_services.refresh_streak()``.  It is never deleted: resetting
    the streak means current_streak → 0, not DELETE.
    """

    student = models.OneToOneField(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="learning_streak",
    )
    current_streak = models.PositiveIntegerField(default=0)
    longest_streak = models.PositiveIntegerField(default=0)
    last_active_date = models.DateField(
        null=True,
        blank=True,
        help_text="School date of the student's most recent lesson completion.",
    )
    streak_broken_at = models.DateField(
        null=True,
        blank=True,
        help_text="Set when a streak resets; cleared on the next completion.",
    )

    class Meta:
        db_table = "learning_streak"

    def __str__(self) -> str:
        return (
            f"{self.student} — streak {self.current_streak} "
            f"(best {self.longest_streak})"
        )


# ---------------------------------------------------------------------------
# Class community feed — Piece 2
# ---------------------------------------------------------------------------


class ClassPost(BaseModel):
    """
    One post on a class notice board.

    Design rules:
      - Teachers can post any type.  Students can only post questions and
        responses; they cannot create announcements or celebrations.
      - Parents can read the feed but never post.
      - ``is_removed`` soft-deletes the post so teachers/admins can moderate
        without losing the record.  Removed posts are invisible to students
        and parents but remain visible (greyed out) to teachers.
      - ``is_pinned`` floats the post to the top of the feed regardless of
        age.  Only teachers can pin.
      - A post belongs to one class (all subjects share one feed per class)
        so students see a single notice board, not one per subject.
    """

    class PostType(models.TextChoices):
        ANNOUNCEMENT     = "announcement",     "Announcement"
        QUESTION         = "question",         "Question"
        TIP              = "tip",              "Tip"
        CELEBRATION      = "celebration",      "Celebration"
        CHALLENGE        = "challenge",        "Challenge"
        DISCUSSION_PROMPT = "discussion_prompt", "Discussion prompt"

    # Posts accessible to anyone in these roles that is in the same class.
    # ``tip`` is shown to students as "I figured it out"; ``celebration``
    # as a thank-you or shout-out. The stored value stays the same.
    STUDENT_ALLOWED_TYPES = {
        PostType.QUESTION,
        PostType.TIP,
        PostType.CELEBRATION,
    }

    school_class = models.ForeignKey(
        "academics.SchoolClass",
        on_delete=models.CASCADE,
        related_name="class_posts",
    )
    author = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="class_posts_authored",
    )
    post_type = models.CharField(
        max_length=20,
        choices=PostType.choices,
        default=PostType.ANNOUNCEMENT,
        db_index=True,
    )
    body = models.TextField(
        help_text="The post content. Plain text only; no HTML.",
    )
    lesson = models.ForeignKey(
        "Lesson",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Optional lesson this post is about. Must belong to the same class.",
    )
    quiz = models.ForeignKey(
        "Quiz",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Optional quiz this post is about. Must belong to the same class.",
    )
    is_pinned = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Pinned posts appear at the top of the feed.",
    )
    is_removed = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "Soft-delete by a teacher or admin.  Students and parents do not "
            "see removed posts; teachers see them greyed-out for the record."
        ),
    )
    removed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="class_posts_removed",
    )
    removed_at = models.DateTimeField(null=True, blank=True)
    digest_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Set by the morning digest job when this post is included in a "
            "teacher's digest. Prevents the same unanswered question from "
            "appearing in consecutive daily digests."
        ),
    )

    class Meta:
        db_table = "learning_class_post"
        ordering = ["-is_pinned", "-created_at"]
        indexes = [
            models.Index(fields=["school_class", "is_removed", "created_at"]),
        ]

    def __str__(self) -> str:
        author = self.author.get_full_name() if self.author else "Unknown"
        return f"[{self.get_post_type_display()}] {author}: {self.body[:60]}"


class ClassPostReply(BaseModel):
    """
    One reply under a feed post. Replies are deliberately one level deep:
    no reply can reply to another reply, so the conversation stays readable
    on a phone and never turns into a private thread.

    ``is_best_answer`` marks the single reply the question's author or a
    teacher chose. A partial unique index enforces at most one best answer
    per post. Removing that reply clears the flag, which puts the question
    back to "Waiting for an answer" unless another teacher reply remains.
    """

    post = models.ForeignKey(
        ClassPost,
        on_delete=models.CASCADE,
        related_name="replies",
    )
    author = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="class_post_replies",
    )
    body = models.TextField(
        help_text="Plain text only; no HTML and no clickable links.",
    )
    is_best_answer = models.BooleanField(
        default=False,
        db_index=True,
        help_text="The one reply chosen as the best answer for this question.",
    )
    is_removed = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Soft-delete by a teacher. Students and parents never see it.",
    )
    removed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="class_post_replies_removed",
    )
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "learning_class_post_reply"
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["post", "is_removed", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["post"],
                condition=models.Q(is_best_answer=True),
                name="uniq_best_answer_per_post",
            )
        ]

    def __str__(self) -> str:
        author = self.author.get_full_name() if self.author else "Unknown"
        return f"Reply by {author} on post {self.post_id}: {self.body[:60]}"


class ClassPostReaction(BaseModel):
    """
    One reaction from one user on one post.

    Reactions are a fixed safe set — no custom emoji, no free text.
    Each user can hold at most one reaction per post (toggling a different
    emoji replaces the previous one; clicking the same emoji removes it).

    The ``EMOJI`` choices are intentionally child-friendly: thumbs-up,
    check-mark, question, heart, fire.  No negative or aggressive reactions.
    """

    class Emoji(models.TextChoices):
        THUMBS_UP = "👍", "Thumbs up"
        CHECK     = "✅", "Got it"
        QUESTION  = "❓", "I have a question too"
        HEART     = "❤️", "Love this"
        FIRE      = "🔥", "This is great"

    post = models.ForeignKey(
        ClassPost,
        on_delete=models.CASCADE,
        related_name="reactions",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="class_post_reactions",
    )
    emoji = models.CharField(max_length=8, choices=Emoji.choices)

    class Meta:
        db_table = "learning_class_post_reaction"
        unique_together = [("post", "user")]

    def __str__(self) -> str:
        return f"{self.user_id} {self.emoji} on post {self.post_id}"


# ---------------------------------------------------------------------------
# Points, levels and badges — Piece 3
# ---------------------------------------------------------------------------


class PointConfig(BaseModel):
    """
    School-wide point values for each award reason.

    One singleton row (pk=1). The admin can edit values here without any
    code change. All award_points() calls read from this row at runtime,
    so adjusting a value takes effect for the next action immediately.

    Field names match the REASON_* constants in points_config.py so the
    service can look up the right value by reason slug.
    """

    SINGLETON_PK = 1

    pts_lesson_complete = models.PositiveSmallIntegerField(
        default=10,
        help_text="Points for completing a lesson (once per lesson).",
    )
    pts_homework_ontime = models.PositiveSmallIntegerField(
        default=20,
        help_text="Points for submitting homework on or before the due date.",
    )
    pts_homework_late = models.PositiveSmallIntegerField(
        default=8,
        help_text=(
            "Points for submitting homework after the due date. "
            "Always less than on-time to reward punctuality, but never zero."
        ),
    )
    pts_feed_question = models.PositiveSmallIntegerField(
        default=5,
        help_text="Points for posting a question on the class feed.",
    )
    pts_streak_milestone = models.PositiveSmallIntegerField(
        default=15,
        help_text="Bonus points awarded when a student's streak reaches a milestone (5, 10, 20 …).",
    )
    daily_feed_cap = models.PositiveSmallIntegerField(
        default=15,
        help_text=(
            "Maximum points a student can earn from feed activity per calendar day. "
            "Prevents farming by posting many questions in one session."
        ),
    )
    pts_quiz_pass = models.PositiveSmallIntegerField(
        default=25,
        help_text=(
            "Points awarded the first time a student passes a quiz. "
            "Never awarded again for the same quiz."
        ),
    )
    pts_quiz_attempt = models.PositiveSmallIntegerField(
        default=5,
        help_text=(
            "Points awarded for completing any quiz attempt (first attempt only, "
            "regardless of pass or fail). Never awarded again for the same quiz."
        ),
    )

    class Meta:
        db_table = "learning_point_config"

    def __str__(self) -> str:
        return "Point configuration"

    @classmethod
    def load(cls) -> "PointConfig":
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj


class PointLedger(BaseModel):
    """
    One immutable row for every point award or reversal.

    The student's current total is always SUM(points) over their ledger rows.
    Points values are positive for awards and negative for reversals. The row
    is never edited or deleted; a mistake is corrected by a reversal row
    (negative value) that references the original via ``reversal_of``.

    ``reason`` is a short slug from points_config.REASON_* so queries can
    group by category (lesson / homework / feed / streak / bonus / reversal).

    ``idempotency_key`` is set for automated awards (one per lesson, one per
    homework) and guards against double-awarding if the hook fires twice.
    It is left blank for manual bonus / reversal rows.
    """

    class Reason(models.TextChoices):
        LESSON      = "lesson",      "Lesson completed"
        HW_ONTIME   = "hw_ontime",   "Homework on time"
        HW_LATE     = "hw_late",     "Homework late"
        FEED_POST   = "feed_post",   "Feed question"
        STREAK      = "streak",      "Streak milestone"
        BONUS       = "bonus",       "Teacher bonus"
        REVERSAL    = "reversal",    "Reversal"

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="point_ledger",
    )
    points = models.SmallIntegerField(
        help_text="Positive = award, negative = reversal.",
    )
    reason = models.CharField(
        max_length=20,
        choices=Reason.choices,
        db_index=True,
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        help_text="Human-readable explanation shown to teachers.",
    )
    awarded_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="points_awarded",
        help_text="Set for manual bonus/reversal rows; null for automatic awards.",
    )
    reversal_of = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reversed_by",
        help_text="The original award row this reversal cancels.",
    )
    idempotency_key = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text=(
            "Unique key preventing double-award for the same action. "
            "Format: '<reason>:<object_pk>'. Blank for manual rows."
        ),
    )

    class Meta:
        db_table = "learning_point_ledger"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["student", "reason"]),
            models.Index(fields=["student", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=models.Q(idempotency_key__gt=""),
                name="uniq_point_ledger_idempotency_key",
            )
        ]

    def __str__(self) -> str:
        sign = "+" if self.points >= 0 else ""
        return f"{self.student} {sign}{self.points} [{self.reason}]"


class StudentLevel(BaseModel):
    """
    Cached level for one student. Updated by check_level_up() after every
    award. The source of truth is the ledger total; this is a cache only,
    so it can always be regenerated from the ledger.

    ``level_number`` matches the index in points_config.LEVELS (1-based).
    ``level_name`` is denormalised from LEVELS for fast display.
    ``total_points`` is denormalised from SUM(ledger) for fast display.
    ``notified_at`` is set when the level-up notification fires, preventing
    duplicate notifications if check_level_up is called more than once at
    the same level boundary.
    """

    student = models.OneToOneField(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="student_level",
    )
    level_number = models.PositiveSmallIntegerField(default=1)
    level_name = models.CharField(max_length=40, default="Seedling")
    total_points = models.IntegerField(default=0)
    notified_level = models.PositiveSmallIntegerField(
        default=0,
        help_text="The highest level for which a level-up notification has been sent.",
    )

    class Meta:
        db_table = "learning_student_level"

    def __str__(self) -> str:
        return f"{self.student} — Level {self.level_number} ({self.level_name})"


# ---------------------------------------------------------------------------
# Student private lesson notes — Checkpoint B
# ---------------------------------------------------------------------------


class StudentLessonNote(BaseModel):
    """
    One student's private notes for one lesson.

    Notes are visible only to that student — not to teachers, parents or
    other students.  A student can edit them at any time.  The content is
    plain text; no HTML is stored or rendered from this field.
    """

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="lesson_notes",
    )
    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.CASCADE,
        related_name="student_notes",
    )
    content = models.TextField(
        blank=True,
        help_text="Student's private notes for this lesson. Plain text only.",
    )

    class Meta:
        db_table = "learning_student_lesson_note"
        unique_together = [("student", "lesson")]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.student} — notes for {self.lesson.title}"


# ---------------------------------------------------------------------------
# Quizzes — Checkpoint C
# ---------------------------------------------------------------------------


class Quiz(BaseModel):
    """
    A quiz attached to one lesson (one quiz per lesson, optional).

    Design rules:
    - Draft quizzes are invisible to students.
    - Once any student has a *submitted* attempt, questions and choices are
      locked. The teacher can duplicate the quiz to create an editable copy.
    - Correct answers NEVER leave the server before a student submits.
      The template receives question and choice objects; is_correct is
      excluded from the student-facing queryset.
    - ``counts_for_gradebook`` is stored now but not connected to report
      cards until the owner approves that design separately.
    """

    class Attempts(models.IntegerChoices):
        ONE       = 1,  "1 — no retakes"
        TWO       = 2,  "2 attempts"
        THREE     = 3,  "3 attempts"
        UNLIMITED = 0,  "Unlimited"

    lesson = models.OneToOneField(
        Lesson,
        on_delete=models.CASCADE,
        related_name="quiz",
    )
    title = models.CharField(max_length=200)
    instructions = models.TextField(
        blank=True,
        help_text="Optional instructions shown to students before they begin.",
    )
    pass_mark = models.PositiveSmallIntegerField(
        default=50,
        help_text="Percentage required to pass (0–100).",
    )
    attempts_allowed = models.PositiveSmallIntegerField(
        default=Attempts.ONE,
        choices=Attempts.choices,
        help_text="0 = unlimited.",
    )
    time_limit_minutes = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Optional time limit in minutes. Leave blank for no limit.",
    )
    shuffle_questions = models.BooleanField(
        default=False,
        help_text="Show questions in a random order for each attempt.",
    )
    shuffle_choices = models.BooleanField(
        default=False,
        help_text="Show answer choices in a random order.",
    )
    show_correct_after = models.BooleanField(
        default=True,
        help_text=(
            "Show the correct answer and explanation after the student submits. "
            "Turn off to hide correct answers (e.g. for formal assessments)."
        ),
    )
    is_published = models.BooleanField(
        default=False,
        help_text="Draft quizzes are invisible to students.",
    )
    counts_for_gradebook = models.BooleanField(
        default=False,
        help_text=(
            "Reserved for future gradebook integration. "
            "Has no effect until the gradebook connection is approved."
        ),
    )

    class Meta:
        db_table = "learning_quiz"

    def __str__(self) -> str:
        return f"{self.title} ({self.lesson.title})"

    @property
    def has_submitted_attempts(self) -> bool:
        """True when at least one student has submitted an attempt."""
        return self.attempts.filter(submitted_at__isnull=False).exists()

    @property
    def question_count(self) -> int:
        return self.questions.count()


class QuizQuestion(BaseModel):
    """
    One question in a quiz.

    ``question_type`` determines which fields are meaningful:
      multiple_choice  — choices provided, exactly one is_correct
      true_false       — two choices generated automatically ("True", "False")

    Future types (not built yet, fields already accommodate them):
      select_all       — multiple choices, multiple is_correct
      short_answer     — no choices; answer_text on QuizAttemptAnswer used

    ``body`` supports the same plain/Markdown format as lesson notes and is
    sanitised with the same bleach pipeline before display.
    ``explanation`` is shown after submission (if show_correct_after=True).
    """

    class QuestionType(models.TextChoices):
        MULTIPLE_CHOICE = "mc",   "Multiple choice"
        TRUE_FALSE      = "tf",   "True / False"

    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    body = models.TextField(
        help_text="Question text. Markdown is supported.",
    )
    question_type = models.CharField(
        max_length=10,
        choices=QuestionType.choices,
        default=QuestionType.MULTIPLE_CHOICE,
    )
    explanation = models.TextField(
        blank=True,
        help_text=(
            "Explanation shown after the student submits (if allowed by quiz settings). "
            "Supports Markdown."
        ),
    )
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "learning_quiz_question"
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return f"Q{self.position + 1}: {self.body[:60]}"


class QuizChoice(BaseModel):
    """
    One answer choice for a QuizQuestion.

    ``is_correct`` is NEVER sent to the student's browser before they submit.
    The student-facing queryset always excludes this field (using .only() or
    a custom manager). Only quiz_services.submit_attempt() reads is_correct,
    entirely server-side.
    """

    question = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name="choices",
    )
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "learning_quiz_choice"
        ordering = ["position", "id"]

    def __str__(self) -> str:
        marker = "✓" if self.is_correct else "✗"
        return f"[{marker}] {self.text[:60]}"


class QuizAttempt(BaseModel):
    """
    One student's attempt at a quiz.

    Only one in-progress attempt may exist at a time (enforced by services).
    Submitted attempts are immutable — the service sets submitted_at and
    score_pct in one transaction and never touches them again.

    ``answers_auto_submitted`` is True when the server auto-submitted because
    the time limit expired, so the teacher can see how the attempt ended.
    """

    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="quiz_attempts",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the student submits. Null means still in progress.",
    )
    score_pct = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Percentage score (0–100). Set on submission.",
    )
    passed = models.BooleanField(
        null=True,
        blank=True,
        help_text="True/False set on submission; null while in progress.",
    )
    answers_auto_submitted = models.BooleanField(
        default=False,
        help_text="True when the server auto-submitted due to time limit.",
    )
    # Shuffle seeds — stored so a resumed attempt shows the same order
    question_order = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered list of question PKs for this attempt (shuffle seed).",
    )

    class Meta:
        db_table = "learning_quiz_attempt"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["student", "quiz"]),
        ]

    def __str__(self) -> str:
        status = f"{self.score_pct}%" if self.submitted_at else "in progress"
        return f"{self.student} → {self.quiz.title} ({status})"

    @property
    def is_submitted(self) -> bool:
        return self.submitted_at is not None

    @property
    def time_remaining_seconds(self) -> int | None:
        """Seconds left on the clock, or None if no limit."""
        if not self.quiz.time_limit_minutes:
            return None
        from django.utils import timezone
        limit = self.quiz.time_limit_minutes * 60
        elapsed = (timezone.now() - self.started_at).total_seconds()
        return max(0, int(limit - elapsed))

    @property
    def is_timed_out(self) -> bool:
        remaining = self.time_remaining_seconds
        return remaining is not None and remaining <= 0


class QuizAttemptAnswer(BaseModel):
    """
    The student's answer to one question in one attempt.

    Saved as the student clicks each answer (via AJAX) so a dropped connection
    or closed browser loses nothing.

    Future-proofing:
    - ``selected_choice``  — FK for single-answer (MC / TF)
    - ``selected_choices`` — M2M for "select all that apply" (empty now)
    - ``answer_text``      — plain text for short-answer (blank now)

    Exactly one row per (attempt, question).
    """

    attempt = models.ForeignKey(
        QuizAttempt,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    question = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name="student_answers",
    )
    selected_choice = models.ForeignKey(
        QuizChoice,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="selected_by",
        help_text="The choice the student selected (single-answer questions).",
    )
    # M2M for future "select all that apply" — empty for now
    selected_choices = models.ManyToManyField(
        QuizChoice,
        blank=True,
        related_name="multi_selected_by",
        help_text="Reserved for future 'select all that apply' questions.",
    )
    # TextField for future short-answer — blank for now
    answer_text = models.TextField(
        blank=True,
        help_text="Reserved for future short-answer questions.",
    )
    # Marked server-side on submission — never sent to the browser before then
    is_correct = models.BooleanField(
        null=True,
        blank=True,
        help_text="Set by the server when the attempt is submitted. Never sent to browser before submission.",
    )

    class Meta:
        db_table = "learning_quiz_attempt_answer"
        unique_together = [("attempt", "question")]
        ordering = ["question__position", "question_id"]

    def __str__(self) -> str:
        return f"Attempt#{self.attempt_id} Q#{self.question_id}"


# ---------------------------------------------------------------------------
# Live Classes and Sessions — Checkpoint D
# ---------------------------------------------------------------------------


class LiveClassSession(BaseModel):
    """
    A scheduled live video session (Google Meet, Zoom, Teams, or custom URL)
    attached to a computed classroom (school_class + subject).

    Option 1: Link-out only (zero server streaming overhead, validated URLs).
    Students can join during the active join window (15 mins before scheduled
    start until scheduled end + 30 mins grace).
    """

    class Platform(models.TextChoices):
        GOOGLE_MEET = "google_meet", "Google Meet"
        ZOOM = "zoom", "Zoom"
        TEAMS = "teams", "Microsoft Teams"
        OTHER = "other", "Other / Custom"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    school_class = models.ForeignKey(
        "academics.SchoolClass",
        on_delete=models.CASCADE,
        related_name="live_sessions",
    )
    subject = models.ForeignKey(
        "academics.Subject",
        on_delete=models.CASCADE,
        related_name="live_sessions",
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hosted_live_sessions",
    )
    term = models.ForeignKey(
        "academics.Term",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="live_sessions",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(
        blank=True, help_text="Session agenda or preparation instructions."
    )
    platform = models.CharField(
        max_length=20,
        choices=Platform.choices,
        default=Platform.GOOGLE_MEET,
    )
    meeting_url = models.URLField(
        max_length=500, help_text="Direct link to join the meeting."
    )
    meeting_id = models.CharField(
        max_length=100, blank=True, help_text="Optional meeting ID for Zoom/Teams."
    )
    passcode = models.CharField(
        max_length=100, blank=True, help_text="Optional meeting password/passcode."
    )
    scheduled_start = models.DateTimeField(db_index=True)
    duration_minutes = models.PositiveSmallIntegerField(
        default=45,
        help_text="Expected duration in minutes.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
        db_index=True,
    )

    class Meta:
        db_table = "learning_live_session"
        ordering = ["scheduled_start"]
        indexes = [
            models.Index(fields=["school_class", "scheduled_start"]),
            models.Index(fields=["status", "scheduled_start"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.school_class.name} - {self.subject.code}) @ {self.scheduled_start:%Y-%m-%d %H:%M}"

    @property
    def scheduled_end(self):
        from datetime import timedelta
        return self.scheduled_start + timedelta(minutes=self.duration_minutes)

    @property
    def is_live(self) -> bool:
        """True if in_progress or current time is between start-10m and scheduled_end."""
        from datetime import timedelta
        from django.utils import timezone
        if self.status == self.Status.IN_PROGRESS:
            return True
        if self.status == self.Status.SCHEDULED:
            now = timezone.now()
            return (self.scheduled_start - timedelta(minutes=10)) <= now <= self.scheduled_end
        return False

    @property
    def is_past(self) -> bool:
        from django.utils import timezone
        if self.status == self.Status.COMPLETED:
            return True
        if self.status != self.Status.CANCELLED:
            return timezone.now() > self.scheduled_end
        return False

    @property
    def join_window_open(self) -> bool:
        """Window when the Join button is enabled for students."""
        from datetime import timedelta
        from django.utils import timezone
        if self.status == self.Status.CANCELLED:
            return False
        if self.status == self.Status.IN_PROGRESS:
            return True
        now = timezone.now()
        start_win = self.scheduled_start - timedelta(minutes=15)
        end_win = self.scheduled_end + timedelta(minutes=30)
        return start_win <= now <= end_win


class LiveClassAttendance(BaseModel):
    """
    Records student attendance (join clicks) for a LiveClassSession.
    """

    session = models.ForeignKey(
        LiveClassSession,
        on_delete=models.CASCADE,
        related_name="attendances",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="live_attendances",
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "learning_live_attendance"
        unique_together = [("session", "student")]
        ordering = ["joined_at"]

    def __str__(self) -> str:
        return f"{self.student} joined {self.session.title} at {self.joined_at:%H:%M}"

