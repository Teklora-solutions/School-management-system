"""
apps/learning/tests/test_live_classes.py
----------------------------------------
Tests for Checkpoint D: Live Class sessions (Option 1 - Link-out).
"""

from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.core.models import SchoolSettings
from apps.learning import live_class_services
from apps.learning.live_class_utils import normalize_meeting_url, validate_meeting_url
from apps.learning.models import LiveClassAttendance, LiveClassSession
from apps.students.models import Student

User = get_user_model()


class LiveClassUtilsTests(TestCase):
    def test_normalize_meeting_url(self):
        self.assertEqual(normalize_meeting_url("meet.google.com/abc-defg-hij"), "https://meet.google.com/abc-defg-hij")
        self.assertEqual(normalize_meeting_url("https://zoom.us/j/123"), "https://zoom.us/j/123")
        self.assertEqual(normalize_meeting_url(""), "")

    def test_validate_google_meet_url(self):
        valid, msg = validate_meeting_url("https://meet.google.com/abc-defg-hij", "google_meet")
        self.assertTrue(valid)
        self.assertEqual(msg, "")

        invalid, msg = validate_meeting_url("https://example.com/not-meet", "google_meet")
        self.assertFalse(invalid)
        self.assertIn("Google Meet", msg)

    def test_validate_zoom_url(self):
        valid, msg = validate_meeting_url("https://us04web.zoom.us/j/1234567890", "zoom")
        self.assertTrue(valid)

        invalid, msg = validate_meeting_url("https://phishing-zoom.com/j/123", "zoom")
        self.assertFalse(invalid)

    def test_validate_teams_url(self):
        valid, msg = validate_meeting_url("https://teams.microsoft.com/l/meetup-join/19...", "teams")
        self.assertTrue(valid)

        invalid, msg = validate_meeting_url("https://teams-fake.org", "teams")
        self.assertFalse(invalid)

    def test_reject_unsafe_schemes(self):
        valid, msg = validate_meeting_url("javascript:alert(1)", "other")
        self.assertFalse(valid)
        self.assertIn("HTTP", msg)


