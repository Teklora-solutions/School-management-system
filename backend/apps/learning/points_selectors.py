"""
apps/learning/points_selectors.py
------------------------------------
Read-only queries for the points and levels system (Piece 3).

Public API
----------
  student_points_summary(student) → StudentPointsSummary
      Everything the student/parent badge page needs: level, total points,
      progress to next level, recent ledger history, and badge list.

  class_points_overview(school_class) → list[StudentPointsRow]
      Teacher view: one row per active student with total, level and recent reason.

  director_points_overview(academic_year) → list[ClassPointsRow]
      Director view: one row per class showing average points and top earner.

  ledger_for_student(student, limit) → QuerySet
      Teacher-facing ledger view for one student (most recent first).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from django.db.models import Sum

from .models import PointLedger, StudentLevel
from .points_config import LEVELS, Level, level_for_total, next_level


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class StudentPointsSummary:
    student: object
    total_points: int
    current_level: Level
    next_level_obj: Optional[Level]
    points_to_next: int               # 0 when already at top level
    pct_to_next: int                  # 0-100 progress bar within current level band
    recent_ledger: list               # last 10 PointLedger rows
    all_levels: list[Level] = field(default_factory=lambda: LEVELS)

    @property
    def at_top_level(self) -> bool:
        return self.next_level_obj is None

    @property
    def badge_icon(self) -> str:
        return self.current_level.badge_icon


@dataclass
class StudentPointsRow:
    student: object
    total_points: int
    level: Level
    last_reason: str      # display label of most recent ledger entry
    last_awarded: object  # datetime or None


@dataclass
class ClassPointsRow:
    school_class: object
    student_count: int
    avg_points: int
    top_earner_name: str
    top_earner_points: int
    engagement_on: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _total_for(student) -> int:
    result = (
        PointLedger.objects.filter(student=student)
        .aggregate(t=Sum("points"))["t"]
    )
    return max(0, result or 0)


def _pct_within_band(total: int, current: Level, nxt: Optional[Level]) -> int:
    if nxt is None:
        return 100
    band = nxt.min_points - current.min_points
    if band <= 0:
        return 100
    earned_in_band = total - current.min_points
    return min(100, max(0, round(earned_in_band * 100 / band)))


# ---------------------------------------------------------------------------
# Public selectors
# ---------------------------------------------------------------------------


def student_points_summary(*, student) -> StudentPointsSummary:
    """Everything the student/parent badge page needs."""
    total = _total_for(student)
    current = level_for_total(total)
    nxt = next_level(current)
    pts_to_next = (nxt.min_points - total) if nxt else 0
    pct = _pct_within_band(total, current, nxt)

    recent = list(
        PointLedger.objects.filter(student=student)
        .select_related("awarded_by")
        .order_by("-created_at")[:10]
    )

    return StudentPointsSummary(
        student=student,
        total_points=total,
        current_level=current,
        next_level_obj=nxt,
        points_to_next=max(0, pts_to_next),
        pct_to_next=pct,
        recent_ledger=recent,
    )


def class_points_overview(*, school_class) -> list[StudentPointsRow]:
    """
    One row per active student in *school_class*, sorted by total_points desc.
    Two queries total: one for all student ledger sums, one for recent entries.
    """
    from apps.students.models import Student

    students = list(
        Student.objects.filter(
            current_class=school_class,
            status=Student.Status.ACTIVE,
        ).select_related("user")
    )
    if not students:
        return []

    student_ids = [s.pk for s in students]

    # Bulk-fetch totals
    totals = {
        row["student_id"]: row["t"]
        for row in PointLedger.objects.filter(student_id__in=student_ids)
        .values("student_id")
        .annotate(t=Sum("points"))
    }

    # Bulk-fetch most recent ledger entry per student
    from django.db.models import Max
    latest_ids = (
        PointLedger.objects.filter(student_id__in=student_ids)
        .values("student_id")
        .annotate(latest=Max("id"))
        .values_list("latest", flat=True)
    )
    latest_rows = {
        r.student_id: r
        for r in PointLedger.objects.filter(pk__in=latest_ids).select_related()
    }

    rows = []
    for stu in students:
        total = max(0, totals.get(stu.pk, 0) or 0)
        lvl = level_for_total(total)
        latest = latest_rows.get(stu.pk)
        rows.append(
            StudentPointsRow(
                student=stu,
                total_points=total,
                level=lvl,
                last_reason=latest.get_reason_display() if latest else "—",
                last_awarded=latest.created_at if latest else None,
            )
        )

    rows.sort(key=lambda r: -r.total_points)
    return rows


def director_points_overview(*, academic_year) -> list[ClassPointsRow]:
    """
    One row per class in *academic_year* for the director.
    Cheap: bulk aggregation, not per-student queries.
    """
    from apps.academics.models import SchoolClass
    from apps.students.models import Student
    from apps.learning.models import ClassEngagementSettings

    classes = list(
        SchoolClass.objects.filter(academic_year=academic_year)
        .order_by("level_order", "name")
    )
    if not classes:
        return []

    rows = []
    for cls in classes:
        # Engagement on/off
        try:
            eng = cls.engagement_settings
            engagement_on = eng.is_active
        except ClassEngagementSettings.DoesNotExist:
            engagement_on = False

        student_ids = list(
            Student.objects.filter(
                current_class=cls,
                status=Student.Status.ACTIVE,
            ).values_list("pk", flat=True)
        )
        n = len(student_ids)

        if n == 0 or not engagement_on:
            rows.append(
                ClassPointsRow(
                    school_class=cls,
                    student_count=n,
                    avg_points=0,
                    top_earner_name="—",
                    top_earner_points=0,
                    engagement_on=engagement_on,
                )
            )
            continue

        totals = {
            row["student_id"]: row["t"] or 0
            for row in PointLedger.objects.filter(student_id__in=student_ids)
            .values("student_id")
            .annotate(t=Sum("points"))
        }
        all_totals = [max(0, totals.get(sid, 0)) for sid in student_ids]
        avg = round(sum(all_totals) / n) if n else 0

        # Top earner
        top_sid = max(student_ids, key=lambda sid: totals.get(sid, 0))
        top_pts = max(0, totals.get(top_sid, 0))
        try:
            top_stu = Student.objects.select_related("user").get(pk=top_sid)
            top_name = top_stu.user.get_full_name() or top_stu.admission_number
        except Student.DoesNotExist:
            top_name = "—"

        rows.append(
            ClassPointsRow(
                school_class=cls,
                student_count=n,
                avg_points=avg,
                top_earner_name=top_name,
                top_earner_points=top_pts,
                engagement_on=True,
            )
        )

    return rows


def ledger_for_student(*, student, limit: int = 50):
    """Teacher-facing full ledger for one student (most recent first)."""
    return (
        PointLedger.objects.filter(student=student)
        .select_related("awarded_by", "reversal_of")
        .order_by("-created_at")[:limit]
    )
