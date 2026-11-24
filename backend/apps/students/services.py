"""
apps/students/services.py
-------------------------
Student CRUD, guardian linking, bulk import commit, and class promotion.
"""

from __future__ import annotations

import io
from datetime import date
from typing import TYPE_CHECKING, Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.accounts.models import ParentProfile
from apps.accounts.services import enqueue_credentials_email
from apps.core.audit_log import log_action
from apps.core.deletion import count_protected, count_relations, phrase

from .models import (
    BulkImportJob,
    PromotionBatch,
    PromotionRecord,
    Student,
    StudentGuardian,
)

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.academics.models import AcademicYear, SchoolClass, Stream, Term
    from apps.accounts.models import User as UserType

User = get_user_model()

BULK_TEMPLATE_HEADERS = [
    "admission_number",
    "first_name",
    "last_name",
    "email",
    "gender",
    "date_of_birth",
    "stream",
    "guardian_first_name",
    "guardian_last_name",
    "guardian_email",
    "guardian_phone",
    "guardian_relationship",
]


def build_bulk_template_bytes() -> bytes:
    """Return an .xlsx template as bytes."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Students"
    ws.append(BULK_TEMPLATE_HEADERS)
    ws.append(
        [
            "ADM001",
            "Ama",
            "Mensah",
            "ama.mensah@student.school",
            "F",
            "2014-05-12",
            "Blue",
            "Kwame",
            "Mensah",
            "kwame.mensah@example.com",
            "0244000000",
            "father",
        ]
    )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate_import_template_response():
    """HttpResponse wrapping the .xlsx bulk-import template."""
    from django.http import HttpResponse

    resp = HttpResponse(
        build_bulk_template_bytes(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = 'attachment; filename="student_import_template.xlsx"'
    return resp


def student_username_for(admission_number: str) -> str:
    """
    The username a student is expected to type for a given admission number.

    Kept in one place because both the account creation here and the student
    login form have to agree; paperwork prints the admission number, the account
    is keyed on this derivation.
    """
    return admission_number.strip().lower().replace("/", "_").replace(" ", "_")


def _unique_username(base: str) -> str:
    candidate = base[:40]
    if not User.objects.filter(username=candidate).exists():
        return candidate
    for i in range(1, 1000):
        candidate = f"{base[:35]}{i}"
        if not User.objects.filter(username=candidate).exists():
            return candidate
    raise ValidationError("Could not generate a unique username.")


def _temp_password() -> str:
    import secrets
    import string

    return "".join(
        secrets.choice(string.ascii_letters + string.digits) for _ in range(12)
    )


@transaction.atomic
def create_student_with_guardian(
    *,
    admission_number: str,
    first_name: str,
    last_name: str,
    email: str,
    gender: str = "",
    date_of_birth: date | None = None,
    current_class: SchoolClass | None = None,
    current_stream: Stream | None = None,
    address: str = "",
    guardian_first_name: str = "",
    guardian_last_name: str = "",
    guardian_email: str = "",
    guardian_phone: str = "",
    guardian_relationship: str = StudentGuardian.Relationship.GUARDIAN,
    existing_parent: ParentProfile | None = None,
    password: str | None = None,
    must_change_password: bool = True,
    actor: UserType | None = None,
    request: HttpRequest | None = None,
    send_credentials: bool = True,
) -> Student:
    admission_number = admission_number.strip().upper()
    if Student.objects.filter(admission_number=admission_number).exists():
        raise ValidationError(
            {"admission_number": "A student with this admission number already exists."}
        )

    email = email.strip().lower()
    if User.objects.filter(email=email).exists():
        raise ValidationError({"email": "A user with this email already exists."})

    # Students log in with their admission number as both username AND password
    # unless the admin typed an override on the add-student form. Bulk import
    # never overrides, so a whole year group still shares one predictable rule.
    student_username = _unique_username(student_username_for(admission_number))
    student_password = password or admission_number
    student_user = User.objects.create_user(
        username=student_username,
        email=email,
        password=student_password,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        role=User.Role.STUDENT,
        must_change_password=must_change_password,
    )

    student = Student.objects.create(
        user=student_user,
        admission_number=admission_number,
        date_of_birth=date_of_birth,
        gender=gender,
        current_class=current_class,
        current_stream=current_stream,
        enrollment_date=date.today(),
        address=address,
        status=Student.Status.ACTIVE,
    )

    parent_profile = existing_parent
    parent_password = None
    if parent_profile is None and guardian_email:
        g_email = guardian_email.strip().lower()
        existing_user = User.objects.filter(email=g_email).first()
        if existing_user:
            if existing_user.role != User.Role.PARENT:
                raise ValidationError(
                    {"guardian_email": "Email belongs to a non-parent account."}
                )
            parent_profile, _ = ParentProfile.objects.get_or_create(user=existing_user)
        else:
            parent_password = _temp_password()
            parent_user = User.objects.create_user(
                username=_unique_username(g_email.split("@")[0]),
                email=g_email,
                password=parent_password,
                first_name=guardian_first_name.strip(),
                last_name=guardian_last_name.strip(),
                phone=guardian_phone.strip(),
                role=User.Role.PARENT,
                must_change_password=True,
            )
            parent_profile = ParentProfile.objects.create(user=parent_user)

    if parent_profile:
        StudentGuardian.objects.get_or_create(
            student=student,
            parent=parent_profile,
            defaults={
                "relationship": guardian_relationship,
                "is_primary": True,
            },
        )

    log_action(
        actor=actor,
        action="student.created",
        content_type="students.Student",
        object_id=student.pk,
        change_summary={
            "admission_number": admission_number,
            "class": current_class.name if current_class else None,
        },
        request=request,
    )

    if send_credentials:
        enqueue_credentials_email(
            student_user, student_password, must_change_password
        )
        if parent_password and parent_profile:
            # The parent's password is still generated, so it always forces a change.
            enqueue_credentials_email(parent_profile.user, parent_password, True)

    return student


# ─── Editing an enrolled student ──────────────────────────────────────────────


@transaction.atomic
def update_student(
    *,
    student: Student,
    first_name: str,
    last_name: str,
    email: str,
    admission_number: str,
    gender: str = "",
    date_of_birth: date | None = None,
    enrollment_date: date | None = None,
    current_class: SchoolClass | None = None,
    current_stream: Stream | None = None,
    address: str = "",
    status: str | None = None,
    actor: UserType | None = None,
    request: HttpRequest | None = None,
) -> Student:
    """
    Correct a student record after enrolment: name, email, admission number,
    date of birth, gender, class/stream, enrolment date, address and status.

    Deliberately excluded: **password** (an override belongs to the add form, and
    this is a typo fix rather than a re-issue — no credentials email is sent
    here), and **``sponsor`` / ``payment_plan_notes``**, which Finance owns
    behind its own write gate in ``finance.services.update_student_finance_notes``.
    Guardians are not touched either: those live on ``StudentGuardian`` rows.

    The admission number is the sensitive field. A pupil signs in with it, and the
    account's username is ``student_username_for(admission_number)`` — the login
    form turns the number typed on the admission letter back into that username.
    So a rename moves both, in this transaction, or the account stops being
    reachable by its own paperwork. When the derived username already belongs to
    another account the rename is *refused* rather than suffix-solved:
    ``_unique_username``'s counter would leave the printed number and the stored
    login name disagreeing, which is the bug this guards against.
    """
    user = student.user
    admission_number = admission_number.strip().upper()
    if not admission_number:
        raise ValidationError({"admission_number": "An admission number is required."})
    if (
        Student.objects.filter(admission_number=admission_number)
        .exclude(pk=student.pk)
        .exists()
    ):
        raise ValidationError(
            {"admission_number": "Another student already has this admission number."}
        )

    email = email.strip().lower()
    if User.objects.filter(email=email).exclude(pk=user.pk).exists():
        raise ValidationError({"email": "Another account already uses this email."})

    renamed = admission_number != student.admission_number
    if renamed:
        derived = student_username_for(admission_number)
        if User.objects.filter(username=derived).exclude(pk=user.pk).exists():
            raise ValidationError(
                {
                    "admission_number": (
                        f"'{admission_number}' cannot be used: its login name "
                        f"'{derived}' already belongs to another account."
                    )
                }
            )

    if current_stream and (
        current_class is None or current_stream.school_class_id != current_class.pk
    ):
        raise ValidationError(
            {
                "current_stream": f"{current_stream.name} is not a stream of "
                f"{current_class.name if current_class else 'the selected class'}; "
                "pick a stream from that class or clear it."
            }
        )

    before = {
        "admission_number": student.admission_number,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "gender": student.gender,
        "date_of_birth": str(student.date_of_birth or ""),
        "enrollment_date": str(student.enrollment_date or ""),
        "class": student.current_class.name if student.current_class else "",
        "stream": student.current_stream.name if student.current_stream else "",
        "status": student.status,
        "address": student.address,
    }

    if renamed:
        user.username = derived
    user.first_name = first_name.strip()
    user.last_name = last_name.strip()
    user.email = email
    user.save(update_fields=["first_name", "last_name", "email", "username", "updated_at"])

    student.admission_number = admission_number
    student.gender = gender or ""
    student.date_of_birth = date_of_birth
    student.enrollment_date = enrollment_date
    student.current_class = current_class
    student.current_stream = current_stream
    student.address = address.strip()
    if status:
        student.status = status
    student.save(
        update_fields=[
            "admission_number",
            "gender",
            "date_of_birth",
            "enrollment_date",
            "current_class",
            "current_stream",
            "address",
            "status",
            "updated_at",
        ]
    )

    after = {
        "admission_number": student.admission_number,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "gender": student.gender,
        "date_of_birth": str(student.date_of_birth or ""),
        "enrollment_date": str(student.enrollment_date or ""),
        "class": student.current_class.name if student.current_class else "",
        "stream": student.current_stream.name if student.current_stream else "",
        "status": student.status,
        "address": student.address,
    }
    log_action(
        actor=actor,
        action="student.updated",
        content_type="students.Student",
        object_id=student.pk,
        change_summary={"before": before, "after": after},
        request=request,
    )
    if renamed:
        # Its own event, so "who moved this pupil's identity?" is one filter away.
        log_action(
            actor=actor,
            action="student.admission_number_changed",
            content_type="students.Student",
            object_id=student.pk,
            change_summary={
                "from": before["admission_number"],
                "to": student.admission_number,
                "username": user.username,
            },
            request=request,
        )
    return student


@transaction.atomic
def set_student_login_active(
    *,
    student: Student,
    active: bool,
    actor: UserType | None = None,
    request: HttpRequest | None = None,
) -> Student:
    """Switch a pupil's portal login on or off, leaving the record alone.

    ``Student.status`` is a listing decision, not an access one: a withdrawn or
    graduated pupil keeps a working sign-in until this runs, which is why the
    admin needs a separate lever. Only ``User.is_active`` changes, so marks,
    fees, attendance and guardian links are untouched and re-enabling restores
    exactly what was there.

    Disabling also drops a session the pupil is already sitting in — Django's
    ``ModelBackend.get_user`` re-checks ``is_active`` on every request, not only
    at login.
    """
    user = student.user
    if user.is_active == active:
        # Nothing to write: a stale tab or a double POST must not invent an
        # audit row for a state that never changed.
        return student
    previous = user.is_active
    user.is_active = active
    user.save(update_fields=["is_active", "updated_at"])
    log_action(
        actor=actor,
        action="student.login_enabled" if active else "student.login_disabled",
        content_type="students.Student",
        object_id=student.pk,
        change_summary={
            "username": user.username,
            "admission_number": student.admission_number,
            "is_active": {"before": previous, "after": active},
        },
        request=request,
    )
    return student


# ─── Deleting a pupil ─────────────────────────────────────────────────────────

# PROTECT refs: these make the delete impossible, they are not "attached history".
STUDENT_DELETE_BLOCKERS = {
    "invoices": "fee invoice",
    "payments": "payment receipt",
    "adjustments": "fee adjustment",
    "payment_plans": "payment plan",
    "attendance_entries": "attendance mark",
}
# CASCADE refs: these disappear with the pupil.
STUDENT_DELETE_DESTROYED = {
    "score_entries": "marks entry",
    "class_teacher_remarks": "class-teacher remark",
    "homework_submissions": "homework submission",
    "guardian_links": "guardian link",
    "promotion_records": "promotion record",
    "fee_elections": "fee election",
}
# M2M: the rule itself stays, the pupil simply stops being named in it.
STUDENT_DELETE_LINKS = {"fee_discount_rules": "fee discount rule"}


def student_delete_forecast(student: Student) -> dict:
    """What blocks deleting this pupil, and what would go with them.

    Advisory only — the database keeps the final say in ``delete_student``.
    """
    return {
        "blockers": count_relations(STUDENT_DELETE_BLOCKERS, student),
        "destroyed": count_relations(STUDENT_DELETE_DESTROYED, student),
        "links": count_relations(STUDENT_DELETE_LINKS, student),
    }


@transaction.atomic
def delete_student(
    *,
    student: Student,
    actor: UserType | None = None,
    request: HttpRequest | None = None,
) -> str:
    """Delete a pupil's account, or refuse and name what blocks it.

    The row deleted is the ``User``, not the ``Student``: the one-to-one runs
    User → Student, so removing the profile alone would leave a live login with
    no pupil behind it. Fees and attendance PROTECT the pupil, so a delete that
    would break the money trail is refused by the database — ``ProtectedError``
    becomes a refusal naming those rows rather than a 500 page.

    The snapshot is taken before the delete and logged after it succeeds, because
    afterwards there is no record left to describe what was removed. A failed
    delete must not leave an audit trail claiming it happened.
    """
    user = student.user
    label = f"{user.full_name} ({student.admission_number})"
    snapshot = {
        "admission_number": student.admission_number,
        "username": user.username,
        "email": user.email,
        "name": user.full_name,
        "class": student.current_class.name if student.current_class else "",
        "status": student.status,
        "login_active": user.is_active,
    }
    forecast = student_delete_forecast(student)

    try:
        with transaction.atomic():
            user.delete()
    except ProtectedError as exc:
        named = count_protected(student, STUDENT_DELETE_BLOCKERS, exc.protected_objects)
        raise ValidationError(
            f"{label} still has {phrase(named)} on record, so the account cannot "
            "be deleted. That refusal is the protection working: delete those rows "
            "only if they were entered in error, otherwise disable the login."
        ) from exc

    log_action(
        actor=actor,
        action="student.deleted",
        content_type="students.Student",
        object_id=student.pk,
        change_summary={"removed": snapshot, "destroyed": forecast["destroyed"]},
        request=request,
    )
    return label


# ─── Bulk import ──────────────────────────────────────────────────────────────


def start_bulk_import(
    *,
    uploaded_file,
    target_class: SchoolClass,
    mode: str = BulkImportJob.Mode.ALL_OR_NOTHING,
    uploaded_by: UserType | None = None,
) -> BulkImportJob:
    job = BulkImportJob.objects.create(
        uploaded_by=uploaded_by,
        file=uploaded_file,
        target_class=target_class,
        mode=mode,
        status=BulkImportJob.Status.PENDING,
    )
    try:
        from django_q.tasks import async_task

        async_task("apps.students.tasks.validate_bulk_import", job.pk)
    except Exception:  # noqa: BLE001
        from apps.students.tasks import validate_bulk_import

        validate_bulk_import(job.pk)
    return job


def validate_bulk_rows(
    rows: list[dict[str, Any]],
    *,
    target_class: SchoolClass,
) -> tuple[list[dict], list[dict]]:
    """
    Validate parsed Excel rows. Returns (valid_rows, error_rows).
    Idempotency: existing admission numbers are marked as skip (not errors).
    """
    valid: list[dict] = []
    errors: list[dict] = []
    seen_adm: set[str] = set()
    seen_emails: set[str] = set()

    stream_names = {s.name.lower(): s for s in target_class.streams.all()}

    for idx, raw in enumerate(rows, start=2):  # row 1 = header
        row = {k: (str(v).strip() if v is not None else "") for k, v in raw.items()}
        row_errors: list[str] = []
        adm = row.get("admission_number", "").upper()
        email = row.get("email", "").lower()

        if not adm:
            row_errors.append("admission_number is required")
        elif adm in seen_adm:
            row_errors.append(f"Duplicate admission_number in file: {adm}")
        if not row.get("first_name"):
            row_errors.append("first_name is required")
        if not row.get("last_name"):
            row_errors.append("last_name is required")
        if not email:
            row_errors.append("email is required")
        elif email in seen_emails:
            row_errors.append(f"Duplicate email in file: {email}")

        existing_student = (
            Student.objects.filter(admission_number=adm).first() if adm else None
        )
        if existing_student:
            # Idempotent skip — not an error
            valid.append(
                {
                    **row,
                    "admission_number": adm,
                    "email": email,
                    "_row": idx,
                    "_action": "skip_existing",
                    "_student_id": existing_student.pk,
                }
            )
            seen_adm.add(adm)
            seen_emails.add(email)
            continue

        if email and User.objects.filter(email=email).exists():
            row_errors.append(f"Email already registered: {email}")

        stream_name = row.get("stream", "")
        stream_id = None
        if stream_name:
            stream = stream_names.get(stream_name.lower())
            if not stream:
                row_errors.append(f"Unknown stream: {stream_name}")
            else:
                stream_id = stream.pk

        dob = row.get("date_of_birth", "")
        if dob:
            try:
                date.fromisoformat(dob[:10])
            except ValueError:
                row_errors.append("date_of_birth must be YYYY-MM-DD")

        gender = row.get("gender", "").upper()
        if gender and gender not in {c.value for c in Student.Gender}:
            row_errors.append("gender must be M, F, or O")

        payload = {
            **row,
            "admission_number": adm,
            "email": email,
            "gender": gender,
            "_row": idx,
            "_stream_id": stream_id,
            "_action": "create",
        }

        if row_errors:
            errors.append({**payload, "_errors": row_errors})
        else:
            valid.append(payload)
            seen_adm.add(adm)
            seen_emails.add(email)

    return valid, errors


@transaction.atomic
def commit_bulk_import(
    *,
    job: BulkImportJob,
    actor: UserType | None = None,
    request: HttpRequest | None = None,
) -> BulkImportJob:
    if job.status != BulkImportJob.Status.READY:
        raise ValidationError("Import job is not ready to commit.")

    valid = [r for r in job.preview_rows if r.get("_action") == "create"]
    skips = [r for r in job.preview_rows if r.get("_action") == "skip_existing"]
    errors = list(job.error_rows)

    if job.mode == BulkImportJob.Mode.ALL_OR_NOTHING and errors:
        raise ValidationError(
            "All-or-nothing mode: fix or remove error rows before committing."
        )

    created = 0
    target_class = job.target_class
    if not target_class:
        raise ValidationError("Target class is missing.")

    for row in valid:
        dob = None
        if row.get("date_of_birth"):
            try:
                dob = date.fromisoformat(row["date_of_birth"][:10])
            except ValueError:
                dob = None
        stream = None
        if row.get("_stream_id"):
            from apps.academics.models import Stream

            stream = Stream.objects.filter(pk=row["_stream_id"]).first()

        create_student_with_guardian(
            admission_number=row["admission_number"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            email=row["email"],
            gender=row.get("gender", ""),
            date_of_birth=dob,
            current_class=target_class,
            current_stream=stream,
            guardian_first_name=row.get("guardian_first_name", ""),
            guardian_last_name=row.get("guardian_last_name", ""),
            guardian_email=row.get("guardian_email", ""),
            guardian_phone=row.get("guardian_phone", ""),
            guardian_relationship=row.get("guardian_relationship")
            or StudentGuardian.Relationship.GUARDIAN,
            actor=actor,
            request=request,
            send_credentials=True,
        )
        created += 1

    job.status = BulkImportJob.Status.COMMITTED
    job.committed_count = created
    job.summary = {
        "created": created,
        "skipped_existing": len(skips),
        "errors": len(errors),
        "mode": job.mode,
    }
    job.save()
    log_action(
        actor=actor,
        action="student.bulk_import_committed",
        content_type="students.BulkImportJob",
        object_id=job.pk,
        change_summary=job.summary,
        request=request,
    )
    return job


def build_error_report_bytes(job: BulkImportJob, *, actor=None) -> bytes:
    """
    Lay the failed bulk-import rows out as .xlsx.

    Every cell here came out of somebody else's uploaded spreadsheet, so the
    data rows go through ``append_rows``: a name or an error message that looks
    like a formula is written as text and stays text. The header row is a fixed
    contract with the reader, so it is written straight.
    """
    from apps.core.exports import append_rows, stamp_document, start_workbook

    wb = start_workbook("Errors")
    ws = wb.active
    headers = BULK_TEMPLATE_HEADERS + ["row", "errors"]
    ws.append(headers)
    append_rows(
        ws,
        (
            [row.get(h, "") for h in BULK_TEMPLATE_HEADERS]
            + [row.get("_row", ""), "; ".join(row.get("_errors", []))]
            for row in job.error_rows
        ),
    )
    stamp_document(
        wb,
        title="Bulk import errors",
        source="Students > Bulk import > Error report",
        actor=actor,
    )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate_error_report_response(job: BulkImportJob, *, actor=None):
    """HttpResponse wrapping the .xlsx error report for a bulk-import job."""
    from django.http import HttpResponse

    from apps.core.exports import XLSX_CONTENT_TYPE

    resp = HttpResponse(
        build_error_report_bytes(job, actor=actor),
        content_type=XLSX_CONTENT_TYPE,
    )
    resp["Content-Disposition"] = f'attachment; filename="import_errors_{job.pk}.xlsx"'
    return resp


# ─── Promotion ────────────────────────────────────────────────────────────────


def _promotion_counts(moves: list[dict]) -> dict[str, int]:
    return {
        "promote": sum(1 for m in moves if m["action"] == PromotionRecord.Action.PROMOTE),
        "repeat": sum(1 for m in moves if m["action"] == PromotionRecord.Action.REPEAT),
        "graduate": sum(
            1 for m in moves if m["action"] == PromotionRecord.Action.GRADUATE
        ),
    }


def _duplicate_level_blockers(year, classes) -> list[str]:
    grouped: dict[int, list] = {}
    for school_class in classes:
        grouped.setdefault(school_class.level_order, []).append(school_class)
    blockers = []
    for level, rows in sorted(grouped.items()):
        if len(rows) < 2:
            continue
        names = ", ".join(sorted(row.name for row in rows))
        blockers.append(
            f"{year.name} has more than one class at level {level}: {names}. "
            "Give each grade its own level order before promoting."
        )
    return blockers


def _pass_mark_for(year):
    from decimal import Decimal

    from apps.results.models import GradingConfig

    config = GradingConfig.objects.filter(academic_year=year).only("pass_mark").first()
    if config is None:
        return Decimal("50")
    return config.pass_mark


def _failing_subjects(term, student_ids: list[int], pass_mark) -> dict[int, list[str]]:
    """Published subject averages below the year's pass mark."""
    from decimal import Decimal

    from django.db.models import Avg

    from apps.results.models import ResultSheet, ScoreEntry

    if not student_ids:
        return {}
    rows = (
        ScoreEntry.objects.filter(
            student_id__in=student_ids,
            result_sheet__term=term,
            result_sheet__status=ResultSheet.Status.PUBLISHED,
            total__isnull=False,
        )
        .values("student_id", "result_sheet__subject__name")
        .annotate(mean=Avg("total"))
    )
    failed: dict[int, list[str]] = {}
    for row in rows:
        mean = row["mean"]
        if mean is None:
            continue
        score = Decimal(str(mean))
        if score < pass_mark:
            failed.setdefault(row["student_id"], []).append(
                f"{row['result_sheet__subject__name']} {score:.2f}"
            )
    for subjects in failed.values():
        subjects.sort()
    return failed


