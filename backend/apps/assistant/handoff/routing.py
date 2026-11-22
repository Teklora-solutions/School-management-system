"""
apps/assistant/handoff/routing.py
----------------------------------
CP4 — trigger evaluation and handoff creation.

The handoff is the moment the assistant decides a human is needed.  It is a one-way
door: once a conversation has an open Handoff row every subsequent turn is flagged
"handed off" and the widget tells the visitor a person is on the way.

Call order
----------
1. ``should_handoff(turn, conversation, settings_row)``
   — Evaluate all enabled rules against the latest turn.  Returns the first
     matching rule (by ``order``), or ``None``.

2. ``create_handoff(conversation, rule, settings_row)``
   — Write the Handoff row, insert a system ChatMessage, freeze the packet,
     calculate the SLA deadline, and notify the destination.

Both are called from ``chat.complete()`` after the assistant's reply has been
stored, so the visitor always sees the reply before the handoff notice.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.notifications import notify, notify_many

from ..models import (
    AssistantSettings,
    ChatConversation,
    ChatMessage,
    Handoff,
    HandoffEvent,
    HandoffPriority,
    HandoffRule,
    HandoffTeam,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trigger detection helpers
# ---------------------------------------------------------------------------

_PERSON_RE = re.compile(
    r"\b(speak|talk|chat|connect)\s+(to|with)\s+(a\s+)?(human|person|staff|teacher|someone|someone\s+real)"
    r"|\btransfer\s+me\b"
    r"|\bi\s+want\s+(a\s+)?human\b"
    r"|\breal\s+person\b"
    r"|\bniliambie\s+mtu\b"
    r"|\b(kuzungumza|zungumza)\s+na\s+mtu\b",
    re.IGNORECASE,
)

_NEGATIVE_RE = re.compile(
    r"\b(terrible|horrible|disgusting|useless|rubbish|lawsuit|sue|legal\s+action"
    r"|furious|outraged|this\s+is\s+ridiculous|shocking|unacceptable)\b",
    re.IGNORECASE,
)

_LOW_CONFIDENCE_THRESHOLD = 0.30  # mirrors retrieval.MIN_SCORE


def _text_matches_signal(
    text: str,
    turn_state: dict,
    signal: str,
) -> bool:
    """True when the given signal is present in the latest turn."""
    if signal == HandoffRule.Signal.ASKS_FOR_PERSON:
        return bool(_PERSON_RE.search(text or ""))
    if signal == HandoffRule.Signal.NEGATIVE_SENTIMENT:
        return bool(_NEGATIVE_RE.search(text or ""))
    if signal == HandoffRule.Signal.LOW_CONFIDENCE:
        return float(turn_state.get("top_vector", 1.0)) < _LOW_CONFIDENCE_THRESHOLD
    if signal == HandoffRule.Signal.REPEATED_UNHELPFUL:
        return int(turn_state.get("unhelpful_count", 0)) >= 2
    return False


def _keyword_match(text: str, keywords: list[str]) -> bool:
    text_lower = (text or "").lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _topic_in_turn(topic: str, turn_state: dict) -> bool:
    """
    Topics cannot be detected from free text alone without an LLM classifier;
    they are detected from structured signals the chat service has already
    computed.  The ``turn_state`` dict carries a ``"topics"`` list of codes
    detected by heuristic keyword sets.
    """
    return topic in turn_state.get("topics", [])


# Simple heuristic topic detector (no LLM needed; runs deterministically).
_TOPIC_PATTERNS: dict[str, list[re.Pattern]] = {
    HandoffRule.Topic.FEE_DISPUTE: [
        re.compile(r"\b(fee|invoice|receipt|payment|overcharged|refund|balance|owed)\b", re.I),
        re.compile(r"\b(ada|malipo|ankara|risiti)\b", re.I),
    ],
    HandoffRule.Topic.COMPLAINT: [
        re.compile(r"\b(complain|complaint|unfair|mistreated|discriminated|issue\s+with|problem\s+with)\b", re.I),
        re.compile(r"\b(lalamika|malalamiko)\b", re.I),
    ],
    HandoffRule.Topic.BULLYING: [
        re.compile(r"\b(bully|bullied|bullying|harass|threatening|intimidat|abuse|unsafe|hit\s+me|hitting)\b", re.I),
        re.compile(r"(kubully|anabully|anakubully|wanabully|kubully|kumkubully|vitisho|kusumbua|udanganyifu)", re.I),
    ],
    HandoffRule.Topic.MEDICAL: [
        re.compile(r"\b(sick|injured|hospital|nurse|doctor|ambulance|emergency|injured|hurt|accident|bleeding|fainted|unconscious)\b", re.I),
        re.compile(r"(mgonjwa|daktari|hospitali|dharura|ameumia|ameanguka)", re.I),
    ],
    HandoffRule.Topic.LEGAL_THREAT: [
        re.compile(r"\b(lawyer|solicitor|legal\s+(action|threat)|court|sue\s+(you|the|school)|police)\b", re.I),
        re.compile(r"\b(wakili|mahakama|polisi|hatua\s+za\s+kisheria)\b", re.I),
    ],
    HandoffRule.Topic.ADMISSIONS: [
        re.compile(r"\b(admission|apply|enroll|interview|visit\s+the\s+school|open\s+day|tour)\b", re.I),
        re.compile(r"\b(usajili|jiandikishe|mahojiano|siku\s+ya\s+wazi)\b", re.I),
    ],
    HandoffRule.Topic.TECHNICAL: [
        re.compile(r"\b(login|password|portal|app|broken|not\s+work|can't\s+(access|log|sign))\b", re.I),
    ],
    HandoffRule.Topic.RECORDS: [
        re.compile(r"\b(report\s+card|certificate|transcript|leaving\s+cert|my\s+(record|grade|mark|result)\s+(for|from))\b", re.I),
        re.compile(r"\b(ripoti\s+ya\s+mtoto|cheti\s+cha)\b", re.I),
    ],
}


def detect_topics(text: str) -> list[str]:
    """Return a list of topic codes whose heuristic patterns match ``text``."""
    found: list[str] = []
    for code, patterns in _TOPIC_PATTERNS.items():
        if any(p.search(text or "") for p in patterns):
            found.append(code)
    return found


def evaluate_rule(
    rule: HandoffRule,
    text: str,
    turn_state: dict,
    user=None,
) -> bool:
    """
    Return True if ``rule`` fires on this turn.

    A rule fires when **any** of its configured triggers matches.
    """
    if not rule.enabled:
        return False

    for topic in rule.topic_codes:
        if _topic_in_turn(topic, turn_state):
            return True

    for signal in rule.signal_codes:
        if signal == HandoffRule.Signal.KEYWORD:
            if _keyword_match(text, rule.keyword_list):
                return True
        elif _text_matches_signal(text, turn_state, signal):
            return True

    if rule.user_matches(user):
        return True

    return False


def should_handoff(
    text: str,
    turn_state: dict,
    user=None,
) -> HandoffRule | None:
    """
    Evaluate all enabled rules in order.  Returns the first that fires, or None.
    Required (safeguarding) rules are always first regardless of their ``order``
    field.
    """
    rules = list(
        HandoffRule.objects.filter(enabled=True)
        .prefetch_related("specific_users")
        .order_by("is_required", "order", "name")
    )
    # Required rules first
    rules.sort(key=lambda r: (0 if r.is_required else 1, r.order, r.name))
    for rule in rules:
        if evaluate_rule(rule, text, turn_state, user=user):
            return rule
    return None


# ---------------------------------------------------------------------------
# Handoff creation
# ---------------------------------------------------------------------------

def _resolve_destination(rule: HandoffRule) -> tuple[object | None, HandoffTeam | None, str]:
    """Return (user, team, role) for the primary destination of this rule."""
    dt = rule.destination_type
    if dt == HandoffRule.DestinationType.PERSON:
        return rule.destination_user, None, ""
    if dt == HandoffRule.DestinationType.TEAM:
        return None, rule.destination_team, ""
    if dt == HandoffRule.DestinationType.ROLE:
        return None, None, rule.destination_role
    return None, None, ""


def _resolve_fallback(rule: HandoffRule) -> tuple[object | None, HandoffTeam | None, str]:
    return rule.fallback_user, rule.fallback_team, rule.fallback_role


def _sla_due_at(rule: HandoffRule, settings_row, now=None) -> object:
    minutes = rule.response_target_for(settings_row)
    return (now or timezone.now()) + __import__("datetime").timedelta(minutes=minutes)


def _freeze_packet(
    conversation: ChatConversation,
    rule: HandoffRule,
) -> dict:
    """Frozen snapshot stored on the Handoff row."""
    messages = list(
        ChatMessage.objects.filter(conversation=conversation)
        .order_by("created_at", "pk")
        .values("role", "body", "created_at", "citations")
    )
    return {
        "rule_name": rule.name,
        "rule_priority": rule.priority,
        "destination_label": rule.destination_label,
        "messages": [
            {
                "role": m["role"],
                "body": m["body"],
                "ts": m["created_at"].isoformat() if m["created_at"] else None,
                "citations": m["citations"] or [],
            }
            for m in messages
        ],
        "frozen_at": timezone.now().isoformat(),
    }


def _handoff_notice(rule: HandoffRule, within_hours: bool) -> str:
    """The system message shown to the visitor when a handoff fires."""
    dest = rule.destination_label
    if not within_hours:
        msg_en = (
            (rule.outside_hours_message_en or "").strip()
            or f"The {dest} team are not available right now. "
               "They will see your message and reply as soon as possible."
        )
        msg_sw = (
            (rule.outside_hours_message_sw or "").strip()
            or f"Timu ya {dest} haipo sasa. "
               "Watapokea ujumbe wako na kujibu haraka iwezekanavyo."
        )
        return f"{msg_en}\n\n{msg_sw}"

    # Within working hours — positive confirmation
    target_minutes = rule.response_target_minutes
    if target_minutes:
        hours, mins = divmod(target_minutes, 60)
        if hours and mins:
            target_en = f"within {hours}h {mins}min"
            target_sw = f"ndani ya saa {hours} na dakika {mins}"
        elif hours:
            target_en = f"within {hours} hour{'s' if hours > 1 else ''}"
            target_sw = f"ndani ya saa {hours}"
        else:
            target_en = f"within {target_minutes} minutes"
            target_sw = f"ndani ya dakika {target_minutes}"
        time_en = f" Someone will reply {target_en}."
        time_sw = f" Mtu atajibu {target_sw}."
    else:
        time_en = " Someone will reply as soon as possible."
        time_sw = " Mtu atajibu haraka iwezekanavyo."

    msg_en = f"I'm passing this to {dest} ({rule.name}).{time_en}"
    msg_sw = f"Ninawasilisha hili kwa {dest} ({rule.name}).{time_sw}"
    return f"{msg_en}\n\n{msg_sw}"


def _notify_destination(handoff: Handoff, rule: HandoffRule) -> None:
    """Send in-app notifications to the people who need to act."""
    from apps.accounts.models import User
    from django.urls import NoReverseMatch

    try:
        inbox_url = reverse("assistant:inbox_detail", kwargs={"pk": handoff.pk})
    except NoReverseMatch:
        inbox_url = "/assistant/inbox/"

    title = f"{handoff.get_priority_display()} handoff — {rule.name}"
    message = (
        f"A conversation needs your attention. Rule: {rule.name}. "
        f"Priority: {handoff.get_priority_display()}."
    )
    category = "safety" if handoff.is_critical else "handoff"

    recipients: list = []

    if handoff.assigned_user:
        recipients.append(handoff.assigned_user)
    elif handoff.assigned_team:
        recipients = list(handoff.assigned_team.members.filter(is_active=True))
    elif handoff.destination_role:
        recipients = list(
            User.objects.filter(role=handoff.destination_role, is_active=True)
        )

    if not recipients:
        # Fallback: notify all admins
        recipients = list(User.objects.filter(role="admin", is_active=True))

    for recipient in recipients:
        try:
            notify(
                recipient=recipient,
                title=title,
                message=message,
                link=inbox_url,
                category=category,
            )
        except Exception:  # noqa: BLE001
            log.warning("Could not notify user %s for handoff %s", recipient.pk, handoff.pk)


@transaction.atomic
def create_handoff(
    conversation: ChatConversation,
    rule: HandoffRule,
    settings_row=None,
) -> Handoff:
    """
    Create a Handoff row for ``conversation``, write the visitor-facing system
    message, freeze the packet, set the SLA deadline and send notifications.

    Called only when no Handoff already exists for this conversation.
    """
    if settings_row is None:
        settings_row = AssistantSettings.read()

    # Resolve destination
    dest_user, dest_team, dest_role = _resolve_destination(rule)

    # Fall back when the primary destination is unset
    if not (dest_user or dest_team or dest_role):
        dest_user, dest_team, dest_role = _resolve_fallback(rule)

    now = timezone.now()
    within_hours = rule.within_working_hours(now)

    handoff = Handoff.objects.create(
        conversation=conversation,
        rule=rule,
        priority=rule.priority,
        status=Handoff.Status.NEW,
        assigned_user=dest_user,
        assigned_team=dest_team,
        destination_role=dest_role,
        packet=_freeze_packet(conversation, rule),
        sla_due_at=_sla_due_at(rule, settings_row, now),
    )

    # System message visible to the visitor
    notice_text = _handoff_notice(rule, within_hours)
    ChatMessage.objects.create(
        conversation=conversation,
        role=ChatMessage.Role.SYSTEM,
        body=notice_text,
        language=conversation.language,
    )

    # Audit event
    HandoffEvent.objects.create(
        handoff=handoff,
        kind=HandoffEvent.Kind.CREATED,
        detail=f"Rule: {rule.name}",
        dedupe_key=f"created:{handoff.pk}",
    )

    # Notify destination staff
    _notify_destination(handoff, rule)

    log.info(
        "Handoff %s created for conversation %s via rule '%s' (priority=%s)",
        handoff.pk,
        conversation.pk,
        rule.name,
        rule.priority,
    )
    return handoff
