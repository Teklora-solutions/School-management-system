"""
Write paths for the knowledge base.

K1 stores documents and keeps a version snapshot. K2 adds reading: approving a
document queues text extraction, chunking and embeddings; leaving Approved or
Archived removes its passages immediately. Answering is K3.
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
from . import personal_data, versions


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


def sync_readiness(document: KnowledgeDocument) -> None:
    """
    Queue reading for a document that may answer; remove passages at once for
    one that may not. The queue runs after the surrounding save commits.
    """
    from . import ingest

    if (
        document.is_approved
        and document.collection.is_approved
        and document.collection.effective_on()
    ):
        ingest.request_ingestion(document)
    else:
        ingest.purge(document, KnowledgeDocument.IngestionState.SKIPPED)


def sync_collection_after_save(collection, *, previous_ready: bool) -> None:
    """Only touch a shelf's documents when the shelf starts or stops answering."""
    from . import ingest

    ready = collection.is_approved and collection.effective_on()
    if ready != previous_ready:
        ingest.sync_collection(collection)
    # The shelf's audience or dates may have changed without changing readiness.
    versions.bump()


def scan_for_personal_data(document: KnowledgeDocument) -> list:
    """
    Scan a document's text now, when the text is available without a network
    call. A failure here is not fatal: reading scans again before embedding.
    """
    from . import chunking, extraction

    if document.source_type == KnowledgeDocument.SourceType.URL:
        return []
    try:
        text = extraction.extract_text(document)
        passages = chunking.split_text(text)
    except (extraction.ExtractionError, chunking.ChunkingError):
        return []
    findings = personal_data.scan_passages(passages)
    personal_data.record_scan(document, findings)
    return findings


@transaction.atomic
def confirm_personal_data(document: KnowledgeDocument, *, actor, request=None) -> KnowledgeDocument:
    """
    Record the admin's decision to send a document that contains personal data.
    The exact wording accepted, who accepted it and when are kept.
    """
    from django.utils import timezone

    if document.pii_state != KnowledgeDocument.PIIState.FOUND:
        raise ValidationError("There is no detected personal data to confirm.")
    document.pii_state = KnowledgeDocument.PIIState.CONFIRMED
    document.pii_confirmed_by = actor
    document.pii_confirmed_at = timezone.now()
    document.pii_confirmation_text = personal_data.CONFIRMATION_TEXT
    document.save(
        update_fields=[
            "pii_state",
            "pii_confirmed_by",
            "pii_confirmed_at",
            "pii_confirmation_text",
            "updated_at",
        ]
    )
    log_action(
        actor=actor,
        action="assistant.knowledge_personal_data_confirmed",
        content_type="assistant.KnowledgeDocument",
        object_id=document.pk,
        change_summary={"title": document.title, "findings": len(document.pii_findings)},
        request=request,
    )
    sync_readiness(document)
    return document


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
    scan_for_personal_data(document)
    sync_readiness(document)
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
    sync_readiness(document)
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
    if status == KnowledgeStatus.APPROVED and document.pii_blocks_embeddings:
        raise ValidationError(
            "Personal data was detected in this document. Confirm it may be sent to "
            "the provider, or edit the document, before approving it."
        )
    previous = document.status
    document.status = status
    document.save(update_fields=["status", "updated_at"])
    if status != previous:
        sync_readiness(document)
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