def _outstanding_by_student(student_ids: list[int]) -> tuple[dict[int, str], str]:
    if not student_ids:
        return {}, ""
    try:
        from apps.finance.selectors import arrears_rows

        rows = arrears_rows(student_ids=student_ids)
    except Exception as exc:  # noqa: BLE001 - a preview is not a finance outage
        return {}, str(exc)
    return {row["student_id"]: f"{row['outstanding']:.2f}" for row in rows}, ""


def _notify_promotion(moves: list[dict], *, to_year_name: str, undone: bool = False) -> int:
    """Tell each pupil and their guardians what the move did. Never raises."""
    from django.urls import reverse

    from apps.core.notifications import notify

    if not moves:
        return 0
    try:
        link = reverse("accounts:dashboard_redirect")
    except Exception:  # noqa: BLE001
        link = ""
    students = {
        student.pk: student
        for student in Student.objects.filter(
            pk__in=[move["student_id"] for move in moves]
        )
        .select_related("user")
        .prefetch_related("guardian_links__parent__user")
    }
    sent = 0
    for move in moves:
        student = students.get(move["student_id"])
        if student is None:
            continue
        name = student.user.full_name
        if undone:
            place = move.get("from_class") or "their previous class"
            title = "Class move undone"
            message = f"The class move for {name} was undone. They are back in {place}."
        elif move["action"] == PromotionRecord.Action.GRADUATE:
            title = "Class placement"
            message = f"{name} has graduated."
        elif move["action"] == PromotionRecord.Action.REPEAT:
            title = "Class placement"
            message = (
                f"{name} will repeat {move.get('to_class') or 'the same class'} "
                f"in {to_year_name}."
            )
        else:
            title = "Class placement"
            message = (
                f"{name} has been promoted to "
                f"{move.get('to_class') or 'the next class'} for {to_year_name}."
            )
        recipients = [student.user]
        seen = {student.user_id}
        for link_row in student.guardian_links.all():
            parent_user = link_row.parent.user
            if parent_user.pk not in seen:
                recipients.append(parent_user)
                seen.add(parent_user.pk)
        for user in recipients:
            try:
                notify(
                    recipient=user,
                    title=title,
                    message=message,
                    link=link,
                    category="promotion",
                )
                sent += 1
            except Exception:  # noqa: BLE001 - notice failure must not undo the move
                continue
    return sent


