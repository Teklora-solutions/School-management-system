"""
apps/teachers/tests/test_assignments.py
----------------------------------------
Module 1 — Teacher ↔ Class ↔ Subject assignments.

Covers:
  - duplicate assignments are rejected
  - one teacher per class/subject per year, regardless of stream, enforced
    both by the service and by a database constraint
  - class teacher is a single teacher (structural)
  - only teacher-role users can be class teachers
  - the assign form hides slots that are already taken
"""

from __future__ import annotations

import datetime
import json
import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import AcademicYear, SchoolClass, Stream, Subject
from apps.accounts.models import User

from ..forms import TeacherAssignmentForm
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

    def test_stream_scopes_the_assignment_but_not_uniqueness(self):
        stream = Stream.objects.create(school_class=self.school_class, name="Blue")
        gold = Stream.objects.create(school_class=self.school_class, name="Gold")
        assign_subject_to_teacher(
            teacher=self.teacher,
            school_class=self.school_class,
            subject=self.subject,
            academic_year=self.year,
            stream=stream,
        )
        # Same teacher, same cell, another stream: still a duplicate.
        with self.assertRaises(ValidationError) as ctx:
            assign_subject_to_teacher(
                teacher=self.teacher,
                school_class=self.school_class,
                subject=self.subject,
                academic_year=self.year,
                stream=gold,
            )
        self.assertIn("already exists", str(ctx.exception))
        # Another teacher is refused even with a *different* stream...
        with self.assertRaises(ValidationError) as ctx:
            assign_subject_to_teacher(
                teacher=self.other_teacher,
                school_class=self.school_class,
                subject=self.subject,
                academic_year=self.year,
                stream=gold,
            )
        self.assertIn("another teacher", str(ctx.exception))
        # ...and with no stream at all.
        with self.assertRaises(ValidationError):
            assign_subject_to_teacher(
                teacher=self.other_teacher,
                school_class=self.school_class,
                subject=self.subject,
                academic_year=self.year,
            )
        self.assertEqual(TeacherAssignment.objects.count(), 1)

    def test_database_rejects_a_second_assignment_for_the_cell(self):
        assign_subject_to_teacher(
            teacher=self.teacher,
            school_class=self.school_class,
            subject=self.subject,
            academic_year=self.year,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TeacherAssignment.objects.create(
                    teacher=self.other_teacher,
                    school_class=self.school_class,
                    subject=self.subject,
                    academic_year=self.year,
                )
        self.assertEqual(TeacherAssignment.objects.count(), 1)

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


class AssignmentFormFilterTests(TestCase):
    """
    The form's own lists are narrowed, so a subject that is already taken is
    not offered in the first place — but a submitted form still says *why* it
    was refused instead of "select a valid choice".
    """

    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.old_year = AcademicYear.objects.create(
            name="2024/2025",
            start_date=datetime.date(2024, 9, 1),
            end_date=datetime.date(2025, 7, 31),
        )
        cls.school_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.old_class = SchoolClass.objects.create(
            academic_year=cls.old_year, name="Grade 4", level_order=4
        )
        cls.stream = Stream.objects.create(school_class=cls.school_class, name="Blue")
        cls.maths = Subject.objects.create(name="Mathematics", code="MATH")
        cls.english = Subject.objects.create(name="English", code="ENG")
        cls.teacher_user, cls.teacher = _make_teacher(1)
        cls.other_user, cls.other_teacher = _make_teacher(2)

    def test_class_choices_are_scoped_to_the_selected_year(self):
        form = TeacherAssignmentForm(initial={"academic_year": self.year.pk})
        self.assertEqual(
            list(form.fields["school_class"].queryset), [self.school_class]
        )

    def test_subject_choices_hide_what_is_already_assigned(self):
        assign_subject_to_teacher(
            teacher=self.teacher,
            school_class=self.school_class,
            subject=self.maths,
            academic_year=self.year,
        )
        form = TeacherAssignmentForm(
            initial={"academic_year": self.year.pk, "school_class": self.school_class.pk}
        )
        self.assertEqual(list(form.fields["subject"].queryset), [self.english])

    def test_a_subject_assigned_in_any_stream_is_hidden_for_the_whole_class(self):
        assign_subject_to_teacher(
            teacher=self.teacher,
            school_class=self.school_class,
            subject=self.maths,
            academic_year=self.year,
            stream=self.stream,
        )
        all_streams = TeacherAssignmentForm(
            initial={"academic_year": self.year.pk, "school_class": self.school_class.pk}
        )
        self.assertNotIn(self.maths, all_streams.fields["subject"].queryset)

        in_blue = TeacherAssignmentForm(
            initial={
                "academic_year": self.year.pk,
                "school_class": self.school_class.pk,
                "stream": self.stream.pk,
            }
        )
        self.assertNotIn(self.maths, in_blue.fields["subject"].queryset)

    def test_stream_choices_are_scoped_to_the_selected_class(self):
        other_stream = Stream.objects.create(
            school_class=self.old_class, name="Gold"
        )
        form = TeacherAssignmentForm(
            initial={"academic_year": self.year.pk, "school_class": self.school_class.pk}
        )
        self.assertEqual(list(form.fields["stream"].queryset), [self.stream])
        self.assertNotIn(other_stream, form.fields["stream"].queryset)

    def test_without_a_class_there_are_no_streams_to_choose(self):
        form = TeacherAssignmentForm(initial={"academic_year": self.year.pk})
        self.assertEqual(list(form.fields["stream"].queryset), [])

    def test_a_submitted_form_keeps_every_subject(self):
        """The refusal must stay the service's wording, not "invalid choice"."""
        assign_subject_to_teacher(
            teacher=self.teacher,
            school_class=self.school_class,
            subject=self.maths,
            academic_year=self.year,
        )
        form = TeacherAssignmentForm(
            data={
                "academic_year": self.year.pk,
                "teacher": self.other_teacher.pk,
                "school_class": self.school_class.pk,
                "subject": self.maths.pk,
                "stream": "",
            }
        )
        self.assertIn(self.maths, form.fields["subject"].queryset)


class AssignmentFormViewTests(TestCase):
    """The page ships the taken slots and honours a pre-scoped class."""

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
        cls.maths = Subject.objects.create(name="Mathematics", code="MATH")
        cls.english = Subject.objects.create(name="English", code="ENG")
        cls.teacher_user, cls.teacher = _make_teacher(1)
        cls.other_user, cls.other_teacher = _make_teacher(2)
        cls.admin = User.objects.create_user(
            username="assign_admin",
            email="assign_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        assign_subject_to_teacher(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.maths,
            academic_year=cls.year,
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("teachers:assignment_create")

    def _picker(self, response) -> dict:
        match = re.search(
            r'<script id="assignment-picker" type="application/json">(.*?)</script>',
            response.content.decode(),
            re.S,
        )
        self.assertIsNotNone(match, "the page must ship the picker payload")
        return json.loads(match.group(1))

    def test_page_ships_the_slots_already_taken(self):
        picker = self._picker(self.client.get(self.url))
        self.assertIn(
            [self.school_class.pk, self.maths.pk],
            [[row[0], row[1]] for row in picker["taken"]],
        )
        self.assertEqual(
            [row["id"] for row in picker["classes"]], [self.school_class.pk]
        )

    def test_pre_scoped_class_hides_its_taken_subject(self):
        response = self.client.get(self.url, {"class": self.school_class.pk})
        form = response.context["form"]
        self.assertEqual(list(form.fields["subject"].queryset), [self.english])
        self.assertEqual(
            form.initial["school_class"], self.school_class.pk
        )

    def test_a_class_from_another_year_is_ignored(self):
        old_year = AcademicYear.objects.create(
            name="2024/2025",
            start_date=datetime.date(2024, 9, 1),
            end_date=datetime.date(2025, 7, 31),
        )
        old_class = SchoolClass.objects.create(
            academic_year=old_year, name="Grade 4", level_order=4
        )
        form = self.client.get(self.url, {"class": old_class.pk}).context["form"]
        self.assertNotIn("school_class", form.initial)

    def test_duplicate_post_still_reports_the_real_reason(self):
        response = self.client.post(
            self.url,
            {
                "academic_year": self.year.pk,
                "teacher": self.other_teacher.pk,
                "school_class": self.school_class.pk,
                "subject": self.maths.pk,
                "stream": "",
            },
        )
        self.assertContains(response, "already assigned to another teacher")
        self.assertEqual(TeacherAssignment.objects.count(), 1)
