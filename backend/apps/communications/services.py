"""
apps/communications/services.py
---------------------------------
Write-side logic for the communications module.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.core.models import Notification

from .models import Announcement, MessageThread, ThreadMessage

logger = logging.getLogger(__name__)


def create_message_thread(
    *,
    sender,
    message_type: str,
    subject: str,
    body: str,
    attachment=None,
    on_behalf_of=None,
) -> MessageThread:
    """Create a new thread with the opening message."""
    thread = MessageThread.objects.create(
        sender=sender,
        message_type=message_type,
        subject=subject,
        status=MessageThread.Status.OPEN,
        on_behalf_of=on_behalf_of,
        last_reply_at=timezone.now(),
    )
    ThreadMessage.objects.create(
        thread=thread,
        author=sender,
        body=body,
        attachment=attachment,
        is_opening=True,
    )

    # Notify all admin/director users
    from apps.accounts.models import User

    admins = User.objects.filter(role__in=["admin", "director"], is_active=True)
    type_label = thread.get_message_type_display()
    for admin in admins:
        Notification.objects.create(
            recipient=admin,
            title=f"New {type_label}: {subject[:80]}",
            message=f"From {sender.get_full_name() or sender.username}",
            link=f"/communications/inbox/{thread.pk}/",
            category="message",
        )
    return thread


def reply_to_thread(
    *,
    thread: MessageThread,
    author,
    body: str,
    attachment=None,
    new_status: str | None = None,
) -> ThreadMessage:
    """Add a reply message and optionally advance status."""
    msg = ThreadMessage.objects.create(
        thread=thread,
        author=author,
        body=body,
        attachment=attachment,
        is_opening=False,
    )
    thread.last_reply_at = timezone.now()
    if new_status and new_status != thread.status:
        thread.status = new_status
    thread.save(update_fields=["last_reply_at", "status"])

    # Notify the original sender if a staff member replied
    is_staff_reply = author.role in ("admin", "director", "teacher", "finance")
    if is_staff_reply and author != thread.sender:
        Notification.objects.create(
            recipient=thread.sender,
            title=f"Reply to your {thread.get_message_type_display()}: {thread.subject[:60]}",
            message=body[:200],
            link=f"/communications/messages/{thread.pk}/",
            category="message",
        )
    return msg


def assign_thread(*, thread: MessageThread, assignee, actor) -> MessageThread:
    thread.assigned_to = assignee
    if thread.status == MessageThread.Status.OPEN:
        thread.status = MessageThread.Status.IN_PROGRESS
    thread.save(update_fields=["assigned_to", "status"])
    return thread


def update_thread_status(*, thread: MessageThread, status: str) -> MessageThread:
    thread.status = status
    thread.save(update_fields=["status"])
    return thread


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------


def create_announcement(
    *,
    title: str,
    body: str,
    audience: str,
    target_roles: list[str] | None = None,
    target_class_ids: list[int] | None = None,
    attachment=None,
    publish_at=None,
    expires_at=None,
    is_published: bool = True,
    created_by,
) -> Announcement:
    """
    Create and optionally publish an announcement.
    Sends in-app notifications to all currently active users who match
    the audience (capped at 500 to avoid large INSERT blasts).
    """

    from .models import Announcement

    ann = Announcement.objects.create(
        title=title,
        body=body,
        audience=audience,
        target_roles=target_roles or [],
        attachment=attachment,
        publish_at=publish_at,
        expires_at=expires_at,
        is_published=is_published,
        created_by=created_by,
    )

    if target_class_ids:
        from apps.academics.models import SchoolClass

        classes = SchoolClass.objects.filter(pk__in=target_class_ids)
        ann.target_classes.set(classes)

    # Send in-app notifications if the announcement is published immediately
    if is_published and publish_at is None:
        _notify_announcement_audience(ann)

    return ann


def _notify_announcement_audience(ann: Announcement) -> None:
    """Create Notification rows for matched users (max 500)."""
    from django.db.models import Q

    from apps.accounts.models import User
    from apps.core.models import Notification

    from .models import Announcement

    qs = User.objects.filter(is_active=True)

    if ann.audience == Announcement.Audience.BY_ROLE and ann.target_roles:
        qs = qs.filter(role__in=ann.target_roles)
    elif ann.audience == Announcement.Audience.BY_CLASS:
        class_ids = list(ann.target_classes.values_list("pk", flat=True))
        # Students in the selected classes plus their linked parents.
        qs = qs.filter(
            Q(student_profile__current_class_id__in=class_ids)
            | Q(parent_profile__children_links__student__current_class_id__in=class_ids)
        ).distinct()
    # EVERYONE: no extra filter

    notifs = [
        Notification(
            recipient=user,
            title=f"New announcement: {ann.title[:80]}",
            message=ann.body[:200],
            link="/communications/",
            category="announcement",
        )
        for user in qs[:500]
    ]
    try:
        Notification.objects.bulk_create(notifs, ignore_conflicts=True)
    except Exception:
        logger.exception(
            "announcement_notification_failed", extra={"announcement_id": ann.pk}
        )
