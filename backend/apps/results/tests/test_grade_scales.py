"""
apps/results/tests/test_grade_scales.py
---------------------------------------
Year grading systems: one per year, class selection, band resolution and
re-grading. Grades are exam-only (a single raw mark out of 100).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.students.models import Student

from .. import selectors
from ..models import (
    GradeBand,
    GradeScale,
    GradeScaleBand,
    ResultSheet,
    ScoreEntry,
)
from ..services import (
    delete_grade_scale,
    ensure_exams_for_term,
    grade_bands_for_class,
    regrade_entries_for_classes,
    resolve_grade_for_school_class,
    save_grade_scale,
    upsert_score_entry,
)

SCALE_BANDS = [
    {"min_score": 0, "max_score": 49, "grade": "F", "remark": "Fail", "points": 0},
    {"min_score": 50, "max_score": 100, "grade": "P", "remark": "Pass", "points": 2},
]


class GradeScaleTestData(TestCase):
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
        cls.other_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 6", level_order=6
        )
        cls.maths = Subject.objects.create(name="Mathematics", code="MATH")

        # Year fallback: 0–59 F, 60–100 A.
        for low, high, grade, remark, points in (
            (0, 59, "F", "Fail", 0),
            (60, 100, "A", "Excellent", 4),
        ):
            GradeBand.objects.create(
                academic_year=cls.year,
                min_score=low,
                max_score=high,
                grade=grade,
                remark=remark,
                points=points,
            )

        cls.scale = GradeScale.objects.create(
            academic_year=cls.year, name="Upper Primary"
        )
        GradeScaleBand.objects.create(
            scale=cls.scale, min_score=0, max_score=49, grade="F",
            remark="Fail", points=0,
        )
        GradeScaleBand.objects.create(
            scale=cls.scale, min_score=50, max_score=100, grade="P",
            remark="Pass", points=2,
        )

        cls.admin = User.objects.create_user(
            username="scale_admin",
            email="scale_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.student = cls._student("Pupil One", "SCALE-001", cls.school_class)
        cls.other_student = cls._student("Pupil Two", "SCALE-002", cls.other_class)

    @classmethod
    def _student(cls, name, admission_number, school_class):
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
            current_class=school_class,
            status=Student.Status.ACTIVE,
        )

    def _term_sheet(self, school_class=None, subject=None) -> ResultSheet:
        return ResultSheet.objects.create(
            school_class=school_class or self.school_class,
            subject=subject or self.maths,
            term=self.term,
        )


class GradeScaleResolutionTests(GradeScaleTestData):
    def test_assigned_scale_takes_precedence_over_year_bands(self):
        self.school_class.grade_scale = self.scale
        self.school_class.save(update_fields=["grade_scale"])

        self.assertEqual(
            resolve_grade_for_school_class(self.school_class, Decimal(60)),
            ("P", "Pass", Decimal(2)),
        )
        # The year fallback still says A for the same mark.
        self.assertEqual(
            resolve_grade_for_school_class(self.other_class, Decimal(60)),
            ("A", "Excellent", Decimal(4)),
        )

    def test_unassigned_class_uses_year_bands(self):
        bands = grade_bands_for_class(self.school_class)
        self.assertEqual([b.grade for b in bands], ["A", "F"])

    def test_scale_bands_are_ordered_highest_first(self):
        self.school_class.grade_scale = self.scale
        self.school_class.save(update_fields=["grade_scale"])
        bands = grade_bands_for_class(self.school_class)
        self.assertEqual([b.grade for b in bands], ["P", "F"])

    def test_duplicate_grade_in_one_scale_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            GradeScaleBand.objects.create(
                scale=self.scale, min_score=50, max_score=100, grade="F"
            )

    def test_one_grading_system_per_year(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            GradeScale.objects.create(academic_year=self.year, name="Second Scale")


class GradeScaleSaveTests(GradeScaleTestData):
    def test_save_creates_the_years_system_and_selects_classes(self):
        result = save_grade_scale(
            academic_year=self.year,
            name="Lower Primary",
            bands=SCALE_BANDS,
            classes=[self.school_class],
            actor=self.admin,
        )
        scale = result["scale"]
        self.assertEqual(scale.name, "Lower Primary")
        self.assertEqual(GradeScale.objects.filter(academic_year=self.year).count(), 1)
        self.assertEqual(scale.bands.count(), 2)

        self.school_class.refresh_from_db()
        self.other_class.refresh_from_db()
        self.assertEqual(self.school_class.grade_scale_id, scale.pk)
        self.assertIsNone(self.other_class.grade_scale_id)
        self.assertTrue(
            AuditLog.objects.filter(action="results.grade_scale_saved").exists()
        )

    def test_save_replaces_the_same_years_system(self):
        save_grade_scale(
            academic_year=self.year,
            name="First",
            bands=SCALE_BANDS,
            classes=[self.school_class],
        )
        save_grade_scale(
            academic_year=self.year,
            name="Second",
            bands=SCALE_BANDS,
            classes=[self.other_class],
        )
        self.assertEqual(GradeScale.objects.filter(academic_year=self.year).count(), 1)
        scale = GradeScale.objects.get(academic_year=self.year)
        self.assertEqual(scale.name, "Second")
        self.school_class.refresh_from_db()
        self.other_class.refresh_from_db()
        self.assertIsNone(self.school_class.grade_scale_id)
        self.assertEqual(self.other_class.grade_scale_id, scale.pk)

    def test_save_rejects_a_class_from_another_year(self):
        other_year = AcademicYear.objects.create(
            name="2024/2025",
            start_date=datetime.date(2024, 9, 1),
            end_date=datetime.date(2025, 7, 31),
        )
        foreign_class = SchoolClass.objects.create(
            academic_year=other_year, name="Grade 4", level_order=4
        )
        with self.assertRaises(ValidationError) as ctx:
            save_grade_scale(
                academic_year=self.year,
                name="Bad",
                bands=SCALE_BANDS,
                classes=[foreign_class],
            )
        self.assertIn("classes", ctx.exception.message_dict)

    def test_save_validates_bands(self):
        with self.assertRaises(ValidationError) as ctx:
            save_grade_scale(
                academic_year=self.year,
                name="Gappy",
                bands=[{"min_score": 0, "max_score": 49, "grade": "F"}],
                classes=[],
            )
        self.assertIn("bands", ctx.exception.message_dict)

    def test_save_regrades_already_entered_marks(self):
        sheet = self._term_sheet()
        entry = ScoreEntry.objects.create(
            result_sheet=sheet,
            student=self.student,
            raw_score=Decimal(60),
            total=Decimal(60),
            grade="A",
            remark="Excellent",
            points=Decimal(4),
        )

        result = save_grade_scale(
            academic_year=self.year,
            name="Upper Primary",
            bands=SCALE_BANDS,
            classes=[self.school_class],
            actor=self.admin,
        )
        self.assertEqual(result["regrade"]["changed"], 1)
        entry.refresh_from_db()
        self.assertEqual(
            (entry.grade, entry.remark, entry.points), ("P", "Pass", Decimal(2))
        )

    def test_delete_falls_classes_back_to_year_bands(self):
        save_grade_scale(
            academic_year=self.year,
            name="Upper Primary",
            bands=SCALE_BANDS,
            classes=[self.school_class],
        )
        entry = ScoreEntry.objects.create(
            result_sheet=self._term_sheet(),
            student=self.student,
            raw_score=Decimal(60),
            total=Decimal(60),
            grade="P",
            remark="Pass",
            points=Decimal(2),
        )

        scale = GradeScale.objects.get(academic_year=self.year)
        counts = delete_grade_scale(scale=scale, actor=self.admin)
        self.assertEqual(counts["changed"], 1)
        self.assertFalse(GradeScale.objects.filter(academic_year=self.year).exists())
        self.school_class.refresh_from_db()
        self.assertIsNone(self.school_class.grade_scale_id)
        entry.refresh_from_db()
        self.assertEqual(
            (entry.grade, entry.remark, entry.points), ("A", "Excellent", Decimal(4))
        )
        self.assertTrue(
            AuditLog.objects.filter(action="results.grade_scale_deleted").exists()
        )


class GradeScaleApplicationTests(GradeScaleTestData):
    def test_upsert_uses_class_scale(self):
        self.school_class.grade_scale = self.scale
        self.school_class.save(update_fields=["grade_scale"])

        entry = upsert_score_entry(
            sheet=self._term_sheet(),
            student=self.student,
            raw_score="60",
            actor=self.admin,
        )
        self.assertEqual(entry.raw_score, Decimal("60.00"))
        self.assertEqual(entry.total, Decimal("60.00"))
        self.assertEqual(entry.grade, "P")
        self.assertEqual(entry.remark, "Pass")
        self.assertEqual(entry.points, Decimal(2))

    def test_upsert_clears_a_mark_when_blank(self):
        sheet = self._term_sheet()
        upsert_score_entry(
            sheet=sheet, student=self.student, raw_score="60", actor=self.admin
        )
        self.assertIsNone(
            upsert_score_entry(
                sheet=sheet, student=self.student, raw_score="", actor=self.admin
            )
        )
        self.assertFalse(sheet.entries.exists())

    def test_upsert_rejects_a_mark_over_100(self):
        with self.assertRaises(ValidationError):
            upsert_score_entry(
                sheet=self._term_sheet(),
                student=self.student,
                raw_score="101",
                actor=self.admin,
            )

    def test_regrade_recomputes_assigned_class_only(self):
        sheet = self._term_sheet()
        other_sheet = self._term_sheet(school_class=self.other_class)
        entry = ScoreEntry.objects.create(
            result_sheet=sheet,
            student=self.student,
            total=Decimal(60),
            grade="A",
            remark="Excellent",
            points=Decimal(4),
        )
        other_entry = ScoreEntry.objects.create(
            result_sheet=other_sheet,
            student=self.other_student,
            total=Decimal(60),
            grade="A",
            remark="Excellent",
            points=Decimal(4),
        )
        self.school_class.grade_scale = self.scale
        self.school_class.save(update_fields=["grade_scale"])

        counts = regrade_entries_for_classes(
            classes=[self.school_class], actor=self.admin, scale=self.scale
        )
        self.assertEqual(counts, {"classes": 1, "entries": 1, "changed": 1})

        entry.refresh_from_db()
        other_entry.refresh_from_db()
        self.assertEqual(
            (entry.grade, entry.remark, entry.points), ("P", "Pass", Decimal(2))
        )
        self.assertEqual(other_entry.grade, "A")

        audit = AuditLog.objects.get(action="results.grade_scale_regraded")
        self.assertEqual(audit.change_summary["changed"], 1)

    def test_regrade_after_scale_removal_returns_to_year_bands(self):
        self.school_class.grade_scale = self.scale
        self.school_class.save(update_fields=["grade_scale"])
        entry = ScoreEntry.objects.create(
            result_sheet=self._term_sheet(),
            student=self.student,
            total=Decimal(60),
            grade="P",
            remark="Pass",
            points=Decimal(2),
        )

        self.scale.delete()
        self.school_class.refresh_from_db()
        self.assertIsNone(self.school_class.grade_scale_id)

        counts = regrade_entries_for_classes(
            classes=[self.school_class], actor=self.admin
        )
        self.assertEqual(counts["changed"], 1)
        entry.refresh_from_db()
        self.assertEqual(
            (entry.grade, entry.remark, entry.points), ("A", "Excellent", Decimal(4))
        )

    def test_regrade_ignores_unscored_entries(self):
        sheet = self._term_sheet()
        ScoreEntry.objects.create(
            result_sheet=sheet, student=self.student, total=None
        )
        self.school_class.grade_scale = self.scale
        self.school_class.save(update_fields=["grade_scale"])

        counts = regrade_entries_for_classes(
            classes=[self.school_class], actor=self.admin
        )
        self.assertEqual(counts["entries"], 0)
        self.assertEqual(counts["changed"], 0)

    def test_broadsheet_grades_follow_the_class_scale(self):
        exam = ensure_exams_for_term(self.term)[0]
        sheet = ResultSheet.objects.create(
            school_class=self.school_class,
            subject=self.maths,
            term=self.term,
            exam=exam,
        )
        ScoreEntry.objects.create(
            result_sheet=sheet,
            student=self.student,
            raw_score=Decimal(60),
            total=Decimal(60),
        )
        self.school_class.grade_scale = self.scale
        self.school_class.save(update_fields=["grade_scale"])

        data = selectors.exam_broadsheet(
            school_class=self.school_class, term=self.term, exam=exam
        )
        row = data["students"][0]
        self.assertEqual(row["cells"][0]["grade"], "P")
        self.assertEqual(row["grade"], "P")
