"""
apps/dashboards/tests/test_director_exports.py
-----------------------------------------------
The Director's download buttons go through the shared export helpers.

The helpers in ``apps.core`` are unit-tested where they live; what is tested
here is that the Director's own export views actually route through them: the
file arrives as a spreadsheet with provenance stamped in, formula-shaped text
from the database stays text, and a PDF that cannot be rendered fails loudly.
The PDF engine is stubbed either way, so these results do not depend on which
native libraries happen to be installed on the machine running them.
"""

from __future__ import annotations

import datetime
import io
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from apps.dashboards.tests.fixtures import (
    make_accounts,
    make_class,
    make_director,
    make_fee_structure,
    make_posted_invoice,
    make_student_user,
    make_term,
    make_user,
    make_year,
)
from apps.finance import services as finance_services
from apps.finance.models import Payment

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class _FakeHTML:
    def __init__(self, string=None, base_url=None):
        self.string = string

    def write_pdf(self):
        return b"%PDF-1.4\nstub\n"


class _BrokenHTML:
    def __init__(self, string=None, base_url=None):
        pass

    def write_pdf(self):
        raise RuntimeError("PDF engine down")


_working = patch.dict(sys.modules, {"weasyprint": SimpleNamespace(HTML=_FakeHTML)})
_broken = patch.dict(
    sys.modules, {"weasyprint": SimpleNamespace(HTML=_BrokenHTML)}
)


class DirectorExportTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="=Basic 5")
        cls.director = make_director(username="dir_export")
        cls.accounts = make_accounts()
        cls.structure = make_fee_structure(cls.school_class, cls.term, Decimal("500.00"))
        cls.student = make_student_user(
            cls.school_class, admission_number="EXP-001"
        )
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, Decimal("500.00")
        )
        finance_services.record_payment(
            student=cls.student,
            amount=Decimal("200.00"),
            method=Payment.Method.BANK,
            reference="EXP-PAY-1",
            payment_date=datetime.date(2025, 9, 10),
            recorded_by=cls.director,
        )

    def setUp(self):
        self.client.force_login(self.director)

    def download(self, name):
        response = self.client.get(reverse(name))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], XLSX)
        return load_workbook(io.BytesIO(response.content)), response


class SpreadsheetExportTests(DirectorExportTestBase):
    def test_fee_export_is_a_workbook_named_for_its_screen(self):
        workbook, response = self.download("dashboards:director_export_fees")
        self.assertIn("class_fee_collection.xlsx", response["Content-Disposition"])
        sheet = workbook.active
        self.assertEqual(sheet.title, "Fee Collection")
        self.assertEqual(
            [c.value for c in sheet[1]],
            [
                "Class",
                "Billed",
                "Collected",
                "Waived / Adjusted",
                "Outstanding",
                "Collection Rate (%)",
            ],
        )

    def test_a_class_name_that_looks_like_a_formula_stays_text(self):
        """
        The class is named ``=Basic 5`` in this fixture. Written straight into a
        sheet that string is a live formula the moment Excel opens the file, so
        the export has to store it as the text it is.
        """
        workbook, _ = self.download("dashboards:director_export_fees")
        cell = workbook.active["A2"]
        self.assertEqual(cell.value, "=Basic 5")
        self.assertEqual(cell.data_type, "s")

    def test_export_records_who_asked_for_it(self):
        workbook, _ = self.download("dashboards:director_export_fees")
        props = workbook.properties
        self.assertEqual(props.creator, "School Management System")
        self.assertEqual(props.lastModifiedBy, "dir_export")
        self.assertEqual(props.title, "Class fee collection")

    def test_performance_and_staff_exports_download(self):
        for name in (
            "dashboards:director_export_performance",
            "dashboards:director_export_staff",
        ):
            workbook, _ = self.download(name)
            self.assertEqual(workbook.properties.creator, "School Management System")

    def test_other_roles_cannot_use_a_director_export(self):
        self.client.force_login(make_user("admin", username="admin_export"))
        for name in (
            "dashboards:director_export_performance",
            "dashboards:director_export_fees",
            "dashboards:director_export_staff",
            "dashboards:director_export_books_pdf",
        ):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)


class DirectorPdfExportTests(DirectorExportTestBase):
    def test_books_pdf_when_the_engine_runs(self):
        with _working:
            response = self.client.get(reverse("dashboards:director_export_books_pdf"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_books_pdf_fails_loudly_when_the_engine_cannot_render(self):
        with _broken, patch(
            "apps.core.pdf._find_browser", return_value=None
        ), self.assertLogs("apps.core.pdf", "ERROR"):
            response = self.client.get(reverse("dashboards:director_export_books_pdf"))
        self.assertEqual(response.status_code, 503)
        self.assertIn("text/plain", response["Content-Type"])
        self.assertNotIn("text/html", response["Content-Type"])
