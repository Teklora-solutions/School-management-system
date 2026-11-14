"""
apps/results/selectors.py
"""

from __future__ import annotations

from django.db.models import Avg, Count, QuerySet

from .models import (
    GradeBand,
    GradeScale,
    GradingConfig,
    ResultSheet,
    ScoreEntry,
)
from .services import grade_bands_for_class, resolve_grade_for_school_class


def grading_for_year(year_id: int) -> dict:
    """Everything the admin grading screens need for one academic year."""
    from apps.academics.models import SchoolClass

    config = GradingConfig.objects.filter(academic_year_id=year_id).first()
    scale = (
        GradeScale.objects.filter(academic_year_id=year_id)
        .prefetch_related("bands")
        .first()
    )
    bands = list(
        GradeBand.objects.filter(academic_year_id=year_id).order_by("min_score")
    )
    classes = list(
        SchoolClass.objects.filter(academic_year_id=year_id)
        .select_related("grade_scale")
        .order_by("level_order", "name")
    )
    selected_ids = {
        school_class.pk for school_class in classes if school_class.grade_scale_id
    }
    return {
        "config": config,
        "scale": scale,
        "bands": bands,
        "classes": classes,
        "selected_class_ids": selected_ids,
    }


def result_sheet_list(
    *,
    status: str = "",
    class_id: int | None = None,
    term_id: int | None = None,
    year_id: int | None = None,
) -> QuerySet:
    qs = ResultSheet.objects.select_related(
        "school_class",
        "subject",
        "term",
        "term__academic_year",
        "teacher__user",
        "stream",
        "confirmed_by",
        "published_by",
    ).annotate(entry_count=Count("entries"))
    if status:
        qs = qs.filter(status=status)
    if class_id:
        qs = qs.filter(school_class_id=class_id)
    if term_id:
        qs = qs.filter(term_id=term_id)
    if year_id:
        qs = qs.filter(term__academic_year_id=year_id)
    return qs


def get_sheet(pk: int) -> ResultSheet:
    return (
        ResultSheet.objects.select_related(
            "school_class",
            "subject",
            "term",
            "term__academic_year",
            "teacher__user",
            "stream",
        )
        .prefetch_related("entries__student__user")
        .get(pk=pk)
    )


def sheets_awaiting_confirmation() -> QuerySet:
    return result_sheet_list(status=ResultSheet.Status.SUBMITTED)


def class_term_all_published(school_class_id: int, term_id: int) -> bool:
    """True if every sheet for class/term is published (and at least one exists)."""
    qs = ResultSheet.objects.filter(school_class_id=school_class_id, term_id=term_id)
    if not qs.exists():
        return False
    return not qs.exclude(status=ResultSheet.Status.PUBLISHED).exists()


def term_all_classes_published(term_id: int) -> bool:
    qs = ResultSheet.objects.filter(term_id=term_id)
    if not qs.exists():
        return False
    return not qs.exclude(status=ResultSheet.Status.PUBLISHED).exists()


# ---------------------------------------------------------------------------
# Class broadsheet (R1)
# ---------------------------------------------------------------------------


def _mean(values: list) -> float | None:
    if not values:
        return None
    return round(float(sum(values) / len(values)), 1)


def _band_score_label(value) -> str:
    """Band bounds without trailing zeros (50.00 prints as 50, 49.99 stays)."""
    return f"{float(value):g}"


def broadsheet_subjects(school_class, term) -> list:
    """
    Subjects shown as columns on a class broadsheet.

    Fallback order: the class's own ClassSubject list, then subjects that
    already have sheets for this class/term, then any subject the class has
    ever had a sheet for, then every active subject. The last fallback keeps
    a brand-new class usable before any setup has been done.
    """
    from apps.academics.models import ClassSubject, Subject

    subject_ids = list(
        ClassSubject.objects.filter(
            school_class=school_class, stream__isnull=True
        ).values_list("subject_id", flat=True)
    )
    if not subject_ids:
        subject_ids = list(
            ResultSheet.objects.filter(
                school_class=school_class, term=term, subject__is_active=True
            ).values_list("subject_id", flat=True)
        )
    if not subject_ids:
        subject_ids = list(
            ResultSheet.objects.filter(
                school_class=school_class, subject__is_active=True
            ).values_list("subject_id", flat=True)
        )

    qs = (
        Subject.objects.filter(pk__in=set(subject_ids))
        if subject_ids
        else Subject.objects.filter(is_active=True)
    )
    return list(qs.order_by("name"))


def class_roster(school_class) -> list:
    """Students currently on the class roll, ordered by admission number."""
    from apps.students.models import Student

    return list(
        Student.objects.filter(
            current_class=school_class,
            status__in=[Student.Status.ACTIVE, Student.Status.REPEATING],
        )
        .select_related("user")
        .order_by("admission_number")
    )


