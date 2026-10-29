"""
apps/homework/tests/test_scope.py
----------------------------------
Module 4: students only see published homework for their own class.
"""

from __future__ import annotations

import datetime

from django.test import TestCase
from django.urls import reverse

from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)

from ..models import Homework


class HomeworkScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.class_a = make_class(cls.year, name="Grade 8", level_order=8)
        cls.class_b = make_class(cls.year, name="Grade 9", level_order=9)
        cls.subject = make_subject("SCI", "Science")
        cls.teacher = make_user("teacher", username="hw_teacher")
        cls.student = make_student_user(cls.class_a, admission_number="HW-001")

        cls.own = Homework.objects.create(
            title="Own class assignment",
            subject=cls.subject,
            school_class=cls.class_a,
            term=cls.term,
            set_by=cls.teacher,
            due_date=datetime.date.today() + datetime.timedelta(days=2),
            status=Homework.Status.PUBLISHED,
        )
        cls.other = Homework.objects.create(
            title="Other class assignment",
            subject=cls.subject,
            school_class=cls.class_b,
            term=cls.term,
            set_by=cls.teacher,
            due_date=datetime.date.today() + datetime.timedelta(days=2),
            status=Homework.Status.PUBLISHED,
        )
        cls.draft = Homework.objects.create(
            title="Draft assignment",
            subject=cls.subject,
            school_class=cls.class_a,
            term=cls.term,
            set_by=cls.teacher,
            due_date=datetime.date.today() + datetime.timedelta(days=2),
            status=Homework.Status.DRAFT,
        )

    def test_student_sees_only_published_homework_for_own_class(self):
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("homework:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Own class assignment")
        self.assertNotContains(response, "Other class assignment")
        self.assertNotContains(response, "Draft assignment")

    def test_attachment_download_for_other_class_is_forbidden(self):
        self.other.attachment = "homework/other.txt"
        self.other.save(update_fields=["attachment", "updated_at"])
        self.client.force_login(self.student.user)
        response = self.client.get(
            reverse("homework:attachment_download", args=[self.other.pk])
        )
        self.assertEqual(response.status_code, 403)
