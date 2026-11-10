"""
apps/learning/live_class_selectors.py
-------------------------------------
Read queries for live class sessions and attendance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.learning.models import LiveClassAttendance, LiveClassSession

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.students.models import Student


def get_live_sessions_for_classroom(
    school_class_id: int,
    subject_id: int,
    *,
    include_cancelled: bool = True,
) -> QuerySet[LiveClassSession]:
    """All sessions for a computed classroom (school_class + subject)."""
    qs = LiveClassSession.objects.filter(
        school_class_id=school_class_id,
        subject_id=subject_id,
    ).select_related("teacher", "school_class", "subject")

    if not include_cancelled:
        qs = qs.exclude(status=LiveClassSession.Status.CANCELLED)

    return qs.order_by("-scheduled_start")


def get_live_session(session_id: int) -> LiveClassSession:
    """Fetch session with related models or 404."""
    return get_object_or_404(
        LiveClassSession.objects.select_related("teacher", "school_class", "subject", "term"),
        pk=session_id,
    )


def get_upcoming_sessions_for_student(
    student: Student,
    *,
    limit: int = 5,
) -> list[LiveClassSession]:
    """Upcoming or live sessions for the student's enrolled class."""
    if not student.current_class_id:
        return []

    now = timezone.now()
    from datetime import timedelta
    # Include sessions starting up to 15 min ago (which are currently running)
    cutoff = now - timedelta(minutes=60)

    qs = (
        LiveClassSession.objects.filter(
            school_class_id=student.current_class_id,
            scheduled_start__gte=cutoff,
        )
        .exclude(status__in=[LiveClassSession.Status.CANCELLED, LiveClassSession.Status.COMPLETED])
        .select_related("teacher", "subject", "school_class")
        .order_by("scheduled_start")
    )
    return list(qs[:limit])


def get_upcoming_sessions_for_teacher(
    teacher: User,
    *,
    limit: int = 5,
) -> list[LiveClassSession]:
    """Upcoming or live sessions hosted by this teacher."""
    now = timezone.now()
    from datetime import timedelta
    cutoff = now - timedelta(minutes=60)

    qs = (
        LiveClassSession.objects.filter(
            teacher=teacher,
            scheduled_start__gte=cutoff,
        )
        .exclude(status__in=[LiveClassSession.Status.CANCELLED, LiveClassSession.Status.COMPLETED])
        .select_related("subject", "school_class")
        .order_by("scheduled_start")
    )
    return list(qs[:limit])


def get_session_attendances(session_id: int) -> QuerySet[LiveClassAttendance]:
    """Students who joined this live session."""
    return LiveClassAttendance.objects.filter(
        session_id=session_id,
    ).select_related("student__user", "student__current_class").order_by("joined_at")


def has_student_joined(session: LiveClassSession, student: Student) -> bool:
    """True if this student has already clicked Join."""
    return LiveClassAttendance.objects.filter(
        session=session,
        student=student,
    ).exists()

