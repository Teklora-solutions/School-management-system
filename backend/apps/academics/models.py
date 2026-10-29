"""
apps/academics/models.py
------------------------
Academic years, terms, classes, streams, subjects, and class–subject mapping.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.core.models import BaseModel

from . import calendar


class AcademicYear(BaseModel):
    name = models.CharField(max_length=32, unique=True)  # e.g. "2025/2026"
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "academics_academic_year"
        ordering = ["-start_date"]

    def __str__(self) -> str:
        return self.name

    @property
    def from_calendar_picker(self) -> bool:
        """False for rows created before the year dropdown replaced free text."""
        return calendar.parse_start_year(self.name) is not None

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError(
                {"end_date": "End date must be on or after start date."}
            )

    @transaction.atomic
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_current:
            AcademicYear.objects.exclude(pk=self.pk).filter(is_current=True).update(
                is_current=False
            )


class Term(BaseModel):
    class TermName(models.TextChoices):
        TERM_1 = "term_1", "Term 1"
        TERM_2 = "term_2", "Term 2"
        TERM_3 = "term_3", "Term 3"

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="terms"
    )
    name = models.CharField(max_length=20, choices=TermName.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "academics_term"
        ordering = ["academic_year", "start_date"]
        unique_together = [("academic_year", "name")]

    def __str__(self) -> str:
        return f"{self.academic_year} – {self.get_name_display()}"

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError(
                {"end_date": "End date must be on or after start date."}
            )

    @transaction.atomic
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_current:
            Term.objects.exclude(pk=self.pk).filter(is_current=True).update(
                is_current=False
            )


class Subject(BaseModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "academics_subject"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.code} – {self.name}"


class SchoolClass(BaseModel):
    """
    A class/grade within an academic year (e.g. Grade 5).
    ``level_order`` drives promotion sequencing (higher = more advanced).
    """

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="classes"
    )
    name = models.CharField(max_length=64)  # e.g. "Grade 5"
    level_order = models.PositiveSmallIntegerField(
        help_text="Promotion order; higher numbers are more advanced (e.g. Grade 6 = 6)."
    )
    class_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="homeroom_classes",
        limit_choices_to={"role": "teacher"},
    )
    grade_scale = models.ForeignKey(
        "results.GradeScale",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="classes",
        help_text="Optional shared grading scale; falls back to the year's grade bands.",
    )

    class Meta:
        db_table = "academics_school_class"
        ordering = ["academic_year", "level_order", "name"]
        unique_together = [("academic_year", "name")]
        verbose_name_plural = "school classes"

    def __str__(self) -> str:
        return f"{self.name} ({self.academic_year})"


class Stream(BaseModel):
    """Optional subdivision of a class (e.g. Blue, Gold)."""

    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, related_name="streams"
    )
    name = models.CharField(max_length=64)

    class Meta:
        db_table = "academics_stream"
        ordering = ["school_class", "name"]
        unique_together = [("school_class", "name")]

    def __str__(self) -> str:
        return f"{self.school_class.name} / {self.name}"


class ClassSubject(BaseModel):
    """Which subjects are taught in a class (optionally per stream)."""

    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, related_name="class_subjects"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="class_subjects"
    )
    stream = models.ForeignKey(
        Stream,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="class_subjects",
    )

    class Meta:
        db_table = "academics_class_subject"
        constraints = [
            models.UniqueConstraint(
                fields=["school_class", "subject", "stream"],
                name="uniq_class_subject_stream",
            ),
            # When stream is NULL, still unique on (class, subject)
            models.UniqueConstraint(
                fields=["school_class", "subject"],
                condition=models.Q(stream__isnull=True),
                name="uniq_class_subject_no_stream",
            ),
        ]

    def __str__(self) -> str:
        stream = f" / {self.stream.name}" if self.stream_id else ""
        return f"{self.school_class.name}{stream}: {self.subject.code}"
