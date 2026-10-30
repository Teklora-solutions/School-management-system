"""
apps/teachers/tests/test_workload.py
-------------------------------------
AW1 — subject distribution by class and teacher workload.

Covers:
  - a class card shows its mapped subjects with the teacher of each
  - a mapped subject with no assignment is flagged ``unassigned``
  - a subject taught without a ClassSubject mapping still appears
  - workload counts classes, subjects and pupils once, ignoring past years
  - homeroom classes are recognised and unassigned teachers sort last
  - teacher detail groups assignments by year with the current year first
  - both pages are admin-only and honour the year selector
"""

from __future__ import annotations

import datetime

from django.test import TestCase
from django.urls import reverse

from apps.academics.models import (
    AcademicYear,
    ClassSubject,
    SchoolClass,
    Stream,
    Subject,
)
from apps.accounts.models import User
from apps.students.models import Student

from .. import selectors
from ..models import TeacherAssignment, TeacherProfile


def _teacher(n: int, first: str) -> tuple[User, TeacherProfile]:
    user = User.objects.create_user(
        username=f"wl_teacher_{n}",
        email=f"wl_teacher_{n}@test.school",
        password="Testpass1!",
        role=User.Role.TEACHER,
        first_name=first,
        last_name=f"Teacher{n}",
        must_change_password=False,
    )
    return user, TeacherProfile.objects.create(user=user, staff_id=f"WL-{n:04d}")


def _student(n: int, cls: SchoolClass, status: str = Student.Status.ACTIVE) -> Student:
    user = User.objects.create_user(
        username=f"wl_student_{n}",
        email=f"wl_student_{n}@test.school",
        password="Testpass1!",
        role=User.Role.STUDENT,
        must_change_password=False,
    )
    return Student.objects.create(
        user=user, admission_number=f"WL-ADM-{n:03d}", current_class=cls, status=status
    )


class WorkloadFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2026/2027",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 7, 31),
            is_current=True,
        )
        cls.old_year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
        )
        cls.grade5 = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.grade6 = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 6", level_order=6
        )
        cls.old_class = SchoolClass.objects.create(
            academic_year=cls.old_year, name="Grade 4", level_order=4
        )
        cls.maths = Subject.objects.create(name="Mathematics", code="MATH")
        cls.english = Subject.objects.create(name="English", code="ENG")

        for cls_, subjects in (
            (cls.grade5, [cls.maths, cls.english]),
            (cls.grade6, [cls.maths]),
            (cls.old_class, [cls.maths]),
        ):
            for subject in subjects:
                ClassSubject.objects.create(school_class=cls_, subject=subject)

        cls.a_user, cls.teacher_a = _teacher(1, "Ama")
        cls.b_user, cls.teacher_b = _teacher(2, "Ben")
        cls.c_user, cls.teacher_c = _teacher(3, "Cleo")

        cls.grade5.class_teacher = cls.a_user
        cls.grade5.save(update_fields=["class_teacher"])

        for cls_, subject in (
            (cls.grade5, cls.maths),
            (cls.grade5, cls.english),
            (cls.grade6, cls.maths),
        ):
            TeacherAssignment.objects.create(
                teacher=cls.teacher_a,
                school_class=cls_,
                subject=subject,
                academic_year=cls.year,
            )
        TeacherAssignment.objects.create(
            teacher=cls.teacher_a,
            school_class=cls.old_class,
            subject=cls.maths,
            academic_year=cls.old_year,
        )

        for n in range(2):
            _student(100 + n, cls.grade5)
        _student(102, cls.grade5, status=Student.Status.WITHDRAWN)
        for n in range(3):
            _student(200 + n, cls.grade6)


