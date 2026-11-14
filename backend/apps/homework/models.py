"""
apps/homework/models.py
-----------------------
Homework assignments set by teachers, scoped to a class/stream/subject.
Students and parents can view homework due for the student's current class.
"""

import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

#: A single homework may carry at most this many files.
MAX_HOMEWORK_ATTACHMENTS = 5

#: A single student submission may carry at most this many files.
MAX_SUBMISSION_ATTACHMENTS = 5


def homework_upload_path(instance: "HomeworkAttachment", filename: str) -> str:
    """Store each file under homework/<year>/class-<id>/<uuid>.<ext>."""
    ext = os.path.splitext(filename)[1].lower()
    homework = instance.homework
    return (
        f"homework/{homework.created_at.year}/class-{homework.school_class_id}/"
        f"{uuid.uuid4().hex}{ext}"
    )


class Homework(BaseModel):
    """
    A homework assignment set by a teacher for a class/subject.
    Visible to students in that class once published.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    subject = models.ForeignKey(
        "academics.Subject",
        on_delete=models.CASCADE,
        related_name="homework_assignments",
    )
    school_class = models.ForeignKey(
        "academics.SchoolClass",
        on_delete=models.CASCADE,
        related_name="homework_assignments",
    )
    stream = models.ForeignKey(
        "academics.Stream",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="homework_assignments",
        help_text="Leave blank to target all streams in the class.",
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.CASCADE,
        related_name="homework_assignments",
    )
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="homework_set",
        limit_choices_to={"role": "teacher"},
    )
    due_date = models.DateField(db_index=True)
    attachment = models.FileField(upload_to="homework/", blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    max_score = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        help_text=(
            "What a full mark is out of. Leave blank until the homework is "
            "ready to mark; marking is refused while this is blank."
        ),
    )
    marks_released_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when marks for the whole class were released to students.",
    )
    counts_for_gradebook = models.BooleanField(
        default=False,
        help_text=(
            "Tick to let this homework's marks feed the teacher's gradebook. "
            "Off by default: existing homework does not affect an average until "
            "the teacher chooses it."
        ),
    )

    class Meta:
        db_table = "homework_homework"
        ordering = ["due_date", "subject__name"]
        indexes = [
            models.Index(fields=["school_class", "term", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.subject.code} / due {self.due_date})"


class HomeworkAttachment(BaseModel):
    """
    One uploaded file for a homework assignment (image, PDF or Word document).

    ``original_name`` keeps the teacher's filename for display and downloads;
    the file itself is stored under a UUID name that reveals nothing about the
    class. Files are streamed through an authenticated view, never linked to
    ``MEDIA_URL`` directly.
    """

    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to=homework_upload_path)
    original_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)
    ordering = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "homework_attachment"
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
    def size_kb(self) -> int:
        if not self.file_size:
            return 0
        return max(1, round(self.file_size / 1024))


class HomeworkSubmission(BaseModel):
    """
    One student's answer to a homework.

    There is exactly one row per (homework, student); resubmitting before the
    due date updates it in place and keeps ``created_at`` as the first time the
    work arrived. ``is_late`` is fixed at the moment of submission, so a pupil
    who hands in after the due date stays flagged even after the teacher edits
    the homework.
    """

    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="homework_submissions",
    )
    note = models.TextField(blank=True)
    submitted_at = models.DateTimeField(db_index=True)
    is_late = models.BooleanField(default=False)

    class Meta:
        db_table = "homework_submission"
        unique_together = [("homework", "student")]
        ordering = [
            "student__user__last_name",
            "student__user__first_name",
            "student__admission_number",
        ]

    def __str__(self) -> str:
        return f"{self.student} → {self.homework.title}"

    @property
    def current_attachments(self) -> list:
        """
        Files belonging to the latest attempt.

        Files from earlier attempts stay on disk and stay attached to their
        revision; they are merely left out of what "this submission" shows.
        """
        return [a for a in self.attachments.all() if a.revision_id is None]


class HomeworkSubmissionRevision(BaseModel):
    """
    An earlier attempt, kept when a pupil hands work in again.

    Only the "Needs revision" loop creates these: the live submission always
    holds the latest attempt, and each revision holds the note and timing of
    the attempt it replaced. Its files move with it (see
    ``HomeworkSubmissionAttachment.revision``), so nothing is copied on disk
    and every version stays openable by the teacher.
    """

    submission = models.ForeignKey(
        HomeworkSubmission,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    number = models.PositiveSmallIntegerField()
    note = models.TextField(blank=True)
    submitted_at = models.DateTimeField()
    is_late = models.BooleanField(default=False)

    class Meta:
        db_table = "homework_submission_revision"
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "number"],
                name="uniq_homework_revision_number",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.submission} — attempt {self.number}"


def submission_upload_path(
    instance: "HomeworkSubmissionAttachment", filename: str
) -> str:
    """Store each submitted file under homework/submissions/<year>/class-<id>/<uuid>.<ext>."""
    ext = os.path.splitext(filename)[1].lower()
    homework = instance.submission.homework
    return (
        f"homework/submissions/{homework.created_at.year}/"
        f"class-{homework.school_class_id}/{uuid.uuid4().hex}{ext}"
    )


class HomeworkSubmissionAttachment(BaseModel):
    """One file a student attached to their submission."""

    submission = models.ForeignKey(
        HomeworkSubmission,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    revision = models.ForeignKey(
        HomeworkSubmissionRevision,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="attachments",
        help_text="Set when this file belongs to an earlier attempt.",
    )
    file = models.FileField(upload_to=submission_upload_path)
    original_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)
    ordering = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "homework_submission_attachment"
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
    def size_kb(self) -> int:
        if not self.file_size:
            return 0
        return max(1, round(self.file_size / 1024))


class HomeworkMark(BaseModel):
    """
    One teacher's mark for one pupil's homework.

    Deliberately separate from ``HomeworkSubmission``: a pupil who handed
    nothing in still needs a status (Not submitted), and marking must never
    rewrite the evidence of what arrived and when. One row per pupil per
    homework, kept whether or not anything was handed in.

    A mark stays invisible to the pupil and their parents until either the
    homework's ``marks_released_at`` (whole class) or this row's
    ``released_at`` (one pupil) is set.
    """

    class Status(models.TextChoices):
        EXCELLENT = "excellent", "Excellent"
        GOOD = "good", "Good"
        NEEDS_REVISION = "needs_revision", "Needs revision"
        NOT_SUBMITTED = "not_submitted", "Not submitted"

    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="marks",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="homework_marks",
    )
    score = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Mark out of the homework's total. Blank when nothing was handed in.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, blank=True)
    feedback = models.TextField(
        blank=True,
        help_text="What the pupil should read. Shown alongside the mark once released.",
    )
    late_deduction = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=Decimal("0"),
        help_text="Marks taken off for lateness, only when the school switch is on.",
    )
    resubmissions_allowed = models.PositiveSmallIntegerField(
        default=0,
        help_text="Extra attempts granted when the work is sent back for revision.",
    )
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="homework_marks_made",
    )
    marked_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when this one pupil's mark was released.",
    )

    class Meta:
        db_table = "homework_mark"
        ordering = [
            "student__user__last_name",
            "student__user__first_name",
            "student__admission_number",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["homework", "student"],
                name="uniq_homework_mark_student",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} → {self.homework.title}"

    @property
    def final_score(self) -> Decimal | None:
        """The mark the pupil sees: the score less any late deduction."""
        if self.score is None:
            return None
        return max(Decimal("0"), self.score - (self.late_deduction or Decimal("0")))