def exam_broadsheet(*, school_class, term, exam) -> dict:
    """
    Compute the broadsheet for one class + term + exam.

    Each student gets one cell per subject (``raw_score`` on an exam sheet,
    falling back to ``total`` for legacy term sheets), a total, a mean, an
    overall grade, and a competition rank (ties share a position). Students
    with no marks are shown but left unranked.
    """
    subjects = broadsheet_subjects(school_class, term)
    sheets = list(
        ResultSheet.objects.filter(
            school_class=school_class, term=term, exam=exam
        )
        .select_related("subject")
        .prefetch_related("entries__student__user")
    )

    entries_by_student: dict[int, dict[int, ScoreEntry]] = {}
    for sheet in sheets:
        for entry in sheet.entries.all():
            entries_by_student.setdefault(entry.student_id, {})[
                sheet.subject_id
            ] = entry

    students = class_roster(school_class)

    bands = grade_bands_for_class(school_class, term.academic_year_id)

    def grade_for(score) -> str:
        if score is None:
            return ""
        for band in bands:
            if band.min_score <= score <= band.max_score:
                return band.grade
        return ""

    rows: list[dict] = []
    subject_scores: dict[int, list] = {subject.pk: [] for subject in subjects}
    for student in students:
        student_entries = entries_by_student.get(student.pk, {})
        cells = []
        total = 0
        scored_subjects = 0
        for subject in subjects:
            entry = student_entries.get(subject.pk)
            score = None
            if entry is not None:
                score = (
                    entry.raw_score if entry.raw_score is not None else entry.total
                )
            if score is not None:
                total += score
                scored_subjects += 1
                subject_scores[subject.pk].append(score)
            grade = (entry.grade if entry and entry.grade else "") or grade_for(score)
            cells.append(
                {
                    "subject": subject,
                    "entry": entry,
                    "score": score,
                    "grade": grade,
                }
            )
        mean = (total / scored_subjects) if scored_subjects else None
        rows.append(
            {
                "student": student,
                "cells": cells,
                "total": total if scored_subjects else None,
                "scored_subjects": scored_subjects,
                "mean": mean,
                "grade": grade_for(mean) if mean is not None else "",
                "position": None,
            }
        )

    ranked = sorted(
        (row for row in rows if row["total"] is not None),
        key=lambda row: row["total"],
        reverse=True,
    )
    previous_total = None
    position = 0
    for index, row in enumerate(ranked, start=1):
        if previous_total is None or row["total"] != previous_total:
            position = index
            previous_total = row["total"]
        row["position"] = position

    subject_means = {
        subject.pk: {
            "mean": _mean(subject_scores[subject.pk]),
            "count": len(subject_scores[subject.pk]),
        }
        for subject in subjects
    }

    columns = [
        {
            "subject": subject,
            "mean": subject_means[subject.pk]["mean"],
            "mean_pct": subject_means[subject.pk]["mean"],
            "count": subject_means[subject.pk]["count"],
            "rank": None,
        }
        for subject in subjects
    ]
    ranked_columns = sorted(
        (col for col in columns if col["mean"] is not None),
        key=lambda col: col["mean"],
        reverse=True,
    )
    previous_mean = None
    column_rank = 0
    for index, col in enumerate(ranked_columns, start=1):
        if previous_mean is None or col["mean"] != previous_mean:
            column_rank = index
            previous_mean = col["mean"]
        col["rank"] = column_rank

    band_counts = [
        (
            band,
            sum(
                1
                for row in ranked
                if band.min_score <= row["mean"] <= band.max_score
            ),
        )
        for band in bands
    ]
    distribution_total = sum(count for _, count in band_counts)
    if distribution_total:
        percentages = [
            round(count * 100.0 / distribution_total, 1) for _, count in band_counts
        ]
        drift = round(100.0 - sum(percentages), 1)
        if drift:
            largest = max(
                range(len(percentages)), key=lambda i: band_counts[i][1]
            )
            percentages[largest] = round(percentages[largest] + drift, 1)
    else:
        percentages = [0.0 for _ in band_counts]
    distribution = [
        {
            "grade": band.grade,
            "description": band.remark,
            "range": (
                f"{_band_score_label(band.min_score)} – "
                f"{_band_score_label(band.max_score)}"
            ),
            "count": count,
            "pct": percentages[index],
        }
        for index, (band, count) in enumerate(band_counts)
    ]

    statuses = {sheet.status for sheet in sheets}
    if not sheets:
        status = {"label": "No sheets", "tone": "muted"}
    elif statuses == {ResultSheet.Status.PUBLISHED}:
        status = {"label": "Published", "tone": "success"}
    elif statuses <= {ResultSheet.Status.CONFIRMED, ResultSheet.Status.PUBLISHED}:
        status = {"label": "Confirmed", "tone": "accent"}
    elif ResultSheet.Status.SUBMITTED in statuses:
        status = {"label": "Submitted", "tone": "warning"}
    else:
        status = {"label": "Draft", "tone": "muted"}

    expected_cells = len(students) * len(subjects)
    scored_cells = sum(
        1 for row in rows for cell in row["cells"] if cell["score"] is not None
    )
    progress = {
        "scored": scored_cells,
        "expected": expected_cells,
        "pct": round(scored_cells * 100 / expected_cells) if expected_cells else 0,
    }

    return {
        "subjects": subjects,
        "columns": columns,
        "students": rows,
        "subject_means": subject_means,
        "distribution": distribution,
        "distribution_total": distribution_total,
        "class_mean_total": _mean([row["total"] for row in ranked]),
        "class_mean_pct": _mean([row["mean"] for row in ranked]),
        "exam": exam,
        "term": term,
        "school_class": school_class,
        "sheets_count": len(sheets),
        "status": status,
        "progress": progress,
        "roster_size": len(students),
    }


