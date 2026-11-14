"""
apps/gradebook/selectors.py
---------------------------
Read-only queries feeding the teacher's gradebook.

Nothing is copied out of homework, quizzes or result sheets: each selector
reads the live records for one classroom and term, so a corrected mark shows
up in the grid the next time the page is opened.
"""

from __future__ import annotations

from django.db.models import Avg, Count, Max, Q, QuerySet, Sum

from apps.academics.models import Term

from .models import (
    GradebookRelease,
    GradebookResult,
    GradebookSetup,
    ManualItem,
    ManualScore,
)


def teacher_classrooms(*, teacher_user_id: int) -> list:
    """Every class + subject the signed-in teacher is allocated, newest year first."""
    from apps.teachers.models import TeacherAssignment

    return list(
        TeacherAssignment.objects.filter(teacher__user_id=teacher_user_id)
        .select_related("school_class", "subject", "stream", "academic_year")
        .order_by(
            "-academic_year__start_date",
            "school_class__level_order",
            "school_class__name",
            "subject__name",
        )
    )


def classroom_assignment(*, teacher_user_id: int, school_class_id: int, subject_id: int):
    """The teacher's allocation for one classroom, or ``None``."""
    from apps.teachers.models import TeacherAssignment

    return (
        TeacherAssignment.objects.filter(
            teacher__user_id=teacher_user_id,
            school_class_id=school_class_id,
            subject_id=subject_id,
        )
        .select_related("school_class", "subject", "stream", "academic_year")
        .order_by("-academic_year__start_date")
        .first()
    )


def terms_for_assignment(assignment) -> QuerySet:
    return Term.objects.filter(academic_year=assignment.academic_year).order_by(
        "start_date", "name"
    )


def roster(*, school_class_id: int, stream_id: int | None = None) -> QuerySet:
    """Pupils still expected to be in the class (and stream, when one is set)."""
    from apps.students.models import Student

    qs = Student.objects.filter(
        current_class_id=school_class_id,
        status__in=("active", "repeating"),
    ).select_related("user", "current_stream", "current_class")
    if stream_id:
        qs = qs.filter(current_stream_id=stream_id)
    return qs.order_by("user__last_name", "user__first_name", "admission_number")


def counted_homework(setup) -> QuerySet:
    """
    Published homework the teacher has ticked for the gradebook.

    Only work with a total mark can produce a percentage, so an unticked or
    unmarked homework simply has no column.
    """
    from apps.homework.models import Homework

    qs = Homework.objects.filter(
        school_class_id=setup.school_class_id,
        subject_id=setup.subject_id,
        term_id=setup.term_id,
        status=Homework.Status.PUBLISHED,
        counts_for_gradebook=True,
        max_score__isnull=False,
    )
    if setup.stream_id:
        qs = qs.filter(stream_id__in=(None, setup.stream_id))
    return qs.order_by("due_date", "pk")


def homework_marks(*, homework_ids) -> dict[tuple[int, int], object]:
    """``(homework_id, student_id) → mark`` for the counted homework."""
    from apps.homework.models import HomeworkMark

    homework_ids = list(homework_ids)
    if not homework_ids:
        return {}
    return {
        (mark.homework_id, mark.student_id): mark
        for mark in HomeworkMark.objects.filter(homework_id__in=homework_ids)
    }


def unreleased_homework(setup) -> QuerySet:
    """
    Counted homework with at least one mark the family has not been shown.

    Releasing the gradebook means the homework percentage becomes visible; the
    individual marks behind it stay hidden until they are released here.
    """
    return counted_homework(setup).filter(
        marks_released_at__isnull=True,
        marks__released_at__isnull=True,
    ).distinct()


def counted_quizzes(setup) -> QuerySet:
    """Published quizzes ticked for the gradebook, through their lesson's module."""
    from apps.learning.models import Quiz

    return Quiz.objects.filter(
        lesson__module__school_class_id=setup.school_class_id,
        lesson__module__subject_id=setup.subject_id,
        lesson__module__term_id=setup.term_id,
        is_published=True,
        counts_for_gradebook=True,
    ).order_by("lesson__module__position", "lesson__position", "pk")


def best_quiz_attempts(*, quiz_ids) -> dict[tuple[int, int], object]:
    """Best submitted score per pupil per quiz: ``(quiz_id, student_id) → pct``."""
    from apps.learning.models import QuizAttempt

    quiz_ids = list(quiz_ids)
    if not quiz_ids:
        return {}
    rows = (
        QuizAttempt.objects.filter(
            quiz_id__in=quiz_ids,
            submitted_at__isnull=False,
            score_pct__isnull=False,
        )
        .values("quiz_id", "student_id")
        .annotate(best=Max("score_pct"))
    )
    return {(row["quiz_id"], row["student_id"]): row["best"] for row in rows}


def counted_manual_items(setup) -> QuerySet:
    return ManualItem.objects.filter(setup=setup, counts_for_gradebook=True).order_by(
        "bucket", "worked_on", "pk"
    )


def manual_scores(*, item_ids) -> dict[tuple[int, int], object]:
    """``(item_id, student_id) → score`` (blank scores are simply absent)."""
    item_ids = list(item_ids)
    if not item_ids:
        return {}
    return {
        (score.item_id, score.student_id): score.score
        for score in ManualScore.objects.filter(item_id__in=item_ids)
        if score.score is not None
    }


