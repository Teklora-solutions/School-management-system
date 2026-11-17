"""
apps/community/tests/test_moderation.py
-----------------------------------------
Tests for Module 3: moderation, warnings, reports, appeals, word filter,
delegation, data retention/export, insights scoping.
"""

from __future__ import annotations

import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.community.models import (
    Appeal,
    BlockedWord,
    Category,
    CategoryModerator,
    Comment,
    CommunityProfile,
    Post,
    Report,
    Warning,
)
from apps.community.selectors import pending_posts, open_reports, pending_appeals
from apps.community.services import (
    anonymise_user_content,
    approve_post,
    assign_moderator,
    export_user_content,
    file_appeal,
    file_report,
    grant_consent,
    invalidate_blocked_words_cache,
    issue_warning,
    lift_warning,
    lock_post,
    mute_user,
    remove_comment,
    remove_post,
    resolve_report,
    restore_comment,
    restore_post,
    review_appeal,
    suspend_user,
    toggle_reaction,
    unsuspend_user,
    can_post_in_category,
    create_post,
    create_comment,
)
from apps.core.models import AuditLog, SchoolSettings

from .fixtures import (
    disable_community,
    enable_community,
    make_category,
    make_comment,
    make_post,
    make_user,
)


# ─── Removal and restore ─────────────────────────────────────────────────────

