"""
The public chat service (Module 3, Part A backend).

One place where a visitor's question becomes a stored, answered turn. The
order is fixed and mirrors the answer pipeline:

  1. the widget and consent gates — nothing is shown before the school has
     approved its privacy wording;
  2. bot checks (honeypot, optional proof of work) and the message length cap;
  3. the visitor, IP and site daily limits, checked before anything is stored;
  4. the visitor's words are masked, then stored masked;
  5. safeguarding screening, retrieval, citation and figure checks run through
     ``knowledge.answers`` — exactly the same code the admin answer desk uses;
  6. only a verified answer, the fixed refusal, the fixed safeguarding reply or
     a plain "unavailable" notice is ever written as the assistant's reply.

The conversation is kept across page loads on the same device through the
signed visitor cookie already used by the rate limits; the database stores only
its SHA-256, and no IP address.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError
from django.utils import timezone

from . import alerts, consent, limits, pow
from .guardrails import build_safe_reply, clean_user_text, mask_text
from .knowledge import scope
from .knowledge.answers import answer_question, deliver, language_hint
from .models import (
    AnswerFeedback,
    AssistantSettings,
    CallbackRequest,
    ChatConversation,
    ChatMessage,
    SuggestedQuestion,
    UsageEvent,
)

log = logging.getLogger(__name__)

MAX_REPLY_CHARS = 4000
MAX_FEEDBACK_REASON = 1000
CALLBACK_DAILY_LIMIT = 3
HONEYPOT_MESSAGE = "We couldn't accept this message. Please try again."
CHECK_FAILED_MESSAGE = "Please reload the page and try again."
CHAT_UNAVAILABLE = (
    "I can't answer right now. Please contact the school office and we will help you."
)


class Refused(Exception):
    """A request the widget must not process, with a safe reason to show."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


@dataclass
class Turn:
    conversation: ChatConversation
    visitor_message: ChatMessage
    reply_message: ChatMessage | None = None
    reply_text: str = ""
    citations: list = field(default_factory=list)
    blocked_reason: str = ""
    is_new_visitor: bool = False


def visitor_digest(request) -> str:
    key, _ = limits.ensure_visitor(request)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def off_reason(row: AssistantSettings) -> str:
    """Why the widget may not appear, in words a visitor can understand."""
    if row.kill_switch:
        return "The assistant is stopped right now."
    if not row.enabled:
        return "The assistant is switched off."
    if not row.public_enabled:
        return "The public assistant is switched off."
    if not row.widget_enabled:
        return "The website chat is switched off."
    if not row.consent_ready:
        return "The chat is waiting for the school to approve its privacy wording."
    if not row.public_visitor_daily_limit:
        return "The public assistant is unavailable today."
    return ""


def widget_mounted(row: AssistantSettings) -> bool:
    """Whether public pages should render the widget at all (not the per-visitor check)."""
    return not off_reason(row)


def open_conversation(digest: str, *, create: bool = True) -> ChatConversation | None:
    conversation = ChatConversation.objects.filter(
        visitor_key=digest, status=ChatConversation.Status.OPEN
    ).first()
    if conversation is None and create:
        conversation = ChatConversation.objects.create(visitor_key=digest)
    return conversation


def serialize_message(message: ChatMessage) -> dict:
    return {
        "id": message.pk,
        "role": message.role,
        "body": message.body,
        "language": message.language,
        "citations": message.citations if message.role == ChatMessage.Role.ASSISTANT else [],
        "created_at": message.created_at.isoformat(),
    }


def serialize_conversation(conversation) -> dict:
    if conversation is None:
        return {"id": None, "messages": []}
    return {
        "id": conversation.pk,
        "language": conversation.language,
        "messages": [serialize_message(message) for message in conversation.messages.all()],
    }


def state(request) -> dict:
    row = AssistantSettings.read()
    reason = off_reason(row)
    payload = {
        "ok": True,
        "enabled": not reason,
        "reason": reason,
        "school": getattr(django_settings, "SCHOOL_NAME", "the school"),
    }
    if reason:
        return payload

    digest = visitor_digest(request)
    payload.update(
        {
            "opening": {"en": row.widget_opening_en, "sw": row.widget_opening_sw},
            "consent": consent.visible_consent(row),
            "suggestions": [
                {"id": item.pk, "en": item.text_en, "sw": item.text_sw, "prompt": item.prompt_text}
                for item in SuggestedQuestion.objects.filter(is_active=True)
            ],
            "max_chars": row.widget_max_message_chars,
            "feedback": row.widget_feedback_enabled,
            "callback": {"purpose": callback_purpose(row)},
            "pow": pow.issue(digest) if row.widget_pow_enabled else None,
            "conversation": serialize_conversation(open_conversation(digest, create=False)),
        }
    )
    return payload


