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
from django.db.models.deletion import ProtectedError

from apps.core.audit_log import log_action
from apps.core.deletion import count_protected, count_relations, phrase

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

# staff_id lives on the role's profile row, not on User.
PROFILE_RELATED_NAME = {
    User.Role.ADMIN: "admin_profile",
    User.Role.DIRECTOR: "director_profile",
    User.Role.FINANCE: "finance_profile",
    User.Role.TEACHER: "teacher_profile",
}


def staff_profile(user: UserType):
    """The profile row that owns this staff member's ``staff_id``, or None."""
    related = PROFILE_RELATED_NAME.get(user.role)
    if not related:
        return None
    return getattr(user, related, None)


@transaction.atomic
def update_staff_user(
    *,
    user: UserType,
    first_name: str,
    last_name: str,
    email: str,
    phone: str = "",
    staff_id: str = "",
    actor: UserType | None = None,
    request: HttpRequest | None = None,
) -> UserType:
    """
    Edit a staff account's identity details.

    Deliberately excluded: **role** (moving a user between roles means moving
    their profile row and re-deriving what they can see, which no route here is
    able to do safely) and **password** (``reset_user_password`` is the audited
    path, and it is the only one that emails the new credentials).

    Note what an email change costs: staff sign in *with their email*
    (``EmailOrAdmissionBackend`` looks the account up by email), so the old
    address stops working the moment this saves. The username is left alone — it
    is the stable internal handle, and renaming it would break nothing but also
    fix nothing.
    """
    email = email.strip().lower()
    if User.objects.filter(email=email).exclude(pk=user.pk).exists():
        raise ValidationError({"email": "Another user already signs in with this email."})

    staff_id = staff_id.strip()
    profile = staff_profile(user)
    if not staff_id:
        # A blank field never erases the ID an account already has. A teacher
        # with no profile row yet gets the generated one, because
        # TeacherProfile.staff_id cannot be blank; the other roles allow blank.
        if profile:
            staff_id = profile.staff_id
        elif user.role == User.Role.TEACHER:
            staff_id = f"TCH-{user.pk:05d}"

    profile_cls = type(profile) if profile else PROFILE_MAP.get(user.role)
    if profile_cls is None:
        if user.role == User.Role.TEACHER:
            from apps.teachers.models import TeacherProfile

            profile_cls = TeacherProfile
        else:
            raise ValidationError(
                {"role": f"{user.get_role_display()} is not a staff role."}
            )
    if profile_cls.objects.filter(staff_id=staff_id).exclude(
        pk=getattr(profile, "pk", None)
    ).exists():
        raise ValidationError(
            {
                "staff_id": "This staff ID is already in use."
                if staff_id
                else "Enter a staff ID — another account is already saved without one."
            }
        )

    before = {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "staff_id": getattr(profile, "staff_id", ""),
    }
    user.first_name = first_name.strip()
    user.last_name = last_name.strip()
    user.email = email
    user.phone = phone.strip()
    user.save(
        update_fields=[
            "first_name",
            "last_name",
            "email",
            "phone",
            "updated_at",
        ]
    )

    if profile:
        profile.staff_id = staff_id
        profile.save(update_fields=["staff_id", "updated_at"])
    else:
        # Created plainly rather than through ``ensure_teacher_profile``: that
        # helper is an update_or_create and would reset
        # qualification/department to their defaults.
        profile = profile_cls.objects.create(user=user, staff_id=staff_id)

    after = {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "staff_id": profile.staff_id,
    }
    log_action(
        actor=actor,
        action="user.staff_updated",
        content_type="accounts.User",
        object_id=user.pk,
        change_summary={"before": before, "after": after},
        request=request,
    )
    return user


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


# ─── Deleting a staff account ─────────────────────────────────────────────────

