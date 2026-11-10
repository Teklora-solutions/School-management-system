"""
apps/learning/streak_utils.py
------------------------------
School-day counting logic for the fair streak algorithm.

Design goals (from the product spec):
  - Weekends (Saturday, Sunday) never count against a streak.
  - Days outside any term (holidays, school breaks) never count.
  - A configurable grace allowance (default: 1 school day) lets a student
    miss one school day without losing their streak — covering power cuts,
    shared phones, and the occasional bad day.
  - Wording is encouraging, never guilt-based.  This module only does the
    arithmetic; the templates decide the words.

Public API
----------
  is_school_day(date, terms)            → bool
  school_days_between(start, end, terms) → int   (exclusive of start, inclusive of end)
  streak_is_alive(last_active, today, grace_days, terms) → bool
  days_since_last_active(last_active, today, terms) → int

All functions are pure (no DB access) so they are fast and trivially testable.
``terms`` is always a list/queryset of Term objects that have ``start_date``
and ``end_date`` date attributes — callers fetch them once and pass them in.
"""

from __future__ import annotations

import datetime
from typing import Protocol


class _TermLike(Protocol):
    start_date: datetime.date
    end_date: datetime.date


def is_school_day(date: datetime.date, terms: list[_TermLike]) -> bool:
    """
    True when *date* is a weekday (Mon–Fri) that falls within at least one term.

    Weekends return False regardless of terms.
    A date between terms (e.g. the August holiday) returns False.
    """
    if date.weekday() >= 5:          # 5 = Saturday, 6 = Sunday
        return False
    return any(t.start_date <= date <= t.end_date for t in terms)


def school_days_between(
    start: datetime.date,
    end: datetime.date,
    terms: list[_TermLike],
) -> int:
    """
    Count school days strictly after *start* and up to and including *end*.

    Returns 0 if ``end <= start``.  The result is the number of school days
    the student was *expected* to be active between the two dates.

    Used by ``streak_is_alive`` to decide whether too many school days have
    been missed.
    """
    if end <= start:
        return 0
    count = 0
    current = start + datetime.timedelta(days=1)
    while current <= end:
        if is_school_day(current, terms):
            count += 1
        current += datetime.timedelta(days=1)
    return count


def days_since_last_active(
    last_active: datetime.date,
    today: datetime.date,
    terms: list[_TermLike],
) -> int:
    """
    How many school days have passed since the student was last active,
    not counting *last_active* itself.

    If ``today == last_active`` this is 0 — the student was active today.
    If the gap spans a weekend or holiday, those days are invisible.
    """
    return school_days_between(last_active, today, terms)


def streak_is_alive(
    last_active: datetime.date | None,
    today: datetime.date,
    grace_days: int,
    terms: list[_TermLike],
) -> bool:
    """
    Whether the student's streak is still alive as of *today*.

    Rules:
      - No previous activity → streak is not alive (count is 0, not broken).
      - Active today → alive.
      - Missed school days since last active ≤ grace_days → alive.
      - Missed school days > grace_days → broken.

    ``grace_days=1`` means one school day can be skipped without penalty.
    ``grace_days=0`` means every school day must have at least one completion.

    The function never looks at calendar days directly — only school days —
    so a three-day weekend with grace_days=1 is always safe.
    """
    if last_active is None:
        return False
    if last_active == today:
        return True
    missed = school_days_between(last_active, today, terms)
    # missed == 0 means no school days fell between last_active and today
    # (e.g. they were consecutive days both outside any term, which we treat
    # as zero gap — the streak was just paused, not broken).
    if missed == 0:
        return True
    # Subtract 1 because the *first* school day after last_active is the
    # day they "should" have been active.  If that's today, missed=1 and
    # they haven't actually skipped any day yet.
    return (missed - 1) <= grace_days