class SubjectDistributionTests(WorkloadFixture):
    def _row(self, cards, class_id, subject_id):
        card = next(c for c in cards if c["class"].pk == class_id)
        return card, next(r for r in card["rows"] if r["subject"].pk == subject_id)

    def test_mapped_subjects_show_their_teacher(self):
        result = selectors.subject_distribution(year_id=self.year.pk)
        card, row = self._row(result["cards"], self.grade5.pk, self.maths.pk)
        self.assertEqual(row["state"], "ok")
        self.assertEqual(row["teacher_name"], "Ama Teacher1")
        self.assertTrue(card["class_teacher_name"])

    def test_mapped_subject_without_teacher_is_unassigned(self):
        TeacherAssignment.objects.filter(
            school_class=self.grade5, subject=self.english
        ).delete()
        result = selectors.subject_distribution(year_id=self.year.pk)
        card, row = self._row(result["cards"], self.grade5.pk, self.english.pk)
        self.assertEqual(row["state"], "unassigned")
        self.assertEqual(card["unassigned_count"], 1)
        self.assertEqual(result["unassigned_count"], 1)

    def test_subject_assigned_to_one_stream_shows_that_teacher(self):
        blue = Stream.objects.create(school_class=self.grade5, name="Blue")
        TeacherAssignment.objects.filter(
            school_class=self.grade5, subject=self.maths, stream__isnull=True
        ).delete()
        TeacherAssignment.objects.create(
            teacher=self.teacher_a,
            school_class=self.grade5,
            subject=self.maths,
            academic_year=self.year,
            stream=blue,
        )
        result = selectors.subject_distribution(year_id=self.year.pk)
        _, row = self._row(result["cards"], self.grade5.pk, self.maths.pk)
        self.assertEqual(row["state"], "ok")
        self.assertEqual(row["teacher_name"], "Ama Teacher1")
        self.assertEqual(result["unassigned_count"], 0)

    def test_assignment_without_mapping_still_appears(self):
        physics = Subject.objects.create(name="Physics", code="PHY")
        TeacherAssignment.objects.create(
            teacher=self.teacher_b,
            school_class=self.grade6,
            subject=physics,
            academic_year=self.year,
        )
        result = selectors.subject_distribution(year_id=self.year.pk)
        _, row = self._row(result["cards"], self.grade6.pk, physics.pk)
        self.assertFalse(row["mapped"])
        self.assertEqual(row["teacher_name"], "Ben Teacher2")

    def test_past_year_is_not_mixed_in(self):
        result = selectors.subject_distribution(year_id=self.year.pk)
        self.assertEqual(
            [c["class"].pk for c in result["cards"]], [self.grade5.pk, self.grade6.pk]
        )
        self.assertTrue(all(c["class"].academic_year_id == self.year.pk for c in result["cards"]))

    def test_class_without_classes_gives_empty_cards(self):
        empty = AcademicYear.objects.create(
            name="2028/2029",
            start_date=datetime.date(2028, 9, 1),
            end_date=datetime.date(2029, 7, 31),
        )
        result = selectors.subject_distribution(year_id=empty.pk)
        self.assertEqual(result["cards"], [])
        self.assertEqual(result["class_count"], 0)


class TeacherWorkloadTests(WorkloadFixture):
    def _row(self, rows, teacher):
        return next(r for r in rows if r["teacher"].pk == teacher.pk)

    def test_counts_are_per_teacher_and_ignore_other_years(self):
        result = selectors.teacher_workload(year_id=self.year.pk)
        row = self._row(result["rows"], self.teacher_a)
        self.assertEqual(row["class_count"], 2)
        self.assertEqual(row["subject_count"], 2)
        self.assertEqual(row["assignment_count"], 3)
        # two active pupils in Grade 5 + three in Grade 6; withdrawn excluded,
        # and the old-year class is not added twice.
        self.assertEqual(row["student_count"], 5)
        self.assertEqual([c.name for c in row["homeroom"]], ["Grade 5"])

    def test_teacher_without_assignments_sorts_last(self):
        result = selectors.teacher_workload(year_id=self.year.pk)
        self.assertEqual(result["rows"][0]["teacher"].pk, self.teacher_a.pk)
        idle = result["rows"][-2:]
        self.assertEqual(
            [r["teacher"].pk for r in idle], [self.teacher_b.pk, self.teacher_c.pk]
        )
        self.assertEqual([r["assignment_count"] for r in idle], [0, 0])
        self.assertEqual([r["student_count"] for r in idle], [0, 0])

    def test_homeroom_without_assignments_is_still_homeroom(self):
        self.grade6.class_teacher = self.c_user
        self.grade6.save(update_fields=["class_teacher"])
        result = selectors.teacher_workload(year_id=self.year.pk)
        row = self._row(result["rows"], self.teacher_c)
        self.assertEqual(row["class_count"], 0)
        self.assertEqual([c.name for c in row["homeroom"]], ["Grade 6"])
        self.assertEqual(result["homeroom_count"], 2)

    def test_pupil_is_counted_once_for_a_teacher_with_two_subjects(self):
        result = selectors.teacher_workload(year_id=self.year.pk)
        row = self._row(result["rows"], self.teacher_a)
        # Grade 5 contributes 2 active pupils once, not once per subject.
        self.assertEqual(row["student_count"], 5)