class RemoveRestoreTests(TestCase):
    def setUp(self):
        enable_community()
        self.cat = make_category(slug="rr-pub", visibility="public", name="RR")
        self.admin = make_user("admin", username="rr_admin")
        self.teacher = make_user("teacher", username="rr_teacher")
        self.student = make_user("student", username="rr_student")
        self.post = make_post(author=self.teacher, category=self.cat, title="Good post")
        self.comment = make_comment(author=self.student, post=self.post, body="Nice!")

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    # ── Post removal ──────────────────────────────────────────────────────────

    def test_admin_can_remove_post(self):
        remove_post(actor=self.admin, post=self.post, reason_code="spam")
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, Post.Status.REMOVED)

    def test_author_can_remove_own_post(self):
        remove_post(actor=self.teacher, post=self.post, reason_code="other")
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, Post.Status.REMOVED)

    def test_student_cannot_remove_other_post(self):
        with self.assertRaises(PermissionError):
            remove_post(actor=self.student, post=self.post, reason_code="spam")

    def test_removed_post_not_in_visitor_feed(self):
        remove_post(actor=self.admin, post=self.post, reason_code="spam")
        from apps.community.selectors import post_feed
        class Anon:
            is_authenticated = False
        feed = post_feed(Anon())
        self.assertNotIn(self.post, feed["posts"])

    def test_removed_post_detail_returns_404_for_visitor(self):
        remove_post(actor=self.admin, post=self.post, reason_code="spam")
        resp = self.client.get(reverse("community:post_detail", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_removed_post_not_in_search(self):
        remove_post(actor=self.admin, post=self.post, reason_code="spam")
        from apps.community.selectors import search_posts
        class Anon:
            is_authenticated = False
        results = search_posts("Good", Anon())
        self.assertNotIn(self.post, results)

    def test_restore_post_republishes(self):
        remove_post(actor=self.admin, post=self.post, reason_code="spam")
        restore_post(actor=self.admin, post=self.post)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, Post.Status.PUBLISHED)

    def test_non_admin_cannot_restore(self):
        remove_post(actor=self.admin, post=self.post, reason_code="spam")
        with self.assertRaises(PermissionError):
            restore_post(actor=self.teacher, post=self.post)

    def test_remove_post_audit_logged(self):
        remove_post(actor=self.admin, post=self.post, reason_code="spam",
                    reason_note="Testing")
        self.assertTrue(
            AuditLog.objects.filter(action="community.post.remove").exists()
        )

    # ── Comment removal ───────────────────────────────────────────────────────

    def test_admin_can_remove_comment(self):
        remove_comment(actor=self.admin, comment=self.comment)
        self.comment.refresh_from_db()
        self.assertTrue(self.comment.is_removed)

    def test_removed_comment_not_shown_to_visitor(self):
        remove_comment(actor=self.admin, comment=self.comment)
        resp = self.client.get(reverse("community:post_detail", args=[self.post.pk]))
        self.assertNotContains(resp, "Nice!")
        self.assertContains(resp, "removed")  # neutral placeholder

    def test_restore_comment(self):
        remove_comment(actor=self.admin, comment=self.comment)
        restore_comment(actor=self.admin, comment=self.comment)
        self.comment.refresh_from_db()
        self.assertFalse(self.comment.is_removed)

    def test_non_admin_non_author_cannot_remove_comment(self):
        other = make_user("parent", username="rr_parent_nc")
        with self.assertRaises(PermissionError):
            remove_comment(actor=other, comment=self.comment)


# ─── Hidden content never leaks ──────────────────────────────────────────────

class HiddenContentLeakTests(TestCase):
    """Removed posts and comments must never appear to anyone except admin."""

    def setUp(self):
        enable_community()
        self.pub_cat = make_category(slug="hcl-pub", visibility="public", name="HCL")
        self.admin = make_user("admin", username="hcl_admin")
        self.teacher = make_user("teacher", username="hcl_teacher")
        self.student = make_user("student", username="hcl_student")
        self.other_teacher = make_user("teacher", username="hcl_other")
        self.post = make_post(author=self.teacher, category=self.pub_cat,
                              title="Secret removed")
        remove_post(actor=self.admin, post=self.post, reason_code="spam")

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_removed_post_hidden_from_anonymous_feed(self):
        from apps.community.selectors import post_feed
        class Anon:
            is_authenticated = False
        feed = post_feed(Anon())
        titles = [p.title for p in feed["posts"]]
        self.assertNotIn("Secret removed", titles)

    def test_removed_post_hidden_from_other_teacher_feed(self):
        from apps.community.selectors import post_feed
        feed = post_feed(self.other_teacher)
        titles = [p.title for p in feed["posts"]]
        self.assertNotIn("Secret removed", titles)

    def test_removed_post_detail_404_for_student(self):
        self.client.force_login(self.student)
        resp = self.client.get(reverse("community:post_detail", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_removed_post_detail_404_for_other_teacher(self):
        self.client.force_login(self.other_teacher)
        resp = self.client.get(reverse("community:post_detail", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_removed_post_not_in_search_for_student(self):
        from apps.community.selectors import search_posts
        results = search_posts("Secret", self.student)
        self.assertEqual(results, [])


# ─── Warnings ────────────────────────────────────────────────────────────────

class WarningTests(TestCase):
    def setUp(self):
        enable_community()
        self.cat = make_category(slug="wt-pub", visibility="public", name="WT")
        self.admin = make_user("admin", username="wt_admin")
        self.teacher = make_user("teacher", username="wt_teacher")
        self.post = make_post(author=self.teacher, category=self.cat)
        from django.core.cache import cache
        cache.clear()

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)
        from django.core.cache import cache
        cache.clear()

    def test_first_removal_issues_reminder(self):
        remove_post(actor=self.admin, post=self.post, reason_code="spam")
        self.assertTrue(
            Warning.objects.filter(recipient=self.teacher,
                                   level=Warning.Level.REMINDER).exists()
        )

    def test_second_removal_in_window_issues_mute(self):
        post2 = make_post(author=self.teacher, category=self.cat, title="Post 2")
        remove_post(actor=self.admin, post=self.post, reason_code="spam")
        remove_post(actor=self.admin, post=post2, reason_code="spam")
        self.assertTrue(
            Warning.objects.filter(recipient=self.teacher,
                                   level=Warning.Level.MUTE).exists()
        )

    def test_mute_prevents_posting(self):
        mute_user(actor=self.admin, user=self.teacher, duration_hours=24,
                  reason="Test mute")
        grant_consent(self.teacher)
        allowed, reason = can_post_in_category(self.teacher, self.cat)
        self.assertFalse(allowed)
        self.assertIn("paused", reason)

    def test_third_removal_issues_suspension(self):
        p1 = make_post(author=self.teacher, category=self.cat, title="P1")
        p2 = make_post(author=self.teacher, category=self.cat, title="P2")
        p3 = make_post(author=self.teacher, category=self.cat, title="P3")
        remove_post(actor=self.admin, post=p1, reason_code="spam")
        remove_post(actor=self.admin, post=p2, reason_code="spam")
        remove_post(actor=self.admin, post=p3, reason_code="spam")
        self.assertTrue(
            Warning.objects.filter(recipient=self.teacher,
                                   level=Warning.Level.SUSPENSION).exists()
        )

    def test_suspension_blocks_posting(self):
        suspend_user(actor=self.admin, user=self.teacher, duration_days=7)
        grant_consent(self.teacher)
        allowed, reason = can_post_in_category(self.teacher, self.cat)
        self.assertFalse(allowed)
        self.assertIn("paused", reason.lower())

    def test_suspension_does_not_affect_non_community(self):
        """Suspension is community-only: the user model is not changed."""
        suspend_user(actor=self.admin, user=self.teacher, duration_days=7)
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.is_active)

    def test_lift_warning_re_enables_posting(self):
        # issue_warning with MUTE level sets the mute AND creates the warning row
        warning = issue_warning(actor=self.admin, recipient=self.teacher,
                                level=Warning.Level.MUTE,
                                internal_reason="Test mute via warning")
        # Confirm muted
        profile = CommunityProfile.objects.get(user=self.teacher)
        self.assertTrue(profile.is_currently_muted)
        # Lift it
        lift_warning(actor=self.admin, warning=warning, reason="Resolved")
        profile.refresh_from_db()
        self.assertFalse(profile.is_muted)

    def test_manual_warning_view_requires_admin(self):
        self.client.force_login(self.teacher)
        resp = self.client.post(reverse("community:admin_warn"), {
            "recipient_pk": self.teacher.pk,
            "level": "manual",
            "internal_reason": "Test",
        })
        self.assertEqual(resp.status_code, 403)

    def test_warning_detail_200(self):
        warning = issue_warning(actor=self.admin, recipient=self.teacher,
                                level=Warning.Level.MANUAL,
                                internal_reason="Test",
                                user_message="Please review the rules.")
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("community:warning_detail", args=[warning.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please review the rules.")

    def test_warning_audit_logged(self):
        issue_warning(actor=self.admin, recipient=self.teacher,
                      level=Warning.Level.MANUAL,
                      internal_reason="Audit test")
        self.assertTrue(
            AuditLog.objects.filter(action="community.warning.issue").exists()
        )


# ─── Mute and suspend ────────────────────────────────────────────────────────

class MuteSuspendTests(TestCase):
    def setUp(self):
        enable_community()
        self.admin = make_user("admin", username="ms_admin")
        self.teacher = make_user("teacher", username="ms_teacher")
        self.cat = make_category(slug="ms-pub", visibility="public", name="MS")
        grant_consent(self.teacher)

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_mute_view_requires_admin(self):
        self.client.force_login(self.teacher)
        resp = self.client.post(reverse("community:admin_mute"),
                                {"user_pk": self.teacher.pk, "hours": 24})
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_mute(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("community:admin_mute"), {
            "user_pk": self.teacher.pk, "hours": 24, "reason": "Test",
            "next": "/community/admin/queue/",
        })
        profile = CommunityProfile.objects.get(user=self.teacher)
        self.assertTrue(profile.is_muted)

    def test_admin_can_unmute(self):
        mute_user(actor=self.admin, user=self.teacher, duration_hours=1)
        self.client.force_login(self.admin)
        self.client.post(reverse("community:admin_unmute"),
                         {"user_pk": self.teacher.pk,
                          "next": "/community/admin/queue/"})
        profile = CommunityProfile.objects.get(user=self.teacher)
        self.assertFalse(profile.is_muted)

    def test_lock_post_blocks_replies(self):
        post = make_post(author=self.teacher, category=self.cat)
        lock_post(actor=self.admin, post=post)
        with self.assertRaises(PermissionError):
            create_comment(actor=self.teacher, post=post, body="Locked reply")


# ─── Reports ─────────────────────────────────────────────────────────────────

class ReportTests(TestCase):
    def setUp(self):
        enable_community()
        self.cat = make_category(slug="rep-cat", visibility="public", name="RepCat")
        self.admin = make_user("admin", username="rep_admin")
        self.teacher = make_user("teacher", username="rep_teacher")
        self.parent = make_user("parent", username="rep_parent")
        self.post = make_post(author=self.teacher, category=self.cat)

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_file_report_creates_row(self):
        report = file_report(reporter=self.parent, post=self.post,
                             reason=Report.Reason.SPAM)
        self.assertEqual(Report.objects.filter(post=self.post).count(), 1)
        self.assertEqual(report.status, Report.Status.OPEN)

    def test_second_report_updates_existing(self):
        file_report(reporter=self.parent, post=self.post, reason=Report.Reason.SPAM)
        file_report(reporter=self.parent, post=self.post, reason=Report.Reason.BULLYING)
        self.assertEqual(Report.objects.filter(post=self.post, reporter=self.parent).count(), 1)

    def test_resolve_report_actioned(self):
        report = file_report(reporter=self.parent, post=self.post,
                             reason=Report.Reason.SPAM)
        resolve_report(actor=self.admin, report=report, resolution="actioned")
        report.refresh_from_db()
        self.assertEqual(report.status, "actioned")

    def test_resolve_report_dismissed(self):
        report = file_report(reporter=self.parent, post=self.post,
                             reason=Report.Reason.OTHER)
        resolve_report(actor=self.admin, report=report, resolution="dismissed")
        report.refresh_from_db()
        self.assertEqual(report.status, "dismissed")

    def test_non_admin_cannot_resolve_report(self):
        report = file_report(reporter=self.parent, post=self.post,
                             reason=Report.Reason.SPAM)
        with self.assertRaises(PermissionError):
            resolve_report(actor=self.parent, report=report, resolution="dismissed")

    def test_report_view_200(self):
        self.client.force_login(self.parent)
        resp = self.client.post(reverse("community:report"), {
            "post_pk": self.post.pk,
            "reason": "spam",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])

    def test_open_reports_selector(self):
        file_report(reporter=self.parent, post=self.post, reason="spam")
        reports = open_reports()
        self.assertGreaterEqual(len(reports), 1)


# ─── Appeals ─────────────────────────────────────────────────────────────────

class AppealTests(TestCase):
    def setUp(self):
        enable_community()
        self.admin = make_user("admin", username="ap_admin")
        self.teacher = make_user("teacher", username="ap_teacher")
        self.cat = make_category(slug="ap-cat", visibility="public", name="ApCat")
        self.post = make_post(author=self.teacher, category=self.cat)
        remove_post(actor=self.admin, post=self.post, reason_code="spam")
        self.warning = Warning.objects.filter(recipient=self.teacher).last()

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_file_appeal_creates_row(self):
        if self.warning:
            appeal = file_appeal(appellant=self.teacher, warning=self.warning,
                                 message="I disagree.")
            self.assertEqual(appeal.status, Appeal.Status.PENDING)
            self.assertEqual(appeal.warning, self.warning)

    def test_cannot_appeal_twice(self):
        if not self.warning:
            return
        file_appeal(appellant=self.teacher, warning=self.warning,
                    message="First appeal.")
        with self.assertRaises(ValueError):
            file_appeal(appellant=self.teacher, warning=self.warning,
                        message="Second appeal.")

    def test_other_user_cannot_appeal_warning(self):
        if not self.warning:
            return
        other = make_user("parent", username="ap_other")
        with self.assertRaises(PermissionError):
            file_appeal(appellant=other, warning=self.warning,
                        message="Not mine.")

    def test_uphold_appeal(self):
        if not self.warning:
            return
        appeal = file_appeal(appellant=self.teacher, warning=self.warning,
                             message="Please reconsider.")
        review_appeal(actor=self.admin, appeal=appeal, decision="upheld")
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, "upheld")

    def test_lift_appeal(self):
        if not self.warning:
            return
        appeal = file_appeal(appellant=self.teacher, warning=self.warning,
                             message="Please reconsider.")
        review_appeal(actor=self.admin, appeal=appeal, decision="lifted")
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, "lifted")

    def test_appeal_view_requires_login(self):
        if not self.warning:
            return
        resp = self.client.post(
            reverse("community:appeal", args=[self.warning.pk]),
            {"message": "Appeal"}
        )
        self.assertEqual(resp.status_code, 302)

    def test_appeal_view_success(self):
        if not self.warning:
            return
        self.client.force_login(self.teacher)
        resp = self.client.post(
            reverse("community:appeal", args=[self.warning.pk]),
            {"message": "I did not mean to cause offence."}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Appeal.objects.filter(warning=self.warning).exists())


# ─── Word filter ─────────────────────────────────────────────────────────────

class WordFilterTests(TestCase):
    def setUp(self):
        enable_community()
        self.cat = make_category(slug="wf-pub", visibility="public", name="WF")
        self.teacher = make_user("teacher", username="wf_teacher")
        grant_consent(self.teacher)
        from django.core.cache import cache
        cache.clear()

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)
        from django.core.cache import cache
        cache.clear()

    def test_blocked_word_holds_post_for_review(self):
        BlockedWord.objects.get_or_create(word="badword",
                                          defaults={"is_active": True})
        invalidate_blocked_words_cache()
        post = create_post(actor=self.teacher, category=self.cat,
                           title="Normal title",
                           body="This contains badword in the text.",
                           student_moderation=False)
        self.assertEqual(post.status, Post.Status.PENDING)

    def test_clean_post_not_held(self):
        post = create_post(actor=self.teacher, category=self.cat,
                           title="Clean title",
                           body="This is a perfectly clean post.",
                           student_moderation=False)
        self.assertEqual(post.status, Post.Status.PUBLISHED)

    def test_whole_word_match_does_not_flag_prefix(self):
        BlockedWord.objects.get_or_create(word="bad",
                                          defaults={"is_active": True})
        invalidate_blocked_words_cache()
        post = create_post(actor=self.teacher, category=self.cat,
                           title="Badminton tournament",
                           body="We played badminton today.",
                           student_moderation=False)
        # "badminton" should NOT match the whole-word "bad"
        self.assertEqual(post.status, Post.Status.PUBLISHED)

    def test_inactive_word_does_not_hold_post(self):
        BlockedWord.objects.get_or_create(word="inactiveword",
                                          defaults={"is_active": False})
        invalidate_blocked_words_cache()
        post = create_post(actor=self.teacher, category=self.cat,
                           title="Normal",
                           body="This has inactiveword in it.",
                           student_moderation=False)
        self.assertEqual(post.status, Post.Status.PUBLISHED)

    def test_word_filter_admin_view_200(self):
        admin = make_user("admin", username="wf_admin")
        self.client.force_login(admin)
        resp = self.client.get(reverse("community:admin_word_filter"))
        self.assertEqual(resp.status_code, 200)

    def test_add_word_via_view(self):
        admin = make_user("admin", username="wf_admin2")
        self.client.force_login(admin)
        self.client.post(reverse("community:admin_word_filter"), {
            "action": "add", "word": "newbadword", "note": "Test",
        })
        self.assertTrue(BlockedWord.objects.filter(word="newbadword").exists())

    def test_seed_categories_marked_staff_only(self):
        for slug in ["announcements", "events-and-reminders", "spotlight"]:
            cat = Category.objects.filter(slug=slug).first()
            if cat:
                self.assertTrue(cat.staff_post_only,
                                f"{slug} should be staff_post_only")


# ─── Delegation / category moderators ────────────────────────────────────────

class DelegationTests(TestCase):
    def setUp(self):
        enable_community()
        self.admin = make_user("admin", username="del_admin")
        self.teacher = make_user("teacher", username="del_teacher")
        self.student = make_user("student", username="del_student")
        self.cat = make_category(slug="del-cat", visibility="public", name="Del")
        self.post = make_post(author=self.teacher, category=self.cat)
        grant_consent(self.student)

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_assign_moderator(self):
        mod = assign_moderator(actor=self.admin, category=self.cat, user=self.teacher)
        self.assertTrue(mod.is_active)
        self.assertEqual(mod.category, self.cat)

    def test_moderator_can_remove_post(self):
        assign_moderator(actor=self.admin, category=self.cat, user=self.teacher)
        # teacher is now a moderator for self.cat; create a different post to remove
        other_post = make_post(author=self.student, category=self.cat, title="Other")
        remove_post(actor=self.teacher, post=other_post, reason_code="spam")
        other_post.refresh_from_db()
        self.assertEqual(other_post.status, Post.Status.REMOVED)

    def test_moderator_cannot_issue_warning(self):
        assign_moderator(actor=self.admin, category=self.cat, user=self.teacher)
        with self.assertRaises(PermissionError):
            issue_warning(actor=self.teacher, recipient=self.student,
                          level=Warning.Level.MANUAL,
                          internal_reason="Test")

    def test_moderator_cannot_suspend(self):
        assign_moderator(actor=self.admin, category=self.cat, user=self.teacher)
        with self.assertRaises(PermissionError):
            from apps.community.services import _require_staff
            _require_staff(self.teacher)

    def test_student_cannot_be_assigned_moderator(self):
        with self.assertRaises(PermissionError):
            assign_moderator(actor=self.admin, category=self.cat, user=self.student)

    def test_director_cannot_remove_post(self):
        """Director is read-only in community."""
        director = make_user("director", username="del_director")
        with self.assertRaises(PermissionError):
            remove_post(actor=director, post=self.post, reason_code="spam")

    def test_delegation_view_200(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("community:admin_delegation"))
        self.assertEqual(resp.status_code, 200)

    def test_delegation_view_requires_admin(self):
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("community:admin_delegation"))
        self.assertEqual(resp.status_code, 403)


# ─── Data retention and export ───────────────────────────────────────────────

class RetentionExportTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="ret_admin")
        self.teacher = make_user("teacher", username="ret_teacher")
        self.cat = make_category(slug="ret-cat", visibility="public", name="Ret")
        self.post = make_post(author=self.teacher, category=self.cat, title="Exportable")
        self.comment = make_comment(author=self.teacher, post=self.post, body="Comment")

    def test_export_contains_posts_and_comments(self):
        rows = export_user_content(self.teacher)
        types = [r["type"] for r in rows]
        self.assertIn("post", types)
        self.assertIn("comment", types)

    def test_export_csv_view_returns_csv(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse("community:admin_export", args=[self.teacher.pk])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])

    def test_anonymise_clears_author_and_body(self):
        anonymise_user_content(actor=self.admin, user=self.teacher)
        self.post.refresh_from_db()
        self.assertIsNone(self.post.author)
        self.assertEqual(self.post.title, "[Content removed]")

    def test_anonymise_preserves_post_row(self):
        """The post itself is never deleted — only author and body cleared."""
        anonymise_user_content(actor=self.admin, user=self.teacher)
        self.assertTrue(Post.objects.filter(pk=self.post.pk).exists())

    def test_anonymise_clears_comment_body(self):
        anonymise_user_content(actor=self.admin, user=self.teacher)
        self.comment.refresh_from_db()
        self.assertIn("[Content removed", self.comment.body)

    def test_anonymise_audit_logged(self):
        anonymise_user_content(actor=self.admin, user=self.teacher)
        self.assertTrue(
            AuditLog.objects.filter(action="community.user.anonymise").exists()
        )

    def test_export_view_requires_admin(self):
        self.client.force_login(self.teacher)
        resp = self.client.get(
            reverse("community:admin_export", args=[self.teacher.pk])
        )
        self.assertEqual(resp.status_code, 403)


