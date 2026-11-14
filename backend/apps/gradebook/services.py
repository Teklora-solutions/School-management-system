"""
apps/gradebook/services.py
--------------------------
The gradebook's one calculation: turn counted homework, quizzes, hand-recorded
work and the end-of-term sheet into a grid of percentages per pupil.

The rules the school agreed to:

- A missing mark counts as zero and stays in its bucket's denominator. Silence
  is never an excuse by accident.
- An excuse removes that one item from one pupil's numerator *and* denominator,
  so the rest of the bucket is re-averaged for them.
- A bucket with no counted work at all drops out and its weight is shared
  between the buckets that do have work. The share is shown, not hidden.
- The overall mark is the weighted average of the bucket percentages. A pupil
  with nothing scorable has no overall mark — not a zero.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.audit_log import log_action

from . import selectors
from .models import (
    GradebookExcuse,
    GradebookRelease,
    GradebookResult,
    GradebookSetup,
    GradebookTermLock,
    ManualItem,
    ManualScore,
)

BUCKETS: tuple[tuple[str, str, str], ...] = (
    ("homework", "Homework", "homework_weight"),
    ("quiz", "Quizzes", "quiz_weight"),
    ("lab", "Labs", "lab_weight"),
    ("exam", "End-of-term exam", "exam_weight"),
)

BUCKET_LABELS = {key: label for key, label, _field in BUCKETS}

SETUP_CONTENT_TYPE = "gradebook.GradebookSetup"
MANUAL_CONTENT_TYPE = "gradebook.ManualItem"
EXCUSE_CONTENT_TYPE = "gradebook.GradebookExcuse"
RELEASE_CONTENT_TYPE = "gradebook.GradebookRelease"
LOCK_CONTENT_TYPE = "gradebook.GradebookTermLock"

LOCKED_MESSAGE = (
    "This term's gradebook is locked. Ask an administrator to unlock it first."
)
NO_WORK_MESSAGE = "There is no counted work to release yet."
RERELEASE_REASON_MESSAGE = "Say what changed since the last release."

REPORT_CARD_CONTENT_TYPE = "results.ResultSheet"
RELEASE_REQUIRED_MESSAGE = (
    "Release the gradebook before sending marks to the report card."
)
SHEET_REQUIRED_MESSAGE = (
    "Choose the end-of-term sheet in Weights & manual work first."
)
STREAM_MISMATCH_MESSAGE = (
    "The chosen sheet covers a different stream from this gradebook. "
    "Choose a sheet for the same stream."
)
SHEET_NOT_DRAFT_MESSAGE = (
    "The report card sheet is {status}. It can only receive marks while it is "
    "a draft; ask an administrator to reopen it with a reason."
)
NOTHING_TO_SEND_MESSAGE = "No pupil has a weighted mark to send yet."


def q1(value) -> Decimal:
    """A percentage rounded to one decimal place."""
    return Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def num(value) -> str:
    """A decimal typed as a person would write it (15, not 15.0)."""
    return format(Decimal(str(value)).normalize(), "f")


def term_is_locked(term) -> bool:
    lock = getattr(term, "gradebook_lock", None)
    return bool(lock and lock.is_locked)


@transaction.atomic
def get_or_create_setup(
    *,
    school_class,
    subject,
    term,
    stream=None,
    actor=None,
    request=None,
) -> GradebookSetup:
    """
    The classroom's setup, created with the agreed 30/30/20/20 defaults.

    Opening the gradebook must never fail because nobody has configured it yet;
    the weights are a starting point a teacher can change later.
    """
    setup = (
        GradebookSetup.objects.select_related(
            "school_class", "subject", "term", "stream", "exam_sheet"
        )
        .filter(
            school_class=school_class,
            subject=subject,
            term=term,
            stream=stream,
        )
        .first()
    )
    if setup is not None:
        return setup

    setup = GradebookSetup.objects.create(
        school_class=school_class,
        subject=subject,
        term=term,
        stream=stream,
        created_by=actor,
    )
    log_action(
        actor=actor,
        action="gradebook.setup_created",
        content_type=SETUP_CONTENT_TYPE,
        object_id=setup.pk,
        change_summary={
            "class": school_class.name,
            "subject": subject.code,
            "term": term.name,
            "weights": {
                "homework": setup.homework_weight,
                "quiz": setup.quiz_weight,
                "lab": setup.lab_weight,
                "exam": setup.exam_weight,
            },
        },
        request=request,
    )
    return setup


def effective_weights(setup: GradebookSetup, present: set[str]) -> dict[str, Decimal]:
    """
    The four weights, renormalised over the buckets that actually have work.

    Returns an empty mapping when no bucket has any counted item.
    """
    weights = {
        key: Decimal(getattr(setup, field))
        for key, _label, field in BUCKETS
        if key in present
    }
    total = sum(weights.values())
    if total <= 0:
        return {}
    return {
        key: q1(weight * Decimal("100") / total) for key, weight in weights.items()
    }


def _homework_item(homework, marks: dict) -> dict:
    return {
        "key": f"homework-{homework.pk}",
        "kind": "homework",
        "source_kind": "homework",
        "source_id": homework.pk,
        "title": homework.title,
        "bucket": "homework",
        "max_score": Decimal(homework.max_score),
        "date": homework.due_date,
        "scores": {
            student_id: mark.final_score
            for (homework_id, student_id), mark in marks.items()
            if homework_id == homework.pk
            and mark.status
            and mark.status != mark.Status.NOT_SUBMITTED
            and mark.final_score is not None
        },
    }


def _quiz_item(quiz, best: dict) -> dict:
    return {
        "key": f"quiz-{quiz.pk}",
        "kind": "quiz",
        "source_kind": "quiz",
        "source_id": quiz.pk,
        "title": quiz.title,
        "bucket": "quiz",
        "max_score": Decimal("100"),
        "date": None,
        "scores": {
            student_id: Decimal(score)
            for (quiz_id, student_id), score in best.items()
            if quiz_id == quiz.pk
        },
    }


def _manual_item(item, scores: dict) -> dict:
    return {
        "key": f"manual-{item.pk}",
        "kind": "manual",
        "source_kind": "manual",
        "source_id": item.pk,
        "title": item.title,
        "bucket": item.bucket,
        "max_score": Decimal(item.max_score),
        "date": item.worked_on,
        "scores": {
            student_id: score
            for (item_id, student_id), score in scores.items()
            if item_id == item.pk
        },
    }


def _exam_item(setup, scores: dict) -> dict:
    sheet = setup.exam_sheet
    title = f"End-of-term exam — {sheet.exam.name}" if sheet.exam_id else "End-of-term exam"
    return {
        "key": f"exam-{sheet.pk}",
        "kind": "exam",
        "source_kind": None,
        "source_id": sheet.pk,
        "title": title,
        "bucket": "exam",
        "max_score": Decimal("100"),
        "date": None,
        "scores": dict(scores),
    }


def collect_items(setup: GradebookSetup) -> list[dict]:
    """Every column in the grid, in bucket then date order."""
    items: list[dict] = []

    homework = list(selectors.counted_homework(setup))
    marks = selectors.homework_marks(homework_ids=[h.pk for h in homework])
    items.extend(_homework_item(h, marks) for h in homework)

    quizzes = list(selectors.counted_quizzes(setup))
    best = selectors.best_quiz_attempts(quiz_ids=[quiz.pk for quiz in quizzes])
    items.extend(_quiz_item(quiz, best) for quiz in quizzes)

    manual = list(selectors.counted_manual_items(setup))
    scores = selectors.manual_scores(item_ids=[item.pk for item in manual])
    items.extend(_manual_item(item, scores) for item in manual)

    if setup.exam_sheet_id is not None:
        items.append(_exam_item(setup, selectors.exam_scores(setup=setup)))

    for item in items:
        item["bucket_label"] = BUCKET_LABELS[item["bucket"]]
    return items


def build_grid(setup: GradebookSetup) -> dict:
    """The full grid: item columns, bucket percentages and one row per pupil."""
    roster = list(
        selectors.roster(
            school_class_id=setup.school_class_id,
            stream_id=setup.stream_id,
        )
    )
    items = collect_items(setup)
    excuses = selectors.excuses(setup)

    item_counts: dict[str, int] = {}
    for item in items:
        item_counts[item["bucket"]] = item_counts.get(item["bucket"], 0) + 1
    present = {key for key, count in item_counts.items() if count}
    weights = effective_weights(setup, present)

    rows = []
    for student in roster:
        cells: dict[str, dict] = {}
        earned = {key: Decimal("0") for key, _l, _f in BUCKETS}
        possible = {key: Decimal("0") for key, _l, _f in BUCKETS}
        missing_count = excused_count = scored_count = 0

        for item in items:
            key = item["key"]
            source_kind = item["source_kind"]
            is_excused = bool(
                source_kind
                and (student.pk, source_kind, item["source_id"]) in excuses
            )
            if is_excused:
                excused_count += 1
                cells[key] = {
                    "is_excused": True,
                    "is_missing": False,
                    "score": None,
                    "max_score": item["max_score"],
                    "item_title": item["title"],
                }
                continue

            score = item["scores"].get(student.pk)
            if score is None:
                missing_count += 1
                possible[item["bucket"]] += item["max_score"]
                cells[key] = {
                    "is_excused": False,
                    "is_missing": True,
                    "score": None,
                    "max_score": item["max_score"],
                    "item_title": item["title"],
                }
                continue

            score = max(Decimal("0"), min(Decimal(score), item["max_score"]))
            scored_count += 1
            earned[item["bucket"]] += score
            possible[item["bucket"]] += item["max_score"]
            cells[key] = {
                "is_excused": False,
                "is_missing": False,
                "score": score,
                "max_score": item["max_score"],
                "display": f"{num(score)}/{num(item['max_score'])}",
                "item_title": item["title"],
            }

        bucket_pcts: dict[str, Decimal] = {}
        for bucket_key, _label, _field in BUCKETS:
            if possible[bucket_key] > 0:
                bucket_pcts[bucket_key] = q1(
                    earned[bucket_key] * Decimal("100") / possible[bucket_key]
                )

        usable = {key: value for key, value in weights.items() if key in bucket_pcts}
        weighted_total = sum(usable.values())
        overall = (
            q1(
                sum(bucket_pcts[key] * weight for key, weight in usable.items())
                / weighted_total
            )
            if weighted_total > 0
            else None
        )

        rows.append(
            {
                "student": student,
                "cells_list": [cells[item["key"]] for item in items],
                "bucket_values": [
                    bucket_pcts.get(key)
                    for key, _label, _field in BUCKETS
                    if item_counts.get(key, 0)
                ],
                "bucket_pcts": bucket_pcts,
                "overall_pct": overall,
                "missing_count": missing_count,
                "excused_count": excused_count,
                "scored_count": scored_count,
            }
        )

    overalls = [row["overall_pct"] for row in rows if row["overall_pct"] is not None]
    bucket_columns = [key for key, _l, _f in BUCKETS if item_counts.get(key, 0)]
    return {
        "items": items,
        "buckets": [
            {
                "key": key,
                "label": label,
                "weight": getattr(setup, field),
                "effective_weight": weights.get(key),
                "item_count": item_counts.get(key, 0),
            }
            for key, label, field in BUCKETS
            if item_counts.get(key, 0)
        ],
        "rows": rows,
        "colspan": len(items) + len(bucket_columns) + 2,
        "roster_size": len(roster),
        "class_average": q1(sum(overalls) / len(overalls)) if overalls else None,
        "missing_total": sum(row["missing_count"] for row in rows),
        "scored_total": sum(row["scored_count"] for row in rows),
        "has_items": bool(items),
        "term_locked": term_is_locked(setup.term),
    }


def resolve_classroom(*, teacher_user_id: int, school_class_id: int, subject_id: int, term):
    """
    The teacher's assignment for this classroom and term.

    Raises ``PermissionDenied`` rather than returning ``None`` so a view cannot
    forget to check: no assignment means no gradebook.
    """
    from apps.teachers.models import TeacherAssignment

    assignment = (
        TeacherAssignment.objects.filter(
            teacher__user_id=teacher_user_id,
            school_class_id=school_class_id,
            subject_id=subject_id,
            academic_year=term.academic_year,
        )
        .select_related("school_class", "subject", "stream", "academic_year")
        .first()
    )
    if assignment is None:
        raise PermissionDenied("You are not assigned to this classroom.")
    return assignment


# ---------------------------------------------------------------------------
# Writes: weights, hand-recorded work, its marks, and excuses
# ---------------------------------------------------------------------------


def ensure_writable(setup: GradebookSetup) -> None:
    if term_is_locked(setup.term):
        raise ValidationError(LOCKED_MESSAGE)


def ensure_tick_writable(*terms) -> None:
    """
    The term lock also covers the tick that decides whether a source counts.

    Called from the homework and quiz save services, where the tick lives. Only
    enforced while the gradebook module is on: with it off there is no average
    for the tick to move.
    """
    from apps.core.features import feature_enabled

    if not feature_enabled("gradebook"):
        return
    if any(term is not None and term_is_locked(term) for term in terms):
        raise ValidationError(LOCKED_MESSAGE)


def _clean_reason(reason: str, message: str = "Give the reason.") -> str:
    text = (reason or "").strip()
    if not text:
        raise ValidationError(message)
    return text


def _roster_ids(setup: GradebookSetup) -> set[int]:
    return set(
        selectors.roster(
            school_class_id=setup.school_class_id,
            stream_id=setup.stream_id,
        ).values_list("pk", flat=True)
    )


@transaction.atomic
def update_weights(
    *,
    setup: GradebookSetup,
    homework_weight: int,
    quiz_weight: int,
    lab_weight: int,
    exam_weight: int,
    exam_sheet=None,
    actor=None,
    request=None,
) -> GradebookSetup:
    ensure_writable(setup)
    total = homework_weight + quiz_weight + lab_weight + exam_weight
    if total != 100:
        raise ValidationError(
            f"The four weights must add up to 100 (they add up to {total})."
        )
    if exam_sheet is not None and (
        exam_sheet.school_class_id != setup.school_class_id
        or exam_sheet.subject_id != setup.subject_id
        or exam_sheet.term_id != setup.term_id
    ):
        raise ValidationError("That sheet belongs to another classroom or term.")

    before = {
        "homework": setup.homework_weight,
        "quiz": setup.quiz_weight,
        "lab": setup.lab_weight,
        "exam": setup.exam_weight,
        "exam_sheet": setup.exam_sheet_id,
    }
    setup.homework_weight = homework_weight
    setup.quiz_weight = quiz_weight
    setup.lab_weight = lab_weight
    setup.exam_weight = exam_weight
    setup.exam_sheet = exam_sheet
    setup.save()

    log_action(
        actor=actor,
        action="gradebook.weights_updated",
        content_type=SETUP_CONTENT_TYPE,
        object_id=setup.pk,
        change_summary={
            "from": before,
            "to": {
                "homework": homework_weight,
                "quiz": quiz_weight,
                "lab": lab_weight,
                "exam": exam_weight,
                "exam_sheet": setup.exam_sheet_id,
            },
        },
        request=request,
    )
    return setup


def _clean_manual_fields(*, bucket: str, title: str, max_score, reason: str):
    if bucket not in ManualItem.Bucket.values:
        raise ValidationError("Choose homework, quizzes or labs for this item.")
    title = (title or "").strip()
    if not title:
        raise ValidationError("Give this piece of work a title.")
    try:
        max_score = Decimal(str(max_score))
    except Exception as exc:  # noqa: BLE001 - ValueError/InvalidOperation
        raise ValidationError("Enter the total as a number.") from exc
    if max_score <= 0:
        raise ValidationError("The total must be more than 0.")
    return bucket, title, max_score, _clean_reason(
        reason, "Say why this work is recorded by hand."
    )


@transaction.atomic
def create_manual_item(
    *,
    setup: GradebookSetup,
    bucket: str,
    title: str,
    max_score,
    worked_on,
    reason: str,
    counts_for_gradebook: bool = True,
    actor=None,
    request=None,
) -> ManualItem:
    ensure_writable(setup)
    bucket, title, max_score, reason = _clean_manual_fields(
        bucket=bucket, title=title, max_score=max_score, reason=reason
    )
    item = ManualItem.objects.create(
        setup=setup,
        bucket=bucket,
        title=title,
        max_score=max_score,
        worked_on=worked_on,
        reason=reason,
        counts_for_gradebook=counts_for_gradebook,
        created_by=actor,
    )
    log_action(
        actor=actor,
        action="gradebook.manual_item_created",
        content_type=MANUAL_CONTENT_TYPE,
        object_id=item.pk,
        change_summary={
            "setup": setup.pk,
            "bucket": bucket,
            "title": title,
            "max_score": str(max_score),
            "counts_for_gradebook": counts_for_gradebook,
            "reason": reason,
        },
        request=request,
    )
    return item


@transaction.atomic
def update_manual_item(
    *,
    item: ManualItem,
    bucket: str,
    title: str,
    max_score,
    worked_on,
    reason: str,
    counts_for_gradebook: bool,
    actor=None,
    request=None,
) -> ManualItem:
    ensure_writable(item.setup)
    bucket, title, max_score, reason = _clean_manual_fields(
        bucket=bucket, title=title, max_score=max_score, reason=reason
    )
    if item.scores.filter(score__gt=max_score).exists():
        raise ValidationError(
            "A mark already recorded is higher than the new total. "
            "Correct that mark first."
        )
    before = {
        "bucket": item.bucket,
        "title": item.title,
        "max_score": str(item.max_score),
        "worked_on": item.worked_on.isoformat(),
        "counts_for_gradebook": item.counts_for_gradebook,
        "reason": item.reason,
    }
    item.bucket = bucket
    item.title = title
    item.max_score = max_score
    item.worked_on = worked_on
    item.reason = reason
    item.counts_for_gradebook = counts_for_gradebook
    item.save()

    log_action(
        actor=actor,
        action="gradebook.manual_item_updated",
        content_type=MANUAL_CONTENT_TYPE,
        object_id=item.pk,
        change_summary={
            "from": before,
            "to": {
                "bucket": bucket,
                "title": title,
                "max_score": str(max_score),
                "worked_on": worked_on.isoformat(),
                "counts_for_gradebook": counts_for_gradebook,
                "reason": reason,
            },
        },
        request=request,
    )
    return item


@transaction.atomic
def remove_manual_item(*, item: ManualItem, actor=None, request=None) -> None:
    """
    Delete an item that never collected a mark.

    An item that already has marks is kept: untick it instead, which leaves the
    evidence in place and takes it out of the average.
    """
    ensure_writable(item.setup)
    if item.scores.exists():
        raise ValidationError(
            "This item already has marks. Untick it to leave it out of the "
            "average instead of deleting the marks."
        )
    summary = {
        "setup": item.setup_id,
        "bucket": item.bucket,
        "title": item.title,
        "max_score": str(item.max_score),
        "reason": item.reason,
    }
    item_id = item.pk
    item.delete()
    log_action(
        actor=actor,
        action="gradebook.manual_item_removed",
        content_type=MANUAL_CONTENT_TYPE,
        object_id=item_id,
        change_summary=summary,
        request=request,
    )


@transaction.atomic
def save_manual_scores(
    *,
    item: ManualItem,
    scores: dict[int, Decimal | None],
    actor=None,
    request=None,
) -> dict:
    """
    Save the marks for one item. A blank score deletes the stored mark, which
    reads as missing in the grid.

    ``scores`` is ``student_id → Decimal | None``; every pupil must be on the
    item's roster.
    """
    ensure_writable(item.setup)
    roster_ids = _roster_ids(item.setup)
    now = timezone.now()
    saved = cleared = 0

    for student_id, score in scores.items():
        student_id = int(student_id)
        if student_id not in roster_ids:
            raise ValidationError("That pupil is not in this class.")
        if score is None:
            cleared += ManualScore.objects.filter(
                item=item, student_id=student_id
            ).delete()[0]
            continue
        score = Decimal(str(score))
        if score < 0 or score > item.max_score:
            raise ValidationError(
                f"A mark on '{item.title}' must be between 0 and {item.max_score}."
            )
        ManualScore.objects.update_or_create(
            item=item,
            student_id=student_id,
            defaults={
                "score": score,
                "entered_by": actor,
                "entered_at": now,
            },
        )
        saved += 1

    log_action(
        actor=actor,
        action="gradebook.manual_scores_saved",
        content_type=MANUAL_CONTENT_TYPE,
        object_id=item.pk,
        change_summary={
            "setup": item.setup_id,
            "title": item.title,
            "max_score": str(item.max_score),
            "saved": saved,
            "cleared": cleared,
        },
        request=request,
    )
    return {"saved": saved, "cleared": cleared}


@transaction.atomic
def record_excuse(
    *,
    setup: GradebookSetup,
    student,
    source_kind: str,
    source_id: int,
    reason: str,
    actor=None,
    request=None,
) -> GradebookExcuse:
    ensure_writable(setup)
    counted = {
        (item["source_kind"], item["source_id"]) for item in collect_items(setup)
    }
    if (source_kind, int(source_id)) not in counted:
        raise ValidationError("That work is not counted in this gradebook.")
    if student.pk not in _roster_ids(setup):
        raise ValidationError("That pupil is not in this class.")
    reason = _clean_reason(reason)

    excuse, created = GradebookExcuse.objects.get_or_create(
        setup=setup,
        student=student,
        source_kind=source_kind,
        source_id=int(source_id),
        defaults={"reason": reason, "created_by": actor},
    )
    if not created:
        raise ValidationError("This pupil is already excused from that work.")

    log_action(
        actor=actor,
        action="gradebook.excuse_recorded",
        content_type=EXCUSE_CONTENT_TYPE,
        object_id=excuse.pk,
        change_summary={
            "setup": setup.pk,
            "student": str(student),
            "source_kind": source_kind,
            "source_id": int(source_id),
            "reason": reason,
        },
        request=request,
    )
    return excuse


@transaction.atomic
def revoke_excuse(*, excuse: GradebookExcuse, actor=None, request=None) -> None:
    ensure_writable(excuse.setup)
    summary = {
        "setup": excuse.setup_id,
        "student": str(excuse.student),
        "source_kind": excuse.source_kind,
        "source_id": excuse.source_id,
        "reason": excuse.reason,
    }
    excuse_id = excuse.pk
    excuse.delete()
    log_action(
        actor=actor,
        action="gradebook.excuse_revoked",
        content_type=EXCUSE_CONTENT_TYPE,
        object_id=excuse_id,
        change_summary=summary,
        request=request,
    )


# ---------------------------------------------------------------------------
# Releases and the admin's term lock
# ---------------------------------------------------------------------------


def latest_release(setup: GradebookSetup) -> GradebookRelease | None:
    """The version families currently see: the newest one."""
    return setup.releases.select_related("released_by").first()


@transaction.atomic
def release_gradebook(
    *,
    setup: GradebookSetup,
    reason: str = "",
    actor=None,
    request=None,
) -> GradebookRelease:
    """
    Freeze the grid into the next numbered release.

    The first release needs no reason; a re-release does, because it moves a
    mark a family may already have seen. Every release keeps its own weights and
    one result row per pupil, so an older version can always be reconstructed.
    """
    ensure_writable(setup)
    grid = build_grid(setup)
    if not grid["has_items"]:
        raise ValidationError(NO_WORK_MESSAGE)

    previous = setup.releases.order_by("-version").first()
    reason = (reason or "").strip()
    if previous is not None and not reason:
        raise ValidationError(RERELEASE_REASON_MESSAGE)

    version = 1 if previous is None else previous.version + 1
    weights = {
        "homework": setup.homework_weight,
        "quiz": setup.quiz_weight,
        "lab": setup.lab_weight,
        "exam": setup.exam_weight,
    }
    release = GradebookRelease.objects.create(
        setup=setup,
        version=version,
        weights=weights,
        released_by=actor,
        reason=reason,
    )
    GradebookResult.objects.bulk_create(
        [
            GradebookResult(
                release=release,
                student=row["student"],
                overall_pct=row["overall_pct"],
                bucket_pcts={
                    key: float(value) for key, value in row["bucket_pcts"].items()
                },
                missing_count=row["missing_count"],
                excused_count=row["excused_count"],
                scored_count=row["scored_count"],
            )
            for row in grid["rows"]
        ]
    )

    log_action(
        actor=actor,
        action="gradebook.released",
        content_type=RELEASE_CONTENT_TYPE,
        object_id=release.pk,
        change_summary={
            "setup": setup.pk,
            "class": setup.school_class.name,
            "subject": setup.subject.code,
            "term": setup.term.name,
            "version": version,
            "previous_version": previous.version if previous else None,
            "reason": reason,
            "weights": weights,
            "pupils": len(grid["rows"]),
            "class_average": (
                None if grid["class_average"] is None else str(grid["class_average"])
            ),
        },
        request=request,
    )
    return release


@transaction.atomic
def set_term_lock(
    *,
    term,
    locked: bool,
    reason: str = "",
    actor=None,
    request=None,
) -> GradebookTermLock | None:
    """
    Lock or unlock one term's gradebook for the whole school.

    The lock touches the gradebook only: hand-recorded marks, weights, ticks,
    releases and the copy-to-report-card action. Result sheets keep their own
    confirmed/published locks. Unlocking a term that was never locked is a no-op.
    """
    lock = GradebookTermLock.objects.filter(term=term).first()
    previous = None if lock is None else lock.is_locked
    if previous == locked:
        return lock
    if lock is None:
        if not locked:
            return None
        lock = GradebookTermLock(term=term)

    lock.is_locked = locked
    if locked:
        lock.locked_by = actor
        lock.locked_at = timezone.now()
    lock.reason = (reason or "").strip()
    lock.save()

    log_action(
        actor=actor,
        action="gradebook.term_locked" if locked else "gradebook.term_unlocked",
        content_type=LOCK_CONTENT_TYPE,
        object_id=lock.pk,
        change_summary={
            "term": term.name,
            "academic_year": term.academic_year.name,
            "from": previous,
            "to": locked,
            "reason": lock.reason,
        },
        request=request,
    )
    return lock


@transaction.atomic
def send_to_report_card(*, setup: GradebookSetup, actor=None, request=None) -> dict:
    """
    Copy each pupil's released weighted mark onto the nominated exam sheet.

    This is the one place the gradebook writes into Results, and it writes
    through Results' own ``upsert_score_entry`` so the grade, remark and points
    are resolved by the same scale as every other mark. Only a Draft sheet is
    written: a submitted, confirmed or published sheet has its own lock, and
    the administrator reopens it with a reason before this can run.
    """
    from apps.results import services as results_services
    from apps.results.models import ResultSheet

    ensure_writable(setup)
    release = latest_release(setup)
    if release is None:
        raise ValidationError(RELEASE_REQUIRED_MESSAGE)

    sheet = setup.exam_sheet
    if sheet is None:
        raise ValidationError(SHEET_REQUIRED_MESSAGE)
    if (setup.stream_id or None) != (sheet.stream_id or None):
        raise ValidationError(STREAM_MISMATCH_MESSAGE)
    if sheet.status != ResultSheet.Status.DRAFT:
        raise ValidationError(
            SHEET_NOT_DRAFT_MESSAGE.format(
                status=sheet.get_status_display().lower()
            )
        )

    roster_ids = _roster_ids(setup)
    written = skipped = 0
    for result in release.results.select_related("student"):
        if result.student_id not in roster_ids:
            continue
        if result.overall_pct is None:
            skipped += 1
            continue
        results_services.upsert_score_entry(
            sheet=sheet,
            student=result.student,
            raw_score=result.overall_pct,
            actor=actor,
            request=request,
        )
        written += 1
    if written == 0:
        raise ValidationError(NOTHING_TO_SEND_MESSAGE)

    log_action(
        actor=actor,
        action="gradebook.sent_to_report_card",
        content_type=REPORT_CARD_CONTENT_TYPE,
        object_id=sheet.pk,
        change_summary={
            "setup": setup.pk,
            "class": setup.school_class.name,
            "subject": setup.subject.code,
            "term": setup.term.name,
            "version": release.version,
            "sheet": sheet.pk,
            "written": written,
            "skipped": skipped,
        },
        request=request,
    )
    return {
        "written": written,
        "skipped": skipped,
        "release": release,
        "sheet": sheet,
    }
