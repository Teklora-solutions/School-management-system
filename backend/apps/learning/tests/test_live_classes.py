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
from apps.accounts.models import ParentProfile
from apps.core.models import SchoolSettings
from apps.learning import live_class_services
from apps.learning.live_class_utils import normalize_meeting_url, validate_meeting_url
from apps.learning.models import LiveClassAttendance, LiveClassSession
from apps.students.models import Student, StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile

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
            password="test-only",
            role=User.Role.TEACHER,
            must_change_password=False,
        )

        # Enrolled student
        self.student_user = User.objects.create_user(
            username="student_ama",
            email="ama@student.edu",
            password="test-only",
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
            password="test-only",
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


class LiveClassDetailScopeTests(TestCase):
    """The shared session page must only open for the people the class admits."""

    def setUp(self):
        settings = SchoolSettings.load()
        settings.learning_enabled = True
        settings.live_classes_enabled = True
        settings.save()

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
        self.other_subject = Subject.objects.create(name="English", code="ENG")

        self.class_teacher_user = User.objects.create_user(
            username="class_teacher",
            email="class_teacher@school.edu",
            password="test-only",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        self.school_class.class_teacher = self.class_teacher_user
        self.school_class.save(update_fields=["class_teacher"])

        self.teacher_user = User.objects.create_user(
            username="teacher_mth",
            email="mth@school.edu",
            password="test-only",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        teacher_profile = TeacherProfile.objects.create(user=self.teacher_user, staff_id="TCH-001")
        TeacherAssignment.objects.create(
            teacher=teacher_profile,
            school_class=self.school_class,
            subject=self.subject,
            academic_year=self.year,
        )

        self.other_subject_teacher_user = User.objects.create_user(
            username="teacher_eng",
            email="eng@school.edu",
            password="test-only",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        other_subject_profile = TeacherProfile.objects.create(
            user=self.other_subject_teacher_user, staff_id="TCH-002"
        )
        TeacherAssignment.objects.create(
            teacher=other_subject_profile,
            school_class=self.school_class,
            subject=self.other_subject,
            academic_year=self.year,
        )

        self.outside_teacher_user = User.objects.create_user(
            username="teacher_outside",
            email="outside@school.edu",
            password="test-only",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        TeacherProfile.objects.create(user=self.outside_teacher_user, staff_id="TCH-003")

        self.student_user = User.objects.create_user(
            username="student_ama",
            email="ama@student.edu",
            password="test-only",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        self.student = Student.objects.create(
            user=self.student_user,
            admission_number="ADM001",
            current_class=self.school_class,
        )

        self.other_student_user = User.objects.create_user(
            username="student_kofi",
            email="kofi@student.edu",
            password="test-only",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        self.other_student = Student.objects.create(
            user=self.other_student_user,
            admission_number="ADM002",
            current_class=self.other_class,
        )

        self.parent_user = User.objects.create_user(
            username="parent_ama",
            email="parent_ama@home.edu",
            password="test-only",
            role=User.Role.PARENT,
            must_change_password=False,
        )
        self.parent = ParentProfile.objects.create(user=self.parent_user)
        StudentGuardian.objects.create(
            student=self.student,
            parent=self.parent,
            relationship=StudentGuardian.Relationship.MOTHER,
        )

        self.other_parent_user = User.objects.create_user(
            username="parent_kofi",
            email="parent_kofi@home.edu",
            password="test-only",
            role=User.Role.PARENT,
            must_change_password=False,
        )
        self.other_parent = ParentProfile.objects.create(user=self.other_parent_user)
        StudentGuardian.objects.create(
            student=self.other_student,
            parent=self.other_parent,
            relationship=StudentGuardian.Relationship.FATHER,
        )

        self.admin_user = User.objects.create_user(
            username="admin_user",
            email="admin@school.edu",
            password="test-only",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        self.director_user = User.objects.create_user(
            username="director_user",
            email="director@school.edu",
            password="test-only",
            role=User.Role.DIRECTOR,
            must_change_password=False,
        )

        self.session = live_class_services.schedule_live_session(
            school_class=self.school_class,
            subject=self.subject,
            teacher=self.teacher_user,
            title="Algebra Live Review",
            scheduled_start=timezone.now() + datetime.timedelta(hours=2),
            duration_minutes=60,
            platform="google_meet",
            meeting_url="https://meet.google.com/abc-defg-hij",
            meeting_id="abc-defg-hij",
            passcode="12345",
            term=self.term,
        )
        self.detail_url = reverse(
            "learning:live_session_detail", kwargs={"session_id": self.session.pk}
        )

    def _get_as(self, user):
        self.client.force_login(user)
        return self.client.get(self.detail_url)

    def test_assigned_teacher_sees_join_details_and_attendance(self):
        resp = self._get_as(self.teacher_user)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "https://meet.google.com/abc-defg-hij")
        self.assertContains(resp, "Pupil Attendance")

    def test_class_teacher_sees_the_session(self):
        resp = self._get_as(self.class_teacher_user)
        self.assertEqual(resp.status_code, 200)

    def test_teacher_of_another_subject_is_refused(self):
        resp = self._get_as(self.other_subject_teacher_user)
        self.assertEqual(resp.status_code, 403)

    def test_teacher_without_an_assignment_is_refused(self):
        resp = self._get_as(self.outside_teacher_user)
        self.assertEqual(resp.status_code, 403)

    def test_pupil_in_the_class_sees_the_session(self):
        resp = self._get_as(self.student_user)
        self.assertEqual(resp.status_code, 200)

    def test_pupil_in_another_class_is_refused(self):
        resp = self._get_as(self.other_student_user)
        self.assertEqual(resp.status_code, 403)

    def test_parent_of_a_pupil_in_the_class_reads_without_attendance(self):
        resp = self._get_as(self.parent_user)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Pupil Attendance")

    def test_parent_of_a_pupil_in_another_class_is_refused(self):
        resp = self._get_as(self.other_parent_user)
        self.assertEqual(resp.status_code, 403)

    def test_admin_and_director_may_oversee_any_session(self):
        for user in (self.admin_user, self.director_user):
            with self.subTest(role=user.role):
                resp = self._get_as(user)
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, "Pupil Attendance")

    def _classroom_url(self, name: str, **extra) -> str:
        kwargs = {
            "class_id": self.school_class.pk,
            "subject_id": self.subject.pk,
            **extra,
        }
        return reverse(f"learning:{name}", kwargs=kwargs)

    def test_assigned_teacher_opens_the_teacher_live_views(self):
        self.client.force_login(self.teacher_user)
        self.assertEqual(self.client.get(self._classroom_url("teacher_live_sessions")).status_code, 200)
        self.assertEqual(self.client.get(self._classroom_url("teacher_live_create")).status_code, 200)
        edit_url = self._classroom_url("teacher_live_edit", session_id=self.session.pk)
        self.assertEqual(self.client.get(edit_url).status_code, 200)

    def test_class_teacher_opens_the_teacher_live_views(self):
        self.client.force_login(self.class_teacher_user)
        self.assertEqual(self.client.get(self._classroom_url("teacher_live_sessions")).status_code, 200)

    def test_teacher_of_another_subject_is_refused_by_the_teacher_views(self):
        self.client.force_login(self.other_subject_teacher_user)
        resp = self.client.get(self._classroom_url("teacher_live_sessions"))
        self.assertEqual(resp.status_code, 403)

    def test_teacher_without_an_assignment_is_refused_by_every_teacher_view(self):
        self.client.force_login(self.outside_teacher_user)

        self.assertEqual(
            self.client.get(self._classroom_url("teacher_live_sessions")).status_code, 403
        )
        self.assertEqual(
            self.client.get(self._classroom_url("teacher_live_create")).status_code, 403
        )
        self.assertEqual(
            self.client.post(self._classroom_url("teacher_live_create"), {}).status_code, 403
        )
        self.assertFalse(
            LiveClassSession.objects.filter(school_class=self.school_class).exclude(pk=self.session.pk).exists()
        )
        self.assertEqual(
            self.client.get(
                self._classroom_url("teacher_live_edit", session_id=self.session.pk)
            ).status_code,
            403,
        )

    def test_teacher_without_an_assignment_cannot_cancel_or_start_the_session(self):
        self.client.force_login(self.outside_teacher_user)

        self.assertEqual(
            self.client.post(
                self._classroom_url("teacher_live_cancel", session_id=self.session.pk)
            ).status_code,
            403,
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, LiveClassSession.Status.SCHEDULED)

        self.assertEqual(
            self.client.post(
                self._classroom_url(
                    "teacher_live_action", session_id=self.session.pk, action="start"
                )
            ).status_code,
            403,
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, LiveClassSession.Status.SCHEDULED)

    def test_class_calendar_follows_the_same_teacher_scope(self):
        calendar_url = reverse(
            "learning:class_calendar", kwargs={"class_id": self.school_class.pk}
        )
        # Any teacher who teaches a subject in the class may read its calendar.
        for user in (
            self.teacher_user,
            self.other_subject_teacher_user,
            self.class_teacher_user,
            self.admin_user,
            self.director_user,
        ):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.client.get(calendar_url).status_code, 200)

        self.client.force_login(self.outside_teacher_user)
        self.assertEqual(self.client.get(calendar_url).status_code, 403)
