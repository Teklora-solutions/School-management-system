"""
apps/learning/points_services.py
----------------------------------
Write-side logic for the points and levels system (Piece 3).

Public API
----------
  award_points(student, reason, points, description, idempotency_key,
               awarded_by=None) -> PointLedger | None
      The single entry point for all automatic awards.  Returns None if the
      idempotency key already exists (duplicate call silently ignored).
      Calls check_level_up() after every successful award.

  reverse_award(ledger_row, reversed_by, reason_text) -> PointLedger
      Write a negative reversal row against an existing award.  Raises
      ValueError if the row has already been reversed.

  teacher_bonus(student, awarded_by, points, description) -> PointLedger
      Manual bonus with a required description.  No idempotency key (each
      bonus is intentional and unique).  Capped at 1–20 points per award.

  check_level_up(student) -> bool
      Recalculate the student's total from the ledger, update StudentLevel,
      and fire a notification + celebration flag if the level rose.
      Returns True when the student levelled up, False otherwise.

Design rules (all from spec):
  - Points never go down (reversals leave total non-negative by clamping to 0).
  - No double-awarding: idempotency_key unique constraint prevents it at the DB level.
  - Daily feed cap: feed_post awards are refused if the student already hit
    daily_feed_cap today (calculated from today's feed_post ledger rows).
  - Streak milestone: bonus fires once per milestone number (idempotency key
    includes the milestone value).
  - Level-up notification fires once per level via notified_level guard.
  - All writes are @transaction.atomic.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.notifications import notify

from .models import PointLedger, StudentLevel
from .points_config import (
    LEVELS,
    REASON_FEED_POST,
    level_for_total,
    next_level,
)

logger = logging.getLogger(__name__)

MAX_TEACHER_BONUS = 20
MIN_TEACHER_BONUS = 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _engagement_active(student) -> bool:
    """True when the school and per-class engagement switches are both on."""
    from apps.core.features import feature_enabled
    if not feature_enabled("engagement"):
        return False
    school_class = getattr(student, "current_class", None)
    if school_class is None:
        return False
    try:
        eng = school_class.engagement_settings
        return eng.is_active
    except Exception:  # noqa: BLE001
        return False


def _daily_feed_total(student, today) -> int:
    """
    Net feed_post points for *student* on *today*.

    Gross: SUM of all FEED_POST award rows created today.
    Clawback: SUM of REVERSAL rows whose reversed original was a FEED_POST row
    created today.  The clawback rows have negative points, so adding them
    reduces the total — restoring the student's cap credit the same day a post
    is removed.

    Only FEED_POST awards and their direct reversals are counted; a reversal of
    any other reason (lesson, homework, etc.) never affects the feed cap.
    """
    day_start = timezone.datetime(today.year, today.month, today.day,
                                  tzinfo=timezone.get_current_timezone())
    day_end = day_start + timezone.timedelta(days=1)

    # 1. Gross: today's FEED_POST awards
    today_feed_ids = list(
        PointLedger.objects.filter(
            student=student,
            reason=PointLedger.Reason.FEED_POST,
            created_at__gte=day_start,
            created_at__lt=day_end,
        ).values_list("id", flat=True)
    )
    if not today_feed_ids:
        return 0

    gross = (
        PointLedger.objects.filter(id__in=today_feed_ids)
        .aggregate(total=Sum("points"))["total"]
        or 0
    )

    # 2. Clawback: reversals whose original was one of today's FEED_POST rows
    clawback = (
        PointLedger.objects.filter(reversal_of_id__in=today_feed_ids)
        .aggregate(total=Sum("points"))["total"]
        or 0
    )

    return max(0, gross + clawback)  # clawback rows hold negative values


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@transaction.atomic
def award_points(
    *,
    student,
    reason: str,
    points: int,
    description: str,
    idempotency_key: str,
    awarded_by=None,
) -> "PointLedger | None":
    """
    Award *points* to *student* for *reason*.

    Returns the new PointLedger row, or None when:
      - Engagement is off for this student's class.
      - The idempotency key already exists (silent no-op — not an error).
      - Daily feed cap reached for feed_post awards.
    """
    if not _engagement_active(student):
        return None

    if points <= 0:
        return None

    # Daily feed cap
    if reason == REASON_FEED_POST:
        from .points_config import get_point_config
        cfg = get_point_config()
        today = timezone.localdate()
        if _daily_feed_total(student, today) >= cfg.daily_feed_cap:
            return None

    try:
        row = PointLedger.objects.create(
            student=student,
            points=points,
            reason=reason,
            description=description,
            awarded_by=awarded_by,
            idempotency_key=idempotency_key,
        )
    except IntegrityError:
        # Duplicate idempotency key — safe to ignore
        return None

    check_level_up(student)
    return row


@transaction.atomic
def reverse_award(
    *,
    ledger_row: "PointLedger",
    reversed_by,
    reason_text: str = "",
) -> "PointLedger":
    """
    Write a negative reversal row against *ledger_row*.

    Raises ValueError when:
      - The row is already a reversal (can't reverse a reversal).
      - The row has already been reversed.
    """
    if ledger_row.points < 0:
        raise ValueError("Cannot reverse a reversal row.")
    if hasattr(ledger_row, "reversed_by") and ledger_row.reversed_by is not None:
        raise ValueError("This award has already been reversed.")

    # Clamp reversal so total never goes below zero.
    total = _ledger_total(ledger_row.student)
    reversal_pts = -min(ledger_row.points, max(total, 0))

    row = PointLedger.objects.create(
        student=ledger_row.student,
        points=reversal_pts,
        reason=PointLedger.Reason.REVERSAL,
        description=reason_text or f"Reversal of award #{ledger_row.pk}",
        awarded_by=reversed_by,
        reversal_of=ledger_row,
        idempotency_key="",   # manual rows have no idempotency key
    )
    check_level_up(ledger_row.student)
    return row


@transaction.atomic
def teacher_bonus(
    *,
    student,
    awarded_by,
    points: int,
    description: str,
) -> "PointLedger":
    """
    Manual bonus award by a teacher.

    ``description`` is required (max 255 chars).
    ``points`` is clamped to 1–20.
    """
    if not description or not description.strip():
        raise ValueError("A description is required for a bonus award.")
    points = max(MIN_TEACHER_BONUS, min(MAX_TEACHER_BONUS, int(points)))

    row = PointLedger.objects.create(
        student=student,
        points=points,
        reason=PointLedger.Reason.BONUS,
        description=description[:255].strip(),
        awarded_by=awarded_by,
        idempotency_key="",   # each bonus is unique
    )
    check_level_up(student)
    return row


def _ledger_total(student) -> int:
    """Current total from the ledger (always recalculated, never cached)."""
    result = (
        PointLedger.objects.filter(student=student)
        .aggregate(total=Sum("points"))["total"]
    )
    return max(0, result or 0)


@transaction.atomic
def check_level_up(student) -> bool:
    """
    Recalculate total, update StudentLevel, fire notification if level rose.
    Returns True when the student moved to a new level.
    """
    total = _ledger_total(student)
    new_level = level_for_total(total)

    sl, created = StudentLevel.objects.select_for_update().get_or_create(
        student=student,
        defaults={
            "level_number": 1,          # always start at 1; update below
            "level_name": LEVELS[0].name,
            "total_points": total,
            "notified_level": 0,
        },
    )

    old_level_number = sl.level_number
    levelled_up = new_level.number > old_level_number

    sl.level_number = new_level.number
    sl.level_name = new_level.name
    sl.total_points = total
    sl.save(update_fields=["level_number", "level_name", "total_points", "updated_at"])

    # Fire level-up notification once per level
    if levelled_up and new_level.number > sl.notified_level:
        _fire_level_up_notification(student, new_level)
        sl.notified_level = new_level.number
        sl.save(update_fields=["notified_level", "updated_at"])

    return levelled_up


def _fire_level_up_notification(student, level) -> None:
    """Send an in-app notification to the student on level-up."""
    nxt = next_level(level)
    pts_to_next = (nxt.min_points - level.min_points) if nxt else 0
    try:
        notify(
            recipient=student.user,
            title=f"You reached Level {level.number}: {level.name}! 🎉",
            message=(
                f"Great work — you're now a {level.name}. "
                + (f"Keep going: {pts_to_next} more points to reach {nxt.name}."
                   if nxt else "You've reached the top level. Outstanding!")
            ),
            link="/learning/my-badges/",
            category="level_up",
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to send level-up notification for student %s", student.pk)


# ---------------------------------------------------------------------------
# Streak milestone bonus
# ---------------------------------------------------------------------------


def maybe_award_streak_bonus(*, student, current_streak: int) -> None:
    """
    Award the streak milestone bonus when *current_streak* hits a milestone.
    Called from engagement_services after streak refresh.
    """
    from .points_config import STREAK_MILESTONES, get_point_config
    if current_streak not in STREAK_MILESTONES:
        return
    cfg = get_point_config()
    idempotency_key = f"streak:{student.pk}:{current_streak}"
    award_points(
        student=student,
        reason=PointLedger.Reason.STREAK,
        points=cfg.pts_streak_milestone,
        description=f"{current_streak}-day streak milestone!",
        idempotency_key=idempotency_key,
    )
