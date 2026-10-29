"""
apps/finance/tasks.py
----------------------
Background tasks for the finance module (django-q2).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def validate_fee_structure_upload(job_pk: int) -> None:
    """
    Parse an uploaded Excel file and populate preview_rows / error_rows.
    Called as a background task.
    """
    from .models import FeeStructureUploadJob
    from .services import validate_fee_structure_rows

    try:
        job = FeeStructureUploadJob.objects.get(pk=job_pk)
    except FeeStructureUploadJob.DoesNotExist:
        logger.error("FeeStructureUploadJob %s not found", job_pk)
        return

    job.status = FeeStructureUploadJob.Status.VALIDATING
    job.save(update_fields=["status"])

    try:
        import openpyxl

        wb = openpyxl.load_workbook(job.file.path)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header = [str(h).strip().lower() if h else "" for h in next(rows_iter, [])]

        raw_rows = []
        for row_values in rows_iter:
            row_dict = {}
            for i, key in enumerate(header):
                if i < len(row_values):
                    row_dict[key] = row_values[i]
            if any(v is not None for v in row_dict.values()):
                raw_rows.append(row_dict)

        valid, errors = validate_fee_structure_rows(raw_rows)
        job.preview_rows = valid
        job.error_rows = errors
        job.status = (
            FeeStructureUploadJob.Status.READY
            if not errors
            else FeeStructureUploadJob.Status.FAILED
        )
    except Exception as exc:
        logger.exception("Fee structure upload validation failed for job %s", job_pk)
        job.status = FeeStructureUploadJob.Status.FAILED
        job.error_rows = [{"_errors": [str(exc)]}]

    job.save(update_fields=["status", "preview_rows", "error_rows"])


def send_fee_reminder_task(
    *,
    student_ids: list[int],
    title: str,
    body: str,
    actor_id: int | None = None,
) -> None:
    """
    Background task: send fee reminder announcement.
    """
    from django.contrib.auth import get_user_model

    from apps.communications.services import create_announcement

    User = get_user_model()
    actor = User.objects.filter(pk=actor_id).first() if actor_id else None

    try:
        create_announcement(
            title=title,
            body=body,
            audience="by_role",
            target_roles=["student", "parent"],
            created_by=actor,
        )
        logger.info(
            "Fee reminder '%s' sent. actor=%s, student_ids=%s",
            title,
            actor_id,
            student_ids,
        )
    except Exception:
        logger.exception("Failed to send fee reminder '%s'", title)
