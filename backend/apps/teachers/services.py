"""
apps/teachers/services.py
-------------------------
Teacher profile and assignment write operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.audit_log import log_action

from .models import TeacherAssignment, TeacherProfile

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.academics.models import AcademicYear, SchoolClass, Stream, Subject
    from apps.accounts.models import User


def ensure_teacher_profile(
    *,
    user: User,
    staff_id: str = "",
    qualification: str = "",
    department: str = "",
) -> TeacherProfile:
    if not staff_id:
        staff_id = f"TCH-{user.pk:05d}"
    if TeacherProfile.objects.filter(staff_id=staff_id).exclude(user=user).exists():
        raise ValidationError({"staff_id": "This staff ID is already in use."})
    profile, _ = TeacherProfile.objects.update_or_create(
        user=user,
        defaults={
            "staff_id": staff_id,
            "qualification": qualification,
            "department": department,
        },
    )
    return profile


@transaction.atomic
def assign_subject_to_teacher(
    *,
    teacher: TeacherProfile,
    school_class: SchoolClass,
    subject: Subject,
    academic_year: AcademicYear,
    stream: Stream | None = None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> TeacherAssignment:
    existing = TeacherAssignment.objects.filter(
        school_class=school_class,
        subject=subject,
        academic_year=academic_year,
        stream=stream,
    ).first()
    if existing and existing.teacher_id != teacher.pk:
        raise ValidationError(
            "This class/subject is already assigned to another teacher."
        )
    if existing and existing.teacher_id == teacher.pk:
        raise ValidationError("This assignment already exists.")

    assignment = TeacherAssignment.objects.create(
        teacher=teacher,
        school_class=school_class,
        subject=subject,
        academic_year=academic_year,
        stream=stream,
    )
    log_action(
        actor=actor,
        action="teacher.assigned",
        content_type="teachers.TeacherAssignment",
        object_id=assignment.pk,
        change_summary={
            "teacher": teacher.staff_id,
            "class": school_class.name,
            "subject": subject.code,
        },
        request=request,
    )
    return assignment


@transaction.atomic
def remove_assignment(
    *,
    assignment: TeacherAssignment,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> None:
    pk = assignment.pk
    assignment.delete()
    log_action(
        actor=actor,
        action="teacher.unassigned",
        content_type="teachers.TeacherAssignment",
        object_id=pk,
        change_summary={},
        request=request,
    )


@transaction.atomic
def set_class_teacher(
    *,
    school_class: SchoolClass,
    teacher_user: User | None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> SchoolClass:
    if teacher_user is not None and teacher_user.role != "teacher":
        raise ValidationError("Class teacher must have the teacher role.")
    school_class.class_teacher = teacher_user
    school_class.save(update_fields=["class_teacher", "updated_at"])
    log_action(
        actor=actor,
        action="class.class_teacher_set",
        content_type="academics.SchoolClass",
        object_id=school_class.pk,
        change_summary={
            "class": school_class.name,
            "teacher_id": teacher_user.pk if teacher_user else None,
        },
        request=request,
    )
    return school_class