class LiveClassSessionTests(TestCase):
    def setUp(self):
        # Enable features
        settings = SchoolSettings.load()
        settings.learning_enabled = True
        settings.live_classes_enabled = True
        settings.save()

        # Academic scaffold
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
        self.other_class = SchoolClass.objects.create(
            academic_year=self.year,
            name="Basic 8",
            level_order=8,
        )
        self.subject = Subject.objects.create(name="Mathematics", code="MTH")

        # Teacher user
        self.teacher_user = User.objects.create_user(
            username="teacher_mth",
            email="mth@school.edu",
            password="REMOVED-DEMO-PASSWORD",
            role=User.Role.TEACHER,
            must_change_password=False,
        )

        # Enrolled student
        self.student_user = User.objects.create_user(
            username="student_ama",
            email="ama@student.edu",
            password="REMOVED-DEMO-PASSWORD",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        self.student = Student.objects.create(
            user=self.student_user,
            admission_number="ADM001",
            current_class=self.school_class,
        )

        # Unenrolled student
        self.other_student_user = User.objects.create_user(
            username="student_kofi",
            email="kofi@student.edu",
            password="REMOVED-DEMO-PASSWORD",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        self.other_student = Student.objects.create(
            user=self.other_student_user,
            admission_number="ADM002",
            current_class=self.other_class,
        )

        self.start_time = timezone.now() + datetime.timedelta(hours=2)

    def test_schedule_session_service(self):
        session = live_class_services.schedule_live_session(
            school_class=self.school_class,
            subject=self.subject,
            teacher=self.teacher_user,
            title="Algebra Live Review",
            scheduled_start=self.start_time,
            duration_minutes=60,
            platform="google_meet",
            meeting_url="https://meet.google.com/abc-defg-hij",
            meeting_id="abc-defg-hij",
            passcode="12345",
            description="Bring graph paper.",
        )
        self.assertEqual(session.title, "Algebra Live Review")
        self.assertEqual(session.status, LiveClassSession.Status.SCHEDULED)
        self.assertEqual(session.duration_minutes, 60)
        self.assertEqual(session.scheduled_end, self.start_time + datetime.timedelta(minutes=60))

    def test_schedule_session_invalid_url_raises(self):
        with self.assertRaises(ValidationError):
            live_class_services.schedule_live_session(
                school_class=self.school_class,
                subject=self.subject,
                teacher=self.teacher_user,
                title="Bad Session",
                scheduled_start=self.start_time,
                platform="zoom",
                meeting_url="https://unauthorized-domain.com",
            )

    def test_teacher_can_edit_session(self):
        session = live_class_services.schedule_live_session(
            school_class=self.school_class,
            subject=self.subject,
            teacher=self.teacher_user,
            title="Initial Title",
            scheduled_start=self.start_time,
            platform="google_meet",
            meeting_url="https://meet.google.com/abc-defg-hij",
        )
        updated = live_class_services.update_live_session(
            session=session,
            title="Updated Title",
            scheduled_start=self.start_time + datetime.timedelta(minutes=30),
            duration_minutes=50,
            platform="google_meet",
            meeting_url="https://meet.google.com/abc-defg-hij",
        )
        self.assertEqual(updated.title, "Updated Title")
        self.assertEqual(updated.duration_minutes, 50)

    def test_teacher_can_cancel_session(self):
        session = live_class_services.schedule_live_session(
            school_class=self.school_class,
            subject=self.subject,
            teacher=self.teacher_user,
            title="Will Be Cancelled",
            scheduled_start=self.start_time,
            platform="google_meet",
            meeting_url="https://meet.google.com/abc-defg-hij",
        )
        cancelled = live_class_services.cancel_live_session(session=session)
        self.assertEqual(cancelled.status, LiveClassSession.Status.CANCELLED)
        self.assertFalse(cancelled.join_window_open)

    def test_student_join_records_attendance(self):
        # Session starting in 5 minutes (within join window: starts -15min to end +30min)
        session = live_class_services.schedule_live_session(
            school_class=self.school_class,
            subject=self.subject,
            teacher=self.teacher_user,
            title="Live Right Now",
            scheduled_start=timezone.now() + datetime.timedelta(minutes=5),
            duration_minutes=45,
            platform="google_meet",
            meeting_url="https://meet.google.com/abc-defg-hij",
        )
        self.assertTrue(session.join_window_open)

        self.client.force_login(self.student_user)
        resp = self.client.get(reverse("learning:student_live_join", kwargs={"session_id": session.pk}))
        # Must redirect to meeting URL
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://meet.google.com/abc-defg-hij")

        # Attendance recorded
        self.assertTrue(LiveClassAttendance.objects.filter(session=session, student=self.student).exists())

    def test_student_cannot_join_unenrolled_class_session(self):
        session = live_class_services.schedule_live_session(
            school_class=self.school_class,
            subject=self.subject,
            teacher=self.teacher_user,
            title="Maths Session",
            scheduled_start=timezone.now() + datetime.timedelta(minutes=5),
            platform="google_meet",
            meeting_url="https://meet.google.com/abc-defg-hij",
        )
        # Login student from other class
        self.client.force_login(self.other_student_user)
        resp = self.client.get(reverse("learning:student_live_join", kwargs={"session_id": session.pk}))
        self.assertEqual(resp.status_code, 403)

    def test_feature_flag_disabled_renders_notice(self):
        settings = SchoolSettings.load()
        settings.live_classes_enabled = False
        settings.save()

        self.client.force_login(self.teacher_user)
        resp = self.client.get(
            reverse("learning:teacher_live_sessions", kwargs={"class_id": self.school_class.pk, "subject_id": self.subject.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Available once the Live Classes module is activated")
