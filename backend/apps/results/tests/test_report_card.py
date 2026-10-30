"""
apps/results/tests/test_report_card.py
---------------------------------------
RC1 — the redesigned report card PDF.

Covers:
  - result_identity reads SchoolSettings (row wins, blanks fall back to env,
    absolute logo URL, show_positions flag)
  - student_term_position ranks by average of published totals (ties share)
  - ClassTeacherRemark is one row per (student, term)
  - the PDF template carries the identity header, summary band, class
    teacher name, remark box, signature block, and honours show_positions
"""

from __future__ import annotations

import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.core.models import SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_grading_config,
    make_published_score,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)

from .. import selectors
from ..models import ClassTeacherRemark

#: 1x1 transparent PNG.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

_rendered: list[str] = []


class _RecordingHTML:
    """Stands in for weasyprint.HTML and keeps the HTML it was handed."""

    def __init__(self, string=None, base_url=None):
        _rendered.append(string or "")

    def write_pdf(self):
        return b"%PDF-1.4\nstub\n"


_recorder = patch.dict(sys.modules, {"weasyprint": SimpleNamespace(HTML=_RecordingHTML)})


class ResultIdentityTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")

    def _row(self, **kwargs) -> SchoolSettings:
        kwargs.setdefault("pk", SchoolSettings.SINGLETON_PK)
        return SchoolSettings.objects.create(**kwargs)

    @override_settings(SCHOOL_NAME="Env School", SCHOOL_MOTTO="Env Motto")
    def test_the_settings_row_wins_over_the_environment(self):
        self._row(name="Row School", motto="Row Motto")
        identity = selectors.result_identity(request=self.request)
        self.assertEqual(identity["name"], "Row School")
        self.assertEqual(identity["motto"], "Row Motto")

    @override_settings(SCHOOL_NAME="Env School", SCHOOL_MOTTO="Env Motto")
    def test_blank_fields_fall_back_to_the_environment(self):
        self._row(name="", motto="")
        identity = selectors.result_identity(request=self.request)
        self.assertEqual(identity["name"], "Env School")
        self.assertEqual(identity["motto"], "Env Motto")

    @override_settings(SCHOOL_NAME="Env School")
    def test_without_a_row_the_environment_is_used_and_positions_shown(self):
        identity = selectors.result_identity(request=self.request)
        self.assertEqual(identity["name"], "Env School")
        self.assertTrue(identity["show_positions"])
        self.assertEqual(identity["logo_url"], "")

    def test_show_positions_false_is_respected(self):
        self._row(name="X", show_positions=False)
        self.assertFalse(selectors.result_identity(request=self.request)["show_positions"])

    def test_the_logo_url_is_absolute_for_weasyprint(self):
        row = self._row(name="X")
        row.logo = ContentFile(PNG_BYTES, name="crest.png")
        row.save()
        url = selectors.result_identity(request=self.request)["logo_url"]
        self.assertTrue(url.startswith("http"), url)
        self.assertIn("/media/school/logo/", url)


class StudentTermPositionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        math = make_subject("MATH", "Mathematics")
        eng = make_subject("ENG", "English")

        cls.top = make_student_user(cls.school_class, admission_number="POS-001")
        cls.mid = make_student_user(cls.school_class, admission_number="POS-002")
        cls.low = make_student_user(cls.school_class, admission_number="POS-003")

        for student, scores in (
            (cls.top, ("90", "92")),
            (cls.mid, ("75", "74")),
            (cls.low, ("60", "58")),
        ):
            make_published_score(cls.school_class, math, cls.term, student, Decimal(scores[0]))
            make_published_score(cls.school_class, eng, cls.term, student, Decimal(scores[1]))

    def test_positions_rank_by_average(self):
        result = selectors.student_term_position(
            student_id=self.top.pk, term_id=self.term.pk
        )
        self.assertEqual(result, {"position": 1, "total_in_class": 3})
        result = selectors.student_term_position(
            student_id=self.low.pk, term_id=self.term.pk
        )
        self.assertEqual(result["position"], 3)

    def test_ties_share_a_position(self):
        math = make_subject("MATH", "Mathematics")
        eng = make_subject("ENG", "English")
        make_published_score(
            self.school_class, math, self.term, self.mid, Decimal("90")
        )
        make_published_score(
            self.school_class, eng, self.term, self.mid, Decimal("92")
        )
        result = selectors.student_term_position(
            student_id=self.mid.pk, term_id=self.term.pk
        )
        self.assertEqual(result, {"position": 1, "total_in_class": 3})

    def test_student_without_a_class_has_no_position(self):
        floating = make_student_user(self.school_class, admission_number="POS-099")
        floating.current_class = None
        floating.save(update_fields=["current_class"])
        self.assertIsNone(
            selectors.student_term_position(
                student_id=floating.pk, term_id=self.term.pk
            )
        )

    def test_student_without_published_results_has_no_position(self):
        quiet = make_student_user(self.school_class, admission_number="POS-100")
        self.assertIsNone(
            selectors.student_term_position(student_id=quiet.pk, term_id=self.term.pk)
        )


