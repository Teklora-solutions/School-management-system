"""
apps/teachers/selectors.py
--------------------------
Read-side queries for teacher management.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet

from apps.academics.models import AcademicYear, SchoolClass

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


def assignment_picker(*, year_id: int | None) -> dict:
    """
    Option lists the assign form filters through in the browser.

    ``taken`` holds the (class, subject) pairs the service refuses within one
    academic year, so the dropdown hides exactly what a save would reject.
    Stream is not part of the key: one teacher owns the subject for the whole
    class no matter which stream (if any) the assignment is scoped to.
    """
    from apps.academics.models import Stream, Subject

    if not year_id:
        return {"classes": [], "streams": [], "subjects": [], "taken": []}

    classes = SchoolClass.objects.filter(academic_year_id=year_id).order_by(
        "level_order", "name"
    )
    streams = Stream.objects.filter(
        school_class__academic_year_id=year_id
    ).select_related("school_class")
    return {
        "classes": [{"id": c.pk, "label": str(c)} for c in classes],
        "streams": [
            {"id": s.pk, "label": str(s), "class": s.school_class_id}
            for s in streams
        ],
        "subjects": [
            {"id": s.pk, "label": str(s)}
            for s in Subject.objects.filter(is_active=True).order_by("name")
        ],
        "taken": [
            list(row)
            for row in TeacherAssignment.objects.filter(
                academic_year_id=year_id
            ).values_list("school_class_id", "subject_id")
        ],
    }


def _display_name(user) -> str:
    return user.get_full_name() or user.email or user.username


def subject_distribution(*, year_id: int) -> dict:
    """
    Every class in a year with the subjects it offers and who teaches each one.

    A subject row comes from either side of the data: the class's mapped
    subjects (``ClassSubject``) and any actual teacher assignment. A subject
    mapped but never assigned shows as unassigned; a subject taught without a
    mapping still appears, because a card that hides it would be lying about
    what happens in that classroom.

    The database enforces one teacher per class+subject+year, so a row has at
    most one teacher — ``state`` is ``ok`` or ``unassigned``.
    """
    from apps.academics.models import ClassSubject

    classes = list(
        SchoolClass.objects.filter(academic_year_id=year_id)
        .select_related("class_teacher")
        .order_by("level_order", "name")
    )

    mapped: dict[tuple[int, int], dict] = {}
    for cs in ClassSubject.objects.filter(
        school_class__academic_year_id=year_id
    ).select_related("subject", "stream"):
        row = mapped.setdefault(
            (cs.school_class_id, cs.subject_id),
            {"subject": cs.subject, "streams": set(), "mapped": True},
        )
        if cs.stream_id:
            row["streams"].add(cs.stream.name)

    assigned: dict[tuple[int, int], dict] = {}
    for a in TeacherAssignment.objects.filter(
        academic_year_id=year_id
    ).select_related("teacher__user", "subject", "stream"):
        key = (a.school_class_id, a.subject_id)
        entry = assigned.setdefault(
            key,
            {
                "teacher": a.teacher,
                "name": _display_name(a.teacher.user),
                "streams": set(),
            },
        )
        if a.stream_id:
            entry["streams"].add(a.stream.name)
        if key not in mapped:
            mapped[key] = {"subject": a.subject, "streams": set(), "mapped": False}

    cards = []
    for cls in classes:
        rows = []
        for (class_id, subject_id), meta in mapped.items():
            if class_id != cls.pk:
                continue
            teacher = assigned.get((class_id, subject_id))
            if teacher:
                teacher["streams"] = sorted(teacher["streams"])
            rows.append(
                {
                    "subject": meta["subject"],
                    "streams": sorted(meta["streams"]),
                    "mapped": meta["mapped"],
                    "teacher": teacher,
                    "teacher_name": teacher["name"] if teacher else "",
                    "state": "unassigned" if teacher is None else "ok",
                }
            )
        rows.sort(key=lambda r: r["subject"].name)
        cards.append(
            {
                "class": cls,
                "class_teacher": cls.class_teacher,
                "class_teacher_name": (
                    _display_name(cls.class_teacher) if cls.class_teacher else ""
                ),
                "rows": rows,
                "unassigned_count": sum(1 for r in rows if r["state"] == "unassigned"),
            }
        )

    return {
        "cards": cards,
        "class_count": len(cards),
        "subject_count": sum(len(c["rows"]) for c in cards),
        "unassigned_count": sum(c["unassigned_count"] for c in cards),
    }


def teacher_workload(*, year_id: int) -> dict:
    """
    One row per teacher: homeroom class, classes and subjects taught, pupils.

    Includes teachers with nothing assigned in the year (zeros, sorted last) so
    the page answers "who is idle" as well as "who is overloaded". Students are
    counted once per teacher even when they teach two subjects in the same class.
    """
    from django.db.models import Count

    from apps.students.models import Student

    teachers = list(
        TeacherProfile.objects.select_related("user").order_by(
            "user__last_name", "user__first_name"
        )
    )

    per_teacher: dict[int, dict] = {}
    for a in TeacherAssignment.objects.filter(
        academic_year_id=year_id
    ).select_related("school_class", "subject"):
        row = per_teacher.setdefault(
            a.teacher_id, {"classes": {}, "subjects": set(), "assignment_count": 0}
        )
        row["classes"][a.school_class_id] = a.school_class
        row["subjects"].add(a.subject.name)
        row["assignment_count"] += 1

    homeroom: dict[int, list] = {}
    for cls in SchoolClass.objects.filter(
        academic_year_id=year_id, class_teacher__isnull=False
    ).order_by("level_order", "name"):
        homeroom.setdefault(cls.class_teacher_id, []).append(cls)

    class_ids = {cid for row in per_teacher.values() for cid in row["classes"]}
    student_counts: dict[int, int] = {}
    if class_ids:
        for record in (
            Student.objects.filter(
                current_class_id__in=class_ids,
                status__in=[Student.Status.ACTIVE, Student.Status.REPEATING],
            )
            .values("current_class_id")
            .annotate(n=Count("id"))
        ):
            student_counts[record["current_class_id"]] = record["n"]

    rows = []
    for teacher in teachers:
        stats = per_teacher.get(teacher.pk, {"classes": {}, "subjects": set(), "assignment_count": 0})
        classes = sorted(
            stats["classes"].values(), key=lambda c: (c.level_order, c.name)
        )
        rows.append(
            {
                "teacher": teacher,
                "name": _display_name(teacher.user),
                "homeroom": homeroom.get(teacher.user_id, []),
                "classes": [c.name for c in classes],
                "class_count": len(classes),
                "subjects": sorted(stats["subjects"]),
                "subject_count": len(stats["subjects"]),
                "assignment_count": stats["assignment_count"],
                "student_count": sum(
                    student_counts.get(c.pk, 0) for c in classes
                ),
            }
        )
    rows.sort(
        key=lambda r: (
            r["assignment_count"] == 0,
            r["teacher"].user.last_name,
            r["teacher"].user.first_name,
        )
    )
    return {
        "rows": rows,
        "teacher_count": len(rows),
        "assigned_count": sum(1 for r in rows if r["assignment_count"]),
        "homeroom_count": sum(1 for r in rows if r["homeroom"]),
    }


def teacher_detail_groups(*, teacher_pk: int) -> list[dict]:
    """
    A teacher's assignments grouped by academic year, then class.

    Current year first, so the detail page opens on the year that matters; older
    years stay visible below instead of disappearing from the history.
    """
    teacher = get_teacher(teacher_pk)
    by_year: dict[int, dict] = {}
    for a in teacher.assignments.all():
        group = by_year.setdefault(
            a.academic_year_id, {"year": a.academic_year, "classes": {}}
        )
        per_class = group["classes"].setdefault(
            a.school_class_id, {"class": a.school_class, "assignments": []}
        )
        per_class["assignments"].append(a)

    groups = []
    for group in by_year.values():
        classes = sorted(
            group["classes"].values(),
            key=lambda c: (c["class"].level_order, c["class"].name),
        )
        for per_class in classes:
            per_class["assignments"].sort(
                key=lambda a: (a.subject.name, a.stream.name if a.stream_id else "")
            )
            per_class["is_homeroom"] = (
                per_class["class"].class_teacher_id == teacher.user_id
            )
            per_class["subjects"] = sorted(
                {a.subject.name for a in per_class["assignments"]}
            )
        groups.append({"year": group["year"], "classes": classes})

    groups.sort(key=lambda g: g["year"].start_date, reverse=True)
    groups.sort(key=lambda g: not g["year"].is_current)
    return groups


def teacher_profile_summary(*, teacher_pk: int, year_id: int | None) -> dict:
    """The numbers above a teacher's assignment list, scoped to one year."""
    from apps.students.models import Student

    summary = {
        "year": None,
        "homeroom_count": 0,
        "class_count": 0,
        "subject_count": 0,
        "student_count": 0,
        "homeroom": [],
    }
    if not year_id:
        return summary

    teacher = get_teacher(teacher_pk)
    summary["year"] = AcademicYear.objects.filter(pk=year_id).first()

    assignments = teacher.assignments.filter(academic_year_id=year_id)
    class_ids = {a.school_class_id for a in assignments}
    summary["class_count"] = len(class_ids)
    summary["subject_count"] = len({a.subject_id for a in assignments})
    if class_ids:
        summary["student_count"] = Student.objects.filter(
            current_class_id__in=class_ids,
            status__in=[Student.Status.ACTIVE, Student.Status.REPEATING],
        ).count()

    summary["homeroom"] = list(
        SchoolClass.objects.filter(
            academic_year_id=year_id, class_teacher_id=teacher.user_id
        ).order_by("level_order", "name")
    )
    summary["homeroom_count"] = len(summary["homeroom"])
    return summary


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
