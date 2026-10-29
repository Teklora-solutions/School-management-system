"""
apps/accounts/selectors.py
--------------------------
Read-side queries for staff user management.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet

User = get_user_model()

STAFF_ROLE_VALUES = [
    User.Role.ADMIN,
    User.Role.DIRECTOR,
    User.Role.TEACHER,
    User.Role.FINANCE,
]


def staff_user_list(
    *,
    search: str = "",
    role: str = "",
    is_active: bool | None = None,
) -> QuerySet:
    qs = User.objects.filter(role__in=STAFF_ROLE_VALUES).order_by(
        "role", "last_name", "first_name"
    )
    if search:
        qs = qs.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
            | Q(username__icontains=search)
        )
    if role:
        qs = qs.filter(role=role)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)
    return qs


def get_staff_user(user_id: int) -> User:
    return User.objects.get(pk=user_id, role__in=STAFF_ROLE_VALUES)
