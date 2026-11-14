"""
apps/learning/tests/test_feed_c1.py
-----------------------------------
C1 — the rich feed checkpoint:

  * scheduled posts (teachers only, invisible to pupils until due),
  * author-only editing with an "Edited" stamp and an audit entry,
  * @mentions resolved against the class roster, never free text,
  * request-level search,
  * link cards that never fetch the destination,
  * the cross-class thread fix, and
  * audit entries for pin, best answer and settings.

Times are checked in Nairobi time, matching the rest of the project.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog, Notification
from apps.learning import feed_selectors
from apps.learning.feed_services import (
    create_post,
    create_reply,
    edit_post,
    mark_best_answer,
    toggle_pin,
)
from apps.learning.models import ClassPost
from apps.learning.templatetags.feed_tags import (
    feed_exact,
    feed_iso,
    feed_relative,
    feed_schedule_label,
)

from apps.dashboards.tests.fixtures import make_class, make_student_user

from .test_feed_community import CommunityTestCase, assign_teacher


class C1Base(CommunityTestCase):
    """Shared fixtures plus a couple of C1 helpers."""

    def make_class_post(self, *, author=None, body="A post", post_type=None):
        return create_post(
            author=author or self.teacher_user,
            school_class=self.school_class,
            body=body,
            post_type=post_type or ClassPost.PostType.ANNOUNCEMENT,
        )

    def feed_url(self):
        return reverse("learning:class_feed", args=[self.school_class.pk])

    def thread_url(self, post):
        return reverse("learning:feed_post", args=[self.school_class.pk, post.pk])


# ---------------------------------------------------------------------------
# Scheduled posts
# ---------------------------------------------------------------------------


class ScheduledPostTests(C1Base):
    def test_teacher_schedules_a_future_post(self):
        due = timezone.now() + timedelta(days=1)
        post = create_post(
            author=self.teacher_user,
            school_class=self.school_class,
            body="Revision starts tomorrow",
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            scheduled_at=due,
        )
        self.assertEqual(post.scheduled_at, due)
        self.assertTrue(post.is_scheduled)

    def test_students_do_not_see_a_scheduled_post_yet(self):
        due = timezone.now() + timedelta(days=1)
        post = create_post(
            author=self.teacher_user,
            school_class=self.school_class,
            body="Secret until tomorrow",
            scheduled_at=due,
        )
        page = feed_selectors.feed_page(
            school_class=self.school_class, viewer=self.student.user
        )
        ids = [item.post.pk for item in page.posts]
        self.assertNotIn(post.pk, ids)
        self.assertEqual(
            feed_selectors.latest_visible_post_id(
                school_class=self.school_class, viewer=self.student.user
            ),
            0,
        )

    def test_teacher_sees_the_scheduled_post_with_a_label(self):
        due = timezone.now() + timedelta(days=1)
        post = create_post(
            author=self.teacher_user,
            school_class=self.school_class,
            body="Revision starts tomorrow",
            scheduled_at=due,
        )
        page = feed_selectors.feed_page(
            school_class=self.school_class, viewer=self.teacher_user
        )
        self.assertIn(post.pk, [item.post.pk for item in page.posts])
        self.assertTrue(page.posts[0].is_scheduled)

        self.client.force_login(self.teacher_user)
        response = self.client.get(self.feed_url())
        self.assertContains(response, "Revision starts tomorrow")
        self.assertContains(response, "Scheduled for")

    def test_a_due_post_becomes_visible_to_students(self):
        post = create_post(
            author=self.teacher_user,
            school_class=self.school_class,
            body="Now visible",
            scheduled_at=timezone.now() + timedelta(days=1),
        )
        ClassPost.objects.filter(pk=post.pk).update(
            scheduled_at=timezone.now() - timedelta(minutes=1)
        )
        post.refresh_from_db()
        self.assertFalse(post.is_scheduled)
        page = feed_selectors.feed_page(
            school_class=self.school_class, viewer=self.student.user
        )
        self.assertIn(post.pk, [item.post.pk for item in page.posts])

    def test_only_teachers_may_schedule(self):
        with self.assertRaises(PermissionDenied):
            create_post(
                author=self.student.user,
                school_class=self.school_class,
                body="Pupil tries to schedule",
                post_type=ClassPost.PostType.QUESTION,
                scheduled_at=timezone.now() + timedelta(days=1),
            )

    def test_a_past_schedule_time_is_refused(self):
        with self.assertRaises(ValueError):
            create_post(
                author=self.teacher_user,
                school_class=self.school_class,
                body="Too late",
                scheduled_at=timezone.now() - timedelta(minutes=5),
            )

    def test_a_scheduled_post_cannot_be_replied_to_yet(self):
        post = create_post(
            author=self.teacher_user,
            school_class=self.school_class,
            body="Wait for it",
            post_type=ClassPost.PostType.QUESTION,
            scheduled_at=timezone.now() + timedelta(days=1),
        )
        self.assertFalse(
            feed_selectors.can_reply(self.student.user, post, self.school_class)
        )
        self.client.force_login(self.student.user)
        self.client.post(
            reverse(
                "learning:feed_reply_create",
                args=[self.school_class.pk, post.pk],
            ),
            {"body": "Too early"},
        )
        self.assertFalse(post.replies.exists())

    def test_scheduled_post_sends_no_mention_notification_yet(self):
        create_post(
            author=self.teacher_user,
            school_class=self.school_class,
            body="Hi @Test Student",
            scheduled_at=timezone.now() + timedelta(days=1),
        )
        self.assertFalse(
            Notification.objects.filter(
                recipient=self.student.user, category="feed_mention"
            ).exists()
        )


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


class EditPostTests(C1Base):
    def test_author_can_edit_and_the_post_is_stamped(self):
        post = self.make_class_post(author=self.teacher_user, body="Old words")
        edit_post(post=post, actor=self.teacher_user, body="New words")
        post.refresh_from_db()
        self.assertEqual(post.body, "New words")
        self.assertIsNotNone(post.edited_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="feed.post.edited",
                content_type="learning.ClassPost",
                object_id=str(post.pk),
            ).exists()
        )

    def test_someone_else_cannot_edit(self):
        post = self.make_class_post(author=self.teacher_user, body="Mine")
        with self.assertRaises(PermissionDenied):
            edit_post(post=post, actor=self.student.user, body="Not mine")

    def test_a_removed_post_cannot_be_edited(self):
        post = self.make_class_post(author=self.teacher_user, body="Gone")
        post.is_removed = True
        post.save(update_fields=["is_removed"])
        with self.assertRaises(PermissionDenied):
            edit_post(post=post, actor=self.teacher_user, body="Back")

    def test_an_unchanged_body_does_not_stamp_edited(self):
        post = self.make_class_post(author=self.teacher_user, body="Same")
        edit_post(post=post, actor=self.teacher_user, body="Same")
        post.refresh_from_db()
        self.assertIsNone(post.edited_at)

    def test_feed_shows_the_edited_label_and_the_new_words(self):
        post = self.make_class_post(
            author=self.student.user,
            body="Before",
            post_type=ClassPost.PostType.QUESTION,
        )
        edit_post(post=post, actor=self.student.user, body="After")
        self.client.force_login(self.student.user)
        response = self.client.get(self.feed_url())
        self.assertContains(response, "After")
        self.assertNotContains(response, "Before")
        self.assertContains(response, "Edited")

    def test_edit_view_redirects_the_author(self):
        post = self.make_class_post(
            author=self.student.user,
            body="Typo",
            post_type=ClassPost.PostType.QUESTION,
        )
        self.client.force_login(self.student.user)
        response = self.client.post(
            reverse("learning:feed_post_edit", args=[self.school_class.pk, post.pk]),
            {"body": "Fixed"},
        )
        self.assertEqual(response.status_code, 302)
        post.refresh_from_db()
        self.assertEqual(post.body, "Fixed")

    def test_edit_view_rejects_another_author(self):
        post = self.make_class_post(author=self.teacher_user, body="Mine")
        self.client.force_login(self.student.user)
        self.client.post(
            reverse("learning:feed_post_edit", args=[self.school_class.pk, post.pk]),
            {"body": "Hijacked"},
        )
        post.refresh_from_db()
        self.assertEqual(post.body, "Mine")

    def test_edit_view_refuses_get(self):
        post = self.make_class_post(
            author=self.student.user,
            body="Hi",
            post_type=ClassPost.PostType.QUESTION,
        )
        self.client.force_login(self.student.user)
        response = self.client.get(
            reverse("learning:feed_post_edit", args=[self.school_class.pk, post.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_body_text_is_escaped(self):
        post = self.make_class_post(author=self.teacher_user, body="Safe")
        edit_post(
            post=post,
            actor=self.teacher_user,
            body="<script>alert(1)</script>",
        )
        self.client.force_login(self.student.user)
        response = self.client.get(self.feed_url())
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")


# ---------------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------------


class MentionTests(C1Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.amina = make_student_user(
            cls.school_class, admission_number="CM-AMINA"
        )
        cls.amina.user.first_name = "Amina"
        cls.amina.user.last_name = "Wanjiru"
        cls.amina.user.save(update_fields=["first_name", "last_name"])

    def test_full_name_is_resolved(self):
        post = self.make_class_post(body="Well done @Amina Wanjiru.")
        self.assertEqual(
            post.mentions, [{"id": self.amina.user.pk, "name": "Amina Wanjiru"}]
        )

    def test_unique_first_name_is_resolved(self):
        post = self.make_class_post(body="Thanks @Amina!")
        self.assertEqual([row["id"] for row in post.mentions], [self.amina.user.pk])

    def test_an_ambiguous_first_name_stays_plain_text(self):
        # Two people in the roster are called "Test" (teacher and pupil).
        post = self.make_class_post(body="Hello @Test")
        self.assertEqual(post.mentions, [])

    def test_unknown_names_and_everyone_are_ignored(self):
        post = self.make_class_post(body="@everyone @Nobody Here hi")
        self.assertEqual(post.mentions, [])

    def test_a_shared_full_name_is_not_guessed(self):
        twin = make_student_user(self.school_class, admission_number="CM-AMINA2")
        twin.user.first_name = "Amina"
        twin.user.last_name = "Wanjiru"
        twin.user.save(update_fields=["first_name", "last_name"])
        post = self.make_class_post(body="Hello @Amina Wanjiru")
        self.assertEqual(post.mentions, [])

    def test_duplicates_are_collapsed(self):
        post = self.make_class_post(
            body="@Amina Wanjiru and again @Amina Wanjiru"
        )
        self.assertEqual(len(post.mentions), 1)

    def test_mentioned_person_is_notified_once(self):
        self.make_class_post(body="@Amina Wanjiru please read this")
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.amina.user, category="feed_mention"
            ).count(),
            1,
        )

    def test_the_author_is_never_notified_of_their_own_mention(self):
        create_post(
            author=self.amina.user,
            school_class=self.school_class,
            body="Hello @Amina Wanjiru, me",
            post_type=ClassPost.PostType.QUESTION,
        )
        self.assertFalse(
            Notification.objects.filter(
                recipient=self.amina.user, category="feed_mention"
            ).exists()
        )

    def test_mention_is_rendered_as_a_marker_not_html(self):
        self.make_class_post(body="Hello @Amina Wanjiru")
        self.client.force_login(self.student.user)
        response = self.client.get(self.feed_url())
        self.assertContains(
            response, '<span class="feed-mention">@Amina Wanjiru</span>'
        )

    def test_editing_notifies_only_the_newly_mentioned_person(self):
        post = self.make_class_post(body="Hello class")
        edit_post(
            post=post, actor=self.teacher_user, body="Hello @Amina Wanjiru"
        )
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.amina.user, category="feed_mention"
            ).count(),
            1,
        )

    def test_reply_mentions_are_resolved_and_notified(self):
        post = self.make_class_post(body="Question", post_type=ClassPost.PostType.QUESTION)
        reply = create_reply(
            post=post, author=self.student.user, body="Thanks @Test Teacher"
        )
        self.assertEqual(
            [row["id"] for row in reply.mentions], [self.teacher_user.pk]
        )
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.teacher_user, category="feed_mention"
            ).exists()
        )

    def test_composer_offers_the_class_roster(self):
        self.client.force_login(self.teacher_user)
        response = self.client.get(self.feed_url())
        self.assertContains(response, "data-feed-mention")
        self.assertContains(response, "Amina Wanjiru")
        self.assertContains(response, "@Amina Wanjiru")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchTests(C1Base):
    def setUp(self):
        self.make_class_post(body="Photosynthesis in plants")
        self.make_class_post(body="Volcanoes and rocks")

    def test_search_matches_the_body(self):
        self.client.force_login(self.student.user)
        response = self.client.get(self.feed_url(), {"q": "photo"})
        self.assertContains(response, "Photosynthesis in plants")
        self.assertNotContains(response, "Volcanoes and rocks")

    def test_search_matches_the_author_name(self):
        post = self.make_class_post(body="Nothing to see")
        post.author.first_name = "Zawadi"
        post.author.save(update_fields=["first_name"])
        self.client.force_login(self.student.user)
        response = self.client.get(self.feed_url(), {"q": "Zawadi"})
        self.assertContains(response, "Nothing to see")

    def test_a_search_with_no_hits_is_honest(self):
        self.client.force_login(self.student.user)
        response = self.client.get(self.feed_url(), {"q": "zzzz"})
        self.assertContains(response, "No posts match")

    def test_the_items_fragment_keeps_the_search(self):
        self.client.force_login(self.student.user)
        response = self.client.get(
            reverse("learning:feed_items", args=[self.school_class.pk]),
            {"filter": "all", "q": "photo"},
        )
        self.assertContains(response, "Photosynthesis in plants")
        self.assertNotContains(response, "Volcanoes and rocks")


# ---------------------------------------------------------------------------
# Link cards, duplicate, cross-class and audit
# ---------------------------------------------------------------------------


class LinkAndDuplicateTests(C1Base):
    def test_teacher_sees_a_link_card_without_a_preview(self):
        body = "Look at https://example.com/cells for the diagram."
        self.make_class_post(body=body)
        self.client.force_login(self.teacher_user)
        response = self.client.get(self.feed_url())
        self.assertContains(response, "feed-linkcard")
        self.assertContains(response, "example.com")
        self.assertNotContains(response, "<img")

    def test_pupils_do_not_get_a_clickable_link_card(self):
        self.make_class_post(body="Look at https://example.com/cells")
        self.client.force_login(self.student.user)
        response = self.client.get(self.feed_url())
        self.assertNotContains(response, "feed-linkcard")

    def test_duplicate_prefills_the_composer_without_saving(self):
        post = self.make_class_post(body="Original words")
        self.client.force_login(self.student.user)
        response = self.client.get(self.feed_url(), {"copy": post.pk})
        self.assertContains(response, "Original words")
        self.assertContains(response, "Copied from")
        self.assertEqual(ClassPost.objects.count(), 1)

    def test_duplicate_is_not_offered_to_parents(self):
        post = self.make_class_post(body="Original words")
        self.client.force_login(self.parent_user)
        response = self.client.get(self.feed_url(), {"copy": post.pk})
        self.assertNotContains(response, "Copied from")


class CrossClassThreadTests(C1Base):
    def test_a_post_from_another_class_is_not_reachable_under_this_class(self):
        other_class = make_class(self.year, name="Grade 8", level_order=8)
        assign_teacher(self.teacher, other_class, self.subject, self.year)
        other_post = create_post(
            author=self.teacher_user,
            school_class=other_class,
            body="Belongs elsewhere",
        )
        self.client.force_login(self.teacher_user)
        response = self.client.get(
            reverse(
                "learning:feed_post",
                args=[self.school_class.pk, other_post.pk],
            )
        )
        self.assertEqual(response.status_code, 404)


class AuditTests(C1Base):
    def test_pin_and_unpin_are_audited(self):
        post = self.make_class_post()
        toggle_pin(post=post, actor=self.teacher_user)
        toggle_pin(post=post, actor=self.teacher_user)
        actions = set(
            AuditLog.objects.filter(
                content_type="learning.ClassPost", object_id=str(post.pk)
            ).values_list("action", flat=True)
        )
        self.assertIn("feed.post.pinned", actions)
        self.assertIn("feed.post.unpinned", actions)

    def test_best_answer_is_audited_once(self):
        post = self.make_class_post(
            author=self.student.user,
            body="Help?",
            post_type=ClassPost.PostType.QUESTION,
        )
        reply = create_reply(
            post=post, author=self.teacher_user, body="Here is the answer"
        )
        mark_best_answer(reply=reply, actor=self.student.user)
        mark_best_answer(reply=reply, actor=self.student.user)
        self.assertEqual(
            AuditLog.objects.filter(
                action="feed.reply.best_answer",
                content_type="learning.ClassPostReply",
                object_id=str(reply.pk),
            ).count(),
            1,
        )

    def test_settings_change_is_audited(self):
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            reverse("learning:feed_settings", args=[self.school_class.pk]),
            {"is_active": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AuditLog.objects.filter(action="feed.settings.updated").exists()
        )


# ---------------------------------------------------------------------------
# Template time helpers
# ---------------------------------------------------------------------------


class FeedTimeFilterTests(TestCase):
    def test_relative_ages(self):
        now = timezone.now()
        self.assertEqual(feed_relative(now - timedelta(seconds=20)), "Just now")
        self.assertEqual(feed_relative(now - timedelta(minutes=5)), "5m ago")
        self.assertEqual(feed_relative(now - timedelta(hours=2, minutes=5)), "2h ago")
        self.assertEqual(
            feed_relative(now - timedelta(days=1, minutes=1)), "Yesterday"
        )
        self.assertEqual(
            feed_relative(now - timedelta(days=3)), "3d ago"
        )
        self.assertEqual(
            feed_relative(now + timedelta(days=1)), "Scheduled"
        )

    def test_exact_time_is_nairobi_time(self):
        value = timezone.make_aware(
            datetime(2026, 11, 14, 8, 0),
            timezone.get_current_timezone(),
        )
        exact = feed_exact(value)
        self.assertIn("14 Nov 2026", exact)
        self.assertIn("8:00", exact)
        self.assertIn("+03:00", feed_iso(value))

    def test_schedule_label_mentions_the_time(self):
        future = timezone.now() + timedelta(days=3)
        self.assertTrue(feed_schedule_label(future).startswith("Scheduled for"))
