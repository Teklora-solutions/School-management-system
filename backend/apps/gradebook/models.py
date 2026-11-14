"""
apps/gradebook/models.py
------------------------
The teacher gradebook: one weighting setup per classroom, hand-recorded items
for work done offline, per-pupil excuses, and the frozen snapshots families see.

Nothing here copies a homework or quiz mark. Those stay on their own records and
are read live; the snapshot tables exist only so a released grade cannot change
under a family without a new, reasoned release.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import BaseModel


class GradebookSetup(BaseModel):
    """
    Weights and the end-of-term sheet for one class + subject + term.

    ``stream`` narrows the setup to one stream of a class, mirroring the
    teacher assignment rule. Weights are whole percentages and must total 100
    when all four buckets are in use.
    """

    school_class = models.ForeignKey(
        "academics.SchoolClass",
        on_delete=models.CASCADE,
        related_name="gradebook_setups",
    )
    subject = models.ForeignKey(
        "academics.Subject",
        on_delete=models.CASCADE,
        related_name="gradebook_setups",
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.CASCADE,
        related_name="gradebook_setups",
    )
    stream = models.ForeignKey(
        "academics.Stream",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gradebook_setups",
    )
    homework_weight = models.PositiveSmallIntegerField(
        default=30, help_text="Percentage of the final mark from counted homework."
    )
    quiz_weight = models.PositiveSmallIntegerField(
        default=30, help_text="Percentage from counted quizzes."
    )
    lab_weight = models.PositiveSmallIntegerField(
        default=20, help_text="Percentage from hand-recorded lab work."
    )
    exam_weight = models.PositiveSmallIntegerField(
        default=20, help_text="Percentage from the end-of-term exam."
    )
    exam_sheet = models.ForeignKey(
        "results.ResultSheet",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gradebook_setups",
        help_text=(
            "The exam sheet whose mark is the end-of-term component, and where "
            "'Send final marks to report card' writes the weighted score."
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gradebook_setups_created",
    )

    class Meta:
        db_table = "gradebook_setup"
        constraints = [
            models.UniqueConstraint(
                fields=["school_class", "subject", "term"],
                condition=models.Q(stream__isnull=True),
                name="uniq_gradebook_setup_no_stream",
            ),
            models.UniqueConstraint(
                fields=["school_class", "subject", "term", "stream"],
                condition=models.Q(stream__isnull=False),
                name="uniq_gradebook_setup_stream",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"Gradebook {self.school_class} / {self.subject.code} / {self.term}"
        )

    @property
    def total_weight(self) -> int:
        return (
            self.homework_weight
            + self.quiz_weight
            + self.lab_weight
            + self.exam_weight
        )

    def clean(self):
        if self.total_weight != 100:
            raise ValidationError(
                {
                    "homework_weight": (
                        f"The four weights must add up to 100 "
                        f"(they add up to {self.total_weight})."
                    )
                }
            )


class ManualItem(BaseModel):
    """
    One piece of work recorded by hand: a lab, a class test or an oral.

    It is filed into one of the counted buckets at creation. ``reason`` says why
    the mark exists outside the system — the evidence a mark can be defended
    with months later.
    """

    class Bucket(models.TextChoices):
        HOMEWORK = "homework", "Homework"
        QUIZ = "quiz", "Quizzes"
        LAB = "lab", "Labs"

    setup = models.ForeignKey(
        GradebookSetup,
        on_delete=models.CASCADE,
        related_name="manual_items",
    )
    bucket = models.CharField(max_length=20, choices=Bucket.choices)
    title = models.CharField(max_length=200)
    max_score = models.DecimalField(
        max_digits=5, decimal_places=1, default=Decimal("100")
    )
    worked_on = models.DateField()
    reason = models.TextField(
        help_text="Why this mark was recorded by hand, e.g. 'Written test, papers kept in class.'"
    )
    counts_for_gradebook = models.BooleanField(
        default=True,
        help_text="Untick to keep the marks but leave them out of the average.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gradebook_manual_items",
    )

    class Meta:
        db_table = "gradebook_manual_item"
        ordering = ["worked_on", "pk"]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_bucket_display()})"

    def clean(self):
        if self.max_score is not None and self.max_score <= 0:
            raise ValidationError({"max_score": "The total must be more than 0."})


class ManualScore(BaseModel):
    """One pupil's mark on a hand-recorded item. A blank score means missing."""

    item = models.ForeignKey(
        ManualItem, on_delete=models.CASCADE, related_name="scores"
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="gradebook_manual_scores",
    )
    score = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Blank when nothing was handed in.",
    )
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gradebook_manual_scores_entered",
    )
    entered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "gradebook_manual_score"
        constraints = [
            models.UniqueConstraint(
                fields=["item", "student"], name="uniq_gradebook_manual_score"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} → {self.item.title}"


class GradebookExcuse(BaseModel):
    """
    One pupil excused from one counted item, with a reason.

    An excused item leaves both the pupil's numerator and denominator for its
    bucket; it neither scores zero nor disappears silently.
    """

    class SourceKind(models.TextChoices):
        HOMEWORK = "homework", "Homework"
        QUIZ = "quiz", "Quiz"
        MANUAL = "manual", "Manual item"

    setup = models.ForeignKey(
        GradebookSetup, on_delete=models.CASCADE, related_name="excuses"
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="gradebook_excuses",
    )
    source_kind = models.CharField(max_length=20, choices=SourceKind.choices)
    source_id = models.PositiveIntegerField()
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gradebook_excuses_recorded",
    )

    class Meta:
        db_table = "gradebook_excuse"
        constraints = [
            models.UniqueConstraint(
                fields=["setup", "student", "source_kind", "source_id"],
                name="uniq_gradebook_excuse",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} excused: {self.source_kind} #{self.source_id}"


class GradebookRelease(BaseModel):
    """
    A frozen, numbered release of one classroom's gradebook.

    Families only ever read the latest release. Changing a mark afterwards and
    releasing again creates the next version with its own reason, so every
    grade a parent has seen can be reconstructed.
    """

    setup = models.ForeignKey(
        GradebookSetup, on_delete=models.CASCADE, related_name="releases"
    )
    version = models.PositiveSmallIntegerField(default=1)
    weights = models.JSONField(
        default=dict, help_text="The four weights as they were at release."
    )
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gradebook_releases",
    )
    reason = models.TextField(
        blank=True,
        help_text="Required when re-releasing an existing release.",
    )

    class Meta:
        db_table = "gradebook_release"
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["setup", "version"], name="uniq_gradebook_release_version"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.setup} — release v{self.version}"


