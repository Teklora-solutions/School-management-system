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

A second engine keeps the button working on Windows hosts without GTK: when
WeasyPrint cannot load, the same HTML is printed by a headless Chromium/Edge
that is already installed. That is still a real PDF produced by a real layout
engine — not a disguised HTML response — so it satisfies the rule above. Set
``PDF_BROWSER_PATH`` to point at a specific browser; otherwise Chrome, Edge and
the usual Linux command names are searched.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.http import HttpResponse
from django.utils.html import escape

logger = logging.getLogger(__name__)

#: Shown to the requester when no PDF could be made. Plain text on purpose: an
#: HTML body here would be the same disguise this module exists to stop.
UNAVAILABLE_MESSAGE = (
    "No PDF was produced, so this download is not a document. "
    "Print the report from the screen instead, or ask an administrator to "
    "install the PDF engine and its system libraries (Pango/GTK), or a "
    "Chromium-based browser for the fallback engine.\n\n"
    "Reason: {reason}"
)

_BROWSER_ENV = "PDF_BROWSER_PATH"

#: Common Windows install locations, checked after PATH.
_BROWSER_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

_BROWSER_COMMANDS = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
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

    Tries WeasyPrint first, then a headless Chromium/Edge. Returns a 503 with a
    plain-text explanation when neither can produce a document; the reason is
    logged either way.
    """
    pdf: bytes | None = None
    failure = "the PDF engine is not installed on this server"

    try:
        from weasyprint import HTML
    except Exception as exc:  # noqa: BLE001
        logger.error("pdf_engine_unavailable: %s", exc)
    else:
        try:
            pdf = HTML(string=html, base_url=base_url).write_pdf()
        except Exception:
            logger.exception("pdf_render_failed: %s", filename)
            failure = "this document failed to render"

    if pdf is None:
        pdf = _browser_pdf(html, base_url)
        if pdf is not None:
            logger.info("pdf_rendered_by_browser: %s", filename)

    if pdf is None:
        return _unavailable(failure)

    response = HttpResponse(pdf, content_type="application/pdf")
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


def _find_browser() -> str | None:
    override = os.environ.get(_BROWSER_ENV, "").strip()
    if override:
        return override if Path(override).exists() else None
    for command in _BROWSER_COMMANDS:
        found = shutil.which(command)
        if found:
            return found
    for candidate in _BROWSER_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def _with_base_url(html: str, base_url: str | None) -> str:
    """Give the browser somewhere to resolve relative CSS/images from."""
    if not base_url or "<base " in html.lower():
        return html
    tag = f'<base href="{escape(base_url)}">'
    head = html.lower().find("<head>")
    if head == -1:
        return tag + html
    insert_at = head + len("<head>")
    return html[:insert_at] + tag + html[insert_at:]


def _browser_pdf(html: str, base_url: str | None) -> bytes | None:
    """
    Print ``html`` with a locally installed Chromium/Edge.

    Returns the PDF bytes, or ``None`` when no browser is installed or it did
    not produce a PDF. A subprocess, because that is how every current browser
    exposes "print to PDF" without a driver.
    """
    browser = _find_browser()
    if browser is None:
        return None

    with tempfile.TemporaryDirectory(
        prefix="sms-pdf-", ignore_cleanup_errors=True
    ) as tmp:
        workdir = Path(tmp)
        source = workdir / "document.html"
        target = workdir / "document.pdf"
        source.write_text(_with_base_url(html, base_url), encoding="utf-8")
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={workdir / 'profile'}",
            "--no-pdf-header-footer",
            f"--print-to-pdf={target}",
            source.as_uri(),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.error("pdf_browser_failed: %s", exc)
            return None

        if not target.exists():
            logger.error(
                "pdf_browser_no_output: rc=%s stderr=%s",
                completed.returncode,
                completed.stderr[-500:].decode("utf-8", "replace"),
            )
            return None
        data = target.read_bytes()
        if not data.startswith(b"%PDF"):
            logger.error("pdf_browser_not_a_pdf: %s bytes", len(data))
            return None
        return data


def _unavailable(reason: str) -> HttpResponse:
    return HttpResponse(
        UNAVAILABLE_MESSAGE.format(reason=reason),
        content_type="text/plain; charset=utf-8",
        status=503,
    )
