"""
The public widget's endpoints (Module 3, Part A).

These are the only assistant URLs open to a visitor. They are JSON (or an
event stream) and never render a template. Every rule — the switches, the
consent gate, the bot checks, the daily limits — lives in ``chat.py``; the views
only translate HTTP to that service.

CSRF protection is on. ``state`` issues the token cookie; the widget sends it
back in ``X-CSRFToken``.
"""

from __future__ import annotations

import json
import logging
import re

from django.http import JsonResponse, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.views import View

from . import chat, limits

log = logging.getLogger(__name__)

NO_STORE = "no-store"
COOKIE_MAX_AGE = limits.VISITOR_COOKIE_MAX_AGE
STREAM_CHUNK_CHARS = 48


def _payload(request) -> dict:
    if (request.content_type or "").startswith("application/json"):
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}
    return request.POST.dict()


def _finish(response, request):
    """Set the signed visitor cookie when this request minted it."""
    if getattr(request, "assistant_visitor_is_new", False):
        response.set_signed_cookie(
            limits.VISITOR_COOKIE,
            getattr(request, "assistant_visitor_key", ""),
            salt=limits.VISITOR_SALT,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure(),
        )
    if isinstance(response, JsonResponse):
        response["Cache-Control"] = NO_STORE
    return response


def _refused(exc: chat.Refused, request):
    return _finish(
        JsonResponse(
            {"ok": False, "error": exc.code, "message": exc.message},
            status=exc.status,
        ),
        request,
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunks(text: str):
    buffer = ""
    for part in re.findall(r"\S+\s*", text or ""):
        if buffer and len(buffer) + len(part) > STREAM_CHUNK_CHARS:
            yield buffer
            buffer = part
        else:
            buffer += part
    if buffer:
        yield buffer


def _wants_stream(request, payload) -> bool:
    value = str(payload.get("stream", "")).lower()
    if value in ("1", "true", "yes"):
        return True
    return "text/event-stream" in request.headers.get("Accept", "")


class WidgetStateView(View):
    """What the widget needs before it shows anything."""

    def get(self, request):
        get_token(request)  # make the CSRF cookie available to the widget
        return _finish(JsonResponse(chat.state(request)), request)


class ChatSendView(View):
    def post(self, request):
        payload = _payload(request)
        try:
            turn = chat.begin(
                request,
                text=payload.get("message", ""),
                honeypot=payload.get("website", ""),
                pow_challenge=payload.get("pow_challenge", ""),
                pow_nonce=payload.get("pow_nonce", ""),
            )
        except chat.Refused as refusal:
            return _refused(refusal, request)

        if _wants_stream(request, payload):
            response = StreamingHttpResponse(
                _stream(turn), content_type="text/event-stream"
            )
            response["Cache-Control"] = NO_STORE
            response["X-Accel-Buffering"] = "no"
            return _finish(response, request)

        turn = chat.complete(turn)
        return _finish(JsonResponse(chat.turn_payload(turn)), request)


def _stream(turn: chat.Turn):
    """
    The answer is verified in full before the first letter is sent.

    Provider tokens are never relayed as they arrive: the citation and figure
    checks must finish first, or an unverified sentence would reach a visitor.
    What streams is the finished reply, in pieces the browser can render as
    they arrive on a slow connection.
    """
    yield _sse("status", {"stage": "thinking"})
    turn = chat.complete(turn, streamed=True)
    yield _sse(
        "start",
        {
            "conversation_id": turn.conversation.pk,
            "message_id": turn.reply_message.pk if turn.reply_message else None,
        },
    )
    for piece in _chunks(turn.reply_text):
        yield _sse("delta", {"text": piece})
    yield _sse("done", chat.turn_payload(turn))


class ChatNewView(View):
    def post(self, request):
        try:
            return _finish(JsonResponse(chat.start_new_conversation(request)), request)
        except chat.Refused as refusal:
            return _refused(refusal, request)


class ChatFeedbackView(View):
    def post(self, request):
        payload = _payload(request)
        try:
            feedback = chat.record_feedback(
                request,
                message_id=payload.get("message_id"),
                kind=payload.get("kind", "rating"),
                rating=payload.get("rating", ""),
                reason=payload.get("reason", ""),
            )
        except chat.Refused as refusal:
            return _refused(refusal, request)
        return _finish(
            JsonResponse({"ok": True, "feedback_id": feedback.pk}), request
        )


class ChatCallbackView(View):
    def post(self, request):
        payload = _payload(request)
        try:
            callback = chat.request_callback(
                request,
                name=payload.get("name", ""),
                phone=payload.get("phone", ""),
                email=payload.get("email", ""),
                honeypot=payload.get("website", ""),
            )
        except chat.Refused as refusal:
            return _refused(refusal, request)
        return _finish(
            JsonResponse(
                {
                    "ok": True,
                    "callback_id": callback.pk,
                    "purpose": callback.purpose,
                }
            ),
            request,
        )
