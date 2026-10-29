"""
apps/core/tests/test_pdf.py
----------------------------
``pdf_response`` answers with a PDF or with a failure — never with HTML wearing
a PDF's content type.

The engine is stubbed for the success path and broken deliberately for the
failure path, so these assertions hold on a machine that cannot import
WeasyPrint at all (which is the machine this suite runs on).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.pdf import pdf_response

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
