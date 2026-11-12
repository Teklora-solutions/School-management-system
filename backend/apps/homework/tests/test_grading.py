"""
apps/homework/tests/test_grading.py
------------------------------------
Module 1: marking homework, keeping work private until release, and the one
extra attempt a "Needs revision" mark opens.

The rules under test are the ones that protect people: another teacher cannot
mark this class, an unreleased mark is invisible to the family, a mark cannot
exceed the homework's total, a late deduction is opt-in, and marking never
touches the points a pupil already earned for handing work in.
"""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ParentProfile
from apps.core.models import Notification, SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.learning.models import PointLedger
from apps.students.models import StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile

from .. import services
from ..models import (
    Homework,
    HomeworkMark,
    HomeworkSubmission,
    HomeworkSubmissionRevision,
)


class GradingTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.class_a = make_class(cls.year, name="Grade 7", level_order=7)
        cls.class_b = make_class(cls.year, name="Grade 8", level_order=8)
        cls.maths = make_subject("MATH", "Mathematics")

        cls.teacher_user = make_user("teacher", username="mark_teacher")
        cls.teacher = TeacherProfile.objects.create(
            user=cls.teacher_user, staff_id="STF-700"
        )
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.class_a,
            subject=cls.maths,
            academic_year=cls.year,
        )

        cls.other_teacher_user = make_user("teacher", username="mark_other")
        cls.other_teacher = TeacherProfile.objects.create(
            user=cls.other_teacher_user, staff_id="STF-701"
        )
        TeacherAssignment.objects.create(
            teacher=cls.other_teacher,
            school_class=cls.class_b,
            subject=cls.maths,
            academic_year=cls.year,
        )

        cls.student_a = make_student_user(
            cls.class_a, admission_number="MK-001"
        )
        cls.student_b = make_student_user(
            cls.class_a, admission_number="MK-002"
        )
        cls.student_other = make_student_user(
            cls.class_b, admission_number="MK-101"
        )

        cls.parent_user = make_user("parent", username="mark_parent")
        cls.parent = ParentProfile.objects.create(user=cls.parent_user)
        StudentGuardian.objects.create(
            student=cls.student_a, parent=cls.parent, is_primary=True
        )

        SchoolSettings.objects.update_or_create(
            pk=SchoolSettings.SINGLETON_PK,
            defaults={"homework_grading_enabled": True},
        )

        cls.homework = Homework.objects.create(
            title="Fractions worksheet",
            description="Show your working.",
            subject=cls.maths,
            school_class=cls.class_a,
            term=cls.term,
            set_by=cls.teacher_user,
            due_date=datetime.date.today() + datetime.timedelta(days=2),
            status=Homework.Status.PUBLISHED,
            published_at=timezone.now(),
            max_score=20,
        )
        cls.submission = HomeworkSubmission.objects.create(
            homework=cls.homework,
            student=cls.student_a,
            note="First try",
            submitted_at=timezone.now(),
            is_late=False,
        )

    def mark(self, *, student=None, score="15", status="good", feedback="", **extra):
        return services.mark_homework(
            homework=self.homework,
            student=student or self.student_a,
            score=score,
            status=status,
            feedback=feedback,
            actor=self.teacher_user,
            **extra,
        )


