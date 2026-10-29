"""
apps/students/selectors.py
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from .models import BulkImportJob, PromotionBatch, Student


def student_list(
    *,
    search: str = "",
    class_id: int | None = None,
    status: str = "",
    year_id: int | None = None,
) -> QuerySet:
    qs = Student.objects.select_related(
        "user", "current_class", "current_stream", "current_class__academic_year"
    ).prefetch_related("guardian_links__parent__user")
    if search:
        qs = qs.filter(
            Q(admission_number__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(user__email__icontains=search)
        )
    if class_id:
        qs = qs.filter(current_class_id=class_id)
    if status:
        qs = qs.filter(status=status)
    if year_id:
        qs = qs.filter(current_class__academic_year_id=year_id)
    return qs


def get_student(pk: int) -> Student:
    return (
        Student.objects.select_related("user", "current_class", "current_stream")
        .prefetch_related("guardian_links__parent__user")
        .get(pk=pk)
    )


def student_count_active() -> int:
    return Student.objects.filter(
        status__in=[Student.Status.ACTIVE, Student.Status.REPEATING]
    ).count()


def get_bulk_job(pk: int) -> BulkImportJob:
    return BulkImportJob.objects.select_related("target_class", "uploaded_by").get(
        pk=pk
    )


def bulk_import_rows(job: BulkImportJob) -> dict[str, list[dict]]:
    """
    Preview/error rows in a form a template can read.

    The stored JSON keys start with an underscore (``_row``, ``_action``,
    ``_errors``) and Django templates refuse to resolve those, so they are
    re-exposed here under plain names.
    """

    def display(row: dict) -> dict:
        return {
            **row,
            "row_number": row.get("_row"),
            "action": row.get("_action"),
            "errors": row.get("_errors") or [],
        }

    return {
        "preview_rows": [display(r) for r in job.preview_rows],
        "error_rows": [display(r) for r in job.error_rows],
    }


def promotion_batch_list() -> QuerySet:
    return PromotionBatch.objects.select_related(
        "from_year", "to_year", "term", "committed_by"
    ).prefetch_related("records")


def get_promotion_batch(pk: int) -> PromotionBatch:
    return (
        PromotionBatch.objects.select_related("from_year", "to_year", "term")
        .prefetch_related(
            "records__student__user", "records__from_class", "records__to_class"
        )
        .get(pk=pk)
    )
