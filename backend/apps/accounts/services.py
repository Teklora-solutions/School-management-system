"""
apps/accounts/services.py
-------------------------
Write-side operations for user/staff management.
"""

from __future__ import annotations

import secrets
import string
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.audit_log import log_action

from .models import AdminProfile, DirectorProfile, FinanceProfile

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.accounts.models import User as UserType

User = get_user_model()

STAFF_ROLES = {
    User.Role.TEACHER,
    User.Role.DIRECTOR,
    User.Role.FINANCE,
    User.Role.ADMIN,
}

PROFILE_MAP = {
    User.Role.ADMIN: AdminProfile,
    User.Role.DIRECTOR: DirectorProfile,
    User.Role.FINANCE: FinanceProfile,
}


def _generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _unique_username(base: str) -> str:
    candidate = base[:40]
    if not User.objects.filter(username=candidate).exists():
        return candidate
    for i in range(1, 1000):
        candidate = f"{base[:35]}{i}"
        if not User.objects.filter(username=candidate).exists():
            return candidate
    raise ValidationError("Could not generate a unique username.")


def _send_credentials_email(
    user_id: int, temp_password: str, must_change: bool = True
) -> None:
    """
    Background-task entry: email login credentials.

    ``must_change`` defaults to True because the queued payload is positional
    and jobs enqueued before the admin-set-password change carry only two
    arguments; a third positional would raise TypeError on those.
    """
    from django.conf import settings
    from django.core.mail import send_mail

    user = User.objects.get(pk=user_id)
    subject = f"Your {getattr(settings, 'SCHOOL_NAME', 'School')} account"
    change_note = (
        "IMPORTANT: You will be required to set a new password when you first log in.\n"
        "Keep your new password safe — do not share it with anyone.\n"
        if must_change
        else "Keep your password safe — do not share it with anyone.\n"
    )

    if user.role == "student":
        # Students normally sign in with their admission number as both
        # username and initial password; an admin may override the password.
        body = (
            f"Hello {user.get_full_name() or user.username},\n\n"
            f"A student portal account has been created for you.\n\n"
            f"Login URL: http://127.0.0.1:8000/login/\n"
            f"Username (Admission No.): {user.username}\n"
            f"Password: {temp_password}\n\n"
            f"{change_note}"
        )
    else:
        body = (
            f"Hello {user.get_full_name() or user.username},\n\n"
            f"An account has been created for you.\n\n"
            f"Username: {user.username}\n"
            f"Password: {temp_password}\n\n"
            f"{change_note}"
        )
    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )


def enqueue_credentials_email(
    user: UserType, temp_password: str, must_change: bool = True
) -> None:
    """Queue credential email via django-q2 (falls back to sync if Q unavailable)."""
    try:
        from django_q.tasks import async_task

        async_task(
            "apps.accounts.services._send_credentials_email",
            user.pk,
            temp_password,
            must_change,
        )
    except Exception:  # noqa: BLE001
        # Dev / test fallback when Q cluster is not running
        _send_credentials_email(user.pk, temp_password, must_change)


@transaction.atomic
def create_staff_user(
    *,
    role: str,
    first_name: str,
    last_name: str,
    email: str,
    phone: str = "",
    staff_id: str = "",
    password: str | None = None,
    must_change_password: bool = True,
    actor: UserType | None = None,
    request: HttpRequest | None = None,
    send_credentials: bool = True,
    extra_profile: dict | None = None,
) -> tuple[UserType, str]:
    """
    Create a staff user (Teacher, Director, Finance, Admin).

    ``password`` is the initial password the admin chose. When it is omitted the
    system generates one — the only callers left doing that are the demo seeder
    and anything that never emails credentials.

    Returns ``(user, initial_password)`` so the caller can hand the password to
    a human. Strength is validated by ``StaffUserCreateForm``, not here:
    ``create_user()`` never runs Django's validators and dev/test disable them.
    """
    if role not in STAFF_ROLES:
        raise ValidationError({"role": f"Role '{role}' is not a staff role."})

    email = email.strip().lower()
    if User.objects.filter(email=email).exists():
        raise ValidationError({"email": "A user with this email already exists."})

    base_username = email.split("@")[0].replace(".", "_")
    username = _unique_username(base_username)
    initial_password = password or _generate_temp_password()

    user = User.objects.create_user(
        username=username,
        email=email,
        password=initial_password,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        phone=phone.strip(),
        role=role,
        must_change_password=must_change_password,
        is_active=True,
    )

    if role == User.Role.TEACHER:
        from apps.teachers.services import ensure_teacher_profile

        ensure_teacher_profile(
            user=user,
            staff_id=staff_id,
            **(extra_profile or {}),
        )
    elif role in PROFILE_MAP:
        profile_cls = PROFILE_MAP[role]
        profile_cls.objects.create(user=user, staff_id=staff_id or "")

    log_action(
        actor=actor,
        action="user.staff_created",
        content_type="accounts.User",
        object_id=user.pk,
        change_summary={"role": role, "email": email, "username": username},
        request=request,
    )

    if send_credentials:
        enqueue_credentials_email(user, initial_password, must_change_password)

    return user, initial_password


@transaction.atomic
def deactivate_user(
    *,
    user: UserType,
    actor: UserType | None = None,
    request: HttpRequest | None = None,
) -> UserType:
    if user.pk == getattr(actor, "pk", None):
        raise ValidationError("You cannot deactivate your own account.")
    user.is_active = False
    user.save(update_fields=["is_active", "updated_at"])
    log_action(
        actor=actor,
        action="user.deactivated",
        content_type="accounts.User",
        object_id=user.pk,
        change_summary={"email": user.email},
        request=request,
    )
    return user


@transaction.atomic
def reactivate_user(
    *,
    user: UserType,
    actor: UserType | None = None,
    request: HttpRequest | None = None,
) -> UserType:
    user.is_active = True
    user.save(update_fields=["is_active", "updated_at"])
    log_action(
        actor=actor,
        action="user.reactivated",
        content_type="accounts.User",
        object_id=user.pk,
        change_summary={"email": user.email},
        request=request,
    )
    return user


@transaction.atomic
def reset_user_password(
    *,
    user: UserType,
    actor: UserType | None = None,
    request: HttpRequest | None = None,
    send_credentials: bool = True,
) -> str:
    temp_password = _generate_temp_password()
    user.set_password(temp_password)
    user.must_change_password = True
    user.save(update_fields=["password", "must_change_password", "updated_at"])
    log_action(
        actor=actor,
        action="user.password_reset",
        content_type="accounts.User",
        object_id=user.pk,
        change_summary={"email": user.email},
        request=request,
    )
    if send_credentials:
        enqueue_credentials_email(user, temp_password)
    return temp_password
