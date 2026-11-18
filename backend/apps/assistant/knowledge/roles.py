"""
Role storage for knowledge audiences.

Roles are stored as a canonical pipe-wrapped string — ``|parent|teacher|`` —
rather than a JSON list. Both SQLite and PostgreSQL can then filter with a
plain ``LIKE``, which keeps the audience filter in the database before any
ranking. An empty string means "every signed-in role".
"""

from __future__ import annotations

ROLE_LABELS = {
    "admin": "Admin",
    "director": "Director",
    "teacher": "Teacher",
    "student": "Student",
    "finance": "Finance officer",
    "parent": "Parent / guardian",
}

VALID_ROLES = frozenset(ROLE_LABELS)


def roles_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [part for part in value.split("|") if part]
    return [str(role) for role in value if role]


def normalise_roles(value) -> list[str]:
    return sorted({role for role in roles_list(value) if role in VALID_ROLES})


def roles_key(value) -> str:
    roles = normalise_roles(value)
    return f"|{'|'.join(roles)}|" if roles else ""


def role_allowed(stored: str, role: str) -> bool:
    return not stored or role in roles_list(stored)
