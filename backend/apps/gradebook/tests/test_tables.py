"""
apps/gradebook/tests/test_tables.py
-----------------------------------
Checkpoint 1: the tables exist with the right defaults, the classroom key is
unique, and the two new switches start off.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.academics.models import Stream
from apps.core.features import feature_enabled
from apps.core.models import SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_year,
)
from apps.gradebook.models import (
    GradebookExcuse,
    GradebookRelease,
    GradebookResult,
    GradebookSetup,
    GradebookTermLock,
    ManualItem,
    ManualScore,
)
from apps.homework.models import Homework
from apps.learning.models import CourseModule, Lesson, Quiz


class GradebookSetupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.subject = make_subject()

    def _setup(self, **overrides) -> GradebookSetup:
        values = {
            "school_class": self.school_class,
            "subject": self.subject,
            "term": self.term,
        }
        values.update(overrides)
        return GradebookSetup.objects.create(**values)

    def test_default_weights_match_the_agreed_model(self):
        setup = self._setup()
        self.assertEqual(
            (
                setup.homework_weight,
                setup.quiz_weight,
                setup.lab_weight,
                setup.exam_weight,
            ),
            (30, 30, 20, 20),
        )
        self.assertEqual(setup.total_weight, 100)
        setup.full_clean()

    def test_weights_that_do_not_total_100_are_rejected(self):
        setup = self._setup(exam_weight=30)
        with self.assertRaises(ValidationError) as caught:
            setup.full_clean()
        self.assertIn("homework_weight", caught.exception.message_dict)

    def test_one_setup_per_classroom(self):
        self._setup()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._setup()

    def test_a_stream_setup_coexists_with_the_whole_class_setup(self):
        self._setup()
        stream = Stream.objects.create(school_class=self.school_class, name="Blue")
        self._setup(stream=stream)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._setup(stream=stream)


class ManualItemTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        year = make_year()
        term = make_term(year)
        cls.setup = GradebookSetup.objects.create(
            school_class=make_class(year), subject=make_subject(), term=term
        )

    def _item(self, **overrides) -> ManualItem:
        values = {
            "setup": self.setup,
            "bucket": ManualItem.Bucket.LAB,
            "title": "Titration",
            "max_score": Decimal("20"),
            "worked_on": datetime.date(2025, 10, 1),
            "reason": "Practical done in the lab; papers kept.",
        }
        values.update(overrides)
        return ManualItem.objects.create(**values)

    def test_a_new_manual_item_counts_until_unticked(self):
        item = self._item()
        self.assertTrue(item.counts_for_gradebook)
        self.assertEqual(item.get_bucket_display(), "Labs")

    def test_a_manual_item_only_uses_the_counted_buckets(self):
        """End-of-term comes from the exam sheet, never from a hand-typed item."""
        self.assertEqual(
            set(ManualItem.Bucket.values), {"homework", "quiz", "lab"}
        )

    def test_a_total_of_zero_is_refused(self):
        item = self._item(max_score=Decimal("0"))
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_one_score_per_pupil_per_item(self):
        item = self._item()
        student = make_student_user(
            self.setup.school_class, admission_number="GB-001"
        )
        ManualScore.objects.create(item=item, student=student, score=Decimal("15"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ManualScore.objects.create(
                item=item, student=student, score=Decimal("16")
            )


class ExcuseAndReleaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        year = make_year()
        cls.term = make_term(year)
        cls.setup = GradebookSetup.objects.create(
            school_class=make_class(year), subject=make_subject(), term=cls.term
        )
        cls.student = make_student_user(
            cls.setup.school_class, admission_number="GB-002"
        )

    def test_an_excuse_is_unique_per_pupil_and_item(self):
        GradebookExcuse.objects.create(
            setup=self.setup,
            student=self.student,
            source_kind=GradebookExcuse.SourceKind.HOMEWORK,
            source_id=7,
            reason="Hospital appointment.",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            GradebookExcuse.objects.create(
                setup=self.setup,
                student=self.student,
                source_kind=GradebookExcuse.SourceKind.HOMEWORK,
                source_id=7,
                reason="Duplicate.",
            )

    def test_release_versions_are_unique_and_latest_first(self):
        first = GradebookRelease.objects.create(setup=self.setup, version=1)
        GradebookRelease.objects.create(setup=self.setup, version=2)
        self.assertEqual(
            list(GradebookRelease.objects.filter(setup=self.setup).values_list(
                "version", flat=True
            )),
            [2, 1],
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            GradebookRelease.objects.create(setup=self.setup, version=1)
        self.assertEqual(first.version, 1)

    def test_one_result_row_per_pupil_per_release(self):
        release = GradebookRelease.objects.create(setup=self.setup, version=1)
        GradebookResult.objects.create(
            release=release, student=self.student, overall_pct=Decimal("71.5")
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            GradebookResult.objects.create(
                release=release, student=self.student, overall_pct=Decimal("72.0")
            )

    def test_a_new_term_lock_is_locked(self):
        lock = GradebookTermLock.objects.create(term=self.term)
        self.assertTrue(lock.is_locked)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GradebookTermLock.objects.create(term=self.term)


class SwitchDefaultsTests(TestCase):
    """The gradebook lands switched off, and so do its sources."""

    def test_gradebook_is_off_on_a_fresh_install(self):
        self.assertEqual(SchoolSettings.objects.count(), 0)
        self.assertFalse(feature_enabled("gradebook"))

    def test_the_switches_the_gradebook_reads_are_off_by_default(self):
        row = SchoolSettings.load()
        self.assertFalse(row.gradebook_enabled)
        self.assertFalse(row.quizzes_enabled)

    def test_saving_the_switch_turns_the_feature_on(self):
        row = SchoolSettings.load()
        row.gradebook_enabled = True
        row.save(update_fields=["gradebook_enabled", "updated_at"])
        self.assertTrue(feature_enabled("gradebook"))

    def test_homework_does_not_count_until_ticked(self):
        year = make_year()
        term = make_term(year)
        school_class = make_class(year)
        subject = make_subject()
        homework = Homework.objects.create(
            title="Fractions",
            subject=subject,
            school_class=school_class,
            term=term,
            due_date=datetime.date(2025, 10, 10),
        )
        self.assertFalse(homework.counts_for_gradebook)

    def test_a_quiz_is_practice_until_ticked(self):
        year = make_year()
        term = make_term(year)
        school_class = make_class(year)
        subject = make_subject()
        module = CourseModule.objects.create(
            school_class=school_class,
            subject=subject,
            term=term,
            title="Module",
            position=0,
            is_published=True,
        )
        lesson = Lesson.objects.create(
            module=module, title="Lesson", position=0, is_published=True
        )
        quiz = Quiz.objects.create(lesson=lesson, title="Practice")
        self.assertFalse(quiz.counts_for_gradebook)
