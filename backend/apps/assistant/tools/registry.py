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


from ._common import ToolError, ToolSpec, _int_param, _require, _resolve_child
from .family_tools import (
    _next_lesson_for,
    _parent_child_attendance,
    _parent_child_fee_balance,
    _parent_child_fee_next_due,
    _parent_child_homework,
    _parent_child_next_lesson,
    _parent_child_quiz_results,
    _parent_child_timetable,
    _parent_child_upcoming_classes,
    _parent_child_week_summary,
    _parent_school_events,
    _school_announcements,
    _student_class_materials,
    _student_due_work,
    _student_next_lesson,
    _student_points_badges,
    _student_progress,
    _student_timetable,
    _student_upcoming_classes,
    _timetable_for_student,
)
from .staff_tools import (
    _admin_assistant_usage,
    _admin_knowledge_gaps,
    _admin_pending_approvals,
    _admin_system_health,
    _director_attendance_trend,
    _director_community_queue,
    _director_engagement_trend,
    _director_handoff_queue,
    _director_school_summary,
    _parent_tutor_usage,
    _teacher_class_progress,
    _teacher_todays_classes,
    _teacher_tutor_usage,
    _teacher_unanswered_questions,
    _teacher_unfinished_items,
    _teacher_unmarked_homework,
)

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
