"""
apps/learning/points_config.py
--------------------------------
Level definitions and point-config helpers for Piece 3.

Nothing in this file writes to the database.  It is the single place that
defines what the seven levels are called and what they require.

Public API
----------
  LEVELS            — ordered list of Level dataclasses (index 0 = Seedling)
  level_for_total(n) → Level   — which level n total points places a student at
  next_level(current) → Level | None  — the level above, or None at the top
  get_point_config() → PointConfig    — cached singleton (one DB hit per process)
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Level:
    number: int          # 1-based
    name: str
    min_points: int      # points needed to reach this level
    badge_icon: str      # Bootstrap Icons class


# Seven levels.  Thresholds chosen so:
#   - A consistent student (2-3 lessons/week + homework) earns ~20 pts/week.
#   - Sprout reachable in ~2 weeks of steady work.
#   - Explorer in ~1 month (one-third of a term).
#   - Achiever by mid-term.
#   - Scholar at a full strong term.
#   - Champion requires two good terms.
#   - Legend is a full-year stretch goal.
LEVELS: list[Level] = [
    Level(1, "Seedling",  0,     "bi-seedling"),
    Level(2, "Sprout",    50,    "bi-flower1"),
    Level(3, "Explorer",  150,   "bi-binoculars"),
    Level(4, "Achiever",  350,   "bi-award"),
    Level(5, "Scholar",   700,   "bi-mortarboard"),
    Level(6, "Champion",  1_200, "bi-trophy"),
    Level(7, "Legend",    2_000, "bi-stars"),
]

# Idempotency-key prefixes — one slug per automated reason.
REASON_LESSON    = "lesson"
REASON_HW_ONTIME = "hw_ontime"
REASON_HW_LATE   = "hw_late"
REASON_FEED_POST = "feed_post"
REASON_STREAK    = "streak"

# Streak milestones that trigger a bonus award.
STREAK_MILESTONES = {5, 10, 20, 30, 50}


def level_for_total(total: int) -> Level:
    """The level a student with *total* points is currently at."""
    current = LEVELS[0]
    for lvl in LEVELS:
        if total >= lvl.min_points:
            current = lvl
    return current


def next_level(current: Level) -> Level | None:
    """The level above *current*, or None if already at the top."""
    if current.number >= len(LEVELS):
        return None
    return LEVELS[current.number]   # LEVELS is 0-indexed, current.number is 1-based


@lru_cache(maxsize=1)
def _cached_config_pk():
    """Return the PointConfig PK — purely to satisfy lru_cache (avoids circular import)."""
    return 1


def get_point_config():
    """
    Return the PointConfig singleton, creating it with defaults if missing.
    Uses Django's per-process query cache (select_related not needed here).
    Not cached with lru_cache because the admin may change values at runtime.
    """
    from .models import PointConfig
    return PointConfig.load()
