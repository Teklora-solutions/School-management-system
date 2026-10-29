"""
apps/attendance/selectors.py
----------------------------
Read-only attendance aggregates. Every function here returns numbers built by
the database, never by looping rows in Python — the Director portal renders
these per class and per weekday, and an N+1 here multiplies with class count.

Rate convention (shared by every function):
  numerator   = entries whose status is present or late
  denominator = every entry recorded in the window
An excused absence stays in the denominator, so a school cannot raise its
attendance rate by marking children "excused".
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.db.models import Count, Prefetch, Q
from django.db.models.functions import ExtractIsoWeekDay

from apps.students.models import Student

from .models import AttendanceEntry, AttendanceRegister

if TYPE_CHECKING:
    import datetime

    from apps.academics.models import SchoolClass

#: Mon=1 .. Sun=7, matching ``ExtractIsoWeekDay``.
WEEKDAY_LABELS = {
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}

_ATTENDED = Q(status__in=list(AttendanceEntry.ATTENDED_STATUSES))


def _attended_count() -> Count:
    return Count("id", filter=_ATTENDED, distinct=True)


def _entries(*, date_from, date_to, school_class=None):
    """Entry queryset for a closed date range, optionally one class."""
    qs = AttendanceEntry.objects.filter(date__gte=date_from, date__lte=date_to)
    if school_class is not None:
        qs = qs.filter(register__school_class=school_class)
    return qs


def summarise(
    *, date_from: datetime.date, date_to: datetime.date, school_class=None
) -> dict:
    """
    Totals and the attendance rate for one window.

    ``rate`` is ``None`` when nothing was recorded — the caller must render an
    honest "no data yet" rather than a 0% that looks like a crisis.
    """
    row = _entries(date_from=date_from, date_to=date_to, school_class=school_class).aggregate(
        total=Count("id", distinct=True),
        attended=_attended_count(),
        absent=Count("id", filter=Q(status=AttendanceEntry.Status.ABSENT), distinct=True),
        late=Count("id", filter=Q(status=AttendanceEntry.Status.LATE), distinct=True),
        excused=Count(
            "id", filter=Q(status=AttendanceEntry.Status.EXCUSED), distinct=True
        ),
    )
    total = row["total"] or 0
    return {
        "date_from": date_from,
        "date_to": date_to,
        "total": total,
        "attended": row["attended"],
        "absent": row["absent"],
        "late": row["late"],
        "excused": row["excused"],
        "rate": (row["attended"] * 100 / total) if total else None,
    }


def by_class(
    *, date_from: datetime.date, date_to: datetime.date, class_ids=None
) -> list[dict]:
    """
    One row per class in the window, strongest attendance first.

    ``class_ids`` narrows the query without a Python filter so callers can scope
    a term's classes in SQL. Sorting happens here rather than in SQL because
    there is one row per class, and a rate built from two aggregates needs a
    subquery to order by in the database.
    """
    qs = _entries(date_from=date_from, date_to=date_to)
    if class_ids is not None:
        qs = qs.filter(register__school_class_id__in=list(class_ids))
    rows = (
        qs.values(
            "register__school_class_id",
            "register__school_class__name",
            "register__school_class__level_order",
        )
        .annotate(total=Count("id", distinct=True), attended=_attended_count())
    )
    out = [
        {
            "class_id": row["register__school_class_id"],
            "class_name": row["register__school_class__name"],
            "level_order": row["register__school_class__level_order"],
            "total": row["total"],
            "attended": row["attended"],
            "rate": (row["attended"] * 100 / row["total"]) if row["total"] else None,
        }
        for row in rows
    ]
    # Classes with data first, best rate first; ties by seniority then name.
    out.sort(key=lambda r: (r["rate"] is None, -(r["rate"] or 0), -r["level_order"], r["class_name"]))
    return out


def by_weekday(*, date_from: datetime.date, date_to: datetime.date) -> list[dict]:
    """
    Monday..Sunday, always all seven keys.

    A day with no register shows ``total=0`` and ``rate=None`` — the pattern the
    Director is looking for (classes never marked on Fridays) needs the empty
    days to still appear.
    """
    rows = (
        _entries(date_from=date_from, date_to=date_to)
        .annotate(weekday=ExtractIsoWeekDay("date"))
        .values("weekday")
        .annotate(
            total=Count("id", distinct=True),
            attended=_attended_count(),
        )
    )
    found = {row["weekday"]: row for row in rows}
    out = []
    for weekday, label in WEEKDAY_LABELS.items():
        row = found.get(weekday)
        total = row["total"] if row else 0
        out.append(
            {
                "weekday": weekday,
                "label": label,
                "total": total,
                "attended": row["attended"] if row else 0,
                "rate": (row["attended"] * 100 / total) if total else None,
            }
        )
    return out


def chronic_absences(
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    threshold: int = 5,
    class_ids=None,
    limit: int = 50,
) -> list[dict]:
    """
    Students with at least ``threshold`` unexcused absences in the window.

    Absent only: late and excused never count toward this. Ordered worst first,
    ties broken by admission number so the list is stable across pages.
    """
    qs = _entries(date_from=date_from, date_to=date_to).filter(
        status=AttendanceEntry.Status.ABSENT
    )
    if class_ids is not None:
        qs = qs.filter(register__school_class_id__in=list(class_ids))
    rows = (
        qs.values(
            "student_id",
            "student__admission_number",
            "student__user__first_name",
            "student__user__last_name",
        )
        .annotate(absences=Count("id", distinct=True))
        .filter(absences__gte=threshold)
        .order_by("-absences", "student__admission_number")[:limit]
    )
    return [
        {
            "student_id": row["student_id"],
            "admission_number": row["student__admission_number"],
            # ``User.full_name`` is a property, so it cannot come out of
            # ``values()``; the name is assembled from the two stored fields.
            "name": " ".join(
                n for n in (row["student__user__first_name"], row["student__user__last_name"]) if n
            ),
            "absences": row["absences"],
        }
        for row in rows
    ]


def marking_compliance(
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    school_classes: Iterable[SchoolClass],
) -> dict:
    """
    How many registers each class has against how many days it should have had.

    ``school_classes`` is the expected set — the caller decides whether that is
    "classes in this term" or "the whole school", because only the caller knows
    which days the school was actually sitting. Every class in the iterable
    counts as one expected register per day in the range, weekends included;
    pass a school-day list if that over-counts for your report.
    """
    classes = list(school_classes)
    days = (date_to - date_from).days + 1
    filled = {
        row["school_class_id"]: row["n"]
        for row in AttendanceRegister.objects.filter(
            date__gte=date_from, date__lte=date_to
        )
        .values("school_class_id")
        .annotate(n=Count("id"))
    }
    expected_per_class = days
    rows = [
        {
            "class_id": cls.pk,
            # ``name`` only: ``str(cls)`` would resolve ``academic_year`` and
            # issue one query per class in the list.
            "class_name": cls.name,
            "expected": expected_per_class,
            "filled": filled.get(cls.pk, 0),
        }
        for cls in classes
    ]
    expected_total = len(classes) * expected_per_class
    return {
        "expected": expected_total,
        "filled": sum(filled.get(cls.pk, 0) for cls in classes),
        "by_class": rows,
    }


def register_for(*, school_class, date) -> AttendanceRegister | None:
    """
    The register for one class on one day, with its lines and pupils loaded.

    Two queries however many children are on the sheet: the register, then its
    entries joined to student and user. A screen listing marks one row at a time
    would otherwise be the one place attendance is read slowly.
    """
    entries = AttendanceEntry.objects.select_related("student__user")
    return (
        AttendanceRegister.objects.filter(school_class=school_class, date=date)
        .prefetch_related(Prefetch("entries", queryset=entries))
        .first()
    )


def student_history(*, student: Student, date_from, date_to, limit: int = 60):
    """A student's marks in the window, most recent first."""
    return (
        AttendanceEntry.objects.filter(
            student=student, date__gte=date_from, date__lte=date_to
        )
        .order_by("-date")[:limit]
    )