class MarkingPermissionTests(GradingTestBase):
    def test_teacher_of_another_class_cannot_open_the_marking_page(self):
        self.client.force_login(self.other_teacher_user)
        response = self.client.get(
            reverse("homework:teacher_mark", args=[self.homework.pk, self.student_a.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_teacher_cannot_mark_a_pupil_outside_the_homework_class(self):
        self.client.force_login(self.teacher_user)
        response = self.client.get(
            reverse(
                "homework:teacher_mark",
                args=[self.homework.pk, self.student_other.pk],
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_teacher_can_mark_a_pupil_in_the_class(self):
        self.client.force_login(self.teacher_user)
        response = self.client.get(
            reverse("homework:teacher_mark", args=[self.homework.pk, self.student_a.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "First try")

    def test_parent_cannot_release_marks(self):
        self.client.force_login(self.parent_user)
        response = self.client.post(
            reverse("homework:teacher_release", args=[self.homework.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_reach_the_autosave_endpoint(self):
        self.client.force_login(self.student_a.user)
        response = self.client.post(
            reverse(
                "homework:teacher_mark_autosave",
                args=[self.homework.pk, self.student_a.pk],
            ),
            {"status": "good", "score": "15"},
        )
        self.assertEqual(response.status_code, 403)


class MarkRulesTests(GradingTestBase):
    def test_marking_saves_one_row_and_audits_it(self):
        self.mark(feedback="Neat working")
        self.mark(feedback="Neat working, and clear")
        mark = HomeworkMark.objects.get(
            homework=self.homework, student=self.student_a
        )
        self.assertEqual(HomeworkMark.objects.count(), 1)
        self.assertEqual(mark.score, 15)
        self.assertEqual(mark.status, HomeworkMark.Status.GOOD)
        self.assertEqual(mark.feedback, "Neat working, and clear")
        self.assertEqual(mark.marked_by, self.teacher_user)

    def test_a_mark_above_the_total_is_refused(self):
        with self.assertRaises(ValidationError):
            self.mark(score="21")
        self.assertFalse(HomeworkMark.objects.exists())

    def test_a_negative_mark_is_refused(self):
        with self.assertRaises(ValidationError):
            self.mark(score="-1")

    def test_a_mark_needs_a_total_on_the_homework(self):
        self.homework.max_score = None
        self.homework.save(update_fields=["max_score", "updated_at"])
        with self.assertRaises(ValidationError):
            self.mark(score="10")

    def test_not_submitted_clears_the_mark(self):
        mark = self.mark(score="12", status="good")
        mark = self.mark(
            score="12", status=HomeworkMark.Status.NOT_SUBMITTED
        )
        self.assertIsNone(mark.score)
        self.assertEqual(mark.late_deduction, 0)

    def test_marking_does_not_change_homework_points(self):
        before = PointLedger.objects.count()
        self.mark(score="20", status="excellent")
        self.assertEqual(PointLedger.objects.count(), before)

    def test_mark_bounds_are_enforced_through_the_view(self):
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            reverse("homework:teacher_mark", args=[self.homework.pk, self.student_a.pk]),
            {"status": "good", "score": "99", "feedback": "", "filter": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The mark must be between 0 and 20")
        self.assertFalse(HomeworkMark.objects.exists())


class ReleaseTests(GradingTestBase):
    def test_an_unreleased_mark_is_invisible_to_the_pupil(self):
        self.mark(feedback="Secret encouraging note")
        self.client.force_login(self.student_a.user)
        response = self.client.get(
            reverse("homework:detail", args=[self.homework.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Secret encouraging note")

    def test_an_unreleased_mark_is_invisible_to_the_parent(self):
        self.mark(feedback="Secret encouraging note")
        self.client.force_login(self.parent_user)
        response = self.client.get(
            reverse("homework:detail", args=[self.homework.pk])
        )
        self.assertNotContains(response, "Secret encouraging note")

    def test_release_shows_the_mark_and_notifies_once(self):
        self.mark(score="15", feedback="Lovely clear working")
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            reverse("homework:teacher_release", args=[self.homework.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.student_a.user, category="homework"
            ).count(),
            1,
        )

        self.client.force_login(self.student_a.user)
        response = self.client.get(
            reverse("homework:detail", args=[self.homework.pk])
        )
        self.assertContains(response, "Lovely clear working")
        self.assertContains(response, "15")

        self.client.force_login(self.parent_user)
        response = self.client.get(
            reverse("homework:detail", args=[self.homework.pk])
        )
        self.assertContains(response, "Lovely clear working")

    def test_releasing_twice_does_not_notify_twice(self):
        self.mark()
        self.client.force_login(self.teacher_user)
        url = reverse("homework:teacher_release", args=[self.homework.pk])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.student_a.user, category="homework"
            ).count(),
            1,
        )

    def test_class_release_does_not_repeat_an_individual_release(self):
        self.mark()
        services.release_marks(
            homework=self.homework,
            actor=self.teacher_user,
            students=[self.student_a],
        )
        services.release_marks(homework=self.homework, actor=self.teacher_user)
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.student_a.user, category="homework"
            ).count(),
            1,
        )

    def test_a_mark_saved_after_class_release_is_visible_and_told_once(self):
        services.release_marks(homework=self.homework, actor=self.teacher_user)
        self.mark(feedback="Marked after the class was released")
        self.mark(feedback="Corrected, still visible")
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.student_a.user, category="homework"
            ).count(),
            1,
        )
        self.client.force_login(self.student_a.user)
        response = self.client.get(
            reverse("homework:detail", args=[self.homework.pk])
        )
        self.assertContains(response, "Corrected, still visible")

    def test_release_to_one_pupil_leaves_the_other_private(self):
        self.mark(student=self.student_a, feedback="For pupil A only")
        self.mark(student=self.student_b, feedback="For pupil B only")
        self.client.force_login(self.teacher_user)
        self.client.post(
            reverse("homework:teacher_release", args=[self.homework.pk]),
            {"student_id": self.student_a.pk},
        )

        self.client.force_login(self.student_a.user)
        response = self.client.get(
            reverse("homework:detail", args=[self.homework.pk])
        )
        self.assertContains(response, "For pupil A only")

        self.client.force_login(self.student_b.user)
        response = self.client.get(
            reverse("homework:detail", args=[self.homework.pk])
        )
        self.assertNotContains(response, "For pupil B only")

    def test_marked_work_page_lists_only_released_marks(self):
        released = self.mark(
            student=self.student_a, feedback="Released to A"
        )
        self.mark(student=self.student_b, feedback="Still private")
        services.release_marks(
            homework=self.homework,
            actor=self.teacher_user,
            students=[self.student_a],
        )
        self.assertIsNotNone(
            HomeworkMark.objects.get(pk=released.pk).released_at
        )

        self.client.force_login(self.student_a.user)
        response = self.client.get(reverse("homework:marked_work"))
        self.assertContains(response, "Released to A")

        self.client.force_login(self.student_b.user)
        response = self.client.get(reverse("homework:marked_work"))
        self.assertNotContains(response, "Still private")

    def test_parent_sees_the_childs_marked_work_read_only(self):
        self.mark(feedback="Read this at home")
        services.release_marks(
            homework=self.homework, actor=self.teacher_user
        )
        self.client.force_login(self.parent_user)
        response = self.client.get(reverse("homework:marked_work"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Read this at home")
        self.assertNotContains(response, "Try again")


class ResubmissionTests(GradingTestBase):
    def _release_needs_revision(self, *, allowed=1):
        # Marking happens after the work is in, so the loop is exercised on
        # homework whose due date has passed — the case where the ordinary
        # "edit until the due date" path is closed.
        self.homework.due_date = datetime.date.today() - datetime.timedelta(days=1)
        self.homework.save(update_fields=["due_date", "updated_at"])
        self.mark(
            score="9",
            status=HomeworkMark.Status.NEEDS_REVISION,
            feedback="Have another go at question 3",
            resubmissions_allowed=allowed,
        )
        services.release_marks(homework=self.homework, actor=self.teacher_user)

    def test_needs_revision_allows_one_more_attempt_and_keeps_the_first(self):
        self._release_needs_revision()
        self.client.force_login(self.student_a.user)
        response = self.client.post(
            reverse("homework:submit", args=[self.homework.pk]),
            {"note": "Second try, question 3 fixed"},
        )
        self.assertEqual(response.status_code, 302)

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.note, "Second try, question 3 fixed")
        revision = HomeworkSubmissionRevision.objects.get(
            submission=self.submission
        )
        self.assertEqual(revision.number, 1)
        self.assertEqual(revision.note, "First try")

        # The teacher can read the earlier attempt on the marking page.
        self.client.force_login(self.teacher_user)
        response = self.client.get(
            reverse("homework:teacher_mark", args=[self.homework.pk, self.student_a.pk])
        )
        self.assertContains(response, "Earlier attempts")
        self.assertContains(response, "First try")

    def test_a_second_resubmission_is_refused_by_default(self):
        self._release_needs_revision()
        self.client.force_login(self.student_a.user)
        url = reverse("homework:submit", args=[self.homework.pk])
        self.client.post(url, {"note": "Second try"})
        self.client.post(url, {"note": "Third try"})
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.note, "Second try")
        self.assertEqual(
            HomeworkSubmissionRevision.objects.filter(
                submission=self.submission
            ).count(),
            1,
        )

    def test_more_attempts_can_be_granted(self):
        self._release_needs_revision(allowed=2)
        self.client.force_login(self.student_a.user)
        url = reverse("homework:submit", args=[self.homework.pk])
        self.client.post(url, {"note": "Second try"})
        self.client.post(url, {"note": "Third try"})
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.note, "Third try")
        self.assertEqual(
            HomeworkSubmissionRevision.objects.filter(
                submission=self.submission
            ).count(),
            2,
        )

    def test_an_unreleased_needs_revision_does_not_open_resubmission(self):
        self.mark(
            status=HomeworkMark.Status.NEEDS_REVISION,
            score="9",
            resubmissions_allowed=1,
        )
        # Past the due date the ordinary edit path is closed, so the only way
        # the note could change is a resubmission the teacher never released.
        self.homework.due_date = datetime.date.today() - datetime.timedelta(days=1)
        self.homework.save(update_fields=["due_date", "updated_at"])
        self.client.force_login(self.student_a.user)
        response = self.client.post(
            reverse("homework:submit", args=[self.homework.pk]),
            {"note": "Sneaky second try"},
        )
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.note, "First try")

    def test_a_due_date_that_has_passed_still_allows_the_granted_attempt(self):
        self._release_needs_revision()
        self.client.force_login(self.student_a.user)
        response = self.client.post(
            reverse("homework:submit", args=[self.homework.pk]),
            {"note": "Late but invited"},
        )
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.note, "Late but invited")
        self.assertTrue(self.submission.is_late)
        self.assertEqual(
            HomeworkSubmissionRevision.objects.get(
                submission=self.submission
            ).note,
            "First try",
        )


class LateDeductionTests(GradingTestBase):
    def setUp(self):
        self.late_submission = HomeworkSubmission.objects.create(
            homework=self.homework,
            student=self.student_b,
            note="Handed in late",
            submitted_at=timezone.now(),
            is_late=True,
        )

    def test_deduction_is_off_by_default(self):
        with self.assertRaises(ValidationError):
            self.mark(
                student=self.student_b,
                score="15",
                late_deduction="2",
            )

    def test_deduction_applies_when_the_school_switches_it_on(self):
        SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).update(
            homework_late_penalty_enabled=True
        )
        mark = self.mark(
            student=self.student_b,
            score="15",
            late_deduction="2",
        )
        self.assertEqual(mark.score, 15)
        self.assertEqual(mark.late_deduction, 2)
        self.assertEqual(mark.final_score, 13)

    def test_deduction_cannot_exceed_the_mark(self):
        SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).update(
            homework_late_penalty_enabled=True
        )
        with self.assertRaises(ValidationError):
            self.mark(student=self.student_b, score="5", late_deduction="6")

    def test_on_time_work_cannot_carry_a_late_deduction(self):
        SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).update(
            homework_late_penalty_enabled=True
        )
        with self.assertRaises(ValidationError):
            self.mark(student=self.student_a, score="15", late_deduction="2")


class SwitchAndAutosaveTests(GradingTestBase):
    def test_switch_off_keeps_the_marking_screen_closed(self):
        SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).update(
            homework_grading_enabled=False
        )
        self.client.force_login(self.teacher_user)
        response = self.client.get(
            reverse("homework:teacher_mark", args=[self.homework.pk, self.student_a.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("homework:teacher_submissions", args=[self.homework.pk]),
        )

    def test_switch_off_hides_the_students_marked_work(self):
        SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).update(
            homework_grading_enabled=False
        )
        self.client.force_login(self.student_a.user)
        response = self.client.get(reverse("homework:marked_work"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "switches on homework marking")

    def test_autosave_saves_the_same_way_the_button_does(self):
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            reverse(
                "homework:teacher_mark_autosave",
                args=[self.homework.pk, self.student_a.pk],
            ),
            {"status": "excellent", "score": "19", "feedback": "Superb"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        mark = HomeworkMark.objects.get(
            homework=self.homework, student=self.student_a
        )
        self.assertEqual(mark.score, 19)
        self.assertEqual(mark.status, HomeworkMark.Status.EXCELLENT)

    def test_autosave_refuses_a_mark_outside_the_total(self):
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            reverse(
                "homework:teacher_mark_autosave",
                args=[self.homework.pk, self.student_a.pk],
            ),
            {"status": "good", "score": "40"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertFalse(HomeworkMark.objects.exists())
