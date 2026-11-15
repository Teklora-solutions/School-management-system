from __future__ import annotations

from django.utils import timezone


class ReleaseStatus:
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    HIDDEN = "hidden"


def release_status(obj, now=None) -> str:
    if now is None:
        now = timezone.now()
    if not getattr(obj, "is_published", False):
        return ReleaseStatus.DRAFT
    publish_at = getattr(obj, "publish_at", None)
    hide_at = getattr(obj, "hide_at", None)
    if hide_at and now >= hide_at:
        return ReleaseStatus.HIDDEN
    if publish_at and now < publish_at:
        return ReleaseStatus.SCHEDULED
    return ReleaseStatus.PUBLISHED


def is_released_at(obj, now=None) -> bool:
    """True when students may currently see this item (published + in window)."""
    return release_status(obj, now=now) == ReleaseStatus.PUBLISHED


def status_label(obj, now=None) -> str:
    """Human-friendly label for teacher screens."""
    return {
        ReleaseStatus.DRAFT: "Draft",
        ReleaseStatus.SCHEDULED: "Scheduled",
        ReleaseStatus.PUBLISHED: "Published",
        ReleaseStatus.HIDDEN: "Hidden",
    }[release_status(obj, now=now)]
