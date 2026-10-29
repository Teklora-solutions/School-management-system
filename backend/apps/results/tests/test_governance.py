"""
apps/results/tests/test_governance.py
---------------------------------------
Module 1 — grading configuration and results governance.

Covers:
  - the year's pass mark is saved and replaced
  - ResultSheet state machine: invalid transitions rejected
  - confirmation locks the sheet
  - reopening requires a reason and writes an audit log
  - publish all confirmed sheets for a class/term
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.students.models import Student

from ..models import GradeBand, GradingConfig, ResultSheet
from ..services import (
    confirm_sheet,
    publish_class_term,
    publish_sheet,
    reopen_sheet,
    save_grading_config,
    submit_sheet,
    upsert_score_entry,
)


class GradingConfigTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )

    def _save(self, pass_mark=50):
        return save_grading_config(academic_year=self.year, pass_mark=pass_mark)

    def test_valid_config_is_saved(self):
        config = self._save()
        self.assertEqual(config.pass_mark, Decimal("50.00"))
        self.assertTrue(
            AuditLog.objects.filter(action="results.grading_saved").exists()
        )

    def test_config_is_replaced_on_resave(self):
        self._save(pass_mark=50)
        self._save(pass_mark=65)
        self.assertEqual(GradingConfig.objects.count(), 1)
        self.assertEqual(GradingConfig.objects.get().pass_mark, Decimal("65.00"))


class ResultSheetGovernanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="results_admin",
            email="results_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.term = Term.objects.create(
            academic_year=cls.year,
            name="term_1",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 20),
            is_current=True,
        )
        cls.school_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.subject = Subject.objects.create(name="Mathematics", code="MATH")
        cls.second_subject = Subject.objects.create(name="English", code="ENG")

        save_grading_config(academic_year=cls.year, pass_mark=50)
        for low, high, grade in ((0, 49, "F"), (50, 100, "P")):
            GradeBand.objects.create(
                academic_year=cls.year,
                min_score=low,
                max_score=high,
                grade=grade,
            )

        student_user = User.objects.create_user(
            username="governance_student",
            email="governance_student@test.school",
            password="Testpass1!",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        cls.student = Student.objects.create(
            user=student_user,
            admission_number="ADM-GOV-1",
            current_class=cls.school_class,
            status=Student.Status.ACTIVE,
        )

    def _sheet(self, subject=None) -> ResultSheet:
        return ResultSheet.objects.create(
            school_class=self.school_class,
            subject=subject or self.subject,
            term=self.term,
        )

    def test_valid_state_flow(self):
        sheet = self._sheet()

        submit_sheet(sheet=sheet, actor=self.admin)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.SUBMITTED)
        self.assertIsNotNone(sheet.submitted_at)

        confirm_sheet(sheet=sheet, actor=self.admin)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.CONFIRMED)
        self.assertEqual(sheet.confirmed_by, self.admin)
        self.assertIsNotNone(sheet.confirmed_at)
        self.assertTrue(sheet.is_locked)

        publish_sheet(sheet=sheet, actor=self.admin)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.PUBLISHED)
        self.assertEqual(sheet.published_by, self.admin)
        self.assertIsNotNone(sheet.published_at)

        for action in ("results.submitted", "results.confirmed", "results.published"):
            self.assertTrue(
                AuditLog.objects.filter(
                    action=action, object_id=str(sheet.pk)
                ).exists(),
                action,
            )

    def test_invalid_transitions_are_rejected(self):
        sheet = self._sheet()

        with self.assertRaises(ValidationError):
            confirm_sheet(sheet=sheet, actor=self.admin)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.DRAFT)

        with self.assertRaises(ValidationError):
            publish_sheet(sheet=sheet, actor=self.admin)

        submit_sheet(sheet=sheet, actor=self.admin)
        with self.assertRaises(ValidationError):
            publish_sheet(sheet=sheet, actor=self.admin)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.SUBMITTED)

        confirm_sheet(sheet=sheet, actor=self.admin)
        with self.assertRaises(ValidationError):
            confirm_sheet(sheet=sheet, actor=self.admin)

        publish_sheet(sheet=sheet, actor=self.admin)
        with self.assertRaises(ValidationError):
            submit_sheet(sheet=sheet, actor=self.admin)

    def test_confirmed_sheet_locks_score_edits(self):
        sheet = self._sheet()
        submit_sheet(sheet=sheet, actor=self.admin)
        confirm_sheet(sheet=sheet, actor=self.admin)

        with self.assertRaises(ValidationError) as ctx:
            upsert_score_entry(
                sheet=sheet,
                student=self.student,
                raw_score="80",
                actor=self.admin,
            )
        self.assertIn("locked", str(ctx.exception))
        self.assertFalse(sheet.entries.exists())

    def test_draft_sheet_accepts_scores(self):
        sheet = self._sheet()
        entry = upsert_score_entry(
            sheet=sheet,
            student=self.student,
            raw_score="80",
            actor=self.admin,
        )
        self.assertEqual(entry.total, Decimal("80.00"))
        self.assertEqual(entry.grade, "P")

    def test_reopen_requires_reason_and_is_audited(self):
        sheet = self._sheet()
        submit_sheet(sheet=sheet, actor=self.admin)
        confirm_sheet(sheet=sheet, actor=self.admin)

        with self.assertRaises(ValidationError) as ctx:
            reopen_sheet(sheet=sheet, reason="   ", actor=self.admin)
        self.assertIn("reopen_reason", ctx.exception.message_dict)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.CONFIRMED)

        reopen_sheet(
            sheet=sheet,
            reason="Marking error in question 4",
            actor=self.admin,
        )
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.DRAFT)
        self.assertEqual(sheet.reopen_reason, "Marking error in question 4")
        self.assertIsNone(sheet.confirmed_at)
        self.assertIsNone(sheet.confirmed_by)
        self.assertIsNone(sheet.published_at)
        self.assertFalse(sheet.is_locked)

        audit = AuditLog.objects.get(action="results.reopened", object_id=str(sheet.pk))
        self.assertEqual(
            audit.change_summary["reopen_reason"], "Marking error in question 4"
        )

    def test_publish_all_confirmed_for_class_term(self):
        first = self._sheet(self.subject)
        second = self._sheet(self.second_subject)
        draft = ResultSheet.objects.create(
            school_class=self.school_class,
            subject=Subject.objects.create(name="Science", code="SCI"),
            term=self.term,
        )

        for sheet in (first, second):
            submit_sheet(sheet=sheet, actor=self.admin)
            confirm_sheet(sheet=sheet, actor=self.admin)

        count = publish_class_term(
            school_class=self.school_class, term=self.term, actor=self.admin
        )
        self.assertEqual(count, 2)

        first.refresh_from_db()
        second.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual(first.status, ResultSheet.Status.PUBLISHED)
        self.assertEqual(second.status, ResultSheet.Status.PUBLISHED)
        self.assertEqual(draft.status, ResultSheet.Status.DRAFT)

    def test_grading_config_view_saves(self):
        self.client.force_login(self.admin)
        url = reverse("results:grading_config", args=[self.year.pk])

        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        data = {"pass_mark": "55"}
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("results:grading_list"))
        self.assertEqual(
            GradingConfig.objects.get(academic_year=self.year).pass_mark,
            Decimal("55.00"),
        )
        self.assertEqual(GradeBand.objects.count(), 2)


class SheetPublishViewTests(TestCase):
    """
    Per-sheet Publish on the sheet detail page.

    Until this existed an administrator could only publish a whole class/term
    from the Result Sheets page, so confirming one sheet left no way to make
    that one sheet visible without touching every other subject.
    """

    @classmethod
    def setUpTestData(cls):
        from apps.dashboards.tests.fixtures import (
            make_class,
            make_subject,
            make_term,
            make_user,
            make_year,
        )

        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.subject = make_subject("PVW", "Physics")
        cls.admin = make_user("admin", username="publish_view_admin")
        cls.teacher = make_user("teacher", username="publish_view_teacher")

    def _sheet(self, status=ResultSheet.Status.DRAFT) -> ResultSheet:
        sheet = ResultSheet.objects.create(
            school_class=self.school_class,
            subject=self.subject,
            term=self.term,
        )
        for target, step in (
            (ResultSheet.Status.SUBMITTED, submit_sheet),
            (ResultSheet.Status.CONFIRMED, confirm_sheet),
            (ResultSheet.Status.PUBLISHED, publish_sheet),
        ):
            if sheet.status == status:
                break
            sheet = step(sheet=sheet, actor=self.admin)
        return sheet

    def _publish(self, sheet):
        self.client.force_login(self.admin)
        return self.client.post(
            reverse("results:sheet_publish", args=[sheet.pk]), follow=True
        )

    def test_publishing_a_confirmed_sheet_makes_it_visible(self):
        sheet = self._sheet(ResultSheet.Status.CONFIRMED)

        response = self._publish(sheet)

        self.assertEqual(response.status_code, 200)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.PUBLISHED)
        self.assertEqual(sheet.published_by, self.admin)
        self.assertIsNotNone(sheet.published_at)
        self.assertContains(response, "published. The student can now see these marks.")
        self.assertTrue(
            AuditLog.objects.filter(
                action="results.published", object_id=str(sheet.pk)
            ).exists()
        )

    def test_a_submitted_sheet_is_refused(self):
        sheet = self._sheet(ResultSheet.Status.SUBMITTED)

        response = self._publish(sheet)

        self.assertEqual(response.status_code, 200)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.SUBMITTED)
        self.assertContains(response, "Cannot transition")

    def test_publishing_twice_is_refused(self):
        sheet = self._sheet(ResultSheet.Status.CONFIRMED)
        self._publish(sheet)

        response = self._publish(sheet)

        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.PUBLISHED)
        self.assertContains(response, "Cannot transition")

    def test_a_teacher_cannot_publish(self):
        sheet = self._sheet(ResultSheet.Status.CONFIRMED)

        self.client.force_login(self.teacher)
        response = self.client.post(reverse("results:sheet_publish", args=[sheet.pk]))

        self.assertEqual(response.status_code, 403)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, ResultSheet.Status.CONFIRMED)

    def test_the_button_appears_only_once_the_sheet_is_confirmed(self):
        sheet = self._sheet(ResultSheet.Status.SUBMITTED)
        url = reverse("results:sheet_detail", args=[sheet.pk])

        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(url), "Publish Sheet")

        confirm_sheet(sheet=sheet, actor=self.admin)
        self.assertContains(self.client.get(url), "Publish Sheet")

        publish_sheet(sheet=sheet, actor=self.admin)
        self.assertNotContains(self.client.get(url), "Publish Sheet")
