"""
apps/learning/tests/test_f2a.py
---------------------------------
Tests for F2a:
  A. Daily cap fix — net feed points (reversal restores cap same day)
  B. Reply starters — student bare-starter refusal, both sets, settings override
  C. Student notifications — first reply, best answer, quiet hours
  D. Morning digest — selection rules, recipients, one per teacher per day
"""

from __future__ import annotations

import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from apps.academics.models import ClassSubject, SchoolClass
from apps.accounts.models import ParentProfile
from apps.core.models import Notification, NotificationPreference, SchoolSettings
from apps.core.notifications import (
    _quiet_deliver_after,
    delivered_notifications,
    notify,
)
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.learning.feed_services import (
    create_post,
    create_reply,
    mark_best_answer,
    remove_post,
    remove_reply,
)
from apps.learning.models import (
    ClassEngagementSettings,
    ClassFeedSettings,
    ClassPost,
    ClassPostReply,
    PointConfig,
    PointLedger,
)
from apps.learning.points_config import REASON_FEED_POST
from apps.learning.points_services import (
    _daily_feed_total,
    award_points,
    reverse_award,
)
from apps.learning.reply_starters import (
    DEFAULT_STUDENT_STARTERS,
    DEFAULT_TEACHER_STARTERS,
    is_starter_only,
    starters_for_role,
)
from apps.students.models import StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def enable_learning_and_feed(school_class=None):
    row = SchoolSettings.load()
    row.learning_enabled = True
    row.feed_community_enabled = True
    row.save(update_fields=["learning_enabled", "feed_community_enabled", "updated_at"])
    if school_class is not None:
        ClassFeedSettings.objects.update_or_create(
            school_class=school_class, defaults={"is_active": True}
        )


def enable_engagement(school_class=None):
    row = SchoolSettings.load()
    row.engagement_enabled = True
    row.save(update_fields=["engagement_enabled", "updated_at"])
    if school_class is not None:
        ClassEngagementSettings.objects.get_or_create(
            school_class=school_class, defaults={"is_active": True}
        )


def make_teacher(username, staff_id):
    user = make_user("teacher", username=username)
    profile = TeacherProfile.objects.create(user=user, staff_id=staff_id)
    return user, profile


def make_parent_for(student):
    parent_user = make_user("parent", username=f"par_{student.admission_number}")
    profile = ParentProfile.objects.create(user=parent_user)
    StudentGuardian.objects.create(student=student, parent=profile, is_primary=True)
    return parent_user, profile


def local_at(hour, minute=0, day=None):
    """An aware datetime at *hour* in the project time zone (today by default)."""
    tz = ZoneInfo("Africa/Nairobi")
    base_day = day or timezone.localdate()
    return datetime.datetime.combine(
        base_day, datetime.time(hour, minute), tzinfo=tz
    )


# ============================================================================
# A. Daily cap fix
# ============================================================================


