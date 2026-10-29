"""
apps/core/services.py
---------------------
Write-side operations for core models. All mutations are transactional and
audit-logged; reads live in ``apps.core.selectors``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models, transaction

from .audit_log import log_action
from .models import SchoolSettings

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.accounts.models import User


def _audit_value(value: Any) -> Any:
    """
    A value as the audit log can store it: the JSON column keeps primitives, and
    a date, a Decimal or a file are objects.

    A file is recorded by name, which is what actually changed — writing the
    bytes into the log would turn a name change into a second copy of the upload.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name or None
    return str(value)


def apply_cleaned_data(
    *,
    instance,
    stored,
    cleaned_data: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """
    Copy a bound form's cleaned data onto its instance and report what moved.

    The return value is the audit-ready diff — ``{field: {"from": …, "to": …}}``
    for the fields whose value actually changed. ``stored`` must be the row as
    it is in the table, because a bound ModelForm has already overwritten
    ``instance`` by the time a view gets to ``form_valid``. Pass ``None`` when
    there is no stored row yet (a create) and the diff comes back empty.

    A file field is the one exception to "write what the form posted". Django
    reads a posted ``None`` on a file input as *nobody picked a file*, not *clear
    the one we have* — see ``FileField.save_form_data`` — and a browser posts
    nothing at all for an untouched file input. Skipping it here is what stops
    saving the school's name from deleting its crest, and it applies to every
    model form that carries an image, so it lives in one function.
    """
    files = {
        field.name for field in instance._meta.fields if isinstance(field, models.FileField)
    }
    changes: dict[str, dict[str, Any]] = {}
    for field, value in cleaned_data.items():
        if value is None and field in files:
            continue
        setattr(instance, field, value)
        if stored is None:
            continue
        before, after = _audit_value(getattr(stored, field)), _audit_value(value)
        if before != after:
            changes[field] = {"from": before, "to": after}
    return changes


@transaction.atomic
def update_school_settings(
    *,
    instance: SchoolSettings,
    cleaned_data: dict[str, Any],
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> SchoolSettings:
    """
    Persist the SchoolSettings singleton and audit only the fields that
    actually changed, storing the before/after pair for each.
    """
    stored = SchoolSettings.objects.filter(pk=instance.pk).first()
    changes = apply_cleaned_data(
        instance=instance, stored=stored, cleaned_data=cleaned_data
    )
    instance.save()

    if changes:
        log_action(
            actor=actor,
            action="core.school_settings_updated",
            content_type="core.SchoolSettings",
            object_id=instance.pk,
            change_summary=changes,
            request=request,
        )
    return instance
