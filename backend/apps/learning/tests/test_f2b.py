"""
apps/learning/tests/test_f2b.py
---------------------------------
F2b — class polls.

Covers the database-level one-vote rule, closing by clock, result
visibility, class/parent permissions, option limits, escaping, the class
switch, and the Nairobi-time conversion in the create view.
"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import ClassSubject
from apps.accounts.models import ParentProfile
from apps.core.models import SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.learning import poll_services
from apps.learning.feed_services import create_post, remove_post
from apps.learning.models import (
    ClassFeedSettings,
    ClassPoll,
    ClassPollVote,
    ClassPost,
)
from apps.learning.poll_selectors import build_poll_summary
from apps.students.models import StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile


def _set_switches(*, classes, community=True, polls=True):
    row = SchoolSettings.load()
    row.feed_community_enabled = community
    row.learning_enabled = True
    row.save(
        update_fields=[
            "feed_community_enabled",
            "learning_enabled",
            "updated_at",
        ]
    )
    for school_class in classes:
        ClassFeedSettings.objects.update_or_create(
            school_class=school_class,
            defaults={"is_active": True, "polls_enabled": polls},
        )


class PollTestCase(TestCase):
    """One teacher, two pupils, a parent, and a second class."""

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 6", level_order=6)
        cls.other_class = make_class(cls.year, name="Grade 7", level_order=7)
        cls.subject = make_subject("SCI", "Science")
        ClassSubject.objects.create(
            school_class=cls.school_class, subject=cls.subject
        )

        cls.teacher_user = make_user("teacher", username="poll_teacher")
        cls.teacher = TeacherProfile.objects.create(
            user=cls.teacher_user, staff_id="PT-001"
        )
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.subject,
            academic_year=cls.year,
        )

        cls.student1 = make_student_user(
            cls.school_class, admission_number="PL-001"
        )
        cls.student2 = make_student_user(
            cls.school_class, admission_number="PL-002"
        )
        cls.other_student = make_student_user(
            cls.other_class, admission_number="PL-003"
        )

        parent_user = make_user("parent", username="poll_parent")
        parent_profile = ParentProfile.objects.create(user=parent_user)
        StudentGuardian.objects.create(
            student=cls.student1, parent=parent_profile, is_primary=True
        )
        cls.parent_user = parent_user

        _set_switches(
            classes=(cls.school_class, cls.other_class), community=True, polls=True
        )

    def make_poll(
        self,
        *,
        question="Which house should lead the assembly?",
        options=None,
        results_visibility=ClassPoll.ResultsVisibility.AFTER_CLOSE,
        closes_at=None,
        author=None,
        school_class=None,
    ):
        return poll_services.create_poll(
            author=author or self.teacher_user,
            school_class=school_class or self.school_class,
            question=question,
            options=options or ["Blue", "Green", "Red"],
            results_visibility=results_visibility,
            closes_at=closes_at,
        )

    def vote_url(self, poll):
        return reverse(
            "learning:feed_poll_vote",
            kwargs={
                "class_id": poll.post.school_class_id,
                "post_id": poll.post_id,
            },
        )

    def feed_url(self):
        return reverse(
            "learning:class_feed", kwargs={"class_id": self.school_class.pk}
        )


# ---------------------------------------------------------------------------
# Creation and option limits
# ---------------------------------------------------------------------------


class CreationTests(PollTestCase):
    def test_teacher_can_create_a_poll_with_two_to_six_options(self):
        poll = self.make_poll(closes_at=timezone.now() + timedelta(days=1))
        self.assertEqual(poll.options.count(), 3)
        self.assertEqual(poll.post.post_type, ClassPost.PostType.POLL)
        self.assertEqual(poll.results_visibility, "after_close")

    def test_one_option_is_rejected(self):
        with self.assertRaises(ValueError):
            self.make_poll(options=["Only one"])

    def test_seven_options_are_rejected(self):
        with self.assertRaises(ValueError):
            self.make_poll(options=[f"Option {i}" for i in range(1, 8)])

    def test_blank_options_are_ignored_before_counting(self):
        poll = self.make_poll(
            options=["Yes", "", "No", "  "],
            closes_at=timezone.now() + timedelta(days=1),
        )
        self.assertEqual(list(poll.options.values_list("text", flat=True)), ["Yes", "No"])

    def test_duplicate_options_are_rejected(self):
        with self.assertRaises(ValueError):
            self.make_poll(options=["Yes", "yes"])

    def test_option_text_is_capped(self):
        with self.assertRaises(ValueError):
            self.make_poll(options=["A" * 121, "B"])

    def test_after_close_requires_a_closing_time(self):
        with self.assertRaises(ValueError):
            self.make_poll(
                results_visibility=ClassPoll.ResultsVisibility.AFTER_CLOSE,
                closes_at=None,
            )

    def test_past_closing_time_is_rejected(self):
        with self.assertRaises(ValueError):
            self.make_poll(
                results_visibility=ClassPoll.ResultsVisibility.LIVE,
                closes_at=timezone.now() - timedelta(minutes=1),
            )

    def test_student_cannot_create_a_poll(self):
        with self.assertRaises(PermissionDenied):
            self.make_poll(author=self.student1.user)

    def test_parent_cannot_create_a_poll(self):
        with self.assertRaises(PermissionDenied):
            self.make_poll(author=self.parent_user)

    def test_post_box_cannot_create_a_poll_post(self):
        with self.assertRaises(PermissionDenied):
            create_post(
                author=self.teacher_user,
                school_class=self.school_class,
                body="Sneaky poll",
                post_type=ClassPost.PostType.POLL,
            )

    def test_create_view_interprets_the_closing_time_as_nairobi(self):
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            reverse(
                "learning:feed_poll_create",
                kwargs={"class_id": self.school_class.pk},
            ),
            {
                "question": "Which trip?",
                "option_1": "Museum",
                "option_2": "Park",
                "results_visibility": "after_close",
                "closes_at": "2030-11-12T18:00",
            },
        )
        self.assertEqual(response.status_code, 302)
        poll = ClassPoll.objects.get(post__body="Which trip?")
        self.assertEqual(
            poll.closes_at.astimezone(ZoneInfo("Africa/Nairobi")).hour, 18
        )
        self.assertEqual(poll.closes_at.hour, 15)   # stored UTC


# ---------------------------------------------------------------------------
# One vote, and changing it
# ---------------------------------------------------------------------------


class VoteTests(PollTestCase):
    def test_student_at_home_class_votes_once(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        option = poll.options.first()

        poll_services.cast_vote(
            poll=poll, voter=self.student1.user, option_id=option.pk
        )
        poll_services.cast_vote(
            poll=poll, voter=self.student1.user, option_id=option.pk
        )

        self.assertEqual(poll.votes.count(), 1)

    def test_database_rejects_a_second_vote_row(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        option = poll.options.first()
        ClassPollVote.objects.create(
            poll=poll, option=option, voter=self.student1.user
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClassPollVote.objects.create(
                    poll=poll, option=option, voter=self.student1.user
                )

    def test_student_may_change_their_vote_before_close(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        first, second = list(poll.options.all())[:2]

        poll_services.cast_vote(
            poll=poll, voter=self.student1.user, option_id=first.pk
        )
        poll_services.cast_vote(
            poll=poll, voter=self.student1.user, option_id=second.pk
        )

        vote = poll.votes.get(voter=self.student1.user)
        self.assertEqual(vote.option_id, second.pk)
        self.assertEqual(poll.votes.count(), 1)

    def test_votes_cannot_change_after_the_poll_closes(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE,
            closes_at=timezone.now() + timedelta(minutes=5),
        )
        option, other = list(poll.options.all())[:2]
        poll_services.cast_vote(
            poll=poll, voter=self.student1.user, option_id=option.pk
        )

        poll.closes_at = timezone.now() - timedelta(seconds=1)
        poll.save(update_fields=["closes_at", "updated_at"])

        with self.assertRaises(PermissionDenied):
            poll_services.cast_vote(
                poll=poll, voter=self.student1.user, option_id=other.pk
            )
        self.assertEqual(
            poll.votes.get(voter=self.student1.user).option_id, option.pk
        )

    def test_closing_is_decided_by_the_clock_not_a_job(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE,
            closes_at=timezone.now() + timedelta(minutes=10),
        )
        self.assertFalse(poll.is_closed())
        self.assertFalse(poll.is_closed(now=timezone.now()))
        self.assertTrue(
            poll.is_closed(now=timezone.now() + timedelta(minutes=11))
        )

    def test_vote_view_saves_a_vote(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        option = poll.options.first()
        self.client.force_login(self.student1.user)

        response = self.client.post(
            self.vote_url(poll), {"option": option.pk, "from": "thread"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            poll.votes.get(voter=self.student1.user).option_id, option.pk
        )

    def test_vote_view_rejects_the_closed_poll(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE,
            closes_at=timezone.now() + timedelta(minutes=10),
        )
        poll.closes_at = timezone.now() - timedelta(seconds=1)
        poll.save(update_fields=["closes_at", "updated_at"])
        self.client.force_login(self.student1.user)

        self.client.post(self.vote_url(poll), {"option": poll.options.first().pk})

        self.assertFalse(poll.votes.exists())


# ---------------------------------------------------------------------------
# Permissions: other class, parent, removed post
# ---------------------------------------------------------------------------


class PermissionTests(PollTestCase):
    def test_student_of_another_class_is_refused_by_the_service(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        with self.assertRaises(PermissionDenied):
            poll_services.cast_vote(
                poll=poll,
                voter=self.other_student.user,
                option_id=poll.options.first().pk,
            )
        self.assertFalse(poll.votes.exists())

    def test_student_of_another_class_is_refused_by_the_view(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        self.client.force_login(self.other_student.user)

        response = self.client.post(
            self.vote_url(poll), {"option": poll.options.first().pk}
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(poll.votes.exists())

    def test_parent_cannot_vote(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        with self.assertRaises(PermissionDenied):
            poll_services.cast_vote(
                poll=poll,
                voter=self.parent_user,
                option_id=poll.options.first().pk,
            )

        self.client.force_login(self.parent_user)
        response = self.client.post(
            self.vote_url(poll), {"option": poll.options.first().pk}
        )
        self.assertIn(response.status_code, (403, 404))
        self.assertFalse(poll.votes.exists())

    def test_removed_poll_is_hidden_from_students_and_parents(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        remove_post(post=poll.post, removed_by=self.teacher_user)

        self.client.force_login(self.student1.user)
        student_page = self.client.get(self.feed_url())
        self.assertNotContains(student_page, poll.post.body)

        self.client.force_login(self.parent_user)
        parent_page = self.client.get(self.feed_url())
        self.assertNotContains(parent_page, poll.post.body)

        self.client.force_login(self.teacher_user)
        teacher_page = self.client.get(self.feed_url())
        self.assertContains(teacher_page, poll.post.body)

    def test_removed_poll_refuses_votes(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        remove_post(post=poll.post, removed_by=self.teacher_user)
        self.client.force_login(self.student1.user)

        self.client.post(
            self.vote_url(poll), {"option": poll.options.first().pk}
        )
        self.assertFalse(poll.votes.exists())


# ---------------------------------------------------------------------------
# Result visibility
# ---------------------------------------------------------------------------


class VisibilityTests(PollTestCase):
    def _vote(self, poll, student, index=0):
        poll_services.cast_vote(
            poll=poll,
            voter=student.user,
            option_id=list(poll.options.all())[index].pk,
        )

    def test_after_close_hides_results_while_open(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.AFTER_CLOSE,
            closes_at=timezone.now() + timedelta(hours=1),
        )
        self._vote(poll, self.student1)

        summary = build_poll_summary(poll, self.student1.user)
        self.assertFalse(summary.show_results)
        self.assertTrue(summary.results_hidden)
        self.assertEqual(summary.total_votes, 1)

    def test_after_close_shows_results_once_closed(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.AFTER_CLOSE,
            closes_at=timezone.now() + timedelta(hours=1),
        )
        self._vote(poll, self.student1)

        summary = build_poll_summary(
            poll,
            self.student2.user,
            now=timezone.now() + timedelta(hours=2),
        )
        self.assertTrue(summary.show_results)
        self.assertEqual(summary.total_votes, 1)

    def test_live_results_are_visible_while_open(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE,
            closes_at=timezone.now() + timedelta(hours=1),
        )
        self._vote(poll, self.student1)

        summary = build_poll_summary(poll, self.student2.user)
        self.assertTrue(summary.show_results)
        counts = {row.text: row.count for row in summary.options}
        self.assertEqual(counts["Blue"], 1)

    def test_teacher_always_sees_the_tally(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.AFTER_CLOSE,
            closes_at=timezone.now() + timedelta(hours=1),
        )
        self._vote(poll, self.student1)

        summary = build_poll_summary(poll, self.teacher_user)
        self.assertTrue(summary.show_results)
        self.assertTrue(summary.viewer_is_teacher)
        self.assertEqual(summary.total_votes, 1)
        self.assertEqual(summary.turnout, 2)

    def test_percentages_are_whole_numbers(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        self._vote(poll, self.student1, index=0)
        self._vote(poll, self.student2, index=1)

        summary = build_poll_summary(poll, self.teacher_user)
        self.assertEqual(
            {row.percent for row in summary.options}, {0, 50}
        )

    def test_student_feed_omits_counts_until_close(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.AFTER_CLOSE,
            closes_at=timezone.now() + timedelta(hours=1),
        )
        self._vote(poll, self.student2)
        self.client.force_login(self.student1.user)

        response = self.client.get(self.feed_url())

        self.assertContains(response, poll.post.body)
        self.assertNotContains(response, 'class="feed-poll__result-count"')
        self.assertContains(response, "Results will appear once the poll closes.")

    def test_student_feed_shows_bars_when_live(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE,
            closes_at=timezone.now() + timedelta(hours=1),
        )
        self._vote(poll, self.student2)
        self.client.force_login(self.student1.user)

        response = self.client.get(self.feed_url())

        self.assertContains(response, 'class="feed-poll__result-count"')
        self.assertContains(response, "50%")

    def test_parent_reads_but_cannot_vote(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        self.client.force_login(self.parent_user)

        response = self.client.get(self.feed_url())

        self.assertContains(response, "Parents can see the poll and its results")
        self.assertNotContains(response, 'class="feed-poll__submit"')
        self.assertNotContains(response, 'name="option"')


# ---------------------------------------------------------------------------
# Escaping and switch-off
# ---------------------------------------------------------------------------


class SafetyTests(PollTestCase):
    def test_question_and_options_are_escaped(self):
        poll = self.make_poll(
            question="<script>alert('question')</script>",
            options=["<b>Yes</b>", "No"],
            results_visibility=ClassPoll.ResultsVisibility.LIVE,
        )
        self.client.force_login(self.student1.user)

        response = self.client.get(self.feed_url())

        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert('question')</script>")
        self.assertNotContains(response, "<b>Yes</b>")
        self.assertEqual(poll.options.first().text, "<b>Yes</b>")

    def test_switching_polls_off_hides_them_and_blocks_votes(self):
        poll = self.make_poll(
            results_visibility=ClassPoll.ResultsVisibility.LIVE
        )
        option = poll.options.first()

        _set_switches(
            classes=(self.school_class, self.other_class), community=True, polls=False
        )
        self.assertFalse(poll_services.polls_enabled_for(self.school_class))

        self.client.force_login(self.student1.user)
        response = self.client.get(self.feed_url())
        self.assertNotContains(response, poll.post.body)

        with self.assertRaises(PermissionDenied):
            poll_services.cast_vote(
                poll=poll, voter=self.student1.user, option_id=option.pk
            )

        self.client.post(self.vote_url(poll), {"option": option.pk})
        self.assertFalse(poll.votes.exists())

        self.client.force_login(self.teacher_user)
        teacher_response = self.client.get(self.feed_url())
        self.assertContains(teacher_response, poll.post.body)

    def test_switching_polls_off_removes_the_creator_card(self):
        self.client.force_login(self.teacher_user)
        on_page = self.client.get(self.feed_url())
        self.assertContains(on_page, "Create a poll")

        _set_switches(
            classes=(self.school_class, self.other_class), community=True, polls=False
        )
        off_page = self.client.get(self.feed_url())
        self.assertNotContains(off_page, "Create a poll")
