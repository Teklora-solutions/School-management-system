"""
Knowledge gaps: the questions the assistant could not answer reliably.

Only the masked question and the audience shape are kept — no name, user, IP
address or session. Resolved gaps are proposed for erasure 90 days after they
are resolved. That period is DRAFT and awaits the school's approval; nothing is
deleted automatically until it is approved.
"""

from __future__ import annotations

import datetime

from django.utils import timezone

from ..models import KnowledgeGap

GAP_RETENTION_DAYS = 90


def open_gaps(include_tests: bool = False):
    queryset = KnowledgeGap.objects.filter(resolved_at__isnull=True)
    if not include_tests:
        queryset = queryset.filter(is_test=False)
    return queryset.order_by("-created_at")


def all_gaps(include_tests: bool = True):
    queryset = KnowledgeGap.objects.all()
    if not include_tests:
        queryset = queryset.filter(is_test=False)
    return queryset.order_by("-created_at")


def resolve(gap: KnowledgeGap, *, actor, note: str = "") -> KnowledgeGap:
    if gap.resolved_at is None:
        gap.resolved_at = timezone.now()
        gap.resolved_by = actor
        gap.resolution_note = (note or "")[:300]
        gap.save(update_fields=["resolved_at", "resolved_by", "resolution_note", "updated_at"])
    return gap


def erase(gap: KnowledgeGap) -> None:
    """Delete one gap. Available to the admin from the gaps screen."""
    gap.delete()


def erasable_gaps(*, now=None):
    """
    Resolved gaps past the DRAFT retention period. No scheduled job calls this
    yet: the period is a proposal, and the admin can delete a gap at any time.
    """
    now = now or timezone.now()
    boundary = now - datetime.timedelta(days=GAP_RETENTION_DAYS)
    return KnowledgeGap.objects.filter(resolved_at__isnull=False, resolved_at__lte=boundary)
