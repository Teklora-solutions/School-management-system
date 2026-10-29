"""Template helpers shared across app templates."""

from django import template

from apps.core.money import format_money, to_money

register = template.Library()


@register.filter
def dict_get(mapping, key):
    """Return ``mapping[key]`` (empty string when the key is absent)."""
    if not mapping:
        return ""
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""


@register.filter
def money(value, currency=None):
    """
    Render an amount as ``GHS 1,234.50``.

    One renderer for every money figure in the project, so a statement, a
    receipt and a dashboard cannot print different shapes of the same number.
    The sign is always shown: a credit is drawn in red by CSS, and red does not
    survive a photocopy, a fax or a screen reader.

    ``{{ row.amount|money:currency }}`` in a table uses the code the view or the
    context processor already put in the page; ``{{ total|money }}`` alone reads
    it from SchoolSettings, which is one query per figure and so right for a
    headline and wrong for a hundred rows.
    """
    code = (str(currency or "").strip() or None)
    return format_money(to_money(value), currency=code)


@register.filter
def money_digits(value):
    """
    Render an amount as ``1,234.50`` with no currency prefix.

    For a table whose column header already names the currency — repeating
    ``GHS`` on every row of a fee table makes the column twice as wide to read
    the same information.
    """
    return format_money(to_money(value), with_currency=False)
