"""
apps/students/tasks.py
----------------------
Background tasks for bulk import validation (django-q2).
"""

from __future__ import annotations


def validate_bulk_import(job_id: int) -> None:
    """Parse uploaded Excel and populate preview/error rows on the job."""
    from openpyxl import load_workbook

    from .models import BulkImportJob
    from .services import BULK_TEMPLATE_HEADERS, validate_bulk_rows

    try:
        job = BulkImportJob.objects.select_related("target_class").get(pk=job_id)
    except BulkImportJob.DoesNotExist:
        return

    job.status = BulkImportJob.Status.VALIDATING
    job.save(update_fields=["status", "updated_at"])

    wb = None
    try:
        wb = load_workbook(job.file.path, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header = [str(c).strip() if c is not None else "" for c in next(rows_iter)]
        # Map header indices
        col_index = {h: i for i, h in enumerate(header)}
        missing = [h for h in BULK_TEMPLATE_HEADERS if h not in col_index]
        if missing:
            job.status = BulkImportJob.Status.FAILED
            job.error_rows = [
                {"_errors": [f"Missing columns: {', '.join(missing)}"], "_row": 1}
            ]
            job.summary = {"error": "invalid_template"}
            job.save()
            return

        raw_rows = []
        for values in rows_iter:
            if values is None or all(v is None or str(v).strip() == "" for v in values):
                continue
            row = {}
            for h in BULK_TEMPLATE_HEADERS:
                val = values[col_index[h]] if col_index[h] < len(values) else None
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                row[h] = val
            raw_rows.append(row)

        valid, errors = validate_bulk_rows(raw_rows, target_class=job.target_class)
        job.preview_rows = valid
        job.error_rows = errors
        job.summary = {
            "valid": len([r for r in valid if r.get("_action") == "create"]),
            "skipped_existing": len(
                [r for r in valid if r.get("_action") == "skip_existing"]
            ),
            "errors": len(errors),
            "total": len(raw_rows),
        }
        job.status = BulkImportJob.Status.READY
        job.save()
    except Exception as exc:  # noqa: BLE001 — surface to job
        job.status = BulkImportJob.Status.FAILED
        job.summary = {"error": str(exc)}
        job.error_rows = [{"_errors": [str(exc)], "_row": 0}]
        job.save()
    finally:
        if wb is not None:
            wb.close()