class GradebookResult(BaseModel):
    """One pupil's numbers inside one frozen release."""

    release = models.ForeignKey(
        GradebookRelease, on_delete=models.CASCADE, related_name="results"
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="gradebook_results",
    )
    overall_pct = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Weighted average; blank when the pupil has no scorable item.",
    )
    bucket_pcts = models.JSONField(
        default=dict,
        help_text="Bucket → percentage at release; a bucket with no data is absent.",
    )
    missing_count = models.PositiveSmallIntegerField(default=0)
    excused_count = models.PositiveSmallIntegerField(default=0)
    scored_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "gradebook_result"
        constraints = [
            models.UniqueConstraint(
                fields=["release", "student"], name="uniq_gradebook_result"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} — {self.overall_pct}%"


class GradebookTermLock(BaseModel):
    """
    The admin's lock on one term's gradebook.

    It stops hand-recorded marks, weight and tick changes, releases and the
    copy-to-report-card action. It deliberately does not touch existing result
    sheets or marks entry, which keep their own confirmed/published locks.
    """

    term = models.OneToOneField(
        "academics.Term",
        on_delete=models.CASCADE,
        related_name="gradebook_lock",
    )
    is_locked = models.BooleanField(default=True)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gradebook_term_locks",
    )
    locked_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)

    class Meta:
        db_table = "gradebook_term_lock"

    def __str__(self) -> str:
        state = "locked" if self.is_locked else "open"
        return f"Gradebook {self.term}: {state}"
