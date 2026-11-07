"""
apps/core/deletion.py
---------------------
Shared wording for the guarded delete screens. Each delete path declares which
reverse relations matter, and these helpers turn them into counts an admin can
read ("3 fee invoices") instead of a model name.
"""

from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist


def count_relations(mapping: dict[str, str], obj) -> dict[str, int]:
    """``{label: n}`` for the relations in ``mapping`` that actually hold rows.

    ``mapping`` is ``{reverse accessor: human label}``, kept next to the delete
    path that uses it so the vocabulary stays with the decision it describes.

    A reverse one-to-one is the odd one out: it has no empty manager to count,
    Django raises when the row is missing, and the value when it exists *is* the
    row — so it contributes one, never a queryset.
    """
    counts: dict[str, int] = {}
    for accessor, label in mapping.items():
        try:
            related = getattr(obj, accessor)
        except ObjectDoesNotExist:
            continue
        count = related.count() if hasattr(related, "count") else 1
        if count:
            counts[label] = counts.get(label, 0) + count
    return counts


def count_protected(obj, mapping: dict[str, str], protected_objects) -> dict[str, int]:
    """Group a ``ProtectedError``'s rows under the labels ``count_relations`` uses.

    The rows Django hands back are model objects, and their default
    ``verbose_name`` is a developer word ("invoice"), not the money word the admin
    was just shown on the confirm page ("fee invoice"). Resolving the label
    through the same mapping keeps the refusal and the preview saying one thing.
    Anything unexpected still falls back to its own name rather than vanishing.
    """
    by_model: dict[type, str] = {}
    for accessor, label in mapping.items():
        related = obj._meta.get_field(accessor).related_model
        if related is not None:
            by_model.setdefault(related, label)
    counts: dict[str, int] = {}
    for protected in protected_objects:
        label = by_model.get(type(protected), protected._meta.verbose_name)
        counts[label] = counts.get(label, 0) + 1
    return counts


def phrase(counts: dict[str, int], empty: str = "nothing") -> str:
    """One readable line out of ``{label: n}``."""
    return (
        ", ".join(
            f"{n} {label}{'' if n == 1 else 's'}" for label, n in counts.items()
        )
        or empty
    )
