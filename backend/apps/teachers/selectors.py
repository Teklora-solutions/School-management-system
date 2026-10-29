"""
apps/teachers/selectors.py
--------------------------
Read-side queries for teacher management.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet

from apps.academics.models import SchoolClass

from .models import TeacherAssignment, TeacherProfile

User = get_user_model()


def teacher_list(*, search: str = "") -> QuerySet:
    qs = TeacherProfile.objects.select_related("user").order_by(
        "user__last_name", "user__first_name"
    )
    if search:
        qs = qs.filter(
            Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(staff_id__icontains=search)
            | Q(user__email__icontains=search)
        )
    return qs


def get_teacher(pk: int) -> TeacherProfile:
    return (
        TeacherProfile.objects.select_related("user")
        .prefetch_related(
            "assignments__school_class",
            "assignments__subject",
            "assignments__academic_year",
            "assignments__stream",
        )
        .get(pk=pk)
    )


def assignment_list(
    *,
    year_id: int | None = None,
    class_id: int | None = None,
    teacher_id: int | None = None,
) -> QuerySet:
    qs = TeacherAssignment.objects.select_related(
        "teacher__user",
        "school_class",
        "school_class__academic_year",
        "subject",
        "academic_year",
        "stream",
    )
    if year_id:
        qs = qs.filter(academic_year_id=year_id)
    if class_id:
        qs = qs.filter(school_class_id=class_id)
    if teacher_id:
        qs = qs.filter(teacher_id=teacher_id)
    return qs.order_by(
        "school_class__level_order", "school_class__name", "subject__name"
    )


def assignment_matrix(year_id: int) -> dict:
    """
    Build a matrix: {class_name: {subject_code: teacher_name}}.
    Used for the assignment matrix view.
    """
    assignments = assignment_list(year_id=year_id)
    classes = list(
        SchoolClass.objects.filter(academic_year_id=year_id).order_by(
            "level_order", "name"
        )
    )
    subjects_set: set[str] = set()
    cell: dict[int, dict[str, str]] = {}

    for a in assignments:
        cls_id = a.school_class_id
        if cls_id not in cell:
            cell[cls_id] = {}
        key = f"{a.subject.code}{'/' + a.stream.name if a.stream_id else ''}"
        subjects_set.add(key)
        cell[cls_id][key] = a.teacher.user.get_full_name()

    subjects = sorted(subjects_set)
    rows = []
    for cls in classes:
        rows.append(
            {
                "class": cls,
                "cells": {s: cell.get(cls.pk, {}).get(s, "") for s in subjects},
            }
        )
    return {"classes": classes, "subjects": subjects, "rows": rows}


def current_year_classes_for_teacher(*, teacher_user_id: int) -> dict:
    """
    The classes a teacher actually has this academic year.

    ``homeroom``  — classes where they are the class teacher.
    ``teaching``  — their subject assignments, one row per class with the
                    subjects (and optional stream) they teach there.

    Both are scoped to the current academic year so a teacher moving up a
    year does not keep last year's classes.
    """
    from apps.academics.selectors import get_current_term

    term = get_current_term()
    if not term:
        return {"term": None, "homeroom": [], "teaching": []}

    year_id = term.academic_year_id

    homeroom = list(
        SchoolClass.objects.filter(
            academic_year_id=year_id, class_teacher_id=teacher_user_id
        ).select_related("academic_year")
    )

    grouped: dict[int, dict] = {}
    for a in TeacherAssignment.objects.filter(
        teacher__user_id=teacher_user_id, academic_year_id=year_id
    ).select_related("school_class", "subject", "stream"):
        row = grouped.setdefault(
            a.school_class_id, {"class": a.school_class, "subjects": []}
        )
        row["subjects"].append(
            f"{a.subject.name}" + (f" ({a.stream.name})" if a.stream_id else "")
        )

    teaching = sorted(grouped.values(), key=lambda r: r["class"].level_order)
    for row in teaching:
        row["subjects"] = sorted(set(row["subjects"]))

    return {"term": term, "homeroom": homeroom, "teaching": teaching}


def _assignment_scope(assignments) -> Q:
    """Match the (class, subject, stream) triples a set of assignments covers."""
    scope = Q()
    for row in assignments:
        scope |= Q(
            school_class_id=row.school_class_id,
            subject_id=row.subject_id,
            stream_id=row.stream_id,
        )
    return scope


def _sheet_counts_by_assignment(assignments, term) -> dict[tuple, dict[str, int]]:
    """
    One grouped query: how many sheets per (class, subject, stream) by status.

    Keyed by the same triple the assignments use, because a sheet is identified
    by class + subject + stream, not by which teacher typed it in.
    """
    from django.db.models import Count

    from apps.results.models import ResultSheet

    if term is None or not assignments:
        return {}

    rows = (
        ResultSheet.objects.filter(term=term)
        .filter(_assignment_scope(assignments))
        .values("school_class_id", "subject_id", "stream_id", "status")
        .annotate(n=Count("id"))
    )
    counts: dict[tuple, dict[str, int]] = {}
    for row in rows:
        key = (row["school_class_id"], row["subject_id"], row["stream_id"])
        counts.setdefault(key, {})[row["status"]] = row["n"]
    return counts


def teacher_home_summary(*, teacher_user_id: int, term) -> dict:
    """
    The teacher dashboard's numbers, all scoped to the current term's year.

    "Students" counts the active students in the classes the teacher is assigned
    to, not just the ones in their subjects — the dashboard answers "who am I
    responsible for", while marks entry scopes per subject.
    """
    from apps.results.models import ResultSheet
    from apps.students.models import Student

    summary = {
        "class_count": 0,
        "student_count": 0,
        "draft_sheet_count": 0,
        "submitted_sheet_count": 0,
        "approved_sheet_count": 0,
    }
    if term is None:
        return summary

    assignments = list(
        TeacherAssignment.objects.filter(
            teacher__user_id=teacher_user_id,
            academic_year_id=term.academic_year_id,
        ).only("school_class_id", "subject_id", "stream_id")
    )
    if not assignments:
        return summary

    class_ids = {row.school_class_id for row in assignments}
    summary["class_count"] = len(class_ids)
    summary["student_count"] = Student.objects.filter(
        current_class_id__in=class_ids,
        status__in=[Student.Status.ACTIVE, Student.Status.REPEATING],
    ).count()

    for per_status in _sheet_counts_by_assignment(assignments, term).values():
        summary["draft_sheet_count"] += per_status.get(ResultSheet.Status.DRAFT, 0)
        summary["submitted_sheet_count"] += per_status.get(
            ResultSheet.Status.SUBMITTED, 0
        )
        summary["approved_sheet_count"] += per_status.get(
            ResultSheet.Status.CONFIRMED, 0
        ) + per_status.get(ResultSheet.Status.PUBLISHED, 0)
    return summary


def assignments_for_marks(*, teacher_user_id: int) -> dict:
    """
    The current-year subject assignments a teacher can enter exam marks for.

    One row per TeacherAssignment so a teacher who teaches the same subject
    in two streams gets two separate mark sheets. Each row carries its
    current-term sheet counts so the list can show what is drafted, waiting on
    the admin, or already approved — the teacher has no other way to tell which
    sheets the admin has locked.
    """
    from apps.academics.selectors import get_current_term
    from apps.results.models import ResultSheet

    term = get_current_term()
    if not term:
        return {"term": None, "rows": []}

    rows = list(
        TeacherAssignment.objects.filter(
            teacher__user_id=teacher_user_id,
            academic_year_id=term.academic_year_id,
        )
        .select_related("school_class", "subject", "stream")
        .order_by(
            "school_class__level_order", "school_class__name", "subject__name"
        )
    )

    counts = _sheet_counts_by_assignment(rows, term)
    for row in rows:
        per_status = counts.get(
            (row.school_class_id, row.subject_id, row.stream_id), {}
        )
        row.draft_count = per_status.get(ResultSheet.Status.DRAFT, 0)
        row.submitted_count = per_status.get(ResultSheet.Status.SUBMITTED, 0)
        row.approved_count = per_status.get(
            ResultSheet.Status.CONFIRMED, 0
        ) + per_status.get(ResultSheet.Status.PUBLISHED, 0)
    return {"term": term, "rows": rows}
