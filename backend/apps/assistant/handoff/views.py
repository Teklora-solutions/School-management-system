"""
apps/assistant/handoff/views.py
---------------------------------
CP4 -- handoff rule and team admin screens.
CP5 -- human inbox: list, detail/takeover, assign, resolve, notes, canned replies.
CP6 -- conversation export and deletion.
"""

from __future__ import annotations

import csv
import io
import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from apps.core.audit_log import log_action
from apps.core.mixins import AdminRequiredMixin, RoleRequiredMixin

from ..models import (
    CannedReply,
    ChatConversation,
    ChatMessage,
    Handoff,
    HandoffEvent,
    HandoffPriority,
    HandoffRule,
    HandoffTeam,
)
from . import services
from .forms import (
    AssignForm,
    CannedReplyForm,
    HandoffNoteForm,
    HandoffRuleForm,
    HandoffTeamForm,
    ResolveForm,
    StaffReplyForm,
)
from .seed import seed_starter_rules

log = logging.getLogger(__name__)

INBOX_ROLES = ("admin", "director", "teacher", "finance")


class InboxAccessMixin(RoleRequiredMixin):
    allowed_roles = list(INBOX_ROLES)


def _can_mutate(user) -> bool:
    return getattr(user, "role", "") in ("admin", "teacher", "finance")


# ---------------------------------------------------------------------------
# CP4 -- Handoff rules
# ---------------------------------------------------------------------------

