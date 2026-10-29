"""
apps/core/exports.py
---------------------
Spreadsheet output built one way, everywhere.

Two problems are solved here rather than at each call site.

**Formula injection.** A string that starts with ``=`` written into an .xlsx by
openpyxl becomes a *live formula* in the file, not text — and Excel evaluates
what it reads when the workbook opens. Every export in this system mixes in text
somebody else typed: a student's name from a bulk-import file, a fee line label,
a phone number. So an upload whose cell read ``=cmd|' /C calc'!A0`` used to turn
into a running command the morning an admin opened the error report to find out
what had gone wrong with the upload. ``append_rows`` writes every string as
literal text by setting the cell's type rather than mangling its content: the
value an admin reads is the value that was stored, with nothing prepended and
nothing removed.

**Provenance.** A workbook that leaves the building says where it came from and
who asked for it, in the file's own properties, so a figure in an attachment can
be traced back to the screen that produced it.
"""

from __future__ import annotations

import io

from django.http import HttpResponse
from django.utils import timezone

XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

#: Prefixes that turn a string into something a spreadsheet may evaluate.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def is_formula_like(value) -> bool:
    """True for text a spreadsheet would treat as a formula rather than as data."""
    return isinstance(value, str) and value.lstrip().startswith(FORMULA_PREFIXES)


def start_workbook(sheet_title: str):
    """
    Start a one-sheet workbook, titled, with the sheet as ``.active``.

    Callers get this instead of importing openpyxl themselves so no view outside
    this module ever has to know which library produces the file.
    """
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.active.title = sheet_title
    return workbook


def append_rows(worksheet, rows) -> None:
    """
    Append ``rows`` (an iterable of sequences), storing every cell as the value
    it holds. Formula-looking strings stay text, so they display instead of
    running.
    """
    for values in rows:
        worksheet.append(list(values))
        for cell in worksheet[worksheet.max_row]:
            if is_formula_like(cell.value):
                cell.data_type = "s"


def format_column_widths(worksheet, *, max_width: int = 40) -> None:
    """Size each column to its content, capped, so exports arrive readable."""
    for column in worksheet.columns:
        longest = max(
            (len(str(cell.value)) for cell in column if cell.value not in (None, "")),
            default=10,
        )
        worksheet.column_dimensions[column[0].column_letter].width = min(
            longest + 4, max_width
        )


def stamp_document(workbook, *, title: str, source: str, actor=None) -> None:
    """
    Record what this workbook is and who exported it in the file's own
    properties — visible to the recipient, without a watermark over the data.
    """
    username = getattr(actor, "username", None) or "system"
    workbook.properties.creator = "School Management System"
    workbook.properties.lastModifiedBy = username
    workbook.properties.title = title
    workbook.properties.description = (
        f"{source} — exported by {username} on "
        f"{timezone.localtime().strftime('%Y-%m-%d %H:%M %Z')}"
    )


def xlsx_response(workbook, filename: str) -> HttpResponse:
    """Save ``workbook`` and hand it back as a download."""
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    response = HttpResponse(buffer.read(), content_type=XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
