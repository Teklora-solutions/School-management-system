"""
apps/academics/services.py
--------------------------
Write operations for academic structure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.core.audit_log import log_action

from . import calendar
from .models import AcademicYear, ClassSubject, SchoolClass, Stream, Subject, Term

if TYPE_CHECKING:
    import datetime

    from django.http import HttpRequest

    from apps.accounts.models import User


@transaction.atomic
def create_academic_year(
    *,
    name: str,
    start_date,
    end_date,
    is_current: bool = False,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> AcademicYear:
    year = AcademicYear(
        name=name, start_date=start_date, end_date=end_date, is_current=is_current
    )
    year.full_clean()
    year.save()
    log_action(
        actor=actor,
        action="academics.year_created",
        content_type="academics.AcademicYear",
        object_id=year.pk,
        change_summary={"name": name, "is_current": is_current},
        request=request,
    )
    return year


@transaction.atomic
def set_current_year(
    *,
    year: AcademicYear,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> AcademicYear:
    year.is_current = True
    year.save()
    log_action(
        actor=actor,
        action="academics.year_set_current",
        content_type="academics.AcademicYear",
        object_id=year.pk,
        change_summary={"name": year.name},
        request=request,
    )
    return year


@transaction.atomic
def create_term(
    *,
    academic_year: AcademicYear,
    name: str,
    start_date,
    end_date,
    is_current: bool = False,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Term:
    term = Term(
        academic_year=academic_year,
        name=name,
        start_date=start_date,
        end_date=end_date,
        is_current=is_current,
    )
    term.full_clean()
    term.save()
    log_action(
        actor=actor,
        action="academics.term_created",
        content_type="academics.Term",
        object_id=term.pk,
        change_summary={"year": academic_year.name, "name": name},
        request=request,
    )
    return term


@transaction.atomic
def set_current_term(
    *,
    term: Term,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Term:
    term.is_current = True
    term.save()
    log_action(
        actor=actor,
        action="academics.term_set_current",
        content_type="academics.Term",
        object_id=term.pk,
        change_summary={"term": str(term)},
        request=request,
    )
    return term


@transaction.atomic
def create_academic_year_with_terms(
    *,
    start_year: int,
    is_current: bool = False,
    actor: User | None = None,
    request: HttpRequest | None = None,
    today: datetime.date | None = None,
) -> AcademicYear:
    """Create a year and the three terms inside it, from the shared calendar.

    When the year becomes the current one and today falls inside one of its term
    windows, that window also becomes the current term; otherwise no term is
    flagged, because a year planned for the future has no term running yet.
    """
    windows = calendar.term_windows(start_year)
    year = create_academic_year(
        name=calendar.year_name(start_year),
        start_date=windows[0][1],
        end_date=windows[-1][2],
        is_current=is_current,
        actor=actor,
        request=request,
    )

    running = calendar.slot_running_on(
        start_year, today if today is not None else timezone.localdate()
    )
    for slot, opening, closing in windows:
        create_term(
            academic_year=year,
            name=slot,
            start_date=opening,
            end_date=closing,
            is_current=is_current and slot == running,
            actor=actor,
            request=request,
        )
    return year


@transaction.atomic
def update_term_dates(
    *,
    term: Term,
    start_date,
    end_date,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Term:
    """The default windows are a guess, so the admin can correct them per term."""
    term.start_date = start_date
    term.end_date = end_date
    term.full_clean()
    term.save()
    log_action(
        actor=actor,
        action="academics.term_updated",
        content_type="academics.Term",
        object_id=term.pk,
        change_summary={
            "term": str(term),
            "start_date": str(start_date),
            "end_date": str(end_date),
        },
        request=request,
    )
    return term


@transaction.atomic
def delete_academic_year(
    *,
    year: AcademicYear,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> None:
    """Delete a year and the scaffolding under it, or refuse with a reason.

    Removing a year cascades to its terms, classes, streams, subject maps and
    teacher assignments, so it is blocked while it is the current year and while
    anything financial or academic still points at it (those are PROTECT
    relationships, caught here rather than enumerated — a new PROTECT FK is
    refused automatically).
    """
    if year.is_current:
        raise ValidationError(
            "Set another academic year as current before deleting this one."
        )
    label = year.name
    try:
        year.delete()
    except ProtectedError as exc:
        raise ValidationError(
            f"'{label}' still has fees, invoices, promotions or results attached "
            "to it, so it cannot be deleted. Remove those first, or leave the "
            "year in place and stop using it."
        ) from exc
    log_action(
        actor=actor,
        action="academics.year_deleted",
        content_type="academics.AcademicYear",
        object_id=None,
        change_summary={"name": label},
        request=request,
    )


@transaction.atomic
def create_class(
    *,
    academic_year: AcademicYear,
    name: str,
    level_order: int,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> SchoolClass:
    obj = SchoolClass(academic_year=academic_year, name=name, level_order=level_order)
    obj.full_clean()
    obj.save()
    log_action(
        actor=actor,
        action="academics.class_created",
        content_type="academics.SchoolClass",
        object_id=obj.pk,
        change_summary={"name": name, "year": academic_year.name},
        request=request,
    )
    return obj


@transaction.atomic
def create_stream(
    *,
    school_class: SchoolClass,
    name: str,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Stream:
    stream = Stream(school_class=school_class, name=name)
    stream.full_clean()
    stream.save()
    log_action(
        actor=actor,
        action="academics.stream_created",
        content_type="academics.Stream",
        object_id=stream.pk,
        change_summary={"class": school_class.name, "name": name},
        request=request,
    )
    return stream


@transaction.atomic
def create_subject(
    *,
    name: str,
    code: str,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Subject:
    code = code.strip().upper()
    subject = Subject(name=name.strip(), code=code)
    subject.full_clean()
    subject.save()
    log_action(
        actor=actor,
        action="academics.subject_created",
        content_type="academics.Subject",
        object_id=subject.pk,
        change_summary={"code": code, "name": name},
        request=request,
    )
    return subject


@transaction.atomic
def map_class_subject(
    *,
    school_class: SchoolClass,
    subject: Subject,
    stream: Stream | None = None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> ClassSubject:
    if ClassSubject.objects.filter(
        school_class=school_class, subject=subject, stream=stream
    ).exists():
        raise ValidationError("This class–subject mapping already exists.")
    mapping = ClassSubject(school_class=school_class, subject=subject, stream=stream)
    mapping.save()
    log_action(
        actor=actor,
        action="academics.class_subject_mapped",
        content_type="academics.ClassSubject",
        object_id=mapping.pk,
        change_summary={
            "class": school_class.name,
            "subject": subject.code,
            "stream": stream.name if stream else None,
        },
        request=request,
    )
    return mapping


@transaction.atomic
def remove_class_subject(
    *,
    mapping: ClassSubject,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> None:
    pk = mapping.pk
    mapping.delete()
    log_action(
        actor=actor,
        action="academics.class_subject_removed",
        content_type="academics.ClassSubject",
        object_id=pk,
        change_summary={},
        request=request,
    )