# CASCADE refs: these disappear with the account.
STAFF_DELETE_DESTROYED = {
    "student_profile": "pupil record",
    "parent_profile": "parent record",
    "notifications": "inbox message",
    "message_threads_sent": "message thread started",
    "thread_messages": "message posted",
    "announcement_reads": "read receipt",
    "logentry_set": "django-admin log entry",
}
# SET_NULL refs: the row stays, the name on it goes. This is the part people
# only notice afterwards, so it is stated before the button is offered.
STAFF_DELETE_BLANKED = {
    "invoices_posted": "invoice",
    "payments_recorded": "payment receipt",
    "journal_entries": "journal entry",
    "expenses_recorded": "expense",
    "adjustments_applied": "fee adjustment",
    "payment_plans_agreed": "payment plan",
    "homework_set": "homework",
    "confirmed_sheets": "result sheet confirmation",
    "published_sheets": "result sheet publication",
    "written_ct_remarks": "class-teacher remark",
    "attendance_registers_recorded": "attendance register",
    "homeroom_classes": "class",
    "announcements_created": "announcement",
    "audit_logs": "audit log row",
}


def staff_delete_forecast(user: UserType) -> dict:
    """What disappears with this account, and what loses its author."""
    destroyed = count_relations(STAFF_DELETE_DESTROYED, user)
    profile = staff_profile(user)
    if profile is not None:
        destroyed["staff profile (staff ID, qualification, department)"] = 1
        assignments = getattr(profile, "assignments", None)
        if assignments is not None:
            count = assignments.count()
            if count:
                destroyed["class-subject assignment"] = count
    return {
        "destroyed": destroyed,
        "blanked": count_relations(STAFF_DELETE_BLANKED, user),
    }


def staff_delete_refusals(user: UserType, actor: UserType | None = None) -> list[str]:
    """Every reason this account must not be deleted yet, as plain sentences.

    The confirm page needs them before anyone presses the button, and
    ``delete_staff_user`` raises with the same list — one rule, one place.
    """
    reasons = []
    if user.pk == getattr(actor, "pk", None):
        reasons.append("You cannot delete your own account.")
    if user.is_active:
        reasons.append(
            f"{user.full_name} is still an active account. Deactivate it first — "
            "deleting is permanent, deactivating is not."
        )
    if user.role == User.Role.ADMIN and (
        User.objects.filter(role=User.Role.ADMIN, is_active=True)
        .exclude(pk=user.pk)
        .count()
        == 0
    ):
        reasons.append(
            "This is the only active administrator, so it cannot be deleted — the "
            "school would be left with no account able to manage staff or pupils."
        )
    return reasons


@transaction.atomic
def delete_staff_user(
    *,
    user: UserType,
    actor: UserType | None = None,
    request: HttpRequest | None = None,
) -> str:
    """Delete a staff account, or refuse and say why.

    Three guards run before anything is touched, because this one is not
    reversible: only an **already deactivated** account may be deleted, so an
    account still in use cannot be wiped by one stray click; nobody deletes their
    own; and the last active administrator cannot be deleted, since no remaining
    account would be able to manage staff or pupils.

    Deleting the ``User`` removes their profile row too, and every money, results
    and attendance row they posted survives but stops naming who did it — those
    columns are ``SET_NULL``. The forecast puts that in writing on the confirm
    page, because afterwards it cannot be undone.
    """
    label = f"{user.full_name} ({user.get_role_display()})"
    reasons = staff_delete_refusals(user, actor)
    if reasons:
        raise ValidationError(reasons)

    snapshot = {
        "username": user.username,
        "email": user.email,
        "name": user.full_name,
        "role": user.role,
        "staff_id": getattr(staff_profile(user), "staff_id", ""),
    }
    forecast = staff_delete_forecast(user)

    try:
        with transaction.atomic():
            user.delete()
    except ProtectedError as exc:
        named = count_protected(
            user, {**STAFF_DELETE_DESTROYED, **STAFF_DELETE_BLANKED}, exc.protected_objects
        )
        raise ValidationError(
            f"{label} still has {phrase(named)} attached, so the account cannot be "
            "deleted. That refusal is the protection working."
        ) from exc

    log_action(
        actor=actor,
        action="user.deleted",
        content_type="accounts.User",
        object_id=user.pk,
        change_summary={"removed": snapshot, "destroyed": forecast["destroyed"]},
        request=request,
    )
    return label
