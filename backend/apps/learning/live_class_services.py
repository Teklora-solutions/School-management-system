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
from apps.learning.live_class_utils import normalize_meeting_url, validate_meeting_url
from apps.learning.models import LiveClassAttendance, LiveClassSession

if TYPE_CHECKING:
    import datetime
    from django.http import HttpRequest
    from apps.academics.models import SchoolClass, Subject, Term
    from apps.accounts.models import User
    from apps.students.models import Student


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

