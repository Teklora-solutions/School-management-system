"""
apps/results/models.py
----------------------
Grading configuration, ResultSheet state machine, and ScoreEntry rows.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import BaseModel


class GradingConfig(BaseModel):
    """One grading configuration per academic year."""

    academic_year = models.OneToOneField(
        "academics.AcademicYear",
        on_delete=models.CASCADE,
        related_name="grading_config",
    )
    pass_mark = models.DecimalField(max_digits=5, decimal_places=2, default=50)

    class Meta:
        db_table = "results_grading_config"

    def __str__(self) -> str:
        return f"Grading {self.academic_year}"


class GradeBand(BaseModel):
    """
    Legacy grade scale band for a year (min–max inclusive).

    Bands must cover 0–100 without gaps or overlaps. These rows are only a
    fallback now: a class included in its year's grading system is graded by
    that system's ``GradeScaleBand`` rows instead.
    """

    academic_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.CASCADE,
        related_name="grade_bands",
    )
    min_score = models.DecimalField(max_digits=5, decimal_places=2)
    max_score = models.DecimalField(max_digits=5, decimal_places=2)
    grade = models.CharField(max_length=8)
    remark = models.CharField(max_length=64, blank=True)
    points = models.DecimalField(max_digits=4, decimal_places=2, default=0)

    class Meta:
        db_table = "results_grade_band"
        ordering = ["-min_score"]
        unique_together = [("academic_year", "grade")]

    def __str__(self) -> str:
        return f"{self.grade}: {self.min_score}�{self.max_score}"

    def clean(self):
        if self.max_score < self.min_score:
            raise ValidationError({"max_score": "Must be ? min_score."})


class GradeScale(BaseModel):
    """
    The grading system for one academic year.

    One year has at most one system (enforced by the OneToOne). Its bands
    grade the classes selected on the system; classes left out fall back to
    the year's legacy ``GradeBand`` rows.
    """

    academic_year = models.OneToOneField(
        "academics.AcademicYear",
        on_delete=models.CASCADE,
        related_name="grade_scale",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "results_grade_scale"
        ordering = ["academic_year__start_date", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "name"], name="uniq_grade_scale_year_name"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.academic_year})"


class GradeScaleBand(BaseModel):
    """One band of a shared grade scale (min–max inclusive)."""

    scale = models.ForeignKey(
        GradeScale,
        on_delete=models.CASCADE,
        related_name="bands",
    )
    min_score = models.DecimalField(max_digits=5, decimal_places=2)
    max_score = models.DecimalField(max_digits=5, decimal_places=2)
    grade = models.CharField(max_length=8)
    remark = models.CharField(max_length=64, blank=True)
    points = models.DecimalField(max_digits=4, decimal_places=2, default=0)

    class Meta:
        db_table = "results_grade_scale_band"
        ordering = ["min_score"]
        constraints = [
            models.UniqueConstraint(
                fields=["scale", "grade"], name="uniq_grade_scale_band_grade"
            )
        ]

    def __str__(self) -> str:
        return f"{self.scale.name} {self.grade}: {self.min_score}–{self.max_score}"

    def clean(self):
        if self.max_score < self.min_score:
            raise ValidationError({"max_score": "Must be ≥ min_score."})


class Exam(BaseModel):
    """
    One of the ten exam events in a term.

    Exam sheets (``ResultSheet.exam`` set) carry one raw mark out of 100 per
    student. Term sheets (``ResultSheet.exam`` NULL) are legacy rows kept for
    previously published results; new marks are always entered on exam sheets.
    """

    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.CASCADE,
        related_name="exams",
    )
    name = models.CharField(max_length=64)
    order = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "results_exam"
        ordering = ["term", "order"]
        unique_together = [("term", "order"), ("term", "name")]

    def __str__(self) -> str:
        return f"{self.term} — {self.name}"


class ResultSheet(BaseModel):
    """
    One sheet per class + subject + term (and exam, when it is an exam sheet).
    State machine: Draft ? Submitted ? Confirmed ? Published.
    Locked after Confirmed; reopen requires a reason and is audit-logged.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        CONFIRMED = "confirmed", "Confirmed"
        PUBLISHED = "published", "Published"

    VALID_TRANSITIONS = {
        Status.DRAFT: {Status.SUBMITTED},
        Status.SUBMITTED: {
            Status.CONFIRMED,
            Status.DRAFT,
        },  # DRAFT = reopen before confirm
        Status.CONFIRMED: {
            Status.PUBLISHED,
            Status.DRAFT,
        },  # DRAFT = reopen with reason
        Status.PUBLISHED: {Status.DRAFT},  # reopen published (rare, audited)
    }

    school_class = models.ForeignKey(
        "academics.SchoolClass",
        on_delete=models.CASCADE,
        related_name="result_sheets",
    )
    subject = models.ForeignKey(
        "academics.Subject",
        on_delete=models.CASCADE,
        related_name="result_sheets",
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.CASCADE,
        related_name="result_sheets",
    )
    stream = models.ForeignKey(
        "academics.Stream",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="result_sheets",
    )
    exam = models.ForeignKey(
        Exam,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="result_sheets",
        help_text="NULL for a term result sheet; set for an exam sheet.",
    )
    teacher = models.ForeignKey(
        "teachers.TeacherProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="result_sheets",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="confirmed_sheets",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_sheets",
    )
    reopen_reason = models.TextField(blank=True)

    class Meta:
        db_table = "results_result_sheet"
        constraints = [
            models.UniqueConstraint(
                fields=["school_class", "subject", "term", "stream"],
                condition=models.Q(exam__isnull=True, stream__isnull=False),
                name="uniq_result_sheet_legacy_stream",
            ),
            models.UniqueConstraint(
                fields=["school_class", "subject", "term"],
                condition=models.Q(exam__isnull=True, stream__isnull=True),
                name="uniq_result_sheet_legacy_no_stream",
            ),
            models.UniqueConstraint(
                fields=["school_class", "subject", "term", "exam", "stream"],
                condition=models.Q(exam__isnull=False, stream__isnull=False),
                name="uniq_result_sheet_exam_stream",
            ),
            models.UniqueConstraint(
                fields=["school_class", "subject", "term", "exam"],
                condition=models.Q(exam__isnull=False, stream__isnull=True),
                name="uniq_result_sheet_exam_no_stream",
            ),
        ]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return (
            f"{self.school_class} / {self.subject.code} / "
            f"{self.term} [{self.status}]"
        )

    @property
    def is_locked(self) -> bool:
        return self.status in {self.Status.CONFIRMED, self.Status.PUBLISHED}

    def can_transition_to(self, new_status: str) -> bool:
        allowed = self.VALID_TRANSITIONS.get(self.status, set())
        return new_status in allowed


