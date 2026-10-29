"""
apps/results/services.py
------------------------
Grading configuration validation and ResultSheet state machine.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.audit_log import log_action
from apps.core.events import record_event

from .models import (
    Exam,
    GradeBand,
    GradeScale,
    GradeScaleBand,
    GradingConfig,
    ResultSheet,
    ScoreEntry,
)

#: Every term carries Exam 1 … Exam 10, matching the school's ten assessment
#: events. Rows are created lazily for terms that predate the exam dimension.
EXAM_COUNT = 10

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
    from apps.accounts.models import User
    from apps.teachers.models import TeacherProfile


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def ensure_exams_for_term(term) -> list[Exam]:
    """Return the term's Exam 1–10 rows, creating any that are missing."""
    existing = set(
        Exam.objects.filter(term=term).values_list("order", flat=True)
    )
    missing = [
        Exam(term=term, name=f"Exam {order}", order=order)
        for order in range(1, EXAM_COUNT + 1)
        if order not in existing
    ]
    if missing:
        Exam.objects.bulk_create(missing, ignore_conflicts=True)
    return list(Exam.objects.filter(term=term).order_by("order"))


@transaction.atomic
def save_grading_config(
    *,
    academic_year: AcademicYear,
    pass_mark: Decimal | float | str,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> GradingConfig:
    """Save the year's pass mark. Grade bands live on the year's grading system."""
    config, _ = GradingConfig.objects.update_or_create(
        academic_year=academic_year,
        defaults={"pass_mark": _d(pass_mark)},
    )

    log_action(
        actor=actor,
        action="results.grading_saved",
        content_type="results.GradingConfig",
        object_id=config.pk,
        change_summary={"year": academic_year.name, "pass_mark": str(pass_mark)},
        request=request,
    )
    return config


def validate_grade_bands(bands: list[dict]) -> None:
    """
    Bands must cover 0–100 with no overlaps.
    Adjacent integer bands are contiguous when next.min == prev.max + 1
    (e.g. 0–39 then 40–49). Overlap if next.min <= prev.max.
    Gap if next.min > prev.max + 1.
    """
    if not bands:
        raise ValidationError({"bands": "At least one grade band is required."})

    sorted_bands = sorted(bands, key=lambda b: _d(b["min_score"]))
    grades = [b["grade"] for b in sorted_bands]
    if len(grades) != len(set(grades)):
        raise ValidationError({"bands": "Grade labels must be unique."})

    if _d(sorted_bands[0]["min_score"]) != Decimal(0):
        raise ValidationError({"bands": "Grade scale must start at 0."})

    last_max = _d(sorted_bands[-1]["max_score"])
    if last_max != Decimal(100):
        if last_max < Decimal(100):
            raise ValidationError(
                {"bands": f"Grade scale must end at 100 (ends at {last_max})."}
            )
        raise ValidationError({"bands": "Grade scale cannot exceed 100."})

    for i, b in enumerate(sorted_bands):
        lo, hi = _d(b["min_score"]), _d(b["max_score"])
        if hi < lo:
            raise ValidationError(
                {"bands": f"Band {b['grade']}: max_score must be ≥ min_score."}
            )
        if i == 0:
            continue
        prev_hi = _d(sorted_bands[i - 1]["max_score"])
        if lo <= prev_hi:
            raise ValidationError(
                {
                    "bands": (
                        f"Overlap at {lo} (grade {b['grade']} overlaps previous band)."
                    )
                }
            )
        if lo > prev_hi + Decimal(1):
            raise ValidationError(
                {
                    "bands": (
                        f"Gap in scale between {prev_hi} and {lo} "
                        f"(before grade {b['grade']})."
                    )
                }
            )


def resolve_grade(
    academic_year_id: int, total: Decimal | float | str
) -> tuple[str, str, Decimal]:
    """Return (grade, remark, points) from the year's fallback bands."""
    total_d = _d(total)
    band = (
        GradeBand.objects.filter(
            academic_year_id=academic_year_id,
            min_score__lte=total_d,
            max_score__gte=total_d,
        )
        .order_by("-min_score")
        .first()
    )
    if not band:
        return "", "", Decimal(0)
    return band.grade, band.remark, band.points


@transaction.atomic
def save_grade_scale(
    *,
    academic_year: AcademicYear,
    name: str,
    bands: list[dict],
    classes,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> dict:
    """
    Create or update the year's grading system and its class selection.

    ``classes`` are ``SchoolClass`` rows; every one must belong to
    ``academic_year``. Classes in the year that are not selected lose their
    assignment and fall back to the year's legacy ``GradeBand`` rows. All
    scored entries in the year are re-graded afterwards so already published
    marks follow the new scale.
    """
    from apps.academics.models import SchoolClass

    bands = list(bands)
    validate_grade_bands(bands)

    year_classes = list(SchoolClass.objects.filter(academic_year=academic_year))
    year_class_ids = {school_class.pk for school_class in year_classes}
    selected_ids = {school_class.pk for school_class in classes}
    unknown = selected_ids - year_class_ids
    if unknown:
        raise ValidationError(
            {"classes": "Every selected class must belong to this academic year."}
        )

    scale, _ = GradeScale.objects.update_or_create(
        academic_year=academic_year,
        defaults={"name": name.strip()},
    )

    scale.bands.all().delete()
    GradeScaleBand.objects.bulk_create(
        [
            GradeScaleBand(
                scale=scale,
                min_score=_d(b["min_score"]),
                max_score=_d(b["max_score"]),
                grade=b["grade"],
                remark=b.get("remark", ""),
                points=_d(b.get("points", 0)),
            )
            for b in bands
        ]
    )

    SchoolClass.objects.filter(pk__in=year_class_ids).update(grade_scale=None)
    SchoolClass.objects.filter(pk__in=selected_ids).update(grade_scale=scale)

    summary = regrade_entries_for_classes(
        classes=year_classes, actor=actor, request=request, scale=scale
    )
    log_action(
        actor=actor,
        action="results.grade_scale_saved",
        content_type="results.GradeScale",
        object_id=scale.pk,
        change_summary={
            "year": academic_year.name,
            "name": scale.name,
            "bands": len(bands),
            "classes": len(selected_ids),
        },
        request=request,
    )
    return {"scale": scale, "regrade": summary}


@transaction.atomic
def delete_grade_scale(
    *,
    scale: GradeScale,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> dict:
    """Remove the year's grading system; its classes fall back to year bands."""
    from apps.academics.models import SchoolClass

    academic_year = scale.academic_year
    classes = list(SchoolClass.objects.filter(academic_year=academic_year))
    scale_name = scale.name
    scale.delete()

    summary = regrade_entries_for_classes(
        classes=classes, actor=actor, request=request
    )
    log_action(
        actor=actor,
        action="results.grade_scale_deleted",
        content_type="results.GradeScale",
        object_id="",
        change_summary={
            "year": str(academic_year),
            "name": scale_name,
            **summary,
        },
        request=request,
    )
    return summary


def grade_bands_for_class(school_class, academic_year_id: int | None = None) -> list:
    """
    Bands that apply to a class, highest first.

    A class included in its year's grading system uses that system's bands.
    A class left out falls back to the year's ``GradeBand`` rows.
    """
    if school_class.grade_scale_id:
        return list(
            GradeScaleBand.objects.filter(scale_id=school_class.grade_scale_id)
            .order_by("-min_score")
        )
    year_id = academic_year_id or school_class.academic_year_id
    return list(GradeBand.objects.filter(academic_year_id=year_id).order_by("-min_score"))


def resolve_grade_for_school_class(
    school_class, total: Decimal | float | str, academic_year_id: int | None = None
) -> tuple[str, str, Decimal]:
    """Resolve a grade using the class's scale, falling back to the year's bands."""
    total_d = _d(total)
    for band in grade_bands_for_class(school_class, academic_year_id):
        if band.min_score <= total_d <= band.max_score:
            return band.grade, band.remark, band.points
    return "", "", Decimal(0)


@transaction.atomic
def create_result_sheet(
    *,
    school_class: SchoolClass,
    subject: Subject,
    term: Term,
    teacher: TeacherProfile | None = None,
    stream=None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> ResultSheet:
    sheet = ResultSheet(
        school_class=school_class,
        subject=subject,
        term=term,
        teacher=teacher,
        stream=stream,
        status=ResultSheet.Status.DRAFT,
    )
    sheet.full_clean()
    sheet.save()
    log_action(
        actor=actor,
        action="results.sheet_created",
        content_type="results.ResultSheet",
        object_id=sheet.pk,
        change_summary={
            "class": school_class.name,
            "subject": subject.code,
            "term": str(term),
        },
        request=request,
    )
    return sheet


def _transition(
    *,
    sheet: ResultSheet,
    new_status: str,
    actor: User | None,
    request: HttpRequest | None,
    action: str,
    extra_summary: dict | None = None,
    reopen_reason: str = "",
) -> ResultSheet:
    if not sheet.can_transition_to(new_status):
        raise ValidationError(
            f"Cannot transition from '{sheet.status}' to '{new_status}'."
        )
    if (
        new_status == ResultSheet.Status.DRAFT
        and sheet.status != ResultSheet.Status.DRAFT
    ):
        if not reopen_reason.strip():
            raise ValidationError({"reopen_reason": "A reason is required to reopen."})
        sheet.reopen_reason = reopen_reason.strip()

    old = sheet.status
    sheet.status = new_status
    now = timezone.now()
    update_fields = ["status", "updated_at", "reopen_reason"]

    if new_status == ResultSheet.Status.SUBMITTED:
        sheet.submitted_at = now
        update_fields.append("submitted_at")
    elif new_status == ResultSheet.Status.CONFIRMED:
        sheet.confirmed_at = now
        sheet.confirmed_by = actor
        update_fields.extend(["confirmed_at", "confirmed_by"])
    elif new_status == ResultSheet.Status.PUBLISHED:
        sheet.published_at = now
        sheet.published_by = actor
        update_fields.extend(["published_at", "published_by"])
    elif new_status == ResultSheet.Status.DRAFT:
        sheet.submitted_at = None
        sheet.confirmed_at = None
        sheet.confirmed_by = None
        sheet.published_at = None
        sheet.published_by = None
        update_fields.extend(
            [
                "submitted_at",
                "confirmed_at",
                "confirmed_by",
                "published_at",
                "published_by",
            ]
        )

    sheet.save(update_fields=update_fields)
    summary = {"from": old, "to": new_status, **(extra_summary or {})}
    if reopen_reason:
        summary["reopen_reason"] = reopen_reason
    log_action(
        actor=actor,
        action=action,
        content_type="results.ResultSheet",
        object_id=sheet.pk,
        change_summary=summary,
        request=request,
    )
    return sheet


@transaction.atomic
def submit_sheet(*, sheet: ResultSheet, actor=None, request=None) -> ResultSheet:
    return _transition(
        sheet=sheet,
        new_status=ResultSheet.Status.SUBMITTED,
        actor=actor,
        request=request,
        action="results.submitted",
    )


@transaction.atomic
def confirm_sheet(*, sheet: ResultSheet, actor=None, request=None) -> ResultSheet:
    return _transition(
        sheet=sheet,
        new_status=ResultSheet.Status.CONFIRMED,
        actor=actor,
        request=request,
        action="results.confirmed",
    )


@transaction.atomic
def publish_sheet(*, sheet: ResultSheet, actor=None, request=None) -> ResultSheet:
    result = _transition(
        sheet=sheet,
        new_status=ResultSheet.Status.PUBLISHED,
        actor=actor,
        request=request,
        action="results.published",
    )
    # Invalidate director KPI cache so the pass rate reflects the newly published results
    try:
        from apps.dashboards.cache import invalidate_school_kpis

        invalidate_school_kpis(term_id=sheet.term_id)
    except Exception:  # noqa: BLE001, S110
        pass
    record_event(
        event_type="results.published",
        actor=actor,
        data={
            "sheet_id": sheet.pk,
            "class": str(sheet.school_class),
            "subject": str(sheet.subject),
            "term_id": sheet.term_id,
        },
        request=request,
    )
    return result


@transaction.atomic
def publish_class_term(
    *,
    school_class: SchoolClass,
    term: Term,
    actor=None,
    request=None,
) -> int:
    """Publish all confirmed sheets for a class/term. Returns count published."""
    sheets = ResultSheet.objects.filter(
        school_class=school_class,
        term=term,
        status=ResultSheet.Status.CONFIRMED,
    )
    count = 0
    for sheet in sheets:
        publish_sheet(sheet=sheet, actor=actor, request=request)
        count += 1
    return count


@transaction.atomic
def reopen_sheet(
    *,
    sheet: ResultSheet,
    reason: str,
    actor=None,
    request=None,
) -> ResultSheet:
    return _transition(
        sheet=sheet,
        new_status=ResultSheet.Status.DRAFT,
        actor=actor,
        request=request,
        action="results.reopened",
        reopen_reason=reason,
    )


@transaction.atomic
def upsert_score_entry(
    *,
    sheet: ResultSheet,
    student,
    raw_score: Decimal | float | str | None,
    actor=None,
    request=None,
) -> ScoreEntry | None:
    """
    Save one student's exam mark (0–100) on a sheet.

    An empty ``raw_score`` clears the student's entry. The mark is stored as
    both ``raw_score`` and ``total`` so legacy student-facing pages keep
    working, and is graded by the class's scale (or the year's fallback).
    """
    if sheet.is_locked:
        raise ValidationError(
            "Cannot edit scores on a locked (confirmed/published) sheet."
        )

    if raw_score is None or raw_score == "":
        ScoreEntry.objects.filter(result_sheet=sheet, student=student).delete()
        return None

    raw = _d(raw_score)
    if raw < 0 or raw > 100:
        raise ValidationError({"raw_score": "Marks must be between 0 and 100."})

    grade, remark, points = resolve_grade_for_school_class(
        sheet.school_class, raw, sheet.term.academic_year_id
    )

    entry, _ = ScoreEntry.objects.update_or_create(
        result_sheet=sheet,
        student=student,
        defaults={
            "raw_score": raw,
            "total": raw,
            "grade": grade,
            "remark": remark,
            "points": points,
        },
    )
    return entry


@transaction.atomic
def regrade_entries_for_classes(
    *, classes, actor=None, request=None, scale: GradeScale | None = None
) -> dict:
    """
    Recompute grade/remark/points for every scored entry on the given classes'
    sheets, using each class's current scale.

    Called after the year's grading system or its class selection changes so
    the new scale applies to marks that were already entered, published ones
    included.
    """
    class_ids = [school_class.pk for school_class in classes]
    entries = ScoreEntry.objects.filter(
        result_sheet__school_class_id__in=class_ids, total__isnull=False
    ).select_related("result_sheet__school_class", "result_sheet__term")

    total_count = 0
    changed = 0
    for entry in entries.iterator():
        sheet = entry.result_sheet
        grade, remark, points = resolve_grade_for_school_class(
            sheet.school_class, entry.total, sheet.term.academic_year_id
        )
        if (entry.grade, entry.remark, entry.points) != (grade, remark, points):
            entry.grade = grade
            entry.remark = remark
            entry.points = points
            entry.save(update_fields=["grade", "remark", "points", "updated_at"])
            changed += 1
        total_count += 1

    summary = {
        "classes": len(class_ids),
        "entries": total_count,
        "changed": changed,
    }
    if scale is not None:
        log_action(
            actor=actor,
            action="results.grade_scale_regraded",
            content_type="results.GradeScale",
            object_id=scale.pk,
            change_summary=summary,
            request=request,
        )
    else:
        log_action(
            actor=actor,
            action="results.regraded",
            content_type="academics.SchoolClass",
            object_id=",".join(str(pk) for pk in class_ids),
            change_summary=summary,
            request=request,
        )
    return summary