def _active_promotion_batches(from_year, to_year):
    return PromotionBatch.objects.filter(
        from_year=from_year,
        to_year=to_year,
        is_committed=True,
        is_reversed=False,
    )


def _next_idempotency_key(from_year, to_year) -> str:
    base = f"{from_year.pk}->{to_year.pk}"
    taken = set(
        PromotionBatch.objects.filter(
            from_year=from_year, to_year=to_year
        ).values_list("idempotency_key", flat=True)
    )
    if base not in taken:
        return base
    n = 2
    while f"{base}#{n}" in taken:
        n += 1
    return f"{base}#{n}"


def _matching_stream(student, dest_class):
    """
    Return ``(stream, note)``.

    ``note is None`` means the destination has streams and none share the
    pupil's stream name — the caller must block the preview.
    """
    current = student.current_stream
    if dest_class is None or current is None:
        return None, ""
    streams = list(dest_class.streams.all())
    if not streams:
        return None, "Destination class has no streams; stream was cleared"
    wanted = current.name.strip().casefold()
    for stream in streams:
        if stream.name.strip().casefold() == wanted:
            return stream, ""
    return None, None


def preview_promotion(
    *,
    from_year: AcademicYear,
    to_year: AcademicYear,
    term: Term,
    repeat_admission_numbers: set[str] | None = None,
    include_suggested_repeats: bool = False,
) -> dict:
    """
    Dry-run: who moves where, who repeats, who graduates.

    Promotion follows ``level_order`` (current + 1). The highest level with no
    class above it graduates. A missing class anywhere else blocks the run.
    Repeats are the admission numbers passed in, plus — when asked — pupils
    whose published subject average is below the source year's pass mark.
    Pupils already in a committed batch for this year pair are left out so a
    later run can pick up admissions that arrived afterwards.
    """
    if from_year.pk == to_year.pk:
        raise ValidationError("From-year and To-year must be different.")
    if term.academic_year_id != from_year.pk:
        raise ValidationError(
            f"Results term {term} belongs to {term.academic_year}, "
            f"not {from_year.name}."
        )

    repeat_admission_numbers = {
        a.strip().upper() for a in (repeat_admission_numbers or set()) if a and a.strip()
    }
    from_classes = list(
        from_year.classes.order_by("level_order", "name").prefetch_related(
            "streams",
            Prefetch(
                "students",
                queryset=Student.objects.filter(
                    status__in=[Student.Status.ACTIVE, Student.Status.REPEATING]
                ).select_related("user", "current_stream"),
            ),
        )
    )
    to_classes = list(
        to_year.classes.order_by("level_order", "name").prefetch_related("streams")
    )

    blockers = []
    blockers.extend(_duplicate_level_blockers(from_year, from_classes))
    blockers.extend(_duplicate_level_blockers(to_year, to_classes))

    known: set[str] = set()
    eligible: list[tuple] = []
    for school_class in from_classes:
        for student in school_class.students.all():
            known.add(student.admission_number.strip().upper())
            eligible.append((school_class, student))

    # Pupils already moved no longer sit in the source year, so their
    # admission numbers are read off the committed records.
    already_rows = PromotionRecord.objects.filter(
        batch__from_year=from_year,
        batch__to_year=to_year,
        batch__is_committed=True,
        batch__is_reversed=False,
    ).values_list("student_id", "student__admission_number")
    already_ids = {student_id for student_id, _adm in already_rows}
    already_admissions = sorted({adm for _student_id, adm in already_rows})
    already_keys = {adm.strip().upper() for adm in already_admissions}
    unknown = sorted(repeat_admission_numbers - known - already_keys)
    if unknown:
        blockers.append(
            "Unknown admission numbers (not active or repeating in "
            f"{from_year.name}): {', '.join(unknown)}."
        )
    if blockers:
        raise ValidationError(blockers)

    to_by_level = {school_class.level_order: school_class for school_class in to_classes}
    max_level = max((school_class.level_order for school_class in from_classes), default=0)
    pending = [
        (school_class, student)
        for school_class, student in eligible
        if student.pk not in already_ids
    ]

    pass_mark = _pass_mark_for(from_year)
    failing = _failing_subjects(term, [student.pk for _, student in pending], pass_mark)
    if include_suggested_repeats:
        for _, student in pending:
            if student.pk in failing:
                repeat_admission_numbers.add(student.admission_number.strip().upper())

    moves: list[dict] = []
    for school_class, student in pending:
        adm = student.admission_number
        adm_key = adm.strip().upper()
        next_class = to_by_level.get(school_class.level_order + 1)
        same_class = to_by_level.get(school_class.level_order)
        is_final = school_class.level_order >= max_level and next_class is None
        failed = failing.get(student.pk) or []
        notes: list[str] = []
        if failed:
            notes.append("Below pass mark: " + ", ".join(failed))

        if adm_key in repeat_admission_numbers:
            action = PromotionRecord.Action.REPEAT
            dest = same_class
            if dest is None:
                blockers.append(
                    f"{adm} is set to repeat {school_class.name}, but {to_year.name} "
                    f"has no class at level {school_class.level_order}."
                )
                continue
            if not failed:
                notes.append("Marked to repeat")
        elif is_final:
            action = PromotionRecord.Action.GRADUATE
            dest = None
        elif next_class is None:
            blockers.append(
                f"{school_class.name} has no class at level "
                f"{school_class.level_order + 1} in {to_year.name}, "
                f"so {adm} cannot be promoted."
            )
            continue
        else:
            action = PromotionRecord.Action.PROMOTE
            dest = next_class

        to_stream, stream_note = _matching_stream(student, dest)
        if stream_note is None and dest is not None:
            blockers.append(
                f"{adm} is in stream {student.current_stream.name}, and "
                f"{dest.name} ({to_year.name}) has no stream with that name."
            )
            continue
        if stream_note:
            notes.append(stream_note)

        moves.append(
            {
                "student_id": student.pk,
                "admission_number": adm,
                "name": student.user.full_name,
                "action": action,
                "from_class_id": school_class.pk,
                "from_class": school_class.name,
                "to_class_id": dest.pk if dest else None,
                "to_class": dest.name if dest else None,
                "from_stream": (
                    student.current_stream.name if student.current_stream_id else ""
                ),
                "to_stream_id": to_stream.pk if to_stream else None,
                "to_stream": to_stream.name if to_stream else "",
                "previous_status": student.status,
                "suggested_repeat": bool(failed),
                "failing_subjects": failed,
                "notes": "; ".join(notes)[:255],
            }
        )

    if blockers:
        raise ValidationError(blockers)

    outstanding, finance_error = _outstanding_by_student(
        [move["student_id"] for move in moves]
    )
    for move in moves:
        move["outstanding"] = outstanding.get(move["student_id"], "0.00")

    from apps.results.selectors import term_all_classes_published

    return {
        "from_year": from_year.name,
        "from_year_id": from_year.pk,
        "to_year": to_year.name,
        "to_year_id": to_year.pk,
        "term": str(term),
        "term_id": term.pk,
        "results_published": term_all_classes_published(term.pk),
        "pass_mark": f"{pass_mark:.2f}",
        "moves": moves,
        "counts": _promotion_counts(moves),
        "classes": sorted({move["from_class"] for move in moves if move["from_class"]}),
        "already_promoted": already_admissions,
        "finance_error": finance_error,
    }


