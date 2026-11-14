"""
apps/gradebook/export_views.py
------------------------------
The teacher's download of one classroom's grid, as CSV or Excel.

The export is scoped through the signed-in teacher's own assignment — the
setup id in the URL can never widen it — and it is the same grid the screen
shows, so a figure in the file can be traced back to the page it came from.
"""

from __future__ import annotations

from django.http import Http404
from django.views import View

from apps.core.exports import (
    append_rows,
    csv_response,
    format_column_widths,
    stamp_document,
    start_workbook,
    xlsx_response,
)
from apps.core.mixins import TeacherRequiredMixin

from . import services
from .setup_views import _scoped_setup
from .views import GradebookEnabledMixin


def _header(grid: dict) -> list:
    return (
        ["Pupil", "Admission"]
        + [
            f"{item['title']} ({item['bucket_label']}, out of "
            f"{services.num(item['max_score'])})"
            for item in grid["items"]
        ]
        + [f"{bucket['label']} (%)" for bucket in grid["buckets"]]
        + ["Overall (%)"]
    )


def _rows(grid: dict) -> list[list]:
    rows = []
    for row in grid["rows"]:
        cells = []
        for cell in row["cells_list"]:
            if cell["is_excused"]:
                cells.append("Excused")
            elif cell["is_missing"]:
                cells.append("")
            else:
                cells.append(cell["display"])
        rows.append(
            [
                row["student"].user.get_full_name()
                or row["student"].user.username,
                row["student"].admission_number,
                *cells,
                *[
                    "" if pct is None else float(pct)
                    for pct in row["bucket_values"]
                ],
                "" if row["overall_pct"] is None else float(row["overall_pct"]),
            ]
        )
    return rows


class TeacherGradebookExportView(TeacherRequiredMixin, GradebookEnabledMixin, View):
    def get(self, request, setup_id: int, fmt: str):
        if fmt not in ("csv", "xlsx"):
            raise Http404("Unknown export format.")
        setup = _scoped_setup(request, setup_id)
        grid = services.build_grid(setup)

        filename = (
            f"gradebook_{setup.school_class.name}_{setup.subject.code}_"
            f"{setup.term.name}.{fmt}"
        ).replace("/", "-").replace(" ", "_")
        header = _header(grid)
        rows = _rows(grid)

        if fmt == "csv":
            return csv_response([header, *rows], filename)

        workbook = start_workbook("Gradebook")
        worksheet = workbook.active
        worksheet.append(header)
        append_rows(worksheet, rows)
        format_column_widths(worksheet)
        stamp_document(
            workbook,
            title=(
                f"Gradebook — {setup.school_class.name} "
                f"{setup.subject.name} {setup.term.get_name_display()}"
            ),
            source="Gradebook > Export",
            actor=request.user,
        )
        return xlsx_response(workbook, filename)