# ---------------------------------------------------------------------------
# Student-facing selectors (published results only)
# ---------------------------------------------------------------------------


def student_published_results(
    *, student_id: int, term_id: int | None = None
) -> QuerySet:
    """
    Return ScoreEntry rows for one student on PUBLISHED sheets only.
    Never exposes draft/submitted/confirmed entries.
    Optionally filtered to a single term.
    """
    qs = (
        ScoreEntry.objects.filter(
            student_id=student_id,
            result_sheet__status=ResultSheet.Status.PUBLISHED,
        )
        .select_related(
            "result_sheet__subject",
            "result_sheet__term",
            "result_sheet__term__academic_year",
            "result_sheet__school_class",
            "result_sheet__stream",
        )
        .order_by(
            "result_sheet__term__academic_year__start_date",
            "result_sheet__term__name",
            "result_sheet__subject__name",
        )
    )
    if term_id:
        qs = qs.filter(result_sheet__term_id=term_id)
    return qs


def student_terms_with_published_results(*, student_id: int) -> QuerySet:
    """Return distinct Terms that have at least one published result for the student."""
    from apps.academics.models import Term

    term_ids = (
        ScoreEntry.objects.filter(
            student_id=student_id,
            result_sheet__status=ResultSheet.Status.PUBLISHED,
        )
        .values_list("result_sheet__term_id", flat=True)
        .distinct()
    )
    return (
        Term.objects.filter(pk__in=term_ids)
        .select_related("academic_year")
        .order_by("-academic_year__start_date", "name")
    )


def student_latest_result_summary(*, student_id: int) -> dict | None:
    """
    Return a summary dict for the most recent term that has published results.
    Used on the dashboard home widget.
    Returns None if no published results exist for this student.
    """
    terms = student_terms_with_published_results(student_id=student_id)
    if not terms.exists():
        return None
    latest_term = terms.first()
    entries = student_published_results(student_id=student_id, term_id=latest_term.pk)
    subject_count = entries.count()
    if not subject_count:
        return None

    # Average total score across published entries
    avg = entries.aggregate(avg=Avg("total"))["avg"] or 0

    # Class position: count students in the same class/term who have
    # an average higher than this student.
    from apps.students.models import Student

    try:
        student = Student.objects.select_related("current_class").get(pk=student_id)
    except Student.DoesNotExist:
        return None

    if student.current_class:
        # Compute average total per student in this class/term
        from django.db.models import Avg as _Avg

        peers = (
            ScoreEntry.objects.filter(
                result_sheet__term=latest_term,
                result_sheet__status=ResultSheet.Status.PUBLISHED,
                student__current_class=student.current_class,
            )
            .values("student_id")
            .annotate(avg_total=_Avg("total"))
        )
        peers_above = sum(1 for p in peers if (p["avg_total"] or 0) > avg)
        class_position = peers_above + 1
        total_in_class = peers.count()
    else:
        class_position = None
        total_in_class = None

    return {
        "term": latest_term,
        "subject_count": subject_count,
        "average": round(float(avg), 1),
        "class_position": class_position,
        "total_in_class": total_in_class,
        "entries": entries,
    }


def student_class_positions(*, student_id: int, term_id: int) -> dict[int, dict]:
    """
    Return {entry_pk: {position, total_in_class}} for each published entry
    in the given term, so the results page can show per-subject position.
    """
    entries = student_published_results(student_id=student_id, term_id=term_id)
    positions: dict[int, dict] = {}
    for entry in entries:
        sheet = entry.result_sheet
        # Rank: how many students on the same sheet scored higher
        higher = ScoreEntry.objects.filter(
            result_sheet=sheet,
            total__gt=entry.total,
        ).count()
        total_in_class = ScoreEntry.objects.filter(result_sheet=sheet).count()
        positions[entry.pk] = {
            "position": higher + 1,
            "total_in_class": total_in_class,
        }
    return positions


