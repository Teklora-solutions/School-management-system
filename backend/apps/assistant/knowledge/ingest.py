"""
Read an approved document: extract text, split it, embed the passages and
store them as one set.

Only here, and only from a background job, does a document turn into
searchable passages. The request that approves a document merely queues the
work. The whole passage set is replaced after a successful embedding, so a
failed re-index can never leave old wording next to new wording. While a
document is not approved its passages are removed immediately.

Nothing in this module writes document text to a log, an audit row or an error
message. ``last_error`` and ``KnowledgeIngestRun.error`` carry plain wording
only.
"""

from __future__ import annotations

import hashlib

from django.db import transaction
from django.utils import timezone

from .. import gateway, selectors
from ..models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIngestRun,
    KnowledgeStatus,
    UsageEvent,
)
from ..providers import registry
from . import chunking, extraction

BATCH_SIZE = 32
MAX_ATTEMPTS = 3
TASK_PATH = "apps.assistant.tasks.ingest_knowledge_document_task"
TASK_NAME = "assistant.knowledge_ingestion"


class IngestionFailed(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        self.safe_message = message
        self.retryable = retryable
        super().__init__(message)


# ── Queueing ─────────────────────────────────────────────────────────────────


def enqueue_ingestion(document_id: int) -> None:
    from django_q.tasks import async_task

    async_task(TASK_PATH, document_id, 1)


def request_ingestion(document: KnowledgeDocument, *, on_commit: bool = True) -> None:
    """Mark a document as waiting and queue it, once the save is committed."""
    document.ingestion_state = KnowledgeDocument.IngestionState.PENDING
    document.last_error = ""
    document.save(update_fields=["ingestion_state", "last_error", "updated_at"])
    if on_commit:
        transaction.on_commit(lambda: enqueue_ingestion(document.pk))
    else:
        enqueue_ingestion(document.pk)


def purge(document: KnowledgeDocument, state: str = KnowledgeDocument.IngestionState.SKIPPED) -> None:
    """Remove passages at once when a document must no longer answer."""
    with transaction.atomic():
        document.chunks.all().delete()
        document.ingestion_state = state
        document.chunk_count = 0
        document.indexed_model = ""
        document.indexed_at = None
        document.is_stale = False
        document.save(
            update_fields=[
                "ingestion_state",
                "chunk_count",
                "indexed_model",
                "indexed_at",
                "is_stale",
                "updated_at",
            ]
        )


def sync_collection(collection) -> None:
    """
    A shelf that stops being approved must stop answering immediately; a shelf
    that becomes approved queues its approved documents.
    """
    if collection.is_approved and collection.effective_on():
        for document in collection.documents.filter(
            status=KnowledgeStatus.APPROVED
        ).order_by("pk"):
            request_ingestion(document)
    else:
        for document in collection.documents.order_by("pk"):
            purge(document, KnowledgeDocument.IngestionState.SKIPPED)


def mark_stale_for_models(active_models: set[str]) -> int:
    """
    After an embedding model change, every passage embedded by a model no
    longer in service is flagged. Re-indexing one document clears its flag.
    """
    documents = KnowledgeDocument.objects.filter(
        ingestion_state=KnowledgeDocument.IngestionState.READY
    ).exclude(indexed_model="")
    if active_models:
        documents = documents.exclude(indexed_model__in=active_models)
    return documents.update(is_stale=True)


# ── The read ─────────────────────────────────────────────────────────────────


def ingest_document(
    document_id: int, *, attempt: int = 1, actor=None
) -> KnowledgeIngestRun:
    """
    Read one document. Raises ``IngestionFailed`` after recording the run and
    the document's failed state, so the worker can decide about a retry.
    """
    document = KnowledgeDocument.objects.select_related("collection").get(pk=document_id)
    run = KnowledgeIngestRun.objects.create(
        document=document,
        status=KnowledgeIngestRun.Status.RUNNING,
        attempt=attempt,
    )

    if not _may_answer(document):
        run.status = KnowledgeIngestRun.Status.SKIPPED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "updated_at"])
        purge(document)
        return run

    document.ingestion_state = KnowledgeDocument.IngestionState.RUNNING
    document.last_error = ""
    document.save(update_fields=["ingestion_state", "last_error", "updated_at"])

    try:
        text = extraction.extract_text(document)
        if document.source_type == KnowledgeDocument.SourceType.URL:
            # The fetched page is kept so an admin can see what was read.
            document.body_text = text
            document.save(update_fields=["body_text", "updated_at"])
        passages = chunking.split_text(text)
        vectors, model_name, dimension, tokens = _embed(document, passages, actor)
    except extraction.ExtractionError as exc:
        _fail(document, run, exc.safe_message)
        raise IngestionFailed(exc.safe_message) from None
    except chunking.ChunkingError as exc:
        _fail(document, run, exc.safe_message)
        raise IngestionFailed(exc.safe_message) from None
    except gateway.AllProvidersUnavailable as exc:
        message = getattr(exc, "detail", "") or str(exc)
        retryable = getattr(exc, "retryable", True)
        _fail(document, run, message)
        raise IngestionFailed(message, retryable=retryable) from None
    except IngestionFailed as exc:
        _fail(document, run, exc.safe_message)
        raise

    now = timezone.now()
    with transaction.atomic():
        document.chunks.all().delete()
        KnowledgeChunk.objects.bulk_create(
            [
                KnowledgeChunk(
                    document=document,
                    ordinal=index,
                    heading=passage.heading,
                    text=passage.text,
                    char_count=len(passage.text),
                    content_sha256=hashlib.sha256(passage.text.encode("utf-8")).hexdigest(),
                    embedding=vector,
                    embedding_model=model_name,
                    dimension=dimension,
                    embedded_at=now,
                )
                for index, (passage, vector) in enumerate(zip(passages, vectors))
            ]
        )
        document.ingestion_state = KnowledgeDocument.IngestionState.READY
        document.chunk_count = len(passages)
        document.indexed_model = model_name
        document.indexed_at = now
        document.is_stale = False
        document.last_error = ""
        document.save(
            update_fields=[
                "ingestion_state",
                "chunk_count",
                "indexed_model",
                "indexed_at",
                "is_stale",
                "last_error",
                "updated_at",
            ]
        )
        run.status = KnowledgeIngestRun.Status.READY
        run.finished_at = now
        run.chunks_written = len(passages)
        run.tokens = tokens
        run.error = ""
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "chunks_written",
                "tokens",
                "error",
                "updated_at",
            ]
        )
    return run


