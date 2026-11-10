"""
apps/learning/engagement_services.py
--------------------------------------
Write-side logic for the engagement layer (Piece 1).

Two public functions:
  record_lesson_completion(student, lesson)
      Called by the lesson-view when a student marks a lesson done.
      Does nothing when engagement is off for the school or the class.
      Updates LessonProgress (already exists), then refreshes the streak.

  refresh_streak(student, today, terms, grace_days)
      Pure streak state-machine.  Separated from record_lesson_completion
      so tests can call it directly with any date.

Design rules:
  - Never decrease points or punish.  current_streak only resets to 0,
    never goes negative.  longest_streak only ever goes up.
  - streak_broken_at is set on reset so templates can show an encouraging
    "welcome back" message instead of a guilt message.
  - The whole operation is wrapped in a single transaction (via
    ATOMIC_REQUESTS=True on every request, and explicitly here for
    background/management use).
  - No notification is fired from this module; Piece 3 (points & badges)
    will hook in here.
"""

from __future__ import annotations

import datetime
import logging

from django.db import transaction
from django.utils import timezone

from apps.core.features import feature_enabled

from .models import ClassEngagementSettings, LearningStreak, LessonProgress
from .streak_utils import streak_is_alive

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@transaction.atomic
def record_lesson_completion(*, student, lesson) -> bool:
    """
    Mark *lesson* as completed for *student* and update their streak.

    Returns True when the engagement layer was active and the streak was
    updated; False when engagement is off (no-op).  The LessonProgress row
    is updated either way — engagement being off never affects whether a
    lesson is recorded as completed.
    """
    # 1. Update (or create) the LessonProgress row — this always happens.
    now = timezone.now()
    progress, _ = LessonProgress.objects.get_or_create(
        student=student,
        lesson=lesson,
        defaults={"completed": True, "last_viewed_at": now},
    )
    if not progress.completed or progress.last_viewed_at != now:
        progress.completed = True
        progress.last_viewed_at = now
        progress.save(update_fields=["completed", "last_viewed_at", "updated_at"])

    # 2. Bail early if engagement is switched off school-wide.
    if not feature_enabled("engagement"):
        return False

    # 3. Check per-class engagement settings.
    school_class = student.current_class
    if school_class is None:
        return False

    try:
        eng = school_class.engagement_settings
    except ClassEngagementSettings.DoesNotExist:
        return False

    if not eng.is_active:
        return False

    # 4. Fetch term dates for the school-day calendar.
    from apps.academics.selectors import get_current_term
    term = get_current_term()
    if term is None:
        return False

    all_terms = list(
        term.academic_year.terms.only("start_date", "end_date")
    )

    today = timezone.localdate()
    streak = refresh_streak(
        student=student,
        today=today,
        terms=all_terms,
        grace_days=eng.streak_grace_days,
    )

    # ── Piece 3: award points for lesson completion ──────────────────────
    # Import lazily to avoid circular imports at module load time.
    try:
        from .points_config import REASON_LESSON, get_point_config
        from .points_services import award_points, maybe_award_streak_bonus

        cfg = get_point_config()
        award_points(
            student=student,
            reason=REASON_LESSON,
            points=cfg.pts_lesson_complete,
            description=f"Completed lesson: {lesson.title}",
            idempotency_key=f"{REASON_LESSON}:{lesson.pk}:{student.pk}",
        )

        # Streak milestone bonus
        maybe_award_streak_bonus(
            student=student,
            current_streak=streak.current_streak,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Points award failed for student %s lesson %s", student.pk, lesson.pk
        )

    return True


@transaction.atomic
def refresh_streak(
    *,
    student,
    today: datetime.date,
    terms: list,
    grace_days: int,
) -> LearningStreak:
    """
    Recalculate and save the student's streak for *today*.

    State machine:
      - No existing row   → create with current_streak=1, last_active=today.
      - Already active today → no change (idempotent).
      - Streak alive (within grace window) → increment current_streak.
      - Streak broken → reset current_streak to 1 (fresh start, not zero),
                         set streak_broken_at to encourage "welcome back".

    ``longest_streak`` is updated whenever current_streak exceeds it.
    """
    streak, created = LearningStreak.objects.select_for_update().get_or_create(
        student=student,
        defaults={
            "current_streak": 1,
            "longest_streak": 1,
            "last_active_date": today,
            "streak_broken_at": None,
        },
    )

    if created:
        return streak

    # Idempotent: if already active today, nothing to do.
    if streak.last_active_date == today:
        return streak

    alive = streak_is_alive(
        last_active=streak.last_active_date,
        today=today,
        grace_days=grace_days,
        terms=terms,
    )

    if alive:
        streak.current_streak += 1
        streak.streak_broken_at = None
    else:
        # Streak broken — reset warmly (1 means they've already done today)
        streak.streak_broken_at = today
        streak.current_streak = 1

    streak.last_active_date = today
    if streak.current_streak > streak.longest_streak:
        streak.longest_streak = streak.current_streak

    streak.save(
        update_fields=[
            "current_streak",
            "longest_streak",
            "last_active_date",
            "streak_broken_at",
            "updated_at",
        ]
    )
    return streak
