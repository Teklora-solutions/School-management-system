from __future__ import annotations

from django.utils import timezone

from ._common import ToolError, _int_param, _require, _resolve_child

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


