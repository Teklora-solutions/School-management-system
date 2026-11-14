"""
apps/learning/live_class_services.py
------------------------------------
Lifecycle services for live class sessions and student join auditing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.audit_log import log_action
from apps.learning.live_class_utils import (
    normalize_meeting_url,
    validate_meeting_url,
    validate_recording_url,
)
from apps.learning.models import LiveClassAttendance, LiveClassSession

if TYPE_CHECKING:
    import datetime
    from django.http import HttpRequest
    from apps.academics.models import SchoolClass, Subject, Term
    from apps.accounts.models import User
    from apps.students.models import Student


def teacher_can_access_classroom(
    user,
    school_class,
    subject=None,
    academic_year_id: int | None = None,
) -> bool:
    """
    Scope for a teacher's classroom (a class, plus a subject where one applies).

    Admins and directors oversee everything. A teacher needs to be the class
    teacher, or hold a TeacherAssignment for that class — narrowed to the
    subject and academic year when those are given.
    """
    role = getattr(user, "role", "")
    if role in ("admin", "director"):
        return True
    if role != "teacher":
        return False
    if school_class.class_teacher_id == user.pk:
        return True

    from apps.teachers.models import TeacherAssignment

    year_id = (
        academic_year_id
        if academic_year_id is not None
        else school_class.academic_year_id
    )
    if year_id is None:
        return False
    assignments = TeacherAssignment.objects.filter(
        teacher__user=user,
        school_class_id=school_class.pk,
        academic_year_id=year_id,
    )
    if subject is not None:
        assignments = assignments.filter(subject_id=subject.pk)
    return assignments.exists()


def can_view_session_detail(user, session: LiveClassSession) -> bool:
    """
    Object-level scope for the shared live-session detail page.

    Admin and director may oversee any session. A teacher needs an assignment
    for that class and subject in the session's academic year, or to be the
    class teacher. A student must be in the session's class. A parent must
    have at least one child in that class (read-only).
    """
    role = getattr(user, "role", "")
    if role in ("admin", "director"):
        return True

    if role == "teacher":
        year_id = (
            session.term.academic_year_id
            if session.term_id
            else session.school_class.academic_year_id
        )
        return teacher_can_access_classroom(
            user,
            session.school_class,
            session.subject,
            academic_year_id=year_id,
        )

    if role == "student":
        student = getattr(user, "student_profile", None)
        return (
            student is not None
            and student.current_class_id == session.school_class_id
        )

    if role == "parent":
        parent = getattr(user, "parent_profile", None)
        if parent is None:
            return False
        return parent.children_links.filter(
            student__current_class_id=session.school_class_id
        ).exists()

    return False


@transaction.atomic
def schedule_live_session(
    *,
    school_class: SchoolClass,
    subject: Subject,
    teacher: User,
    title: str,
    scheduled_start: datetime.datetime,
    duration_minutes: int = 45,
    platform: str = LiveClassSession.Platform.GOOGLE_MEET,
    meeting_url: str = "",
    meeting_id: str = "",
    passcode: str = "",
    description: str = "",
    term: Term | None = None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> LiveClassSession:
    """Create a new live class session."""
    title = (title or "").strip()
    if not title:
        raise ValidationError({"title": "Session title is required."})

    norm_url = normalize_meeting_url(meeting_url)
    valid, err_msg = validate_meeting_url(norm_url, platform)
    if not valid:
        raise ValidationError({"meeting_url": err_msg})

    if duration_minutes < 5 or duration_minutes > 480:
        raise ValidationError({"duration_minutes": "Duration must be between 5 and 480 minutes."})

    session = LiveClassSession.objects.create(
        school_class=school_class,
        subject=subject,
        teacher=teacher,
        term=term,
        title=title,
        description=(description or "").strip(),
        platform=platform,
        meeting_url=norm_url,
        meeting_id=(meeting_id or "").strip(),
        passcode=(passcode or "").strip(),
        scheduled_start=scheduled_start,
        duration_minutes=duration_minutes,
        status=LiveClassSession.Status.SCHEDULED,
    )

    log_action(
        actor=actor or teacher,
        action="live_class.session_scheduled",
        content_type="learning.LiveClassSession",
        object_id=session.pk,
        change_summary={
            "title": title,
            "class": school_class.name,
            "subject": subject.code,
            "start": scheduled_start.isoformat(),
            "platform": platform,
        },
        request=request,
    )
    return session


@transaction.atomic
def update_live_session(
    *,
    session: LiveClassSession,
    title: str,
    scheduled_start: datetime.datetime,
    duration_minutes: int,
    platform: str,
    meeting_url: str,
    meeting_id: str = "",
    passcode: str = "",
    description: str = "",
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> LiveClassSession:
    """Update details of an upcoming or in-progress session."""
    if session.status == LiveClassSession.Status.CANCELLED:
        raise ValidationError("Cannot edit a cancelled session.")
    if session.status == LiveClassSession.Status.COMPLETED:
        raise ValidationError("Cannot edit a completed session.")

    title = (title or "").strip()
    if not title:
        raise ValidationError({"title": "Session title is required."})

    norm_url = normalize_meeting_url(meeting_url)
    valid, err_msg = validate_meeting_url(norm_url, platform)
    if not valid:
        raise ValidationError({"meeting_url": err_msg})

    if duration_minutes < 5 or duration_minutes > 480:
        raise ValidationError({"duration_minutes": "Duration must be between 5 and 480 minutes."})

    session.title = title
    session.description = (description or "").strip()
    session.platform = platform
    session.meeting_url = norm_url
    session.meeting_id = (meeting_id or "").strip()
    session.passcode = (passcode or "").strip()
    session.scheduled_start = scheduled_start
    session.duration_minutes = duration_minutes
    session.save()

    log_action(
        actor=actor,
        action="live_class.session_updated",
        content_type="learning.LiveClassSession",
        object_id=session.pk,
        change_summary={
            "title": title,
            "platform": platform,
            "start": scheduled_start.isoformat(),
        },
        request=request,
    )
    return session


@transaction.atomic
def cancel_live_session(
    *,
    session: LiveClassSession,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> LiveClassSession:
    """Mark a live class session as cancelled."""
    if session.status == LiveClassSession.Status.CANCELLED:
        return session

    session.status = LiveClassSession.Status.CANCELLED
    session.save(update_fields=["status", "updated_at"])

    log_action(
        actor=actor,
        action="live_class.session_cancelled",
        content_type="learning.LiveClassSession",
        object_id=session.pk,
        change_summary={"title": session.title, "status": "cancelled"},
        request=request,
    )
    return session


@transaction.atomic
def start_live_session(
    *,
    session: LiveClassSession,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> LiveClassSession:
    """Manually flag a session as in progress."""
    if session.status == LiveClassSession.Status.CANCELLED:
        raise ValidationError("Cannot start a cancelled session.")

    session.status = LiveClassSession.Status.IN_PROGRESS
    session.save(update_fields=["status", "updated_at"])

    log_action(
        actor=actor,
        action="live_class.session_started",
        content_type="learning.LiveClassSession",
        object_id=session.pk,
        change_summary={"title": session.title, "status": "in_progress"},
        request=request,
    )
    return session


@transaction.atomic
def end_live_session(
    *,
    session: LiveClassSession,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> LiveClassSession:
    """Mark a live session as completed."""
    session.status = LiveClassSession.Status.COMPLETED
    session.save(update_fields=["status", "updated_at"])

    log_action(
        actor=actor,
        action="live_class.session_completed",
        content_type="learning.LiveClassSession",
        object_id=session.pk,
        change_summary={"title": session.title, "status": "completed"},
        request=request,
    )
    return session


RECORDING_NOTICE_REQUIRED = (
    "Tick the box to confirm you told the class the lesson is being recorded."
)
RECORDING_TOO_EARLY = (
    "You can add a recording once the session has started."
)
RECORDING_CANCELLED = "A cancelled session cannot have a recording."
RECORDING_LESSON_MISMATCH = (
    "Choose a lesson from this class and subject."
)


@transaction.atomic
def attach_recording(
    *,
    session: LiveClassSession,
    recording_url: str,
    lesson=None,
    notice_given: bool = False,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> LiveClassSession:
    """
    Attach a recording link to a session, optionally so it also shows on a
    lesson page.

    The app never records the video; the teacher records in Meet/Zoom and
    pastes the link here, after the session has started and after telling the
    class (``notice_given``).
    """
    if session.status == LiveClassSession.Status.CANCELLED:
        raise ValidationError(RECORDING_CANCELLED)
    if timezone.now() < session.scheduled_start:
        raise ValidationError(RECORDING_TOO_EARLY)
    if not notice_given:
        raise ValidationError(RECORDING_NOTICE_REQUIRED)

    norm_url = normalize_meeting_url(recording_url)
    valid, err_msg = validate_recording_url(norm_url)
    if not valid:
        raise ValidationError({"recording_url": err_msg})

    if lesson is not None and (
        lesson.module.school_class_id != session.school_class_id
        or lesson.module.subject_id != session.subject_id
    ):
        raise ValidationError({"recording_lesson": RECORDING_LESSON_MISMATCH})

    session.recording_url = norm_url
    session.recording_lesson = lesson
    session.recording_added_at = timezone.now()
    session.recording_added_by = actor
    session.recording_notice_acknowledged = True
    session.save(
        update_fields=[
            "recording_url",
            "recording_lesson",
            "recording_added_at",
            "recording_added_by",
            "recording_notice_acknowledged",
            "updated_at",
        ]
    )

    log_action(
        actor=actor,
        action="live_class.recording_added",
        content_type="learning.LiveClassSession",
        object_id=session.pk,
        change_summary={
            "title": session.title,
            "lesson": lesson.title if lesson is not None else "",
        },
        request=request,
    )
    return session


@transaction.atomic
def remove_recording(
    *,
    session: LiveClassSession,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> LiveClassSession:
    """Clear a session's recording link."""
    if not session.recording_url:
        return session

    session.recording_url = ""
    session.recording_lesson = None
    session.recording_added_at = None
    session.recording_added_by = None
    session.recording_notice_acknowledged = False
    session.save(
        update_fields=[
            "recording_url",
            "recording_lesson",
            "recording_added_at",
            "recording_added_by",
            "recording_notice_acknowledged",
            "updated_at",
        ]
    )

    log_action(
        actor=actor,
        action="live_class.recording_removed",
        content_type="learning.LiveClassSession",
        object_id=session.pk,
        change_summary={"title": session.title},
        request=request,
    )
    return session


@transaction.atomic
def record_student_join(
    *,
    session: LiveClassSession,
    student: Student,
    request: HttpRequest | None = None,
) -> LiveClassAttendance:
    """Record student joining the live session."""
    user_agent = ""
    if request:
        user_agent = (request.META.get("HTTP_USER_AGENT", "") or "")[:255]

    attendance, created = LiveClassAttendance.objects.get_or_create(
        session=session,
        student=student,
        defaults={"user_agent": user_agent},
    )

    if created:
        log_action(
            actor=getattr(student, "user", None),
            action="live_class.student_joined",
            content_type="learning.LiveClassAttendance",
            object_id=attendance.pk,
            change_summary={
                "session_id": session.pk,
                "session_title": session.title,
                "student_id": student.pk,
            },
            request=request,
        )
    return attendance

