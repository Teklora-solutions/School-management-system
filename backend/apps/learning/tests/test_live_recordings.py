"""
apps/learning/tests/test_live_recordings.py
-------------------------------------------
Module 3, checkpoint 1: a teacher attaches a recording link to a session,
optionally so it also appears on the lesson page, after confirming the class
was told the lesson is being recorded.

The app never records the video; these tests only prove the link, who may add
or remove it, and who may watch it.
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
from apps.core.models import AuditLog, SchoolSettings
from apps.learning import live_class_services
from apps.learning.live_class_utils import validate_recording_url
from apps.learning.models import CourseModule, Lesson, LiveClassSession
from apps.students.models import Student, StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile

User = get_user_model()

RECORDING_URL = "https://drive.example.com/recordings/fractions"
MEETING_URL = "https://meet.google.com/abc-defg-hij"


class RecordingFixture(TestCase):
    """Academic scaffold, one teacher who may edit, and families in two classes."""

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
            academic_year=self.year, name="Basic 7", level_order=7
        )
        self.other_class = SchoolClass.objects.create(
            academic_year=self.year, name="Basic 8", level_order=8
        )
        self.subject = Subject.objects.create(name="Mathematics", code="MTH")
        self.other_subject = Subject.objects.create(name="English", code="ENG")

        self.teacher = User.objects.create_user(
            username="rec_teacher",
            email="rec_teacher@school.edu",
            password="test-only",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        TeacherAssignment.objects.create(
            teacher=TeacherProfile.objects.create(user=self.teacher, staff_id="REC-T-1"),
            school_class=self.school_class,
            subject=self.subject,
            academic_year=self.year,
        )
        self.other_teacher = User.objects.create_user(
            username="rec_other_teacher",
            email="rec_other_teacher@school.edu",
            password="test-only",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        TeacherAssignment.objects.create(
            teacher=TeacherProfile.objects.create(
                user=self.other_teacher, staff_id="REC-T-2"
            ),
            school_class=self.school_class,
            subject=self.other_subject,
            academic_year=self.year,
        )

        self.student_user = User.objects.create_user(
            username="rec_student",
            email="rec_student@student.edu",
            password="test-only",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        self.student = Student.objects.create(
            user=self.student_user,
            admission_number="REC-001",
            current_class=self.school_class,
        )
        self.other_student_user = User.objects.create_user(
            username="rec_other_student",
            email="rec_other_student@student.edu",
            password="test-only",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        self.other_student = Student.objects.create(
            user=self.other_student_user,
            admission_number="REC-002",
            current_class=self.other_class,
        )

        parent_user = User.objects.create_user(
            username="rec_parent",
            email="rec_parent@guardian.edu",
            password="test-only",
            role=User.Role.PARENT,
            must_change_password=False,
        )
        StudentGuardian.objects.create(
            student=self.student,
            parent=ParentProfile.objects.create(user=parent_user),
            is_primary=True,
        )
        self.parent = parent_user

        self.module = CourseModule.objects.create(
            school_class=self.school_class,
            subject=self.subject,
            term=self.term,
            title="Fractions",
            position=1,
        )
        self.lesson = Lesson.objects.create(
            module=self.module,
            title="Adding fractions",
            notes="Add the numerators.",
            position=1,
            is_published=True,
        )
        other_module = CourseModule.objects.create(
            school_class=self.school_class,
            subject=self.other_subject,
            term=self.term,
            title="Poetry",
            position=1,
        )
        self.other_subject_lesson = Lesson.objects.create(
            module=other_module, title="Rhyme", position=1, is_published=True
        )

        self.session = self._session()

    def _session(self, *, start=None, status=None, title="Fractions live") -> LiveClassSession:
        return LiveClassSession.objects.create(
            school_class=self.school_class,
            subject=self.subject,
            teacher=self.teacher,
            term=self.term,
            title=title,
            platform=LiveClassSession.Platform.GOOGLE_MEET,
            meeting_url=MEETING_URL,
            scheduled_start=start or timezone.now() - datetime.timedelta(minutes=30),
            duration_minutes=45,
            status=status or LiveClassSession.Status.COMPLETED,
        )

    def _attach(self, *, session=None, lesson=None, notice_given=True):
        return live_class_services.attach_recording(
            session=session or self.session,
            recording_url=RECORDING_URL,
            lesson=lesson,
            notice_given=notice_given,
            actor=self.teacher,
        )


class RecordingUrlTests(TestCase):
    def test_public_https_links_are_accepted(self):
        for url in (RECORDING_URL, "https://youtu.be/abc", "http://example.org/v.mp4"):
            valid, message = validate_recording_url(url)
            self.assertTrue(valid, f"{url}: {message}")

    def test_unsafe_or_local_links_are_refused(self):
        for url in (
            "",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "https://localhost/rec",
            "https://user:secret@example.com/rec",
        ):
            valid, _ = validate_recording_url(url)
            self.assertFalse(valid, url)


class RecordingServiceTests(RecordingFixture):
    def test_attach_recording_saves_link_notice_and_who_added_it(self):
        session = self._attach(lesson=self.lesson)

        self.assertEqual(session.recording_url, RECORDING_URL)
        self.assertEqual(session.recording_lesson, self.lesson)
        self.assertTrue(session.recording_notice_acknowledged)
        self.assertEqual(session.recording_added_by, self.teacher)
        self.assertIsNotNone(session.recording_added_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="live_class.recording_added", object_id=str(session.pk)
            ).exists()
        )

    def test_the_notice_confirmation_is_required(self):
        with self.assertRaisesMessage(
            ValidationError, "Tick the box to confirm"
        ):
            self._attach(notice_given=False)
        self.session.refresh_from_db()
        self.assertEqual(self.session.recording_url, "")

    def test_a_recording_cannot_be_added_before_the_session_starts(self):
        future = self._session(start=timezone.now() + datetime.timedelta(hours=2))
        with self.assertRaisesMessage(ValidationError, "once the session has started"):
            self._attach(session=future)

    def test_a_cancelled_session_refuses_a_recording(self):
        cancelled = self._session(
            status=LiveClassSession.Status.CANCELLED, title="Called off"
        )
        with self.assertRaisesMessage(ValidationError, "cancelled session"):
            self._attach(session=cancelled)

    def test_an_unsafe_link_is_refused(self):
        with self.assertRaises(ValidationError):
            live_class_services.attach_recording(
                session=self.session,
                recording_url="javascript:alert(1)",
                notice_given=True,
                actor=self.teacher,
            )

    def test_the_lesson_must_be_in_the_same_classroom(self):
        with self.assertRaisesMessage(ValidationError, "this class and subject"):
            self._attach(lesson=self.other_subject_lesson)

        other_class_module = CourseModule.objects.create(
            school_class=self.other_class,
            subject=self.subject,
            term=self.term,
            title="Elsewhere",
            position=1,
        )
        other_class_lesson = Lesson.objects.create(
            module=other_class_module, title="Other class", position=1
        )
        with self.assertRaisesMessage(ValidationError, "this class and subject"):
            self._attach(lesson=other_class_lesson)

    def test_removing_clears_the_link_and_audits(self):
        self._attach(lesson=self.lesson)

        session = live_class_services.remove_recording(
            session=self.session, actor=self.teacher
        )

        self.assertEqual(session.recording_url, "")
        self.assertIsNone(session.recording_lesson)
        self.assertIsNone(session.recording_added_at)
        self.assertIsNone(session.recording_added_by)
        self.assertFalse(session.recording_notice_acknowledged)
        self.assertTrue(
            AuditLog.objects.filter(
                action="live_class.recording_removed", object_id=str(session.pk)
            ).exists()
        )

    def test_removing_when_there_is_no_recording_writes_no_audit(self):
        live_class_services.remove_recording(session=self.session, actor=self.teacher)
        self.assertFalse(
            AuditLog.objects.filter(
                action="live_class.recording_removed",
                object_id=str(self.session.pk),
            ).exists()
        )


class RecordingScreenTests(RecordingFixture):
    def _form_url(self, session=None):
        session = session or self.session
        return reverse(
            "learning:teacher_live_recording",
            kwargs={
                "class_id": self.school_class.pk,
                "subject_id": self.subject.pk,
                "session_id": session.pk,
            },
        )

    def test_the_form_shows_the_consent_notice_and_lesson_choices(self):
        self.client.force_login(self.teacher)
        response = self.client.get(self._form_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recording notice.")
        self.assertContains(response, "keep their camera off")
        self.assertContains(response, "I told the class this lesson is being recorded.")
        self.assertContains(response, "Adding fractions")

    def test_the_teacher_saves_a_recording_from_the_screen(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            self._form_url(),
            {
                "recording_url": RECORDING_URL,
                "recording_lesson": self.lesson.pk,
                "notice_given": "on",
            },
        )

        self.assertRedirects(
            response,
            reverse("learning:live_session_detail", kwargs={"session_id": self.session.pk}),
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.recording_url, RECORDING_URL)
        self.assertEqual(self.session.recording_lesson, self.lesson)

    def test_saving_without_the_notice_tick_is_refused(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            self._form_url(),
            {"recording_url": RECORDING_URL, "recording_lesson": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tick the box to confirm")
        self.session.refresh_from_db()
        self.assertEqual(self.session.recording_url, "")

    def test_saving_before_the_session_starts_is_refused(self):
        future = self._session(start=timezone.now() + datetime.timedelta(hours=2))
        self.client.force_login(self.teacher)
        response = self.client.post(
            self._form_url(future),
            {"recording_url": RECORDING_URL, "recording_lesson": "", "notice_given": "on"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "once the session has started")
        future.refresh_from_db()
        self.assertEqual(future.recording_url, "")

    def test_another_teacher_cannot_add_or_remove(self):
        self._attach()
        self.client.force_login(self.other_teacher)

        self.assertEqual(self.client.get(self._form_url()).status_code, 403)
        response = self.client.post(
            reverse(
                "learning:teacher_live_recording_remove",
                kwargs={
                    "class_id": self.school_class.pk,
                    "subject_id": self.subject.pk,
                    "session_id": self.session.pk,
                },
            )
        )
        self.assertEqual(response.status_code, 403)
        self.session.refresh_from_db()
        self.assertEqual(self.session.recording_url, RECORDING_URL)

    def test_a_session_from_another_classroom_cannot_be_used(self):
        self.client.force_login(self.teacher)
        response = self.client.get(
            reverse(
                "learning:teacher_live_recording",
                kwargs={
                    "class_id": self.other_class.pk,
                    "subject_id": self.subject.pk,
                    "session_id": self.session.pk,
                },
            )
        )
        self.assertEqual(response.status_code, 403)

    def test_the_teacher_can_remove_the_recording(self):
        self._attach()
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse(
                "learning:teacher_live_recording_remove",
                kwargs={
                    "class_id": self.school_class.pk,
                    "subject_id": self.subject.pk,
                    "session_id": self.session.pk,
                },
            )
        )

        self.assertRedirects(
            response,
            reverse("learning:live_session_detail", kwargs={"session_id": self.session.pk}),
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.recording_url, "")

    def test_the_switch_off_hides_the_recording_screen(self):
        settings = SchoolSettings.load()
        settings.live_classes_enabled = False
        settings.save()

        self.client.force_login(self.teacher)
        response = self.client.get(self._form_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Available once the Live Classes module is activated")


class RecordingVisibilityTests(RecordingFixture):
    def _detail_url(self):
        return reverse("learning:live_session_detail", kwargs={"session_id": self.session.pk})

    def _lesson_url(self):
        return reverse(
            "learning:lesson_view",
            kwargs={
                "class_id": self.school_class.pk,
                "subject_id": self.subject.pk,
                "lesson_id": self.lesson.pk,
            },
        )

    def test_a_pupil_in_the_class_sees_the_recording_and_the_notice(self):
        self._attach(lesson=self.lesson)
        self.client.force_login(self.student_user)
        response = self.client.get(self._detail_url())

        self.assertContains(response, RECORDING_URL)
        self.assertContains(response, "Watch recording")
        self.assertContains(response, "Recording notice.")

    def test_a_parent_in_the_class_sees_the_recording(self):
        self._attach()
        self.client.force_login(self.parent)
        response = self.client.get(self._detail_url())

        self.assertContains(response, RECORDING_URL)
        self.assertContains(response, "Recording notice.")

    def test_nothing_is_shown_before_a_recording_is_added(self):
        self.client.force_login(self.student_user)
        response = self.client.get(self._detail_url())

        self.assertNotContains(response, "Watch recording")
        self.assertNotContains(response, "Recording notice.")

    def test_a_cancelled_session_hides_the_recording_from_families(self):
        self._attach()
        self.session.status = LiveClassSession.Status.CANCELLED
        self.session.save(update_fields=["status"])

        self.client.force_login(self.student_user)
        pupil_response = self.client.get(self._detail_url())
        self.assertNotContains(pupil_response, RECORDING_URL)

        self.client.force_login(self.teacher)
        teacher_response = self.client.get(self._detail_url())
        self.assertContains(teacher_response, RECORDING_URL)

    def test_the_lesson_page_shows_a_recording_attached_to_it(self):
        self._attach(lesson=self.lesson)
        self.client.force_login(self.student_user)
        response = self.client.get(self._lesson_url())

        self.assertContains(response, "Session recordings")
        self.assertContains(response, RECORDING_URL)
        self.assertContains(response, "Recording notice.")

    def test_the_lesson_page_shows_it_to_a_parent(self):
        self._attach(lesson=self.lesson)
        self.client.force_login(self.parent)
        response = self.client.get(self._lesson_url())

        self.assertContains(response, RECORDING_URL)

    def test_a_recording_not_attached_to_the_lesson_stays_off_it(self):
        self._attach(lesson=None)
        self.client.force_login(self.student_user)
        response = self.client.get(self._lesson_url())

        self.assertNotContains(response, RECORDING_URL)

    def test_a_pupil_from_another_class_cannot_open_the_lesson(self):
        self._attach(lesson=self.lesson)
        self.client.force_login(self.other_student_user)
        response = self.client.get(self._lesson_url())

        self.assertEqual(response.status_code, 404)

    def test_the_teacher_preview_shows_the_recording(self):
        self._attach(lesson=self.lesson)
        self.client.force_login(self.teacher)
        response = self.client.get(
            reverse(
                "learning:lesson_preview",
                kwargs={
                    "class_id": self.school_class.pk,
                    "subject_id": self.subject.pk,
                    "module_id": self.module.pk,
                    "lesson_id": self.lesson.pk,
                },
            )
        )

        self.assertContains(response, RECORDING_URL)
