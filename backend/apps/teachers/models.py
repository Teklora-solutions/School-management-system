"""
apps/teachers/models.py
-----------------------
Teacher profile and subject/class assignments.
"""

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class TeacherProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="teacher_profile",
    )
    staff_id = models.CharField(max_length=30, unique=True)
    qualification = models.CharField(max_length=200, blank=True)
    department = models.CharField(max_length=100, blank=True)
    date_joined = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "teachers_teacher_profile"
        ordering = ["user__last_name", "user__first_name"]

    def __str__(self) -> str:
        return f"Teacher: {self.user.full_name} ({self.staff_id})"


class TeacherAssignment(BaseModel):
    """
    Teacher ? Class ? Subject assignment.
    Duplicates are prevented by unique_together.
    """

    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    school_class = models.ForeignKey(
        "academics.SchoolClass",
        on_delete=models.CASCADE,
        related_name="teacher_assignments",
    )
    subject = models.ForeignKey(
        "academics.Subject",
        on_delete=models.CASCADE,
        related_name="teacher_assignments",
    )
    stream = models.ForeignKey(
        "academics.Stream",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="teacher_assignments",
    )
    academic_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.CASCADE,
        related_name="teacher_assignments",
    )

    class Meta:
        db_table = "teachers_assignment"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "teacher",
                    "school_class",
                    "subject",
                    "stream",
                    "academic_year",
                ],
                name="uniq_teacher_class_subject_stream_year",
            ),
            models.UniqueConstraint(
                fields=["teacher", "school_class", "subject", "academic_year"],
                condition=models.Q(stream__isnull=True),
                name="uniq_teacher_class_subject_year_no_stream",
            ),
            # One teacher per class+subject(+stream) per year
            models.UniqueConstraint(
                fields=["school_class", "subject", "stream", "academic_year"],
                name="uniq_class_subject_stream_year_teacher",
            ),
            models.UniqueConstraint(
                fields=["school_class", "subject", "academic_year"],
                condition=models.Q(stream__isnull=True),
                name="uniq_class_subject_year_teacher_no_stream",
            ),
        ]

    def __str__(self) -> str:
        stream = f"/{self.stream.name}" if self.stream_id else ""
        return (
            f"{self.teacher.user.full_name} ? "
            f"{self.school_class.name}{stream} / {self.subject.code}"
        )
