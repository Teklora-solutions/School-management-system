"""
apps/assistant/handoff/services.py
------------------------------------
Write operations for the human inbox (CP5).

All mutations go through these functions so the views stay thin and the same
audit/notification logic runs whether the action comes from the inbox, an API
or a management command.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.audit_log import log_action
from apps.core.notifications import notify

from ..models import (
    AssistantSettings,
    ChatConversation,
    ChatMessage,
    Handoff,
    HandoffEvent,
    HandoffNote,
    HandoffPriority,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inbox queries (read helpers used by views)
# ---------------------------------------------------------------------------

def inbox_for_user(user) -> object:
    """
    Handoffs visible to ``user``:
      - admin: all
      - director: all (read-only enforced in views)
      - others: only handoffs assigned to them or their teams
    """
    from ..models import HandoffTeam

    qs = (
        Handoff.objects.select_related(
            "conversation", "rule", "assigned_user", "assigned_team"
        )
        .prefetch_related("notes")
        .exclude(status=Handoff.Status.RESOLVED)
        .order_by(
            # Critical first, then urgent, then normal; newest within priority
            "-priority",
            "sla_due_at",
            "-created_at",
        )
    )

    role = getattr(user, "role", "")
    if role in ("admin", "director"):
        return qs

    # Staff: assigned directly or via their teams
    team_ids = HandoffTeam.objects.filter(members=user).values_list("pk", flat=True)
    return qs.filter(
        models.Q(assigned_user=user)
        | models.Q(assigned_team__in=team_ids)
        | models.Q(destination_role=role)
    )


def import_models():
    from django.db import models as _m
    return _m


models = import_models()


def inbox_resolved_for_user(user) -> object:
    qs = Handoff.objects.filter(status=Handoff.Status.RESOLVED).order_by("-resolved_at")
    role = getattr(user, "role", "")
    if role in ("admin", "director"):
        return qs
    from ..models import HandoffTeam
    team_ids = HandoffTeam.objects.filter(members=user).values_list("pk", flat=True)
    return qs.filter(
        models.Q(assigned_user=user)
        | models.Q(assigned_team__in=team_ids)
    )


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

@transaction.atomic
def assign(handoff: Handoff, *, user=None, team=None, actor) -> Handoff:
    """Assign a handoff to a person or a team."""
    handoff.assigned_user = user
    handoff.assigned_team = team
    handoff.status = Handoff.Status.ASSIGNED
    handoff.save(update_fields=["assigned_user", "assigned_team", "status", "updated_at"])

    dest = (
        (user.get_full_name() or user.username)
        if user
        else (team.name if team else "unassigned")
    )
    HandoffEvent.objects.create(
        handoff=handoff,
        kind=HandoffEvent.Kind.ASSIGNED,
        actor=actor,
        detail=f"Assigned to {dest}",
        dedupe_key="",
    )
    log_action(
        actor=actor,
        action="assistant.handoff_assigned",
        content_type="assistant.Handoff",
        object_id=handoff.pk,
        change_summary={"dest": dest},
    )

    if user:
        try:
            url = reverse("assistant:inbox_detail", kwargs={"pk": handoff.pk})
            notify(
                recipient=user,
                title=f"Handoff assigned to you — {handoff.rule.name}",
                message=f"Priority: {handoff.get_priority_display()}.",
                link=url,
                category="handoff",
            )
        except Exception:  # noqa: BLE001
            pass

    return handoff


@transaction.atomic
def add_note(handoff: Handoff, *, body: str, actor) -> HandoffNote:
    """Add an internal note (never visible to the visitor)."""
    note = HandoffNote(handoff=handoff, author=actor, body=body.strip())
    note.full_clean()
    note.save()
    HandoffEvent.objects.create(
        handoff=handoff,
        kind=HandoffEvent.Kind.STATUS_CHANGED,
        actor=actor,
        detail="Note added",
        dedupe_key="",
    )
    return note


@transaction.atomic
def staff_reply(handoff: Handoff, *, body: str, actor) -> ChatMessage:
    """
    Write a staff reply into the conversation.  This is what appears in the
    visitor's widget as "A person from the school is now replying."
    """
    if not body.strip():
        from django.core.exceptions import ValidationError
        raise ValidationError({"body": "Write your reply before sending."})

    conversation = handoff.conversation

    msg = ChatMessage.objects.create(
        conversation=conversation,
        role=ChatMessage.Role.STAFF,
        body=body.strip(),
        staff_user=actor,
        language=conversation.language,
    )
    conversation.last_activity_at = timezone.now()
    conversation.message_count = models.F("message_count") + 1
    conversation.save(update_fields=["last_activity_at", "message_count", "updated_at"])

    if handoff.status in (Handoff.Status.NEW, Handoff.Status.ASSIGNED):
        handoff.status = Handoff.Status.IN_PROGRESS
        if not handoff.first_response_at:
            handoff.first_response_at = timezone.now()
        handoff.save(update_fields=["status", "first_response_at", "updated_at"])

    HandoffEvent.objects.create(
        handoff=handoff,
        kind=HandoffEvent.Kind.STAFF_REPLY,
        actor=actor,
        detail="",
        dedupe_key="",
    )
    log_action(
        actor=actor,
        action="assistant.handoff_staff_replied",
        content_type="assistant.Handoff",
        object_id=handoff.pk,
        change_summary={"chars": len(body)},
    )
    return msg


@transaction.atomic
def resolve(
    handoff: Handoff,
    *,
    actor,
    note: str = "",
    suggest_kb: bool = False,
) -> Handoff:
    """Resolve a handoff and optionally propose a KB Q&A pair."""
    handoff.status = Handoff.Status.RESOLVED
    handoff.resolved_at = timezone.now()
    handoff.resolved_by = actor
    handoff.resolution_note = (note or "").strip()[:500]
    handoff.save(
        update_fields=[
            "status", "resolved_at", "resolved_by", "resolution_note", "updated_at"
        ]
    )

    HandoffEvent.objects.create(
        handoff=handoff,
        kind=HandoffEvent.Kind.RESOLVED,
        actor=actor,
        detail=handoff.resolution_note[:100] if handoff.resolution_note else "",
        dedupe_key=f"resolved:{handoff.pk}",
    )
    log_action(
        actor=actor,
        action="assistant.handoff_resolved",
        content_type="assistant.Handoff",
        object_id=handoff.pk,
        change_summary={"suggest_kb": suggest_kb},
    )

    if suggest_kb and handoff.resolution_note:
        # Draft a Q&A pair in the knowledge base for admin review
        try:
            from ..knowledge.services import draft_qa_from_handoff
            draft_qa_from_handoff(handoff, actor=actor)
        except Exception:  # noqa: BLE001
            log.warning("Could not draft Q&A from handoff %s", handoff.pk)

    return handoff


@transaction.atomic
def escalate_overdue(handoff: Handoff, *, settings_row=None) -> bool:
    """
    Send a reminder to the assignee and then escalate to admin.
    Returns True when an escalation was actually performed.
    Called by the SLA check task.
    """
    if handoff.is_resolved or not handoff.is_overdue:
        return False

    # Deduplication: one reminder per handoff per overdue cycle
    dedupe = f"overdue_reminder:{handoff.pk}"
    if HandoffEvent.objects.filter(handoff=handoff, dedupe_key=dedupe).exists():
        # Already reminded — escalate to admin
        return _escalate_to_admin(handoff)

    # First notice: remind the assignee
    HandoffEvent.objects.create(
        handoff=handoff,
        kind=HandoffEvent.Kind.SLA_REMINDER,
        detail="Overdue",
        dedupe_key=dedupe,
    )

    target = handoff.assigned_user
    if not target and handoff.assigned_team:
        target = handoff.assigned_team.members.filter(is_active=True).first()

    if target:
        try:
            url = reverse("assistant:inbox_detail", kwargs={"pk": handoff.pk})
            notify(
                recipient=target,
                title=f"Overdue handoff — {handoff.rule.name}",
                message=f"This {handoff.get_priority_display()} handoff is past its response target.",
                link=url,
                category="safety" if handoff.is_critical else "handoff",
            )
        except Exception:  # noqa: BLE001
            pass

    return False


def _escalate_to_admin(handoff: Handoff) -> bool:
    from apps.accounts.models import User

    dedupe = f"escalated:{handoff.pk}"
    if HandoffEvent.objects.filter(handoff=handoff, dedupe_key=dedupe).exists():
        return False  # Already escalated

    HandoffEvent.objects.create(
        handoff=handoff,
        kind=HandoffEvent.Kind.SLA_ESCALATED,
        detail="Escalated to admin",
        dedupe_key=dedupe,
    )

    admins = User.objects.filter(role="admin", is_active=True)
    try:
        url = reverse("assistant:inbox_detail", kwargs={"pk": handoff.pk})
        for admin in admins:
            notify(
                recipient=admin,
                title=f"ESCALATED handoff — {handoff.rule.name}",
                message=(
                    f"A {handoff.get_priority_display()} handoff has not been answered. "
                    "No critical item will be silently dropped."
                ),
                link=url,
                category="safety",
            )
    except Exception:  # noqa: BLE001
        pass

    return True
