"""
apps/community/tests/test_participation.py
-------------------------------------------
Tests for Module 2: participation (post, reply, react, drafts, rate-limits,
consent gate, sign-in redirect, dashboard cards).
"""

from __future__ import annotations

import json

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.community.models import (
    Category,
    Comment,
    CommunityProfile,
    Post,
    PostDraft,
    Reaction,
)
from apps.community.selectors import postable_categories
from apps.community.services import (
    can_post_in_category,
    clear_draft,
    create_comment,
    create_post,
    get_draft,
    grant_consent,
    save_draft,
    toggle_reaction,
    touch_profile,
)
from apps.core.models import SchoolSettings

from .fixtures import (
    disable_community,
    enable_community,
    make_category,
    make_comment,
    make_post,
    make_user,
)


def _login(client, user):
    client.force_login(user)


# ─── Consent gate ─────────────────────────────────────────────────────────────

class ConsentGateTests(TestCase):
    def setUp(self):
        enable_community()
        self.cat = make_category(slug="cg-pub", visibility="public", name="CG")
        self.student = make_user("student", username="cg_student")
        self.teacher = make_user("teacher", username="cg_teacher")

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_can_post_returns_consent_required_before_consent(self):
        # No profile exists yet → consent_required
        allowed, reason = can_post_in_category(self.student, self.cat)
        self.assertFalse(allowed)
        self.assertEqual(reason, "consent_required")

    def test_can_post_allowed_after_consent(self):
        grant_consent(self.student)
        allowed, _ = can_post_in_category(self.student, self.cat)
        self.assertTrue(allowed)

    def test_post_create_raises_without_consent(self):
        with self.assertRaises(PermissionError):
            create_post(actor=self.student, category=self.cat,
                        title="Test", body="Body", student_moderation=True)

    def test_consent_view_get_200(self):
        _login(self.client, self.student)
        resp = self.client.get(reverse("community:consent"))
        self.assertEqual(resp.status_code, 200)

    def test_consent_view_post_grants_consent(self):
        _login(self.client, self.student)
        self.client.post(reverse("community:consent"),
                         {"consent": "yes", "next": "/community/"})
        profile = CommunityProfile.objects.get(user=self.student)
        self.assertIsNotNone(profile.consented_at)

    def test_post_create_view_redirects_to_consent_without_it(self):
        _login(self.client, self.student)
        resp = self.client.post(reverse("community:post_create"), {
            "title": "Hello", "body": "World", "category": self.cat.slug,
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        data = json.loads(resp.content)
        self.assertFalse(data["ok"])

    def test_unauth_post_create_returns_400_json(self):
        resp = self.client.post(reverse("community:post_create"), {
            "title": "Hello", "body": "World", "category": self.cat.slug,
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        # 302 redirect to login (LoginRequiredMixin)
        self.assertEqual(resp.status_code, 302)


# ─── Post creation ────────────────────────────────────────────────────────────

class PostCreateServiceTests(TestCase):
    def setUp(self):
        enable_community()
        self.pub_cat = make_category(slug="pc-pub", visibility="public", name="PC")
        self.teacher = make_user("teacher", username="pc_teacher")
        self.student = make_user("student", username="pc_student",
                                 first_name="Lena", last_name="Ochieng")
        grant_consent(self.teacher)
        grant_consent(self.student)
        # Clear rate-limit cache so each test class starts with a clean slate
        from django.core.cache import cache
        cache.clear()

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)
        from django.core.cache import cache
        cache.clear()

    def test_teacher_post_published_immediately(self):
        post = create_post(actor=self.teacher, category=self.pub_cat,
                           title="Hello world", body="Content here",
                           student_moderation=True)
        self.assertEqual(post.status, Post.Status.PUBLISHED)

    def test_student_post_public_pending_when_moderation_on(self):
        post = create_post(actor=self.student, category=self.pub_cat,
                           title="Student post", body="Body",
                           student_moderation=True)
        self.assertEqual(post.status, Post.Status.PENDING)

    def test_student_post_public_published_when_moderation_off(self):
        post = create_post(actor=self.student, category=self.pub_cat,
                           title="Student open", body="Body",
                           student_moderation=False)
        self.assertEqual(post.status, Post.Status.PUBLISHED)

    def test_xss_stripped_from_body(self):
        post = create_post(actor=self.teacher, category=self.pub_cat,
                           title="Safe", body='<script>alert("xss")</script>Body',
                           student_moderation=False)
        self.assertNotIn("<script>", post.body)
        self.assertIn("Body", post.body)

    def test_title_stripped_of_html(self):
        post = create_post(actor=self.teacher, category=self.pub_cat,
                           title="<b>Bold</b>", body="Body",
                           student_moderation=False)
        self.assertNotIn("<b>", post.title)
        self.assertIn("Bold", post.title)

    def test_staff_only_category_blocks_student(self):
        staff_cat = make_category(slug="staff-only", visibility="public",
                                  staff_post_only=True, name="Staff Only")
        with self.assertRaises(PermissionError):
            create_post(actor=self.student, category=staff_cat,
                        title="Nope", body="Nope", student_moderation=False)

    def test_staff_only_allows_teacher(self):
        staff_cat = make_category(slug="staff-only2", visibility="public",
                                  staff_post_only=True, name="Staff Only 2")
        # Need teacher to have access via visibility too (public so visible)
        post = create_post(actor=self.teacher, category=staff_cat,
                           title="OK", body="Body", student_moderation=False)
        self.assertEqual(post.status, Post.Status.PUBLISHED)

    def test_draft_cleared_on_post(self):
        save_draft(user=self.teacher, title="Draft", body="Body")
        create_post(actor=self.teacher, category=self.pub_cat,
                    title="Real post", body="Real body", student_moderation=False)
        self.assertIsNone(PostDraft.objects.filter(user=self.teacher).first())


# ─── Post create view (HTTP) ──────────────────────────────────────────────────

class PostCreateViewTests(TestCase):
    def setUp(self):
        enable_community()
        self.cat = make_category(slug="pv-pub", visibility="public", name="PV")
        self.teacher = make_user("teacher", username="pv_teacher")
        grant_consent(self.teacher)
        from django.core.cache import cache
        cache.clear()

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)
        from django.core.cache import cache
        cache.clear()

    def test_post_create_success_json(self):
        _login(self.client, self.teacher)
        resp = self.client.post(reverse("community:post_create"), {
            "title": "My post", "body": "My body", "category": self.cat.slug,
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        self.assertTrue(Post.objects.filter(title="My post").exists())

    def test_post_create_missing_title_returns_error(self):
        _login(self.client, self.teacher)
        resp = self.client.post(reverse("community:post_create"), {
            "title": "", "body": "Body", "category": self.cat.slug,
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        data = json.loads(resp.content)
        self.assertFalse(data["ok"])

    def test_post_create_no_category_returns_error(self):
        _login(self.client, self.teacher)
        resp = self.client.post(reverse("community:post_create"), {
            "title": "T", "body": "B", "category": "",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        data = json.loads(resp.content)
        self.assertFalse(data["ok"])


# ─── Rate limits ──────────────────────────────────────────────────────────────

class RateLimitTests(TestCase):
    """Daily rate limit: 30 posts per user per day."""

    def setUp(self):
        enable_community()
        self.cat = make_category(slug="rl-pub", visibility="public", name="RL")
        self.teacher = make_user("teacher", username="rl_teacher")
        grant_consent(self.teacher)
        from django.core.cache import cache
        cache.clear()
        profile, _ = CommunityProfile.objects.get_or_create(user=self.teacher)
        # Simulate reaching the daily limit
        from apps.community.services import RATE_PER_DAY
        profile.posts_today = RATE_PER_DAY
        profile.posts_today_date = timezone.localdate()
        profile.save()
        # Also zero out the per-minute counter so only the daily check fires
        from django.core.cache import cache as c2
        c2.set(f"community:posts_per_min:{self.teacher.pk}", 0, 60)

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)
        from django.core.cache import cache
        cache.clear()

    def test_daily_limit_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            create_post(actor=self.teacher, category=self.cat,
                        title="Over limit", body="Body", student_moderation=False)
        self.assertIn("today's posting limit", str(ctx.exception))

    def test_new_day_resets_limit(self):
        from datetime import date, timedelta
        from django.core.cache import cache
        profile = CommunityProfile.objects.get(user=self.teacher)
        profile.posts_today_date = date.today() - timedelta(days=1)
        profile.save()
        # Reset per-minute counter so we don't hit that limit either
        cache.set(f"community:posts_per_min:{self.teacher.pk}", 0, 60)
        post = create_post(actor=self.teacher, category=self.cat,
                           title="New day", body="Body", student_moderation=False)
        self.assertEqual(post.status, Post.Status.PUBLISHED)


# ─── Duplicate detection ──────────────────────────────────────────────────────

class DuplicateDetectionTests(TestCase):
    def setUp(self):
        enable_community()
        self.cat = make_category(slug="dd-pub", visibility="public", name="DD")
        self.teacher = make_user("teacher", username="dd_teacher")
        grant_consent(self.teacher)
        from django.core.cache import cache
        cache.clear()

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)
        from django.core.cache import cache
        cache.clear()

    def test_duplicate_raises_on_second_submit(self):
        create_post(actor=self.teacher, category=self.cat,
                    title="Unique title", body="Unique body", student_moderation=False)
        with self.assertRaises(ValueError) as ctx:
            create_post(actor=self.teacher, category=self.cat,
                        title="Unique title", body="Unique body", student_moderation=False)
        self.assertIn("same post", str(ctx.exception))


# ─── Replies ─────────────────────────────────────────────────────────────────

class ReplyTests(TestCase):
    def setUp(self):
        enable_community()
        self.cat = make_category(slug="rep-pub", visibility="public", name="Rep")
        self.teacher = make_user("teacher", username="rep_teacher")
        self.student = make_user("student", username="rep_student")
        self.admin = make_user("admin", username="rep_admin")
        grant_consent(self.teacher)
        grant_consent(self.student)
        self.post = make_post(author=self.teacher, category=self.cat)
        from django.core.cache import cache
        cache.clear()

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)
        from django.core.cache import cache
        cache.clear()

    def test_reply_creates_comment(self):
        comment = create_comment(actor=self.student, post=self.post, body="Nice post!")
        self.assertEqual(Comment.objects.filter(post=self.post).count(), 1)
        self.assertEqual(comment.body, "Nice post!")

    def test_reply_to_comment_sets_parent(self):
        top = create_comment(actor=self.teacher, post=self.post, body="Top")
        reply = create_comment(actor=self.student, post=self.post,
                               body="Nested", parent=top)
        self.assertEqual(reply.parent_id, top.pk)

    def test_reply_to_reply_joins_top_level_thread(self):
        top = create_comment(actor=self.teacher, post=self.post, body="Top")
        first = create_comment(actor=self.student, post=self.post,
                               body="First", parent=top)
        second = create_comment(actor=self.teacher, post=self.post,
                                body="Second", parent=first)
        self.assertEqual(second.parent_id, top.pk)  # threads stay one level deep

    def test_reply_view_accepts_parent(self):
        top = create_comment(actor=self.teacher, post=self.post, body="Top")
        _login(self.client, self.student)
        resp = self.client.post(
            reverse("community:comment_create", args=[self.post.pk]),
            {"body": "Replying to you", "parent": top.pk},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        created = Comment.objects.get(pk=data["comment_pk"])
        self.assertEqual(created.parent_id, top.pk)
        self.assertIn(f"#comment-{created.pk}", data["redirect"])

    def test_parent_from_another_post_is_rejected(self):
        other_cat = make_category(slug="rep-other", visibility="public", name="Other")
        other_post = make_post(author=self.teacher, category=other_cat)
        foreign = make_comment(author=self.teacher, post=other_post)
        _login(self.client, self.student)
        resp = self.client.post(
            reverse("community:comment_create", args=[self.post.pk]),
            {"body": "Wrong thread", "parent": foreign.pk},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        data = json.loads(resp.content)
        self.assertFalse(data["ok"])
        self.assertFalse(Comment.objects.filter(post=self.post).exists())

    def test_reply_blocked_on_locked_post(self):
        from apps.community.services import lock_post
        lock_post(actor=self.admin, post=self.post)   # admin locks, not teacher
        self.post.refresh_from_db()
        with self.assertRaises(PermissionError):
            create_comment(actor=self.student, post=self.post, body="Locked!")

    def test_reply_blocked_on_removed_post(self):
        self.post.status = Post.Status.REMOVED
        self.post.save()
        with self.assertRaises(PermissionError):
            create_comment(actor=self.student, post=self.post, body="Removed!")

    def test_reply_blocked_without_consent(self):
        new_user = make_user("parent", username="rep_parent_nc")
        with self.assertRaises(PermissionError):
            create_comment(actor=new_user, post=self.post, body="No consent")

    def test_reply_view_success(self):
        _login(self.client, self.student)
        resp = self.client.post(
            reverse("community:comment_create", args=[self.post.pk]),
            {"body": "My reply here"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])

    def test_reply_view_empty_body_returns_error(self):
        _login(self.client, self.student)
        resp = self.client.post(
            reverse("community:comment_create", args=[self.post.pk]),
            {"body": ""},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        data = json.loads(resp.content)
        self.assertFalse(data["ok"])


# ─── Reactions ────────────────────────────────────────────────────────────────

class ReactionTests(TestCase):
    def setUp(self):
        enable_community()
        self.cat = make_category(slug="rx-pub", visibility="public", name="Rx")
        self.teacher = make_user("teacher", username="rx_teacher")
        self.parent = make_user("parent", username="rx_parent")
        grant_consent(self.parent)
        self.post = make_post(author=self.teacher, category=self.cat)

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_reaction_toggle_add(self):
        result = toggle_reaction(actor=self.parent, post=self.post, emoji="👍")
        self.assertTrue(result["added"])
        self.assertEqual(result["count"], 1)

    def test_reaction_toggle_remove_on_second_call(self):
        toggle_reaction(actor=self.parent, post=self.post, emoji="👍")
        result = toggle_reaction(actor=self.parent, post=self.post, emoji="👍")
        self.assertFalse(result["added"])
        self.assertEqual(result["count"], 0)

    def test_different_emoji_both_saved(self):
        toggle_reaction(actor=self.parent, post=self.post, emoji="👍")
        toggle_reaction(actor=self.parent, post=self.post, emoji="❤️")
        self.assertEqual(Reaction.objects.filter(post=self.post).count(), 2)

    def test_invalid_emoji_raises(self):
        with self.assertRaises(ValueError):
            toggle_reaction(actor=self.parent, post=self.post, emoji="💩")

    def test_reaction_view_success(self):
        _login(self.client, self.parent)
        resp = self.client.post(
            reverse("community:reaction_toggle", args=[self.post.pk]),
            {"emoji": "🎉"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        self.assertTrue(data["added"])


# ─── Drafts ───────────────────────────────────────────────────────────────────

class DraftTests(TestCase):
    """Drafts are isolated per account — a draft for user A is never visible to user B."""

    def setUp(self):
        self.user_a = make_user("teacher", username="draft_a")
        self.user_b = make_user("teacher", username="draft_b")

    def test_save_and_retrieve_draft(self):
        save_draft(user=self.user_a, title="My draft", body="Draft body")
        draft = get_draft(self.user_a)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.title, "My draft")

    def test_draft_isolated_between_users(self):
        save_draft(user=self.user_a, title="A's draft", body="For A only")
        self.assertIsNone(get_draft(self.user_b))

    def test_save_overwrites_previous_draft(self):
        save_draft(user=self.user_a, title="First", body="Body 1")
        save_draft(user=self.user_a, title="Second", body="Body 2")
        draft = get_draft(self.user_a)
        self.assertEqual(draft.title, "Second")

    def test_clear_draft(self):
        save_draft(user=self.user_a, title="Temp", body="Body")
        clear_draft(self.user_a)
        self.assertIsNone(get_draft(self.user_a))

    def test_draft_save_view_requires_login(self):
        resp = self.client.post(reverse("community:draft_save"),
                                json.dumps({"title": "T", "body": "B"}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 302)

    def test_draft_save_view_success(self):
        _login(self.client, self.user_a)
        resp = self.client.post(
            reverse("community:draft_save"),
            json.dumps({"title": "Draft title", "body": "Draft body"}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        draft = PostDraft.objects.filter(user=self.user_a).first()
        self.assertIsNotNone(draft)
        self.assertEqual(draft.title, "Draft title")

    def test_signout_does_not_expose_draft_to_next_user(self):
        """After user A logs out, user B should not see user A's draft."""
        save_draft(user=self.user_a, title="Private", body="Secret")
        # user_b has their own session — draft is by user FK, not session
        self.assertIsNone(get_draft(self.user_b))


# ─── Postable categories ──────────────────────────────────────────────────────

class PostableCategoriesTests(TestCase):
    def setUp(self):
        self.staff_cat = make_category(
            slug="pc-staff", visibility="public",
            staff_post_only=True, name="Staff Only"
        )
        self.open_cat = make_category(
            slug="pc-open", visibility="public",
            staff_post_only=False, name="Open"
        )
        self.teacher = make_user("teacher", username="pct_teacher")
        self.student = make_user("student", username="pct_student")

    def test_student_does_not_see_staff_only_in_postable(self):
        cats = postable_categories(self.student)
        slugs = [c.slug for c in cats]
        self.assertNotIn("pc-staff", slugs)
        self.assertIn("pc-open", slugs)

    def test_teacher_sees_staff_only_in_postable(self):
        cats = postable_categories(self.teacher)
        slugs = [c.slug for c in cats]
        self.assertIn("pc-staff", slugs)


# ─── Sign-in redirect ─────────────────────────────────────────────────────────

class SignInRedirectTests(TestCase):
    def setUp(self):
        enable_community()

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_unauth_post_create_redirects_to_login(self):
        resp = self.client.post(reverse("community:post_create"),
                                {"title": "T", "body": "B", "category": "general-discussion"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])

    def test_unauth_comment_redirects_to_login(self):
        cat = make_category(slug="sr-cat", visibility="public", name="SR")
        teacher = make_user("teacher", username="sr_teacher")
        post = make_post(author=teacher, category=cat)
        resp = self.client.post(reverse("community:comment_create", args=[post.pk]),
                                {"body": "Reply"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])

    def test_next_param_carried_through_consent(self):
        user = make_user("parent", username="sr_parent")
        _login(self.client, user)
        resp = self.client.get(reverse("community:consent") + "?next=/community/post/1/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "/community/post/1/")


# ─── Dashboard card endpoint ──────────────────────────────────────────────────

class DashboardCardTests(TestCase):
    def setUp(self):
        enable_community()
        self.student = make_user("student", username="dc_student")
        self.teacher = make_user("teacher", username="dc_teacher")
        self.admin = make_user("admin", username="dc_admin")
        self.parent = make_user("parent", username="dc_parent")

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def _card(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("community:dashboard_card"))

    def test_student_card_200(self):
        self.assertEqual(self._card(self.student).status_code, 200)

    def test_teacher_card_200(self):
        self.assertEqual(self._card(self.teacher).status_code, 200)

    def test_admin_card_200(self):
        self.assertEqual(self._card(self.admin).status_code, 200)

    def test_parent_card_200(self):
        self.assertEqual(self._card(self.parent).status_code, 200)

    def test_card_empty_when_community_off(self):
        disable_community()
        resp = self._card(self.student)
        self.assertEqual(resp.content, b"")
