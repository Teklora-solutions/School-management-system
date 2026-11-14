"""
apps/gradebook/tests/test_grid.py
---------------------------------
Checkpoint 2: the live grid. Scope, the agreed defaults, live marks from
homework and quizzes, missing as zero, excuses, weight renormalisation and the
feature switch.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import Stream
from apps.core.models import SchoolSettings
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
    GradebookSetup,
    ManualItem,
    ManualScore,
)
from apps.homework.models import Homework, HomeworkMark
from apps.learning.models import CourseModule, Lesson, Quiz, QuizAttempt
from apps.results.models import ResultSheet, ScoreEntry
from apps.teachers.models import TeacherAssignment, TeacherProfile


class GradebookGridTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Basic 6", level_order=6)
        cls.other_class = make_class(cls.year, name="Basic 7", level_order=7)
        cls.maths = make_subject("MATH", "Mathematics")
        cls.science = make_subject("SCI", "Science")

        cls.teacher_user = make_user("teacher", username="gb_teacher")
        cls.teacher = TeacherProfile.objects.create(
            user=cls.teacher_user, staff_id="GB-001"
        )
        cls.assignment = TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.maths,
            academic_year=cls.year,
        )

        cls.other_teacher_user = make_user("teacher", username="gb_other")
        cls.other_teacher = TeacherProfile.objects.create(
            user=cls.other_teacher_user, staff_id="GB-002"
        )
        cls.other_assignment = TeacherAssignment.objects.create(
            teacher=cls.other_teacher,
            school_class=cls.other_class,
            subject=cls.science,
            academic_year=cls.year,
        )

        cls.ama = make_student_user(cls.school_class, admission_number="GB-101")
        cls.kofi = make_student_user(cls.school_class, admission_number="GB-102")

    def setUp(self):
        row = SchoolSettings.load()
        row.gradebook_enabled = True
        row.save(update_fields=["gradebook_enabled", "updated_at"])
        self.client.force_login(self.teacher_user)

    @property
    def grid_url(self) -> str:
        return (
            reverse(
                "gradebook:teacher_grid",
                args=[self.school_class.pk, self.maths.pk],
            )
            + f"?term={self.term.pk}"
        )

    def _homework(self, *, max_score=Decimal("20"), title="Fractions") -> Homework:
        return Homework.objects.create(
            title=title,
            subject=self.maths,
            school_class=self.school_class,
            term=self.term,
            due_date=datetime.date(2025, 10, 10),
            status=Homework.Status.PUBLISHED,
            max_score=max_score,
            counts_for_gradebook=True,
        )

    def _mark(self, homework, student, score) -> HomeworkMark:
        return HomeworkMark.objects.create(
            homework=homework,
            student=student,
            score=Decimal(str(score)),
            status=HomeworkMark.Status.GOOD,
            marked_by=self.teacher_user,
            marked_at=timezone.now(),
        )

    def _quiz(self, *, title="Quick check", score=None, student=None):
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
        quiz = Quiz.objects.create(
            lesson=lesson,
            title=title,
            is_published=True,
            counts_for_gradebook=True,
        )
        if student is not None and score is not None:
            QuizAttempt.objects.create(
                quiz=quiz,
                student=student,
                submitted_at=timezone.now(),
                score_pct=score,
                passed=score >= 50,
            )
        return quiz

    def _setup(self) -> GradebookSetup:
        return services.get_or_create_setup(
            school_class=self.school_class,
            subject=self.maths,
            term=self.term,
            stream=self.assignment.stream,
            actor=self.teacher_user,
        )


class GridOpeningTests(GradebookGridTestCase):
    def test_the_grid_opens_with_the_agreed_defaults(self):
        self._homework()
        response = self.client.get(self.grid_url)
        self.assertEqual(response.status_code, 200)

        setup = GradebookSetup.objects.get(
            school_class=self.school_class,
            subject=self.maths,
            term=self.term,
        )
        self.assertEqual(
            (
                setup.homework_weight,
                setup.quiz_weight,
                setup.lab_weight,
                setup.exam_weight,
            ),
            (30, 30, 20, 20),
        )
        self.assertContains(response, "Basic 6")
        self.assertContains(response, "Mathematics")
        self.assertContains(response, "GB-101")

    def test_opening_twice_does_not_create_a_second_setup(self):
        self.client.get(self.grid_url)
        self.client.get(self.grid_url)
        self.assertEqual(GradebookSetup.objects.count(), 1)

    def test_a_teacher_cannot_open_another_classroom(self):
        response = self.client.get(
            reverse(
                "gradebook:teacher_grid",
                args=[self.other_class.pk, self.science.pk],
            )
        )
        self.assertEqual(response.status_code, 403)

    def test_a_pupil_cannot_open_the_gradebook(self):
        self.client.force_login(self.ama.user)
        response = self.client.get(self.grid_url)
        self.assertEqual(response.status_code, 403)

    def test_the_switch_off_replaces_the_grid_with_a_sentence(self):
        row = SchoolSettings.load()
        row.gradebook_enabled = False
        row.save(update_fields=["gradebook_enabled", "updated_at"])

        response = self.client.get(self.grid_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Available once the Gradebook module is activated."
        )
        self.assertNotContains(response, "gb-grid")

    def test_the_sidebar_link_is_hidden_while_the_module_is_off(self):
        list_url = reverse("gradebook:teacher_list")
        self.assertContains(self.client.get(list_url), 'href="/gradebook/"')

        row = SchoolSettings.load()
        row.gradebook_enabled = False
        row.save(update_fields=["gradebook_enabled", "updated_at"])
        self.assertNotContains(self.client.get(list_url), 'href="/gradebook/"')

    def test_a_term_without_terms_renders_the_explanation(self):
        # The fixture year has exactly one term; removing it leaves the
        # classroom with nothing to open a gradebook for.
        self.term.delete()

        response = self.client.get(
            reverse(
                "gradebook:teacher_grid",
                args=[self.school_class.pk, self.maths.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no terms yet")


class GridCalculationTests(GradebookGridTestCase):
    def test_homework_and_quiz_feed_the_average_and_share_their_weights(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        self._quiz(score=80, student=self.ama)

        response = self.client.get(self.grid_url)
        self.assertContains(response, "15/20")
        # Only homework and quizzes have work, so their 30/30 share the average.
        self.assertContains(response, "50.0%")
        # Homework 75%, quiz 80% → 77.5% overall.
        self.assertContains(response, "77.5%")

    def test_a_missing_mark_counts_as_zero_and_is_highlighted(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        self._quiz(score=40, student=self.kofi)

        response = self.client.get(self.grid_url)
        self.assertContains(response, "gb-missing")
        # Kofi scored 0 on homework and 40 on the quiz: 20.0% overall.
        self.assertContains(response, "20.0%")

        grid = services.build_grid(self._setup())
        kofi_row = next(r for r in grid["rows"] if r["student"].pk == self.kofi.pk)
        self.assertEqual(kofi_row["missing_count"], 1)
        self.assertEqual(kofi_row["overall_pct"], Decimal("20.0"))

    def test_an_excuse_removes_one_item_for_one_pupil_only(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        quiz = self._quiz(score=80, student=self.ama)
        QuizAttempt.objects.create(
            quiz=quiz,
            student=self.kofi,
            submitted_at=timezone.now(),
            score_pct=40,
            passed=False,
        )
        setup = self._setup()
        GradebookExcuse.objects.create(
            setup=setup,
            student=self.kofi,
            source_kind=GradebookExcuse.SourceKind.HOMEWORK,
            source_id=homework.pk,
            reason="Hospital appointment, work set at home later.",
        )

        response = self.client.get(self.grid_url)
        self.assertContains(response, "Excused")

        grid = services.build_grid(setup)
        ama_row = next(r for r in grid["rows"] if r["student"].pk == self.ama.pk)
        kofi_row = next(r for r in grid["rows"] if r["student"].pk == self.kofi.pk)
        # Ama is unaffected: 75% homework + 80% quiz.
        self.assertEqual(ama_row["overall_pct"], Decimal("77.5"))
        # Kofi's homework leaves his average; only the quiz remains.
        self.assertEqual(kofi_row["overall_pct"], Decimal("40.0"))
        self.assertEqual(kofi_row["missing_count"], 0)
        self.assertEqual(kofi_row["excused_count"], 1)

    def test_only_the_best_quiz_attempt_counts(self):
        quiz = self._quiz()
        QuizAttempt.objects.create(
            quiz=quiz,
            student=self.ama,
            submitted_at=timezone.now(),
            score_pct=40,
            passed=False,
        )
        QuizAttempt.objects.create(
            quiz=quiz,
            student=self.ama,
            submitted_at=timezone.now(),
            score_pct=90,
            passed=True,
        )
        grid = services.build_grid(self._setup())
        row = next(r for r in grid["rows"] if r["student"].pk == self.ama.pk)
        self.assertEqual(row["overall_pct"], Decimal("90.0"))

    def test_an_unpublished_quiz_or_homework_has_no_column(self):
        homework = self._homework()
        homework.status = Homework.Status.DRAFT
        homework.save(update_fields=["status"])
        quiz = self._quiz()
        quiz.is_published = False
        quiz.save(update_fields=["is_published"])

        grid = services.build_grid(self._setup())
        self.assertFalse(grid["has_items"])

    def test_a_lone_bucket_carries_the_whole_average(self):
        self._homework()
        grid = services.build_grid(self._setup())
        self.assertEqual([b["key"] for b in grid["buckets"]], ["homework"])
        self.assertEqual(grid["buckets"][0]["effective_weight"], Decimal("100.0"))

    def test_a_manual_item_can_feed_the_average(self):
        setup = self._setup()
        item = ManualItem.objects.create(
            setup=setup,
            bucket=ManualItem.Bucket.LAB,
            title="Titration",
            max_score=Decimal("10"),
            worked_on=datetime.date(2025, 10, 1),
            reason="Practical done in the lab; papers kept.",
        )
        ManualScore.objects.create(item=item, student=self.ama, score=Decimal("7"))

        grid = services.build_grid(setup)
        ama_row = next(r for r in grid["rows"] if r["student"].pk == self.ama.pk)
        self.assertEqual(ama_row["overall_pct"], Decimal("70.0"))
        self.assertEqual(ama_row["missing_count"], 0)

    def test_the_exam_sheet_is_a_column_and_a_bucket(self):
        sheet = ResultSheet.objects.create(
            school_class=self.school_class,
            subject=self.maths,
            term=self.term,
        )
        ScoreEntry.objects.create(
            result_sheet=sheet,
            student=self.ama,
            raw_score=Decimal("64"),
            total=Decimal("64"),
        )
        setup = self._setup()
        setup.exam_sheet = sheet
        setup.save(update_fields=["exam_sheet", "updated_at"])

        grid = services.build_grid(setup)
        self.assertEqual([b["key"] for b in grid["buckets"]], ["exam"])
        ama_row = next(r for r in grid["rows"] if r["student"].pk == self.ama.pk)
        self.assertEqual(ama_row["overall_pct"], Decimal("64.0"))

    def test_a_pupil_with_nothing_scorable_has_no_overall_mark(self):
        homework = self._homework()
        setup = self._setup()
        GradebookExcuse.objects.create(
            setup=setup,
            student=self.kofi,
            source_kind=GradebookExcuse.SourceKind.HOMEWORK,
            source_id=homework.pk,
            reason="Joined late; not expected to make up the work.",
        )
        grid = services.build_grid(setup)
        kofi_row = next(r for r in grid["rows"] if r["student"].pk == self.kofi.pk)
        self.assertIsNone(kofi_row["overall_pct"])

    def test_a_stream_setup_only_counts_that_streams_pupils(self):
        stream = Stream.objects.create(school_class=self.school_class, name="Blue")
        self.assignment.stream = stream
        self.assignment.save(update_fields=["stream"])

        setup = services.get_or_create_setup(
            school_class=self.school_class,
            subject=self.maths,
            term=self.term,
            stream=stream,
        )
        grid = services.build_grid(setup)
        # Both fixture pupils have no stream, so the stream grid is empty.
        self.assertEqual(grid["roster_size"], 0)