# ---------------------------------------------------------------------------
# Report card identity + overall standing (RC1)
# ---------------------------------------------------------------------------


def result_identity(*, request) -> dict:
    """
    The school identity for a PDF header: name, motto, contacts, the
    show/hide-positions flag, and an absolute logo URL when a logo is uploaded.

    Identity comes from the ``SchoolSettings`` row exactly like every other
    page (blank fields fall back to the ``SCHOOL_*`` settings), so a report
    card always matches the school's own website. The logo URL is made
    absolute against the request because WeasyPrint resolves images against
    the ``base_url`` it is given.
    """
    from apps.core.models import SchoolSettings
    from apps.core.selectors import school_settings_values

    values = school_settings_values(request)
    row = SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).first()
    logo = getattr(row, "logo", None) if row else None
    return {
        "name": values["name"] or "My School",
        "motto": values["motto"],
        "phone": values["phone"],
        "email": values["email"],
        "show_positions": values["show_positions"],
        "logo_url": request.build_absolute_uri(logo.url) if logo else "",
    }


def student_term_position(*, student_id: int, term_id: int) -> dict | None:
    """
    The student's overall standing in their current class for one term:
    {"position": int, "total_in_class": int} by average of published totals.

    Mirrors ``student_latest_result_summary`` but scoped to a chosen term, so
    the report card can print "Position 3 of 27" for the term it covers.
    Ties share a position (same strictly-greater-count rule as elsewhere).
    None when the student has no class or no published results in the term.
    """
    from apps.students.models import Student

    student = (
        Student.objects.select_related("current_class")
        .filter(pk=student_id)
        .first()
    )
    if student is None or student.current_class_id is None:
        return None

    own_avg = ScoreEntry.objects.filter(
        student_id=student_id,
        result_sheet__term_id=term_id,
        result_sheet__status=ResultSheet.Status.PUBLISHED,
    ).aggregate(avg=Avg("total"))["avg"]
    if own_avg is None:
        return None

    peers = (
        ScoreEntry.objects.filter(
            result_sheet__term_id=term_id,
            result_sheet__status=ResultSheet.Status.PUBLISHED,
            student__current_class_id=student.current_class_id,
        )
        .values("student_id")
        .annotate(avg_total=Avg("total"))
    )
    above = sum(1 for p in peers if (p["avg_total"] or 0) > own_avg)
    return {"position": above + 1, "total_in_class": peers.count()}


def class_teacher_remark(*, student_id: int, term_id: int):
    """The class teacher's remark row for the card, or None."""
    from .models import ClassTeacherRemark

    return (
        ClassTeacherRemark.objects.filter(student_id=student_id, term_id=term_id)
        .select_related("class_teacher")
        .first()
    )


def report_card_context(
    *,
    student,
    term,
    identity: dict,
    entries: list | None = None,
    positions: dict[int, dict] | None = None,
) -> dict | None:
    """
    Every field one report card page needs, assembled in one place.

    ``entries`` and ``positions`` may be handed in by a caller that already
    fetched them for a whole class or school; left out, the published rows are
    looked up here. ``None`` comes back when the pupil has no published results
    for the term — that is what lets a bulk download skip the pupil and a
    single request answer 404.

    The arithmetic is done on the rows rather than with SQL aggregates so a
    pre-fetched list produces exactly the same card as a fresh query.
    """
    if entries is None:
        entries = list(
            student_published_results(student_id=student.pk, term_id=term.pk)
        )
    if not entries:
        return None
    if positions is None:
        positions = student_class_positions(student_id=student.pk, term_id=term.pk)

    scored = [entry.total for entry in entries if entry.total is not None]
    grand_total = sum(scored) if scored else 0
    average = round(float(sum(scored) / len(scored)), 1) if scored else 0.0

    overall_grade = ""
    class_teacher_name = ""
    if student.current_class_id:
        overall_grade, _, _ = resolve_grade_for_school_class(
            student.current_class, average, term.academic_year_id
        )
        class_teacher = student.current_class.class_teacher
        if class_teacher:
            class_teacher_name = (
                class_teacher.get_full_name()
                or class_teacher.email
                or class_teacher.username
            )

    return {
        "student": student,
        "term": term,
        "entries": entries,
        "positions": positions,
        "average": average,
        "grand_total": grand_total,
        "overall_grade": overall_grade,
        "class_teacher_name": class_teacher_name,
        "term_position": (
            student_term_position(student_id=student.pk, term_id=term.pk)
            if identity["show_positions"]
            else None
        ),
        "ct_remark": class_teacher_remark(student_id=student.pk, term_id=term.pk),
    }
