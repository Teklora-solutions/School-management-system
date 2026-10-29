"""
apps/academics/selectors.py
"""

from __future__ import annotations

from django.db.models import Prefetch, QuerySet

from .models import AcademicYear, ClassSubject, SchoolClass, Subject, Term


def academic_year_list() -> QuerySet:
    return AcademicYear.objects.prefetch_related("terms").all()


def get_current_year() -> AcademicYear | None:
    return AcademicYear.objects.filter(is_current=True).first()


def get_current_term() -> Term | None:
    return Term.objects.filter(is_current=True).select_related("academic_year").first()


def terms_for_year(year_id: int) -> QuerySet:
    return Term.objects.filter(academic_year_id=year_id)


def class_list(*, year_id: int | None = None) -> QuerySet:
    qs = SchoolClass.objects.select_related(
        "academic_year", "class_teacher"
    ).prefetch_related("streams")
    if year_id:
        qs = qs.filter(academic_year_id=year_id)
    return qs


def get_class(pk: int) -> SchoolClass:
    return (
        SchoolClass.objects.select_related("academic_year", "class_teacher")
        .prefetch_related("streams")
        .get(pk=pk)
    )


def subject_list(*, active_only: bool = True) -> QuerySet:
    qs = Subject.objects.all()
    if active_only:
        qs = qs.filter(is_active=True)
    return qs


def class_subject_list(
    *, class_id: int | None = None, year_id: int | None = None
) -> QuerySet:
    qs = ClassSubject.objects.select_related("school_class", "subject", "stream")
    if class_id:
        qs = qs.filter(school_class_id=class_id)
    if year_id:
        qs = qs.filter(school_class__academic_year_id=year_id)
    return qs


def year_with_structure(year_id: int) -> AcademicYear:
    return AcademicYear.objects.prefetch_related(
        "terms",
        Prefetch(
            "classes",
            queryset=SchoolClass.objects.prefetch_related(
                "streams", "class_subjects__subject"
            ),
        ),
    ).get(pk=year_id)
