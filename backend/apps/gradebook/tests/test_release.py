"""
apps/gradebook/tests/test_release.py
------------------------------------
Checkpoint 4: frozen, numbered releases, the reason a re-release needs, and the
administrator's lock over one term.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog, SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.gradebook import services
from apps.gradebook.models import (
    GradebookExcuse,
    GradebookRelease,
    GradebookResult,
    GradebookSetup,
    GradebookTermLock,
    ManualItem,
)
from apps.homework import services as homework_services
from apps.homework.models import Homework, HomeworkMark
from apps.learning import quiz_services
from apps.learning.models import CourseModule, Lesson, Quiz
from apps.teachers.models import TeacherAssignment, TeacherProfile


class ReleaseTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Basic 6", level_order=6)
        cls.other_class = make_class(cls.year, name="Basic 7", level_order=7)
        cls.maths = make_subject("MATH", "Mathematics")
        cls.science = make_subject("SCI", "Science")

        cls.teacher_user = make_user("teacher", username="rel_teacher")
        cls.teacher = TeacherProfile.objects.create(
            user=cls.teacher_user, staff_id="REL-001"
        )
        cls.assignment = TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.maths,
            academic_year=cls.year,
        )

        cls.other_teacher_user = make_user("teacher", username="rel_other")
        cls.other_teacher = TeacherProfile.objects.create(
            user=cls.other_teacher_user, staff_id="REL-002"
        )
        TeacherAssignment.objects.create(
            teacher=cls.other_teacher,
            school_class=cls.other_class,
            subject=cls.science,
            academic_year=cls.year,
        )

        cls.ama = make_student_user(cls.school_class, admission_number="REL-101")
        cls.kofi = make_student_user(cls.school_class, admission_number="REL-102")
        cls.outsider = make_student_user(cls.other_class, admission_number="REL-201")

        cls.admin_user = make_user("admin", username="rel_admin")
        cls.director_user = make_user("director", username="rel_director")

    def setUp(self):
        row = SchoolSettings.load()
        row.gradebook_enabled = True
        row.save(update_fields=["gradebook_enabled", "updated_at"])
        self.client.force_login(self.teacher_user)

    def _setup(self) -> GradebookSetup:
        return services.get_or_create_setup(
            school_class=self.school_class,
            subject=self.maths,
            term=self.term,
            stream=self.assignment.stream,
            actor=self.teacher_user,
        )

    def _homework(
        self,
        *,
        title="Fractions",
        max_score=Decimal("20"),
        released=False,
    ) -> Homework:
        return Homework.objects.create(
            title=title,
            subject=self.maths,
            school_class=self.school_class,
            term=self.term,
            due_date=datetime.date(2025, 10, 10),
            status=Homework.Status.PUBLISHED,
            max_score=max_score,
            counts_for_gradebook=True,
            marks_released_at=timezone.now() if released else None,
        )

    def _mark(self, homework, student, score):
        return HomeworkMark.objects.create(
            homework=homework,
            student=student,
            score=Decimal(str(score)),
            status=HomeworkMark.Status.GOOD,
            marked_by=self.teacher_user,
            marked_at=timezone.now(),
        )

    @property
    def grid_url(self) -> str:
        return (
            reverse(
                "gradebook:teacher_grid",
                args=[self.school_class.pk, self.maths.pk],
            )
            + f"?term={self.term.pk}"
        )

    @property
    def release_url(self) -> str:
        return reverse("gradebook:teacher_release", args=[self._setup().pk])


class ReleaseServiceTests(ReleaseTestCase):
    def test_the_first_release_freezes_todays_numbers(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        self._mark(homework, self.kofi, 10)
        setup = self._setup()

        release = services.release_gradebook(
            setup=setup, actor=self.teacher_user
        )

        self.assertEqual(release.version, 1)
        self.assertEqual(release.released_by, self.teacher_user)
        self.assertEqual(release.reason, "")
        self.assertEqual(
            release.weights, {"homework": 30, "quiz": 30, "lab": 20, "exam": 20}
        )
        self.assertEqual(release.results.count(), 2)

        ama = GradebookResult.objects.get(release=release, student=self.ama)
        kofi = GradebookResult.objects.get(release=release, student=self.kofi)
        self.assertEqual(ama.overall_pct, Decimal("75.0"))
        self.assertEqual(kofi.overall_pct, Decimal("50.0"))
        self.assertEqual(ama.bucket_pcts, {"homework": 75.0})
        self.assertEqual(ama.missing_count, 0)

    def test_the_release_is_audited_with_its_version_and_weights(self):
        self._mark(self._homework(), self.ama, 15)
        release = services.release_gradebook(
            setup=self._setup(), actor=self.teacher_user
        )
        log = AuditLog.objects.get(action="gradebook.released")
        self.assertEqual(log.actor, self.teacher_user)
        self.assertEqual(log.object_id, str(release.pk))
        self.assertEqual(log.change_summary["version"], 1)
        self.assertIsNone(log.change_summary["previous_version"])
        self.assertEqual(log.change_summary["weights"]["homework"], 30)
        self.assertEqual(log.change_summary["pupils"], 2)

    def test_a_change_is_released_as_the_next_version_and_the_old_one_stays(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        setup = self._setup()
        first = services.release_gradebook(setup=setup, actor=self.teacher_user)

        self._mark(homework, self.kofi, 20)
        second = services.release_gradebook(
            setup=setup,
            reason="Kofi's homework was marked late.",
            actor=self.teacher_user,
        )

        self.assertEqual(second.version, 2)
        self.assertEqual(second.reason, "Kofi's homework was marked late.")
        self.assertEqual(
            GradebookResult.objects.get(release=first, student=self.kofi).overall_pct,
            Decimal("0.0"),
        )
        self.assertEqual(
            GradebookResult.objects.get(release=second, student=self.kofi).overall_pct,
            Decimal("100.0"),
        )
        self.assertEqual(services.latest_release(setup).pk, second.pk)

    def test_a_rerelease_without_a_reason_is_refused(self):
        self._mark(self._homework(), self.ama, 15)
        setup = self._setup()
        services.release_gradebook(setup=setup, actor=self.teacher_user)

        with self.assertRaises(ValidationError) as caught:
            services.release_gradebook(
                setup=setup, reason="   ", actor=self.teacher_user
            )
        self.assertIn("Say what changed", str(caught.exception))
        self.assertEqual(GradebookRelease.objects.count(), 1)

    def test_an_empty_gradebook_is_not_released(self):
        with self.assertRaises(ValidationError):
            services.release_gradebook(
                setup=self._setup(), actor=self.teacher_user
            )
        self.assertFalse(GradebookRelease.objects.exists())

    def test_a_locked_term_cannot_be_released(self):
        self._mark(self._homework(), self.ama, 15)
        setup = self._setup()
        GradebookTermLock.objects.create(term=self.term)
        with self.assertRaises(ValidationError):
            services.release_gradebook(setup=setup, actor=self.teacher_user)
        self.assertFalse(GradebookRelease.objects.exists())

    def test_a_fully_excused_pupil_has_no_released_mark(self):
        homework = self._homework()
        setup = self._setup()
        self._mark(homework, self.ama, 15)
        GradebookExcuse.objects.create(
            setup=setup,
            student=self.kofi,
            source_kind=GradebookExcuse.SourceKind.HOMEWORK,
            source_id=homework.pk,
            reason="Joined late; not expected to make up the work.",
        )

        release = services.release_gradebook(setup=setup, actor=self.teacher_user)
        kofi = GradebookResult.objects.get(release=release, student=self.kofi)
        self.assertIsNone(kofi.overall_pct)
        self.assertEqual(kofi.excused_count, 1)
        self.assertEqual(kofi.missing_count, 0)

    def test_the_weights_agreed_before_release_are_the_ones_frozen(self):
        self._mark(self._homework(), self.ama, 15)
        setup = self._setup()
        services.update_weights(
            setup=setup,
            homework_weight=40,
            quiz_weight=20,
            lab_weight=20,
            exam_weight=20,
            actor=self.teacher_user,
        )
        release = services.release_gradebook(setup=setup, actor=self.teacher_user)
        self.assertEqual(release.weights["homework"], 40)
        # Homework alone carries all of the renormalised average.
        ama = GradebookResult.objects.get(release=release, student=self.ama)
        self.assertEqual(ama.overall_pct, Decimal("75.0"))


class ReleaseScreenTests(ReleaseTestCase):
    def test_the_page_opens_before_anything_is_released(self):
        self._mark(self._homework(), self.ama, 15)
        response = self.client.get(self.release_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not released yet")
        self.assertContains(response, "This is the first release")
        self.assertContains(response, "Nothing has been released")

    def test_the_screen_releases_and_returns_to_the_grid(self):
        self._mark(self._homework(), self.ama, 15)
        response = self.client.post(
            self.release_url, {"reason": "End of week 6."}, follow=True
        )
        self.assertRedirects(response, self.grid_url)
        release = GradebookRelease.objects.get()
        self.assertEqual(release.version, 1)
        self.assertEqual(release.reason, "End of week 6.")
        self.assertContains(response, "Version 1 released")

    def test_the_screen_asks_what_changed_on_a_rerelease(self):
        self._mark(self._homework(), self.ama, 15)
        self.client.post(self.release_url, {"reason": "First release."})

        response = self.client.post(self.release_url, {"reason": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Say what changed since the last release.")
        self.assertEqual(GradebookRelease.objects.count(), 1)

    def test_the_screen_shows_the_release_history(self):
        self._mark(self._homework(), self.ama, 15)
        self.client.post(self.release_url, {"reason": "First release."})
        response = self.client.get(self.release_url)
        self.assertContains(response, "v1")
        self.assertContains(response, "First release.")
        self.assertContains(response, "Latest: v1")

    def test_unreleased_homework_marks_are_warned_about_but_not_blocked(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        response = self.client.get(self.release_url)
        self.assertContains(response, "1 counted homework task still has marks")
        self.assertContains(response, "have not been shown")

        self.client.post(self.release_url, {"reason": "Release anyway."})
        self.assertTrue(GradebookRelease.objects.exists())

    def test_released_homework_marks_produce_no_warning(self):
        homework = self._homework(released=True)
        self._mark(homework, self.ama, 15)
        response = self.client.get(self.release_url)
        self.assertNotContains(response, "have not been shown")

    def test_an_empty_gradebook_says_what_to_do_first(self):
        response = self.client.get(self.release_url)
        self.assertContains(response, "There is no counted work yet")
        self.assertNotContains(response, "Release version 1")

    def test_a_locked_term_shows_the_lock_and_hides_the_form(self):
        self._mark(self._homework(), self.ama, 15)
        GradebookTermLock.objects.create(term=self.term)
        response = self.client.get(self.release_url)
        self.assertContains(response, "is locked, so nothing new can be released")
        self.assertNotContains(response, "Release version 1")
        self.assertEqual(self.client.post(self.release_url, {}).status_code, 200)
        self.assertFalse(GradebookRelease.objects.exists())

    def test_another_teacher_cannot_release(self):
        self._mark(self._homework(), self.ama, 15)
        url = self.release_url
        self.client.force_login(self.other_teacher_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"reason": "Mine."}).status_code, 403)
        self.assertFalse(GradebookRelease.objects.exists())

    def test_a_pupil_cannot_release(self):
        self._mark(self._homework(), self.ama, 15)
        url = self.release_url
        self.client.force_login(self.ama.user)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_the_switch_off_replaces_the_page_with_the_sentence(self):
        self._mark(self._homework(), self.ama, 15)
        row = SchoolSettings.load()
        row.gradebook_enabled = False
        row.save(update_fields=["gradebook_enabled", "updated_at"])
        response = self.client.get(self.release_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Available once the Gradebook module is activated.")
        self.assertNotContains(response, "Recent releases")

    def test_the_grid_shows_the_latest_release_and_a_way_to_it(self):
        self._mark(self._homework(), self.ama, 15)
        self.client.post(self.release_url, {"reason": "First release."})
        response = self.client.get(self.grid_url)
        self.assertContains(response, "Released v1")
        self.assertContains(response, f'href="/gradebook/setup/{self._setup().pk}/release/"')


class TermLockServiceTests(ReleaseTestCase):
    def _lock(self):
        services.set_term_lock(
            term=self.term,
            locked=True,
            reason="Term closed for reporting.",
            actor=self.admin_user,
        )
        return GradebookTermLock.objects.get(term=self.term)

    def test_locking_records_who_when_and_why(self):
        lock = self._lock()
        self.assertTrue(lock.is_locked)
        self.assertEqual(lock.locked_by, self.admin_user)
        self.assertIsNotNone(lock.locked_at)
        self.assertEqual(lock.reason, "Term closed for reporting.")
        log = AuditLog.objects.get(action="gradebook.term_locked")
        self.assertEqual(log.actor, self.admin_user)
        self.assertEqual(log.change_summary["from"], None)
        self.assertTrue(log.change_summary["to"])

    def test_locking_twice_leaves_one_row_and_one_audit_entry(self):
        self._lock()
        services.set_term_lock(
            term=self.term, locked=True, actor=self.admin_user
        )
        self.assertEqual(GradebookTermLock.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="gradebook.term_locked").count(), 1)

    def test_unlocking_reopens_the_gradebook(self):
        self._lock()
        services.set_term_lock(
            term=self.term,
            locked=False,
            reason="Correction agreed with the head.",
            actor=self.admin_user,
        )
        lock = GradebookTermLock.objects.get(term=self.term)
        self.assertFalse(lock.is_locked)
        self.assertTrue(
            AuditLog.objects.filter(action="gradebook.term_unlocked").exists()
        )

    def test_unlocking_a_term_that_was_never_locked_does_nothing(self):
        result = services.set_term_lock(
            term=self.term, locked=False, actor=self.admin_user
        )
        self.assertIsNone(result)
        self.assertFalse(GradebookTermLock.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action="gradebook.term_unlocked").exists())

    def test_a_locked_term_refuses_a_new_ticked_homework(self):
        self._lock()
        with self.assertRaises(ValidationError):
            homework_services.create_homework(
                assignment=self.assignment,
                term=self.term,
                title="Fractions",
                due_date=datetime.date(2025, 10, 10),
                publish=True,
                max_score=Decimal("20"),
                counts_for_gradebook=True,
                actor=self.teacher_user,
            )
        self.assertFalse(Homework.objects.exists())

    def test_untracked_homework_can_still_be_set_while_the_term_is_locked(self):
        self._lock()
        homework = homework_services.create_homework(
            assignment=self.assignment,
            term=self.term,
            title="Reading log",
            due_date=datetime.date(2025, 10, 10),
            publish=True,
            counts_for_gradebook=False,
            actor=self.teacher_user,
        )
        self.assertFalse(homework.counts_for_gradebook)

    def test_a_locked_term_refuses_changing_an_existing_homework_tick(self):
        homework = self._homework()
        self._lock()
        with self.assertRaises(ValidationError):
            homework_services.update_homework(
                homework=homework,
                assignment=self.assignment,
                term=self.term,
                title=homework.title,
                description="",
                due_date=homework.due_date,
                max_score=homework.max_score,
                counts_for_gradebook=False,
                actor=self.teacher_user,
            )
        homework.refresh_from_db()
        self.assertTrue(homework.counts_for_gradebook)

    def test_a_locked_term_refuses_the_quiz_tick(self):
        module = CourseModule.objects.create(
            school_class=self.school_class,
            subject=self.maths,
            term=self.term,
            title="Module 1",
            position=0,
            is_published=True,
        )
        lesson = Lesson.objects.create(
            module=module, title="Lesson 1", position=0, is_published=True
        )
        quiz = Quiz.objects.create(lesson=lesson, title="Quick check")
        self._lock()

        with self.assertRaises(ValidationError):
            quiz_services.update_quiz_settings(
                quiz=quiz,
                title="Quick check",
                instructions="",
                pass_mark=50,
                attempts_allowed=1,
                time_limit_minutes=None,
                shuffle_questions=False,
                shuffle_choices=False,
                show_correct_after=True,
                counts_for_gradebook=True,
                actor=self.teacher_user,
            )
        quiz.refresh_from_db()
        self.assertFalse(quiz.counts_for_gradebook)

    def test_the_tick_stays_editable_when_the_module_is_switched_off(self):
        self._lock()
        row = SchoolSettings.load()
        row.gradebook_enabled = False
        row.save(update_fields=["gradebook_enabled", "updated_at"])
        homework = homework_services.create_homework(
            assignment=self.assignment,
            term=self.term,
            title="Fractions",
            due_date=datetime.date(2025, 10, 10),
            publish=True,
            max_score=Decimal("20"),
            counts_for_gradebook=True,
            actor=self.teacher_user,
        )
        self.assertTrue(homework.counts_for_gradebook)


class AdminLockScreenTests(ReleaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.url = reverse("gradebook:admin_locks")

    def test_the_page_lists_the_terms_and_their_state(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gradebook term locks")
        self.assertContains(response, "Term 1")
        self.assertContains(response, "Open")
        self.assertContains(response, "Lock")

    def test_the_admin_locks_and_unlocks_a_term_from_the_page(self):
        response = self.client.post(
            self.url,
            {
                "term_id": str(self.term.pk),
                "action": "lock",
                "reason": "Exams finished.",
            },
            follow=True,
        )
        self.assertContains(response, "is now locked")
        lock = GradebookTermLock.objects.get(term=self.term)
        self.assertTrue(lock.is_locked)
        self.assertContains(self.client.get(self.url), "Locked")

        response = self.client.post(
            self.url,
            {"term_id": str(self.term.pk), "action": "unlock"},
            follow=True,
        )
        self.assertContains(response, "is open again")
        lock.refresh_from_db()
        self.assertFalse(lock.is_locked)

    def test_a_teacher_cannot_open_or_change_the_locks(self):
        self.client.force_login(self.teacher_user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.url, {"term_id": str(self.term.pk), "action": "lock"}
            ).status_code,
            403,
        )
        self.assertFalse(GradebookTermLock.objects.exists())

    def test_a_director_cannot_change_the_locks(self):
        self.client.force_login(self.director_user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_a_locked_term_stops_the_teacher_changing_marks(self):
        services.set_term_lock(
            term=self.term, locked=True, actor=self.admin_user
        )
        self.client.force_login(self.teacher_user)

        response = self.client.get(self.grid_url)
        self.assertContains(response, "Term locked")

        setup = self._setup()
        with self.assertRaises(ValidationError):
            services.create_manual_item(
                setup=setup,
                bucket=ManualItem.Bucket.LAB,
                title="Titration",
                max_score=Decimal("20"),
                worked_on=datetime.date(2025, 10, 5),
                reason="Practical done in the lab.",
                actor=self.teacher_user,
            )

    def test_the_sidebar_entry_is_hidden_while_the_module_is_off(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'href="/gradebook/locks/"')

        row = SchoolSettings.load()
        row.gradebook_enabled = False
        row.save(update_fields=["gradebook_enabled", "updated_at"])
        response = self.client.get(self.url)
        self.assertNotContains(response, 'href="/gradebook/locks/"')
        self.assertContains(response, "Available once the Gradebook module is activated.")