class TeacherDetailGroupsTests(WorkloadFixture):
    def test_groups_current_year_first_then_older(self):
        groups = selectors.teacher_detail_groups(teacher_pk=self.teacher_a.pk)
        self.assertEqual([g["year"].pk for g in groups], [self.year.pk, self.old_year.pk])
        current = groups[0]
        self.assertEqual(
            [c["class"].pk for c in current["classes"]],
            [self.grade5.pk, self.grade6.pk],
        )
        self.assertTrue(current["classes"][0]["is_homeroom"])
        self.assertEqual(current["classes"][0]["subjects"], ["English", "Mathematics"])

    def test_summary_counts_are_scoped_to_the_year(self):
        summary = selectors.teacher_profile_summary(
            teacher_pk=self.teacher_a.pk, year_id=self.year.pk
        )
        self.assertEqual(summary["class_count"], 2)
        self.assertEqual(summary["subject_count"], 2)
        self.assertEqual(summary["student_count"], 5)
        self.assertEqual(summary["homeroom_count"], 1)

    def test_summary_without_a_year_is_empty(self):
        summary = selectors.teacher_profile_summary(
            teacher_pk=self.teacher_a.pk, year_id=None
        )
        self.assertEqual(summary["class_count"], 0)
        self.assertIsNone(summary["year"])


class WorkloadViewTests(WorkloadFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = User.objects.create_user(
            username="wl_admin",
            email="wl_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )

    def test_requires_login(self):
        for name in ("teachers:subject_distribution", "teachers:workload"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login/", response["Location"])

    def test_non_admin_is_refused(self):
        self.client.force_login(self.a_user)
        for name in ("teachers:subject_distribution", "teachers:workload"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 403)

    def test_distribution_page_renders_cards(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("teachers:subject_distribution"), {"year": self.year.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_year_id"], self.year.pk)
        self.assertEqual(len(response.context["cards"]), 2)
        self.assertContains(response, "Subject Distribution by Class")
        self.assertContains(response, "Ama Teacher1")

    def test_workload_page_renders_rows(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("teachers:workload"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_year_id"], self.year.pk)
        self.assertEqual(len(response.context["rows"]), 3)
        self.assertContains(response, "Teacher Workload")
        self.assertContains(response, "Grade 5")

    def test_year_selector_scopes_the_pages(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("teachers:workload"), {"year": self.old_year.pk}
        )
        row = next(
            r for r in response.context["rows"] if r["teacher"].pk == self.teacher_a.pk
        )
        self.assertEqual(row["class_count"], 1)
        self.assertEqual(response.context["selected_year_id"], self.old_year.pk)

    def test_invalid_year_falls_back_to_the_current_year(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("teachers:subject_distribution"), {"year": "999999"}
        )
        self.assertEqual(response.context["selected_year_id"], self.year.pk)

    def test_teacher_detail_shows_homeroom_and_number_tiles(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("teachers:detail", args=[self.teacher_a.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Class teacher")
        self.assertEqual(response.context["summary"]["class_count"], 2)
        self.assertEqual(len(response.context["groups"]), 2)
