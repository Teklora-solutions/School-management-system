"""
apps/core/context_processors.py
--------------------------------
Injects into every template:
  - base_template: the role-specific dashboard shell path
  - school_settings: SCHOOL_NAME, SCHOOL_MOTTO, etc.
  - navigation: sidebar nav items with resolved URLs and active flags
  - parent_children / selected_child: child-switcher data for parent role
  - unread_announcement_count: unread badge for student/parent navbar
  - unread_notification_count: unread badge on the navbar bell
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import NoReverseMatch, reverse

from .navigation import NAVIGATION

if TYPE_CHECKING:
    from django.http import HttpRequest

# Maps role slug → dashboard shell template path
ROLE_BASE_TEMPLATES: dict[str, str] = {
    "admin": "dashboards/admin/base.html",
    "director": "dashboards/director/base.html",
    "teacher": "dashboards/teacher/base.html",
    "student": "dashboards/student/base.html",
    "finance": "dashboards/finance/base.html",
    "parent": "dashboards/parent/base.html",
}

DEFAULT_BASE_TEMPLATE = "base.html"


def base_template(request: HttpRequest) -> dict:
    """
    Provides ``base_template`` context variable.
    Feature templates use ``{% extends base_template %}`` so they
    automatically pick up the correct role shell.
    """
    tmpl = DEFAULT_BASE_TEMPLATE
    if request.user.is_authenticated and hasattr(request.user, "role"):
        tmpl = ROLE_BASE_TEMPLATES.get(request.user.role, DEFAULT_BASE_TEMPLATE)
    return {"base_template": tmpl}


def school_settings(request: HttpRequest) -> dict:
    """
    Provides school identity variables available in every template.

    The editable ``SchoolSettings`` row wins; any field left blank falls back to
    the matching ``SCHOOL_*`` setting from the environment. Costs one
    primary-key read per rendered request.
    """
    from .selectors import school_settings_values

    values = school_settings_values(request)
    return {
        "SCHOOL_NAME": values["name"] or "My School",
        "SCHOOL_MOTTO": values["motto"],
        "SCHOOL_PHONE": values["phone"],
        "SCHOOL_EMAIL": values["email"],
        "SCHOOL_CURRENCY": values["currency"],
        "SCHOOL_SHOW_POSITIONS": values["show_positions"],
    }


def _is_active_path(current_path: str, href: str) -> bool:
    """True when ``current_path`` is the nav target or one of its children."""
    if href == "#":
        return False
    if href == "/":
        return current_path == "/"
    return current_path == href or current_path.startswith(href.rstrip("/") + "/")


def _entry_is_available(request: HttpRequest | None, item: dict) -> bool:
    """
    Whether a nav entry may be shown at all.

    An entry that names a switched-off module is not a shortcut, it is a
    dead end with a label on it. Entries without a ``feature`` key are always
    shown, which is how the Finance portal keeps its links when the school
    stops billing.
    """
    slug = item.get("feature")
    if slug is None:
        return True
    from .features import feature_enabled

    return feature_enabled(slug, request=request)


def _resolve_nav_item(
    item: dict, current_path: str, request: HttpRequest | None = None
) -> dict:
    """Resolve a single nav item's URL and active state."""
    resolved = dict(item)
    url_name = item.get("url_name", "")
    url_args = item.get("url_args", [])
    url_kwargs = item.get("url_kwargs", {})
    try:
        href = reverse(url_name, args=url_args, kwargs=url_kwargs)
    except NoReverseMatch:
        href = "#"
    resolved["href"] = href
    resolved["active"] = _is_active_path(current_path, href)
    # Recurse into children
    resolved["children"] = [
        _resolve_nav_item(child, current_path, request)
        for child in item.get("children", [])
        if _entry_is_available(request, child)
    ]
    # A parent item is active if any child is active
    if resolved["children"] and any(c["active"] for c in resolved["children"]):
        resolved["active"] = True
    return resolved


def navigation(request: HttpRequest) -> dict:
    """
    Provides ``nav_items`` — the sidebar navigation for the current user's role,
    with resolved hrefs and active flags.
    """
    if not request.user.is_authenticated:
        return {"nav_items": []}

    role = getattr(request.user, "role", None)
    raw_items = NAVIGATION.get(role, [])
    current_path = request.path

    nav_items = [
        _resolve_nav_item(item, current_path, request)
        for item in raw_items
        if _entry_is_available(request, item)
    ]
    return {"nav_items": nav_items}


def parent_children(request: HttpRequest) -> dict:
    """
    For parent users: injects ``parent_children`` (list of Student) and
    ``selected_child`` (the currently active child) into every template.
    The selected child is stored in ``request.session["selected_child_id"]``.
    """
    if (
        not request.user.is_authenticated
        or getattr(request.user, "role", "") != "parent"
    ):
        return {"parent_children": [], "selected_child": None}

    if not hasattr(request.user, "parent_profile"):
        return {"parent_children": [], "selected_child": None}

    try:
        from apps.students.models import Student

        children = list(
            Student.objects.filter(
                guardian_links__parent=request.user.parent_profile
            ).select_related("user", "current_class")
        )

        child_ids = [c.pk for c in children]
        selected_id = request.session.get("selected_child_id")

        if selected_id and int(selected_id) in child_ids:
            selected = next((c for c in children if c.pk == int(selected_id)), None)
        else:
            # Default to primary child
            primary_link = (
                request.user.parent_profile.children_links.filter(is_primary=True)
                .select_related("student__user", "student__current_class")
                .first()
            )
            selected = (
                primary_link.student
                if primary_link
                else (children[0] if children else None)
            )
            if selected:
                request.session["selected_child_id"] = selected.pk

        return {"parent_children": children, "selected_child": selected}
    except Exception:  # noqa: BLE001
        return {"parent_children": [], "selected_child": None}


def unread_notifications(request: HttpRequest) -> dict:
    """
    Injects ``unread_notification_count`` for the navbar bell.

    One indexed COUNT per rendered page for a signed-in user; redirect and JSON
    answers never render, so they pay nothing.
    """
    if not request.user.is_authenticated:
        return {"unread_notification_count": 0}
    try:
        from .models import Notification
        from .notifications import delivered_notifications

        return {
            "unread_notification_count": delivered_notifications(
                Notification.objects.filter(recipient=request.user, is_read=False)
            ).count()
        }
    except Exception:  # noqa: BLE001
        return {"unread_notification_count": 0}


def unread_count(request: HttpRequest) -> dict:
    """
    Injects ``unread_announcement_count`` for use in the navbar badge.
    Only computed for student and parent roles to avoid overhead on staff pages.
    """
    if not request.user.is_authenticated:
        return {"unread_announcement_count": 0}
    if getattr(request.user, "role", "") not in ("student", "parent"):
        return {"unread_announcement_count": 0}
    try:
        from apps.communications.selectors import unread_announcement_count

        return {
            "unread_announcement_count": unread_announcement_count(user=request.user)
        }
    except Exception:  # noqa: BLE001
        return {"unread_announcement_count": 0}
