"""
Audience and class scoping for the knowledge base.

The rule that matters: a document may be NARROWER than its collection, never
broader. A public shelf can hold a signed-in document; a signed-in shelf can
never hold a public one, because that would quietly widen who can read it.

Retrieval (K3) must call ``visible_documents()`` BEFORE ranking, so a document
the reader may not see never influences an answer or a citation.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone

from ..models import (
    KnowledgeAudience,
    KnowledgeDocument,
    KnowledgeStatus,
)
from .roles import ROLE_LABELS, roles_list, VALID_ROLES

_AUDIENCE_RANK = {
    KnowledgeAudience.PUBLIC: 0,
    KnowledgeAudience.SIGNED_IN: 1,
}


def _clean_roles(roles) -> list[str]:
    return roles_list(roles)


def scope_problems(
    *,
    audience: str,
    roles,
    class_ids,
    parent_audience: str,
    parent_roles,
    parent_class_ids,
) -> list[str]:
    """
    Return plain-language reasons a document scope is not inside its parent
    scope. An empty list means the scope is allowed.
    """
    reasons: list[str] = []
    roles = _clean_roles(roles)
    parent_roles = _clean_roles(parent_roles)
    class_ids = {int(pk) for pk in (class_ids or [])}
    parent_class_ids = {int(pk) for pk in (parent_class_ids or [])}

    if _AUDIENCE_RANK.get(audience, 99) < _AUDIENCE_RANK.get(parent_audience, 99):
        reasons.append(
            "This document is public, but its collection is signed-in only. "
            "Choose signed-in, or move it to a public collection."
        )

    if audience == KnowledgeAudience.PUBLIC and roles:
        reasons.append("A public document cannot be limited to specific roles.")
    if audience == KnowledgeAudience.PUBLIC and class_ids:
        reasons.append("A public document cannot be limited to specific classes.")

    if parent_roles:
        if audience == KnowledgeAudience.PUBLIC:
            # Covered above; kept separate so the reason names the collection.
            pass
        elif not roles:
            reasons.append(
                "The collection is limited to some roles, so this document must "
                "name the roles it is for."
            )
        elif not set(roles).issubset(parent_roles):
            outside = sorted(set(roles) - set(parent_roles))
            reasons.append(
                "This document names roles the collection does not allow: "
                + ", ".join(ROLE_LABELS.get(role, role) for role in outside)
                + "."
            )

    if parent_class_ids:
        if not class_ids:
            reasons.append(
                "The collection is limited to some classes, so this document "
                "must name the classes it is for."
            )
        elif not class_ids.issubset(parent_class_ids):
            reasons.append(
                "This document names classes the collection does not allow."
            )

    return reasons


def collection_problems(*, audience: str, roles, class_ids) -> list[str]:
    """A collection has no parent, so only its own consistency is checked."""
    return scope_problems(
        audience=audience,
        roles=roles,
        class_ids=class_ids,
        parent_audience=KnowledgeAudience.PUBLIC,
        parent_roles=[],
        parent_class_ids=[],
    )


def document_within_collection(document: KnowledgeDocument) -> bool:
    """Belt-and-braces check for K3, where a row may have been edited by hand."""
    if not document.collection_id:
        return False
    return not document.scope_problems()


def audience_summary(audience: str, roles, class_names) -> str:
    if audience == KnowledgeAudience.PUBLIC:
        text = "Public"
    elif roles:
        text = "Signed in: " + ", ".join(
            ROLE_LABELS.get(role, role) for role in _clean_roles(roles)
        )
    else:
        text = "Signed in (every role)"
    class_names = [name for name in (class_names or []) if name]
    if class_names:
        text += " · classes: " + ", ".join(class_names)
    return text


def user_class_ids(user) -> set[int]:
    """The class ids a user belongs to, for class-scoped documents."""
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    role = getattr(user, "role", "")
    if role == "student":
        profile = getattr(user, "student_profile", None)
        class_id = getattr(profile, "current_class_id", None)
        return {class_id} if class_id else set()
    if role == "parent":
        profile = getattr(user, "parent_profile", None)
        if profile is None:
            return set()
        from apps.students.models import Student

        return set(
            Student.objects.filter(
                guardian_links__parent=profile,
                status=Student.Status.ACTIVE,
                current_class__isnull=False,
            ).values_list("current_class_id", flat=True)
        )
    if role == "teacher":
        from apps.academics.models import SchoolClass

        return set(
            SchoolClass.objects.filter(class_teacher=user).values_list("pk", flat=True)
        )
    return set()


def _effective_q(prefix: str, day: datetime.date) -> Q:
    return Q(**{f"{prefix}effective_from__isnull": True}) | Q(
        **{f"{prefix}effective_from__lte": day}
    )


def _not_expired_q(prefix: str, day: datetime.date) -> Q:
    return Q(**{f"{prefix}effective_to__isnull": True}) | Q(
        **{f"{prefix}effective_to__gte": day}
    )


@dataclass(frozen=True)
class Audience:
    """
    Who is reading. One object serves real users and the admin preview, so the
    preview can never take a shortcut around the visibility rules.
    """

    role: str  # empty for a visitor
    class_ids: tuple[int, ...] = ()
    is_authenticated: bool = False

    @property
    def is_visitor(self) -> bool:
        return not self.is_authenticated

    @property
    def signature(self) -> str:
        """Stable key material for caches. Roles and classes only, no identity."""
        if self.is_visitor:
            return "visitor"
        classes = ",".join(str(pk) for pk in sorted(self.class_ids))
        return f"{self.role}|{classes}"

    @property
    def label(self) -> str:
        if self.is_visitor:
            return "Visitor"
        label = ROLE_LABELS.get(self.role, self.role)
        if self.class_ids:
            label += " · class ids " + ", ".join(str(pk) for pk in sorted(self.class_ids))
        return label


def audience_for_user(user) -> Audience:
    if not user or not getattr(user, "is_authenticated", False):
        return Audience("", (), False)
    return Audience(
        role=getattr(user, "role", "") or "",
        class_ids=tuple(sorted(user_class_ids(user))),
        is_authenticated=True,
    )


def audience_for_preview(role: str, class_ids=()) -> Audience:
    """The answer desk builds the same object a real reader would get."""
    role = (role or "").strip()
    if role in ("", "visitor"):
        return Audience("", (), False)
    if role not in VALID_ROLES:
        raise ValueError("Unknown role for preview.")
    return Audience(
        role=role,
        class_ids=tuple(sorted({int(pk) for pk in (class_ids or [])})),
        is_authenticated=True,
    )


def scoped_documents(audience: Audience, on_date: datetime.date | None = None):
    """
    Approved documents the audience may see, as a queryset, so the filter runs
    in the database before any ranking. Effective dates are applied at read
    time, which is what makes a cached ranking safe to reuse.
    """
    day = on_date or timezone.localdate()
    queryset = (
        KnowledgeDocument.objects.filter(
            status=KnowledgeStatus.APPROVED,
            collection__status=KnowledgeStatus.APPROVED,
        )
        .filter(_effective_q("", day), _not_expired_q("", day))
        .filter(
            _effective_q("collection__", day),
            _not_expired_q("collection__", day),
        )
        # A document can never be broader than its collection.
        .filter(
            Q(collection__audience=KnowledgeAudience.PUBLIC)
            | Q(
                collection__audience=KnowledgeAudience.SIGNED_IN,
                audience=KnowledgeAudience.SIGNED_IN,
            )
        )
    )

    public_q = Q(audience=KnowledgeAudience.PUBLIC, roles="") & Q(
        classes__isnull=True
    )
    if audience.is_visitor:
        return queryset.filter(public_q).distinct()

    role = audience.role
    class_ids = set(audience.class_ids)
    # Canonical role strings let both SQLite and PostgreSQL filter with LIKE,
    # so the audience filter stays in the database before any ranking.
    signed_in_q = Q(audience=KnowledgeAudience.SIGNED_IN) & (
        Q(roles__contains=f"|{role}|") | Q(roles="")
    )
    if class_ids:
        signed_in_q &= Q(classes__isnull=True) | Q(classes__in=class_ids)
    else:
        signed_in_q &= Q(classes__isnull=True)

    return queryset.filter(public_q | signed_in_q).distinct()


def visible_collection_ids(audience: Audience, on_date: datetime.date | None = None) -> list[int]:
    return sorted(
        set(scoped_documents(audience, on_date).values_list("collection_id", flat=True))
    )


def visible_documents(user, on_date: datetime.date | None = None):
    """Backwards-compatible wrapper: a real user's visible documents."""
    return scoped_documents(audience_for_user(user), on_date)

