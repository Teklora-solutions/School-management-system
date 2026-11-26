from __future__ import annotations

from django.utils import timezone

from ._common import ToolError, _int_param, _require, _resolve_child

# ── Parent and student tools ─────────────────────────────────────────────────

def _parent_child_attendance(user, params: dict) -> dict:
    """Attendance summary for one child: days present/absent in last N days."""
    child_id = _int_param(params, "child_id")
    days = _int_param(params, "days", default=30, min_val=1, max_val=90)
    student = _resolve_child(user, child_id)

    import datetime
    from apps.attendance.selectors import student_history
    from apps.attendance.models import AttendanceEntry

    today = timezone.localdate()
    date_from = today - datetime.timedelta(days=days)
    history = list(student_history(student=student, date_from=date_from, date_to=today))
    total = len(history)
    present = sum(1 for e in history if e.status in AttendanceEntry.ATTENDED_STATUSES)
    pct = round(present * 100 / total) if total else None

    return {
        "child_name": student.user.get_full_name(),
        "class_name": student.current_class.name if student.current_class else "",
        "days_checked": days,
        "total_marked": total,
        "days_present": present,
        "days_absent": total - present,
        "attendance_pct": pct,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _parent_child_homework(user, params: dict) -> dict:
    """Homework due and overdue for one child."""
    child_id = _int_param(params, "child_id")
    student = _resolve_child(user, child_id)

    from apps.homework.selectors import homework_due_soon, mark_new
    from apps.academics.selectors import get_current_term

    term = get_current_term()
    items = []
    if student.current_class_id and term:
        raw = list(homework_due_soon(
            school_class_id=student.current_class_id,
            stream_id=student.current_stream_id,
            term_id=term.pk,
            days=14,
        ))
        raw = mark_new(raw)
        items = [
            {
                "title": hw.title,
                "subject": hw.subject.name,
                "due_date": hw.due_date.isoformat(),
                "is_overdue": hw.is_overdue if hasattr(hw, "is_overdue") else False,
                "is_new": getattr(hw, "is_new", False),
            }
            for hw in raw
        ]

    return {
        "child_name": student.user.get_full_name(),
        "homework": items,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _parent_child_fee_balance(user, params: dict) -> dict:
    """Fee balance for one child."""
    child_id = _int_param(params, "child_id")
    student = _resolve_child(user, child_id)

    try:
        from apps.finance.interface import student_balance
        balance = student_balance(student_id=student.pk)
    except Exception:
        balance = None

    return {
        "child_name": student.user.get_full_name(),
        "balance": str(balance) if balance is not None else "unavailable",
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _parent_child_upcoming_classes(user, params: dict) -> dict:
    """Upcoming live classes for one child."""
    child_id = _int_param(params, "child_id")
    student = _resolve_child(user, child_id)

    from apps.learning.live_class_selectors import get_upcoming_sessions_for_student
    sessions = get_upcoming_sessions_for_student(student, limit=5)
    return {
        "child_name": student.user.get_full_name(),
        "upcoming_classes": [
            {
                "title": s.title,
                "subject": s.subject.name,
                "platform": s.get_platform_display(),
                "scheduled_start": s.scheduled_start.isoformat(),
                "is_live": s.is_live,
            }
            for s in sessions
        ],
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _parent_child_quiz_results(user, params: dict) -> dict:
    """Recently released quiz results for one child."""
    child_id = _int_param(params, "child_id")
    student = _resolve_child(user, child_id)

    try:
        from apps.homework.selectors import released_marks_for_student
        from apps.academics.selectors import get_current_term
        term = get_current_term()
        marks = list(released_marks_for_student(
            student_id=student.pk, term_id=term.pk if term else None
        )[:10])
        result = [
            {
                "homework": m.homework.title,
                "subject": m.homework.subject.name,
                "score": str(m.score) if m.score is not None else None,
                "max_score": str(m.homework.max_score) if m.homework.max_score else None,
                "status": m.status,
            }
            for m in marks
        ]
    except Exception:
        result = []

    return {
        "child_name": student.user.get_full_name(),
        "quiz_marks": result,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _school_announcements(user, params: dict) -> dict:
    """Recent school announcements for the calling user."""
    from apps.communications.selectors import announcements_for_user
    items = list(announcements_for_user(user=user).order_by("-created_at")[:5])
    return {
        "announcements": [
            {
                "title": a.title,
                "date": a.created_at.strftime("%d %b %Y") if a.created_at else "",
                "is_read": a.is_read,
            }
            for a in items
        ],
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _parent_school_events(user, params: dict) -> dict:
    """Upcoming school events (from the public site event list)."""
    from apps.public.models import EventItem
    events = list(EventItem.objects.filter(is_active=True).order_by("display_order")[:6])
    return {
        "events": [
            {"day": e.day, "month": e.month, "title": e.title, "description": e.description}
            for e in events
        ],
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


# ── Student tools ─────────────────────────────────────────────────────────────

def _student_due_work(user, params: dict) -> dict:
    """Homework and quizzes due for the calling student."""
    student = _resolve_child(user, None)
    from apps.homework.selectors import homework_due_soon, mark_new
    from apps.academics.selectors import get_current_term

    term = get_current_term()
    items = []
    if student.current_class_id and term:
        raw = list(homework_due_soon(
            school_class_id=student.current_class_id,
            stream_id=student.current_stream_id,
            term_id=term.pk, days=14,
        ))
        items = [
            {"title": hw.title, "subject": hw.subject.name,
             "due_date": hw.due_date.isoformat()}
            for hw in raw
        ]
    return {
        "due_work": items,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _student_progress(user, params: dict) -> dict:
    """The student's latest result summary."""
    student = _resolve_child(user, None)
    from apps.results.selectors import student_latest_result_summary
    summary = student_latest_result_summary(student_id=student.pk)
    if summary is None:
        return {"progress": None, "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M")}
    return {
        "progress": {
            "term": str(summary["term"]),
            "average": summary["average"],
            "class_position": summary.get("class_position"),
            "total_in_class": summary.get("total_in_class"),
            "subject_count": summary.get("subject_count"),
        },
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _student_upcoming_classes(user, params: dict) -> dict:
    """Upcoming live classes for the calling student."""
    student = _resolve_child(user, None)
    from apps.learning.live_class_selectors import get_upcoming_sessions_for_student
    sessions = get_upcoming_sessions_for_student(student, limit=5)
    return {
        "upcoming_classes": [
            {"title": s.title, "subject": s.subject.name,
             "scheduled_start": s.scheduled_start.isoformat(), "is_live": s.is_live}
            for s in sessions
        ],
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _student_points_badges(user, params: dict) -> dict:
    """Points and badges for the calling student."""
    student = _resolve_child(user, None)
    try:
        from apps.learning.points_selectors import student_points_summary
        summary = student_points_summary(student=student)
        return {
            "points": summary.get("total_points", 0),
            "level": summary.get("level_name", ""),
            "badge_count": summary.get("badge_count", 0),
            "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
        }
    except Exception:
        return {"points": None, "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M")}



# ── Timetable / lessons / materials ───────────────────────────────────────────

def _timetable_for_student(student) -> dict:
    """Subjects the student's class takes this term (the school's 'timetable')."""
    from apps.learning.selectors import classrooms_for_student
    data = classrooms_for_student(student=student)
    return {
        "class_name": student.current_class.name if student.current_class else "",
        "subjects": [
            {
                "subject": c.subject.name,
                "stream": c.stream_name,
                "teacher": c.teacher_name,
            }
            for c in data.get("classrooms", [])
        ],
        "term": str(data.get("term")) if data.get("term") else "",
    }


def _student_timetable(user, params: dict) -> dict:
    student = _resolve_child(user, None)
    result = _timetable_for_student(student)
    result["fetched_at"] = timezone.now().strftime("%Y-%m-%d %H:%M")
    return result


def _parent_child_timetable(user, params: dict) -> dict:
    child_id = _int_param(params, "child_id")
    student = _resolve_child(user, child_id)
    result = _timetable_for_student(student)
    result["child_name"] = student.user.get_full_name()
    result["fetched_at"] = timezone.now().strftime("%Y-%m-%d %H:%M")
    return result


def _next_lesson_for(student) -> dict | None:
    """First published, not-yet-completed lesson across the student's classrooms."""
    from apps.learning.selectors import classrooms_for_student
    from apps.learning.course_selectors import (
        course_for_student, ordered_course_lessons,
    )
    data = classrooms_for_student(student=student)
    for classroom in data.get("classrooms", [])[:5]:
        modules = course_for_student(
            school_class=classroom.school_class,
            subject=classroom.subject,
            term=classroom.term,
            student=student,
        )
        for lp in ordered_course_lessons(modules):
            if not lp.completed:
                return {
                    "title": lp.lesson.title,
                    "subject": classroom.subject.name,
                    "class_name": student.current_class.name
                    if student.current_class else "",
                }
    return None


def _student_next_lesson(user, params: dict) -> dict:
    student = _resolve_child(user, None)
    lesson = _next_lesson_for(student)
    return {
        "next_lesson": lesson,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _parent_child_next_lesson(user, params: dict) -> dict:
    child_id = _int_param(params, "child_id")
    student = _resolve_child(user, child_id)
    lesson = _next_lesson_for(student)
    return {
        "child_name": student.user.get_full_name(),
        "next_lesson": lesson,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _student_class_materials(user, params: dict) -> dict:
    """Published lesson titles + short notes the student may see (tutor grounding)."""
    student = _resolve_child(user, None)
    from apps.learning.selectors import classrooms_for_student
    from apps.learning.course_selectors import course_for_student

    data = classrooms_for_student(student=student)
    materials = []
    for classroom in data.get("classrooms", [])[:3]:
        modules = course_for_student(
            school_class=classroom.school_class,
            subject=classroom.subject,
            term=classroom.term,
            student=student,
        )
        count = 0
        for mwl in modules:
            for lp in mwl.lessons:
                if count >= 8:
                    break
                notes = (lp.lesson.notes or "").strip()
                materials.append({
                    "subject": classroom.subject.name,
                    "title": lp.lesson.title,
                    "completed": lp.completed,
                    "notes_excerpt": notes[:400],
                })
                count += 1
    return {
        "materials": materials[:12],
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _parent_child_fee_next_due(user, params: dict) -> dict:
    """Earliest unpaid posted invoice due date for one child."""
    child_id = _int_param(params, "child_id")
    student = _resolve_child(user, child_id)
    from apps.finance.models import Invoice

    today = timezone.localdate()
    unpaid = [
        inv for inv in Invoice.objects.filter(
            student=student, status=Invoice.Status.POSTED
        ).order_by("due_date", "issued_date")
        if inv.amount_outstanding > 0
    ]
    upcoming = [inv for inv in unpaid if inv.due_date and inv.due_date >= today]
    next_due = upcoming[0] if upcoming else (unpaid[0] if unpaid else None)
    return {
        "child_name": student.user.get_full_name(),
        "next_due_date": next_due.due_date.isoformat() if next_due and next_due.due_date else None,
        "next_due_amount": str(next_due.amount_outstanding) if next_due else None,
        "overdue_count": sum(1 for inv in unpaid if inv.due_date and inv.due_date < today),
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _parent_child_week_summary(user, params: dict) -> dict:
    """'This week' digest for one child: attendance, homework, classes, events."""
    child_id = _int_param(params, "child_id")
    student = _resolve_child(user, child_id)

    import datetime
    from apps.attendance.selectors import student_history
    from apps.attendance.models import AttendanceEntry
    from apps.homework.selectors import homework_due_soon
    from apps.academics.selectors import get_current_term
    from apps.learning.live_class_selectors import get_upcoming_sessions_for_student

    today = timezone.localdate()
    week_ago = today - datetime.timedelta(days=7)
    history = list(student_history(student=student, date_from=week_ago, date_to=today))
    attended = sum(1 for e in history if e.status in AttendanceEntry.ATTENDED_STATUSES)

    hw_due = 0
    term = get_current_term()
    if student.current_class_id and term:
        hw_due = len(list(homework_due_soon(
            school_class_id=student.current_class_id,
            stream_id=student.current_stream_id,
            term_id=term.pk, days=7,
        )))
    live = len(get_upcoming_sessions_for_student(student, limit=5))

    return {
        "child_name": student.user.get_full_name(),
        "class_name": student.current_class.name if student.current_class else "",
        "attendance_this_week": {
            "days_marked": len(history),
            "days_present": attended,
        },
        "homework_due_this_week": hw_due,
        "upcoming_live_classes": live,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


