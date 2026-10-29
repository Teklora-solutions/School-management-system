"""
apps/core/tests/test_exports.py
--------------------------------
The guarantees every spreadsheet export is now built on: what a cell holds, and
what the file says about itself.

``append_rows`` is the formula-injection barrier, so these tests write the
payloads that used to become live formulas and read the saved file back to prove
the value an admin sees is the value that was stored — unchanged, and with no
apostrophe prepended to "fix" it.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase
from openpyxl import Workbook, load_workbook

from apps.core.exports import (
    XLSX_CONTENT_TYPE,
    append_rows,
    format_column_widths,
    is_formula_like,
    stamp_document,
    xlsx_response,
)

#: Strings that a spreadsheet would evaluate instead of display.
FORMULA_LIKE = ("=1+1", "-2+3", "@SUM(A1)", "\t=x", "+1")

NORMAL_ROW = ("Ama Mensah", "ADM/001", "3.5")


def _workbook_with(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Name", "Admission No.", "Score"])
    append_rows(ws, rows)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class IsFormulaLikeTests(SimpleTestCase):
    def test_formula_prefixes_are_detected(self):
        for value in FORMULA_LIKE:
            self.assertTrue(is_formula_like(value), value)

    def test_ordinary_data_is_not_formula_like(self):
        for value in ("Ama", "ADM/001", " 3.5", "hello", ""):
            self.assertFalse(is_formula_like(value), value)

    def test_non_strings_are_never_formula_like(self):
        for value in (1, 2.5, Decimal("1.5"), None, True):
            self.assertFalse(is_formula_like(value))


class AppendRowsTests(SimpleTestCase):
    def test_formula_looking_text_is_stored_as_literal_text(self):
        data = _workbook_with([list(FORMULA_LIKE)])
        ws = load_workbook(io.BytesIO(data)).active

        for column, expected in enumerate(FORMULA_LIKE, start=1):
            cell = ws.cell(row=2, column=column)
            self.assertEqual(cell.value, expected)
            self.assertNotIn(cell.data_type, ("f",))

    def test_no_apostrophe_is_prepended_to_the_value(self):
        data = _workbook_with([["=1+1"]])
        ws = load_workbook(io.BytesIO(data)).active
        self.assertNotIn("'", ws.cell(row=2, column=1).value)
        self.assertEqual(ws.cell(row=2, column=1).value, "=1+1")

    def test_saved_xml_holds_no_formula_element(self):
        data = _workbook_with([list(FORMULA_LIKE)])
        xml = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml")
        self.assertNotIn(b"<f>", xml)
        self.assertNotIn(b"<f ", xml)

    def test_non_string_values_round_trip(self):
        row = ["Kofi", Decimal("1.50"), date(2024, 1, 1), 42, ""]
        data = _workbook_with([row])
        ws = load_workbook(io.BytesIO(data)).active

        # xlsx stores a date as a serial and reads it back as midnight datetime,
        # and 1.5 == Decimal("1.50") numerically; the day itself is unharmed.
        self.assertEqual(ws["A2"].value, "Kofi")
        self.assertEqual(ws["B2"].value, Decimal("1.50"))
        self.assertEqual(ws["C2"].value.date(), date(2024, 1, 1))
        self.assertEqual(ws["D2"].value, 42)

    def test_every_row_is_appended_in_order_below_the_header(self):
        data = _workbook_with([NORMAL_ROW, ("Efua", "ADM/002", "4.0")])
        ws = load_workbook(io.BytesIO(data)).active
        self.assertEqual(ws.max_row, 3)
        self.assertEqual(ws["A2"].value, "Ama Mensah")
        self.assertEqual(ws["A3"].value, "Efua")


class FormatColumnWidthsTests(SimpleTestCase):
    def test_width_sized_to_the_longest_value(self):
        wb = Workbook()
        ws = wb.active
        append_rows(ws, [["abcdefghij"], ["x"]])
        format_column_widths(ws)
        self.assertEqual(ws.column_dimensions["A"].width, len("abcdefghij") + 4)

    def test_width_capped_at_max_width(self):
        wb = Workbook()
        ws = wb.active
        append_rows(ws, [["n" * 200]])
        format_column_widths(ws, max_width=30)
        self.assertEqual(ws.column_dimensions["A"].width, 30)


class StampDocumentTests(SimpleTestCase):
    class _Actor:
        username = "bursar"

    def test_properties_record_origin_and_requester(self):
        wb = Workbook()
        stamp_document(
            wb,
            title="Outstanding fees",
            source="Finance > Outstanding fees",
            actor=self._Actor(),
        )
        self.assertEqual(wb.properties.creator, "School Management System")
        self.assertEqual(wb.properties.lastModifiedBy, "bursar")
        self.assertEqual(wb.properties.title, "Outstanding fees")
        self.assertIn("Finance > Outstanding fees", wb.properties.description)
        self.assertIn("bursar", wb.properties.description)

    def test_missing_actor_is_recorded_as_system(self):
        wb = Workbook()
        stamp_document(wb, title="Staff directory", source="Director > Staff")
        self.assertEqual(wb.properties.lastModifiedBy, "system")
        self.assertIn("system", wb.properties.description)

    def test_properties_survive_the_saved_file(self):
        wb = Workbook()
        stamp_document(wb, title="Class performance", source="Director", actor=None)
        buf = io.BytesIO()
        wb.save(buf)
        reopened = load_workbook(io.BytesIO(buf.getvalue()))
        self.assertEqual(reopened.properties.title, "Class performance")
        self.assertEqual(reopened.properties.lastModifiedBy, "system")


class XlsxResponseTests(SimpleTestCase):
    def test_workbook_is_returned_as_a_download(self):
        wb = Workbook()
        response = xlsx_response(wb, "class_performance.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], XLSX_CONTENT_TYPE)
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="class_performance.xlsx"',
        )
        self.assertTrue(response.content[:2] == b"PK")