@transaction.atomic
@transaction.atomic
def commit_promotion(
    *,
    from_year: AcademicYear,
    to_year: AcademicYear,
    term: Term,
    repeat_admission_numbers: set[str] | None = None,
    override_unpublished: bool = False,
    override_reason: str = "",
    actor: UserType | None = None,
    request: HttpRequest | None = None,
) -> PromotionBatch:
    """
    Move every pupil in the preview.

    A second commit of the same years, with nobody new to move, returns the
    latest committed batch and sets ``replayed`` on it. Pupils admitted after
    that batch are written to a new batch. Decisions for pupils already in a
    committed batch are not changed here — reverse that batch first.
    """
    from apps.academics.models import AcademicYear

    AcademicYear.objects.select_for_update().filter(pk=from_year.pk).exists()
    preview = preview_promotion(
        from_year=from_year,
        to_year=to_year,
        term=term,
        repeat_admission_numbers=repeat_admission_numbers,
        include_suggested_repeats=False,
    )
    active = list(_active_promotion_batches(from_year, to_year).order_by("-committed_at", "-pk"))
    if not preview["moves"]:
        if active:
            batch = active[0]
            batch.replayed = True
            return batch
        raise ValidationError("No students to transition between these years.")

    if not preview["results_published"]:
        if not override_unpublished:
            raise ValidationError(
                "Cannot promote: term results are not fully published. "
                "Use override with a reason if you must proceed."
            )
        if not override_reason.strip():
            raise ValidationError(
                {
                    "override_reason": "Override reason is required when results are unpublished."
                }
            )

    batch = PromotionBatch.objects.create(
        from_year=from_year,
        to_year=to_year,
        term=term,
        committed_by=actor,
        override_unpublished=override_unpublished,
        override_reason=override_reason,
        preview_snapshot=preview,
        idempotency_key=_next_idempotency_key(from_year, to_year),
    )

    students = {
        student.pk: student
        for student in Student.objects.select_related("user", "current_stream").filter(
            pk__in=[move["student_id"] for move in preview["moves"]]
        )
    }
    for move in preview["moves"]:
        student = students[move["student_id"]]
        PromotionRecord.objects.create(
            batch=batch,
            student=student,
            action=move["action"],
            from_class_id=move["from_class_id"],
            to_class_id=move["to_class_id"],
            previous_status=student.status,
            previous_stream_id=student.current_stream_id,
            to_stream_id=move.get("to_stream_id"),
            notes=(move.get("notes") or "")[:255],
        )
        if move["action"] == PromotionRecord.Action.GRADUATE:
            student.status = Student.Status.GRADUATED
            student.current_class_id = None
            student.current_stream_id = None
        elif move["action"] == PromotionRecord.Action.REPEAT:
            student.status = Student.Status.REPEATING
            student.current_class_id = move["to_class_id"]
            student.current_stream_id = move.get("to_stream_id")
        else:
            student.status = Student.Status.ACTIVE
            student.current_class_id = move["to_class_id"]
            student.current_stream_id = move.get("to_stream_id")
        student.save()

    batch.is_committed = True
    batch.committed_at = timezone.now()
    batch.save()

    log_action(
        actor=actor,
        action="student.promotion_committed",
        content_type="students.PromotionBatch",
        object_id=batch.pk,
        change_summary={
            "from_year": from_year.name,
            "to_year": to_year.name,
            "counts": preview["counts"],
            "override": override_unpublished,
            "override_reason": override_reason,
            "already_promoted": preview["already_promoted"],
        },
        request=request,
    )
    if override_unpublished:
        log_action(
            actor=actor,
            action="student.promotion_override_unpublished",
            content_type="students.PromotionBatch",
            object_id=batch.pk,
            change_summary={"reason": override_reason},
            request=request,
        )

    # Finance's hook, and the reason it lives here: a balance is not a field on
    # ``Student``, so promotion cannot lose one — but it can lose sight of one,
    # because every class fee page selects pupils by their *current* class. This
    # reads each pupil's debt back over all terms and writes it to the audit log
    # beside the promotion that moved them. It never raises: a finance read that
    # cannot be answered must not leave children in last year's class.
    from apps.finance.services import carry_forward_arrears

    carry_forward_arrears(
        student_ids=[move["student_id"] for move in preview["moves"]],
        actor=actor,
        context={
            "batch": str(batch.pk),
            "from_year": from_year.name,
            "to_year": to_year.name,
        },
        request=request,
    )
    batch.replayed = False
    batch.notified = _notify_promotion(preview["moves"], to_year_name=to_year.name)
    return batch


