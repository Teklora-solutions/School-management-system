"""
apps/learning/tests/test_calendar.py
------------------------------------
Tests for Checkpoint D: Shared Class Calendar.
"""

from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.core.models import SchoolSettings
from apps.homework.models import Homework
from apps.learning import calendar_selectors, live_class_services
from apps.learning.models import CourseModule, Lesson, Quiz
from apps.students.models import Student

User = get_user_model()


class ClassCalendarTests(TestCase):
    def setUp(self):
        # Use queryset update to bypass any instance-level caching
        SchoolSettings.objects.update_or_create(
            pk=SchoolSettings.SINGLETON_PK,
            defaults={
                "learning_enabled": True,
                "live_classes_enabled": True,
            },
        )

        self.year = AcademicYear.objects.create(
            name="2026/2027",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 7, 31),
            is_current=True,
        )
        self.term = Term.objects.create(
            academic_year=self.year,
            name="term_1",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 15),
            is_current=True,
        )
        self.school_class = SchoolClass.objects.create(
            academic_year=self.year,
            name="Basic 7",
            level_order=7,
        )
        self.subject = Subject.objects.create(name="Science", code="SCI")

        self.teacher_user = User.objects.create_user(
            username="teacher_sci",
            email="sci@school.edu",
            password="test-only",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        self.student_user = User.objects.create_user(
            username="student_yaw",
            email="yaw@student.edu",
            password="test-only",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        self.student = Student.objects.create(
            user=self.student_user,
            admission_number="ADM003",
            current_class=self.school_class,
        )

    def test_calendar_selector_aggregates_events(self):
        # 1. Live class session
        session_time = timezone.now() + datetime.timedelta(days=2)
        live_class_services.schedule_live_session(
            school_class=self.school_class,
            subject=self.subject,
            teacher=self.teacher_user,
            title="Lab Safety Live Session",
            scheduled_start=session_time,
            platform="google_meet",
            meeting_url="https://meet.google.com/abc-defg-hij",
        )

        # 2. Homework deadline
        hw_due = timezone.localdate() + datetime.timedelta(days=4)
        Homework.objects.create(
            title="Plant Cell Diagram",
            school_class=self.school_class,
            subject=self.subject,
            term=self.term,
            set_by=self.teacher_user,
            due_date=hw_due,
            status=Homework.Status.PUBLISHED,
        )

        # 3. Quiz
        module = CourseModule.objects.create(
            school_class=self.school_class,
            subject=self.subject,
            term=self.term,
            title="Module 1: Cells",
            position=0,
        )
        lesson = Lesson.objects.create(
            module=module,
            title="Cell Structure",
            position=0,
            is_published=True,
        )
        Quiz.objects.create(
            lesson=lesson,
            title="Cell Anatomy Quiz",
            is_published=True,
        )

        cal_data = calendar_selectors.get_class_calendar_data(
            school_class_id=self.school_class.pk,
            year=timezone.localdate().year,
            month=timezone.localdate().month,
        )

        self.assertGreater(cal_data["total_events"], 0)
        self.assertIn(len(cal_data["calendar_weeks"]), [4, 5, 6])  # months vary 4-6 weeks
        self.assertIsNotNone(cal_data["month_name"])

    def test_calendar_view_accessible_by_student(self):
        self.client.force_login(self.student_user)
        resp = self.client.get(reverse("learning:class_calendar", kwargs={"class_id": self.school_class.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Basic 7 Calendar")

    def test_calendar_view_accessible_by_teacher(self):
        self.client.force_login(self.teacher_user)
        resp = self.client.get(reverse("learning:class_calendar", kwargs={"class_id": self.school_class.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Basic 7 Calendar")

    def test_calendar_view_forbidden_for_other_student(self):
        other_class = SchoolClass.objects.create(
            academic_year=self.year,
            name="Basic 9",
            level_order=9,
        )
        other_user = User.objects.create_user(
            username="other_stud",
            email="other@student.edu",
            password="test-only",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        Student.objects.create(
            user=other_user,
            admission_number="ADM004",
            current_class=other_class,
        )

        self.client.force_login(other_user)
        resp = self.client.get(reverse("learning:class_calendar", kwargs={"class_id": self.school_class.pk}))
        self.assertEqual(resp.status_code, 403)

