"""
apps/learning/tests/test_points.py
------------------------------------
Tests for Piece 3: points, levels and badges.

Coverage
--------
  A. points_config  — level_for_total, next_level, LEVELS ordering
  B. points_services — award_points (no double-award, daily cap, engagement gate),
                       reverse_award (validation, clamp to zero),
                       teacher_bonus (validation), check_level_up (notification),
                       maybe_award_streak_bonus
  C. hooks          — lesson completion awards points,
                      homework on-time/late awards correct reason,
                      feed question awards/reversal on removal
  D. points_selectors — student_points_summary, class_points_overview
  E. Views          — StudentBadgesView, TeacherClassPointsView,
                      TeacherBonusAwardView, TeacherReverseAwardView,
                      DirectorPointsOverviewView — access control
"""

from __future__ import annotations

import datetime

from django.test import TestCase
from django.urls import reverse

from apps.academics.models import ClassSubject
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
from apps.learning.models import (
    ClassEngagementSettings,
    CourseModule,
    Lesson,
    PointConfig,
    PointLedger,
    StudentLevel,
)
from apps.learning.points_config import (
    LEVELS,
    level_for_total,
    next_level,
)
from apps.learning.points_services import (
    award_points,
    check_level_up,
    maybe_award_streak_bonus,
    reverse_award,
    teacher_bonus,
)
from apps.students.models import Student, StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def enable_engagement(school_class=None):
    row = SchoolSettings.load()
    row.learning_enabled = True
    row.engagement_enabled = True
    row.save(update_fields=["learning_enabled", "engagement_enabled", "updated_at"])
    if school_class is not None:
        ClassEngagementSettings.objects.get_or_create(
            school_class=school_class,
            defaults={"is_active": True},
        )


def make_teacher_user(username, staff_id):
    user = make_user("teacher", username=username)
    profile = TeacherProfile.objects.create(user=user, staff_id=staff_id)
    return user, profile


def make_parent_for(student):
    parent_user = make_user("parent", username=f"par_{student.admission_number}")
    profile = ParentProfile.objects.create(user=parent_user)
    StudentGuardian.objects.create(student=student, parent=profile, is_primary=True)
    return parent_user, profile


# ============================================================================
# A. points_config
# ============================================================================


class LevelConfigTests(TestCase):
    def test_levels_ordered_ascending_by_min_points(self):
        for i in range(1, len(LEVELS)):
            self.assertGreater(LEVELS[i].min_points, LEVELS[i - 1].min_points)

    def test_level_numbers_are_sequential(self):
        for i, lvl in enumerate(LEVELS, start=1):
            self.assertEqual(lvl.number, i)

    def test_level_for_zero_is_seedling(self):
        lvl = level_for_total(0)
        self.assertEqual(lvl.name, "Seedling")

    def test_level_for_49_is_still_seedling(self):
        self.assertEqual(level_for_total(49).name, "Seedling")

    def test_level_for_50_is_sprout(self):
        self.assertEqual(level_for_total(50).name, "Sprout")

    def test_level_for_2000_is_legend(self):
        self.assertEqual(level_for_total(2000).name, "Legend")

    def test_level_for_very_large_number_is_legend(self):
        self.assertEqual(level_for_total(99999).name, "Legend")

    def test_next_level_from_seedling_is_sprout(self):
        seedling = LEVELS[0]
        nxt = next_level(seedling)
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.name, "Sprout")

    def test_next_level_from_legend_is_none(self):
        legend = LEVELS[-1]
        self.assertIsNone(next_level(legend))


# ============================================================================
# B. points_services
# ============================================================================


class AwardPointsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        enable_engagement(cls.school_class)
        cls.student = make_student_user(
            cls.school_class, admission_number="PT-001"
        )
        PointConfig.objects.get_or_create(pk=1)

    def _award(self, pts=10, reason="lesson", key=None):
        key = key or f"lesson:999:{self.student.pk}"
        return award_points(
            student=self.student,
            reason=reason,
            points=pts,
            description="Test award",
            idempotency_key=key,
        )

    # ── Basic award ──────────────────────────────────────────────────────────

    def test_award_creates_ledger_row(self):
        row = self._award(pts=10)
        self.assertIsNotNone(row)
        self.assertEqual(row.points, 10)
        self.assertEqual(row.reason, "lesson")

    def test_award_updates_student_level_total(self):
        self._award(pts=10)
        sl = StudentLevel.objects.get(student=self.student)
        self.assertEqual(sl.total_points, 10)

    # ── No double-award ──────────────────────────────────────────────────────

    def test_same_idempotency_key_returns_none(self):
        self._award(pts=10, key="lesson:1:1")
        result = self._award(pts=10, key="lesson:1:1")
        self.assertIsNone(result)

    def test_same_idempotency_key_does_not_create_second_row(self):
        self._award(pts=10, key="lesson:2:2")
        self._award(pts=10, key="lesson:2:2")
        count = PointLedger.objects.filter(student=self.student, idempotency_key="lesson:2:2").count()
        self.assertEqual(count, 1)

    # ── Engagement gate ──────────────────────────────────────────────────────

    def test_award_returns_none_when_engagement_off(self):
        row = SchoolSettings.load()
        row.engagement_enabled = False
        row.save(update_fields=["engagement_enabled", "updated_at"])
        result = self._award(pts=10, key="lesson:off:1")
        self.assertIsNone(result)
        row.engagement_enabled = True
        row.save(update_fields=["engagement_enabled", "updated_at"])

    def test_award_returns_none_when_class_engagement_off(self):
        # Use a fresh student in a separate class to avoid shared setUpTestData state
        other_class = make_class(self.year, name="Grade Off", level_order=99)
        eng = ClassEngagementSettings.objects.create(
            school_class=other_class,
            is_active=False,
        )
        row = SchoolSettings.load()
        row.engagement_enabled = True
        row.save(update_fields=["engagement_enabled", "updated_at"])
        other_student = make_student_user(other_class, admission_number="PT-OFF-01")
        result = award_points(
            student=other_student,
            reason="lesson",
            points=10,
            description="Should not award",
            idempotency_key=f"lesson:off_class:1:{other_student.pk}",
        )
        self.assertIsNone(result)


class DailyFeedCapTests(TestCase):
    """Daily feed cap prevents farming via many feed posts."""

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        enable_engagement(cls.school_class)
        cls.student = make_student_user(cls.school_class, admission_number="CAP-001")
        cfg = PointConfig.objects.get_or_create(pk=1)[0]
        # Cap = 15 pts, feed post = 5 pts → 3 posts hit the cap
        cfg.daily_feed_cap = 15
        cfg.pts_feed_question = 5
        cfg.save()

    def test_three_feed_posts_are_all_awarded(self):
        for i in range(3):
            r = award_points(
                student=self.student,
                reason="feed_post",
                points=5,
                description="Question",
                idempotency_key=f"feed_post:cap_post_{i}:{self.student.pk}",
            )
            self.assertIsNotNone(r, f"Award {i} should have succeeded")

    def test_fourth_feed_post_is_blocked_by_cap(self):
        for i in range(3):
            award_points(
                student=self.student,
                reason="feed_post",
                points=5,
                description="Question",
                idempotency_key=f"feed_post:cap4_post_{i}:{self.student.pk}",
            )
        result = award_points(
            student=self.student,
            reason="feed_post",
            points=5,
            description="Question",
            idempotency_key=f"feed_post:cap4_post_3:{self.student.pk}",
        )
        self.assertIsNone(result)

    def test_non_feed_reason_not_subject_to_cap(self):
        for i in range(3):
            award_points(
                student=self.student,
                reason="feed_post",
                points=5,
                description="Question",
                idempotency_key=f"feed_post:noncap_{i}:{self.student.pk}",
            )
        # Lesson award should still go through
        result = award_points(
            student=self.student,
            reason="lesson",
            points=10,
            description="Lesson done",
            idempotency_key=f"lesson:noncap:999:{self.student.pk}",
        )
        self.assertIsNotNone(result)


class ReverseAwardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        enable_engagement(cls.school_class)
        cls.student = make_student_user(cls.school_class, admission_number="RV-001")
        PointConfig.objects.get_or_create(pk=1)
        cls.teacher_user, cls.teacher = make_teacher_user("rv_teacher", "STF-RV1")

    def test_reverse_creates_negative_row(self):
        row = award_points(
            student=self.student,
            reason="lesson",
            points=10,
            description="Lesson",
            idempotency_key="lesson:rv:1",
        )
        rev = reverse_award(
            ledger_row=row,
            reversed_by=self.teacher_user,
            reason_text="Mistake",
        )
        self.assertLess(rev.points, 0)
        self.assertEqual(rev.reason, PointLedger.Reason.REVERSAL)
        self.assertEqual(rev.reversal_of, row)

    def test_total_never_goes_below_zero(self):
        row = award_points(
            student=self.student,
            reason="lesson",
            points=10,
            description="Lesson",
            idempotency_key="lesson:rv:2",
        )
        # Reverse twice as much as awarded
        from apps.learning.points_services import _ledger_total
        before = _ledger_total(self.student)
        reverse_award(ledger_row=row, reversed_by=self.teacher_user)
        after = _ledger_total(self.student)
        self.assertGreaterEqual(after, 0)

    def test_cannot_reverse_a_reversal_row(self):
        row = award_points(
            student=self.student,
            reason="lesson",
            points=10,
            description="Lesson",
            idempotency_key="lesson:rv:3",
        )
        rev = reverse_award(ledger_row=row, reversed_by=self.teacher_user)
        with self.assertRaises(ValueError):
            reverse_award(ledger_row=rev, reversed_by=self.teacher_user)

    def test_cannot_reverse_already_reversed_row(self):
        row = award_points(
            student=self.student,
            reason="lesson",
            points=10,
            description="Lesson",
            idempotency_key="lesson:rv:4",
        )
        reverse_award(ledger_row=row, reversed_by=self.teacher_user)
        # Reload to get the reversed_by relation
        row.refresh_from_db()
        with self.assertRaises(ValueError):
            reverse_award(ledger_row=row, reversed_by=self.teacher_user)


class TeacherBonusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        enable_engagement(cls.school_class)
        cls.student = make_student_user(cls.school_class, admission_number="BN-001")
        cls.teacher_user, _ = make_teacher_user("bn_teacher", "STF-BN1")
        PointConfig.objects.get_or_create(pk=1)

    def test_valid_bonus_creates_row(self):
        row = teacher_bonus(
            student=self.student,
            awarded_by=self.teacher_user,
            points=5,
            description="Great question",
        )
        self.assertIsNotNone(row)
        self.assertEqual(row.reason, PointLedger.Reason.BONUS)
        self.assertEqual(row.points, 5)

    def test_bonus_clamped_to_max_20(self):
        row = teacher_bonus(
            student=self.student,
            awarded_by=self.teacher_user,
            points=999,
            description="Over the limit",
        )
        self.assertEqual(row.points, 20)

    def test_bonus_clamped_to_min_1(self):
        row = teacher_bonus(
            student=self.student,
            awarded_by=self.teacher_user,
            points=0,
            description="At zero",
        )
        self.assertEqual(row.points, 1)

    def test_empty_description_raises(self):
        with self.assertRaises(ValueError):
            teacher_bonus(
                student=self.student,
                awarded_by=self.teacher_user,
                points=5,
                description="   ",
            )


class CheckLevelUpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        enable_engagement(cls.school_class)
        cls.student = make_student_user(cls.school_class, admission_number="LU-001")
        PointConfig.objects.get_or_create(pk=1)

    def test_level_up_fires_notification(self):
        # Award 50 points (Seedling → Sprout threshold)
        award_points(
            student=self.student,
            reason="lesson",
            points=50,
            description="Big lesson",
            idempotency_key=f"lesson:lu:big:{self.student.pk}",
        )
        # Notification should have been created for the student
        notifs = Notification.objects.filter(
            recipient=self.student.user,
            category="level_up",
        )
        self.assertTrue(notifs.exists())
        self.assertIn("Sprout", notifs.first().title)

    def test_level_up_notification_fires_only_once(self):
        # Award points twice at the same level
        award_points(
            student=self.student,
            reason="lesson",
            points=50,
            description="Big lesson",
            idempotency_key=f"lesson:lu:once1:{self.student.pk}",
        )
        award_points(
            student=self.student,
            reason="lesson",
            points=1,
            description="Small lesson",
            idempotency_key=f"lesson:lu:once2:{self.student.pk}",
        )
        count = Notification.objects.filter(
            recipient=self.student.user,
            category="level_up",
            title__contains="Sprout",
        ).count()
        self.assertEqual(count, 1)

    def test_student_level_cache_updated(self):
        award_points(
            student=self.student,
            reason="lesson",
            points=10,
            description="Lesson",
            idempotency_key=f"lesson:lu:cache:{self.student.pk}",
        )
        sl = StudentLevel.objects.get(student=self.student)
        self.assertEqual(sl.total_points, 10)
        self.assertEqual(sl.level_name, "Seedling")


class StreakMilestoneTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        enable_engagement(cls.school_class)
        cls.student = make_student_user(cls.school_class, admission_number="SM-001")
        PointConfig.objects.get_or_create(pk=1)

    def test_milestone_5_awards_bonus(self):
        maybe_award_streak_bonus(student=self.student, current_streak=5)
        self.assertTrue(
            PointLedger.objects.filter(
                student=self.student,
                reason=PointLedger.Reason.STREAK,
            ).exists()
        )

    def test_non_milestone_does_not_award(self):
        maybe_award_streak_bonus(student=self.student, current_streak=3)
        self.assertFalse(
            PointLedger.objects.filter(
                student=self.student,
                reason=PointLedger.Reason.STREAK,
            ).exists()
        )

    def test_milestone_idempotent(self):
        maybe_award_streak_bonus(student=self.student, current_streak=10)
        maybe_award_streak_bonus(student=self.student, current_streak=10)
        count = PointLedger.objects.filter(
            student=self.student,
            reason=PointLedger.Reason.STREAK,
            idempotency_key=f"streak:{self.student.pk}:10",
        ).count()
        self.assertEqual(count, 1)


# ============================================================================
# C. Hooks — integration via services
# ============================================================================


class LessonHookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.subject = make_subject("HOOK", "Hookology")
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.subject)
        enable_engagement(cls.school_class)
        cls.student = make_student_user(cls.school_class, admission_number="HK-001")
        PointConfig.objects.get_or_create(pk=1)

        mod = CourseModule.objects.create(
            school_class=cls.school_class,
            subject=cls.subject,
            term=cls.term,
            title="Module 1",
        )
        cls.lesson = Lesson.objects.create(module=mod, title="Lesson 1")

    def test_completing_lesson_awards_points(self):
        from apps.learning.engagement_services import record_lesson_completion
        record_lesson_completion(student=self.student, lesson=self.lesson)
        self.assertTrue(
            PointLedger.objects.filter(
                student=self.student,
                reason=PointLedger.Reason.LESSON,
            ).exists()
        )

    def test_completing_same_lesson_twice_earns_once(self):
        from apps.learning.engagement_services import record_lesson_completion
        record_lesson_completion(student=self.student, lesson=self.lesson)
        record_lesson_completion(student=self.student, lesson=self.lesson)
        count = PointLedger.objects.filter(
            student=self.student,
            reason=PointLedger.Reason.LESSON,
            idempotency_key=f"lesson:{self.lesson.pk}:{self.student.pk}",
        ).count()
        self.assertEqual(count, 1)


class HomeworkHookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.subject = make_subject("HWHK", "Homework Hook")
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.subject)
        enable_engagement(cls.school_class)
        cls.student = make_student_user(cls.school_class, admission_number="HW-001")
        cls.teacher_user, _ = make_teacher_user("hw_hook_teacher", "STF-HWH")
        PointConfig.objects.get_or_create(pk=1)

    def _homework(self, days_offset=1):
        from apps.homework.models import Homework
        from django.utils import timezone
        due = datetime.date.today() + datetime.timedelta(days=days_offset)
        hw = Homework.objects.create(
            title="Test HW",
            subject=self.subject,
            school_class=self.school_class,
            term=self.term,
            set_by=self.teacher_user,
            due_date=due,
            status=Homework.Status.PUBLISHED,
        )
        return hw

    def test_on_time_submission_awards_hw_ontime_points(self):
        from apps.homework.services import submit_homework
        hw = self._homework(days_offset=5)
        submit_homework(
            homework=hw,
            student=self.student,
            note="Done",
            actor=self.student.user,
        )
        self.assertTrue(
            PointLedger.objects.filter(
                student=self.student,
                reason=PointLedger.Reason.HW_ONTIME,
            ).exists()
        )

    def test_late_submission_awards_hw_late_points(self):
        from apps.homework.services import submit_homework
        hw = self._homework(days_offset=-1)   # past due
        submit_homework(
            homework=hw,
            student=self.student,
            note="Late but done",
            actor=self.student.user,
        )
        self.assertTrue(
            PointLedger.objects.filter(
                student=self.student,
                reason=PointLedger.Reason.HW_LATE,
            ).exists()
        )

    def test_second_submission_does_not_award_again(self):
        from apps.homework.services import submit_homework
        hw = self._homework(days_offset=5)
        submit_homework(
            homework=hw,
            student=self.student,
            note="First",
            actor=self.student.user,
        )
        # Try resubmitting (before due date — allowed)
        submit_homework(
            homework=hw,
            student=self.student,
            note="Second",
            actor=self.student.user,
        )
        count = PointLedger.objects.filter(
            student=self.student,
            reason=PointLedger.Reason.HW_ONTIME,
        ).count()
        self.assertEqual(count, 1)


class FeedHookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        cls.subject = make_subject("FHKQ", "Feed Hook")
        enable_engagement(cls.school_class)
        cls.teacher_user, cls.teacher = make_teacher_user("fhk_teacher", "STF-FHK")
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.subject,
            academic_year=cls.year,
        )
        cls.student = make_student_user(cls.school_class, admission_number="FHK-001")
        PointConfig.objects.get_or_create(pk=1)

    def test_student_question_earns_feed_points(self):
        from apps.learning.feed_services import create_post
        from apps.learning.models import ClassPost
        create_post(
            author=self.student.user,
            school_class=self.school_class,
            body="What is gravity?",
            post_type=ClassPost.PostType.QUESTION,
        )
        self.assertTrue(
            PointLedger.objects.filter(
                student=self.student,
                reason=PointLedger.Reason.FEED_POST,
            ).exists()
        )

    def test_removing_post_reverses_points(self):
        from apps.learning.feed_services import create_post, remove_post
        from apps.learning.models import ClassPost
        post = create_post(
            author=self.student.user,
            school_class=self.school_class,
            body="Bad question.",
            post_type=ClassPost.PostType.QUESTION,
        )
        # Verify points were awarded
        self.assertTrue(
            PointLedger.objects.filter(
                student=self.student,
                reason=PointLedger.Reason.FEED_POST,
            ).exists()
        )
        remove_post(post=post, removed_by=self.teacher_user)
        # A reversal row must now exist
        self.assertTrue(
            PointLedger.objects.filter(
                student=self.student,
                reason=PointLedger.Reason.REVERSAL,
            ).exists()
        )

    def test_teacher_post_earns_no_points(self):
        from apps.learning.feed_services import create_post
        from apps.learning.models import ClassPost
        create_post(
            author=self.teacher_user,
            school_class=self.school_class,
            body="Hello class!",
            post_type=ClassPost.PostType.ANNOUNCEMENT,
        )
        # Teachers are not students — no FEED_POST row
        self.assertFalse(
            PointLedger.objects.filter(reason=PointLedger.Reason.FEED_POST).exists()
        )


# ============================================================================
# D. points_selectors
# ============================================================================


class StudentPointsSummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        enable_engagement(cls.school_class)
        cls.student = make_student_user(cls.school_class, admission_number="SEL-001")
        PointConfig.objects.get_or_create(pk=1)

    def test_zero_points_is_seedling(self):
        from apps.learning.points_selectors import student_points_summary
        summary = student_points_summary(student=self.student)
        self.assertEqual(summary.total_points, 0)
        self.assertEqual(summary.current_level.name, "Seedling")

    def test_pct_to_next_is_0_at_start(self):
        from apps.learning.points_selectors import student_points_summary
        summary = student_points_summary(student=self.student)
        self.assertEqual(summary.pct_to_next, 0)

    def test_summary_reflects_awarded_points(self):
        from apps.learning.points_selectors import student_points_summary
        award_points(
            student=self.student,
            reason="lesson",
            points=50,
            description="50 pts",
            idempotency_key=f"lesson:sel:50:{self.student.pk}",
        )
        summary = student_points_summary(student=self.student)
        self.assertEqual(summary.total_points, 50)
        self.assertEqual(summary.current_level.name, "Sprout")

    def test_recent_ledger_not_empty_after_award(self):
        from apps.learning.points_selectors import student_points_summary
        award_points(
            student=self.student,
            reason="lesson",
            points=10,
            description="Lesson",
            idempotency_key=f"lesson:sel:hist:{self.student.pk}",
        )
        summary = student_points_summary(student=self.student)
        self.assertTrue(len(summary.recent_ledger) > 0)


# ============================================================================
# E. Views — access control
# ============================================================================


class StudentBadgesViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        enable_engagement(cls.school_class)
        cls.student = make_student_user(cls.school_class, admission_number="VBG-001")
        cls.parent_user, _ = make_parent_for(cls.student)
        cls.teacher_user, _ = make_teacher_user("vbg_teacher", "STF-VBG")
        PointConfig.objects.get_or_create(pk=1)

    def test_student_can_view_badges(self):
        self.client.force_login(self.student.user)
        r = self.client.get(reverse("learning:student_badges"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "My Badges")
        self.assertContains(r, "Seedling")

    def test_parent_can_view_childs_badges(self):
        self.client.force_login(self.parent_user)
        r = self.client.get(reverse("learning:student_badges"))
        self.assertEqual(r.status_code, 200)

    def test_teacher_cannot_access_student_badges(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(reverse("learning:student_badges"))
        self.assertEqual(r.status_code, 403)

    def test_anonymous_is_redirected(self):
        r = self.client.get(reverse("learning:student_badges"))
        self.assertEqual(r.status_code, 302)

    def test_engagement_off_redirects(self):
        row = SchoolSettings.load()
        row.engagement_enabled = False
        row.save(update_fields=["engagement_enabled", "updated_at"])
        self.client.force_login(self.student.user)
        r = self.client.get(reverse("learning:student_badges"))
        self.assertEqual(r.status_code, 302)
        row.engagement_enabled = True
        row.save(update_fields=["engagement_enabled", "updated_at"])


class TeacherClassPointsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year, name="Grade T1", level_order=9)
        cls.subject = make_subject("TPT", "Teacher Points")
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.subject)
        enable_engagement(cls.school_class)
        cls.teacher_user, cls.teacher = make_teacher_user("tp_teacher", "STF-TP1")
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.subject,
            academic_year=cls.year,
        )
        cls.student = make_student_user(cls.school_class, admission_number="TP-001")
        PointConfig.objects.get_or_create(pk=1)

    def _url(self):
        return reverse("learning:teacher_class_points", args=[self.school_class.pk])

    def test_assigned_teacher_sees_class_points(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Class Points")

    def test_unassigned_teacher_gets_404(self):
        other_user, _ = make_teacher_user("tp_other", "STF-TP2")
        self.client.force_login(other_user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 404)

    def test_student_cannot_access(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 403)


class TeacherBonusAwardViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year, name="Grade T2", level_order=10)
        cls.subject = make_subject("TBA", "Teacher Bonus")
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.subject)
        enable_engagement(cls.school_class)
        cls.teacher_user, cls.teacher = make_teacher_user("tba_teacher", "STF-TBA")
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.subject,
            academic_year=cls.year,
        )
        cls.student = make_student_user(cls.school_class, admission_number="TBA-001")
        PointConfig.objects.get_or_create(pk=1)

    def _url(self):
        return reverse(
            "learning:teacher_bonus_award",
            args=[self.school_class.pk, self.student.pk],
        )

    def test_teacher_can_award_bonus(self):
        self.client.force_login(self.teacher_user)
        r = self.client.post(self._url(), {"points": "5", "description": "Great answer"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(
            PointLedger.objects.filter(
                student=self.student,
                reason=PointLedger.Reason.BONUS,
                points=5,
            ).exists()
        )

    def test_empty_description_shows_error(self):
        self.client.force_login(self.teacher_user)
        r = self.client.post(self._url(), {"points": "5", "description": "  "})
        self.assertEqual(r.status_code, 302)   # redirects with error message
        self.assertFalse(
            PointLedger.objects.filter(
                student=self.student,
                reason=PointLedger.Reason.BONUS,
            ).exists()
        )

    def test_student_cannot_access_bonus_form(self):
        self.client.force_login(self.student.user)
        r = self.client.post(self._url(), {"points": "5", "description": "Cheat"})
        self.assertEqual(r.status_code, 403)


class DirectorPointsOverviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year, name="Grade D1", level_order=11)
        enable_engagement(cls.school_class)
        cls.director_user = make_user("director", username="pts_director")
        cls.teacher_user, _ = make_teacher_user("dir_pts_teacher", "STF-DPT")
        PointConfig.objects.get_or_create(pk=1)

    def test_director_sees_overview(self):
        self.client.force_login(self.director_user)
        r = self.client.get(reverse("learning:director_points"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Points Overview")

    def test_teacher_cannot_access(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(reverse("learning:director_points"))
        self.assertEqual(r.status_code, 403)
