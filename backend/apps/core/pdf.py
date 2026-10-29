"""
apps/core/pdf.py
-----------------
PDF responses that admit it when they cannot make a PDF.

WeasyPrint needs native libraries (Pango, GTK) that are not present on every
machine this project runs on. When they are missing, the honest answer is a
failure — not a page of HTML returned with a 200 from a URL whose name ends in
``.pdf``. The old behaviour did exactly that, so a receipt or a report card that
was never rendered looked, to the person who clicked the button, like a document
that had been produced. Money and grades are the two things in this system that
must not have a "probably fine" version: a headteacher who forwards what they
believe is the official receipt and finds markup instead stops trusting the
figures, and a file that quietly differs from the screen cannot be reconciled.

So ``pdf_response`` returns a real PDF or a 503 that says which, and logs the
reason with a traceback either way.
"""

from __future__ import annotations

import logging

from django.http import HttpResponse

logger = logging.getLogger(__name__)

#: Shown to the requester when no PDF could be made. Plain text on purpose: an
#: HTML body here would be the same disguise this module exists to stop.
UNAVAILABLE_MESSAGE = (
    "No PDF was produced, so this download is not a document. "
    "Print the report from the screen instead, or ask an administrator to "
    "install the PDF engine and its system libraries (Pango/GTK).\n\n"
    "Reason: {reason}"
)


def pdf_response(
    *,
    html: str,
    filename: str,
    base_url: str | None = None,
    inline: bool = True,
) -> HttpResponse:
    """
    Render ``html`` into a PDF response.

    Returns a 503 with a plain-text explanation when WeasyPrint cannot be
    imported or cannot render this document; the reason is logged either way.
    """
    try:
        from weasyprint import HTML
    except Exception as exc:  # noqa: BLE001
        logger.error("pdf_engine_unavailable: %s", exc)
        return _unavailable("the PDF engine is not installed on this server")

    try:
        pdf = HTML(string=html, base_url=base_url).write_pdf()
    except Exception:
        logger.exception("pdf_render_failed: %s", filename)
        return _unavailable("this document failed to render")

    response = HttpResponse(pdf, content_type="application/pdf")
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


def _unavailable(reason: str) -> HttpResponse:
    return HttpResponse(
        UNAVAILABLE_MESSAGE.format(reason=reason),
        content_type="text/plain; charset=utf-8",
        status=503,
    )
