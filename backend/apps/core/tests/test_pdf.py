"""
apps/core/tests/test_pdf.py
----------------------------
``pdf_response`` answers with a PDF or with a failure — never with HTML wearing
a PDF's content type.

The engine is stubbed for the success path and broken deliberately for the
failure path, so these assertions hold on a machine that cannot import
WeasyPrint at all (which is the machine this suite runs on). The Chromium
fallback is stubbed too: its tests must not launch a real browser.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse
from urllib.request import url2pathname

from django.test import SimpleTestCase

from apps.core.pdf import _with_base_url, pdf_response

HTML_BODY = "<html><body>Receipt for fee payment ADM/001</body></html>"


class _FakeHTML:
    def __init__(self, string=None, base_url=None):
        self.string = string
        self.base_url = base_url

    def write_pdf(self):
        return b"%PDF-1.4\nstub\n"


class _ExplodingHTML:
    def __init__(self, string=None, base_url=None):
        raise RuntimeError("PDF engine down")


_engine = patch.dict(sys.modules, {"weasyprint": SimpleNamespace(HTML=_FakeHTML)})
_broken_engine = patch.dict(
    sys.modules, {"weasyprint": SimpleNamespace(HTML=_ExplodingHTML)}
)
_no_engine = patch.dict(sys.modules, {"weasyprint": None})


class PdfSuccessTests(SimpleTestCase):
    def test_stubbed_engine_returns_a_pdf(self):
        with _engine:
            response = pdf_response(html=HTML_BODY, filename="receipt_R1.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_inline_by_default_and_attachment_on_request(self):
        with _engine:
            inline = pdf_response(html=HTML_BODY, filename="report.pdf")
            attached = pdf_response(html=HTML_BODY, filename="report.pdf", inline=False)
        self.assertEqual(inline["Content-Disposition"], 'inline; filename="report.pdf"')
        self.assertEqual(
            attached["Content-Disposition"], 'attachment; filename="report.pdf"'
        )

    def test_base_url_is_passed_through_to_the_engine(self):
        seen = {}

        class _RecordingHTML(_FakeHTML):
            def __init__(self, string=None, base_url=None):
                super().__init__(string=string, base_url=base_url)
                seen["base_url"] = base_url

        engine = patch.dict(
            sys.modules, {"weasyprint": SimpleNamespace(HTML=_RecordingHTML)}
        )
        with engine:
            response = pdf_response(
                html=HTML_BODY, filename="report.pdf", base_url="http://school.test/"
            )
        self.assertEqual(seen["base_url"], "http://school.test/")
        self.assertEqual(response.status_code, 200)


class PdfFailureTests(SimpleTestCase):
    def setUp(self):
        no_browser = patch("apps.core.pdf._find_browser", return_value=None)
        no_browser.start()
        self.addCleanup(no_browser.stop)

    def test_missing_engine_returns_503_and_logs(self):
        with _no_engine, self.assertLogs("apps.core.pdf", "ERROR") as logs:
            response = pdf_response(html=HTML_BODY, filename="receipt_R1.pdf")
        self.assertEqual(response.status_code, 503)
        self.assertIn("text/plain", response["Content-Type"])
        self.assertIn("No PDF was produced", response.content.decode())
        self.assertIn("pdf_engine_unavailable", "\n".join(logs.output))

    def test_render_failure_returns_503_with_a_traceback_logged(self):
        with _broken_engine, self.assertLogs("apps.core.pdf", "ERROR") as logs:
            response = pdf_response(html=HTML_BODY, filename="report_card.pdf")
        self.assertEqual(response.status_code, 503)
        self.assertIn("text/plain", response["Content-Type"])
        self.assertIn("No PDF was produced", response.content.decode())
        self.assertIn("Traceback", "\n".join(logs.output))

    def test_the_html_is_never_served_in_the_body(self):
        for engine in (_no_engine, _broken_engine):
            with engine, self.assertLogs("apps.core.pdf", "ERROR"):
                response = pdf_response(html=HTML_BODY, filename="statement.pdf")
            self.assertNotIn("ADM/001", response.content.decode())
            self.assertNotIn("<html>", response.content.decode())
            self.assertNotIn("text/html", response["Content-Type"])

    def test_no_html_is_ever_returned_with_a_200(self):
        for engine in (_no_engine, _broken_engine):
            with engine, self.assertLogs("apps.core.pdf", "ERROR"):
                response = pdf_response(html=HTML_BODY, filename="books.pdf")
            self.assertNotEqual(response.status_code, 200)


def _print_to_pdf_target(command: list[str]) -> Path:
    argument = next(
        item for item in command if item.startswith("--print-to-pdf=")
    )
    return Path(argument.split("=", 1)[1])


def _browser_writes_a_pdf(command, **_kwargs):
    _print_to_pdf_target(command).write_bytes(b"%PDF-1.7\nchromium\n")
    return subprocess.CompletedProcess(command, 0, b"", b"")


def _browser_writes_html(command, **_kwargs):
    _print_to_pdf_target(command).write_text("<html>not a pdf</html>", encoding="utf-8")
    return subprocess.CompletedProcess(command, 0, b"", b"")


class PdfBrowserFallbackTests(SimpleTestCase):
    def setUp(self):
        browser = patch(
            "apps.core.pdf._find_browser", return_value=r"C:\chrome.exe"
        )
        browser.start()
        self.addCleanup(browser.stop)

    def test_missing_weasyprint_still_prints_with_the_browser(self):
        with _no_engine, patch(
            "apps.core.pdf.subprocess.run", side_effect=_browser_writes_a_pdf
        ):
            response = pdf_response(html=HTML_BODY, filename="receipt_R1.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_render_failure_falls_back_to_the_browser(self):
        with _broken_engine, patch(
            "apps.core.pdf.subprocess.run", side_effect=_browser_writes_a_pdf
        ):
            response = pdf_response(html=HTML_BODY, filename="report_card.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_the_browser_is_given_a_headless_print_without_headers(self):
        seen = {}

        def run(command, **_kwargs):
            seen["command"] = command
            uri = command[-1]
            seen["html"] = Path(url2pathname(urlparse(uri).path)).read_text(
                encoding="utf-8"
            )
            return _browser_writes_a_pdf(command)

        with _no_engine, patch("apps.core.pdf.subprocess.run", side_effect=run):
            pdf_response(
                html=HTML_BODY,
                filename="broadsheet.pdf",
                base_url="http://school.test/",
            )
        self.assertIn("--headless=new", seen["command"])
        self.assertIn("--no-pdf-header-footer", seen["command"])
        self.assertIn(
            '<base href="http://school.test/">', seen["html"]
        )

    def test_browser_output_that_is_not_a_pdf_is_not_served(self):
        with _no_engine, patch(
            "apps.core.pdf.subprocess.run", side_effect=_browser_writes_html
        ), self.assertLogs("apps.core.pdf", "ERROR") as logs:
            response = pdf_response(html=HTML_BODY, filename="statement.pdf")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("<html>", response.content.decode())
        self.assertIn("pdf_browser_not_a_pdf", "\n".join(logs.output))

    def test_a_missing_browser_falls_through_to_the_503(self):
        with _no_engine, patch(
            "apps.core.pdf._find_browser", return_value=None
        ), patch("apps.core.pdf.subprocess.run") as run:
            response = pdf_response(html=HTML_BODY, filename="statement.pdf")
        run.assert_not_called()
        self.assertEqual(response.status_code, 503)


class BaseUrlInjectionTests(SimpleTestCase):
    def test_base_is_inserted_right_after_head(self):
        html = '<html><head><meta charset="utf-8"></head><body></body></html>'
        self.assertIn(
            '<head><base href="http://school.test/">',
            _with_base_url(html, "http://school.test/"),
        )

    def test_an_existing_base_is_left_alone(self):
        html = '<html><head><base href="/static/"></head></html>'
        self.assertEqual(_with_base_url(html, "http://school.test/"), html)

    def test_no_base_url_leaves_the_document_untouched(self):
        self.assertEqual(_with_base_url(HTML_BODY, None), HTML_BODY)