@transaction.atomic
@transaction.atomic
def reverse_promotion(
    *,
    batch: PromotionBatch,
    reason: str,
    actor: UserType | None = None,
    request: HttpRequest | None = None,
) -> PromotionBatch:
    """Put every pupil in the batch back in the class, stream and status they left."""
    batch = PromotionBatch.objects.select_for_update().get(pk=batch.pk)
    if not batch.is_committed or batch.is_reversed:
        raise ValidationError("Only a committed promotion can be reversed.")
    if not reason.strip():
        raise ValidationError({"reason": "A reason is required to undo a promotion."})

    from django.db.models import Q

    student_ids = list(batch.records.values_list("student_id", flat=True))
    later = (
        PromotionRecord.objects.filter(
            student_id__in=student_ids,
            batch__is_committed=True,
            batch__is_reversed=False,
        )
        .exclude(batch=batch)
        .filter(
            Q(batch__committed_at__gt=batch.committed_at)
            | Q(batch__committed_at=batch.committed_at, batch_id__gt=batch.pk)
        )
    )
    if later.exists():
        raise ValidationError(
            "A later promotion moved some of these pupils. Reverse that batch first."
        )

    records = list(
        batch.records.select_related("student", "from_class", "previous_stream", "to_class")
    )
    moves = []
    for record in records:
        student = record.student
        student.status = record.previous_status or Student.Status.ACTIVE
        student.current_class = record.from_class
        student.current_stream = record.previous_stream
        student.save()
        moves.append(
            {
                "student_id": student.pk,
                "action": record.action,
                "from_class": record.from_class.name if record.from_class_id else "",
                "to_class": record.to_class.name if record.to_class_id else "",
            }
        )

    batch.is_reversed = True
    batch.reversed_at = timezone.now()
    batch.reversed_by = actor
    batch.reverse_reason = reason.strip()
    batch.idempotency_key = f"reversed-{batch.pk}"
    batch.save()

    log_action(
        actor=actor,
        action="student.promotion_reversed",
        content_type="students.PromotionBatch",
        object_id=batch.pk,
        change_summary={
            "from_year": batch.from_year.name,
            "to_year": batch.to_year.name,
            "pupils": len(records),
            "reason": batch.reverse_reason,
        },
        request=request,
    )
    _notify_promotion(moves, to_year_name=batch.to_year.name, undone=True)
    return batch
