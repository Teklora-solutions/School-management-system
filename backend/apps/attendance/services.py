"""
apps/attendance/services.py
---------------------------
The only write path for attendance. Reads live in ``selectors``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.db import models, transaction
from django.utils import timezone

from apps.core.audit_log import log_action
from apps.core.events import record_event

from .models import AttendanceEntry, AttendanceRegister

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.academics.models import SchoolClass
    from apps.accounts.models import User
    from apps.students.models import Student


@transaction.atomic
def record_register(
    *,
    school_class: SchoolClass,
    date,
    marks: Iterable[tuple[Student, str, str]],
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> AttendanceRegister:
    """
    Create or overwrite one class's register for one day.

    ``marks`` is an iterable of ``(student, status, reason)``; the last line for
    a student wins, so a repeated post cannot raise on the unique constraint.
    A student left out simply has no line — deciding whether that is an absence
    belongs to the screen taking attendance, not here.

    Re-running it with the same marks changes no rows: the register is keyed on
    (class, date) and each line is updated in place, so the counts the Director
    reads are never doubled.
    """
    register, _created = AttendanceRegister.objects.get_or_create(
        school_class=school_class,
        date=date,
        defaults={"recorded_by": actor, "recorded_at": timezone.now()},
    )
    # A correction never moves the stamp or steals the authorship; but if the
    # first fill recorded nobody, the caller who does identify themselves claims
    # it, so a register is not permanently anonymous.
    if register.recorded_at is None:
        register.recorded_at = timezone.now()
    if register.recorded_by_id is None and actor is not None:
        register.recorded_by = actor

    by_student: dict[int, tuple[Student, str, str]] = {}
    for student, status, reason in marks:
        by_student[student.pk] = (student, status, (reason or "").strip())

    existing = {entry.student_id: entry for entry in register.entries.all()}

    to_create = [
        AttendanceEntry(
            register=register,
            student=student,
            status=status,
            reason=reason,
            date=date,
        )
        for student_id, (student, status, reason) in by_student.items()
        if student_id not in existing
    ]
    to_update = []
    for student_id, (student, status, reason) in by_student.items():
        entry = existing.get(student_id)
        if entry is not None and (entry.status, entry.reason) != (status, reason):
            entry.status = status
            entry.reason = reason
            entry.date = date
            to_update.append(entry)

    if to_create:
        AttendanceEntry.objects.bulk_create(to_create)
    if to_update:
        AttendanceEntry.objects.bulk_update(to_update, ["status", "reason", "date"])

    register.save(update_fields=["recorded_by", "recorded_at", "updated_at"])

    counts = _count_by_status(register)
    _emit_activity(
        register=register,
        school_class=school_class,
        counts=counts,
        actor=actor,
        request=request,
    )
    return register


def _count_by_status(register: AttendanceRegister) -> dict[str, int]:
    rows = register.entries.values("status").annotate(n=models.Count("id"))
    return {row["status"]: row["n"] for row in rows}


def _emit_activity(*, register, school_class, counts, actor, request) -> None:
    """
    One feed row plus one audit row per register written.

    The feed carries the sentence the admin live view shows; ``AuditLog`` keeps
    the immutable "who marked this, when" record that a disputed register is
    settled with.

    The class label comes from the object the caller passed in — rendering
    ``str(register.school_class)`` would lazily fetch the class and its academic
    year, two queries per register just to spell out a name we already had.
    """
    attended = sum(
        counts.get(status, 0) for status in AttendanceEntry.ATTENDED_STATUSES
    )
    data = {
        "class": school_class.name,
        "date": register.date.isoformat(),
        "present": attended,
        "total": sum(counts.values()),
        "absent": counts.get(AttendanceEntry.Status.ABSENT, 0),
        "excused": counts.get(AttendanceEntry.Status.EXCUSED, 0),
        "late": counts.get(AttendanceEntry.Status.LATE, 0),
    }
    record_event(
        event_type="attendance.register_recorded",
        actor=actor,
        data=data,
        request=request,
    )
    log_action(
        actor=actor,
        action="attendance.register_recorded",
        content_type="attendance.AttendanceRegister",
        object_id=register.pk,
        change_summary=data,
        request=request,
    )