def _require_widget_on() -> AssistantSettings:
    row = AssistantSettings.read()
    reason = off_reason(row)
    if reason:
        raise Refused("off", reason, 403)
    return row


def begin(
    request,
    *,
    text: str,
    honeypot: str = "",
    pow_challenge: str = "",
    pow_nonce: str = "",
) -> Turn:
    """Validate one question and store it. Nothing is sent to a provider yet."""
    row = _require_widget_on()

    if (honeypot or "").strip():
        raise Refused("rejected", HONEYPOT_MESSAGE)

    digest = visitor_digest(request)
    if row.widget_pow_enabled and not pow.verify(digest, pow_challenge, pow_nonce):
        raise Refused("check_failed", CHECK_FAILED_MESSAGE)

    cleaned = clean_user_text(text or "")
    if not cleaned:
        raise Refused("empty", "Type a question first.")
    longest = row.widget_max_message_chars or 1000
    if len(cleaned) > longest:
        raise Refused(
            "too_long",
            f"Please shorten your question to {longest} characters or fewer.",
        )

    decision = limits.check_public_rate(request, row)
    if not decision.allowed:
        raise Refused("limit", decision.reason, 429)

    masked = mask_text(cleaned)
    body = masked.text
    conversation = open_conversation(digest, create=True)
    visitor_message = ChatMessage.objects.create(
        conversation=conversation,
        role=ChatMessage.Role.VISITOR,
        body=body,
        body_masked=masked.text,
        language=language_hint(cleaned),
    )
    limits.record_public_attempt(request)
    conversation.message_count += 1
    conversation.language = visitor_message.language or conversation.language
    conversation.last_activity_at = timezone.now()
    conversation.save(
        update_fields=["message_count", "language", "last_activity_at", "updated_at"]
    )
    return Turn(
        conversation=conversation,
        visitor_message=visitor_message,
        is_new_visitor=bool(getattr(request, "assistant_visitor_is_new", False)),
    )


def complete(turn: Turn, *, streamed: bool = False) -> Turn:
    """Answer the stored question and store the assistant's reply."""
    row = AssistantSettings.read()
    question = turn.visitor_message.body_masked or turn.visitor_message.body
    citations: list = []
    answer = None
    try:
        answer = answer_question(
            question,
            audience=scope.Audience("", (), False),
            profile=UsageEvent.Profile.PUBLIC,
            use_cache=True,
            record_gaps=True,
        )
    except Exception:  # noqa: BLE001 — never show a visitor a traceback
        log.exception("Public assistant answer failed")
        text = CHAT_UNAVAILABLE
        blocked = "unavailable"
    else:
        if answer.screened:
            alerts.create_alert(
                profile=UsageEvent.Profile.PUBLIC,
                category=(answer.categories or ["other"])[0],
                rule_id=(answer.rules_fired or [""])[0],
                raw_text=question,
            )
            text = answer.safe_reply or build_safe_reply(row)
            blocked = "safeguarding"
        elif answer.error:
            text = CHAT_UNAVAILABLE
            blocked = "unavailable"
        elif answer.verified and answer.answer.strip():
            text = answer.answer
            citations = answer.citations
            blocked = ""
        else:
            text = deliver(answer, row)
            blocked = "no_match" if answer.refused else "unverified"

    reply = ChatMessage.objects.create(
        conversation=turn.conversation,
        role=ChatMessage.Role.ASSISTANT,
        body=text[:MAX_REPLY_CHARS],
        language=answer.language if answer is not None else "",
        citations=citations,
        model_name=answer.model if answer is not None else "",
        provider_label=answer.provider_name if answer is not None else "",
        latency_ms=answer.latency_ms if answer is not None else 0,
        tokens_in=answer.tokens_in if answer is not None else 0,
        tokens_out=answer.tokens_out if answer is not None else 0,
        blocked_reason=blocked,
        is_streamed=streamed,
    )
    turn.conversation.message_count += 1
    turn.conversation.last_activity_at = timezone.now()
    turn.conversation.save(
        update_fields=["message_count", "last_activity_at", "updated_at"]
    )
    turn.reply_message = reply
    turn.reply_text = reply.body
    turn.citations = citations
    turn.blocked_reason = blocked

    # ── CP4: evaluate handoff triggers ───────────────────────────────────────
    # Only check if the conversation doesn't already have an open handoff.
    try:
        from .handoff.routing import create_handoff, detect_topics, should_handoff
        from .models import Handoff

        already_handed_off = Handoff.objects.filter(
            conversation=turn.conversation,
        ).exclude(status=Handoff.Status.RESOLVED).exists()

        if not already_handed_off:
            # Build turn_state so trigger signals can be evaluated
            turn_state = {
                "top_vector": answer.top_vector if answer else 1.0,
                "topics": detect_topics(question),
                "unhelpful_count": ChatMessage.objects.filter(
                    conversation=turn.conversation,
                    role=ChatMessage.Role.ASSISTANT,
                    blocked_reason__in=["no_match", "unverified"],
                ).count(),
            }
            rule = should_handoff(question, turn_state)
            if rule is not None:
                create_handoff(turn.conversation, rule, row)
    except Exception:  # noqa: BLE001 — routing failure must never break the chat reply
        log.exception("Handoff routing check failed for conversation %s", turn.conversation.pk)

    return turn