class DailyCapFixTests(TestCase):
    """
    Net feed points: a reversed post restores cap credit the same day.
    """

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Cap Class", level_order=50)
        cls.subj = make_subject("CAPSUBJ", "Cap Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.teacher_user, cls.teacher = make_teacher("cap_teacher", "CAP-001")
        TeacherAssignment.objects.create(
            teacher=cls.teacher, school_class=cls.sc,
            subject=cls.subj, academic_year=cls.year,
        )
        cls.student = make_student_user(cls.sc, admission_number="CAP-S01")
        enable_engagement(cls.sc)
        cfg = PointConfig.objects.get_or_create(pk=1)[0]
        cfg.daily_feed_cap = 15
        cfg.pts_feed_question = 5
        cfg.save()

    def _award_feed(self, key_suffix):
        return award_points(
            student=self.student,
            reason=REASON_FEED_POST,
            points=5,
            description="Feed question",
            idempotency_key=f"feed_post:cap_{key_suffix}:{self.student.pk}",
        )

    def test_three_posts_hit_cap_and_fourth_blocked(self):
        for i in range(3):
            r = self._award_feed(f"orig_{i}")
            self.assertIsNotNone(r, f"Award {i} should succeed")
        result = self._award_feed("orig_3")
        self.assertIsNone(result, "4th post should be blocked by cap")

    def test_reversal_restores_cap_credit_same_day(self):
        rows = [self._award_feed(f"rev_{i}") for i in range(3)]
        self.assertIsNone(self._award_feed("rev_blocked"), "cap should be full")

        # Reverse the second award
        reverse_award(ledger_row=rows[1], reversed_by=self.teacher_user)

        # Cap total should now be 10 (gross 15 − clawback 5)
        today = timezone.localdate()
        net = _daily_feed_total(self.student, today)
        self.assertEqual(net, 10, f"Expected 10 after reversal, got {net}")

        # Fourth post should now succeed
        fourth = self._award_feed("rev_fourth")
        self.assertIsNotNone(fourth, "4th post should succeed after reversal")

    def test_reversal_of_different_reason_does_not_change_feed_cap(self):
        # Award 2 feed posts (10 pts)
        for i in range(2):
            self._award_feed(f"other_{i}")

        # Award a lesson row and immediately reverse it
        lesson_row = award_points(
            student=self.student,
            reason=PointLedger.Reason.LESSON,
            points=10,
            description="Lesson",
            idempotency_key=f"lesson:other:999:{self.student.pk}",
        )
        reverse_award(ledger_row=lesson_row, reversed_by=self.teacher_user)

        # Feed cap should still be 10 (not affected by lesson reversal)
        today = timezone.localdate()
        net = _daily_feed_total(self.student, today)
        self.assertEqual(net, 10, "Lesson reversal must not change feed cap")

    def test_cap_fix_does_not_alter_existing_rows(self):
        """_daily_feed_total() is read-only. No row is updated or deleted."""
        r = self._award_feed("ro_test")
        self.assertIsNotNone(r)
        pk = r.pk
        pts_before = r.points

        today = timezone.localdate()
        _daily_feed_total(self.student, today)  # call the fixed function

        r.refresh_from_db()
        self.assertEqual(r.pk, pk)
        self.assertEqual(r.points, pts_before)

    def test_award_balance_unchanged_by_cap_fix(self):
        """The ledger still records exactly what was awarded; the cap only reads."""
        before = list(
            PointLedger.objects.filter(student=self.student)
            .order_by("pk")
            .values_list("pk", "reason", "points")
        )
        r = self._award_feed("balance")
        after = list(
            PointLedger.objects.filter(student=self.student)
            .order_by("pk")
            .values_list("pk", "reason", "points")
        )
        self.assertEqual(after[:-1], before)
        self.assertEqual(after[-1][1], PointLedger.Reason.FEED_POST)
        self.assertEqual(after[-1][2], r.points)


# ============================================================================
# B. Reply starters
# ============================================================================


class ReplyStarterTests(TestCase):
    """Starter text, role rules and create_reply() enforcement."""

    def test_teacher_set_has_ten_plain_starters(self):
        self.assertEqual(len(DEFAULT_TEACHER_STARTERS), 10)
        for s in DEFAULT_TEACHER_STARTERS:
            self.assertIsInstance(s, str)
            self.assertTrue(s.strip())

    def test_student_set_has_five_plain_starters(self):
        self.assertEqual(len(DEFAULT_STUDENT_STARTERS), 5)
        for s in DEFAULT_STUDENT_STARTERS:
            self.assertIsInstance(s, str)
            self.assertTrue(s.strip())

    def test_no_placeholder_brackets_in_either_set(self):
        for s in DEFAULT_TEACHER_STARTERS + DEFAULT_STUDENT_STARTERS:
            self.assertNotIn("[", s, f"Starter contains a placeholder: {s!r}")
            self.assertNotIn("]", s, f"Starter contains a placeholder: {s!r}")

    def test_is_starter_only_checks_students_only(self):
        self.assertTrue(is_starter_only(DEFAULT_STUDENT_STARTERS[0], "student"))
        self.assertFalse(is_starter_only(DEFAULT_STUDENT_STARTERS[0], "teacher"))

    def test_is_starter_only_matches_whitespace_and_case(self):
        raw = "  " + DEFAULT_STUDENT_STARTERS[0].upper() + "  "
        self.assertTrue(is_starter_only(raw, "student"))

    def test_is_starter_only_false_when_words_added(self):
        body = DEFAULT_STUDENT_STARTERS[0] + " checking the units first."
        self.assertFalse(is_starter_only(body, "student"))

    def test_is_starter_only_false_for_ordinary_answer(self):
        self.assertFalse(is_starter_only("The answer is 42 because 6 times 7.", "student"))

    def test_school_can_override_student_starters(self):
        row = SchoolSettings.load()
        row.feed_student_starters = "Ask about the units.\nDraw the shape."
        row.save(update_fields=["feed_student_starters", "updated_at"])
        self.assertEqual(
            starters_for_role("student"),
            ("Ask about the units.", "Draw the shape."),
        )

    def test_placeholder_lines_never_reach_the_reply_box(self):
        row = SchoolSettings.load()
        row.feed_student_starters = "Try [topic] here.\nUse the lesson notes."
        row.save(update_fields=["feed_student_starters", "updated_at"])
        self.assertEqual(starters_for_role("student"), ("Use the lesson notes.",))

    def test_blank_settings_fall_back_to_built_in_set(self):
        row = SchoolSettings.load()
        row.feed_teacher_starters = ""
        row.save(update_fields=["feed_teacher_starters", "updated_at"])
        self.assertEqual(starters_for_role("teacher"), DEFAULT_TEACHER_STARTERS)

    def test_parents_get_no_starters(self):
        self.assertEqual(starters_for_role("parent"), ())


class StarterReplyEnforcementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Starter Class", level_order=51)
        cls.subj = make_subject("STSUBJ", "Starter Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.teacher_user, cls.teacher = make_teacher("st_teacher", "ST-001")
        TeacherAssignment.objects.create(
            teacher=cls.teacher, school_class=cls.sc,
            subject=cls.subj, academic_year=cls.year,
        )
        cls.student = make_student_user(cls.sc, admission_number="ST-S01")
        cls.other_student = make_student_user(cls.sc, admission_number="ST-S02")
        enable_learning_and_feed(cls.sc)

    def _make_question(self):
        return create_post(
            author=self.student.user,
            school_class=self.sc,
            body="What is photosynthesis?",
            post_type=ClassPost.PostType.QUESTION,
        )

    def test_teacher_bare_starter_is_allowed(self):
        """A guiding question is the point of the teacher set."""
        post = self._make_question()
        reply = create_reply(
            post=post, author=self.teacher_user, body=DEFAULT_TEACHER_STARTERS[0]
        )
        self.assertIsNotNone(reply.pk)

    def test_student_bare_starter_is_refused(self):
        post = self._make_question()
        with self.assertRaises(ValueError) as cm:
            create_reply(
                post=post,
                author=self.other_student.user,
                body=DEFAULT_STUDENT_STARTERS[0],
            )
        self.assertIn("prompt", str(cm.exception).lower())
        self.assertEqual(
            ClassPostReply.objects.filter(post=post, is_removed=False).count(),
            0,
            "A refused starter must not become an answer",
        )

    def test_student_starter_plus_own_words_is_accepted(self):
        post = self._make_question()
        body = DEFAULT_STUDENT_STARTERS[0] + " re-reading page 12."
        reply = create_reply(post=post, author=self.other_student.user, body=body)
        self.assertIsNotNone(reply.pk)


# ============================================================================
# C. Student notifications
# ============================================================================


class FirstReplyNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Notif Class", level_order=52)
        cls.subj = make_subject("NFSUBJ", "Notif Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.teacher_user, cls.teacher = make_teacher("nf_teacher", "NF-001")
        cls.sc.class_teacher = cls.teacher_user
        cls.sc.save(update_fields=["class_teacher"])
        TeacherAssignment.objects.create(
            teacher=cls.teacher, school_class=cls.sc,
            subject=cls.subj, academic_year=cls.year,
        )
        cls.student = make_student_user(cls.sc, admission_number="NF-S01")
        cls.other_student = make_student_user(cls.sc, admission_number="NF-S02")
        enable_learning_and_feed(cls.sc)

    def test_first_reply_notifies_question_author(self):
        post = create_post(
            author=self.student.user,
            school_class=self.sc,
            body="What is gravity?",
            post_type=ClassPost.PostType.QUESTION,
        )
        Notification.objects.filter(recipient=self.student.user).delete()

        create_reply(post=post, author=self.teacher_user, body="Great question. What have you tried?")

        notifs = Notification.objects.filter(
            recipient=self.student.user, category="feed_reply"
        )
        self.assertEqual(notifs.count(), 1)
        self.assertIn(self.teacher_user.first_name, notifs.first().title)
        self.assertIn("Notif Class", notifs.first().title)

    def test_second_reply_does_not_notify_again(self):
        post = create_post(
            author=self.student.user,
            school_class=self.sc,
            body="What is momentum?",
            post_type=ClassPost.PostType.QUESTION,
        )
        Notification.objects.filter(recipient=self.student.user).delete()

        create_reply(post=post, author=self.teacher_user, body="First reply here.")
        count_after_first = Notification.objects.filter(
            recipient=self.student.user, category="feed_reply"
        ).count()
        create_reply(post=post, author=self.teacher_user, body="Second reply here.")
        count_after_second = Notification.objects.filter(
            recipient=self.student.user, category="feed_reply"
        ).count()

        self.assertEqual(count_after_first, 1)
        self.assertEqual(count_after_second, 1, "Second reply must not send another notification")

    def test_author_not_notified_about_own_reply(self):
        post = create_post(
            author=self.student.user,
            school_class=self.sc,
            body="I found the answer.",
            post_type=ClassPost.PostType.QUESTION,
        )
        Notification.objects.filter(recipient=self.student.user).delete()

        create_reply(post=post, author=self.student.user, body="Never mind, I figured it out.")

        notifs = Notification.objects.filter(
            recipient=self.student.user, category="feed_reply"
        )
        self.assertEqual(notifs.count(), 0, "Author must not be notified about their own reply")

    def test_reply_notification_contains_no_reply_content(self):
        post = create_post(
            author=self.student.user,
            school_class=self.sc,
            body="What is diffusion?",
            post_type=ClassPost.PostType.QUESTION,
        )
        secret_text = "SECRET_REPLY_CONTENT_XYZ"
        create_reply(post=post, author=self.teacher_user, body=secret_text)

        notif = Notification.objects.filter(
            recipient=self.student.user, category="feed_reply"
        ).first()
        self.assertIsNotNone(notif)
        self.assertNotIn(secret_text, notif.title)
        self.assertNotIn(secret_text, notif.message)

    def test_notify_opt_out_respected(self):
        post = create_post(
            author=self.student.user,
            school_class=self.sc,
            body="What is osmosis?",
            post_type=ClassPost.PostType.QUESTION,
        )
        pref = NotificationPreference.for_user(self.student.user)
        pref.notify_feed_reply = False
        pref.save()

        Notification.objects.filter(recipient=self.student.user).delete()
        create_reply(post=post, author=self.teacher_user, body="Good question. What do you notice?")

        self.assertEqual(
            Notification.objects.filter(
                recipient=self.student.user, category="feed_reply"
            ).count(),
            0,
        )
        # Restore
        pref.notify_feed_reply = True
        pref.save()

    def test_missing_preference_row_means_everything_on(self):
        NotificationPreference.objects.filter(user=self.student.user).delete()
        post = create_post(
            author=self.student.user,
            school_class=self.sc,
            body="First question without a preference row.",
            post_type=ClassPost.PostType.QUESTION,
        )
        Notification.objects.filter(recipient=self.student.user).delete()
        create_reply(post=post, author=self.teacher_user, body="Here is a hint.")
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.student.user, category="feed_reply"
            ).count(),
            1,
        )

    def test_removing_a_reply_sends_no_notification(self):
        post = create_post(
            author=self.student.user,
            school_class=self.sc,
            body="What is gravity again?",
            post_type=ClassPost.PostType.QUESTION,
        )
        reply = create_reply(post=post, author=self.teacher_user, body="A hint.")
        Notification.objects.filter(recipient=self.student.user).delete()

        remove_reply(reply=reply, removed_by=self.teacher_user, reason="Test removal")

        self.assertEqual(
            Notification.objects.filter(
                recipient=self.student.user, category="feed_reply"
            ).count(),
            0,
        )


class BestAnswerNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Best Answer Class", level_order=53)
        cls.subj = make_subject("BASUBJ", "BA Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.teacher_user, cls.teacher = make_teacher("ba_teacher", "BA-001")
        TeacherAssignment.objects.create(
            teacher=cls.teacher, school_class=cls.sc,
            subject=cls.subj, academic_year=cls.year,
        )
        cls.asker = make_student_user(cls.sc, admission_number="BA-S01")
        cls.answerer = make_student_user(cls.sc, admission_number="BA-S02")
        enable_learning_and_feed(cls.sc)

    def test_reply_author_notified_on_best_answer(self):
        post = create_post(
            author=self.asker.user,
            school_class=self.sc,
            body="Explain Newton's first law.",
            post_type=ClassPost.PostType.QUESTION,
        )
        reply = create_reply(post=post, author=self.answerer.user,
                             body="An object at rest stays at rest unless acted upon.")
        Notification.objects.filter(recipient=self.answerer.user).delete()

        mark_best_answer(reply=reply, actor=self.teacher_user)

        notif = Notification.objects.filter(
            recipient=self.answerer.user, category="feed_best_answer"
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Best Answer", notif.title)
        # Reply content must not appear
        self.assertNotIn("stays at rest", notif.title)
        self.assertNotIn("stays at rest", notif.message)

    def test_asker_notified_when_teacher_chooses_best_answer(self):
        post = create_post(
            author=self.asker.user,
            school_class=self.sc,
            body="What is photosynthesis?",
            post_type=ClassPost.PostType.QUESTION,
        )
        reply = create_reply(post=post, author=self.answerer.user,
                             body="Plants make food using sunlight.")
        Notification.objects.filter(recipient=self.asker.user).delete()

        mark_best_answer(reply=reply, actor=self.teacher_user)

        notif = Notification.objects.filter(
            recipient=self.asker.user, category="feed_best_answer"
        ).first()
        self.assertIsNotNone(notif, "Asker should be notified when teacher chooses best answer")
        self.assertIn(self.teacher_user.first_name, notif.title)

    def test_asker_not_notified_when_asker_chooses(self):
        post = create_post(
            author=self.asker.user,
            school_class=self.sc,
            body="What is friction?",
            post_type=ClassPost.PostType.QUESTION,
        )
        reply = create_reply(post=post, author=self.answerer.user,
                             body="A force that opposes motion.")
        Notification.objects.filter(recipient=self.asker.user).delete()

        mark_best_answer(reply=reply, actor=self.asker.user)

        self.assertEqual(
            Notification.objects.filter(
                recipient=self.asker.user, category="feed_best_answer"
            ).count(),
            0,
            "Choosers are not told about their own choice",
        )

    def test_marking_same_reply_twice_notifies_once(self):
        post = create_post(
            author=self.asker.user,
            school_class=self.sc,
            body="What is energy?",
            post_type=ClassPost.PostType.QUESTION,
        )
        reply = create_reply(post=post, author=self.answerer.user,
                             body="The ability to do work.")
        Notification.objects.filter(recipient=self.answerer.user).delete()

        mark_best_answer(reply=reply, actor=self.teacher_user)
        mark_best_answer(reply=reply, actor=self.teacher_user)

        self.assertEqual(
            Notification.objects.filter(
                recipient=self.answerer.user, category="feed_best_answer"
            ).count(),
            1,
        )


# ============================================================================
# C2. Quiet hours
# ============================================================================


class QuietHoursTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.sc = make_class(cls.year, name="QH Class", level_order=54)
        cls.student = make_student_user(cls.sc, admission_number="QH-S01")

    def _enable_quiet_hours(self, start=20, end=6):
        SchoolSettings.objects.update_or_create(
            pk=SchoolSettings.SINGLETON_PK,
            defaults={
                "notification_quiet_hours_enabled": True,
                "quiet_hours_start": start,
                "quiet_hours_end": end,
            },
        )

    def _disable_quiet_hours(self):
        SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).update(
            notification_quiet_hours_enabled=False
        )

    def _deliver_after_at(self, hour):
        with patch("django.utils.timezone.now", return_value=local_at(hour)):
            return _quiet_deliver_after()

    def test_off_means_deliver_immediately(self):
        self._disable_quiet_hours()
        self.assertIsNone(self._deliver_after_at(23))

    def test_inside_cross_midnight_window_waits_for_morning(self):
        self._enable_quiet_hours(20, 6)
        self.assertEqual(
            self._deliver_after_at(23),
            local_at(6, day=timezone.localdate() + datetime.timedelta(days=1)),
        )

    def test_after_midnight_waits_for_today_six(self):
        self._enable_quiet_hours(20, 6)
        self.assertEqual(self._deliver_after_at(2), local_at(6))

    def test_outside_window_delivers_now(self):
        self._enable_quiet_hours(20, 6)
        self.assertIsNone(self._deliver_after_at(12))
        self.assertIsNone(self._deliver_after_at(18))

    def test_same_day_window_waits_for_its_end(self):
        self._enable_quiet_hours(13, 15)
        self.assertEqual(self._deliver_after_at(14), local_at(15))
        self.assertIsNone(self._deliver_after_at(16))

    def test_same_start_and_end_is_never_quiet(self):
        self._enable_quiet_hours(20, 20)
        self.assertIsNone(self._deliver_after_at(21))

    def test_held_notification_is_hidden_from_bell_then_appears(self):
        self._enable_quiet_hours(20, 6)
        with patch("django.utils.timezone.now", return_value=local_at(23)):
            notify(
                recipient=self.student.user,
                title="Held overnight",
                category="feed_reply",
            )
        notif = Notification.objects.get(title="Held overnight")
        self.assertIsNotNone(notif.deliver_after)
        qs = Notification.objects.filter(recipient=self.student.user)
        # Still inside the window
        with patch("django.utils.timezone.now", return_value=local_at(23)):
            self.assertEqual(delivered_notifications(qs).count(), 0)
        # Morning
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        with patch(
            "django.utils.timezone.now", return_value=local_at(6, day=tomorrow)
        ):
            self.assertEqual(delivered_notifications(qs).count(), 1)

    def test_digest_notification_is_never_held(self):
        """The 07:00 digest is generated for the morning; holding it defeats it."""
        self._enable_quiet_hours(0, 23)
        with patch("django.utils.timezone.now", return_value=local_at(7)):
            notify(
                recipient=self.student.user,
                title="Digest test",
                category="digest",
            )
        notif = Notification.objects.get(title="Digest test")
        self.assertIsNone(notif.deliver_after)

    def test_quiet_hours_model_hour_validation(self):
        from django.core.exceptions import ValidationError

        row = SchoolSettings.load()
        row.quiet_hours_start = 25   # invalid
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_quiet_hours_midnight_crossing_accepted(self):
        row = SchoolSettings.load()
        row.quiet_hours_start = 20
        row.quiet_hours_end = 6
        row.notification_quiet_hours_enabled = True
        try:
            row.full_clean()
        except Exception as exc:
            self.fail(f"20→6 quiet window raised unexpectedly: {exc}")

    def test_quiet_hours_default_is_off(self):
        row = SchoolSettings.load()
        self.assertFalse(row.notification_quiet_hours_enabled)


# ============================================================================
# D. Morning digest
# ============================================================================


class DigestTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Digest Class", level_order=55)
        cls.subj = make_subject("DGSUBJ", "Digest Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.teacher_user, cls.teacher = make_teacher("dg_teacher", "DG-001")
        TeacherAssignment.objects.create(
            teacher=cls.teacher, school_class=cls.sc,
            subject=cls.subj, academic_year=cls.year,
        )
        cls.student = make_student_user(cls.sc, admission_number="DG-S01")
        enable_learning_and_feed(cls.sc)

    def _post_question(self, body="Why is the sky blue?", school_class=None):
        return create_post(
            author=self.student.user,
            school_class=school_class or self.sc,
            body=body,
            post_type=ClassPost.PostType.QUESTION,
        )

    def _run(self, **kwargs):
        from apps.learning.digest_tasks import send_teacher_digests
        return send_teacher_digests(force=True, **kwargs)

    def _digest_for(self, user):
        return Notification.objects.filter(
            recipient=user, category="digest"
        ).first()

    # ── Empty digest never sent ─────────────────────────────────────────

    def test_no_digest_sent_when_no_unanswered_questions(self):
        Notification.objects.filter(category="digest").delete()
        self.assertEqual(self._run(), 0)
        self.assertFalse(Notification.objects.filter(category="digest").exists())

    def test_answered_question_excluded_so_digest_is_empty(self):
        post = self._post_question("What is respiration?")
        create_reply(post=post, author=self.teacher_user,
                     body="Think about what cells need to make energy.")
        Notification.objects.filter(category="digest").delete()

        self.assertEqual(self._run(), 0)

    # ── A waiting question produces one digest ───────────────────────────

    def test_unanswered_question_triggers_digest_and_stamps(self):
        post = self._post_question("What is photosynthesis?")

        sent = self._run()

        self.assertEqual(sent, 1)
        notif = self._digest_for(self.teacher_user)
        self.assertIsNotNone(notif)
        self.assertIn("1 student question", notif.title)
        self.assertIn("/feed/?filter=unanswered", notif.link)
        post.refresh_from_db()
        self.assertIsNotNone(post.digest_sent_at)

    def test_removed_question_excluded(self):
        post = self._post_question("Inappropriate post text")
        remove_post(post=post, removed_by=self.teacher_user)
        self.assertEqual(self._run(), 0)

    def test_removed_reply_puts_question_back_in_digest(self):
        post = self._post_question("Can someone check my answer?")
        reply = create_reply(post=post, author=self.teacher_user, body="A hint.")
        remove_reply(reply=reply, removed_by=self.teacher_user, reason="Test")

        sent = self._run()

        self.assertEqual(sent, 1)

    def test_digest_sent_at_prevents_repeat(self):
        post = self._post_question("What is osmosis?")
        self._run()
        post.refresh_from_db()
        self.assertIsNotNone(post.digest_sent_at)

        Notification.objects.filter(category="digest").delete()
        self.assertEqual(self._run(), 0)

    # ── One digest per teacher per day, across classes ───────────────────

    def test_teacher_with_two_classes_gets_one_digest(self):
        second_sc = make_class(self.year, name="Digest Class 2", level_order=56)
        ClassSubject.objects.create(school_class=second_sc, subject=self.subj)
        TeacherAssignment.objects.create(
            teacher=self.teacher, school_class=second_sc,
            subject=self.subj, academic_year=self.year,
        )
        ClassFeedSettings.objects.create(school_class=second_sc, is_active=True)
        make_student_user(second_sc, admission_number="DG-S02")

        self._post_question("First class question")
        second_student = make_student_user(second_sc, admission_number="DG-S03")
        create_post(
            author=second_student.user,
            school_class=second_sc,
            body="Second class question",
            post_type=ClassPost.PostType.QUESTION,
        )

        sent = self._run()

        self.assertEqual(sent, 1)
        notif = self._digest_for(self.teacher_user)
        self.assertIn("2 student questions", notif.title)
        self.assertIn("Digest Class: 1 waiting", notif.message)
        self.assertIn("Digest Class 2: 1 waiting", notif.message)

    def test_second_run_same_day_sends_nothing_more(self):
        self._post_question("Question A")
        self.assertEqual(self._run(), 1)
        self.assertEqual(self._run(), 0)

    # ── Older questions are never hidden ─────────────────────────────────

    def test_older_waiting_line_uses_exact_wording(self):
        self._post_question("Older question")
        self.assertEqual(self._run(), 1)

        # Next day: today's digest rows are gone, a new question arrives.
        Notification.objects.filter(category="digest").delete()
        self._post_question("Newer question")

        self.assertEqual(self._run(), 1)
        notif = self._digest_for(self.teacher_user)
        self.assertIn("1 older question is still waiting", notif.message)

    def test_two_older_questions_use_plural(self):
        for i in range(2):
            self._post_question(f"Old question {i}")
        self._run()

        Notification.objects.filter(category="digest").delete()
        self._post_question("New question")

        self._run()
        notif = self._digest_for(self.teacher_user)
        self.assertIn("2 older questions are still waiting", notif.message)

    # ── Recipients ───────────────────────────────────────────────────────

    def test_unlinked_question_goes_to_class_teachers_only(self):
        from apps.learning.digest_tasks import get_digest_recipients

        other_class = make_class(self.year, name="Other Digest Class", level_order=57)
        other_subject = make_subject("DGOTH", "Other Subject")
        ClassSubject.objects.create(school_class=other_class, subject=other_subject)
        unrelated_user, unrelated_teacher = make_teacher("dg_other", "DG-003")
        TeacherAssignment.objects.create(
            teacher=unrelated_teacher,
            school_class=other_class,
            subject=other_subject,
            academic_year=self.year,
        )

        post = self._post_question("No lesson link")
        recipients = get_digest_recipients(post)

        self.assertIn(self.teacher_user, recipients)
        self.assertNotIn(unrelated_user, recipients)

    def test_class_teacher_without_subject_assignment_receives_digest(self):
        from apps.learning.digest_tasks import get_digest_recipients

        form_teacher_user, _ = make_teacher("dg_form", "DG-004")
        self.sc.class_teacher = form_teacher_user
        self.sc.save(update_fields=["class_teacher"])

        post = self._post_question("Who is our class teacher?")
        self.assertIn(form_teacher_user, get_digest_recipients(post))

    def test_subject_teacher_of_linked_lesson_receives_digest(self):
        from apps.learning.digest_tasks import get_digest_recipients
        from apps.learning.models import CourseModule, Lesson

        second_subj = make_subject("DGSUB2", "Digest Subject 2")
        ClassSubject.objects.get_or_create(
            school_class=self.sc, subject=second_subj
        )
        second_teacher_user, second_teacher = make_teacher("dg_teacher2", "DG-002")
        TeacherAssignment.objects.create(
            teacher=second_teacher,
            school_class=self.sc,
            subject=second_subj,
            academic_year=self.year,
        )

        mod = CourseModule.objects.create(
            school_class=self.sc, subject=second_subj,
            term=self.term, title="Mod", position=0,
        )
        lesson = Lesson.objects.create(module=mod, title="Lesson", position=0)

        post = create_post(
            author=self.student.user,
            school_class=self.sc,
            body="Lesson linked question",
            post_type=ClassPost.PostType.QUESTION,
            lesson=lesson,
        )

        self.assertIn(second_teacher_user, get_digest_recipients(post))

    # ── Switches ─────────────────────────────────────────────────────────

    def test_no_digest_when_class_feed_switched_off(self):
        ClassFeedSettings.objects.filter(school_class=self.sc).update(is_active=False)
        self._post_question("Should not appear")

        self.assertEqual(self._run(), 0)

        ClassFeedSettings.objects.filter(school_class=self.sc).update(is_active=True)

    def test_no_digest_when_school_feed_switched_off(self):
        SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).update(
            feed_community_enabled=False
        )
        self._post_question("Should not appear")

        self.assertEqual(self._run(), 0)

        SchoolSettings.objects.filter(pk=SchoolSettings.SINGLETON_PK).update(
            feed_community_enabled=True
        )

    # ── Weekday guard ────────────────────────────────────────────────────

    def test_weekend_run_is_skipped_unless_forced(self):
        from apps.learning.digest_tasks import send_teacher_digests

        saturday = datetime.date(2026, 11, 14)
        self._post_question("Weekend question")

        with patch(
            "django.utils.timezone.now",
            return_value=local_at(7, day=saturday),
        ):
            self.assertEqual(send_teacher_digests(), 0)

        self.assertEqual(self._run(), 1)
