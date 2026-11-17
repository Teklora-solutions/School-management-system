"""
apps/community/tests/test_community.py
---------------------------------------
Tests for Module 1: the read-only public community.

Coverage matrix
~~~~~~~~~~~~~~~
Visibility rules
  - Visitors see Public categories and posts; nothing else
  - Signed-in users see Public + SIGNED_IN categories
  - ROLE categories shown only to users whose role is in allowed_roles
  - Hidden (is_active=False) categories never shown to anyone
  - PENDING and REMOVED posts never shown to visitors or signed-in non-staff

Feature gate
  - community OFF → all URLs redirect to /community/paused/
  - community PAUSED → all main URLs redirect to /community/paused/
  - Paused page itself always renders (no redirect loop)

Navbar
  - Community link absent when feature OFF
  - Community link present when feature ON

Search
  - Only returns posts in categories the visitor can see
  - Empty query returns empty list

Selectors
  - post_feed returns pinned posts first on "new" sort
  - Cursor paging: next_cursor set when more than PAGE_SIZE posts exist
  - members_list excludes students; returns empty for visitors

Services
  - create_post: published immediately for non-student or non-public category
  - create_post: PENDING for student + public category when moderation=True
  - create_post: published for student + public category when moderation=False
  - toggle_reaction: adds and removes correctly
  - Sanitisation: script tags stripped from post body

noindex
  - Community pages send X-Robots-Tag noindex when community_index=False
  - Pages send no noindex override when community_index=True

Student safety
  - Student author shown as "First L." (first name + last initial only)
  - Student posts in public category held PENDING when moderation on
"""

