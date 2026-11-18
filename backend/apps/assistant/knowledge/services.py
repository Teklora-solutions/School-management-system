"""
Write paths for the knowledge base.

K1 stores documents and keeps a version snapshot. Extracting text, chunking and
writing embeddings are K2; answering from the documents is K3.
"""

from __future__ import annotations

import hashlib

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from apps.core.audit_log import log_action

from ..models import (
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeQAPair,
    KnowledgeStatus,
)


def file_digest(uploaded) -> str:
    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    return digest.hexdigest()


def snapshot(
    document: KnowledgeDocument, *, actor, note: str = "", first: bool = False
) -> KnowledgeDocumentVersion:
    """Append a version row and keep ``document.version`` in step."""
    latest = document.versions.aggregate(latest=Max("version"))["latest"] or 0
    number = 1 if first else max(document.version, latest) + 1
    version = KnowledgeDocumentVersion.objects.create(
        document=document,
        version=number,
        title=document.title,
        note=note,
        content=(
            document.body_text
            if document.source_type
            in (KnowledgeDocument.SourceType.PASTED, KnowledgeDocument.SourceType.FAQ)
            else ""
        ),
        source_url=document.source_url,
        file_sha256=document.file_sha256,
        created_by=actor,
    )
    document.version = number
    document.save(update_fields=["version", "updated_at"])
    return version


def _earlier_duplicate(document: KnowledgeDocument) -> KnowledgeDocument | None:
    candidates = KnowledgeDocument.objects.filter(
        collection=document.collection
    ).exclude(pk=document.pk)
    if document.file_sha256:
        found = candidates.filter(file_sha256=document.file_sha256).order_by("created_at").first()
        if found:
            return found
    if document.content_sha256:
        return candidates.filter(content_sha256=document.content_sha256).order_by("created_at").first()
    return None


def _scope_or_raise(document: KnowledgeDocument) -> None:
    problems = document.scope_problems()
    if problems:
        raise ValidationError(problems)


@transaction.atomic
def create_document(form, *, source_type: str, actor, request) -> KnowledgeDocument:
    document = form.save(commit=False)
    document.source_type = source_type
    document.owner = actor
    if document.file:
        document.file_sha256 = file_digest(document.file)
    document.save()
    form.save_m2m()

    _scope_or_raise(document)

    duplicate = _earlier_duplicate(document)
    if duplicate:
        document.duplicate_of = duplicate
        document.save(update_fields=["duplicate_of", "updated_at"])

    snapshot(document, actor=actor, note="First version.", first=True)
    log_action(
        actor=actor,
        action="assistant.knowledge_document_added",
        content_type="assistant.KnowledgeDocument",
        object_id=document.pk,
        change_summary={
            "title": document.title,
            "collection": document.collection.name,
            "source_type": document.source_type,
            "status": document.status,
            "duplicate_of": document.duplicate_of_id,
        },
        request=request,
    )
    return document


@transaction.atomic
def update_document_metadata(
    form, *, actor, request, change_note: str = ""
) -> KnowledgeDocument:
    document = form.instance
    previous_title = type(document).objects.get(pk=document.pk).title
    # commit=True saves the many-to-many rows as part of save().
    document = form.save()

    _scope_or_raise(document)

    if document.title != previous_title:
        snapshot(document, actor=actor, note=change_note or "Title changed.")
    log_action(
        actor=actor,
        action="assistant.knowledge_document_changed",
        content_type="assistant.KnowledgeDocument",
        object_id=document.pk,
        change_summary={
            "title": document.title,
            "status": document.status,
            "audience": document.audience,
            "roles": document.roles,
        },
        request=request,
    )
    return document


@transaction.atomic
def set_status(document: KnowledgeDocument, status: str, *, actor, request) -> KnowledgeDocument:
    if status not in dict(KnowledgeStatus.choices):
        raise ValidationError("Unknown status.")
    document.status = status
    document.save(update_fields=["status", "updated_at"])
    log_action(
        actor=actor,
        action="assistant.knowledge_document_status",
        content_type="assistant.KnowledgeDocument",
        object_id=document.pk,
        change_summary={"title": document.title, "status": status},
        request=request,
    )
    return document


def save_qa_pairs(document: KnowledgeDocument, rows: list[dict]) -> int:
    """Replace a FAQ document's pairs with the submitted, non-empty rows."""
    document.qa_pairs.all().delete()
    created = 0
    for order, row in enumerate(rows):
        question = (row.get("question") or "").strip()
        answer = (row.get("answer") or "").strip()
        if not question or not answer:
            continue
        KnowledgeQAPair.objects.create(
            document=document,
            question=question,
            answer=answer,
            display_order=order,
        )
        created += 1
    return created


def qa_rows_from_formset(formset) -> list[dict]:
    """Complete pairs only; a blank extra row is not a pair."""
    rows = []
    for form in formset.forms:
        if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
            continue
        question = (form.cleaned_data.get("question") or "").strip()
        answer = (form.cleaned_data.get("answer") or "").strip()
        if question and answer:
            rows.append({"question": question, "answer": answer})
    return rows