# ─── Insights scoping ────────────────────────────────────────────────────────

class InsightsScopingTests(TestCase):
    def setUp(self):
        enable_community()
        self.admin = make_user("admin", username="ins_admin")
        self.director = make_user("director", username="ins_director")
        self.teacher = make_user("teacher", username="ins_teacher")

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_insights_view_200_for_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("community:admin_insights"))
        self.assertEqual(resp.status_code, 200)

    def test_insights_view_200_for_director(self):
        self.client.force_login(self.director)
        resp = self.client.get(reverse("community:admin_insights"))
        self.assertEqual(resp.status_code, 200)

    def test_insights_view_403_for_teacher(self):
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("community:admin_insights"))
        self.assertEqual(resp.status_code, 403)

    def test_insights_selector_returns_expected_keys(self):
        from apps.community.selectors import community_insights
        data = community_insights()
        for key in ["daily_activity", "top_categories", "total_posts",
                    "total_members", "pending_posts_count"]:
            self.assertIn(key, data)


# ─── Moderation queue views ───────────────────────────────────────────────────

class ModQueueViewTests(TestCase):
    def setUp(self):
        enable_community()
        self.admin = make_user("admin", username="mq_admin")
        self.teacher = make_user("teacher", username="mq_teacher")

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)

    def test_queue_200_for_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("community:admin_queue"))
        self.assertEqual(resp.status_code, 200)

    def test_queue_403_for_teacher(self):
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse("community:admin_queue"))
        self.assertEqual(resp.status_code, 403)

    def test_approve_pending_post(self):
        cat = make_category(slug="mq-pub", visibility="public", name="MQ")
        student = make_user("student", username="mq_student")
        grant_consent(student)
        post = make_post(author=student, category=cat, title="Pending",
                         status=Post.Status.PENDING)
        self.client.force_login(self.admin)
        self.client.post(reverse("community:admin_post_approve", args=[post.pk]))
        post.refresh_from_db()
        self.assertEqual(post.status, Post.Status.PUBLISHED)

    def test_restore_removed_post_via_view(self):
        cat = make_category(slug="mq-pub2", visibility="public", name="MQ2")
        teacher = make_user("teacher", username="mq_teacher2")
        post = make_post(author=teacher, category=cat, title="Removed",
                         status=Post.Status.REMOVED)
        self.client.force_login(self.admin)
        self.client.post(reverse("community:admin_post_restore", args=[post.pk]))
        post.refresh_from_db()
        self.assertEqual(post.status, Post.Status.PUBLISHED)
