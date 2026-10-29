"""
apps/results/tests/test_access_and_grades.py
---------------------------------------------
Module 4 IDOR / published-only guarantees and Module 2 grade boundaries.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ParentProfile
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
from apps.students.models import StudentGuardian

from ..models import ResultSheet, ScoreEntry
from ..services import (
    confirm_sheet,
    create_result_sheet,
    publish_class_term,
    resolve_grade,
    submit_sheet,
    upsert_score_entry,
)


class _FakeHTML:
    def __init__(self, string=None, base_url=None):
        self.string = string

    def write_pdf(self):
        return b"%PDF-1.4\nstub\n"


#: Access tests must not depend on the host having WeasyPrint's native libraries,
#: so the engine is stubbed rather than skipped.
_engine = patch.dict(sys.modules, {"weasyprint": SimpleNamespace(HTML=_FakeHTML)})


class GradeBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        make_grading_config(cls.year)

    def test_boundaries_use_configured_bands(self):
        cases = [
            ("0", "F"),
            ("49", "F"),
            ("50", "C"),
            ("69", "C"),
            ("70", "B"),
            ("84", "B"),
            ("85", "A"),
            ("100", "A"),
        ]
        for score, expected in cases:
            grade, _remark, _points = resolve_grade(self.year.pk, Decimal(score))
            self.assertEqual(grade, expected, f"score {score}")


class PublishedOnlyAndIdorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        make_grading_config(cls.year)

        cls.student = make_student_user(cls.school_class, admission_number="RES-001")
        cls.student_user = cls.student.user
        cls.other_student = make_student_user(
            cls.school_class, admission_number="RES-002"
        )

        cls.math = make_subject("MATH", "Mathematics")
        cls.english = make_subject("ENG", "English")
        make_published_score(
            cls.school_class, cls.math, cls.term, cls.student, total=Decimal("80.00")
        )
        draft_sheet = ResultSheet.objects.create(
            school_class=cls.school_class,
            subject=cls.english,
            term=cls.term,
            stream=None,
            status=ResultSheet.Status.DRAFT,
        )
        ScoreEntry.objects.create(
            result_sheet=draft_sheet,
            student=cls.student,
            raw_score="99.00",
            total="99.00",
            grade="A",
            remark="Draft",
            points=4,
        )

        cls.parent_user = make_user("parent", username="res_parent")
        cls.parent_profile = ParentProfile.objects.create(user=cls.parent_user)
        StudentGuardian.objects.create(
            student=cls.student,
            parent=cls.parent_profile,
            relationship=StudentGuardian.Relationship.GUARDIAN,
            is_primary=True,
        )

    def test_student_results_show_published_only(self):
        self.client.force_login(self.student_user)
        response = self.client.get(reverse("results:student_results"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mathematics")
        self.assertNotContains(response, "English")

    def test_student_cannot_request_another_students_results(self):
        self.client.force_login(self.student_user)
        response = self.client.get(
            reverse("results:student_results") + f"?student={self.other_student.pk}"
        )
        # Students cannot override identity by query parameter.
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.other_student.admission_number)

    def test_student_cannot_download_another_students_report_card(self):
        self.client.force_login(self.student_user)
        response = self.client.get(
            reverse(
                "results:report_card_pdf",
                args=[self.other_student.pk, self.term.pk],
            )
        )
        self.assertEqual(response.status_code, 403)

    def test_parent_sees_linked_child_and_is_blocked_from_others(self):
        self.client.force_login(self.parent_user)
        with _engine:
            own = self.client.get(
                reverse("results:report_card_pdf", args=[self.student.pk, self.term.pk])
            )
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own["Content-Type"], "application/pdf")
        other = self.client.get(
            reverse(
                "results:report_card_pdf", args=[self.other_student.pk, self.term.pk]
            )
        )
        self.assertEqual(other.status_code, 403)

    def test_student_cannot_open_another_students_fee_statement(self):
        self.client.force_login(self.student_user)
        response = self.client.get(
            reverse("finance:student_statement", args=[self.other_student.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_access_admin_results_sheets(self):
        teacher_user = make_user("teacher", username="res_teacher")
        self.client.force_login(teacher_user)
        response = self.client.get(reverse("results:sheet_list"))
        self.assertEqual(response.status_code, 403)


class PublishIsTheGateTests(TestCase):
    """
    C3 — "students see results once the admin approves them".

    The staff *approval* is Confirm, but the gate the student sees is Publish:
    a confirmed sheet is still invisible until an administrator publishes the
    class/term. Drive the real state machine rather than writing statuses, so a
    change to VALID_TRANSITIONS cannot make this pass by accident.
    """

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        make_grading_config(cls.year)
        cls.student = make_student_user(cls.school_class, admission_number="PUB-001")
        cls.physics = make_subject("PHY", "Physics")
        cls.admin = make_user("admin", username="pub_admin")

    def setUp(self):
        self.sheet = create_result_sheet(
            school_class=self.school_class,
            subject=self.physics,
            term=self.term,
            actor=self.admin,
        )
        upsert_score_entry(
            sheet=self.sheet,
            student=self.student,
            raw_score="77",
            actor=self.admin,
        )

    def _page(self):
        self.client.force_login(self.student.user)
        return self.client.get(reverse("results:student_results"))

    def _assert_invisible(self):
        response = self._page()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Physics")
        self.assertNotContains(response, "77.0%")
        self.assertContains(response, "No results have been published for you yet.")
        self.assertEqual(list(response.context["terms"]), [])

    def test_a_draft_sheet_is_invisible(self):
        self._assert_invisible()

    def test_a_submitted_sheet_is_invisible(self):
        submit_sheet(sheet=self.sheet, actor=self.admin)
        self._assert_invisible()

    def test_a_confirmed_sheet_is_still_invisible(self):
        submit_sheet(sheet=self.sheet, actor=self.admin)
        confirm_sheet(sheet=self.sheet, actor=self.admin)
        self._assert_invisible()

    def test_publishing_reveals_the_marks(self):
        submit_sheet(sheet=self.sheet, actor=self.admin)
        confirm_sheet(sheet=self.sheet, actor=self.admin)
        published = publish_class_term(
            school_class=self.school_class, term=self.term, actor=self.admin
        )
        self.assertEqual(published, 1)

        response = self._page()
        self.assertContains(response, "Physics")
        self.assertContains(response, "77.0%")
        self.assertNotContains(response, "No results have been published for you yet.")
        self.assertEqual([t.pk for t in response.context["terms"]], [self.term.pk])

    def test_the_report_card_is_refused_until_published(self):
        submit_sheet(sheet=self.sheet, actor=self.admin)
        confirm_sheet(sheet=self.sheet, actor=self.admin)
        url = reverse("results:report_card_pdf", args=[self.student.pk, self.term.pk])

        self.client.force_login(self.student.user)
        with _engine:
            self.assertEqual(self.client.get(url).status_code, 404)

            publish_class_term(
                school_class=self.school_class, term=self.term, actor=self.admin
            )
            published = self.client.get(url)

        self.assertEqual(published.status_code, 200)
        self.assertEqual(published["Content-Type"], "application/pdf")
