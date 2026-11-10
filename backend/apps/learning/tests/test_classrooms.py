"""
apps/learning/tests/test_classrooms.py
--------------------------------------
L0 — the classroom shell. Classrooms are computed from existing school data,
gated behind the ``learning`` feature, and scoped so a teacher sees only their
assignments and a pupil only their own class.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ParentProfile
from apps.academics.models import ClassSubject, Stream
from apps.core.features import feature_enabled
from apps.core.models import SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.students.models import StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile


def enable_learning() -> None:
    row = SchoolSettings.load()
    row.learning_enabled = True
    row.save(update_fields=["learning_enabled", "updated_at"])


def make_teacher(username: str, staff_id: str):
    user = make_user("teacher", username=username)
    profile = TeacherProfile.objects.create(user=user, staff_id=staff_id)
    return user, profile


class LearningDisabledTests(TestCase):
    """With the switch off, the module is a friendly notice, not a dead link."""

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.subject = make_subject()
        cls.teacher_user, cls.teacher = make_teacher("off_teacher", "STF-OFF")
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.subject,
            academic_year=cls.year,
        )

    def test_feature_is_off_by_default(self):
        self.assertFalse(feature_enabled("learning"))

    def test_teacher_sees_disabled_notice_not_classrooms(self):
        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse("learning:teacher_classrooms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Available once the Learning module is activated.")
        self.assertNotContains(response, "My Classrooms")

    def test_student_sees_disabled_notice(self):
        student = make_student_user(self.school_class, admission_number="OFF-001")
        ClassSubject.objects.create(
            school_class=self.school_class, subject=self.subject
        )
        self.client.force_login(student.user)
        response = self.client.get(reverse("learning:classrooms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Available once the Learning module is activated.")


class LearningClassroomsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.class_a = make_class(cls.year, name="Grade 7", level_order=7)
        cls.class_b = make_class(cls.year, name="Grade 8", level_order=8)
        cls.maths = make_subject("MATH", "Mathematics")
        cls.science = make_subject("SCI", "Science")

        # Teacher one teaches Maths to Grade 7; teacher two Science to Grade 8.
        cls.teacher_user, cls.teacher = make_teacher("lms_teacher", "STF-100")
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.class_a,
            subject=cls.maths,
            academic_year=cls.year,
        )
        cls.other_teacher_user, cls.other_teacher = make_teacher(
            "lms_other", "STF-200"
        )
        TeacherAssignment.objects.create(
            teacher=cls.other_teacher,
            school_class=cls.class_b,
            subject=cls.science,
            academic_year=cls.year,
        )

        # Subjects offered to each class (drives the pupil's classrooms).
        ClassSubject.objects.create(school_class=cls.class_a, subject=cls.maths)
        ClassSubject.objects.create(school_class=cls.class_b, subject=cls.science)

        cls.pupil = make_student_user(cls.class_a, admission_number="LMS-001")
        cls.pupil_two = make_student_user(cls.class_a, admission_number="LMS-002")
        cls.other_pupil = make_student_user(cls.class_b, admission_number="LMS-003")

    # ── Teacher ────────────────────────────────────────────────────────────

    def test_teacher_sees_own_classroom_with_enrolled_count(self):
        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse("learning:teacher_classrooms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grade 7")
        self.assertContains(response, "Mathematics")
        self.assertContains(response, "My Classrooms")
        # Two active pupils are enrolled automatically.
        self.assertEqual(response.context["classrooms"][0].student_count, 2)

    def test_teacher_does_not_see_other_teachers_classroom(self):
        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse("learning:teacher_classrooms"))
        self.assertNotContains(response, "Grade 8")
        self.assertEqual(len(response.context["classrooms"]), 1)

    def test_teacher_detail_shows_roster(self):
        self.client.force_login(self.teacher_user)
        url = reverse(
            "learning:teacher_classroom", args=[self.class_a.pk, self.maths.pk]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LMS-001")
        self.assertContains(response, "LMS-002")
        self.assertNotContains(response, "LMS-003")

    def test_teacher_detail_for_unassigned_classroom_is_404(self):
        self.client.force_login(self.teacher_user)
        url = reverse(
            "learning:teacher_classroom", args=[self.class_b.pk, self.science.pk]
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_teacher_with_no_assignments_sees_empty_state(self):
        lonely_user, _ = make_teacher("lms_lonely", "STF-300")
        self.client.force_login(lonely_user)
        response = self.client.get(reverse("learning:teacher_classrooms"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["classrooms"], [])
        self.assertContains(response, "no subject assignments")

    # ── Student ────────────────────────────────────────────────────────────

    def test_student_sees_own_subject_with_teacher_name(self):
        self.client.force_login(self.pupil.user)
        response = self.client.get(reverse("learning:classrooms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mathematics")
        self.assertContains(response, "Test Teacher")  # make_user last_name = role.title()
        self.assertNotContains(response, "Science")

    def test_student_detail_for_other_class_is_404(self):
        self.client.force_login(self.pupil.user)
        url = reverse("learning:classroom", args=[self.class_b.pk, self.science.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_student_detail_for_unoffered_subject_is_404(self):
        self.client.force_login(self.pupil.user)
        url = reverse("learning:classroom", args=[self.class_a.pk, self.science.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_student_detail_own_classroom_renders(self):
        self.client.force_login(self.pupil.user)
        url = reverse("learning:classroom", args=[self.class_a.pk, self.maths.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mathematics")

    # ── Parent ─────────────────────────────────────────────────────────────

    def test_parent_sees_active_childs_classrooms_read_only(self):
        parent_user = make_user("parent", username="lms_parent")
        profile = ParentProfile.objects.create(user=parent_user)
        StudentGuardian.objects.create(
            student=self.pupil, parent=profile, is_primary=True
        )
        self.client.force_login(parent_user)
        response = self.client.get(reverse("learning:classrooms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mathematics")
        self.assertTrue(response.context["is_parent"])
        detail = self.client.get(
            reverse("learning:classroom", args=[self.class_a.pk, self.maths.pk])
        )
        self.assertContains(detail, "read-only")

    # ── Role guards ────────────────────────────────────────────────────────

    def test_student_cannot_open_teacher_classrooms(self):
        self.client.force_login(self.pupil.user)
        response = self.client.get(reverse("learning:teacher_classrooms"))
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_open_student_classrooms(self):
        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse("learning:classrooms"))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("learning:classrooms"))
        self.assertEqual(response.status_code, 302)


class LearningStreamTests(TestCase):
    """Stream-specific assignments narrow the roster; whole-class still applies."""

    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 7", level_order=7)
        cls.blue = Stream.objects.create(school_class=cls.school_class, name="Blue")
        cls.gold = Stream.objects.create(school_class=cls.school_class, name="Gold")
        cls.subject = make_subject("SCI", "Science")

        cls.teacher_user, cls.teacher = make_teacher("stream_teacher", "STF-S1")
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.subject,
            stream=cls.blue,
            academic_year=cls.year,
        )
        # Whole-class subject entry plus a Blue-specific one.
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.subject)
        ClassSubject.objects.create(
            school_class=cls.school_class, subject=cls.subject, stream=cls.blue
        )

        cls.blue_pupil = make_student_user(cls.school_class, admission_number="BLU-1")
        cls.blue_pupil.current_stream = cls.blue
        cls.blue_pupil.save(update_fields=["current_stream", "updated_at"])
        cls.gold_pupil = make_student_user(cls.school_class, admission_number="GLD-1")
        cls.gold_pupil.current_stream = cls.gold
        cls.gold_pupil.save(update_fields=["current_stream", "updated_at"])

    def test_teacher_stream_assignment_counts_only_that_stream(self):
        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse("learning:teacher_classrooms"))
        self.assertEqual(response.context["classrooms"][0].student_count, 1)
        detail = self.client.get(
            reverse(
                "learning:teacher_classroom",
                args=[self.school_class.pk, self.subject.pk],
            )
        )
        self.assertContains(detail, "BLU-1")
        self.assertNotContains(detail, "GLD-1")

    def test_pupil_in_stream_sees_the_subject(self):
        self.client.force_login(self.blue_pupil.user)
        response = self.client.get(reverse("learning:classrooms"))
        self.assertContains(response, "Science")
        self.assertEqual(len(response.context["classrooms"]), 1)