from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.community.models import Category, CommunityProfile, Post, Reaction
from apps.community.selectors import (
    PAGE_SIZE,
    _visible_category_ids,
    community_stats,
    members_list,
    post_feed,
    search_posts,
    visible_categories,
)
from apps.community.services import (
    create_post,
    toggle_reaction,
    can_post_in_category,
    grant_consent,
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


# ─── Helpers ─────────────────────────────────────────────────────────────────

class AnonymousUser:
    """Minimal stand-in for request.user when not authenticated."""
    is_authenticated = False


ANON = AnonymousUser()


# ─── Visibility rules ────────────────────────────────────────────────────────

class CategoryVisibilityTests(TestCase):
    def setUp(self):
        self.public_cat    = make_category(slug="pub",    visibility="public",    name="Public")
        self.signed_in_cat = make_category(slug="signed", visibility="signed_in", name="Members")
        self.role_cat      = make_category(
            slug="teachers", visibility="role", name="Teachers",
            allowed_roles=["teacher", "director", "admin"],
        )
        self.hidden_cat    = make_category(slug="hidden", is_active=False, name="Hidden")

        self.teacher = make_user("teacher")
        self.student = make_user("student")
        self.parent  = make_user("parent")

    # Public category
    def test_public_visible_to_visitor(self):
        self.assertTrue(self.public_cat.is_visible_to(ANON))

    def test_public_visible_to_student(self):
        self.assertTrue(self.public_cat.is_visible_to(self.student))

    # Signed-in category
    def test_signed_in_hidden_from_visitor(self):
        self.assertFalse(self.signed_in_cat.is_visible_to(ANON))

    def test_signed_in_visible_to_student(self):
        self.assertTrue(self.signed_in_cat.is_visible_to(self.student))

    def test_signed_in_visible_to_parent(self):
        self.assertTrue(self.signed_in_cat.is_visible_to(self.parent))

    # Role-restricted category
    def test_role_hidden_from_visitor(self):
        self.assertFalse(self.role_cat.is_visible_to(ANON))

    def test_role_hidden_from_student(self):
        self.assertFalse(self.role_cat.is_visible_to(self.student))

    def test_role_hidden_from_parent(self):
        self.assertFalse(self.role_cat.is_visible_to(self.parent))

    def test_role_visible_to_teacher(self):
        self.assertTrue(self.role_cat.is_visible_to(self.teacher))

    # Hidden category
    def test_hidden_invisible_to_everyone(self):
        self.assertFalse(self.hidden_cat.is_visible_to(ANON))
        self.assertFalse(self.hidden_cat.is_visible_to(self.teacher))

    # visible_categories() selector
    def test_visible_categories_visitor_gets_only_public(self):
        cats = visible_categories(ANON)
        slugs = [c.slug for c in cats]
        self.assertIn("pub", slugs)
        self.assertNotIn("signed", slugs)
        self.assertNotIn("teachers", slugs)
        self.assertNotIn("hidden", slugs)

    def test_visible_categories_student_gets_public_and_signed_in(self):
        cats = visible_categories(self.student)
        slugs = [c.slug for c in cats]
        self.assertIn("pub", slugs)
        self.assertIn("signed", slugs)
        self.assertNotIn("teachers", slugs)

    def test_visible_categories_teacher_gets_role_category(self):
        cats = visible_categories(self.teacher)
        slugs = [c.slug for c in cats]
        self.assertIn("teachers", slugs)


class PostVisibilityTests(TestCase):
    def setUp(self):
        self.public_cat = make_category(slug="v-pub", visibility="public", name="Public")
        self.role_cat   = make_category(
            slug="v-role", visibility="role", name="Staff",
            allowed_roles=["teacher", "admin"],
        )
        self.teacher = make_user("teacher", username="vis_teacher")
        self.student = make_user("student", username="vis_student")

        self.pub_post  = make_post(author=self.teacher, category=self.public_cat, title="Public post")
        self.role_post = make_post(author=self.teacher, category=self.role_cat,   title="Staff post")
        self.pending   = make_post(
            author=self.student, category=self.public_cat,
            title="Pending", status=Post.Status.PENDING,
        )
        self.removed = make_post(
            author=self.teacher, category=self.public_cat,
            title="Removed", status=Post.Status.REMOVED,
        )

    def test_visitor_sees_public_post(self):
        feed = post_feed(ANON)
        titles = [p.title for p in feed["posts"]]
        self.assertIn("Public post", titles)

    def test_visitor_does_not_see_role_post(self):
        feed = post_feed(ANON)
        titles = [p.title for p in feed["posts"]]
        self.assertNotIn("Staff post", titles)

    def test_visitor_never_sees_pending_post(self):
        feed = post_feed(ANON)
        titles = [p.title for p in feed["posts"]]
        self.assertNotIn("Pending", titles)

    def test_visitor_never_sees_removed_post(self):
        feed = post_feed(ANON)
        titles = [p.title for p in feed["posts"]]
        self.assertNotIn("Removed", titles)

    def test_teacher_sees_role_post(self):
        feed = post_feed(self.teacher)
        titles = [p.title for p in feed["posts"]]
        self.assertIn("Staff post", titles)


# ─── Feature gate (HTTP views) ───────────────────────────────────────────────

class FeatureGateTests(TestCase):
    def setUp(self):
        disable_community()

    def tearDown(self):
        # Leave settings clean for other tests
        SchoolSettings.objects.filter(pk=1).update(
            community_enabled=False, community_paused=False
        )

    def _clear_cache(self):
        # school_settings_values is memoised on the request; a fresh client
        # gets a fresh request each time, so no cache clearing needed.
        pass

    def test_home_redirects_to_paused_when_off(self):
        resp = self.client.get(reverse("community:home"))
        self.assertRedirects(resp, reverse("community:paused"), fetch_redirect_response=False)

    def test_events_redirects_when_off(self):
        resp = self.client.get(reverse("community:events"))
        self.assertRedirects(resp, reverse("community:paused"), fetch_redirect_response=False)

    def test_members_redirects_when_off(self):
        resp = self.client.get(reverse("community:members"))
        self.assertRedirects(resp, reverse("community:paused"), fetch_redirect_response=False)

    def test_about_redirects_when_off(self):
        resp = self.client.get(reverse("community:about"))
        self.assertRedirects(resp, reverse("community:paused"), fetch_redirect_response=False)

    def test_paused_page_always_renders(self):
        resp = self.client.get(reverse("community:paused"))
        self.assertEqual(resp.status_code, 200)

    def test_home_redirects_to_paused_when_paused(self):
        enable_community(paused=True)
        resp = self.client.get(reverse("community:home"))
        self.assertRedirects(resp, reverse("community:paused"), fetch_redirect_response=False)

    def test_home_renders_when_enabled(self):
        enable_community()
        resp = self.client.get(reverse("community:home"))
        self.assertEqual(resp.status_code, 200)


# ─── HTTP view responses ──────────────────────────────────────────────────────

class CommunityViewTests(TestCase):
    def setUp(self):
        enable_community()
        self.public_cat = make_category(slug="http-pub", visibility="public", name="HTTP Public")
        self.author = make_user("teacher", username="http_author")
        self.post = make_post(author=self.author, category=self.public_cat, title="Hello world")

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_home_200(self):
        self.assertEqual(self.client.get(reverse("community:home")).status_code, 200)

    def test_events_200(self):
        self.assertEqual(self.client.get(reverse("community:events")).status_code, 200)

    def test_members_200(self):
        self.assertEqual(self.client.get(reverse("community:members")).status_code, 200)

    def test_about_200(self):
        self.assertEqual(self.client.get(reverse("community:about")).status_code, 200)

    def test_search_200(self):
        self.assertEqual(
            self.client.get(reverse("community:search") + "?q=hello").status_code, 200
        )

    def test_post_detail_200(self):
        resp = self.client.get(reverse("community:post_detail", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_post_detail_404_for_pending(self):
        pending = make_post(
            author=self.author, category=self.public_cat,
            title="Hidden", status=Post.Status.PENDING,
        )
        resp = self.client.get(reverse("community:post_detail", args=[pending.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_post_detail_404_for_removed(self):
        removed = make_post(
            author=self.author, category=self.public_cat,
            title="Gone", status=Post.Status.REMOVED,
        )
        resp = self.client.get(reverse("community:post_detail", args=[removed.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_category_feed_200(self):
        resp = self.client.get(
            reverse("community:category_feed", args=[self.public_cat.slug])
        )
        self.assertEqual(resp.status_code, 200)

    def test_category_feed_404_for_invisible(self):
        role_cat = make_category(
            slug="http-role", visibility="role",
            allowed_roles=["admin"], name="Admin only",
        )
        resp = self.client.get(reverse("community:category_feed", args=[role_cat.slug]))
        self.assertEqual(resp.status_code, 404)

    def test_post_title_in_home(self):
        resp = self.client.get(reverse("community:home"))
        self.assertContains(resp, "Hello world")

    def test_post_title_in_detail(self):
        resp = self.client.get(reverse("community:post_detail", args=[self.post.pk]))
        self.assertContains(resp, "Hello world")

    def test_role_restricted_post_not_in_visitor_home(self):
        admin_cat = make_category(
            slug="http-admin", visibility="role",
            allowed_roles=["admin"], name="Admins",
        )
        secret = make_post(author=self.author, category=admin_cat, title="Secret admin post")
        resp = self.client.get(reverse("community:home"))
        self.assertNotContains(resp, "Secret admin post")


# ─── Navbar link ─────────────────────────────────────────────────────────────

class NavbarCommunityLinkTests(TestCase):
    def test_community_link_absent_when_feature_off(self):
        disable_community()
        resp = self.client.get(reverse("public:home"))
        self.assertNotContains(resp, reverse("community:home"))

    def test_community_link_present_when_feature_on(self):
        enable_community()
        resp = self.client.get(reverse("public:home"))
        self.assertContains(resp, reverse("community:home"))
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)


# ─── Search ──────────────────────────────────────────────────────────────────

class SearchTests(TestCase):
    def setUp(self):
        enable_community()
        self.public_cat = make_category(slug="srch-pub", visibility="public",  name="Search Public")
        self.role_cat   = make_category(
            slug="srch-role", visibility="role",
            allowed_roles=["admin"], name="Search Admin",
        )
        self.teacher = make_user("teacher", username="srch_teacher")
        make_post(author=self.teacher, category=self.public_cat, title="Mango season")
        make_post(author=self.teacher, category=self.role_cat,   title="Confidential mango")

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_search_finds_public_post(self):
        results = search_posts("mango", ANON)
        self.assertTrue(any(p.title == "Mango season" for p in results))

    def test_search_hides_restricted_post_from_visitor(self):
        results = search_posts("mango", ANON)
        self.assertFalse(any(p.title == "Confidential mango" for p in results))

    def test_search_view_contains_result(self):
        resp = self.client.get(reverse("community:search") + "?q=mango")
        self.assertContains(resp, "Mango season")
        self.assertNotContains(resp, "Confidential mango")

    def test_empty_query_returns_nothing(self):
        results = search_posts("", ANON)
        self.assertEqual(results, [])

    def test_single_char_query_returns_nothing(self):
        results = search_posts("a", ANON)
        self.assertEqual(results, [])


# ─── Paging ──────────────────────────────────────────────────────────────────

class PagingTests(TestCase):
    def setUp(self):
        self.cat = make_category(slug="page-pub", visibility="public", name="Paging")
        self.teacher = make_user("teacher", username="page_teacher")
        for i in range(PAGE_SIZE + 2):
            make_post(author=self.teacher, category=self.cat, title=f"Post {i}")

    def test_first_page_has_page_size_posts(self):
        feed = post_feed(ANON)
        self.assertEqual(len(feed["posts"]), PAGE_SIZE)

    def test_has_more_is_true_on_first_page(self):
        self.assertTrue(post_feed(ANON)["has_more"])

    def test_next_cursor_is_set(self):
        self.assertIsNotNone(post_feed(ANON)["next_cursor"])

    def test_cursor_fetches_remaining(self):
        first = post_feed(ANON)
        second = post_feed(ANON, cursor=first["next_cursor"])
        self.assertGreaterEqual(len(second["posts"]), 1)

    def test_pinned_floats_first(self):
        # Pin one of the later posts
        last_post = Post.objects.filter(category=self.cat).order_by("created_at").first()
        last_post.is_pinned = True
        last_post.save()
        feed = post_feed(ANON, sort="new")
        self.assertTrue(feed["posts"][0].is_pinned)


# ─── Members list ────────────────────────────────────────────────────────────

class MembersListTests(TestCase):
    def setUp(self):
        self.teacher = make_user("teacher", username="mem_teacher")
        self.student = make_user("student", username="mem_student")
        self.parent  = make_user("parent",  username="mem_parent")

    def test_members_list_excludes_students(self):
        members = members_list(self.teacher)
        roles = [m.role for m in members]
        self.assertNotIn("student", roles)

    def test_members_list_empty_for_visitor(self):
        self.assertEqual(members_list(ANON), [])

    def test_members_list_includes_teacher(self):
        members = members_list(self.teacher)
        self.assertIn(self.teacher, members)


# ─── Student safety ──────────────────────────────────────────────────────────

class StudentSafetyTests(TestCase):
    def setUp(self):
        self.student = make_user(
            "student", username="safety_student",
            first_name="Amara", last_name="Odhiambo",
        )
        self.cat = make_category(slug="safety-pub", visibility="public", name="Safety")
        grant_consent(self.student)
        from django.core.cache import cache
        cache.clear()

    def test_student_display_name_is_first_plus_initial(self):
        post = make_post(author=self.student, category=self.cat)
        self.assertEqual(post.author_display_name, "Amara O.")

    def test_student_post_public_category_pending_when_moderation_on(self):
        enable_community(student_moderation=True)
        post = create_post(
            actor=self.student,
            category=self.cat,
            title="Student post",
            body="Hello",
            student_moderation=True,
        )
        self.assertEqual(post.status, Post.Status.PENDING)
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_student_post_published_when_moderation_off(self):
        enable_community(student_moderation=False)
        post = create_post(
            actor=self.student,
            category=self.cat,
            title="Student post open",
            body="Hello open",
            student_moderation=False,
        )
        self.assertEqual(post.status, Post.Status.PUBLISHED)
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_student_post_in_role_category_published_immediately(self):
        """
        A student technically can't see a ROLE category, so can_post_in_category
        will block them. Confirm that correctly.
        """
        role_cat = make_category(
            slug="safety-role", visibility="role",
            allowed_roles=["teacher"], name="Teachers only",
        )
        allowed, _ = can_post_in_category(self.student, role_cat)
        self.assertFalse(allowed)

    def test_pending_post_invisible_to_visitor(self):
        enable_community()
        pending = make_post(
            author=self.student, category=self.cat,
            title="Waiting approval", status=Post.Status.PENDING,
        )
        resp = self.client.get(reverse("community:home"))
        self.assertNotContains(resp, "Waiting approval")
        resp2 = self.client.get(reverse("community:post_detail", args=[pending.pk]))
        self.assertEqual(resp2.status_code, 404)
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)


# ─── Services ────────────────────────────────────────────────────────────────

class CreatePostTests(TestCase):
    def setUp(self):
        self.public_cat = make_category(slug="svc-pub", visibility="public", name="Service")
        self.teacher = make_user("teacher", username="svc_teacher")
        self.student = make_user("student", username="svc_student")
        grant_consent(self.teacher)
        from django.core.cache import cache
        cache.clear()

    def test_teacher_post_published_immediately(self):
        post = create_post(
            actor=self.teacher, category=self.public_cat,
            title="Teacher post", body="Body", student_moderation=True,
        )
        self.assertEqual(post.status, Post.Status.PUBLISHED)

    def test_script_tags_stripped_from_body(self):
        post = create_post(
            actor=self.teacher, category=self.public_cat,
            title="Safe",
            body='Hello <script>alert("xss")</script> world',
            student_moderation=False,
        )
        self.assertNotIn("<script>", post.body)
        self.assertIn("world", post.body)

    def test_title_stripped_of_html(self):
        post = create_post(
            actor=self.teacher, category=self.public_cat,
            title="<b>Bold title</b>", body="Body", student_moderation=False,
        )
        self.assertNotIn("<b>", post.title)

    def test_cannot_post_in_invisible_category(self):
        role_cat = make_category(
            slug="svc-role", visibility="role",
            allowed_roles=["admin"], name="Admin only",
        )
        with self.assertRaises(PermissionError):
            create_post(
                actor=self.student, category=role_cat,
                title="Nope", body="Nope", student_moderation=False,
            )


class ReactionTests(TestCase):
    def setUp(self):
        self.cat = make_category(slug="react-pub", visibility="public", name="Reactions")
        self.teacher = make_user("teacher", username="react_teacher")
        self.post = make_post(author=self.teacher, category=self.cat)

    def test_reaction_added(self):
        result = toggle_reaction(actor=self.teacher, post=self.post, emoji="👍")
        self.assertTrue(result["added"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(Reaction.objects.filter(post=self.post).count(), 1)

    def test_reaction_removed_on_second_toggle(self):
        toggle_reaction(actor=self.teacher, post=self.post, emoji="👍")
        result = toggle_reaction(actor=self.teacher, post=self.post, emoji="👍")
        self.assertFalse(result["added"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(Reaction.objects.filter(post=self.post).count(), 0)

    def test_different_emoji_allowed(self):
        toggle_reaction(actor=self.teacher, post=self.post, emoji="👍")
        toggle_reaction(actor=self.teacher, post=self.post, emoji="❤️")
        self.assertEqual(Reaction.objects.filter(post=self.post).count(), 2)

    def test_invalid_emoji_raises(self):
        with self.assertRaises(ValueError):
            toggle_reaction(actor=self.teacher, post=self.post, emoji="💩")


# ─── noindex behaviour ───────────────────────────────────────────────────────

class NoindexTests(TestCase):
    """
    The community templates set noindex via a template tag in community/base.html.
    We check the rendered response contains the meta tag content.
    """

    def setUp(self):
        enable_community()

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(
            community_enabled=False, community_index=False
        )

    def test_noindex_meta_present_when_index_off(self):
        SchoolSettings.objects.filter(pk=1).update(community_index=False)
        resp = self.client.get(reverse("community:home"))
        self.assertContains(resp, "noindex")

    def test_noindex_absent_when_index_on(self):
        SchoolSettings.objects.filter(pk=1).update(community_index=True)
        resp = self.client.get(reverse("community:home"))
        self.assertNotContains(resp, "noindex")


# ─── Community stats ─────────────────────────────────────────────────────────

class CommunityStatsTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="stats_admin")
        self.teacher = make_user("teacher", username="stats_teacher")

    def test_total_members_counts_active_users(self):
        stats = community_stats(ANON)
        self.assertGreaterEqual(stats["total_members"], 2)

    def test_admins_count_includes_admin(self):
        stats = community_stats(ANON)
        self.assertGreaterEqual(stats["admins"], 1)

    def test_online_now_starts_at_zero(self):
        stats = community_stats(ANON)
        self.assertGreaterEqual(stats["online_now"], 0)


# ─── Members view: sign-in gate ──────────────────────────────────────────────

class MembersViewGateTests(TestCase):
    def setUp(self):
        enable_community()

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_visitor_sees_counts_not_directory(self):
        resp = self.client.get(reverse("community:members"))
        self.assertEqual(resp.status_code, 200)
        # Visitor gets stats but no member list (gate message shown)
        self.assertContains(resp, "Sign in to view")

    def test_signed_in_user_sees_directory(self):
        teacher = make_user("teacher", username="mem_view_teacher")
        self.client.force_login(teacher)
        resp = self.client.get(reverse("community:members"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Sign in to view")


# ─── Seed data ───────────────────────────────────────────────────────────────

class SeedCategoriesTests(TestCase):
    """The 0002 migration seeds 10 categories. Confirm they're all there."""

    EXPECTED_SLUGS = [
        "announcements",
        "general-discussion",
        "homework-help",
        "wins-and-celebrations",
        "parents-corner",
        "teachers-lounge",
        "events-and-reminders",
        "student-voice",
        "spotlight",
        "resources-and-ideas",
    ]

    def test_all_seed_categories_exist(self):
        for slug in self.EXPECTED_SLUGS:
            self.assertTrue(
                Category.objects.filter(slug=slug).exists(),
                f"Seed category '{slug}' not found",
            )

    def test_teachers_lounge_is_role_restricted(self):
        cat = Category.objects.get(slug="teachers-lounge")
        self.assertEqual(cat.visibility, Category.Visibility.ROLE)
        self.assertIn("teacher", cat.allowed_roles)

    def test_parents_corner_is_signed_in(self):
        cat = Category.objects.get(slug="parents-corner")
        self.assertEqual(cat.visibility, Category.Visibility.SIGNED_IN)

    def test_announcements_is_public(self):
        cat = Category.objects.get(slug="announcements")
        self.assertEqual(cat.visibility, Category.Visibility.PUBLIC)
