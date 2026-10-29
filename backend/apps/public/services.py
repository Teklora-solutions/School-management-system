"""
apps/public/services.py
-----------------------
Write side of the website: the photographs the school publishes and the enquiries
the office works through. Every mutation is transactional and audit-logged;
reads live in ``apps.public.selectors``.

The photograph functions share one rule with School Settings — a form that
posts nothing for its file input means *nobody picked a file*, not *delete the
one we have* — and that rule lives in ``core.services.apply_cleaned_data`` so
it cannot be re-implemented slightly differently a second time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

from apps.core.audit_log import log_action
from apps.core.services import apply_cleaned_data

from .models import ContactMessage, GalleryPhoto

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.accounts.models import User


@transaction.atomic
def save_gallery_photo(
    *,
    instance: GalleryPhoto,
    cleaned_data: dict[str, Any],
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> GalleryPhoto:
    """
    Create or update one photograph, auditing whichever it was.

    An update logs only the fields that moved. A create logs the values the row
    was born with, because "row 7 appeared, here is what it held" is the whole
    answer to the question an audit row exists to ask.
    """
    creating = instance.pk is None
    stored = (
        None if creating else GalleryPhoto.objects.filter(pk=instance.pk).first()
    )
    changes = apply_cleaned_data(
        instance=instance, stored=stored, cleaned_data=cleaned_data
    )
    instance.full_clean()
    instance.save()

    if creating:
        log_action(
            actor=actor,
            action="public.gallery_photo_created",
            content_type="public.GalleryPhoto",
            object_id=instance.pk,
            change_summary={
                "title": instance.title,
                "image": instance.image.name,
                "display_order": instance.display_order,
                "is_published": instance.is_published,
            },
            request=request,
        )
    elif changes:
        log_action(
            actor=actor,
            action="public.gallery_photo_updated",
            content_type="public.GalleryPhoto",
            object_id=instance.pk,
            change_summary=changes,
            request=request,
        )
    return instance


@transaction.atomic
def delete_gallery_photo(
    *,
    instance: GalleryPhoto,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> None:
    """
    Remove a photograph's row.

    The uploaded file is deliberately left in ``MEDIA_ROOT``: every other delete
    in this project behaves the same way, and cleaning up orphaned files is a
    separate job with a much better way to decide what is safe to unlink.
    """
    pk, title = instance.pk, instance.title
    instance.delete()
    log_action(
        actor=actor,
        action="public.gallery_photo_deleted",
        content_type="public.GalleryPhoto",
        object_id=pk,
        change_summary={"title": title},
        request=request,
    )


@transaction.atomic
def set_enquiry_handled(
    *,
    instance: ContactMessage,
    handled: bool,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> ContactMessage:
    """
    Mark an enquiry answered, or reopen it, and stamp when.

    Ticking a box that is already ticked is not a change and writes no audit
    row — the log is a record of what happened, not of every click.
    """
    stored = ContactMessage.objects.filter(pk=instance.pk).first()
    if stored is None or stored.handled == handled:
        return instance

    instance.handled = handled
    instance.handled_at = timezone.now() if handled else None
    instance.save(update_fields=["handled", "handled_at", "updated_at"])

    log_action(
        actor=actor,
        action="public.enquiry_handled" if handled else "public.enquiry_reopened",
        content_type="public.ContactMessage",
        object_id=instance.pk,
        change_summary={"handled": {"from": stored.handled, "to": handled}},
        request=request,
    )
    return instance


@transaction.atomic
def delete_enquiry(
    *,
    instance: ContactMessage,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> None:
    """
    Remove an enquiry — a spam cleanup or a privacy request.

    The audit row keeps the sender's name and address and not the message body:
    without the first two nobody can tell later whether this was a deletion the
    school owed someone, while copying their whole letter into a second table
    would spread the personal data rather than remove it.
    """
    pk, name, email = instance.pk, instance.name, instance.email
    instance.delete()
    log_action(
        actor=actor,
        action="public.enquiry_deleted",
        content_type="public.ContactMessage",
        object_id=pk,
        change_summary={"name": name, "email": email},
        request=request,
    )
