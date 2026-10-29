"""
apps/results/tests/test_broadsheet.py
-------------------------------------
R1 — class broadsheet selectors, exam dimension, and the admin view.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import (
    AcademicYear,
    ClassSubject,
    SchoolClass,
    Subject,
    Term,
)
from apps.accounts.models import User
from apps.students.models import Student

from .. import selectors
from ..models import Exam, GradeBand, ResultSheet, ScoreEntry
from ..services import confirm_sheet, ensure_exams_for_term, publish_sheet, submit_sheet


class BroadsheetDataMixin:
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
        cls.maths = Subject.objects.create(name="Mathematics", code="MATH")
        cls.english = Subject.objects.create(name="English", code="ENG")
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.maths)
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.english)

        for band in (
            (0, 49, "F", "Fail", 0),
            (50, 69, "C", "Pass", 2),
            (70, 84, "B", "Good", 3),
            (85, 100, "A", "Excellent", 4),
        ):
            GradeBand.objects.create(
                academic_year=cls.year,
                min_score=band[0],
                max_score=band[1],
                grade=band[2],
                remark=band[3],
                points=band[4],
            )

        cls.admin = User.objects.create_user(
            username="broadsheet_admin",
            email="broadsheet_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.teacher = User.objects.create_user(
            username="broadsheet_teacher",
            email="broadsheet_teacher@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )

        cls.alice = cls._student("Alice A", "ADM-001", Student.Status.ACTIVE)
        cls.bob = cls._student("Bob B", "ADM-002", Student.Status.REPEATING)
        cls.cara = cls._student("Cara C", "ADM-003", Student.Status.ACTIVE)
        cls.dan = cls._student("Dan D", "ADM-004", Student.Status.ACTIVE)
        cls.left = cls._student("Left L", "ADM-005", Student.Status.WITHDRAWN)

        exams = ensure_exams_for_term(cls.term)
        cls.exam = exams[0]
        cls.math_sheet = cls._sheet(cls.maths, cls.exam)
        cls.eng_sheet = cls._sheet(cls.english, cls.exam)

        cls._score(cls.math_sheet, cls.alice, 90)
        cls._score(cls.eng_sheet, cls.alice, 90)
        cls._score(cls.math_sheet, cls.bob, 80)
        cls._score(cls.eng_sheet, cls.bob, 90)
        cls._score(cls.math_sheet, cls.cara, 90)
        cls._score(cls.eng_sheet, cls.cara, 80)
        cls._score(cls.math_sheet, cls.left, 100)
        cls._score(cls.eng_sheet, cls.left, 100)

    @classmethod
    def _student(cls, name, admission_number, status) -> Student:
        first, last = name.split(" ", 1)
        user = User.objects.create_user(
            username=f"student_{admission_number}",
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
            status=status,
        )

    @classmethod
    def _sheet(cls, subject, exam) -> ResultSheet:
        return ResultSheet.objects.create(
            school_class=cls.school_class,
            subject=subject,
            term=cls.term,
            exam=exam,
        )

    @classmethod
    def _score(cls, sheet, student, raw):
        ScoreEntry.objects.create(
            result_sheet=sheet,
            student=student,
            raw_score=Decimal(raw),
            total=Decimal(raw),
        )


class ExamDimensionTests(BroadsheetDataMixin, TestCase):
    def test_ensure_exams_creates_ten_and_is_idempotent(self):
        term = Term.objects.create(
            academic_year=self.year,
            name="Term 2",
            start_date=datetime.date(2026, 1, 5),
            end_date=datetime.date(2026, 4, 10),
        )
        exams = ensure_exams_for_term(term)
        self.assertEqual(len(exams), 10)
        self.assertEqual([e.order for e in exams], list(range(1, 11)))
        self.assertEqual(
            list(Exam.objects.filter(term=term).values_list("name", flat=True)),
            [f"Exam {n}" for n in range(1, 11)],
        )

        again = ensure_exams_for_term(term)
        self.assertEqual(len(again), 10)
        self.assertEqual(Exam.objects.filter(term=term).count(), 10)

    def test_exam_sheet_and_term_sheet_coexist(self):
        legacy = ResultSheet.objects.create(
            school_class=self.school_class,
            subject=self.maths,
            term=self.term,
        )
        self.assertIsNone(legacy.exam)

        duplicate = ResultSheet(
            school_class=self.school_class,
            subject=self.maths,
            term=self.term,
            exam=self.exam,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()


class BroadsheetSelectorTests(BroadsheetDataMixin, TestCase):
    def _broadsheet(self, **kwargs):
        defaults = {
            "school_class": self.school_class,
            "term": self.term,
            "exam": self.exam,
        }
        defaults.update(kwargs)
        return selectors.exam_broadsheet(**defaults)

    def test_subject_columns_follow_class_subjects(self):
        subjects = selectors.broadsheet_subjects(self.school_class, self.term)
        self.assertEqual([s.code for s in subjects], ["ENG", "MATH"])

    def test_subjects_fall_back_to_sheets_then_all_active(self):
        bare_class = SchoolClass.objects.create(
            academic_year=self.year, name="Grade 6", level_order=6
        )
        ResultSheet.objects.create(
            school_class=bare_class,
            subject=self.maths,
            term=self.term,
        )
        self.assertEqual(
            [s.code for s in selectors.broadsheet_subjects(bare_class, self.term)],
            ["MATH"],
        )

        untouched = SchoolClass.objects.create(
            academic_year=self.year, name="Grade 7", level_order=7
        )
        self.assertEqual(
            {s.code for s in selectors.broadsheet_subjects(untouched, self.term)},
            {"ENG", "MATH"},
        )

    def test_roster_excludes_withdrawn_students(self):
        roster = selectors.class_roster(self.school_class)
        self.assertEqual(
            [s.admission_number for s in roster],
            ["ADM-001", "ADM-002", "ADM-003", "ADM-004"],
        )

    def test_totals_means_grades_and_positions(self):
        data = self._broadsheet()
        rows = {row["student"].admission_number: row for row in data["students"]}
        self.assertEqual(set(rows), {"ADM-001", "ADM-002", "ADM-003", "ADM-004"})

        self.assertEqual(rows["ADM-001"]["total"], Decimal("180.00"))
        self.assertEqual(rows["ADM-001"]["mean"], Decimal(90))
        self.assertEqual(rows["ADM-001"]["grade"], "A")
        self.assertEqual(rows["ADM-001"]["position"], 1)

        # 80 + 90 and 90 + 80 tie for second under competition ranking.
        self.assertEqual(rows["ADM-002"]["total"], Decimal("170.00"))
        self.assertEqual(rows["ADM-003"]["total"], Decimal("170.00"))
        self.assertEqual(rows["ADM-002"]["position"], 2)
        self.assertEqual(rows["ADM-003"]["position"], 2)

        # No marks: shown, but not ranked.
        self.assertIsNone(rows["ADM-004"]["total"])
        self.assertIsNone(rows["ADM-004"]["mean"])
        self.assertIsNone(rows["ADM-004"]["position"])
        self.assertEqual(rows["ADM-004"]["grade"], "")

    def test_means_and_progress(self):
        data = self._broadsheet()
        self.assertEqual(data["class_mean_total"], 173.3)
        self.assertEqual(data["class_mean_pct"], 86.7)
        self.assertEqual(data["subject_means"][self.maths.pk]["mean"], 86.7)
        self.assertEqual(data["subject_means"][self.english.pk]["mean"], 86.7)
        self.assertEqual(data["progress"]["scored"], 6)
        self.assertEqual(data["progress"]["expected"], 8)
        self.assertEqual(data["progress"]["pct"], 75)

    def test_raw_score_falls_back_to_total(self):
        ScoreEntry.objects.filter(
            result_sheet=self.math_sheet, student=self.cara
        ).update(raw_score=None, total=Decimal(65))
        data = self._broadsheet()
        row = next(
            r for r in data["students"] if r["student"].admission_number == "ADM-003"
        )
        self.assertEqual(row["cells"][1]["score"], Decimal("65.00"))
        self.assertEqual(row["cells"][1]["grade"], "C")

    def test_status_reflects_sheet_state(self):
        self.assertEqual(self._broadsheet()["status"]["label"], "Draft")

        for sheet in (self.math_sheet, self.eng_sheet):
            submit_sheet(sheet=sheet, actor=self.admin)
        self.assertEqual(self._broadsheet()["status"]["label"], "Submitted")

        for sheet in (self.math_sheet, self.eng_sheet):
            confirm_sheet(sheet=sheet, actor=self.admin)
        self.assertEqual(self._broadsheet()["status"]["label"], "Confirmed")

        for sheet in (self.math_sheet, self.eng_sheet):
            publish_sheet(sheet=sheet, actor=self.admin)
        self.assertEqual(self._broadsheet()["status"]["label"], "Published")

    def test_no_sheets_for_exam(self):
        other_exam = Exam.objects.get(term=self.term, order=2)
        data = self._broadsheet(exam=other_exam)
        self.assertEqual(data["sheets_count"], 0)
        self.assertEqual(data["status"]["label"], "No sheets")
        self.assertIsNone(data["class_mean_total"])
        self.assertEqual(data["progress"]["scored"], 0)


class BroadsheetViewTests(BroadsheetDataMixin, TestCase):
    def test_admin_sees_the_broadsheet(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("results:broadsheet"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Class Broadsheet", content)
        self.assertIn("Grade 5", content)
        self.assertIn("Alice A", content)
        self.assertIn("MATH", content)
        self.assertNotIn("Left L", content)

    def test_view_honours_filters(self):
        other_exam = Exam.objects.get(term=self.term, order=3)
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("results:broadsheet"),
            {
                "year": self.year.pk,
                "class": self.school_class.pk,
                "term": self.term.pk,
                "exam": other_exam.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Exam 3", response.content.decode())

    def test_invalid_filters_fall_back_to_defaults(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("results:broadsheet"), {"class": "abc", "term": "-1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_class"], self.school_class)
        self.assertEqual(response.context["selected_term"], self.term)

    def test_non_admin_is_denied(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("results:broadsheet"))
        self.assertEqual(response.status_code, 403)

    def test_page_loads_with_no_academic_data(self):
        ResultSheet.objects.all().delete()
        Exam.objects.all().delete()
        AcademicYear.objects.all().delete()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("results:broadsheet"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["years"])