class ClassTeacherRemarkModelTests(TestCase):
    def test_one_remark_per_student_and_term(self):
        year = make_year()
        term = make_term(year)
        school_class = make_class(year)
        student = make_student_user(school_class)
        teacher = make_user("teacher")

        ClassTeacherRemark.objects.create(
            student=student, term=term, remark="Excellent term.", class_teacher=teacher
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClassTeacherRemark.objects.create(
                    student=student, term=term, remark="Duplicate."
                )

    def test_selector_returns_the_written_remark(self):
        year = make_year()
        term = make_term(year)
        school_class = make_class(year)
        student = make_student_user(school_class)
        written = ClassTeacherRemark.objects.create(
            student=student, term=term, remark="Keeps improving."
        )
        found = selectors.class_teacher_remark(student_id=student.pk, term_id=term.pk)
        self.assertEqual(found.pk, written.pk)
        self.assertIsNone(
            selectors.class_teacher_remark(student_id=student.pk, term_id=term.pk + 999)
        )


class ReportCardPdfTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        make_grading_config(cls.year)
        cls.teacher_user = make_user("teacher", first_name="Ama", last_name="Boateng")
        cls.school_class = make_class(cls.year, class_teacher=cls.teacher_user)
        cls.math = make_subject("MATH", "Mathematics")
        cls.eng = make_subject("ENG", "English")

        cls.student = make_student_user(cls.school_class, admission_number="RC-001")
        cls.peer = make_student_user(cls.school_class, admission_number="RC-002")
        make_published_score(cls.school_class, cls.math, cls.term, cls.student, Decimal("90"))
        make_published_score(cls.school_class, cls.eng, cls.term, cls.student, Decimal("85"))
        make_published_score(cls.school_class, cls.math, cls.term, cls.peer, Decimal("60"))
        make_published_score(cls.school_class, cls.eng, cls.term, cls.peer, Decimal("55"))

        cls.admin = make_user("admin", username="rc_admin")

    def setUp(self):
        self.url = reverse(
            "results:report_card_pdf", args=[self.student.pk, self.term.pk]
        )

    def _html_as(self, user) -> str:
        _rendered.clear()
        self.client.force_login(user)
        with _recorder:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, _rendered)
        self.assertEqual(response["Content-Type"], "application/pdf")
        return _rendered[-1]

    def test_card_carries_identity_summary_teacher_and_signatures(self):
        html = self._html_as(self.admin)
        squashed = html.replace(" ", "").replace("\n", "")
        self.assertIn("My School", html)
        self.assertIn("Academic Report Card", html)
        self.assertIn("Ama Boateng", html)  # meta row and signature line
        self.assertIn("Principal", html)
        self.assertIn("Overall Average", html)
        self.assertIn("Overall Grade", html)
        self.assertIn('value">A<', squashed)  # 87.5 average -> band grade A
        self.assertIn("Class Position", html)
        self.assertIn("1/2", squashed)
        self.assertIn("Mathematics", html)

    def test_logo_renders_when_uploaded_and_placeholder_otherwise(self):
        html = self._html_as(self.admin)
        self.assertIn("crest-placeholder", html)

        row = SchoolSettings.objects.create(
            pk=SchoolSettings.SINGLETON_PK, name="Crest School"
        )
        row.logo = ContentFile(PNG_BYTES, name="crest.png")
        row.save()
        html = self._html_as(self.admin)
        self.assertIn('class="crest"', html)
        self.assertIn("/media/school/logo/", html)
        self.assertIn("Crest School", html)

    def test_positions_disappear_when_the_settings_hide_them(self):
        SchoolSettings.objects.create(
            pk=SchoolSettings.SINGLETON_PK, name="Quiet School", show_positions=False
        )
        html = self._html_as(self.admin)
        self.assertNotIn("Class Position", html)
        self.assertNotIn("<th class=\"center\">Position</th>", html)

    def test_remark_box_only_when_a_remark_was_written(self):
        html = self._html_as(self.admin)
        self.assertNotIn("Class Teacher&#x27;s Remark", html)

        ClassTeacherRemark.objects.create(
            student=self.student, term=self.term, remark="A steady, focused term."
        )
        html = self._html_as(self.admin)
        self.assertIn("A steady, focused term.", html)
