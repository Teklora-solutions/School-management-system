"""
apps/homework/selectors.py
--------------------------
Read-only queries for homework. Always filter by student's class.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Q, QuerySet
from django.utils import timezone

from .models import Homework


def mark_new(homework_list, *, days: int = 7) -> list:
    """Flag items published within the last ``days`` days for a NEW badge."""
    cutoff = timezone.now() - timedelta(days=days)
    for homework in homework_list:
        homework.is_new = bool(
            homework.published_at and homework.published_at >= cutoff
        )
    return homework_list


def homework_for_student(
    *,
    school_class_id: int,
    stream_id: int | None = None,
    term_id: int | None = None,
    subject_id: int | None = None,
) -> QuerySet:
    """
    Published homework visible to a student in the given class.
    Matches assignments that have no stream restriction OR match the student's stream.
    """
    qs = (
        Homework.objects.filter(
            school_class_id=school_class_id,
            status=Homework.Status.PUBLISHED,
        )
        .select_related("subject", "term", "school_class", "stream", "set_by")
        .prefetch_related("attachments")
        .order_by("due_date", "subject__name")
    )
    # Stream: show homework with no stream OR matching the student's stream
    if stream_id:
        from django.db.models import Q

        qs = qs.filter(Q(stream__isnull=True) | Q(stream_id=stream_id))
    else:
        qs = qs.filter(stream__isnull=True)

    if term_id:
        qs = qs.filter(term_id=term_id)
    if subject_id:
        qs = qs.filter(subject_id=subject_id)
    return qs


def homework_due_soon(
    *,
    school_class_id: int,
    stream_id: int | None = None,
    term_id: int | None = None,
    days: int = 7,
) -> QuerySet:
    """Published homework due within ``days`` days (inclusive of today)."""
    today = date.today()
    from datetime import timedelta

    cutoff = today + timedelta(days=days)
    return homework_for_student(
        school_class_id=school_class_id,
        stream_id=stream_id,
        term_id=term_id,
    ).filter(due_date__gte=today, due_date__lte=cutoff)


def get_homework(pk: int) -> Homework:
    return Homework.objects.select_related(
        "subject", "term", "school_class", "stream", "set_by"
    ).get(pk=pk)


# ---------------------------------------------------------------------------
# Teacher side
# ---------------------------------------------------------------------------


def teacher_assignments(*, teacher_user_id: int) -> list:
    """
    The signed-in teacher's assignments for the current academic year.

    Homework is set per assigned class + subject, so this list doubles as the
    set of combinations the workspace may offer and as the scope guard: a
    homework is "mine" only if its class + subject + stream is in here.
    """
    from apps.academics.selectors import get_current_term, get_current_year
    from apps.teachers.models import TeacherAssignment

    term = get_current_term()
    year = term.academic_year if term else get_current_year()
    if year is None:
        return []
    return list(
        TeacherAssignment.objects.filter(
            teacher__user_id=teacher_user_id,
            academic_year=year,
        )
        .select_related("school_class", "subject", "stream", "academic_year")
        .order_by(
            "school_class__level_order", "school_class__name", "subject__name"
        )
    )


def _assignment_scope(assignments) -> Q:
    scope = Q()
    for assignment in assignments:
        scope |= Q(
            school_class_id=assignment.school_class_id,
            subject_id=assignment.subject_id,
            stream_id=assignment.stream_id,
        )
    return scope


def homework_for_teacher(
    *,
    teacher_user_id: int,
    term_id: int | None = None,
    class_id: int | None = None,
    status: str | None = None,
) -> QuerySet:
    """Every homework (drafts included) inside the teacher's assignments."""
    assignments = teacher_assignments(teacher_user_id=teacher_user_id)
    if not assignments:
        return Homework.objects.none()

    qs = (
        Homework.objects.filter(_assignment_scope(assignments))
        .select_related("school_class", "subject", "stream", "term", "set_by")
        .prefetch_related("attachments")
    )
    if term_id:
        qs = qs.filter(term_id=term_id)
    if class_id:
        qs = qs.filter(school_class_id=class_id)
    if status:
        qs = qs.filter(status=status)
    return qs.order_by("term__start_date", "school_class__level_order", "-due_date")


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------

#: Pupils who are still expected to hand work in.
ROSTER_STATUSES = ("active", "repeating")


