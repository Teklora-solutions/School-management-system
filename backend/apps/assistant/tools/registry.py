"""
apps/assistant/tools/registry.py
----------------------------------
The tool registry.  Every tool in this file is:
  - read-only (no mutations)
  - scoped to the calling user's own data (never another person's)
  - argument-validated before the selector is called
  - audit-logged (tool name, outcome, field names returned -- never values)
  - permission-checked inside the tool, not just at the view level

Design rules (from architecture.md section 6):
  * The model never receives a user ID from its own output.
  * Arguments come from the conversation turn; they are validated here.
  * Selectors are called with the acting user's identity from the Django
    session, not from any string the model produced.
  * Results are minimised: only fields the prompt needs are included.
  * Sensitive reads are logged in ToolCallLog (see models.py).
  * No free-form SQL; no arbitrary model references.

Adding a tool
-------------
1. Write a function ``_tool_<name>(user, params) -> dict`` below.
2. Register it in REGISTRY.
3. Add its name to the right role set in policies.py.
4. Write a test in tests/test_tools.py that proves it cannot leak data.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registry entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str                    # shown in the system prompt tool catalogue
    allowed_roles: frozenset[str]       # which roles may call this tool
    params: dict                        # JSON-schema-style param definitions
    fn: Any                             # callable(user, params) -> dict
    result_fields: tuple[str, ...]      # expected top-level result keys (for audit log)


# ---------------------------------------------------------------------------
# Parameter validation helpers
# ---------------------------------------------------------------------------

class ToolError(ValueError):
    """Raised when a tool call cannot proceed; the message is safe to surface."""
    code: str = "invalid"

    def __init__(self, msg: str, *, code: str = "invalid"):
        self.code = code
        super().__init__(msg)


def _require(params: dict, name: str, *, type_=str):
    """Validate a required parameter exists and is the right type."""
    if name not in params:
        raise ToolError("Missing required parameter: %s" % name)
    val = params[name]
    if not isinstance(val, type_):
        raise ToolError("Parameter %s must be %s" % (name, type_.__name__))
    return val


def _int_param(params: dict, name: str, *, default=None, min_val=None, max_val=None):
    val = params.get(name, default)
    if val is None:
        return None
    try:
        val = int(val)
    except (TypeError, ValueError):
        raise ToolError("Parameter %s must be an integer" % name)
    if min_val is not None and val < min_val:
        raise ToolError("Parameter %s must be >= %d" % (name, min_val))
    if max_val is not None and val > max_val:
        raise ToolError("Parameter %s must be <= %d" % (name, max_val))
    return val


def _resolve_child(user, child_id: int | None):
    """
    Resolve a Student for a parent user.  If child_id is None, use the
    session-active child.  Raises ToolError if the child is not linked to
    this parent.  NEVER trusts child_id from the model's text alone.
    """
    from apps.students.models import Student

    role = getattr(user, "role", "")
    if role == "student":
        profile = getattr(user, "student_profile", None)
        if profile is None:
            raise ToolError("No student profile linked to this account.", code="denied")
        return profile

    if role == "parent":
        parent = getattr(user, "parent_profile", None)
        if parent is None:
            raise ToolError("No parent profile linked to this account.", code="denied")
        linked_ids = set(
            parent.children_links.values_list("student_id", flat=True)
        )
        if not linked_ids:
            raise ToolError("No children are linked to this account.", code="denied")
        if child_id is None:
            # Use the first linked child
            child_id = sorted(linked_ids)[0]
        if child_id not in linked_ids:
            raise ToolError(
                "You are not authorised to view this child's information.", code="denied"
            )
        return Student.objects.select_related(
            "user", "current_class", "current_stream"
        ).get(pk=child_id)

    raise ToolError("This tool is not available for your role.", code="denied")


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

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


# ── Teacher tools ─────────────────────────────────────────────────────────────

def _teacher_unmarked_homework(user, params: dict) -> dict:
    """Homework the teacher has set that still has unmarked submissions."""
    if getattr(user, "role", "") != "teacher":
        raise ToolError("This tool is only available to teachers.", code="denied")
    from apps.homework.selectors import teacher_assignments
    assignments = teacher_assignments(teacher_user_id=user.pk)
    result = []
    for a in assignments:
        # Get homework with pending marks
        try:
            from apps.homework.models import Homework, HomeworkSubmission
            from apps.academics.selectors import get_current_term
            term = get_current_term()
            if not term:
                continue
            hw_qs = Homework.objects.filter(
                school_class_id=a.school_class_id,
                subject_id=a.subject_id,
                term=term,
                status=Homework.Status.PUBLISHED,
                max_score__isnull=False,
            )
            for hw in hw_qs:
                ungraded = HomeworkSubmission.objects.filter(
                    homework=hw
                ).exclude(marks__isnull=False).count()
                if ungraded:
                    result.append({
                        "title": hw.title,
                        "subject": hw.subject.name,
                        "ungraded_count": ungraded,
                        "due_date": hw.due_date.isoformat(),
                    })
        except Exception:
            pass
    return {
        "unmarked_homework": result[:20],
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _teacher_unanswered_questions(user, params: dict) -> dict:
    """Unanswered class-feed questions in the teacher's classes."""
    if getattr(user, "role", "") not in ("teacher", "admin", "director"):
        raise ToolError("This tool is only available to teachers.", code="denied")
    try:
        from apps.learning.feed_selectors import classrooms_for_feed, can_view_feed
        from apps.learning.models import ClassPost
        classes = classrooms_for_feed(user)
        result = []
        for cls in classes[:5]:
            unanswered = ClassPost.objects.filter(
                school_class=cls,
                post_type=ClassPost.PostType.QUESTION,
                is_removed=False,
            ).exclude(
                replies__is_removed=False
            ).count()
            if unanswered:
                result.append({"class": cls.name, "unanswered": unanswered})
    except Exception:
        result = []
    return {
        "unanswered_questions": result,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _teacher_todays_classes(user, params: dict) -> dict:
    """Today's live classes scheduled for the calling teacher."""
    if getattr(user, "role", "") != "teacher":
        raise ToolError("This tool is only available to teachers.", code="denied")
    from apps.learning.live_class_selectors import get_upcoming_sessions_for_teacher
    sessions = get_upcoming_sessions_for_teacher(user, limit=10)
    today = timezone.localdate()
    todays = [s for s in sessions if s.scheduled_start.date() == today]
    return {
        "todays_classes": [
            {"title": s.title, "subject": s.subject.name,
             "class": s.school_class.name,
             "time": s.scheduled_start.strftime("%H:%M"),
             "is_live": s.is_live}
            for s in todays
        ],
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _teacher_class_progress(user, params: dict) -> dict:
    """Average completion across the teacher's classes."""
    if getattr(user, "role", "") != "teacher":
        raise ToolError("This tool is only available to teachers.", code="denied")
    try:
        from apps.learning.selectors import classrooms_for_teacher
        data = classrooms_for_teacher(teacher_user_id=user.pk)
        result = [
            {"class": c.school_class.name, "subject": c.subject.name,
             "students": c.student_count}
            for c in data.get("classrooms", [])
        ]
    except Exception:
        result = []
    return {
        "classrooms": result[:20],
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


# ── Director tools ────────────────────────────────────────────────────────────

def _director_school_summary(user, params: dict) -> dict:
    """High-level school numbers for the director."""
    if getattr(user, "role", "") not in ("director", "admin"):
        raise ToolError("This tool is only available to directors and admins.", code="denied")
    from apps.students.models import Student
    from apps.accounts.models import User
    from apps.academics.selectors import get_current_term

    term = get_current_term()
    return {
        "active_students": Student.objects.filter(
            status__in=[Student.Status.ACTIVE, Student.Status.REPEATING]
        ).count(),
        "teaching_staff": User.objects.filter(role="teacher", is_active=True).count(),
        "current_term": str(term) if term else None,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _director_handoff_queue(user, params: dict) -> dict:
    """Assistant handoff queue health for the director."""
    if getattr(user, "role", "") not in ("director", "admin"):
        raise ToolError("This tool is only available to directors and admins.", code="denied")
    from apps.assistant.models import Handoff, HandoffPriority
    from django.db.models import Count

    open_qs = Handoff.objects.exclude(status=Handoff.Status.RESOLVED)
    total = open_qs.count()
    critical = open_qs.filter(priority=HandoffPriority.CRITICAL).count()
    overdue = sum(1 for h in open_qs if h.is_overdue)
    return {
        "open_handoffs": total,
        "critical": critical,
        "overdue": overdue,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _director_attendance_trend(user, params: dict) -> dict:
    """School-wide attendance rate for the last 7 days."""
    if getattr(user, "role", "") not in ("director", "admin"):
        raise ToolError("This tool is only available to directors and admins.", code="denied")
    import datetime
    from apps.attendance.selectors import summarise

    today = timezone.localdate()
    date_from = today - datetime.timedelta(days=7)
    try:
        summary = summarise(date_from=date_from, date_to=today)
        return {
            "attendance_pct": summary.get("pct"),
            "total_marked": summary.get("total"),
            "days": 7,
            "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
        }
    except Exception:
        return {"attendance_pct": None, "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M")}


# ── Admin tools ───────────────────────────────────────────────────────────────

def _admin_assistant_usage(user, params: dict) -> dict:
    """Assistant usage and cost summary for the admin."""
    if getattr(user, "role", "") != "admin":
        raise ToolError("This tool is only available to admins.", code="denied")
    from apps.assistant.limits import month_spend_ksh
    from apps.assistant.models import UsageEvent
    from apps.assistant.selectors import assistant_settings

    settings_row = assistant_settings()
    return {
        "month_spend_ksh": str(month_spend_ksh()),
        "monthly_budget_ksh": str(settings_row.monthly_budget_ksh),
        "total_calls_today": UsageEvent.objects.filter(
            created_at__date=timezone.localdate()
        ).count(),
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _admin_knowledge_gaps(user, params: dict) -> dict:
    """Recent knowledge gaps the assistant could not answer."""
    if getattr(user, "role", "") != "admin":
        raise ToolError("This tool is only available to admins.", code="denied")
    from apps.assistant.knowledge.gaps import open_gaps
    gaps = list(open_gaps(include_tests=False)[:10])
    return {
        "open_gaps": [
            {"question": g.question[:80], "reason": g.reason,
             "language": g.language_hint}
            for g in gaps
        ],
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _admin_pending_approvals(user, params: dict) -> dict:
    """Knowledge documents awaiting admin approval."""
    if getattr(user, "role", "") != "admin":
        raise ToolError("This tool is only available to admins.", code="denied")
    from apps.assistant.models import KnowledgeDocument, KnowledgeStatus
    pending = KnowledgeDocument.objects.filter(
        status=KnowledgeStatus.DRAFT
    ).order_by("-created_at")[:10]
    return {
        "pending_documents": [
            {"title": d.title, "collection": d.collection.name if d.collection else ""}
            for d in pending
        ],
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _admin_system_health(user, params: dict) -> dict:
    """Background job failures and assistant health for the admin."""
    if getattr(user, "role", "") != "admin":
        raise ToolError("This tool is only available to admins.", code="denied")
    from apps.assistant.models import AssistantJobFailure, SafeguardingAlert
    unresolved_failures = AssistantJobFailure.objects.filter(
        resolved_at__isnull=True
    ).count()
    open_alerts = SafeguardingAlert.objects.filter(
        status=SafeguardingAlert.Status.OPEN
    ).count()
    return {
        "unresolved_job_failures": unresolved_failures,
        "open_safeguarding_alerts": open_alerts,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


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


# ── Teacher: unfinished items ─────────────────────────────────────────────────

def _teacher_unfinished_items(user, params: dict) -> dict:
    """Published homework whose targeted students have not submitted yet."""
    if getattr(user, "role", "") != "teacher":
        raise ToolError("This tool is only available to teachers.", code="denied")
    from apps.homework.models import Homework, HomeworkSubmission
    from apps.homework.selectors import teacher_assignments
    from apps.academics.selectors import get_current_term
    from apps.students.models import Student

    term = get_current_term()
    if not term:
        return {"unfinished": [], "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M")}

    result = []
    for a in teacher_assignments(teacher_user_id=user.pk):
        homework_qs = Homework.objects.filter(
            school_class_id=a.school_class_id,
            subject_id=a.subject_id,
            term=term,
            status=Homework.Status.PUBLISHED,
        ).select_related("school_class", "subject", "stream")
        for hw in homework_qs:
            students_qs = Student.objects.filter(
                current_class_id=hw.school_class_id,
                status__in=[Student.Status.ACTIVE, Student.Status.REPEATING],
            )
            if hw.stream_id:
                students_qs = students_qs.filter(current_stream_id=hw.stream_id)
            submitted_ids = set(
                HomeworkSubmission.objects.filter(homework=hw)
                .values_list("student_id", flat=True)
            )
            missing = [
                s.user.get_full_name() for s in students_qs
                if s.pk not in submitted_ids
            ]
            if missing:
                result.append({
                    "title": hw.title,
                    "subject": hw.subject.name,
                    "class_name": hw.school_class.name,
                    "due_date": hw.due_date.isoformat(),
                    "not_submitted": len(missing),
                    "names": missing[:10],
                })
    return {
        "unfinished": result[:20],
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


# ── Director: community queue + engagement trend ─────────────────────────────

def _director_community_queue(user, params: dict) -> dict:
    """Community moderation backlog: posts awaiting approval and open reports."""
    if getattr(user, "role", "") not in ("director", "admin"):
        raise ToolError("This tool is only available to directors and admins.", code="denied")
    try:
        from apps.community import selectors as community_selectors
        pending = len(community_selectors.pending_posts())
        reports = len(community_selectors.open_reports())
    except Exception:
        pending = reports = None
    return {
        "pending_posts": pending,
        "open_reports": reports,
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _director_engagement_trend(user, params: dict) -> dict:
    """Learning engagement over the last 7 days: lessons done, quiz attempts, homework."""
    if getattr(user, "role", "") not in ("director", "admin"):
        raise ToolError("This tool is only available to directors and admins.", code="denied")
    import datetime
    from django.db.models import Count
    from apps.learning.models import LessonProgress, QuizAttempt
    from apps.homework.models import HomeworkSubmission

    since = timezone.now() - datetime.timedelta(days=7)
    return {
        "lessons_completed_7d": LessonProgress.objects.filter(
            completed=True, updated_at__gte=since
        ).count(),
        "quiz_attempts_7d": QuizAttempt.objects.filter(
            submitted_at__gte=since
        ).count(),
        "homework_submissions_7d": HomeworkSubmission.objects.filter(
            submitted_at__gte=since
        ).count(),
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


# ── Tutor usage summaries (counts and topics only — never message text) ───────

def _tutor_topic(text: str) -> str:
    """Coarse topic bucket for a masked question; never returns the text."""
    t = (text or "").lower()
    for topic, words in (
        ("fees", ("fee", "fees", "payment", "balance", "owe")),
        ("homework", ("homework", "assignment", "due")),
        ("results", ("mark", "score", "result", "grade", "exam")),
        ("attendance", ("attendance", "absent", "present")),
        ("learning", ("lesson", "class", "learn", "study", "quiz")),
        ("events", ("event", "trip", "meeting", "announcement")),
    ):
        if any(w in t for w in words):
            return topic
    return "general"


def _tutor_usage_for_students(student_ids: list[int]) -> dict:
    """Counts + topic buckets of tutor-mode turns for the given students."""
    import datetime
    from apps.assistant.models import SignedInMessage
    from apps.students.models import Student

    if not student_ids:
        return {"students": [], "total_turns": 0}
    user_ids = list(
        Student.objects.filter(pk__in=student_ids)
        .values_list("user_id", flat=True)
    )
    msgs = list(
        SignedInMessage.objects.filter(
            conversation__user_id__in=user_ids,
            is_tutor=True,
            role=SignedInMessage.Role.USER,
            created_at__gte=timezone.now() - datetime.timedelta(days=30),
        ).values_list("conversation__user_id", "body_masked")
    )
    by_user: dict[int, dict] = {}
    for uid, body in msgs:
        bucket = by_user.setdefault(uid, {"turns": 0, "topics": {}})
        bucket["turns"] += 1
        topic = _tutor_topic(body)
        bucket["topics"][topic] = bucket["topics"].get(topic, 0) + 1
    return {
        "students": [
            {"user_id": uid, "tutor_turns_30d": b["turns"], "topics": b["topics"]}
            for uid, b in by_user.items()
        ],
        "total_turns": len(msgs),
        "fetched_at": timezone.now().strftime("%Y-%m-%d %H:%M"),
    }


def _parent_tutor_usage(user, params: dict) -> dict:
    """Tutor usage summary for the caller's own children only."""
    parent = getattr(user, "parent_profile", None)
    if parent is None:
        raise ToolError("No parent profile linked to this account.", code="denied")
    child_ids = list(parent.children_links.values_list("student_id", flat=True))
    return _tutor_usage_for_students(child_ids)


def _teacher_tutor_usage(user, params: dict) -> dict:
    """Tutor usage summary for students in the caller's own classes."""
    if getattr(user, "role", "") != "teacher":
        raise ToolError("This tool is only available to teachers.", code="denied")
    from apps.homework.selectors import teacher_assignments
    from apps.students.models import Student

    class_ids = [
        a.school_class_id for a in teacher_assignments(teacher_user_id=user.pk)
    ]
    student_ids = list(
        Student.objects.filter(
            current_class_id__in=class_ids,
            status__in=[Student.Status.ACTIVE, Student.Status.REPEATING],
        ).values_list("pk", flat=True)
    )
    return _tutor_usage_for_students(student_ids)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, ToolSpec] = {}


def _reg(name, description, roles, params, fn, result_fields):
    REGISTRY[name] = ToolSpec(
        name=name, description=description,
        allowed_roles=frozenset(roles), params=params,
        fn=fn, result_fields=tuple(result_fields),
    )


_reg("parent_child_attendance",
     "Attendance summary for one child (days present/absent in the last N days).",
     ["parent"],
     {"child_id": {"type": "integer", "optional": True}, "days": {"type": "integer", "default": 30}},
     _parent_child_attendance, ["child_name", "total_marked", "days_present", "attendance_pct"])

_reg("parent_child_homework",
     "Homework due and overdue for one child (next 14 days).",
     ["parent"],
     {"child_id": {"type": "integer", "optional": True}},
     _parent_child_homework, ["child_name", "homework"])

_reg("parent_child_quiz_results",
     "Recently released homework marks for one child.",
     ["parent"],
     {"child_id": {"type": "integer", "optional": True}},
     _parent_child_quiz_results, ["child_name", "quiz_marks"])

_reg("parent_child_fee_balance",
     "Fee balance for one child.",
     ["parent"],
     {"child_id": {"type": "integer", "optional": True}},
     _parent_child_fee_balance, ["child_name", "balance"])

_reg("parent_child_upcoming_classes",
     "Upcoming live/online classes for one child.",
     ["parent"],
     {"child_id": {"type": "integer", "optional": True}},
     _parent_child_upcoming_classes, ["child_name", "upcoming_classes"])

_reg("parent_school_events",
     "Upcoming school events.",
     ["parent", "student"],
     {},
     _parent_school_events, ["events"])

_reg("school_announcements",
     "Recent school announcements for this user.",
     ["parent", "student", "teacher", "director", "admin", "finance"],
     {},
     _school_announcements, ["announcements"])

_reg("student_due_work",
     "Homework and assignments due for the calling student.",
     ["student"],
     {},
     _student_due_work, ["due_work"])

_reg("student_progress",
     "Latest result summary for the calling student.",
     ["student"],
     {},
     _student_progress, ["progress"])

_reg("student_upcoming_classes",
     "Upcoming live classes for the calling student.",
     ["student"],
     {},
     _student_upcoming_classes, ["upcoming_classes"])

_reg("student_points_badges",
     "Points and badges for the calling student.",
     ["student"],
     {},
     _student_points_badges, ["points", "level", "badge_count"])

_reg("teacher_unmarked_homework",
     "Homework with pending ungraded submissions for the calling teacher.",
     ["teacher"],
     {},
     _teacher_unmarked_homework, ["unmarked_homework"])

_reg("teacher_unanswered_questions",
     "Unanswered class-feed questions in the teacher's classes.",
     ["teacher"],
     {},
     _teacher_unanswered_questions, ["unanswered_questions"])

_reg("teacher_todays_classes",
     "Live classes scheduled for today for the calling teacher.",
     ["teacher"],
     {},
     _teacher_todays_classes, ["todays_classes"])

_reg("teacher_class_progress",
     "Class roster summary for all classrooms assigned to the calling teacher.",
     ["teacher"],
     {},
     _teacher_class_progress, ["classrooms"])

_reg("director_school_summary",
     "High-level school numbers: student count, staff count, current term.",
     ["director", "admin"],
     {},
     _director_school_summary, ["active_students", "teaching_staff", "current_term"])

_reg("director_attendance_trend",
     "School-wide attendance rate for the last 7 days.",
     ["director", "admin"],
     {},
     _director_attendance_trend, ["attendance_pct", "days"])

_reg("director_handoff_queue",
     "Assistant handoff queue: open, critical and overdue counts.",
     ["director", "admin"],
     {},
     _director_handoff_queue, ["open_handoffs", "critical", "overdue"])

_reg("admin_system_health",
     "Unresolved background failures and open safeguarding alerts.",
     ["admin"],
     {},
     _admin_system_health, ["unresolved_job_failures", "open_safeguarding_alerts"])

_reg("admin_assistant_usage",
     "Month-to-date assistant spend and today's call count.",
     ["admin"],
     {},
     _admin_assistant_usage, ["month_spend_ksh", "total_calls_today"])

_reg("admin_knowledge_gaps",
     "Recent questions the assistant could not answer.",
     ["admin"],
     {},
     _admin_knowledge_gaps, ["open_gaps"])

_reg("admin_pending_approvals",
     "Knowledge documents in Draft status awaiting admin approval.",
     ["admin"],
     {},
     _admin_pending_approvals, ["pending_documents"])

# ── Module 4 additions ────────────────────────────────────────────────────────

_reg("student_timetable",
     "The calling student's class subjects this term (their timetable).",
     ["student"],
     {},
     _student_timetable, ["class_name", "subjects", "term"])

_reg("parent_child_timetable",
     "Class subjects for one child (their timetable).",
     ["parent"],
     {"child_id": {"type": "integer", "optional": True}},
     _parent_child_timetable, ["child_name", "subjects", "term"])

_reg("student_next_lesson",
     "The next published lesson the calling student has not completed.",
     ["student"],
     {},
     _student_next_lesson, ["next_lesson"])

_reg("parent_child_next_lesson",
     "The next published lesson for one child.",
     ["parent"],
     {"child_id": {"type": "integer", "optional": True}},
     _parent_child_next_lesson, ["child_name", "next_lesson"])

_reg("student_class_materials",
     "Published lesson titles and short notes the student may see (for tutoring).",
     ["student"],
     {},
     _student_class_materials, ["materials"])

_reg("parent_child_fee_next_due",
     "Earliest unpaid invoice due date and amount for one child.",
     ["parent"],
     {"child_id": {"type": "integer", "optional": True}},
     _parent_child_fee_next_due, ["child_name", "next_due_date", "overdue_count"])

_reg("parent_child_week_summary",
     "This-week digest for one child: attendance, homework due, live classes.",
     ["parent"],
     {"child_id": {"type": "integer", "optional": True}},
     _parent_child_week_summary,
     ["child_name", "attendance_this_week", "homework_due_this_week"])

_reg("teacher_unfinished_items",
     "Published homework with students who have not submitted yet (teacher's own classes).",
     ["teacher"],
     {},
     _teacher_unfinished_items, ["unfinished"])

_reg("director_community_queue",
     "Community moderation backlog: posts awaiting approval and open reports.",
     ["director", "admin"],
     {},
     _director_community_queue, ["pending_posts", "open_reports"])

_reg("director_engagement_trend",
     "Learning engagement counts for the last 7 days.",
     ["director", "admin"],
     {},
     _director_engagement_trend,
     ["lessons_completed_7d", "quiz_attempts_7d", "homework_submissions_7d"])

_reg("parent_tutor_usage",
     "Tutor-mode usage summary (counts and topics) for the caller's own children.",
     ["parent"],
     {},
     _parent_tutor_usage, ["students", "total_turns"])

_reg("teacher_tutor_usage",
     "Tutor-mode usage summary (counts and topics) for students in the caller's classes.",
     ["teacher"],
     {},
     _teacher_tutor_usage, ["students", "total_turns"])


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def call_tool(
    name: str,
    params: dict,
    *,
    user,
    conversation,
    policy,
) -> dict:
    """
    Validate, run and audit-log one tool call.

    Returns a dict with keys:
      ok          bool
      result      dict (tool output) or {}
      error       str (safe to surface) if ok is False
      fields      list of result field names (never values)
      latency_ms  int
    """
    from apps.assistant.models import ToolCallLog

    t0 = time.monotonic()
    role = getattr(user, "role", "")

    # 1. Tool must be in the registry
    spec = REGISTRY.get(name)
    if spec is None:
        ToolCallLog.objects.create(
            conversation=conversation, tool_name=name, actor=user,
            outcome=ToolCallLog.Outcome.INVALID, reason="Unknown tool",
            latency_ms=0,
        )
        return {"ok": False, "result": {}, "error": "Unknown tool: %s" % name, "fields": [], "latency_ms": 0}

    # 2. Tool must be allowed by the policy for this role
    if name not in policy.tools_for(role):
        ToolCallLog.objects.create(
            conversation=conversation, tool_name=name, actor=user,
            outcome=ToolCallLog.Outcome.DENIED, reason="Role not permitted",
            latency_ms=0,
        )
        return {"ok": False, "result": {}, "error": "This tool is not available for your role.", "fields": [], "latency_ms": 0}

    # 3. Call the tool
    try:
        result = spec.fn(user, params or {})
        latency = int((time.monotonic() - t0) * 1000)
        fields = list(result.keys()) if isinstance(result, dict) else []
        ToolCallLog.objects.create(
            conversation=conversation, tool_name=name, actor=user,
            outcome=ToolCallLog.Outcome.SUCCESS,
            fields_returned=fields, latency_ms=latency,
        )
        return {"ok": True, "result": result, "error": "", "fields": fields, "latency_ms": latency}

    except ToolError as exc:
        latency = int((time.monotonic() - t0) * 1000)
        outcome = (
            ToolCallLog.Outcome.DENIED
            if exc.code == "denied"
            else ToolCallLog.Outcome.INVALID
        )
        ToolCallLog.objects.create(
            conversation=conversation, tool_name=name, actor=user,
            outcome=outcome, reason=str(exc)[:200], latency_ms=latency,
        )
        return {"ok": False, "result": {}, "error": str(exc), "fields": [], "latency_ms": latency}

    except Exception as exc:  # noqa: BLE001
        latency = int((time.monotonic() - t0) * 1000)
        log.warning("Tool %s failed for user %s: %s", name, user.pk, exc)
        ToolCallLog.objects.create(
            conversation=conversation, tool_name=name, actor=user,
            outcome=ToolCallLog.Outcome.ERROR, reason=str(exc)[:200], latency_ms=latency,
        )
        return {"ok": False, "result": {}, "error": "The tool could not complete.", "fields": [], "latency_ms": latency}
