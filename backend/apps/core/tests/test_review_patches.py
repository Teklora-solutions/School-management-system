"""Regression tests for academics, students, public, community, and accounts patches."""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.academics.forms import ClassSubjectForm
from apps.academics.models import AcademicYear, ClassSubject, SchoolClass, Stream, Subject
from apps.accounts.models import User
from apps.community.models import Category
from apps.core.models import SchoolSettings
from apps.students.models import Student
from apps.students.services import update_student
from apps.teachers.models import TeacherAssignment, TeacherProfile


class AcademicsRegressionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="acad_admin",
            email="acad_admin@example.com",
            password="Password123!",
            role="admin",
            must_change_password=False,
        )
        self.year = AcademicYear.objects.create(
            name="2026",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
            is_current=True,
        )
        self.c1 = SchoolClass.objects.create(academic_year=self.year, name="Grade 1", level_order=1)
        self.c2 = SchoolClass.objects.create(academic_year=self.year, name="Grade 2", level_order=2)
        self.stream2 = Stream.objects.create(school_class=self.c2, name="Blue")
        self.subj1 = Subject.objects.create(name="Math", code="MATH")
        self.subj2 = Subject.objects.create(name="English", code="ENG")

    def test_class_subject_form_rejects_mismatched_stream(self):
        form = ClassSubjectForm(
            data={
                "school_class": self.c1.pk,
                "subject": self.subj1.pk,
                "stream": self.stream2.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("stream", form.errors)

    def test_class_subject_update_view_blocks_repoint_when_teacher_assigned(self):
        mapping = ClassSubject.objects.create(school_class=self.c1, subject=self.subj1)
        t_user = User.objects.create_user(
            username="acad_teacher",
            email="acad_teacher@example.com",
            password="Password123!",
            role="teacher",
        )
        TeacherProfile.objects.create(user=t_user, staff_id="T-101")
        TeacherAssignment.objects.create(
            teacher=t_user.teacher_profile,
            school_class=self.c1,
            subject=self.subj1,
            academic_year=self.year,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("academics:class_subject_update", kwargs={"pk": mapping.pk}),
            data={"school_class": self.c2.pk, "subject": self.subj2.pk, "stream": ""},
        )
        self.assertIn(resp.status_code, (200, 302))
        mapping.refresh_from_db()
        self.assertEqual(mapping.school_class, self.c1)
        self.assertEqual(mapping.subject, self.subj1)


class StudentRegressionTests(TestCase):
    def test_update_student_rejects_stream_when_class_is_none(self):
        year = AcademicYear.objects.create(
            name="2026",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
            is_current=True,
        )
        c1 = SchoolClass.objects.create(academic_year=year, name="Grade 1", level_order=1)
        stream1 = Stream.objects.create(school_class=c1, name="Red")
        s_user = User.objects.create_user(
            username="stu1",
            email="stu1@example.com",
            password="Password123!",
            role="student",
            first_name="A",
            last_name="B",
        )
        student = Student.objects.create(
            user=s_user, admission_number="ADM-1", current_class=c1
        )
        with self.assertRaises(ValidationError):
            update_student(
                student=student,
                admission_number="ADM-1",
                first_name="A",
                last_name="B",
                email="stu1@example.com",
                current_class=None,
                current_stream=stream1,
            )


class PublicEditorAndCommunityRegressionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="pub_admin",
            email="pub_admin@example.com",
            password="Password123!",
            role="admin",
            must_change_password=False,
        )
        self.director = User.objects.create_user(
            username="pub_director",
            email="pub_director@example.com",
            password="Password123!",
            role="director",
            must_change_password=False,
        )

    def test_page_editor_saves_cta_and_about_sections(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("public_admin:editor_home"),
            data={"section": "cta", "cta_title": "Join Us", "cta_text": "Apply today."},
        )
        self.assertEqual(resp.status_code, 302)
        row = SchoolSettings.objects.get(pk=SchoolSettings.SINGLETON_PK)
        self.assertEqual(row.cta_title, "Join Us")

        resp2 = self.client.post(
            reverse("public_admin:editor_about"),
            data={
                "section": "story",
                "about": "Our story",
                "mission": "Our mission",
                "vision": "Our vision",
                "values": "Integrity",
            },
        )
        self.assertEqual(resp2.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.about, "Our story")

    def test_director_blocked_from_community_mutations_and_open_redirect_safe(self):
        cs = SchoolSettings.load()
        cs.feature_community = True
        cs.save()
        Category.objects.create(name="General", slug="general")

        self.client.force_login(self.director)
        resp = self.client.post(
            reverse("community:admin_word_filter"),
            data={"action": "add", "word": "badword"},
        )
        self.assertEqual(resp.status_code, 403)

        resp_cat = self.client.post(
            reverse("community:admin_category_create"),
            data={"name": "NewCat", "display_order": "notanint"},
        )
        self.assertEqual(resp_cat.status_code, 403)

        self.client.force_login(self.admin)
        resp_missing = self.client.get(
            reverse("community:admin_category_edit", kwargs={"pk": 99999})
        )
        self.assertEqual(resp_missing.status_code, 404)

        resp_consent = self.client.post(
            reverse("community:consent"),
            data={"consent": "yes", "next": "https://evil.example/phish"},
        )
        self.assertEqual(resp_consent.status_code, 302)
        self.assertNotIn("evil.example", resp_consent["Location"])

    def test_staff_create_does_not_flash_plaintext_password(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("accounts:staff_create"),
            data={
                "role": "teacher",
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "janedoe@example.com",
                "password": "UniqueSecretPass!99",
                "confirm_password": "UniqueSecretPass!99",
                "must_change_password": "on",
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "UniqueSecretPass!99")