class HandoffRuleListView(AdminRequiredMixin, ListView):
    template_name = "features/assistant/handoff/rule_list.html"
    context_object_name = "rules"

    def get_queryset(self):
        return HandoffRule.objects.select_related(
            "destination_user", "destination_team", "fallback_user", "fallback_team"
        ).order_by("order", "name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["teams"] = HandoffTeam.objects.all()
        ctx["has_safeguarding_rule"] = HandoffRule.objects.filter(
            is_required=True, enabled=True
        ).exists()
        return ctx


class HandoffRuleCreateView(AdminRequiredMixin, View):
    template_name = "features/assistant/handoff/rule_form.html"

    def get(self, request):
        return render(request, self.template_name, {
            "form": HandoffRuleForm(), "title": "Add handoff rule",
        })

    def post(self, request):
        form = HandoffRuleForm(request.POST)
        if form.is_valid():
            try:
                rule = form.save(commit=False)
                rule.full_clean()
                rule.save()
                form.save_m2m()
                log_action(
                    actor=request.user, action="assistant.handoff_rule_created",
                    content_type="assistant.HandoffRule", object_id=rule.pk,
                    change_summary={"name": rule.name}, request=request,
                )
                messages.success(request, 'Rule "%s" created.' % rule.name)
                return redirect("assistant:handoff_rules")
            except ValidationError as exc:
                form.add_error(None, exc)
        return render(request, self.template_name, {
            "form": form, "title": "Add handoff rule",
        })


class HandoffRuleUpdateView(AdminRequiredMixin, View):
    template_name = "features/assistant/handoff/rule_form.html"

    def _get_rule(self, pk):
        return get_object_or_404(HandoffRule, pk=pk)

    def get(self, request, pk):
        rule = self._get_rule(pk)
        return render(request, self.template_name, {
            "form": HandoffRuleForm(instance=rule), "rule": rule,
            "title": "Edit rule: %s" % rule.name,
        })

    def post(self, request, pk):
        rule = self._get_rule(pk)
        form = HandoffRuleForm(request.POST, instance=rule)
        if form.is_valid():
            try:
                updated = form.save(commit=False)
                updated.full_clean()
                updated.save()
                form.save_m2m()
                log_action(
                    actor=request.user, action="assistant.handoff_rule_updated",
                    content_type="assistant.HandoffRule", object_id=rule.pk,
                    change_summary={"name": rule.name}, request=request,
                )
                messages.success(request, 'Rule "%s" saved.' % rule.name)
                return redirect("assistant:handoff_rules")
            except ValidationError as exc:
                form.add_error(None, exc)
        return render(request, self.template_name, {
            "form": form, "rule": rule, "title": "Edit rule: %s" % rule.name,
        })


class HandoffRuleDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        rule = get_object_or_404(HandoffRule, pk=pk)
        try:
            name = rule.name
            rule.delete()
            log_action(
                actor=request.user, action="assistant.handoff_rule_deleted",
                content_type="assistant.HandoffRule", object_id=pk,
                change_summary={"name": name}, request=request,
            )
            messages.success(request, 'Rule "%s" deleted.' % name)
        except ValidationError as exc:
            messages.error(request, str(exc))
        return redirect("assistant:handoff_rules")


class HandoffRuleToggleView(AdminRequiredMixin, View):
    def post(self, request, pk):
        rule = get_object_or_404(HandoffRule, pk=pk)
        try:
            rule.enabled = not rule.enabled
            rule.full_clean()
            rule.save(update_fields=["enabled", "updated_at"])
            state = "enabled" if rule.enabled else "disabled"
            messages.success(request, 'Rule "%s" %s.' % (rule.name, state))
        except ValidationError as exc:
            messages.error(request, str(exc))
        return redirect("assistant:handoff_rules")


class HandoffSeedView(AdminRequiredMixin, View):
    def post(self, request):
        output = io.StringIO()
        counts = seed_starter_rules(stdout=output)
        log_action(
            actor=request.user, action="assistant.handoff_rules_seeded",
            content_type="assistant.HandoffRule", object_id=0,
            change_summary=counts, request=request,
        )
        messages.success(
            request,
            "Starter rules: %d created, %d updated, %d skipped." % (
                counts["created"], counts["updated"], counts["skipped"]
            ),
        )
        return redirect("assistant:handoff_rules")


# ---------------------------------------------------------------------------
# CP4 -- Teams
# ---------------------------------------------------------------------------

class HandoffTeamListView(AdminRequiredMixin, ListView):
    template_name = "features/assistant/handoff/team_list.html"
    context_object_name = "teams"

    def get_queryset(self):
        return HandoffTeam.objects.prefetch_related("members").order_by("name")


class HandoffTeamCreateView(AdminRequiredMixin, View):
    template_name = "features/assistant/handoff/team_form.html"

    def get(self, request):
        return render(request, self.template_name, {
            "form": HandoffTeamForm(), "title": "Add team",
        })

    def post(self, request):
        form = HandoffTeamForm(request.POST)
        if form.is_valid():
            team = form.save()
            messages.success(request, 'Team "%s" created.' % team.name)
            return redirect("assistant:handoff_teams")
        return render(request, self.template_name, {"form": form, "title": "Add team"})


class HandoffTeamUpdateView(AdminRequiredMixin, View):
    template_name = "features/assistant/handoff/team_form.html"

    def get(self, request, pk):
        team = get_object_or_404(HandoffTeam, pk=pk)
        return render(request, self.template_name, {
            "form": HandoffTeamForm(instance=team), "team": team,
            "title": "Edit team: %s" % team.name,
        })

    def post(self, request, pk):
        team = get_object_or_404(HandoffTeam, pk=pk)
        form = HandoffTeamForm(request.POST, instance=team)
        if form.is_valid():
            form.save()
            messages.success(request, 'Team "%s" saved.' % team.name)
            return redirect("assistant:handoff_teams")
        return render(request, self.template_name, {
            "form": form, "team": team, "title": "Edit team: %s" % team.name,
        })


# ---------------------------------------------------------------------------
# CP5 -- Inbox
# ---------------------------------------------------------------------------

class InboxListView(InboxAccessMixin, View):
    template_name = "features/assistant/handoff/inbox.html"

    def get(self, request):
        status_filter = request.GET.get("status", "open")
        qs = (
            services.inbox_resolved_for_user(request.user)
            if status_filter == "resolved"
            else services.inbox_for_user(request.user)
        )
        return render(request, self.template_name, {
            "handoffs": qs[:100],
            "status_filter": status_filter,
            "can_mutate": _can_mutate(request.user),
            "canned_replies": CannedReply.objects.filter(is_active=True).order_by(
                "display_order", "title"
            ),
        })


class InboxDetailView(InboxAccessMixin, View):
    template_name = "features/assistant/handoff/inbox_detail.html"

    def _get_handoff(self, pk, user):
        handoff = get_object_or_404(
            Handoff.objects.select_related(
                "conversation", "rule", "assigned_user", "assigned_team"
            ).prefetch_related("notes__author", "events"),
            pk=pk,
        )
        if getattr(user, "role", "") not in ("admin", "director"):
            from ..models import HandoffTeam as _HT
            from django.db.models import Q
            team_ids = _HT.objects.filter(members=user).values_list("pk", flat=True)
            if not (
                handoff.assigned_user == user
                or (handoff.assigned_team_id and handoff.assigned_team_id in list(team_ids))
                or handoff.destination_role == getattr(user, "role", "")
            ):
                raise PermissionDenied("This handoff is not assigned to you.")
        return handoff

    def get(self, request, pk):
        handoff = self._get_handoff(pk, request.user)
        if handoff.signed_in_conversation_id:
            from ..models import SignedInMessage

            chat_messages = SignedInMessage.objects.filter(
                conversation=handoff.signed_in_conversation
            ).order_by("created_at", "pk")
        else:
            chat_messages = ChatMessage.objects.filter(
                conversation=handoff.conversation
            ).order_by("created_at", "pk")
        return render(request, self.template_name, {
            "handoff": handoff,
            "chat_messages": chat_messages,
            "note_form": HandoffNoteForm(),
            "reply_form": StaffReplyForm(),
            "resolve_form": ResolveForm(),
            "assign_form": AssignForm(),
            "can_mutate": _can_mutate(request.user),
            "canned_replies": CannedReply.objects.filter(is_active=True).order_by(
                "display_order", "title"
            ),
            "events": handoff.events.order_by("created_at"),
        })

    def post(self, request, pk):
        if not _can_mutate(request.user):
            raise PermissionDenied("Directors have read-only inbox access.")
        handoff = self._get_handoff(pk, request.user)
        action = request.POST.get("action", "")

        if action == "note":
            form = HandoffNoteForm(request.POST)
            if form.is_valid():
                services.add_note(handoff, body=form.cleaned_data["body"], actor=request.user)
                messages.success(request, "Note added.")
            else:
                messages.error(request, "Note body cannot be blank.")

        elif action == "reply":
            form = StaffReplyForm(request.POST)
            if form.is_valid():
                services.staff_reply(handoff, body=form.cleaned_data["body"], actor=request.user)
                messages.success(request, "Reply sent.")
            else:
                messages.error(request, "Reply cannot be blank.")

        elif action == "resolve":
            form = ResolveForm(request.POST)
            if form.is_valid():
                services.resolve(
                    handoff,
                    actor=request.user,
                    note=form.cleaned_data.get("note", ""),
                    suggest_kb=form.cleaned_data.get("suggest_kb", False),
                )
                messages.success(request, "Handoff resolved.")
                return redirect("assistant:inbox")

        elif action == "assign":
            form = AssignForm(request.POST)
            if form.is_valid():
                dest = form.cleaned_data["assign_to"]
                from apps.accounts.models import User
                u, t = None, None
                if dest.startswith("u:"):
                    u = User.objects.filter(pk=int(dest[2:])).first()
                elif dest.startswith("t:"):
                    t = HandoffTeam.objects.filter(pk=int(dest[2:])).first()
                services.assign(handoff, user=u, team=t, actor=request.user)
                messages.success(request, "Assigned.")

        return redirect("assistant:inbox_detail", pk=pk)


# ---------------------------------------------------------------------------
# CP5 -- Canned replies
# ---------------------------------------------------------------------------

class CannedReplyListView(AdminRequiredMixin, ListView):
    template_name = "features/assistant/handoff/canned_reply_list.html"
    context_object_name = "replies"

    def get_queryset(self):
        return CannedReply.objects.order_by("display_order", "title")


class CannedReplyCreateView(AdminRequiredMixin, View):
    template_name = "features/assistant/handoff/canned_reply_form.html"

    def get(self, request):
        return render(request, self.template_name, {
            "form": CannedReplyForm(), "title": "Add canned reply",
        })

    def post(self, request):
        form = CannedReplyForm(request.POST)
        if form.is_valid():
            reply = form.save()
            messages.success(request, 'Canned reply "%s" created.' % reply.title)
            return redirect("assistant:canned_replies")
        return render(request, self.template_name, {
            "form": form, "title": "Add canned reply",
        })


class CannedReplyUpdateView(AdminRequiredMixin, View):
    template_name = "features/assistant/handoff/canned_reply_form.html"

    def get(self, request, pk):
        reply = get_object_or_404(CannedReply, pk=pk)
        return render(request, self.template_name, {
            "form": CannedReplyForm(instance=reply),
            "reply": reply, "title": "Edit: %s" % reply.title,
        })

    def post(self, request, pk):
        reply = get_object_or_404(CannedReply, pk=pk)
        form = CannedReplyForm(request.POST, instance=reply)
        if form.is_valid():
            form.save()
            messages.success(request, "Saved.")
            return redirect("assistant:canned_replies")
        return render(request, self.template_name, {
            "form": form, "reply": reply, "title": "Edit: %s" % reply.title,
        })


class CannedReplyDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        reply = get_object_or_404(CannedReply, pk=pk)
        title = reply.title
        reply.delete()
        messages.success(request, 'Deleted "%s".' % title)
        return redirect("assistant:canned_replies")


# ---------------------------------------------------------------------------
# CP6 -- Conversation export and deletion
# ---------------------------------------------------------------------------

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_csv_cell(value) -> str:
    text = str(value or "")
    if text and text[0] in _CSV_FORMULA_PREFIXES:
        return "'" + text
    return text


class ConversationExportView(AdminRequiredMixin, View):
    def get(self, request, pk):
        conv = get_object_or_404(ChatConversation, pk=pk)
        rows = ChatMessage.objects.filter(conversation=conv).order_by("created_at")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["timestamp", "role", "body", "language", "blocked_reason"])
        for msg in rows:
            writer.writerow([
                msg.created_at.isoformat() if msg.created_at else "",
                _safe_csv_cell(msg.role),
                _safe_csv_cell(msg.body),
                _safe_csv_cell(msg.language),
                _safe_csv_cell(msg.blocked_reason),
            ])
        log_action(
            actor=request.user, action="assistant.conversation_exported",
            content_type="assistant.ChatConversation", object_id=conv.pk,
            change_summary={"message_count": rows.count()}, request=request,
        )
        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="conversation_%d.csv"' % conv.pk
        )
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class ConversationDeleteView(AdminRequiredMixin, View):
    template_name = "features/assistant/handoff/conversation_delete.html"

    def get(self, request, pk):
        conv = get_object_or_404(ChatConversation, pk=pk)
        return render(request, self.template_name, {"conversation": conv})

    def post(self, request, pk):
        conv = get_object_or_404(ChatConversation, pk=pk)
        reason = request.POST.get("reason", "").strip()
        log_action(
            actor=request.user, action="assistant.conversation_deleted",
            content_type="assistant.ChatConversation", object_id=pk,
            change_summary={
                "visitor_key": conv.visitor_key[:8] + "...",
                "message_count": conv.message_count,
                "reason": reason[:200],
            },
            request=request,
        )
        conv.delete()
        messages.success(request, "Conversation and all related records deleted.")
        return redirect("assistant:inbox")