def _may_answer(document: KnowledgeDocument) -> bool:
    return (
        document.is_approved
        and document.collection.is_approved
        and document.collection.effective_on()
    )


def _embed(document, passages: list[chunking.Chunk], actor):
    providers = selectors.providers_in_order()
    vectors: list[list[float]] = []
    model_name = ""
    dimension = 0
    tokens = 0

    for start in range(0, len(passages), BATCH_SIZE):
        batch = passages[start : start + BATCH_SIZE]
        result = gateway.embed_chain(
            providers,
            [passage.text for passage in batch],
            actor=actor,
            profile=UsageEvent.Profile.INGESTION,
        )
        reply = result.reply
        batch_model = (reply.model or registry.model_for(result.provider, "embed")).strip()
        batch_dimension = reply.dimension or len(reply.vectors[0])
        if model_name and (batch_model != model_name or batch_dimension != dimension):
            raise IngestionFailed(
                "The embedding provider changed while the document was being read. "
                "Try again when one provider is stable.",
                retryable=True,
            )
        model_name = batch_model
        dimension = batch_dimension
        tokens += getattr(reply, "tokens", 0) or 0
        vectors.extend(reply.vectors)

    if len(vectors) != len(passages):
        raise IngestionFailed("The provider returned an incomplete set of passages.")
    return vectors, model_name, dimension, tokens


def _fail(
    document: KnowledgeDocument,
    run: KnowledgeIngestRun,
    message: str,
) -> None:
    """Remove passages and record the plain reason on both rows."""
    purge(document, KnowledgeDocument.IngestionState.FAILED)
    KnowledgeDocument.objects.filter(pk=document.pk).update(last_error=message[:300])
    document.last_error = message[:300]
    run.status = KnowledgeIngestRun.Status.FAILED
    run.error = message[:300]
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at", "updated_at"])
