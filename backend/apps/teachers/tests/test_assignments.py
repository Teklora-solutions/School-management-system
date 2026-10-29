"""
apps/teachers/tests/test_assignments.py
----------------------------------------
Module 1 — Teacher ↔ Class ↔ Subject assignments.

Covers:
  - duplicate assignments are rejected
  - one teacher per class/subject(/stream) per year
  - class teacher is a single teacher (structural)
  - only teacher-role users can be class teachers
"""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.academics.models import AcademicYear, SchoolClass, Stream, Subject
from apps.accounts.models import User

from ..models import TeacherAssignment, TeacherProfile
from ..services import assign_subject_to_teacher, remove_assignment, set_class_teacher


def _make_teacher(n: int) -> tuple[User, TeacherProfile]:
    user = User.objects.create_user(
        username=f"teacher_{n}",
        email=f"teacher_{n}@test.school",
        password="Testpass1!",
        role=User.Role.TEACHER,
        first_name="Teacher",
        last_name=str(n),
        must_change_password=False,
    )
    return user, TeacherProfile.objects.create(user=user, staff_id=f"TCH-{n:04d}")


class TeacherAssignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.school_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.subject = Subject.objects.create(name="Mathematics", code="MATH")
        cls.other_subject = Subject.objects.create(name="English", code="ENG")
        cls.teacher_user, cls.teacher = _make_teacher(1)
        cls.other_user, cls.other_teacher = _make_teacher(2)

    def test_assignment_is_created(self):
        assignment = assign_subject_to_teacher(
            teacher=self.teacher,
            school_class=self.school_class,
            subject=self.subject,
            academic_year=self.year,
        )
        self.assertEqual(assignment.teacher, self.teacher)
        self.assertIsNone(assignment.stream)

    def test_duplicate_assignment_is_rejected(self):
        assign_subject_to_teacher(
            teacher=self.teacher,
            school_class=self.school_class,
            subject=self.subject,
            academic_year=self.year,
        )
        with self.assertRaises(ValidationError) as ctx:
            assign_subject_to_teacher(
                teacher=self.teacher,
                school_class=self.school_class,
                subject=self.subject,
                academic_year=self.year,
            )
        self.assertIn("already exists", str(ctx.exception))

    def test_same_class_subject_cannot_go_to_another_teacher(self):
        assign_subject_to_teacher(
            teacher=self.teacher,
            school_class=self.school_class,
            subject=self.subject,
            academic_year=self.year,
        )
        with self.assertRaises(ValidationError) as ctx:
            assign_subject_to_teacher(
                teacher=self.other_teacher,
                school_class=self.school_class,
                subject=self.subject,
                academic_year=self.year,
            )
        self.assertIn("another teacher", str(ctx.exception))
        self.assertEqual(TeacherAssignment.objects.count(), 1)

    def test_different_subject_is_allowed(self):
        assign_subject_to_teacher(
            teacher=self.teacher,
            school_class=self.school_class,
            subject=self.subject,
            academic_year=self.year,
        )
        assign_subject_to_teacher(
            teacher=self.other_teacher,
            school_class=self.school_class,
            subject=self.other_subject,
            academic_year=self.year,
        )
        self.assertEqual(TeacherAssignment.objects.count(), 2)

    def test_stream_specific_assignment_is_allowed(self):
        stream = Stream.objects.create(school_class=self.school_class, name="Blue")
        assign_subject_to_teacher(
            teacher=self.teacher,
            school_class=self.school_class,
            subject=self.subject,
            academic_year=self.year,
            stream=stream,
        )
        # The same cell is still unique for that stream.
        with self.assertRaises(ValidationError):
            assign_subject_to_teacher(
                teacher=self.other_teacher,
                school_class=self.school_class,
                subject=self.subject,
                academic_year=self.year,
                stream=stream,
            )

    def test_remove_assignment(self):
        assignment = assign_subject_to_teacher(
            teacher=self.teacher,
            school_class=self.school_class,
            subject=self.subject,
            academic_year=self.year,
        )
        remove_assignment(assignment=assignment)
        self.assertEqual(TeacherAssignment.objects.count(), 0)

    def test_class_teacher_is_single_and_must_be_teacher_role(self):
        set_class_teacher(
            school_class=self.school_class, teacher_user=self.teacher_user
        )
        self.school_class.refresh_from_db()
        self.assertEqual(self.school_class.class_teacher, self.teacher_user)

        set_class_teacher(school_class=self.school_class, teacher_user=self.other_user)
        self.school_class.refresh_from_db()
        self.assertEqual(self.school_class.class_teacher, self.other_user)

        student = User.objects.create_user(
            username="not_a_teacher",
            email="not_a_teacher@test.school",
            password="Testpass1!",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        with self.assertRaises(ValidationError):
            set_class_teacher(school_class=self.school_class, teacher_user=student)
        self.school_class.refresh_from_db()
        self.assertEqual(self.school_class.class_teacher, self.other_user)