def excuses(setup) -> dict[tuple[int, str, int], object]:
    """``(student_id, source_kind, source_id) → excuse`` for one setup."""
    return {
        (excuse.student_id, excuse.source_kind, excuse.source_id): excuse
        for excuse in setup.excuses.all()
    }


def exam_scores(*, setup) -> dict[int, object]:
    """``student_id → raw_score`` on the nominated end-of-term exam sheet."""
    if setup.exam_sheet_id is None:
        return {}
    from apps.results.models import ScoreEntry

    return {
        row["student_id"]: row["raw_score"]
        for row in ScoreEntry.objects.filter(
            result_sheet=setup.exam_sheet, raw_score__isnull=False
        ).values("student_id", "raw_score")
    }


# ---------------------------------------------------------------------------
# What a family may read, and what the director may aggregate
# ---------------------------------------------------------------------------


def _stream_compatible_setups(student) -> QuerySet:
    """
    Setups the pupil's class can be graded under.

    A setup with no stream covers the whole class; a setup for one stream only
    covers the pupils in it.
    """
    qs = GradebookSetup.objects.filter(
        school_class_id=student.current_class_id
    )
    stream_id = getattr(student, "current_stream_id", None)
    if stream_id:
        return qs.filter(Q(stream__isnull=True) | Q(stream_id=stream_id))
    return qs.filter(stream__isnull=True)


def family_releases(student) -> list[dict]:
    """
    One entry per subject and term the pupil has a released result in.

    Only the newest release of each setup is read — a version a family was
    never shown must not surface. When a whole-class setup and a stream setup
    both exist for the same subject and term, the stream one wins: it is the
    grading the pupil was actually in.
    """
    setups = (
        _stream_compatible_setups(student)
        .select_related("subject", "term__academic_year", "stream")
        .order_by(
            "-term__academic_year__start_date",
            "-term__start_date",
            "subject__name",
        )
    )
    chosen: dict[tuple[int, int], dict] = {}
    for setup in setups:
        release = (
            setup.releases.select_related("released_by").order_by("-version").first()
        )
        if release is None:
            continue
        result = release.results.filter(student=student).first()
        if result is None:
            continue
        key = (setup.term_id, setup.subject_id)
        current = chosen.get(key)
        if current is None or (setup.stream_id and not current["setup"].stream_id):
            chosen[key] = {
                "setup": setup,
                "release": release,
                "result": result,
            }
    return list(chosen.values())


def family_terms(rows: list[dict]) -> list:
    """The terms present in ``family_releases``, newest academic year first."""
    seen: set[int] = set()
    terms = []
    for row in rows:
        term = row["setup"].term
        if term.pk not in seen:
            seen.add(term.pk)
            terms.append(term)
    return terms


def term_release_overview(term) -> dict:
    """
    The director's view of one term: one row per released classroom.

    Every figure comes from the frozen release snapshots, and no pupil name is
    read at all — the point of the page is the class, not the child.
    """
    setups = list(
        GradebookSetup.objects.filter(term=term)
        .select_related("school_class", "subject", "stream")
        .order_by(
            "school_class__level_order", "school_class__name", "subject__name"
        )
    )
    latest: dict[int, GradebookRelease] = {}
    for release in GradebookRelease.objects.filter(setup__in=setups).order_by(
        "-version"
    ):
        latest.setdefault(release.setup_id, release)

    release_ids = [release.pk for release in latest.values()]
    stats = {
        row["release_id"]: row
        for row in GradebookResult.objects.filter(release_id__in=release_ids)
        .values("release_id")
        .annotate(
            pupils=Count("pk"),
            average=Avg("overall_pct"),
            missing=Sum("missing_count"),
            excused=Sum("excused_count"),
            scored=Sum("scored_count"),
        )
    }

    rows = []
    for setup in setups:
        release = latest.get(setup.pk)
        if release is None:
            continue
        stat = stats.get(release.pk, {})
        missing = stat.get("missing") or 0
        scored = stat.get("scored") or 0
        denominator = missing + scored
        rows.append(
            {
                "setup": setup,
                "release": release,
                "pupils": stat.get("pupils") or 0,
                "average": stat.get("average"),
                "missing": missing,
                "excused": stat.get("excused") or 0,
                "scored": scored,
                "missing_rate": (
                    round(missing * 100 / denominator, 1) if denominator else None
                ),
            }
        )

    totals = GradebookResult.objects.filter(release_id__in=release_ids).aggregate(
        pupils=Count("pk"),
        average=Avg("overall_pct"),
        missing=Sum("missing_count"),
        excused=Sum("excused_count"),
        scored=Sum("scored_count"),
    )
    released_pupils = totals["pupils"] or 0
    return {
        "rows": rows,
        "summary": {
            "classrooms": len(rows),
            "classrooms_total": len(setups),
            "not_released": len(setups) - len(rows),
            "pupils": released_pupils,
            "average": totals["average"],
            "missing": totals["missing"] or 0,
            "excused": totals["excused"] or 0,
            "scored": totals["scored"] or 0,
        },
    }
