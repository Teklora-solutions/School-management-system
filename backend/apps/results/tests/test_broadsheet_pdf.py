"""
apps/results/tests/test_broadsheet_pdf.py
------------------------------------------
RC2 — the broadsheet PDF export and the report-card buttons on the
on-screen broadsheet.

Covers:
  - BroadsheetPdfView renders landscape A4 with the identity header, the
    class/term/year/exam mean-score line, ranked rows, positions, the
    subject means/mean %/rank footer and the grade distribution
  - the download honours the same filters as the page and refuses to print
    an empty scope (redirect back with a message)
  - non-admins are denied
  - the broadsheet page offers the download and, once every sheet is
    published, a report-card PDF button per student
"""

from __future__ import annotations

import datetime
import sys
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.core.models import SchoolSettings
from apps.results.services import confirm_sheet, publish_sheet, submit_sheet

from .test_broadsheet import BroadsheetDataMixin

_rendered: list[str] = []


class _RecordingHTML:
    """Stands in for weasyprint.HTML and keeps the HTML it was handed."""

    def __init__(self, string=None, base_url=None):
        _rendered.append(string or "")

    def write_pdf(self):
        return b"%PDF-1.4\nstub\n"


_recorder = patch.dict(sys.modules, {"weasyprint": SimpleNamespace(HTML=_RecordingHTML)})


class BroadsheetPdfTests(BroadsheetDataMixin, TestCase):
    def _query(self) -> dict:
        return {
            "year": self.year.pk,
            "class": self.school_class.pk,
            "term": self.term.pk,
            "exam": self.exam.pk,
        }

    def _html_as(self, user) -> str:
        _rendered.clear()
        self.client.force_login(user)
        with _recorder:
            response = self.client.get(reverse("results:broadsheet_pdf"), self._query())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(_rendered)
        return _rendered[-1]

    def setUp(self):
        SchoolSettings.objects.create(pk=SchoolSettings.SINGLETON_PK, name="PDF School")

    def test_pdf_carries_identity_meta_line_and_table(self):
        html = self._html_as(self.admin)
        self.assertIn("A4 landscape", html)
        self.assertIn("PDF School", html)
        self.assertIn("Grade 5", html)
        self.assertIn("Term 1", html)
        self.assertIn("Exam 1", html)
        # Mean of totals 173.3; mean of student means 86.7%.
        self.assertIn("173.30", html)
        self.assertIn("86.70%", html)
        self.assertIn("Student Name", html)
        self.assertIn("ALICE A", html)
        self.assertIn("ADM-001", html)
        self.assertIn("Subject Mean", html)
        self.assertIn("Subject Mean %", html)
        self.assertIn("Subject Rank", html)
        self.assertIn("Grade Distribution", html)

    def test_rows_are_ranked_with_positions_and_subject_means(self):
        html = self._html_as(self.admin)
        body = html.split("<tbody>")[1].split("</tbody>")[0]
        self.assertLess(body.index("ALICE A"), body.index("BOB B"))
        self.assertLess(body.index("BOB B"), body.index("CARA C"))
        # Alice is position 1; Bob and Cara tie at 170 and share position 2.
        squashed = html.replace(" ", "").replace("\n", "")
        self.assertIn('strong">1<', squashed)
        self.assertIn('strong">2<', squashed)
        self.assertNotIn('strong">3<', squashed)
        # ENG and MATH both average (90 + 90 + 80) / 3 = 86.7.
        self.assertIn("86.7", squashed)

    def test_show_positions_off_hides_the_position_column(self):
        SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).update(
            show_positions=False
        )
        html = self._html_as(self.admin)
        self.assertNotIn(">Pos</th>", html)

    def test_rows_still_print_without_scores(self):
        from apps.results.models import ScoreEntry

        ScoreEntry.objects.all().delete()
        _rendered.clear()
        self.client.force_login(self.admin)
        with _recorder:
            response = self.client.get(reverse("results:broadsheet_pdf"), self._query())
        self.assertEqual(response.status_code, 200)
        # Dan D has no scores: he is listed but unranked.
        self.assertIn("DAN D", _rendered[-1])
        # With no marks there is nothing to distribute.
        self.assertNotIn("Grade Distribution", _rendered[-1])

    def test_missing_scope_redirects_back_with_a_message(self):
        empty_year = self.year.__class__.objects.create(
            name="Empty Year",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 7, 31),
        )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("results:broadsheet_pdf"), {"year": empty_year.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("results:broadsheet"))

    def test_non_admin_is_denied(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("results:broadsheet_pdf"), self._query())
        self.assertEqual(response.status_code, 403)


class BroadsheetCardButtonTests(BroadsheetDataMixin, TestCase):
    def _publish_everything(self):
        for sheet in (self.math_sheet, self.eng_sheet):
            submit_sheet(sheet=sheet, actor=self.admin)
            confirm_sheet(sheet=sheet, actor=self.admin)
            publish_sheet(sheet=sheet, actor=self.admin)

    def test_download_button_is_offered_with_current_filters(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("results:broadsheet"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        expected = (
            reverse("results:broadsheet_pdf")
            + f"?year={self.year.pk}&class={self.school_class.pk}"
            + f"&term={self.term.pk}&exam={self.exam.pk}"
        )
        self.assertIn(expected, content)

    def test_card_buttons_appear_only_once_everything_is_published(self):
        self.client.force_login(self.admin)
        before = self.client.get(reverse("results:broadsheet")).content.decode()
        self.assertIn("bi-file-earmark-pdf", before)  # the Download PDF button
        self.assertNotIn("report_card_pdf", before)

        self._publish_everything()
        after = self.client.get(reverse("results:broadsheet")).content.decode()
        self.assertIn(
            reverse("results:report_card_pdf", args=[self.alice.pk, self.term.pk]),
            after,
        )