def turn_payload(turn: Turn) -> dict:
    return {
        "ok": True,
        "conversation_id": turn.conversation.pk,
        "visitor_message_id": turn.visitor_message.pk,
        "reply": serialize_message(turn.reply_message) if turn.reply_message else None,
    }


def start_new_conversation(request) -> dict:
    _require_widget_on()
    digest = visitor_digest(request)
    ChatConversation.objects.filter(
        visitor_key=digest, status=ChatConversation.Status.OPEN
    ).update(status=ChatConversation.Status.CLOSED, updated_at=timezone.now())
    open_conversation(digest, create=True)
    return state(request)


def record_feedback(
    request, *, message_id, kind: str, rating: str = "", reason: str = ""
) -> AnswerFeedback:
    row = _require_widget_on()
    if not row.widget_feedback_enabled:
        raise Refused("feedback_off", "Feedback is switched off.", 403)

    if kind not in dict(AnswerFeedback.Kind.choices):
        raise Refused("bad_kind", "Choose a rating or a report.")
    if kind == AnswerFeedback.Kind.RATING:
        if rating not in dict(AnswerFeedback.Rating.choices):
            raise Refused("bad_rating", "Choose helpful or not helpful.")

    digest = visitor_digest(request)
    try:
        message = ChatMessage.objects.select_related("conversation").get(
            pk=int(message_id),
            conversation__visitor_key=digest,
            role=ChatMessage.Role.ASSISTANT,
        )
    except (ChatMessage.DoesNotExist, TypeError, ValueError):
        raise Refused("not_found", "That message was not found.", 404) from None

    try:
        feedback = AnswerFeedback.objects.get(message=message, kind=kind)
        created = False
    except AnswerFeedback.DoesNotExist:
        try:
            feedback = AnswerFeedback.objects.create(
                message=message,
                kind=kind,
                rating=rating if kind == AnswerFeedback.Kind.RATING else "",
                reason=(reason or "").strip()[:MAX_FEEDBACK_REASON],
            )
            created = True
        except IntegrityError:
            feedback = AnswerFeedback.objects.get(message=message, kind=kind)
            created = False
    if not created:
        feedback.rating = rating if kind == AnswerFeedback.Kind.RATING else ""
        feedback.reason = (reason or "").strip()[:MAX_FEEDBACK_REASON]
        feedback.save(update_fields=["rating", "reason", "updated_at"])
    return feedback


def callback_purpose(row: AssistantSettings) -> str:
    days = row.callback_retention_days or 30
    return (
        "The school will use these details only to reply to you about this "
        f"conversation. They are deleted {days} days after your request is "
        "completed, or sooner if you ask."
    )


def request_callback(
    request, *, name: str, phone: str = "", email: str = "", honeypot: str = ""
) -> CallbackRequest:
    row = _require_widget_on()
    if (honeypot or "").strip():
        raise Refused("rejected", HONEYPOT_MESSAGE)

    name = (name or "").strip()
    phone = (phone or "").strip()
    email = (email or "").strip().lower()
    if not name:
        raise Refused("name", "Give the name the school should ask for.")
    if not (phone or email):
        raise Refused("contact", "Give a phone number or an email address.")
    if email:
        try:
            validate_email(email)
        except ValidationError:
            raise Refused("email", "That email address does not look right.") from None

    digest = visitor_digest(request)
    key = f"assistant:callback:{digest}:{timezone.localdate().isoformat()}"
    cache.add(key, 0, timeout=60 * 60 * 26)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=60 * 60 * 26)
        count = 1
    if count > CALLBACK_DAILY_LIMIT:
        raise Refused(
            "callback_limit",
            "The school already has your callback request. Please call the office instead.",
            429,
        )

    conversation = open_conversation(digest, create=False)
    existing = CallbackRequest.objects.filter(
        conversation=conversation,
        status=CallbackRequest.Status.NEW,
        name=name,
        phone=phone,
        email=email,
    ).first()
    if existing is not None:
        return existing

    return CallbackRequest.objects.create(
        conversation=conversation,
        name=name[:120],
        phone=phone[:40],
        email=email,
        purpose=callback_purpose(row),
    )
