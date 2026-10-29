"""
apps/teachers/tests/test_marks_entry.py
----------------------------------------
T1 — teachers enter exam marks for the classes they are assigned to.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.accounts.models import User
from apps.results.models import GradeBand, ResultSheet, ScoreEntry
from apps.results.services import ensure_exams_for_term
from apps.students.models import Student
from apps.teachers.models import TeacherAssignment, TeacherProfile


class MarksEntryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.term = Term.objects.create(
            academic_year=cls.year,
            name="Term 1",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 20),
            is_current=True,
        )
        cls.school_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.subject = Subject.objects.create(name="Mathematics", code="MATH")
        for low, high, grade in ((0, 49, "F"), (50, 100, "P")):
            GradeBand.objects.create(
                academic_year=cls.year,
                min_score=low,
                max_score=high,
                grade=grade,
            )

        cls.teacher_user = cls._user("tch_marks_1", User.Role.TEACHER)
        cls.teacher = TeacherProfile.objects.create(
            user=cls.teacher_user, staff_id="TCH-MARKS-1"
        )
        cls.assignment = TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.subject,
            academic_year=cls.year,
        )

        cls.other_teacher_user = cls._user("tch_marks_2", User.Role.TEACHER)
        cls.other_teacher = TeacherProfile.objects.create(
            user=cls.other_teacher_user, staff_id="TCH-MARKS-2"
        )

        cls.student = cls._student("Pupil One", "MARKS-001")
        cls.exam = ensure_exams_for_term(cls.term)[0]

    @classmethod
    def _user(cls, username, role):
        return User.objects.create_user(
            username=username,
            email=f"{username}@test.school",
            password="Testpass1!",
            role=role,
            must_change_password=False,
        )

    @classmethod
    def _student(cls, name, admission_number):
        first, last = name.split(" ", 1)
        user = User.objects.create_user(
            username=f"user_{admission_number}",
            email=f"{admission_number}@test.school",
            password="Testpass1!",
            role=User.Role.STUDENT,
            must_change_password=False,
            first_name=first,
            last_name=last,
        )
        return Student.objects.create(
            user=user,
            admission_number=admission_number,
            current_class=cls.school_class,
            status=Student.Status.ACTIVE,
        )

    def setUp(self):
        self.client.force_login(self.teacher_user)

    def _url(self):
        return reverse("teachers:marks_entry", args=[self.assignment.pk])

    def _sheet(self) -> ResultSheet:
        return ResultSheet.objects.get(
            school_class=self.school_class,
            subject=self.subject,
            term=self.term,
            exam=self.exam,
            stream=None,
        )

    def _entry(self) -> ScoreEntry:
        return ScoreEntry.objects.get(
            result_sheet=self._sheet(), student=self.student
        )

    def test_list_shows_only_the_teachers_own_assignments(self):
        TeacherAssignment.objects.create(
            teacher=self.other_teacher,
            school_class=self.school_class,
            subject=Subject.objects.create(name="English", code="ENG"),
            academic_year=self.year,
        )
        resp = self.client.get(reverse("teachers:marks_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mathematics")
        self.assertNotContains(resp, "English")

    def test_entry_page_lists_the_roster(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Pupil One")
        self.assertContains(resp, f'name="score_{self.student.pk}"')

    def test_teacher_saves_a_draft_mark(self):
        resp = self.client.post(
            self._url(),
            {
                "term": str(self.term.pk),
                "exam": str(self.exam.pk),
                "action": "save",
                f"score_{self.student.pk}": "72",
            },
        )
        self.assertEqual(resp.status_code, 302)

        sheet = self._sheet()
        self.assertEqual(sheet.status, ResultSheet.Status.DRAFT)
        self.assertEqual(sheet.teacher, self.teacher)
        entry = self._entry()
        self.assertEqual(entry.raw_score, Decimal("72.00"))
        self.assertEqual(entry.total, Decimal("72.00"))
        self.assertEqual(entry.grade, "P")

    def test_blank_mark_clears_the_entry(self):
        self.client.post(
            self._url(),
            {
                "term": str(self.term.pk),
                "exam": str(self.exam.pk),
                "action": "save",
                f"score_{self.student.pk}": "72",
            },
        )
        self.client.post(
            self._url(),
            {
                "term": str(self.term.pk),
                "exam": str(self.exam.pk),
                "action": "save",
                f"score_{self.student.pk}": "",
            },
        )
        self.assertFalse(self._sheet().entries.exists())

    def test_submit_hands_the_sheet_to_the_admin(self):
        resp = self.client.post(
            self._url(),
            {
                "term": str(self.term.pk),
                "exam": str(self.exam.pk),
                "action": "submit",
                f"score_{self.student.pk}": "65",
            },
        )
        self.assertEqual(resp.status_code, 302)
        sheet = self._sheet()
        self.assertEqual(sheet.status, ResultSheet.Status.SUBMITTED)
        self.assertIsNotNone(sheet.submitted_at)
        self.assertEqual(self._entry().raw_score, Decimal("65.00"))

    def test_submitted_sheet_cannot_be_edited(self):
        self.client.post(
            self._url(),
            {
                "term": str(self.term.pk),
                "exam": str(self.exam.pk),
                "action": "submit",
                f"score_{self.student.pk}": "65",
            },
        )
        self.client.post(
            self._url(),
            {
                "term": str(self.term.pk),
                "exam": str(self.exam.pk),
                "action": "save",
                f"score_{self.student.pk}": "90",
            },
        )
        self.assertEqual(self._entry().raw_score, Decimal("65.00"))
        self.assertEqual(self._sheet().status, ResultSheet.Status.SUBMITTED)

    def test_marks_over_100_are_rejected(self):
        self.client.post(
            self._url(),
            {
                "term": str(self.term.pk),
                "exam": str(self.exam.pk),
                "action": "save",
                f"score_{self.student.pk}": "101",
            },
        )
        self.assertFalse(self._sheet().entries.exists())

    def test_another_teacher_cannot_open_the_sheet(self):
        self.client.force_login(self.other_teacher_user)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 404)

    def test_admin_cannot_use_the_teacher_marks_page(self):
        admin = self._user("marks_admin", User.Role.ADMIN)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("teachers:marks_list")).status_code, 403)
        self.assertEqual(self.client.get(self._url()).status_code, 403)
