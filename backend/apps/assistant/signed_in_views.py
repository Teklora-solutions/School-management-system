"""
apps/assistant/signed_in_views.py
-----------------------------------
Module 4 HTTP endpoints for the signed-in assistant.

All endpoints require an authenticated user.  CSRF is on; the floating
button's JS sends the cookie in X-CSRFToken.

Endpoints:
  GET  /assistant/me/state/          -- is the assistant on for this user?
  POST /assistant/me/send/           -- ask a question
  POST /assistant/me/new/            -- start a new conversation
  GET  /assistant/me/history/        -- conversation message list
  POST /assistant/me/child/          -- switch active child (parents only)
  GET  /assistant/me/memory/         -- view memory
  POST /assistant/me/memory/         -- update or forget memory
  POST /assistant/me/delete/         -- delete the user's own conversation history
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views import View

from . import signed_in_chat
from .models import AssistantMemory, AssistantSettings, SignedInConversation, SignedInMessage
from .signed_in_chat import (
    SignedInRefused,
    begin,
    close_signed_in_conversation,
    complete,
    get_or_create_memory,
    open_signed_in_conversation,
    serialize_signed_in_message,
    update_memory,
)

log = logging.getLogger(__name__)

NO_STORE = "no-store"


def _payload(request) -> dict:
    if (request.content_type or "").startswith("application/json"):
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}
    return request.POST.dict()


def _refused(exc: SignedInRefused):
    return JsonResponse(
        {"ok": False, "error": exc.code, "message": exc.message},
        status=exc.status,
    )


class SignedInStateView(LoginRequiredMixin, View):
    """Is the signed-in assistant available to this user?"""

    def get(self, request):
        get_token(request)
        user = request.user
        reason = signed_in_chat.signed_in_off_reason(user)
        row = AssistantSettings.read()

        if reason:
            return JsonResponse({"ok": True, "enabled": False, "reason": reason},
                                headers={"Cache-Control": NO_STORE})

        conv = open_signed_in_conversation(user, create=False)
        mem = AssistantMemory.objects.filter(user=user, opted_in=True).first()

        payload = {
            "ok": True,
            "enabled": True,
            "school": getattr(__import__("django.conf", fromlist=["settings"]).settings,
                              "SCHOOL_NAME", "the school"),
            "role": getattr(user, "role", ""),
            "user_name": user.get_full_name() or user.username,
            "language": conv.language if conv else (mem.prefer_language if mem else "en"),
            "max_chars": 4000,
            "tutor_enabled": row.student_tutor_enabled and getattr(user, "role", "") == "student",
            "conversation": {
                "id": conv.pk if conv else None,
                "messages": [
                    serialize_signed_in_message(m)
                    for m in (
                        SignedInMessage.objects.filter(conversation=conv)
                        .order_by("created_at")
                        .exclude(role=SignedInMessage.Role.SYSTEM)
                        if conv else []
                    )
                ],
            },
        }

        # For parents: inject child list
        if getattr(user, "role", "") == "parent":
            payload["children"] = _children_for_parent(user, conv)

        return JsonResponse(payload, headers={"Cache-Control": NO_STORE})


def _children_for_parent(user, conv) -> list:
    try:
        parent = user.parent_profile
        from apps.students.models import Student
        children = list(
            Student.objects.filter(
                guardian_links__parent=parent,
                status__in=[Student.Status.ACTIVE, Student.Status.REPEATING],
            ).select_related("user", "current_class")
        )
        active_id = conv.active_child_id if conv else None
        return [
            {
                "id": c.pk,
                "name": c.user.get_full_name(),
                "class_name": c.current_class.name if c.current_class else "",
                "is_active": c.pk == active_id,
            }
            for c in children
        ]
    except Exception:
        return []


class SignedInSendView(LoginRequiredMixin, View):
    def post(self, request):
        payload = _payload(request)
        tutor_mode = str(payload.get("tutor_mode", "")).lower() in ("1", "true", "yes")
        try:
            turn = begin(request, text=payload.get("message", ""), tutor_mode=tutor_mode)
        except SignedInRefused as exc:
            return _refused(exc)

        try:
            turn = complete(turn)
        except Exception:  # noqa: BLE001
            log.exception("Signed-in complete() failed for user %s", request.user.pk)
            turn.reply_text = signed_in_chat.CHAT_UNAVAILABLE
            turn.blocked_reason = "unavailable"

        reply_data = serialize_signed_in_message(turn.reply_message) if turn.reply_message else None
        return JsonResponse(
            {
                "ok": True,
                "conversation_id": turn.conversation.pk,
                "user_message_id": turn.user_message.pk,
                "reply": reply_data,
                "tool_calls": [
                    {"name": tc["name"], "ok": tc.get("ok", False)}
                    for tc in (turn.tool_calls or [])
                ],
            },
            headers={"Cache-Control": NO_STORE},
        )


class SignedInNewView(LoginRequiredMixin, View):
    def post(self, request):
        close_signed_in_conversation(request.user)
        open_signed_in_conversation(request.user, create=True)
        return JsonResponse({"ok": True}, headers={"Cache-Control": NO_STORE})


class SignedInHistoryView(LoginRequiredMixin, View):
    def get(self, request):
        conv = open_signed_in_conversation(request.user, create=False)
        if conv is None:
            return JsonResponse({"ok": True, "messages": []}, headers={"Cache-Control": NO_STORE})
        messages = list(
            SignedInMessage.objects.filter(conversation=conv)
            .exclude(role=SignedInMessage.Role.SYSTEM)
            .order_by("created_at")
        )
        return JsonResponse(
            {"ok": True, "messages": [serialize_signed_in_message(m) for m in messages]},
            headers={"Cache-Control": NO_STORE},
        )


class SignedInChildSwitchView(LoginRequiredMixin, View):
    """Parents only: switch which child the conversation is about."""

    def post(self, request):
        if getattr(request.user, "role", "") != "parent":
            return JsonResponse({"ok": False, "error": "Only parents can switch children."},
                                status=403, headers={"Cache-Control": NO_STORE})
        payload = _payload(request)
        try:
            child_id = int(payload.get("child_id", 0))
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Invalid child_id."}, status=400,
                                headers={"Cache-Control": NO_STORE})

        # Verify this child is linked to the parent
        try:
            parent = request.user.parent_profile
            linked = set(parent.children_links.values_list("student_id", flat=True))
            if child_id not in linked:
                return JsonResponse(
                    {"ok": False, "error": "Child not linked to this account."},
                    status=403, headers={"Cache-Control": NO_STORE},
                )
        except Exception:
            return JsonResponse({"ok": False, "error": "Could not resolve children."},
                                status=400, headers={"Cache-Control": NO_STORE})

        conv = open_signed_in_conversation(request.user, create=True)
        signed_in_chat.set_active_child(conv, child_id)
        return JsonResponse({"ok": True, "active_child_id": child_id},
                            headers={"Cache-Control": NO_STORE})


class SignedInMemoryView(LoginRequiredMixin, View):
    """View and manage the user's personalisation memory."""

    def get(self, request):
        mem = get_or_create_memory(request.user)
        return JsonResponse(
            {
                "ok": True,
                "opted_in": mem.opted_in,
                "prefer_language": mem.prefer_language,
                "prefer_tone": mem.prefer_tone,
                "recurring_summary": mem.recurring_summary if mem.opted_in else "",
                "note": (
                    "Memory stores only your language preference, tone preference, "
                    "and a short non-sensitive summary of recurring topics. "
                    "It never stores names, fees, marks or health details."
                ),
            },
            headers={"Cache-Control": NO_STORE},
        )

    def post(self, request):
        payload = _payload(request)
        action = payload.get("action", "")

        if action == "forget":
            mem = get_or_create_memory(request.user)
            mem.forget()
            return JsonResponse({"ok": True, "message": "All memory cleared."},
                                headers={"Cache-Control": NO_STORE})

        if action == "forget_summary":
            mem = get_or_create_memory(request.user)
            mem.recurring_summary = ""
            mem.save(update_fields=["recurring_summary", "updated_at"])
            return JsonResponse({"ok": True, "message": "Summary cleared."},
                                headers={"Cache-Control": NO_STORE})

        # Update preferences
        lang = payload.get("prefer_language")
        tone = payload.get("prefer_tone")
        opted_in = payload.get("opted_in")

        kwargs: dict = {}
        if lang is not None:
            kwargs["prefer_language"] = str(lang)[:8]
        if tone is not None:
            kwargs["prefer_tone"] = str(tone)[:20]
        if opted_in is not None:
            kwargs["opted_in"] = str(opted_in).lower() in ("1", "true", "yes")

        update_memory(request.user, **kwargs)
        return JsonResponse({"ok": True}, headers={"Cache-Control": NO_STORE})