def class_roster(*, school_class_id: int, stream_id: int | None = None) -> QuerySet:
    """Pupils in the homework's class (and stream, when one is set)."""
    from apps.students.models import Student

    qs = Student.objects.filter(
        current_class_id=school_class_id,
        status__in=ROSTER_STATUSES,
    ).select_related("user", "current_stream", "current_class")
    if stream_id:
        qs = qs.filter(current_stream_id=stream_id)
    return qs.order_by(
        "user__last_name", "user__first_name", "admission_number"
    )


def submission_for(*, homework_id: int, student_id: int):
    """The student's submission for one homework, or ``None``."""
    from .models import HomeworkSubmission

    return (
        HomeworkSubmission.objects.filter(
            homework_id=homework_id,
            student_id=student_id,
        )
        .prefetch_related("attachments")
        .first()
    )


def submissions_for_homework(*, homework_id: int) -> QuerySet:
    from .models import HomeworkSubmission

    return (
        HomeworkSubmission.objects.filter(homework_id=homework_id)
        .select_related("student__user", "student__current_class")
        .prefetch_related("attachments")
    )


def submission_counts(*, homework_ids) -> dict[int, int]:
    """Number of submissions per homework, in one query."""
    from django.db.models import Count

    from .models import HomeworkSubmission

    homework_ids = list(homework_ids)
    if not homework_ids:
        return {}
    rows = (
        HomeworkSubmission.objects.filter(homework_id__in=homework_ids)
        .values("homework_id")
        .annotate(total=Count("id"))
    )
    return {row["homework_id"]: row["total"] for row in rows}


# ---------------------------------------------------------------------------
# Marking and feedback
# ---------------------------------------------------------------------------

#: A mark is visible to the family when either release is set.
_RELEASED = Q(released_at__isnull=False) | Q(
    homework__marks_released_at__isnull=False
)


def mark_for(*, homework_id: int, student_id: int):
    from .models import HomeworkMark

    return HomeworkMark.objects.filter(
        homework_id=homework_id, student_id=student_id
    ).first()


def marks_for_homework(*, homework_id: int) -> dict[int, object]:
    """One pupil's mark each, keyed by student id."""
    from .models import HomeworkMark

    return {
        mark.student_id: mark
        for mark in HomeworkMark.objects.filter(homework_id=homework_id)
    }


def resubmission_window(*, homework, student) -> tuple[bool, int, int]:
    """
    ``(open, used, allowed)`` for the "Needs revision" loop.

    Open only when the pupil's work was sent back, the family has actually
    been told (the mark is released), and attempts remain. Counting starts
    from the revisions, which are written only for this loop.
    """
    from .models import HomeworkMark, HomeworkSubmissionRevision

    mark = HomeworkMark.objects.filter(
        homework=homework, student=student
    ).first()
    if mark is None or mark.status != HomeworkMark.Status.NEEDS_REVISION:
        return (False, 0, 0)
    if not _released_for(mark, homework):
        return (False, 0, 0)
    used = HomeworkSubmissionRevision.objects.filter(
        submission__homework=homework,
        submission__student=student,
    ).count()
    allowed = mark.resubmissions_allowed
    return (used < allowed, used, allowed)


def _released_for(mark, homework) -> bool:
    return mark.released_at is not None or homework.marks_released_at is not None


def released_mark(*, homework_id: int, student_id: int):
    """The pupil's mark, but only once it has been released."""
    from .models import HomeworkMark

    return (
        HomeworkMark.objects.filter(
            homework_id=homework_id,
            student_id=student_id,
        )
        .exclude(status="")
        .filter(_RELEASED)
        .select_related("homework")
        .first()
    )


def released_marks_for_student(*, student_id: int, term_id: int | None = None):
    """Every released mark for one pupil, newest homework first."""
    from .models import HomeworkMark

    qs = (
        HomeworkMark.objects.filter(student_id=student_id)
        .exclude(status="")
        .filter(_RELEASED)
        .select_related("homework__subject", "homework__term", "homework__school_class")
    )
    if term_id:
        qs = qs.filter(homework__term_id=term_id)
    return qs.order_by("-homework__due_date", "homework__subject__name")


def marks_awaiting_release(*, homework_ids) -> dict[int, int]:
    """How many marked pupils are still waiting, per homework, in one query."""
    from django.db.models import Count

    from .models import HomeworkMark

    homework_ids = list(homework_ids)
    if not homework_ids:
        return {}
    rows = (
        HomeworkMark.objects.filter(
            homework_id__in=homework_ids,
            released_at__isnull=True,
            homework__marks_released_at__isnull=True,
        )
        .exclude(status="")
        .values("homework_id")
        .annotate(total=Count("id"))
    )
    return {row["homework_id"]: row["total"] for row in rows}

