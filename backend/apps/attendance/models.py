"""
apps/attendance/models.py
-------------------------
Daily attendance, in two tables that mirror how the register is actually filled.

``AttendanceRegister`` is one class on one day — the sheet somebody completes.
``AttendanceEntry`` is that sheet's line for one student. Splitting them is what
makes the Director's "was the register even filled?" question answerable: the
absence of a register is itself a fact, and a count of entries alone cannot show
it.

``AttendanceEntry.date`` is copied from its register on write. That is
deliberate denormalisation: every analytics query groups or filters by date
(weekdays, terms, chronic-absence windows) and would otherwise join through the
register for a column that never changes after the entry is written.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import BaseModel


class AttendanceRegister(BaseModel):
    """One class's attendance for one day."""

    school_class = models.ForeignKey(
        "academics.SchoolClass",
        on_delete=models.CASCADE,
        related_name="attendance_registers",
    )
    date = models.DateField(db_index=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="attendance_registers_recorded",
    )
    # Set the first time the register is filled and left alone afterwards, so
    # "was it filled on time" is not answered by the latest edit.
    recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "attendance_register"
        # Same reason as ``AttendanceEntry``: this ordering is applied to every
        # queryset over the model, including the compliance aggregate, and sorting
        # by the class would join ``academics`` for nothing.
        ordering = ["-date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["school_class", "date"],
                name="attendance_one_register_per_class_per_day",
            )
        ]

    def __str__(self) -> str:
        return f"{self.school_class} - {self.date:%d/%m/%Y}"

    def clean(self) -> None:
        if self.date and self.date > self._today():
            raise ValidationError({"date": "Attendance cannot be taken for a future day."})

    @staticmethod
    def _today():
        from django.utils import timezone

        return timezone.localdate()


class AttendanceEntry(BaseModel):
    """One student's mark on one register."""

    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        LATE = "late", "Late"
        EXCUSED = "excused", "Excused"

    #: Statuses counted as "in school" for an attendance rate. Late counts as
    #: present; an excused absence stays out of the numerator so a rate is not
    #: flattered by approved absences.
    ATTENDED_STATUSES = (Status.PRESENT, Status.LATE)

    register = models.ForeignKey(
        AttendanceRegister, on_delete=models.CASCADE, related_name="entries"
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="attendance_entries",
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PRESENT, db_index=True
    )
    reason = models.CharField(max_length=200, blank=True)
    date = models.DateField(db_index=True)

    class Meta:
        db_table = "attendance_entry"
        # Never ordered by a related field: ``Meta.ordering`` is applied to
        # every queryset over this model, including the aggregates, and a join
        # just to sort is paid by every attendance report. Screens that want
        # pupils in admission order say so themselves.
        ordering = ["-date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["register", "student"],
                name="attendance_one_entry_per_student_per_register",
            )
        ]
        indexes = [
            models.Index(fields=["student", "date"]),
            models.Index(fields=["date", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.status} ({self.date})"