class SignedInDeleteConversationView(LoginRequiredMixin, View):
    """Delete the user's own signed-in conversation history."""

    def post(self, request):
        from apps.core.audit_log import log_action
        convs = SignedInConversation.objects.filter(user=request.user)
        count = convs.count()
        convs.delete()
        log_action(
            actor=request.user,
            action="assistant.signed_in_conversation_deleted_by_user",
            content_type="assistant.SignedInConversation",
            object_id=0,
            change_summary={"count": count, "user_id": request.user.pk},
            request=request,
        )
        return JsonResponse(
            {"ok": True, "deleted_conversations": count},
            headers={"Cache-Control": NO_STORE},
        )


class SignedInDraftAnnouncementView(LoginRequiredMixin, View):
    """
    Confirm-to-save for an AI-drafted announcement.  Only admin and director
    may save — the same roles the announcements screen allows.  The draft is
    created UNPUBLISHED; a person reviews and publishes it on the
    announcements screen.  This is the explicit confirmation step Part B3
    requires for anything that changes data.
    """

    ALLOWED_ROLES = {"admin", "director"}

    def post(self, request):
        if getattr(request.user, "role", "") not in self.ALLOWED_ROLES:
            return JsonResponse(
                {"ok": False, "error": "Only admins and directors can save announcements."},
                status=403, headers={"Cache-Control": NO_STORE},
            )
        payload = _payload(request)
        title = str(payload.get("title", "")).strip()[:200]
        body = str(payload.get("body", "")).strip()[:10000]
        if not title or not body:
            return JsonResponse(
                {"ok": False, "error": "A draft needs a title and a body."},
                status=400, headers={"Cache-Control": NO_STORE},
            )
        audience = str(payload.get("audience", "everyone"))
        if audience not in ("everyone", "by_role", "by_class"):
            audience = "everyone"
        target_roles = payload.get("target_roles") or []
        if not isinstance(target_roles, list):
            target_roles = []
        target_class_ids = payload.get("target_class_ids") or []
        try:
            target_class_ids = [int(c) for c in target_class_ids][:50]
        except (TypeError, ValueError):
            target_class_ids = []

        from apps.communications import services as comms_services
        from apps.core.audit_log import log_action

        ann = comms_services.create_announcement(
            title=title,
            body=body,
            audience=audience,
            target_roles=[str(r)[:30] for r in target_roles],
            target_class_ids=target_class_ids,
            is_published=False,   # always a draft — never goes live from the assistant
            created_by=request.user,
        )
        log_action(
            actor=request.user,
            action="assistant.announcement_draft_saved",
            content_type="communications.Announcement",
            object_id=ann.pk,
            change_summary={
                "title": title[:80],
                "audience": audience,
                "ai_draft": True,
            },
            request=request,
        )
        return JsonResponse(
            {"ok": True, "announcement_id": ann.pk, "published": False},
            headers={"Cache-Control": NO_STORE},
        )


class SignedInChatPageView(LoginRequiredMixin, View):
    """
    The signed-in assistant HTML page — renders the chat UI for dashboards.
    The JS makes the API calls to me/state/, me/send/ etc.
    """

    def get(self, request):
        from django.shortcuts import render
        user = request.user
        reason = signed_in_chat.signed_in_off_reason(user)
        row = AssistantSettings.read()
        mem = AssistantMemory.objects.filter(user=user, opted_in=True).first()
        return render(request, "features/assistant/signed_in_chat.html", {
            "off_reason": reason,
            "user_name": user.get_full_name() or user.username,
            "role": getattr(user, "role", ""),
            "tutor_enabled": row.student_tutor_enabled and getattr(user, "role", "") == "student",
            "memory": mem,
            "state_url":   "/assistant/me/state/",
            "send_url":    "/assistant/me/send/",
            "new_url":     "/assistant/me/new/",
            "history_url": "/assistant/me/history/",
            "child_url":   "/assistant/me/child/",
            "memory_url":  "/assistant/me/memory/",
            "delete_url":  "/assistant/me/delete/",
        })