class ScoreEntry(BaseModel):
    """
    One student's mark on a ResultSheet.

    ``raw_score`` is the exam mark out of 100. ``total`` mirrors it so the
    student-facing result pages keep working unchanged.
    """

    result_sheet = models.ForeignKey(
        ResultSheet, on_delete=models.CASCADE, related_name="entries"
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="score_entries",
    )
    raw_score = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Raw exam mark (0–100); NULL when no mark has been entered.",
    )
    total = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    grade = models.CharField(max_length=8, blank=True)
    remark = models.CharField(max_length=64, blank=True)
    points = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "results_score_entry"
        unique_together = [("result_sheet", "student")]
        ordering = ["student__admission_number"]

    def __str__(self) -> str:
        return f"{self.student.admission_number} on sheet#{self.result_sheet_id}"


class ClassTeacherRemark(BaseModel):
    """
    The class teacher's free-text overall remark for one student in one term.

    Shown on the report card below the results table. One row per
    (student, term); written by the student's class teacher (entry UI lives in
    the teacher portal), so a card printed before the remark is written simply
    has no remark section.
    """

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="class_teacher_remarks",
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.CASCADE,
        related_name="class_teacher_remarks",
    )
    remark = models.TextField(blank=True)
    class_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="written_ct_remarks",
    )

    class Meta:
        db_table = "results_class_teacher_remark"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "term"], name="uniq_ct_remark_student_term"
            )
        ]

    def __str__(self) -> str:
        return f"CT remark: {self.student.admission_number} / {self.term}"
