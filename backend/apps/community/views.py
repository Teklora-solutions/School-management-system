"""
apps/community/views.py
------------------------
All community views: public pages (M1), participation (M2),
moderation and admin (M3).

URL namespace: community:
Admin views are under community:admin_* and require admin or director role.
Moderator views require admin, director, or category-moderator role.
"""

from __future__ import annotations

import csv
import io
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.core.mixins import RoleRequiredMixin
from apps.core.selectors import school_settings_values

from . import selectors, services
from .models import (
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

# ─── Shared helpers ───────────────────────────────────────────────────────────

def _community_settings(request):
    memo = getattr(request, "_community_settings", None)
    if memo is not None:
        return memo
    sv = school_settings_values(request)
    result = {
        "enabled": sv.get("community_enabled", False),
        "paused": sv.get("community_paused", False),
        "allow_index": sv.get("community_index", False),
        "student_moderation": sv.get("community_student_moderation", True),
    }
    request._community_settings = result
    return result


def _wants_json(request) -> bool:
    return (request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.headers.get("Accept", "").startswith("application/json"))


class CommunityGateMixin:
    def dispatch(self, request, *args, **kwargs):
        cs = _community_settings(request)
        if not cs["enabled"] or cs["paused"]:
            if isinstance(self, CommunityPausedView):
                return super().dispatch(request, *args, **kwargs)
            return redirect(reverse("community:paused"))
        return super().dispatch(request, *args, **kwargs)


class CommunityContextMixin:
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.public.selectors import nav_links, site_identity
        ctx["identity"] = site_identity(self.request)
        ctx["nav_links"] = nav_links(self.request)
        ctx["cs"] = _community_settings(self.request)
        ctx["categories"] = selectors.visible_categories(self.request.user)
        ctx["stats"] = selectors.community_stats(self.request.user)
        ctx["active_category_slug"] = self.kwargs.get(
            "category_slug", self.request.GET.get("cat", "all")
        )
        if self.request.user.is_authenticated:
            services.touch_profile(self.request.user)
            # Pending warning count for the bell badge
            ctx["pending_warning_count"] = Warning.objects.filter(
                recipient=self.request.user,
                status=Warning.Status.ACTIVE,
            ).count()
        return ctx


# ─── Paused / off ─────────────────────────────────────────────────────────────

class CommunityPausedView(TemplateView):
    template_name = "community/paused.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.public.selectors import nav_links, site_identity
        ctx["identity"] = site_identity(self.request)
        ctx["nav_links"] = nav_links(self.request)
        ctx["cs"] = _community_settings(self.request)
        return ctx


# ─── Home / feed ──────────────────────────────────────────────────────────────

class CommunityHomeView(CommunityGateMixin, CommunityContextMixin, TemplateView):
    template_name = "community/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        req = self.request
        sort = req.GET.get("sort", "new")
        if sort not in ("new", "top", "pinned"):
            sort = "new"
        try:
            cursor = int(req.GET.get("cursor", 0)) or None
        except (ValueError, TypeError):
            cursor = None
        cat_slug = req.GET.get("cat", "all")
        feed = selectors.post_feed(
            req.user,
            category_slug=cat_slug if cat_slug != "all" else None,
            sort=sort, cursor=cursor,
        )
        ctx.update(feed)
        ctx["sort"] = sort
        ctx["active_category_slug"] = cat_slug
        ctx["active_tab"] = "community"
        if cursor is None and cat_slug == "all" and sort != "pinned":
            ctx["pinned_posts"] = selectors.post_feed(req.user, sort="pinned")["posts"]
        else:
            ctx["pinned_posts"] = []
        # Composer state
        if req.user.is_authenticated:
            ctx["draft"] = services.get_draft(req.user)
            ctx["postable_categories"] = selectors.postable_categories(req.user)
        return ctx


class CategoryFeedView(CommunityGateMixin, CommunityContextMixin, TemplateView):
    template_name = "community/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        req = self.request
        slug = self.kwargs["category_slug"]
        category = selectors.category_by_slug(slug, req.user)
        if category is None:
            raise Http404
        sort = req.GET.get("sort", "new")
        if sort not in ("new", "top", "pinned"):
            sort = "new"
        try:
            cursor = int(req.GET.get("cursor", 0)) or None
        except (ValueError, TypeError):
            cursor = None
        feed = selectors.post_feed(req.user, category_slug=slug, sort=sort, cursor=cursor)
        ctx.update(feed)
        ctx["sort"] = sort
        ctx["active_category_slug"] = slug
        ctx["current_category"] = category
        ctx["pinned_posts"] = []
        ctx["active_tab"] = "community"
        if req.user.is_authenticated:
            ctx["draft"] = services.get_draft(req.user)
            ctx["postable_categories"] = selectors.postable_categories(req.user)
        return ctx


# ─── Post detail ─────────────────────────────────────────────────────────────

class PostDetailView(CommunityGateMixin, CommunityContextMixin, TemplateView):
    template_name = "community/post_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        post = selectors.post_detail(pk=self.kwargs["pk"], user=self.request.user)
        if post is None:
            raise Http404
        ctx["post"] = post
        ctx["comments"] = post._comments_list
        ctx["reactions"] = post._reactions_by_emoji
        ctx["active_tab"] = "community"
        ctx["share_url"] = self.request.build_absolute_uri(post.share_path)
        # Can the signed-in user reply?
        if self.request.user.is_authenticated:
            can_reply, reply_reason = services.can_post_in_category(
                self.request.user, post.category
            )
            ctx["can_reply"] = can_reply and not post.is_locked
            ctx["reply_blocked_reason"] = reply_reason if not can_reply else (
                "This thread is locked." if post.is_locked else ""
            )
        # Can the user report?
        ctx["can_report"] = self.request.user.is_authenticated
        # Can the user edit / remove?
        user = self.request.user
        if user.is_authenticated:
            ctx["can_edit_post"] = (
                post.author_id == user.pk
                and (timezone.now() - post.created_at).total_seconds() < 900
            )
            ctx["can_remove_post"] = (
                post.author_id == user.pk
                or getattr(user, "role", "") in ("admin", "director")
            )
        return ctx


# ─── Post submit (M2) ────────────────────────────────────────────────────────

class PostCreateView(CommunityGateMixin, LoginRequiredMixin, View):
    """POST-only: create a new post. Returns JSON for AJAX, redirect otherwise."""

    def post(self, request):
        cs = _community_settings(request)
        category_slug = request.POST.get("category", "")
        title = request.POST.get("title", "").strip()
        body = request.POST.get("body", "").strip()

        # Consent gate
        allowed, reason = _check_consent_or_gate(request.user)
        if not allowed:
            if reason == "consent_required":
                return _gate_response(request, "consent_required",
                                      redirect_url=reverse("community:consent"))
            return _gate_response(request, reason)

        category = selectors.category_by_slug(category_slug, request.user)
        if not category:
            return _gate_response(request, "Invalid category.")

        if not title:
            return _gate_response(request, "Please give your post a title.")
        if not body:
            return _gate_response(request, "Please write something in the body.")

        try:
            post = services.create_post(
                actor=request.user,
                category=category,
                title=title,
                body=body,
                student_moderation=cs["student_moderation"],
                request=request,
            )
        except (PermissionError, ValueError) as exc:
            return _gate_response(request, str(exc))

        if post.status == Post.Status.PENDING:
            msg = (
                "Your post has been submitted and is waiting for a quick review. "
                "We'll publish it very soon — thank you for sharing!"
            )
        else:
            msg = "Your post is live!"

        if _wants_json(request):
            return JsonResponse({
                "ok": True,
                "message": msg,
                "redirect": reverse("community:post_detail", args=[post.pk]),
            })
        messages.success(request, msg)
        return redirect(reverse("community:post_detail", args=[post.pk]))


class PostEditView(CommunityGateMixin, LoginRequiredMixin, View):
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        try:
            services.edit_post(
                actor=request.user,
                post=post,
                title=request.POST.get("title"),
                body=request.POST.get("body"),
                request=request,
            )
        except (PermissionError, ValueError) as exc:
            return _gate_response(request, str(exc))
        if _wants_json(request):
            return JsonResponse({"ok": True})
        return redirect(reverse("community:post_detail", args=[post.pk]))


class PostRemoveView(CommunityGateMixin, LoginRequiredMixin, View):
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        reason_code = request.POST.get("reason_code", "other")
        reason_note = request.POST.get("reason_note", "")
        try:
            services.remove_post(actor=request.user, post=post,
                                 reason_code=reason_code,
                                 reason_note=reason_note, request=request)
        except PermissionError as exc:
            return _gate_response(request, str(exc))
        if _wants_json(request):
            return JsonResponse({"ok": True})
        messages.success(request, "The post has been removed.")
        return redirect(reverse("community:home"))


# ─── Comment submit (M2) ─────────────────────────────────────────────────────

class CommentCreateView(CommunityGateMixin, LoginRequiredMixin, View):
    def post(self, request, post_pk):
        post = get_object_or_404(Post, pk=post_pk, status=Post.Status.PUBLISHED)
        body = request.POST.get("body", "").strip()

        allowed, reason = _check_consent_or_gate(request.user)
        if not allowed:
            if reason == "consent_required":
                return _gate_response(request, "consent_required",
                                      redirect_url=reverse("community:consent"))
            return _gate_response(request, reason)

        if not body:
            return _gate_response(request, "Please write something before replying.")

        try:
            comment = services.create_comment(
                actor=request.user, post=post, body=body, request=request
            )
        except (PermissionError, ValueError) as exc:
            return _gate_response(request, str(exc))

        if _wants_json(request):
            return JsonResponse({
                "ok": True,
                "comment_pk": comment.pk,
                "redirect": reverse("community:post_detail", args=[post.pk]) + "#comments",
            })
        return redirect(reverse("community:post_detail", args=[post.pk]) + "#comments")


class CommentEditView(CommunityGateMixin, LoginRequiredMixin, View):
    def post(self, request, pk):
        comment = get_object_or_404(Comment, pk=pk)
        body = request.POST.get("body", "").strip()
        try:
            services.edit_comment(actor=request.user, comment=comment,
                                  body=body, request=request)
        except (PermissionError, ValueError) as exc:
            return _gate_response(request, str(exc))
        if _wants_json(request):
            return JsonResponse({"ok": True})
        return redirect(reverse("community:post_detail", args=[comment.post_id]) + "#comments")


class CommentRemoveView(CommunityGateMixin, LoginRequiredMixin, View):
    def post(self, request, pk):
        comment = get_object_or_404(Comment, pk=pk)
        try:
            services.remove_comment(actor=request.user, comment=comment,
                                    reason_code=request.POST.get("reason_code", "other"),
                                    reason_note=request.POST.get("reason_note", ""),
                                    request=request)
        except PermissionError as exc:
            return _gate_response(request, str(exc))
        if _wants_json(request):
            return JsonResponse({"ok": True})
        return redirect(reverse("community:post_detail", args=[comment.post_id]) + "#comments")


# ─── Reactions (AJAX, M2) ────────────────────────────────────────────────────

class ReactionToggleView(CommunityGateMixin, LoginRequiredMixin, View):
    def post(self, request, post_pk):
        post = get_object_or_404(Post, pk=post_pk)
        emoji = request.POST.get("emoji", "")
        try:
            result = services.toggle_reaction(actor=request.user, post=post, emoji=emoji)
        except (PermissionError, ValueError) as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        return JsonResponse({"ok": True, **result})


# ─── Draft autosave (AJAX, M2) ───────────────────────────────────────────────

class DraftSaveView(LoginRequiredMixin, View):
    def post(self, request):
        body_data = request.body
        try:
            data = json.loads(body_data)
        except (json.JSONDecodeError, ValueError):
            data = {}
        services.save_draft(
            user=request.user,
            title=data.get("title", ""),
            body=data.get("body", ""),
            category_id=data.get("category_id") or None,
        )
        return JsonResponse({"ok": True})


class DraftClearView(LoginRequiredMixin, View):
    def post(self, request):
        services.clear_draft(request.user)
        return JsonResponse({"ok": True})


# ─── Consent gate (M2) ───────────────────────────────────────────────────────

class ConsentView(CommunityGateMixin, LoginRequiredMixin, TemplateView):
    template_name = "community/consent.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.public.selectors import nav_links, site_identity
        ctx["identity"] = site_identity(self.request)
        ctx["nav_links"] = nav_links(self.request)
        ctx["cs"] = _community_settings(self.request)
        ctx["next"] = self.request.GET.get("next", reverse("community:home"))
        return ctx

    def post(self, request):
        if request.POST.get("consent") == "yes":
            services.grant_consent(request.user)
            next_url = request.POST.get("next", reverse("community:home"))
            return redirect(next_url)
        return redirect(reverse("community:home"))


# ─── My posts (M2) ───────────────────────────────────────────────────────────

class MyPostsView(CommunityGateMixin, LoginRequiredMixin, CommunityContextMixin, TemplateView):
    template_name = "community/my_posts.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["my_posts"] = selectors.user_posts(self.request.user)
        ctx["my_warnings"] = Warning.objects.filter(
            recipient=self.request.user
        ).order_by("-created_at")[:10]
        ctx["active_tab"] = "community"
        return ctx


# ─── Report (M3) ─────────────────────────────────────────────────────────────

class ReportView(CommunityGateMixin, LoginRequiredMixin, View):
    def post(self, request):
        post_pk = request.POST.get("post_pk")
        comment_pk = request.POST.get("comment_pk")
        reason = request.POST.get("reason", Report.Reason.OTHER)
        detail = request.POST.get("detail", "")

        post_obj = get_object_or_404(Post, pk=post_pk) if post_pk else None
        comment_obj = get_object_or_404(Comment, pk=comment_pk) if comment_pk else None

        try:
            services.file_report(
                reporter=request.user, post=post_obj,
                comment=comment_obj, reason=reason, detail=detail,
            )
        except ValueError as exc:
            return _gate_response(request, str(exc))

        if _wants_json(request):
            return JsonResponse({"ok": True, "message": "Thank you — your report has been submitted."})
        messages.success(request, "Thank you — your report has been submitted.")
        redirect_to = reverse("community:post_detail", args=[post_pk or comment_obj.post_id])
        return redirect(redirect_to)


# ─── Warning detail / appeal (M3) ────────────────────────────────────────────

class WarningDetailView(LoginRequiredMixin, TemplateView):
    template_name = "community/warning_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.public.selectors import nav_links, site_identity
        warning = get_object_or_404(Warning, pk=self.kwargs["pk"],
                                    recipient=self.request.user)
        ctx["warning"] = warning
        ctx["can_appeal"] = (
            warning.status == Warning.Status.ACTIVE
            and not hasattr(warning, "appeal")
        )
        ctx["identity"] = site_identity(self.request)
        ctx["nav_links"] = nav_links(self.request)
        ctx["cs"] = _community_settings(self.request)
        return ctx


class AppealView(LoginRequiredMixin, View):
    def post(self, request, warning_pk):
        warning = get_object_or_404(Warning, pk=warning_pk, recipient=request.user)
        message = request.POST.get("message", "").strip()
        if not message:
            messages.error(request, "Please write a message for your appeal.")
            return redirect(reverse("community:warning_detail", args=[warning_pk]))
        try:
            services.file_appeal(appellant=request.user, warning=warning, message=message)
        except (PermissionError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect(reverse("community:warning_detail", args=[warning_pk]))
        messages.success(request, "Your appeal has been submitted. We'll review it shortly.")
        return redirect(reverse("community:my_posts"))


# ─── Events / Members / About / Search (unchanged from M1) ───────────────────

class CommunityEventsView(CommunityGateMixin, CommunityContextMixin, TemplateView):
    template_name = "community/events.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.public.selectors import active_event_items
        ctx["event_items"] = active_event_items()
        ctx["active_tab"] = "events"
        return ctx


class CommunityMembersView(CommunityGateMixin, CommunityContextMixin, TemplateView):
    template_name = "community/members.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        q = self.request.GET.get("q", "").strip()
        ctx["members"] = selectors.members_list(self.request.user, q=q)
        ctx["q"] = q
        ctx["active_tab"] = "members"
        return ctx


class CommunityAboutView(CommunityGateMixin, CommunityContextMixin, TemplateView):
    template_name = "community/about.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_tab"] = "about"
        ctx["all_categories"] = selectors.visible_categories(self.request.user)
        return ctx


class CommunitySearchView(CommunityGateMixin, CommunityContextMixin, TemplateView):
    template_name = "community/search.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        q = self.request.GET.get("q", "").strip()
        ctx["q"] = q
        ctx["results"] = selectors.search_posts(q, self.request.user) if q else []
        ctx["active_tab"] = "community"
        return ctx


# ─── Admin: moderation queue (M3) ────────────────────────────────────────────

class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = ["admin", "director"]


class ModQueueView(AdminRequiredMixin, TemplateView):
    template_name = "community/admin/mod_queue.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.public.selectors import site_identity
        ctx["identity"] = site_identity(self.request)
        ctx["pending_posts"] = selectors.pending_posts()
        ctx["open_reports"] = selectors.open_reports()
        ctx["active_appeals"] = selectors.pending_appeals()
        ctx["removal_reasons"] = [
            ("spam", "Spam or advertising"),
            ("inappropriate", "Inappropriate content"),
            ("bullying", "Bullying or harassment"),
            ("safety", "Safety concern"),
            ("misinformation", "Misinformation"),
            ("wrong_category", "Wrong category"),
            ("other", "Other"),
        ]
        return ctx


class PostApproveView(AdminRequiredMixin, View):
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        try:
            services.approve_post(actor=request.user, post=post, request=request)
        except (PermissionError, ValueError) as exc:
            return _gate_response(request, str(exc))
        if _wants_json(request):
            return JsonResponse({"ok": True})
        messages.success(request, f'"{post.title}" approved and published.')
        return redirect(reverse("community:admin_queue"))


class PostRestoreView(AdminRequiredMixin, View):
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        try:
            services.restore_post(actor=request.user, post=post, request=request)
        except (PermissionError, ValueError) as exc:
            return _gate_response(request, str(exc))
        if _wants_json(request):
            return JsonResponse({"ok": True})
        messages.success(request, "Post restored.")
        return redirect(reverse("community:admin_queue"))


class PostPinView(AdminRequiredMixin, View):
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        pin_order = int(request.POST.get("pin_order", 0))
        services.pin_post(actor=request.user, post=post, pin_order=pin_order, request=request)
        if _wants_json(request):
            return JsonResponse({"ok": True})
        return redirect(reverse("community:post_detail", args=[pk]))


class PostUnpinView(AdminRequiredMixin, View):
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        services.unpin_post(actor=request.user, post=post, request=request)
        if _wants_json(request):
            return JsonResponse({"ok": True})
        return redirect(reverse("community:post_detail", args=[pk]))


class PostLockView(AdminRequiredMixin, View):
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        action = request.POST.get("action", "lock")
        if action == "lock":
            services.lock_post(actor=request.user, post=post, request=request)
        else:
            services.unlock_post(actor=request.user, post=post, request=request)
        if _wants_json(request):
            return JsonResponse({"ok": True, "locked": post.is_locked})
        return redirect(reverse("community:post_detail", args=[pk]))


class PostMoveView(AdminRequiredMixin, View):
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        new_cat_slug = request.POST.get("category", "")
        new_cat = get_object_or_404(Category, slug=new_cat_slug, is_active=True)
        services.move_post(actor=request.user, post=post, new_category=new_cat, request=request)
        messages.success(request, f'Post moved to "{new_cat.name}".')
        return redirect(reverse("community:post_detail", args=[pk]))


# ─── Admin: warnings (M3) ────────────────────────────────────────────────────

class IssueWarningView(AdminRequiredMixin, View):
    def post(self, request):
        from apps.accounts.models import User
        recipient_pk = request.POST.get("recipient_pk")
        level = request.POST.get("level", Warning.Level.MANUAL)
        internal_reason = request.POST.get("internal_reason", "")
        user_message = request.POST.get("user_message", "")
        post_pk = request.POST.get("post_pk")
        comment_pk = request.POST.get("comment_pk")

        recipient = get_object_or_404(User, pk=recipient_pk)
        post_obj = Post.objects.filter(pk=post_pk).first() if post_pk else None
        comment_obj = Comment.objects.filter(pk=comment_pk).first() if comment_pk else None

        try:
            services.issue_warning(
                actor=request.user, recipient=recipient, level=level,
                internal_reason=internal_reason, user_message=user_message,
                post=post_obj, comment=comment_obj, request=request,
            )
        except (PermissionError, ValueError) as exc:
            return _gate_response(request, str(exc))

        if _wants_json(request):
            return JsonResponse({"ok": True})
        messages.success(request, f"Warning issued to {recipient.get_full_name() or recipient.username}.")
        return redirect(request.POST.get("next", reverse("community:admin_queue")))


class LiftWarningView(AdminRequiredMixin, View):
    def post(self, request, pk):
        warning = get_object_or_404(Warning, pk=pk)
        reason = request.POST.get("reason", "")
        try:
            services.lift_warning(actor=request.user, warning=warning,
                                  reason=reason, request=request)
        except (PermissionError, ValueError) as exc:
            return _gate_response(request, str(exc))
        if _wants_json(request):
            return JsonResponse({"ok": True})
        messages.success(request, "Warning lifted.")
        return redirect(request.POST.get("next", reverse("community:admin_queue")))


# ─── Admin: appeals (M3) ─────────────────────────────────────────────────────

class AdminAppealView(AdminRequiredMixin, TemplateView):
    template_name = "community/admin/appeal_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.public.selectors import site_identity
        ctx["identity"] = site_identity(self.request)
        ctx["appeal"] = get_object_or_404(Appeal, pk=self.kwargs["pk"])
        return ctx

    def post(self, request, pk):
        appeal = get_object_or_404(Appeal, pk=pk)
        decision = request.POST.get("decision", "upheld")
        admin_response = request.POST.get("admin_response", "")
        try:
            services.review_appeal(actor=request.user, appeal=appeal,
                                   decision=decision,
                                   admin_response=admin_response, request=request)
        except (PermissionError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect(reverse("community:admin_appeal", args=[pk]))
        messages.success(request, f"Appeal {decision}.")
        return redirect(reverse("community:admin_queue"))


# ─── Admin: reports (M3) ─────────────────────────────────────────────────────

class ResolveReportView(AdminRequiredMixin, View):
    def post(self, request, pk):
        report = get_object_or_404(Report, pk=pk)
        resolution = request.POST.get("resolution", "dismissed")
        note = request.POST.get("note", "")
        try:
            services.resolve_report(actor=request.user, report=report,
                                    resolution=resolution, note=note, request=request)
        except (PermissionError, ValueError) as exc:
            return _gate_response(request, str(exc))
        if _wants_json(request):
            return JsonResponse({"ok": True})
        messages.success(request, f"Report {resolution}.")
        return redirect(reverse("community:admin_queue"))


# ─── Admin: mute / suspend (M3) ──────────────────────────────────────────────

class MuteUserView(AdminRequiredMixin, View):
    def post(self, request):
        from apps.accounts.models import User
        user = get_object_or_404(User, pk=request.POST.get("user_pk"))
        hours = int(request.POST.get("hours", 24))
        reason = request.POST.get("reason", "")
        services.mute_user(actor=request.user, user=user,
                           duration_hours=hours, reason=reason, request=request)
        if _wants_json(request):
            return JsonResponse({"ok": True})
        messages.success(request, f"Posting paused for {user.get_full_name() or user.username}.")
        return redirect(request.POST.get("next", reverse("community:admin_queue")))


class UnmuteUserView(AdminRequiredMixin, View):
    def post(self, request):
        from apps.accounts.models import User
        user = get_object_or_404(User, pk=request.POST.get("user_pk"))
        services.unmute_user(actor=request.user, user=user, request=request)
        if _wants_json(request):
            return JsonResponse({"ok": True})
        messages.success(request, "Posting re-enabled.")
        return redirect(request.POST.get("next", reverse("community:admin_queue")))


class SuspendUserView(AdminRequiredMixin, View):
    def post(self, request):
        from apps.accounts.models import User
        user = get_object_or_404(User, pk=request.POST.get("user_pk"))
        days = int(request.POST.get("days", 7))
        reason = request.POST.get("reason", "")
        services.suspend_user(actor=request.user, user=user,
                              duration_days=days, reason=reason, request=request)
        if _wants_json(request):
            return JsonResponse({"ok": True})
        messages.success(request, f"Community access paused for {user.get_full_name() or user.username}.")
        return redirect(request.POST.get("next", reverse("community:admin_queue")))


class UnsuspendUserView(AdminRequiredMixin, View):
    def post(self, request):
        from apps.accounts.models import User
        user = get_object_or_404(User, pk=request.POST.get("user_pk"))
        services.unsuspend_user(actor=request.user, user=user, request=request)
        if _wants_json(request):
            return JsonResponse({"ok": True})
        messages.success(request, "Community access restored.")
        return redirect(request.POST.get("next", reverse("community:admin_queue")))


# ─── Admin: word filter (M3) ─────────────────────────────────────────────────

class WordFilterView(AdminRequiredMixin, TemplateView):
    template_name = "community/admin/word_filter.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.public.selectors import site_identity
        ctx["identity"] = site_identity(self.request)
        ctx["words"] = BlockedWord.objects.order_by("word")
        return ctx

    def post(self, request):
        action = request.POST.get("action")
        if action == "add":
            word = request.POST.get("word", "").strip().lower()
            note = request.POST.get("note", "")
            if word:
                BlockedWord.objects.get_or_create(
                    word=word, defaults={"note": note, "is_active": True}
                )
                services.invalidate_blocked_words_cache()
                messages.success(request, f'"{word}" added to the word filter.')
        elif action == "toggle":
            word_pk = request.POST.get("word_pk")
            bw = get_object_or_404(BlockedWord, pk=word_pk)
            bw.is_active = not bw.is_active
            bw.save()
            services.invalidate_blocked_words_cache()
            state = "active" if bw.is_active else "inactive"
            messages.success(request, f'"{bw.word}" is now {state}.')
        elif action == "delete":
            word_pk = request.POST.get("word_pk")
            bw = get_object_or_404(BlockedWord, pk=word_pk)
            bw.delete()
            services.invalidate_blocked_words_cache()
            messages.success(request, "Word removed from the filter.")
        return redirect(reverse("community:admin_word_filter"))


# ─── Admin: delegation (M3) ──────────────────────────────────────────────────

class DelegationView(AdminRequiredMixin, TemplateView):
    template_name = "community/admin/delegation.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.public.selectors import site_identity
        from apps.accounts.models import User
        ctx["identity"] = site_identity(self.request)
        ctx["categories"] = Category.objects.filter(is_active=True).prefetch_related(
            "moderators__user"
        ).order_by("display_order")
        ctx["eligible_users"] = User.objects.filter(
            role__in=["teacher", "admin", "director"], is_active=True
        ).order_by("last_name", "first_name")
        return ctx

    def post(self, request):
        action = request.POST.get("action")
        category = get_object_or_404(Category, pk=request.POST.get("category_pk"))
        if action == "assign":
            from apps.accounts.models import User
            user = get_object_or_404(User, pk=request.POST.get("user_pk"))
            try:
                services.assign_moderator(actor=request.user, category=category,
                                          user=user, request=request)
                messages.success(request, f"{user.get_full_name()} assigned as moderator for {category.name}.")
            except PermissionError as exc:
                messages.error(request, str(exc))
        elif action == "remove":
            mod = get_object_or_404(CategoryModerator, pk=request.POST.get("mod_pk"))
            services.remove_moderator(actor=request.user, mod=mod, request=request)
            messages.success(request, "Moderator removed.")
        return redirect(reverse("community:admin_delegation"))


# ─── Admin: insights (M3) ────────────────────────────────────────────────────

class InsightsView(AdminRequiredMixin, TemplateView):
    template_name = "community/admin/insights.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.public.selectors import site_identity
        ctx["identity"] = site_identity(self.request)
        ctx.update(selectors.community_insights())
        return ctx


# ─── Admin: data management (M3) ─────────────────────────────────────────────

class AnonymiseUserView(AdminRequiredMixin, View):
    def post(self, request):
        from apps.accounts.models import User
        user = get_object_or_404(User, pk=request.POST.get("user_pk"))
        summary = services.anonymise_user_content(
            actor=request.user, user=user, request=request
        )
        messages.success(
            request,
            f"Content anonymised: {summary['posts_anonymised']} posts, "
            f"{summary['comments_anonymised']} comments.",
        )
        return redirect(request.POST.get("next", reverse("community:admin_queue")))


class ExportUserContentView(AdminRequiredMixin, View):
    def get(self, request, user_pk):
        from apps.accounts.models import User
        user = get_object_or_404(User, pk=user_pk)
        rows = services.export_user_content(user)
        output = io.StringIO()
        fieldnames = ["type", "id", "category", "title", "body", "post_id",
                      "post_title", "status", "is_removed", "created_at"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="community_export_{user.pk}.csv"'
        )
        return response


# ─── Admin: category management (M3) ─────────────────────────────────────────

class CategoryAdminListView(AdminRequiredMixin, TemplateView):
    template_name = "community/admin/categories.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.public.selectors import site_identity
        ctx["identity"] = site_identity(self.request)
        ctx["categories"] = Category.objects.order_by("display_order", "id")
        return ctx


class CategoryAdminEditView(AdminRequiredMixin, View):
    template_name = "community/admin/category_edit.html"

    def _get_ctx(self, request, category=None):
        from apps.public.selectors import site_identity
        return {
            "identity": site_identity(request),
            "category": category,
            "visibility_choices": Category.Visibility.choices,
        }

    def get(self, request, pk=None):
        from django.shortcuts import render
        category = Category.objects.get(pk=pk) if pk else None
        return render(request, self.template_name, self._get_ctx(request, category))

    def post(self, request, pk=None):
        category = Category.objects.get(pk=pk) if pk else None
        data = {
            "name": request.POST.get("name", ""),
            "icon": request.POST.get("icon", ""),
            "description": request.POST.get("description", ""),
            "visibility": request.POST.get("visibility", Category.Visibility.PUBLIC),
            "allowed_roles": request.POST.getlist("allowed_roles"),
            "display_order": int(request.POST.get("display_order", 0)),
            "is_active": request.POST.get("is_active") == "on",
            "staff_post_only": request.POST.get("staff_post_only") == "on",
        }
        if category:
            services.update_category(actor=request.user, category=category, **data)
            messages.success(request, f'"{category.name}" updated.')
        else:
            services.create_category(actor=request.user, **data)
            messages.success(request, "Category created.")
        return redirect(reverse("community:admin_categories"))


# ─── Dashboard card data endpoints (M2) ──────────────────────────────────────

class CommunityCardView(LoginRequiredMixin, View):
    """
    Returns a lightweight HTML fragment for the dashboard community card.
    Each role gets its own slice — served by the same view, branched on role.
    """

    def get(self, request):
        from django.shortcuts import render
        role = getattr(request.user, "role", "")
        cs = _community_settings(request)
        if not cs["enabled"]:
            return HttpResponse("")

        ctx = {
            "cs": cs,
            "user": request.user,
        }

        if role == "student":
            ctx.update(selectors.student_community_insights(request.user))
        elif role == "parent":
            ctx.update(selectors.parent_community_insights(request.user))
        elif role == "teacher":
            ctx.update(selectors.teacher_community_insights(request.user))
        elif role in ("director", "admin"):
            ctx.update(selectors.admin_community_summary())

        template = f"community/dashboard_cards/{role}.html"
        try:
            return render(request, template, ctx)
        except Exception:
            return HttpResponse("")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _check_consent_or_gate(user) -> tuple[bool, str]:
    try:
        profile = user.community_profile
        if not profile.has_consented:
            return False, "consent_required"
        return True, ""
    except CommunityProfile.DoesNotExist:
        return False, "consent_required"


def _gate_response(request, reason: str, redirect_url: str | None = None):
    if _wants_json(request):
        return JsonResponse({"ok": False, "error": reason}, status=400)
    if redirect_url:
        messages.warning(request, reason if reason != "consent_required" else
                         "Please accept the community rules before posting.")
        return redirect(redirect_url)
    messages.error(request, reason)
    return redirect(request.META.get("HTTP_REFERER", reverse("community:home")))
