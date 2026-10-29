"""
apps/core/money.py
------------------
The single definition of money arithmetic in this project.

Why this module exists: every figure the school prints — a receipt, a ledger
line, a Director KPI, an .xlsx export — has to round the same way, or two
screens showing the same invoice disagree by a pesewa and the first person to
notice is a parent who was quoted one number and charged another. Decimal
alone does not give you that; only one place deciding ``ROUND_HALF_UP`` and
2 dp does.

The rules this module enforces:

  - Money is always a ``Decimal`` with exactly 2 decimal places. Never a
    ``float``, in either direction: not stored, not returned to a template, not
    summed. ``float`` reappears only where a chart's JSON is built, at the view
    boundary, because Chart.js cannot read a Decimal.
  - Rounding is ``ROUND_HALF_UP``. Python's builtin ``round()`` is
    ``ROUND_HALF_EVEN`` ("banker's rounding"), which rounds 0.125 and 0.135 to
    the *same* value — a split that looks like a bug to anyone holding two
    receipts.
  - ``percent()`` returns ``None`` for "nothing to rate", never a misleading 0.

``currency_code()`` belongs here for the same reason: the currency symbol a
receipt prints and the one a report totals must come from one place —
``SchoolSettings``, with the ``settings.SCHOOL_CURRENCY`` constant as the
fallback for a school that has never saved its settings page.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")
ONE_DP = Decimal("0.1")

__all__ = [
    "CENTS",
    "ONE_DP",
    "ZERO",
    "currency_code",
    "format_money",
    "money_or_zero",
    "money_sum",
    "percent",
    "to_money",
]


def to_money(value) -> Decimal:
    """
    Quantize anything money-shaped to the school's 2 dp, ROUND_HALF_UP.

    Accepts ``Decimal``, ``int``, ``str`` (what a form posts) and ``None``/""
    (an optional field). A ``float`` is coerced through ``str`` rather than
    ``Decimal(float)``, because ``Decimal(0.1)`` is
    0.1000000000000000055511151231257827021181583404541015625 and that noise is
    what eventually shows up on a statement.

    Raises ``ValidationError`` on text that is not a number, so a bad cell in an
    uploaded spreadsheet becomes a row error, not a 500.
    """
    from django.core.exceptions import ValidationError

    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        quantized = value
    elif isinstance(value, float):
        quantized = Decimal(str(value))
    elif isinstance(value, int):
        quantized = Decimal(value)
    else:
        try:
            quantized = Decimal(str(value).strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            raise ValidationError(f"'{value}' is not a valid amount of money.") from None
    return quantized.quantize(CENTS, rounding=ROUND_HALF_UP)


# ``money_or_zero`` is a clearer name at the call sites that total a column
# whose SUM is NULL when nothing matched.
money_or_zero = to_money


def money_sum(values: Iterable, start: Decimal = ZERO) -> Decimal:
    """SUM Decimals, staying 2 dp. Summing a generator of money fields."""
    total = start
    for value in values:
        total += to_money(value)
    return total.quantize(CENTS, rounding=ROUND_HALF_UP)


def percent(part, whole, *, places: Decimal = ONE_DP):
    """
    ``part`` as a percentage of ``whole``, 1 dp, ROUND_HALF_UP.

    ``None`` when ``whole`` is zero — "we have billed nothing this term" is a
    different fact from "collection is 0%", and a screen that shows 0% for an
    unbilled term teaches a headteacher to distrust the number.
    """
    part, whole = to_money(part), to_money(whole)
    if not whole:
        return None
    return (part / whole * 100).quantize(places, rounding=ROUND_HALF_UP)


def currency_code(request=None) -> str:
    """
    The one currency this school keeps its books in.

    ``SchoolSettings.currency`` wins; the ``SCHOOL_CURRENCY`` setting is the
    fallback so a fresh install renders before anyone has saved the settings
    page (the same fallback ``core.context_processors.school_settings`` uses).

    Pass the request in a view and the read is shared with the context
    processor instead of duplicated — see
    :func:`apps.core.selectors.school_settings_values`.
    """
    from apps.core.selectors import school_settings_values

    code = (school_settings_values(request).get("currency") or "").strip()
    return code or "GHS"


def format_money(value, *, currency: str | None = None, with_currency: bool = True) -> str:
    """
    Render money for a human: ``GHS 1,234.50``, and ``GHS -1,234.50`` when it is
    negative or a credit.

    The sign is always printed. Colour is not a distinction a photocopy, a
    screen reader, or a black-and-white receipt can see, so it never carries
    this information on its own.

    ``currency`` lets a caller that already knows the code (a template context,
    an export building 5,000 cells) avoid one read of ``SchoolSettings``.
    """
    amount = to_money(value)
    text = f"{amount:,.2f}"
    if not with_currency:
        return text
    return f"{currency or currency_code()} {text}"
