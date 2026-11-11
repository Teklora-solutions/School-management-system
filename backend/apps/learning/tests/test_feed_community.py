"""
apps/learning/tests/test_feed_community.py
-------------------------------------------
F1 — the class community feed: replies, best answer, question status, filters,
pagination, polling counts, drafts' server-side prerequisites, audit entries,
and the switch pair (school-wide + per class) that keeps it off by default.

The original notice-board behaviour is covered by ``test_feed.py``; these
tests only exercise what F1 adds and the permission tightening it brings.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import ClassSubject
from apps.accounts.models import ParentProfile
from apps.core.models import AuditLog, SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.learning import feed_selectors
from apps.learning.feed_services import (
    create_post,
    create_reply,
    mark_best_answer,
    remove_post,
    remove_reply,
    restore_post,
    set_reaction,
    toggle_pin,
)
from apps.learning.models import (
    ClassEngagementSettings,
    ClassFeedSettings,
    ClassPost,
    ClassPostReply,
    ClassPostReaction,
    CourseModule,
    Lesson,
    PointLedger,
    Quiz,
)
from apps.students.models import StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_teacher(username: str, staff_id: str):
    user = make_user("teacher", username=username)
    profile = TeacherProfile.objects.create(user=user, staff_id=staff_id)
    return user, profile


def make_parent_for(student):
    parent_user = make_user("parent", username=f"parent_{student.admission_number}")
    profile = ParentProfile.objects.create(user=parent_user)
    StudentGuardian.objects.create(student=student, parent=profile, is_primary=True)
    return parent_user, profile


def assign_teacher(teacher_profile, school_class, subject, year):
    return TeacherAssignment.objects.create(
        teacher=teacher_profile,
        school_class=school_class,
        subject=subject,
        academic_year=year,
    )


def set_school_switches(*, community=True, engagement=False, learning=True):
    row = SchoolSettings.load()
    row.learning_enabled = learning
    row.feed_community_enabled = community
    row.engagement_enabled = engagement
    row.save(
        update_fields=[
            "learning_enabled",
            "feed_community_enabled",
            "engagement_enabled",
            "updated_at",
        ]
    )
    return row


def set_class_community(school_class, active=True):
    row, _ = ClassFeedSettings.objects.update_or_create(
        school_class=school_class, defaults={"is_active": active}
    )
    return row


class CommunityTestCase(TestCase):
    """Shared academic structure: one class, one teacher, one pupil, one parent."""

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 6", level_order=6)
        cls.subject = make_subject("SCI", "Science")
        ClassSubject.objects.create(
            school_class=cls.school_class, subject=cls.subject
        )

        cls.teacher_user, cls.teacher = make_teacher("feed_teacher", "FT-001")
        assign_teacher(cls.teacher, cls.school_class, cls.subject, cls.year)

        cls.student = make_student_user(cls.school_class, admission_number="CM-001")
        cls.parent_user, _ = make_parent_for(cls.student)

        set_school_switches(community=True)
        set_class_community(cls.school_class, True)

    def make_post(self, *, post_type=ClassPost.PostType.QUESTION, author=None, body="Help?"):
        return ClassPost.objects.create(
            school_class=self.school_class,
            author=author or self.student.user,
            post_type=post_type,
            body=body,
        )


# ---------------------------------------------------------------------------
# Switches and defaults
# ---------------------------------------------------------------------------


class SwitchTests(CommunityTestCase):
    def test_class_switch_defaults_to_off(self):
        other = make_class(self.year, name="Grade 7", level_order=7)
        self.assertFalse(
            ClassFeedSettings.objects.filter(school_class=other).exists()
        )
        self.assertFalse(feed_selectors.community_enabled_for(other))

    def test_both_switches_are_required(self):
        self.assertTrue(feed_selectors.community_enabled_for(self.school_class))

        set_school_switches(community=False)
        self.assertFalse(
            feed_selectors.community_enabled_for(self.school_class)
        )

        set_school_switches(community=True)
        set_class_community(self.school_class, False)
        self.assertFalse(
            feed_selectors.community_enabled_for(self.school_class)
        )


# ---------------------------------------------------------------------------
# Post types and links
# ---------------------------------------------------------------------------


class PostTypeTests(CommunityTestCase):
    def test_student_can_post_question_tip_and_celebration_when_on(self):
        for post_type in (
            ClassPost.PostType.QUESTION,
            ClassPost.PostType.TIP,
            ClassPost.PostType.CELEBRATION,
        ):
            with self.subTest(post_type=post_type):
                post = create_post(
                    author=self.student.user,
                    school_class=self.school_class,
                    body=f"A {post_type}",
                    post_type=post_type,
                )
                self.assertEqual(post.post_type, post_type)

    def test_student_cannot_post_a_teacher_type(self):
        with self.assertRaises(PermissionDenied):
            create_post(
                author=self.student.user,
                school_class=self.school_class,
                body="Announcement",
                post_type=ClassPost.PostType.ANNOUNCEMENT,
            )

    def test_community_post_types_need_the_switch(self):
        set_class_community(self.school_class, False)
        with self.assertRaises(PermissionDenied):
            create_post(
                author=self.student.user,
                school_class=self.school_class,
                body="I figured it out",
                post_type=ClassPost.PostType.TIP,
            )
        with self.assertRaises(PermissionDenied):
            create_post(
                author=self.teacher_user,
                school_class=self.school_class,
                body="Challenge!",
                post_type=ClassPost.PostType.CHALLENGE,
            )

    def test_student_question_still_works_when_community_is_off(self):
        set_class_community(self.school_class, False)
        post = create_post(
            author=self.student.user,
            school_class=self.school_class,
            body="Legacy question",
            post_type=ClassPost.PostType.QUESTION,
        )
        self.assertEqual(post.post_type, ClassPost.PostType.QUESTION)

    def test_teacher_can_post_new_types_when_on(self):
        for post_type in (
            ClassPost.PostType.CHALLENGE,
            ClassPost.PostType.DISCUSSION_PROMPT,
        ):
            with self.subTest(post_type=post_type):
                post = create_post(
                    author=self.teacher_user,
                    school_class=self.school_class,
                    body=f"A {post_type}",
                    post_type=post_type,
                )
                self.assertEqual(post.post_type, post_type)


class LessonQuizLinkTests(CommunityTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.module = CourseModule.objects.create(
            school_class=cls.school_class,
            subject=cls.subject,
            term=cls.term,
            title="Living things",
        )
        cls.lesson = Lesson.objects.create(
            module=cls.module, title="Cells", is_published=True
        )
        cls.quiz = Quiz.objects.create(
            lesson=cls.lesson, title="Cells quiz", is_published=True
        )

    def test_teacher_can_link_a_lesson_and_a_quiz(self):
        post = create_post(
            author=self.teacher_user,
            school_class=self.school_class,
            body="Read this before Friday",
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            lesson=self.lesson,
            quiz=self.quiz,
        )
        self.assertEqual(post.lesson, self.lesson)
        self.assertEqual(post.quiz, self.quiz)

        item = feed_selectors.post_detail(
            post_id=post.pk, viewer=self.student.user
        )
        self.assertIsNotNone(item.lesson_link)
        self.assertIsNotNone(item.quiz_link)
        self.assertIn(str(self.lesson.pk), item.lesson_link["url"])

    def test_link_to_another_class_is_rejected(self):
        other = make_class(self.year, name="Grade 8", level_order=8)
        other_module = CourseModule.objects.create(
            school_class=other,
            subject=self.subject,
            term=self.term,
            title="Other",
        )
        other_lesson = Lesson.objects.create(module=other_module, title="Elsewhere")
        with self.assertRaises(ValueError):
            create_post(
                author=self.teacher_user,
                school_class=self.school_class,
                body="Wrong class",
                lesson=other_lesson,
            )

    def test_links_need_the_community_switch(self):
        set_class_community(self.school_class, False)
        with self.assertRaises(PermissionDenied):
            create_post(
                author=self.teacher_user,
                school_class=self.school_class,
                body="Link while off",
                lesson=self.lesson,
            )


# ---------------------------------------------------------------------------
# Replies and best answer
# ---------------------------------------------------------------------------


class ReplyTests(CommunityTestCase):
    def test_student_and_teacher_can_reply(self):
        post = self.make_post()
        student_reply = create_reply(
            post=post, author=self.student.user, body="I need help too."
        )
        teacher_reply = create_reply(
            post=post, author=self.teacher_user, body="Start on page 12."
        )
        self.assertEqual(student_reply.post, post)
        self.assertEqual(teacher_reply.author, self.teacher_user)

    def test_parent_cannot_reply(self):
        post = self.make_post()
        with self.assertRaises(PermissionDenied):
            create_reply(post=post, author=self.parent_user, body="Hello")

    def test_cannot_reply_to_a_removed_post(self):
        post = self.make_post()
        remove_post(post=post, removed_by=self.teacher_user)
        with self.assertRaises(PermissionDenied):
            create_reply(post=post, author=self.student.user, body="Too late")

    def test_replies_need_the_community_switch(self):
        post = self.make_post()
        set_class_community(self.school_class, False)
        with self.assertRaises(PermissionDenied):
            create_reply(post=post, author=self.teacher_user, body="Off")

    def test_empty_reply_is_rejected(self):
        post = self.make_post()
        with self.assertRaises(ValueError):
            create_reply(post=post, author=self.student.user, body="   ")

    def test_replies_earn_no_points(self):
        set_school_switches(community=True, engagement=True)
        ClassEngagementSettings.objects.create(
            school_class=self.school_class, is_active=True
        )
        post = self.make_post()
        create_reply(post=post, author=self.student.user, body="A reply")
        self.assertEqual(PointLedger.objects.count(), 0)


class BestAnswerTests(CommunityTestCase):
    def test_question_author_can_mark_a_best_answer(self):
        post = self.make_post()
        reply = create_reply(
            post=post, author=self.teacher_user, body="Here is the answer."
        )
        mark_best_answer(reply=reply, actor=self.student.user)
        reply.refresh_from_db()
        self.assertTrue(reply.is_best_answer)

    def test_teacher_can_mark_a_best_answer(self):
        post = self.make_post(author=self.student.user)
        reply = create_reply(
            post=post, author=self.student.user, body="I think it is 4."
        )
        mark_best_answer(reply=reply, actor=self.teacher_user)
        reply.refresh_from_db()
        self.assertTrue(reply.is_best_answer)

    def test_another_student_cannot_mark_a_best_answer(self):
        post = self.make_post()
        reply = create_reply(
            post=post, author=self.teacher_user, body="The answer."
        )
        other = make_student_user(self.school_class, admission_number="CM-002")
        with self.assertRaises(PermissionDenied):
            mark_best_answer(reply=reply, actor=other.user)

    def test_marking_another_reply_replaces_the_first(self):
        post = self.make_post()
        first = create_reply(post=post, author=self.teacher_user, body="First")
        second = create_reply(post=post, author=self.teacher_user, body="Second")

        mark_best_answer(reply=first, actor=self.student.user)
        mark_best_answer(reply=second, actor=self.student.user)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_best_answer)
        self.assertTrue(second.is_best_answer)
        self.assertEqual(
            ClassPostReply.objects.filter(
                post=post, is_best_answer=True
            ).count(),
            1,
        )

    def test_non_question_cannot_have_a_best_answer(self):
        post = self.make_post(post_type=ClassPost.PostType.ANNOUNCEMENT)
        reply = create_reply(post=post, author=self.student.user, body="Thanks")
        with self.assertRaises(ValueError):
            mark_best_answer(reply=reply, actor=self.teacher_user)


class RemoveReplyTests(CommunityTestCase):
    def test_teacher_can_remove_a_reply_and_it_is_audited(self):
        post = self.make_post()
        reply = create_reply(
            post=post, author=self.student.user, body="Silly reply"
        )
        remove_reply(
            reply=reply,
            removed_by=self.teacher_user,
            reason="Not kind",
        )
        reply.refresh_from_db()
        self.assertTrue(reply.is_removed)
        self.assertIsNotNone(reply.removed_at)

        entry = AuditLog.objects.filter(
            action="feed.reply.removed",
            content_type="learning.ClassPostReply",
            object_id=str(reply.pk),
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.change_summary.get("reason"), "Not kind")

    def test_student_cannot_remove_a_reply(self):
        post = self.make_post()
        reply = create_reply(
            post=post, author=self.student.user, body="Mine"
        )
        with self.assertRaises(PermissionDenied):
            remove_reply(reply=reply, removed_by=self.student.user)

    def test_removing_the_best_answer_returns_the_question_to_waiting(self):
        post = self.make_post()
        only = create_reply(
            post=post, author=self.teacher_user, body="The only answer."
        )
        mark_best_answer(reply=only, actor=self.student.user)

        thread = feed_selectors.post_thread(
            post_id=post.pk, viewer=self.student.user
        )
        self.assertEqual(thread.question_state, "answered")
        self.assertEqual(thread.best_reply_id, only.pk)

        remove_reply(reply=only, removed_by=self.teacher_user)

        thread = feed_selectors.post_thread(
            post_id=post.pk, viewer=self.student.user
        )
        self.assertEqual(thread.question_state, "waiting")
        self.assertIsNone(thread.best_reply_id)

    def test_another_visible_reply_keeps_the_question_answered(self):
        post = self.make_post()
        best = create_reply(post=post, author=self.teacher_user, body="Best")
        other = create_reply(post=post, author=self.teacher_user, body="Also helps")
        mark_best_answer(reply=best, actor=self.student.user)

        remove_reply(reply=best, removed_by=self.teacher_user)

        thread = feed_selectors.post_thread(
            post_id=post.pk, viewer=self.student.user
        )
        self.assertEqual(thread.question_state, "answered")
        self.assertIsNone(thread.best_reply_id)
        self.assertIn(other.pk, [r.pk for r in thread.replies])


# ---------------------------------------------------------------------------
# Visibility and existing-action hardening
# ---------------------------------------------------------------------------


class VisibilityTests(CommunityTestCase):
    def test_removed_post_hides_its_replies_from_students_and_parents(self):
        post = self.make_post()
        create_reply(post=post, author=self.teacher_user, body="The answer")
        remove_post(post=post, removed_by=self.teacher_user, reason="Wrong topic")

        self.assertIsNone(
            feed_selectors.post_thread(post_id=post.pk, viewer=self.student.user)
        )
        self.assertIsNone(
            feed_selectors.post_thread(post_id=post.pk, viewer=self.parent_user)
        )
        teacher_thread = feed_selectors.post_thread(
            post_id=post.pk, viewer=self.teacher_user
        )
        self.assertIsNotNone(teacher_thread)

    def test_removed_reply_is_hidden_from_students_but_not_teachers(self):
        post = self.make_post()
        reply = create_reply(
            post=post, author=self.student.user, body="Gone"
        )
        remove_reply(reply=reply, removed_by=self.teacher_user)

        student_thread = feed_selectors.post_thread(
            post_id=post.pk, viewer=self.student.user
        )
        self.assertEqual(student_thread.replies, [])

        teacher_thread = feed_selectors.post_thread(
            post_id=post.pk, viewer=self.teacher_user
        )
        self.assertEqual([r.pk for r in teacher_thread.replies], [reply.pk])

    def test_post_removal_and_restore_are_audited(self):
        post = self.make_post()
        remove_post(post=post, removed_by=self.teacher_user, reason="Off topic")
        restore_post(post=post, restored_by=self.teacher_user)

        actions = set(
            AuditLog.objects.filter(
                content_type="learning.ClassPost", object_id=str(post.pk)
            ).values_list("action", flat=True)
        )
        self.assertIn("feed.post.removed", actions)
        self.assertIn("feed.post.restored", actions)


class ExistingActionPermissionTests(CommunityTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.outsider_class = make_class(cls.year, name="Grade 9", level_order=9)
        cls.outsider_user, cls.outsider = make_teacher("outsider", "OT-001")
        assign_teacher(
            cls.outsider, cls.outsider_class, cls.subject, cls.year
        )

    def test_teacher_outside_the_class_cannot_pin_or_remove(self):
        post = self.make_post()
        with self.assertRaises(PermissionDenied):
            toggle_pin(post=post, actor=self.outsider_user)
        with self.assertRaises(PermissionDenied):
            remove_post(post=post, removed_by=self.outsider_user)
        with self.assertRaises(PermissionDenied):
            restore_post(post=post, restored_by=self.outsider_user)

    def test_teacher_outside_the_class_cannot_react(self):
        post = self.make_post()
        with self.assertRaises(PermissionDenied):
            set_reaction(
                post=post,
                user=self.outsider_user,
                emoji=ClassPostReaction.Emoji.THUMBS_UP,
            )


# ---------------------------------------------------------------------------
# Feed page: filters, pinned strip, cursor pagination, poll count
# ---------------------------------------------------------------------------


class FeedPageTests(CommunityTestCase):
    def setUp(self):
        self.q1 = self.make_post(body="Question one")
        self.q2 = self.make_post(body="Question two")
        self.a1 = self.make_post(
            post_type=ClassPost.PostType.ANNOUNCEMENT, author=self.teacher_user, body="News"
        )
        self.a2 = self.make_post(
            post_type=ClassPost.PostType.TIP, author=self.teacher_user, body="Tip"
        )
        self.pinned = self.make_post(
            post_type=ClassPost.PostType.ANNOUNCEMENT, author=self.teacher_user, body="Pinned"
        )
        self.pinned.is_pinned = True
        self.pinned.save(update_fields=["is_pinned"])
        self.other_student = make_student_user(
            self.school_class, admission_number="CM-003"
        )
        self.mine = ClassPost.objects.create(
            school_class=self.school_class,
            author=self.other_student.user,
            post_type=ClassPost.PostType.QUESTION,
            body="Not mine",
        )

    def test_all_tab_separates_pinned_from_the_stream(self):
        page = feed_selectors.feed_page(
            school_class=self.school_class, viewer=self.student.user
        )
        self.assertEqual([p.post.pk for p in page.pinned], [self.pinned.pk])
        self.assertNotIn(self.pinned.pk, [p.post.pk for p in page.posts])

    def test_type_filters(self):
        questions = feed_selectors.feed_page(
            school_class=self.school_class,
            viewer=self.student.user,
            filter_key="questions",
        )
        self.assertTrue(all(p.post.post_type == "question" for p in questions.posts))
        self.assertEqual(questions.pinned, [])

        announcements = feed_selectors.feed_page(
            school_class=self.school_class,
            viewer=self.student.user,
            filter_key="announcements",
        )
        self.assertTrue(
            all(p.post.post_type == "announcement" for p in announcements.posts)
        )

        pinned = feed_selectors.feed_page(
            school_class=self.school_class,
            viewer=self.student.user,
            filter_key="pinned",
        )
        self.assertEqual([p.post.pk for p in pinned.posts], [self.pinned.pk])

    def test_mine_filter_is_the_viewers_own_posts(self):
        page = feed_selectors.feed_page(
            school_class=self.school_class,
            viewer=self.student.user,
            filter_key="mine",
        )
        self.assertTrue(page.posts)
        self.assertTrue(all(p.post.author_id == self.student.user_id for p in page.posts))

    def test_unanswered_queue_is_questions_without_a_visible_reply(self):
        reply = create_reply(post=self.q2, author=self.teacher_user, body="Answer")

        def queue_ids():
            page = feed_selectors.feed_page(
                school_class=self.school_class,
                viewer=self.teacher_user,
                filter_key="unanswered",
            )
            return {p.post.pk for p in page.posts}

        ids = queue_ids()
        self.assertIn(self.q1.pk, ids)
        self.assertIn(self.mine.pk, ids)  # another pupil's question, still open
        self.assertNotIn(self.q2.pk, ids)  # answered
        self.assertNotIn(self.a1.pk, ids)  # an announcement, not a question

        remove_reply(reply=reply, removed_by=self.teacher_user)
        self.assertIn(self.q2.pk, queue_ids())

    def test_unanswered_is_a_teacher_only_filter(self):
        page = feed_selectors.feed_page(
            school_class=self.school_class,
            viewer=self.student.user,
            filter_key="unanswered",
        )
        self.assertEqual(page.filter_key, "all")

    def test_cursor_pagination_never_skips_or_repeats(self):
        expected_ids = list(
            ClassPost.objects.filter(
                school_class=self.school_class, is_removed=False, is_pinned=False
            )
            .order_by("-id")
            .values_list("id", flat=True)
        )

        collected = []
        cursor = None
        while True:
            page = feed_selectors.feed_page(
                school_class=self.school_class,
                viewer=self.student.user,
                before_id=cursor,
                limit=2,
            )
            collected.extend(p.post.pk for p in page.posts)
            if not page.has_more:
                break
            cursor = page.next_before_id

        self.assertEqual(collected, expected_ids)
        self.assertEqual(len(collected), len(set(collected)))

    def test_poll_counts_only_newer_visible_posts(self):
        start = feed_selectors.latest_visible_post_id(
            school_class=self.school_class, viewer=self.student.user
        )
        fresh = self.make_post(body="Brand new")
        self.assertEqual(
            feed_selectors.new_posts_count(
                school_class=self.school_class,
                viewer=self.student.user,
                after_id=start,
            ),
            1,
        )
        remove_post(post=fresh, removed_by=self.teacher_user)
        self.assertEqual(
            feed_selectors.new_posts_count(
                school_class=self.school_class,
                viewer=self.student.user,
                after_id=start,
            ),
            0,
        )
        # A teacher still sees the removed post.
        self.assertEqual(
            feed_selectors.new_posts_count(
                school_class=self.school_class,
                viewer=self.teacher_user,
                after_id=start,
            ),
            1,
        )

    def test_poll_filter_matches_the_current_tab(self):
        start = feed_selectors.latest_visible_post_id(
            school_class=self.school_class, viewer=self.student.user
        )
        self.make_post(
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            author=self.teacher_user,
            body="Not a question",
        )
        self.assertEqual(
            feed_selectors.new_posts_count(
                school_class=self.school_class,
                viewer=self.student.user,
                after_id=start,
                filter_key="questions",
            ),
            0,
        )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class CommunityViewTests(CommunityTestCase):
    def setUp(self):
        set_school_switches(community=True)
        set_class_community(self.school_class, True)

    def feed_url(self):
        return reverse("learning:class_feed", args=[self.school_class.pk])

    def test_feed_renders_community_layout(self):
        self.make_post(body="What is a cell?")
        self.client.force_login(self.student.user)
        response = self.client.get(self.feed_url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["community_on"])
        self.assertContains(response, "Waiting for an answer")
        self.assertContains(response, "feedCommunity")
        self.assertContains(response, "Show more")

    def test_teacher_feed_offers_all_six_post_types(self):
        module = CourseModule.objects.create(
            school_class=self.school_class,
            subject=self.subject,
            term=self.term,
            title="Living things",
        )
        lesson = Lesson.objects.create(
            module=module, title="Cells", is_published=True
        )
        Quiz.objects.create(lesson=lesson, title="Cells quiz", is_published=True)
        self.client.force_login(self.teacher_user)
        response = self.client.get(self.feed_url())
        self.assertEqual(response.status_code, 200)
        for label in (
            "Announcement",
            "Question",
            "Tip",
            "Celebration",
            "Challenge",
            "Discussion prompt",
        ):
            with self.subTest(label=label):
                self.assertContains(response, label)
        self.assertContains(response, "Link a lesson")

    def test_parent_feed_is_read_only(self):
        self.make_post(body="What is a cell?")
        self.client.force_login(self.parent_user)
        response = self.client.get(self.feed_url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_post"])
        self.assertContains(response, "Parents can read but cannot post or reply.")
        self.assertNotContains(response, "Add a post")

    def test_unanswered_chip_is_shown_to_teachers_only(self):
        self.client.force_login(self.teacher_user)
        self.assertContains(self.client.get(self.feed_url()), "filter=unanswered")
        self.client.force_login(self.student.user)
        self.assertNotContains(self.client.get(self.feed_url()), "filter=unanswered")

    def test_teacher_can_reach_settings_while_the_feed_is_off(self):
        set_class_community(self.school_class, False)
        self.client.force_login(self.teacher_user)
        response = self.client.get(self.feed_url())
        self.assertFalse(response.context["community_on"])
        self.assertContains(
            response,
            reverse("learning:feed_settings", args=[self.school_class.pk]),
        )

    def test_feed_home_goes_straight_to_the_single_class(self):
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("learning:feed_home"))
        self.assertRedirects(
            response, self.feed_url(), fetch_redirect_response=False
        )

    def test_feed_home_shows_a_chooser_for_several_classes(self):
        second = make_class(self.year, name="Grade 7", level_order=7)
        assign_teacher(self.teacher, second, self.subject, self.year)
        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse("learning:feed_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.school_class.name)
        self.assertContains(response, second.name)

    def test_thread_page_visible_to_student_and_parent(self):
        post = self.make_post()
        create_reply(post=post, author=self.teacher_user, body="An answer")
        url = reverse(
            "learning:feed_post", args=[self.school_class.pk, post.pk]
        )
        for user in (self.student.user, self.parent_user):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
        self.client.force_login(self.parent_user)
        self.assertContains(
            self.client.get(url), "Parents can read the class feed"
        )

    def test_thread_page_is_404_for_another_class_student(self):
        other_class = make_class(self.year, name="Grade 10", level_order=10)
        outsider = make_student_user(other_class, admission_number="CM-999")
        post = self.make_post()
        self.client.force_login(outsider.user)
        response = self.client.get(
            reverse("learning:feed_post", args=[self.school_class.pk, post.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_student_can_post_a_reply_through_the_view(self):
        post = self.make_post()
        self.client.force_login(self.student.user)
        response = self.client.post(
            reverse("learning:feed_reply_create", args=[self.school_class.pk, post.pk]),
            {"body": "Here is my attempt."},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ClassPostReply.objects.filter(post=post, author=self.student.user).exists()
        )

    def test_parent_reply_is_rejected_by_the_view(self):
        post = self.make_post()
        self.client.force_login(self.parent_user)
        self.client.post(
            reverse("learning:feed_reply_create", args=[self.school_class.pk, post.pk]),
            {"body": "Hello"},
        )
        self.assertFalse(ClassPostReply.objects.filter(post=post).exists())

    def test_question_author_marks_best_answer_through_the_view(self):
        post = self.make_post()
        reply = create_reply(
            post=post, author=self.teacher_user, body="The answer"
        )
        self.client.force_login(self.student.user)
        self.client.post(
            reverse(
                "learning:feed_reply_action",
                args=[self.school_class.pk, reply.pk],
            ),
            {"action": "best"},
        )
        reply.refresh_from_db()
        self.assertTrue(reply.is_best_answer)

    def test_teacher_removes_a_reply_through_the_view(self):
        post = self.make_post()
        reply = create_reply(
            post=post, author=self.student.user, body="Unkind"
        )
        self.client.force_login(self.teacher_user)
        self.client.post(
            reverse(
                "learning:feed_reply_action",
                args=[self.school_class.pk, reply.pk],
            ),
            {"action": "remove", "reason": "Not kind"},
        )
        reply.refresh_from_db()
        self.assertTrue(reply.is_removed)

    def test_items_fragment_carries_the_next_cursor(self):
        for i in range(feed_selectors.PAGE_SIZE + 1):
            self.make_post(body=f"Post {i}")
        self.client.force_login(self.student.user)
        response = self.client.get(
            reverse("learning:feed_items", args=[self.school_class.pk]),
            {"filter": "all"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-feed-page")
        self.assertContains(response, 'data-has-more="1"')

    def test_poll_endpoint_returns_a_count(self):
        self.make_post(body="Already here")
        start = feed_selectors.latest_visible_post_id(
            school_class=self.school_class, viewer=self.student.user
        )
        self.make_post(body="New question")
        self.client.force_login(self.student.user)
        response = self.client.get(
            reverse("learning:feed_poll", args=[self.school_class.pk]),
            {"after": start, "filter": "all"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)

    def test_teacher_can_switch_the_class_feed_on_and_off(self):
        set_class_community(self.school_class, False)
        self.client.force_login(self.teacher_user)
        url = reverse("learning:feed_settings", args=[self.school_class.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Points are off for this class")

        self.client.post(url, {"is_active": "1"})
        self.assertTrue(
            ClassFeedSettings.objects.get(school_class=self.school_class).is_active
        )

    def test_feed_settings_is_404_for_a_teacher_outside_the_class(self):
        other_class = make_class(self.year, name="Grade 11", level_order=11)
        outsider, profile = make_teacher("settings_outsider", "SO-001")
        assign_teacher(profile, other_class, self.subject, self.year)
        self.client.force_login(outsider)
        response = self.client.get(
            reverse("learning:feed_settings", args=[self.school_class.pk])
        )
        self.assertEqual(response.status_code, 404)
