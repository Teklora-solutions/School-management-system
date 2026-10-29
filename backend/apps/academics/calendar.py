"""
apps/academics/calendar.py
--------------------------
The one definition of what an academic year is made of: which years the admin
may pick, the dates each generates, and the three term windows inside it.

Kept out of the models and services so the add-year form, the demo seeder and
the legacy-name check all read the same rules instead of each carrying their
own copy of "September to July".
"""

from __future__ import annotations

import datetime
import re

from django.utils import timezone

#: The owner picks years from the running one up to here, and no further.
TERMINAL_START_YEAR = 2030

#: A school year belongs to the September it opens in, even in January.
YEAR_OPENS_MONTH = 9

#: Term slots in the order they run. Mirrors ``Term.TermName``; a test pins the
#: two together so they cannot drift.
TERM_SLOTS: tuple[str, ...] = ("term_1", "term_2", "term_3")

# Default windows, measured from the year's start year: (slot, opens, closes).
# A month before September belongs to the following calendar year.
_TERM_WINDOWS = (
    ("term_1", (9, 1), (12, 20)),
    ("term_2", (1, 11), (4, 10)),
    ("term_3", (5, 10), (7, 31)),
)

_NAME_PATTERN = re.compile(r"(\d{4})/(\d{4})")


def current_start_year(today: datetime.date | None = None) -> int:
    """The academic year a school is actually in on ``today``."""
    today = today or timezone.localdate()
    return today.year if today.month >= YEAR_OPENS_MONTH else today.year - 1


def selectable_start_years(today: datetime.date | None = None) -> list[int]:
    return list(range(current_start_year(today), TERMINAL_START_YEAR + 1))


def year_name(start_year: int) -> str:
    """``2026`` becomes the label ``"2026/2027"``."""
    return f"{start_year}/{start_year + 1}"


def year_dates(start_year: int) -> tuple[datetime.date, datetime.date]:
    """Derived from the term windows so the year can never outlive its own terms."""
    windows = term_windows(start_year)
    return (windows[0][1], windows[-1][2])


def _dated(start_year: int, month: int, day: int) -> datetime.date:
    year = start_year if month >= YEAR_OPENS_MONTH else start_year + 1
    return datetime.date(year, month, day)


def term_windows(start_year: int) -> list[tuple[str, datetime.date, datetime.date]]:
    """The three terms a year is expected to contain, in order."""
    return [
        (slot, _dated(start_year, *opens), _dated(start_year, *closes))
        for slot, opens, closes in _TERM_WINDOWS
    ]


def slot_running_on(start_year: int, today: datetime.date) -> str | None:
    """Which term window ``today`` falls inside, for a year starting ``start_year``.

    ``None`` when today is in a holiday between years, so a freshly created year
    does not steal the current-term flag for no reason.
    """
    for slot, opening, closing in term_windows(start_year):
        if opening <= today <= closing:
            return slot
    return None


def parse_start_year(name: str) -> int | None:
    """The start year of a picker-shaped name like ``"2026/2027"``, else ``None``.

    Used to flag rows created before the dropdown existed ("1000", "3958") so the
    admin can spot and retire them, and nothing else.
    """
    match = _NAME_PATTERN.fullmatch((name or "").strip())
    if not match:
        return None
    first, second = int(match[1]), int(match[2])
    return first if second == first + 1 else None
