"""
apps/learning/tests/test_feed.py
----------------------------------
Tests for Piece 2: class community feed.

Coverage
--------
  A. Models — ClassPost defaults, soft-delete, ClassPostReaction uniqueness
  B. feed_services — create_post (role guards, type guards, class membership),
                     remove_post, restore_post, toggle_pin, set_reaction
  C. feed_selectors — feed_for_classroom visibility (removed posts),
                      can_post, can_view_feed, is_moderator, post_detail
  D. Views — ClassFeedView (GET renders, POST creates),
             ClassFeedPostActionView (remove, pin, react),
             access control (roles that cannot see / cannot post)
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

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
from apps.learning.feed_selectors import (
    can_post,
    can_view_feed,
    feed_for_classroom,
    is_moderator,
    post_detail,
)
from apps.learning.feed_services import (
    create_post,
    remove_post,
    restore_post,
    set_reaction,
    toggle_pin,
)
from apps.learning.models import ClassPost, ClassPostReaction
from apps.students.models import StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def enable_learning():
    row = SchoolSettings.load()
    row.learning_enabled = True
    row.save(update_fields=["learning_enabled", "updated_at"])


def make_teacher(username, staff_id):
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


# ============================================================================
# A. Models
# ============================================================================


class ClassPostModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        cls.teacher_user = make_user("teacher", username="model_teacher")
        TeacherProfile.objects.create(user=cls.teacher_user, staff_id="MT-001")

    def test_default_is_not_pinned_not_removed(self):
        post = ClassPost.objects.create(
            school_class=self.school_class,
            author=self.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="Hello class!",
        )
        self.assertFalse(post.is_pinned)
        self.assertFalse(post.is_removed)
        self.assertIsNone(post.removed_by)
        self.assertIsNone(post.removed_at)

    def test_str_representation(self):
        post = ClassPost.objects.create(
            school_class=self.school_class,
            author=self.teacher_user,
            post_type=ClassPost.PostType.QUESTION,
            body="What is 2+2?",
        )
        self.assertIn("Question", str(post))
        self.assertIn("What is 2+2?", str(post))

    def test_reaction_unique_per_user_per_post(self):
        post = ClassPost.objects.create(
            school_class=self.school_class,
            author=self.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="Test",
        )
        ClassPostReaction.objects.create(
            post=post, user=self.teacher_user, emoji=ClassPostReaction.Emoji.THUMBS_UP
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            ClassPostReaction.objects.create(
                post=post,
                user=self.teacher_user,
                emoji=ClassPostReaction.Emoji.HEART,
            )

    def test_student_allowed_types_are_question_and_community_types(self):
        self.assertEqual(
            ClassPost.STUDENT_ALLOWED_TYPES,
            {
                ClassPost.PostType.QUESTION,
                ClassPost.PostType.TIP,
                ClassPost.PostType.CELEBRATION,
            },
        )


# ============================================================================
# B. feed_services
# ============================================================================


class CreatePostServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 6", level_order=6)
        cls.subject = make_subject("ENG", "English")
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.subject)

        cls.teacher_user, cls.teacher = make_teacher("feed_teacher", "FT-001")
        assign_teacher(cls.teacher, cls.school_class, cls.subject, cls.year)

        cls.student = make_student_user(cls.school_class, admission_number="FD-001")
        cls.other_student = make_student_user(
            make_class(cls.year, name="Grade 7", level_order=7),
            admission_number="FD-002",
        )

    # ── Teachers can post any type ──────────────────────────────────────────

    def test_teacher_can_post_announcement(self):
        post = create_post(
            author=self.teacher_user,
            school_class=self.school_class,
            body="Welcome to Term 1!",
            post_type=ClassPost.PostType.ANNOUNCEMENT,
        )
        self.assertEqual(post.post_type, ClassPost.PostType.ANNOUNCEMENT)
        self.assertEqual(post.school_class, self.school_class)
        self.assertEqual(post.author, self.teacher_user)

    def test_teacher_can_post_celebration(self):
        post = create_post(
            author=self.teacher_user,
            school_class=self.school_class,
            body="Great work everyone!",
            post_type=ClassPost.PostType.CELEBRATION,
        )
        self.assertEqual(post.post_type, ClassPost.PostType.CELEBRATION)

    def test_teacher_can_post_tip(self):
        post = create_post(
            author=self.teacher_user,
            school_class=self.school_class,
            body="Remember to read chapter 3.",
            post_type=ClassPost.PostType.TIP,
        )
        self.assertEqual(post.post_type, ClassPost.PostType.TIP)

    # ── Students can only post questions ────────────────────────────────────

    def test_student_can_post_question(self):
        post = create_post(
            author=self.student.user,
            school_class=self.school_class,
            body="What does photosynthesis mean?",
            post_type=ClassPost.PostType.QUESTION,
        )
        self.assertEqual(post.post_type, ClassPost.PostType.QUESTION)

    def test_student_cannot_post_announcement(self):
        with self.assertRaises(PermissionDenied):
            create_post(
                author=self.student.user,
                school_class=self.school_class,
                body="I am making an announcement.",
                post_type=ClassPost.PostType.ANNOUNCEMENT,
            )

    def test_student_cannot_post_celebration(self):
        with self.assertRaises(PermissionDenied):
            create_post(
                author=self.student.user,
                school_class=self.school_class,
                body="Party!",
                post_type=ClassPost.PostType.CELEBRATION,
            )

    # ── Class membership guards ─────────────────────────────────────────────

    def test_student_from_other_class_cannot_post(self):
        with self.assertRaises(PermissionDenied):
            create_post(
                author=self.other_student.user,
                school_class=self.school_class,
                body="Sneaking in.",
                post_type=ClassPost.PostType.QUESTION,
            )

    def test_parent_cannot_post(self):
        parent_user, _ = make_parent_for(self.student)
        with self.assertRaises(PermissionDenied):
            create_post(
                author=parent_user,
                school_class=self.school_class,
                body="Just a parent posting.",
                post_type=ClassPost.PostType.QUESTION,
            )

    # ── Input validation ────────────────────────────────────────────────────

    def test_empty_body_raises_value_error(self):
        with self.assertRaises(ValueError):
            create_post(
                author=self.teacher_user,
                school_class=self.school_class,
                body="   ",
                post_type=ClassPost.PostType.ANNOUNCEMENT,
            )

    def test_body_over_2000_chars_raises_value_error(self):
        with self.assertRaises(ValueError):
            create_post(
                author=self.teacher_user,
                school_class=self.school_class,
                body="x" * 2001,
                post_type=ClassPost.PostType.ANNOUNCEMENT,
            )


class RemoveRestoreTogglePinTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        cls.teacher_user, cls.teacher = make_teacher("mod_teacher", "MT-002")
        cls.subject = make_subject("SCI", "Science")
        assign_teacher(cls.teacher, cls.school_class, cls.subject, cls.year)
        cls.student = make_student_user(cls.school_class, admission_number="MD-001")
        cls.post = ClassPost.objects.create(
            school_class=cls.school_class,
            author=cls.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="Original post.",
        )

    def test_teacher_can_remove_post(self):
        post = ClassPost.objects.create(
            school_class=self.school_class,
            author=self.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="To be removed.",
        )
        remove_post(post=post, removed_by=self.teacher_user)
        post.refresh_from_db()
        self.assertTrue(post.is_removed)
        self.assertEqual(post.removed_by, self.teacher_user)
        self.assertIsNotNone(post.removed_at)

    def test_teacher_can_restore_post(self):
        post = ClassPost.objects.create(
            school_class=self.school_class,
            author=self.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="To be restored.",
            is_removed=True,
        )
        restore_post(post=post, restored_by=self.teacher_user)
        post.refresh_from_db()
        self.assertFalse(post.is_removed)
        self.assertIsNone(post.removed_by)

    def test_student_cannot_remove_post(self):
        with self.assertRaises(PermissionDenied):
            remove_post(post=self.post, removed_by=self.student.user)

    def test_teacher_can_pin_post(self):
        post = ClassPost.objects.create(
            school_class=self.school_class,
            author=self.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="Pin me.",
        )
        self.assertFalse(post.is_pinned)
        toggle_pin(post=post, actor=self.teacher_user)
        post.refresh_from_db()
        self.assertTrue(post.is_pinned)

    def test_pin_toggles_off(self):
        post = ClassPost.objects.create(
            school_class=self.school_class,
            author=self.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="Already pinned.",
            is_pinned=True,
        )
        toggle_pin(post=post, actor=self.teacher_user)
        post.refresh_from_db()
        self.assertFalse(post.is_pinned)

    def test_student_cannot_pin(self):
        with self.assertRaises(PermissionDenied):
            toggle_pin(post=self.post, actor=self.student.user)


class SetReactionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        cls.teacher_user, cls.teacher = make_teacher("react_teacher", "RT-001")
        cls.subject = make_subject("HIST", "History")
        assign_teacher(cls.teacher, cls.school_class, cls.subject, cls.year)
        cls.student = make_student_user(cls.school_class, admission_number="RT-S01")
        cls.post = ClassPost.objects.create(
            school_class=cls.school_class,
            author=cls.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="React to this.",
        )

    def test_student_can_add_reaction(self):
        r = set_reaction(
            post=self.post,
            user=self.student.user,
            emoji=ClassPostReaction.Emoji.THUMBS_UP,
        )
        self.assertIsNotNone(r)
        self.assertEqual(r.emoji, ClassPostReaction.Emoji.THUMBS_UP)

    def test_same_emoji_toggles_off(self):
        set_reaction(
            post=self.post,
            user=self.student.user,
            emoji=ClassPostReaction.Emoji.THUMBS_UP,
        )
        result = set_reaction(
            post=self.post,
            user=self.student.user,
            emoji=ClassPostReaction.Emoji.THUMBS_UP,
        )
        self.assertIsNone(result)
        self.assertFalse(
            ClassPostReaction.objects.filter(
                post=self.post, user=self.student.user
            ).exists()
        )

    def test_different_emoji_replaces_existing(self):
        set_reaction(
            post=self.post,
            user=self.student.user,
            emoji=ClassPostReaction.Emoji.THUMBS_UP,
        )
        r = set_reaction(
            post=self.post,
            user=self.student.user,
            emoji=ClassPostReaction.Emoji.HEART,
        )
        self.assertEqual(r.emoji, ClassPostReaction.Emoji.HEART)
        self.assertEqual(
            ClassPostReaction.objects.filter(
                post=self.post, user=self.student.user
            ).count(),
            1,
        )

    def test_none_emoji_removes_reaction(self):
        set_reaction(
            post=self.post,
            user=self.student.user,
            emoji=ClassPostReaction.Emoji.CHECK,
        )
        result = set_reaction(post=self.post, user=self.student.user, emoji=None)
        self.assertIsNone(result)
        self.assertFalse(
            ClassPostReaction.objects.filter(
                post=self.post, user=self.student.user
            ).exists()
        )

    def test_invalid_emoji_raises_value_error(self):
        with self.assertRaises(ValueError):
            set_reaction(
                post=self.post,
                user=self.student.user,
                emoji="💀",  # not in allowed set
            )

    def test_parent_cannot_react(self):
        """Parents are read-only in the feed; the server rejects the reaction."""
        parent_user, _ = make_parent_for(self.student)
        with self.assertRaises(PermissionDenied):
            set_reaction(
                post=self.post,
                user=parent_user,
                emoji=ClassPostReaction.Emoji.HEART,
            )


# ============================================================================
# C. feed_selectors
# ============================================================================


class FeedSelectorsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year, name="Grade 5", level_order=5)
        cls.subject = make_subject("GEO", "Geography")
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.subject)

        cls.teacher_user, cls.teacher = make_teacher("sel_teacher", "ST-001")
        assign_teacher(cls.teacher, cls.school_class, cls.subject, cls.year)

        cls.student = make_student_user(cls.school_class, admission_number="SL-001")
        cls.parent_user, _ = make_parent_for(cls.student)

        cls.other_class = make_class(cls.year, name="Grade 4", level_order=4)
        cls.other_student = make_student_user(
            cls.other_class, admission_number="SL-002"
        )

        # Posts: one normal, one pinned, one removed
        cls.normal_post = ClassPost.objects.create(
            school_class=cls.school_class,
            author=cls.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="Normal post.",
        )
        cls.pinned_post = ClassPost.objects.create(
            school_class=cls.school_class,
            author=cls.teacher_user,
            post_type=ClassPost.PostType.TIP,
            body="Pinned tip.",
            is_pinned=True,
        )
        cls.removed_post = ClassPost.objects.create(
            school_class=cls.school_class,
            author=cls.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="Removed — should be hidden from students.",
            is_removed=True,
        )

    # ── can_post ────────────────────────────────────────────────────────────

    def test_assigned_teacher_can_post(self):
        self.assertTrue(can_post(self.teacher_user, self.school_class))

    def test_enrolled_student_can_post(self):
        self.assertTrue(can_post(self.student.user, self.school_class))

    def test_parent_cannot_post(self):
        self.assertFalse(can_post(self.parent_user, self.school_class))

    def test_student_in_other_class_cannot_post(self):
        self.assertFalse(can_post(self.other_student.user, self.school_class))

    # ── can_view_feed ────────────────────────────────────────────────────────

    def test_teacher_can_view_own_class_feed(self):
        self.assertTrue(can_view_feed(self.teacher_user, self.school_class))

    def test_student_can_view_own_class_feed(self):
        self.assertTrue(can_view_feed(self.student.user, self.school_class))

    def test_parent_can_view_childs_class_feed(self):
        self.assertTrue(can_view_feed(self.parent_user, self.school_class))

    def test_student_cannot_view_other_class_feed(self):
        self.assertFalse(can_view_feed(self.other_student.user, self.school_class))

    # ── is_moderator ─────────────────────────────────────────────────────────

    def test_teacher_is_moderator(self):
        self.assertTrue(is_moderator(self.teacher_user))

    def test_student_is_not_moderator(self):
        self.assertFalse(is_moderator(self.student.user))

    def test_parent_is_not_moderator(self):
        self.assertFalse(is_moderator(self.parent_user))

    # ── feed_for_classroom visibility ─────────────────────────────────────

    def test_student_does_not_see_removed_post(self):
        items = feed_for_classroom(
            school_class=self.school_class, viewer=self.student.user
        )
        post_ids = [i.post.pk for i in items]
        self.assertNotIn(self.removed_post.pk, post_ids)

    def test_teacher_sees_removed_post(self):
        items = feed_for_classroom(
            school_class=self.school_class, viewer=self.teacher_user
        )
        post_ids = [i.post.pk for i in items]
        self.assertIn(self.removed_post.pk, post_ids)

    def test_parent_does_not_see_removed_post(self):
        items = feed_for_classroom(
            school_class=self.school_class, viewer=self.parent_user
        )
        post_ids = [i.post.pk for i in items]
        self.assertNotIn(self.removed_post.pk, post_ids)

    def test_pinned_post_comes_first(self):
        items = feed_for_classroom(
            school_class=self.school_class, viewer=self.student.user
        )
        self.assertTrue(len(items) >= 2)
        self.assertEqual(items[0].post.pk, self.pinned_post.pk)

    def test_reaction_summary_reflects_stored_reactions(self):
        ClassPostReaction.objects.create(
            post=self.normal_post,
            user=self.student.user,
            emoji=ClassPostReaction.Emoji.THUMBS_UP,
        )
        items = feed_for_classroom(
            school_class=self.school_class, viewer=self.student.user
        )
        normal_item = next(i for i in items if i.post.pk == self.normal_post.pk)
        self.assertEqual(
            normal_item.reactions.counts.get(ClassPostReaction.Emoji.THUMBS_UP), 1
        )
        self.assertEqual(
            normal_item.reactions.viewer_emoji, ClassPostReaction.Emoji.THUMBS_UP
        )

    # ── post_detail ──────────────────────────────────────────────────────────

    def test_post_detail_returns_data_for_visible_post(self):
        result = post_detail(
            post_id=self.normal_post.pk, viewer=self.student.user
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.post.pk, self.normal_post.pk)

    def test_post_detail_returns_none_for_removed_post_as_student(self):
        result = post_detail(
            post_id=self.removed_post.pk, viewer=self.student.user
        )
        self.assertIsNone(result)

    def test_post_detail_returns_removed_post_to_teacher(self):
        result = post_detail(
            post_id=self.removed_post.pk, viewer=self.teacher_user
        )
        self.assertIsNotNone(result)


# ============================================================================
# D. Views
# ============================================================================


class ClassFeedViewGetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 8", level_order=8)
        cls.subject = make_subject("ART", "Art")
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.subject)

        cls.teacher_user, cls.teacher = make_teacher("view_teacher", "VT-001")
        assign_teacher(cls.teacher, cls.school_class, cls.subject, cls.year)

        cls.student = make_student_user(cls.school_class, admission_number="VW-001")
        cls.parent_user, _ = make_parent_for(cls.student)

        cls.other_student = make_student_user(
            make_class(cls.year, name="Grade 9", level_order=9),
            admission_number="VW-002",
        )

    def _url(self):
        return reverse("learning:class_feed", args=[self.school_class.pk])

    def test_teacher_can_view_feed(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Class Feed")
        self.assertContains(r, self.school_class.name)

    def test_student_can_view_own_class_feed(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)

    def test_parent_can_view_childs_class_feed(self):
        self.client.force_login(self.parent_user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)

    def test_student_from_other_class_gets_404(self):
        self.client.force_login(self.other_student.user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 404)

    def test_anonymous_is_redirected(self):
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 302)

    def test_teacher_sees_add_post_form(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._url())
        self.assertContains(r, "Add a post")

    def test_parent_does_not_see_post_form(self):
        self.client.force_login(self.parent_user)
        r = self.client.get(self._url())
        self.assertNotContains(r, "Add a post")

    def test_learning_disabled_shows_disabled_page(self):
        row = SchoolSettings.load()
        row.learning_enabled = False
        row.save(update_fields=["learning_enabled", "updated_at"])
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Learning module is activated")
        # Re-enable
        row.learning_enabled = True
        row.save(update_fields=["learning_enabled", "updated_at"])


class ClassFeedViewPostTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 10", level_order=10)
        cls.subject = make_subject("MUS", "Music")
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.subject)

        cls.teacher_user, cls.teacher = make_teacher("post_teacher", "PT-002")
        assign_teacher(cls.teacher, cls.school_class, cls.subject, cls.year)

        cls.student = make_student_user(cls.school_class, admission_number="FP-001")

    def _url(self):
        return reverse("learning:class_feed", args=[self.school_class.pk])

    def test_teacher_can_create_announcement(self):
        self.client.force_login(self.teacher_user)
        self.client.post(
            self._url(),
            {"body": "Welcome to the class!", "post_type": "announcement"},
        )
        self.assertTrue(
            ClassPost.objects.filter(
                school_class=self.school_class,
                author=self.teacher_user,
                post_type="announcement",
            ).exists()
        )

    def test_student_cannot_post_a_teacher_type(self):
        self.client.force_login(self.student.user)
        self.client.post(
            self._url(),
            {"body": "What page are we on?", "post_type": "announcement"},
        )
        self.assertFalse(
            ClassPost.objects.filter(
                school_class=self.school_class, author=self.student.user
            ).exists()
        )

    def test_empty_body_does_not_create_post(self):
        self.client.force_login(self.teacher_user)
        count_before = ClassPost.objects.filter(school_class=self.school_class).count()
        self.client.post(self._url(), {"body": "   ", "post_type": "announcement"})
        self.assertEqual(
            ClassPost.objects.filter(school_class=self.school_class).count(),
            count_before,
        )

    def test_parent_cannot_post(self):
        parent_user, _ = make_parent_for(self.student)
        self.client.force_login(parent_user)
        r = self.client.post(
            self._url(),
            {"body": "Hello!", "post_type": "question"},
        )
        # Should redirect (no PermissionDenied raised to the HTTP layer —
        # the service raises it and the view catches it and shows an error)
        self.assertEqual(r.status_code, 302)
        self.assertFalse(
            ClassPost.objects.filter(
                school_class=self.school_class, author=parent_user
            ).exists()
        )


class ClassFeedPostActionViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 11", level_order=11)
        cls.subject = make_subject("PHY", "Physics")
        ClassSubject.objects.create(school_class=cls.school_class, subject=cls.subject)

        cls.teacher_user, cls.teacher = make_teacher("act_teacher", "AT-001")
        assign_teacher(cls.teacher, cls.school_class, cls.subject, cls.year)

        cls.student = make_student_user(cls.school_class, admission_number="AC-001")
        cls.parent_user, _ = make_parent_for(cls.student)

    def _make_post(self, body="Test post."):
        return ClassPost.objects.create(
            school_class=self.school_class,
            author=self.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body=body,
        )

    def _action_url(self, post):
        return reverse(
            "learning:feed_post_action",
            args=[self.school_class.pk, post.pk],
        )

    # ── Remove ──────────────────────────────────────────────────────────────

    def test_teacher_can_remove_post_via_view(self):
        post = self._make_post("Remove me.")
        self.client.force_login(self.teacher_user)
        r = self.client.post(self._action_url(post), {"action": "remove"})
        self.assertEqual(r.status_code, 302)
        post.refresh_from_db()
        self.assertTrue(post.is_removed)

    def test_student_cannot_remove_post_via_view(self):
        post = self._make_post("Student tries to remove.")
        self.client.force_login(self.student.user)
        r = self.client.post(self._action_url(post), {"action": "remove"})
        # Redirects with error message — no 403 exposed to HTTP layer
        self.assertEqual(r.status_code, 302)
        post.refresh_from_db()
        self.assertFalse(post.is_removed)

    # ── Pin ─────────────────────────────────────────────────────────────────

    def test_teacher_can_pin_post_via_view(self):
        post = self._make_post("Pin me.")
        self.client.force_login(self.teacher_user)
        self.client.post(self._action_url(post), {"action": "pin"})
        post.refresh_from_db()
        self.assertTrue(post.is_pinned)

    # ── React ────────────────────────────────────────────────────────────────

    def test_student_can_react_via_view(self):
        post = self._make_post("React here.")
        self.client.force_login(self.student.user)
        self.client.post(
            self._action_url(post),
            {"action": "react", "emoji": ClassPostReaction.Emoji.THUMBS_UP},
        )
        self.assertTrue(
            ClassPostReaction.objects.filter(
                post=post,
                user=self.student.user,
                emoji=ClassPostReaction.Emoji.THUMBS_UP,
            ).exists()
        )

    def test_reaction_xhr_returns_json(self):
        post = self._make_post("XHR react.")
        self.client.force_login(self.student.user)
        r = self.client.post(
            self._action_url(post),
            {"action": "react", "emoji": ClassPostReaction.Emoji.CHECK},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertIn("counts", data)

    def test_get_returns_404(self):
        post = self._make_post()
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._action_url(post))
        self.assertEqual(r.status_code, 404)

    def test_action_on_post_in_other_class_is_404(self):
        other_class = make_class(self.year, name="Grade 12", level_order=12)
        other_post = ClassPost.objects.create(
            school_class=other_class,
            author=self.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="Other class post.",
        )
        url = reverse(
            "learning:feed_post_action",
            # wrong class_id in URL
            args=[self.school_class.pk, other_post.pk],
        )
        self.client.force_login(self.teacher_user)
        r = self.client.post(url, {"action": "pin"})
        self.assertEqual(r.status_code, 404)
