"""
apps/core/notifications.py
--------------------------
Reusable in-app notification helper. Call from any service.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.accounts.models import User


def notify(
    *,
    recipient: User,
    title: str,
    message: str = "",
    link: str = "",
    category: str = "",
) -> None:
    """Create a single in-app notification for ``recipient``."""
    from .models import Notification

    Notification.objects.create(
        recipient=recipient,
        title=title,
        message=message,
        link=link,
        category=category,
    )


def notify_many(
    *,
    recipients: Iterable[User],
    title: str,
    message: str = "",
    link: str = "",
    category: str = "",
) -> int:
    """Bulk-create notifications. Returns count created."""
    from .models import Notification

    objs = [
        Notification(
            recipient=user,
            title=title,
            message=message,
            link=link,
            category=category,
        )
        for user in recipients
    ]
    if not objs:
        return 0
    Notification.objects.bulk_create(objs)
    return len(objs)
