"""
The reading pipeline: embedding calls, ingest runs, lifecycle hooks, retries,
the re-index screen and the bulk command.

The fake provider is the only provider used. No test needs a key or a network.
"""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse

from apps.assistant import gateway, limits
from apps.assistant import services as assistant_services
from apps.assistant.knowledge import ingest
from apps.assistant.knowledge import services as knowledge_services
from apps.assistant.models import (
    AssistantJobFailure,
    AssistantSettings,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIngestRun,
    KnowledgeStatus,
    ProviderConfig,
    UsageEvent,
)
from apps.assistant.providers.base import EmbeddingReply
from apps.assistant.tasks import ingest_knowledge_document_task
from apps.core.models import AuditLog

from .knowledge_helpers import KnowledgeTestCase

BACKEND = "apps.accounts.backends.EmailOrAdmissionBackend"

LONG_TEXT = "Fees are due at the start of each term and must be paid on time. " * 20


class IngestionTestCase(KnowledgeTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        row = AssistantSettings.load()
        row.enabled = True
        row.monthly_budget_ksh = Decimal("1000")
        row.usd_to_ksh = Decimal("130")
        row.save()
        self.provider = self.make_embed_provider()

    def make_embed_provider(self, name="Fake embeddings", **overrides):
        data = {
            "name": name,
            "kind": ProviderConfig.Kind.FAKE,
            "model_name": "fake",
            "embedding_model_name": "fake-embed",
            "embedding_usd_per_1m": Decimal("0.02"),
            "priority": 1,
            "is_active": True,
        }
        data.update(overrides)
        return ProviderConfig.objects.create(**data)

    def make_readable(self, title="Fee policy", body=LONG_TEXT, **kwargs):
        collection = kwargs.pop("collection", None) or self.make_collection(
            f"Readable {title}"
        )
        return self.make_document(collection, title=title, body_text=body, **kwargs)

    def edit_payload(self, document, **overrides):
        data = {
            "collection": document.collection_id,
            "title": document.title,
            "language": document.language,
            "audience": document.audience,
            "status": document.status,
            "effective_from": "",
            "effective_to": "",
            "change_note": "",
        }
        data.update(overrides)
        return data


# ── Embedding gateway ────────────────────────────────────────────────────────


class EmbeddingGatewayTests(IngestionTestCase):
    def test_embedding_success_records_usage_with_the_embedding_price(self):
        result = gateway.embed_chain([self.provider], ["hello school", "fees are due"])
        self.assertEqual(len(result.reply.vectors), 2)
        row = UsageEvent.objects.get()
        self.assertEqual(row.source, UsageEvent.Source.INGESTION)
        self.assertEqual(row.profile, UsageEvent.Profile.INGESTION)
        self.assertEqual(row.tokens_in, result.reply.tokens)
        self.assertEqual(
            row.estimated_cost_ksh,
            limits.embedding_cost_ksh(
                self.provider, AssistantSettings.load(), result.reply.tokens
            ),
        )

    def test_usage_never_contains_the_passage_text(self):
        gateway.embed_chain([self.provider], ["a secret fee arrangement"])
        self.assertNotIn("secret", UsageEvent.objects.get().detail)

    def test_embedding_falls_back_to_the_next_provider(self):
        failing = self.make_embed_provider(
            name="Broken embeddings",
            model_name="fake-auth",
            embedding_model_name="fake-embed",
            priority=1,
        )
        good = self.make_embed_provider(name="Good embeddings", priority=2)
        result = gateway.embed_chain([failing, good], ["hello"], sleep=lambda _: None)
        self.assertTrue(result.used_fallback)
        self.assertEqual(result.provider, good)
        self.assertEqual(UsageEvent.objects.filter(outcome=UsageEvent.Outcome.ERROR).count(), 1)

    def test_a_provider_without_an_embedding_model_is_skipped(self):
        no_model = self.make_embed_provider(
            name="Chat only", embedding_model_name="", priority=1
        )
        result = gateway.embed_chain([no_model, self.provider], ["hello"])
        self.assertEqual(result.provider, self.provider)
        self.assertIn("no embedding model", result.skipped[0])
        self.assertEqual(UsageEvent.objects.count(), 1)

    def test_no_provider_can_embed_gives_a_plain_reason(self):
        ProviderConfig.objects.all().delete()
        with self.assertRaises(gateway.AllProvidersUnavailable) as caught:
            gateway.embed_chain([], ["hello"])
        self.assertIn("embedding model", str(caught.exception).lower())

    def test_no_budget_refuses_before_any_provider_call(self):
        row = AssistantSettings.load()
        row.monthly_budget_ksh = Decimal("0")
        row.save()
        with self.assertRaises(gateway.AllProvidersUnavailable) as caught:
            gateway.embed_chain([self.provider], ["hello"])
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(UsageEvent.objects.count(), 0)

    def test_an_open_circuit_skips_the_provider(self):
        cache.set(f"assistant:circuit:{self.provider.pk}", {"open_until": 1}, 300)
        good = self.make_embed_provider(name="Backup embeddings", priority=2)
        result = gateway.embed_chain([self.provider, good], ["hello"])
        self.assertEqual(result.provider, good)
        self.assertIn("circuit open", result.skipped[0])

    def test_a_provider_at_its_own_monthly_budget_is_skipped(self):
        ProviderConfig.objects.all().delete()
        capped = self.make_embed_provider(
            name="Capped embeddings", monthly_budget_ksh=Decimal("0")
        )
        with self.assertRaises(gateway.AllProvidersUnavailable):
            gateway.embed_chain([capped], ["hello"])
        self.assertEqual(UsageEvent.objects.count(), 0)

    def test_the_wrong_number_of_vectors_is_malformed(self):
        class WrongCount:
            def embed(self, texts, *, model=None, timeout=None):
                return EmbeddingReply(
                    vectors=[[0.0] * 8], tokens=2, model="stub", dimension=8
                )

        with patch("apps.assistant.gateway.registry.build", return_value=WrongCount()):
            with self.assertRaises(gateway.AllProvidersUnavailable) as caught:
                gateway.embed_chain([self.provider], ["one", "two"])
        self.assertIn("wrong number", caught.exception.detail)
        self.assertEqual(UsageEvent.objects.get().outcome, UsageEvent.Outcome.ERROR)

    def test_a_wrong_vector_size_is_malformed(self):
        class WrongSize:
            def embed(self, texts, *, model=None, timeout=None):
                return EmbeddingReply(
                    vectors=[[0.0] * 3, [0.0] * 3], tokens=2, model="stub", dimension=8
                )

        with patch("apps.assistant.gateway.registry.build", return_value=WrongSize()):
            with self.assertRaises(gateway.AllProvidersUnavailable):
                gateway.embed_chain([self.provider], ["one", "two"])


# ── The read itself ──────────────────────────────────────────────────────────


class IngestDocumentTests(IngestionTestCase):
    def test_an_approved_document_becomes_ready_with_passages(self):
        document = self.make_readable()
        run = ingest.ingest_document(document.pk)

        document.refresh_from_db()
        self.assertEqual(run.status, KnowledgeIngestRun.Status.READY)
        self.assertEqual(document.ingestion_state, KnowledgeDocument.IngestionState.READY)
        self.assertGreater(document.chunk_count, 1)
        self.assertEqual(document.chunk_count, document.chunks.count())
        self.assertEqual(document.indexed_model, "fake-embed")
        self.assertIsNotNone(document.indexed_at)
        self.assertFalse(document.is_stale)
        first = document.chunks.order_by("ordinal").first()
        self.assertEqual(first.ordinal, 0)
        self.assertEqual(first.dimension, 8)
        self.assertEqual(len(first.embedding), 8)
        self.assertEqual(first.embedding_model, "fake-embed")
        self.assertEqual(len(first.content_sha256), 64)
        self.assertIn("Fees are due", first.text)

    def test_the_read_records_usage_and_an_ingest_run(self):
        document = self.make_readable()
        ingest.ingest_document(document.pk)
        run = KnowledgeIngestRun.objects.get()
        self.assertEqual(run.attempt, 1)
        self.assertGreater(run.tokens, 0)
        self.assertEqual(run.chunks_written, document.chunks.count())
        self.assertTrue(UsageEvent.objects.filter(profile=UsageEvent.Profile.INGESTION).exists())

    def test_rerunning_replaces_the_passages_instead_of_duplicating(self):
        document = self.make_readable()
        ingest.ingest_document(document.pk)
        count = document.chunks.count()
        ingest.ingest_document(document.pk)
        document.refresh_from_db()
        self.assertEqual(document.chunks.count(), count)
        self.assertEqual(KnowledgeIngestRun.objects.count(), 2)

    def test_a_draft_is_skipped_without_calling_the_provider(self):
        document = self.make_readable(status=KnowledgeStatus.DRAFT)
        run = ingest.ingest_document(document.pk)
        document.refresh_from_db()
        self.assertEqual(run.status, KnowledgeIngestRun.Status.SKIPPED)
        self.assertEqual(document.ingestion_state, KnowledgeDocument.IngestionState.SKIPPED)
        self.assertEqual(document.chunks.count(), 0)
        self.assertEqual(UsageEvent.objects.count(), 0)

    def test_a_document_on_an_unapproved_shelf_is_skipped(self):
        collection = self.make_collection("Draft shelf", status=KnowledgeStatus.DRAFT)
        document = self.make_readable(collection=collection)
        run = ingest.ingest_document(document.pk)
        self.assertEqual(run.status, KnowledgeIngestRun.Status.SKIPPED)
        self.assertEqual(document.chunks.count(), 0)

    def test_a_future_shelf_is_skipped(self):
        import datetime

        collection = self.make_collection(
            "Future shelf", effective_from=datetime.date(2099, 1, 1)
        )
        document = self.make_readable(collection=collection)
        run = ingest.ingest_document(document.pk)
        self.assertEqual(run.status, KnowledgeIngestRun.Status.SKIPPED)

    def test_a_read_failure_removes_old_passages_and_records_the_reason(self):
        document = self.make_readable(body="")
        KnowledgeChunk.objects.create(
            document=document,
            ordinal=0,
            text="Old wording that must not survive.",
            char_count=30,
            embedding=[0.1] * 8,
            embedding_model="old",
            dimension=8,
        )
        with self.assertRaises(ingest.IngestionFailed) as caught:
            ingest.ingest_document(document.pk)

        document.refresh_from_db()
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(document.ingestion_state, KnowledgeDocument.IngestionState.FAILED)
        self.assertTrue(document.last_error)
        self.assertEqual(document.chunks.count(), 0)
        run = KnowledgeIngestRun.objects.get()
        self.assertEqual(run.status, KnowledgeIngestRun.Status.FAILED)
        self.assertEqual(run.error, document.last_error)

    def test_a_budget_refusal_is_not_retried(self):
        row = AssistantSettings.load()
        row.monthly_budget_ksh = Decimal("0")
        row.save()
        document = self.make_readable()
        with self.assertRaises(ingest.IngestionFailed) as caught:
            ingest.ingest_document(document.pk)
        self.assertFalse(caught.exception.retryable)
        self.assertIn("budget", str(caught.exception))
        document.refresh_from_db()
        self.assertEqual(document.ingestion_state, KnowledgeDocument.IngestionState.FAILED)

    def test_a_fetched_page_is_kept_as_saved_text(self):
        document = self.make_document(
            self.make_collection("Web"),
            title="Web page",
            source_type="url",
            source_url="https://example.com/fees",
            body_text="",
        )
        with patch("apps.assistant.knowledge.extraction.fetch_url", return_value=LONG_TEXT):
            ingest.ingest_document(document.pk)
        document.refresh_from_db()
        self.assertEqual(document.body_text, LONG_TEXT.strip())

    def test_faq_pairs_are_embedded(self):
        collection = self.make_collection("FAQ shelf")
        document = self.make_document(
            collection, title="FAQ", source_type="faq", body_text=""
        )
        document.qa_pairs.create(
            question="When are fees due?",
            answer="Fees are due at the start of each term and must be paid on time.",
            display_order=1,
        )
        document.qa_pairs.create(
            question="How may I pay?",
            answer="Families may pay in three instalments during the term.",
            display_order=2,
        )
        ingest.ingest_document(document.pk)
        text = " ".join(document.chunks.values_list("text", flat=True))
        self.assertIn("When are fees due?", text)
        self.assertIn("three instalments", text)


# ── Lifecycle ────────────────────────────────────────────────────────────────


class LifecycleTests(IngestionTestCase):
    def test_approving_a_document_queues_a_read(self):
        document = self.make_readable(status=KnowledgeStatus.DRAFT)
        with patch("apps.assistant.knowledge.ingest.enqueue_ingestion") as queued:
            with self.captureOnCommitCallbacks(execute=True):
                knowledge_services.set_status(
                    document, KnowledgeStatus.APPROVED, actor=self.admin, request=None
                )
        document.refresh_from_db()
        queued.assert_called_once_with(document.pk)
        self.assertEqual(document.ingestion_state, KnowledgeDocument.IngestionState.PENDING)

    def test_saving_an_approved_document_queues_a_fresh_read(self):
        document = self.make_readable()
        self.client.force_login(self.admin, backend=BACKEND)
        with patch("apps.assistant.knowledge.ingest.enqueue_ingestion") as queued:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("assistant:knowledge_document_edit", args=[document.pk]),
                    self.edit_payload(document, title="Fee policy 2027"),
                )
        self.assertEqual(response.status_code, 302)
        queued.assert_called_once()

    def test_archiving_removes_passages_at_once(self):
        document = self.make_readable()
        ingest.ingest_document(document.pk)
        self.assertGreater(document.chunks.count(), 0)
        knowledge_services.set_status(
            document, KnowledgeStatus.ARCHIVED, actor=self.admin, request=None
        )
        document.refresh_from_db()
        self.assertEqual(document.chunks.count(), 0)
        self.assertEqual(document.ingestion_state, KnowledgeDocument.IngestionState.SKIPPED)
        self.assertEqual(document.chunk_count, 0)

    def test_a_draft_on_a_draft_shelf_is_not_queued(self):
        collection = self.make_collection("Draft shelf", status=KnowledgeStatus.DRAFT)
        document = self.make_readable(collection=collection, status=KnowledgeStatus.DRAFT)
        with patch("apps.assistant.knowledge.ingest.enqueue_ingestion") as queued:
            knowledge_services.set_status(
                document, KnowledgeStatus.APPROVED, actor=self.admin, request=None
            )
        queued.assert_not_called()
        document.refresh_from_db()
        self.assertEqual(document.ingestion_state, KnowledgeDocument.IngestionState.SKIPPED)

    def test_unapproving_a_shelf_removes_its_passages(self):
        collection = self.make_collection("Shelf")
        document = self.make_readable(collection=collection)
        ingest.ingest_document(document.pk)
        collection.status = KnowledgeStatus.DRAFT
        collection.save(update_fields=["status", "updated_at"])
        knowledge_services.sync_collection_after_save(collection, previous_ready=True)
        document.refresh_from_db()
        self.assertEqual(document.chunks.count(), 0)
        self.assertEqual(document.ingestion_state, KnowledgeDocument.IngestionState.SKIPPED)

    def test_approving_a_shelf_queues_its_approved_documents(self):
        collection = self.make_collection("Shelf", status=KnowledgeStatus.DRAFT)
        document = self.make_readable(collection=collection)
        collection.status = KnowledgeStatus.APPROVED
        collection.save(update_fields=["status", "updated_at"])
        with patch("apps.assistant.knowledge.ingest.enqueue_ingestion") as queued:
            with self.captureOnCommitCallbacks(execute=True):
                knowledge_services.sync_collection_after_save(collection, previous_ready=False)
        queued.assert_called_once_with(document.pk)

    def test_a_model_change_marks_ready_documents_as_stale(self):
        document = self.make_readable()
        ingest.ingest_document(document.pk)
        ingest.mark_stale_for_models({"a-different-model"})
        document.refresh_from_db()
        self.assertTrue(document.is_stale)

        ingest.ingest_document(document.pk)
        document.refresh_from_db()
        self.assertFalse(document.is_stale)

    def test_changing_the_embedding_model_in_the_settings_marks_documents_stale(self):
        document = self.make_readable()
        ingest.ingest_document(document.pk)
        provider = ProviderConfig.objects.get(pk=self.provider.pk)
        cleaned = {field: getattr(provider, field) for field in assistant_services.PROVIDER_FIELDS}
        cleaned["embedding_model_name"] = "fake-embed-v2"
        cleaned["api_key"] = ""
        provider.embedding_model_name = "fake-embed-v2"
        assistant_services.update_provider(
            instance=provider, cleaned_data=cleaned, actor=self.admin, request=None
        )
        document.refresh_from_db()
        self.assertTrue(document.is_stale)


# ── The worker ───────────────────────────────────────────────────────────────


class IngestionTaskTests(IngestionTestCase):
    def test_a_temporary_provider_failure_records_a_reason_and_queues_one_retry(self):
        failure = ingest.IngestionFailed("The provider is unavailable.", retryable=True)
        with patch("apps.assistant.knowledge.ingest.ingest_document", side_effect=failure):
            with patch("django_q.tasks.async_task") as queued:
                ingest_knowledge_document_task(42, 1)
        self.assertEqual(AssistantJobFailure.objects.count(), 1)
        queued.assert_called_once_with(ingest.TASK_PATH, 42, 2)

    def test_a_plain_failure_is_recorded_and_not_retried(self):
        failure = ingest.IngestionFailed("This document has no readable text.")
        with patch("apps.assistant.knowledge.ingest.ingest_document", side_effect=failure):
            with patch("django_q.tasks.async_task") as queued:
                ingest_knowledge_document_task(43, 1)
        self.assertEqual(AssistantJobFailure.objects.count(), 1)
        queued.assert_not_called()

    def test_the_third_attempt_is_not_retried(self):
        failure = ingest.IngestionFailed("Timeout.", retryable=True)
        with patch("apps.assistant.knowledge.ingest.ingest_document", side_effect=failure):
            with patch("django_q.tasks.async_task") as queued:
                ingest_knowledge_document_task(44, ingest.MAX_ATTEMPTS)
        queued.assert_not_called()

    def test_a_deleted_document_is_left_alone(self):
        ingest_knowledge_document_task(999999, 1)
        self.assertEqual(AssistantJobFailure.objects.count(), 0)

    def test_a_successful_read_records_no_job_failure(self):
        document = self.make_readable()
        ingest_knowledge_document_task(document.pk, 1)
        document.refresh_from_db()
        self.assertEqual(document.ingestion_state, KnowledgeDocument.IngestionState.READY)
        self.assertEqual(AssistantJobFailure.objects.count(), 0)


# ── Screens and command ──────────────────────────────────────────────────────


class ReindexScreenTests(IngestionTestCase):
    def test_an_admin_can_queue_a_fresh_read(self):
        document = self.make_readable()
        self.client.force_login(self.admin, backend=BACKEND)
        with patch("apps.assistant.knowledge.ingest.enqueue_ingestion") as queued:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("assistant:knowledge_document_reindex", args=[document.pk])
                )
        self.assertRedirects(
            response, reverse("assistant:knowledge_document", args=[document.pk])
        )
        document.refresh_from_db()
        self.assertEqual(document.ingestion_state, KnowledgeDocument.IngestionState.PENDING)
        queued.assert_called_once_with(document.pk)
        self.assertTrue(
            AuditLog.objects.filter(action="assistant.knowledge_document_reindex").exists()
        )

    def test_a_draft_cannot_be_queued(self):
        document = self.make_readable(status=KnowledgeStatus.DRAFT)
        self.client.force_login(self.admin, backend=BACKEND)
        with patch("apps.assistant.knowledge.ingest.enqueue_ingestion") as queued:
            response = self.client.post(
                reverse("assistant:knowledge_document_reindex", args=[document.pk]),
                follow=True,
            )
        self.assertContains(response, "Approve this document and its collection")
        queued.assert_not_called()

    def test_non_admin_staff_are_refused(self):
        document = self.make_readable()
        self.client.force_login(self.teacher, backend=BACKEND)
        response = self.client.post(
            reverse("assistant:knowledge_document_reindex", args=[document.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_the_detail_screen_shows_the_reading_state_and_recent_reads(self):
        document = self.make_readable()
        ingest.ingest_document(document.pk)
        self.client.force_login(self.admin, backend=BACKEND)
        response = self.client.get(
            reverse("assistant:knowledge_document", args=[document.pk])
        )
        self.assertContains(response, "Ready to answer from")
        self.assertContains(response, "Recent reads")
        self.assertContains(response, "fake-embed")


class ReindexCommandTests(IngestionTestCase):
    def test_now_reads_the_document_synchronously(self):
        document = self.make_readable()
        output = StringIO()
        call_command("reindex_knowledge", str(document.pk), "--now", stdout=output)
        document.refresh_from_db()
        self.assertEqual(document.ingestion_state, KnowledgeDocument.IngestionState.READY)
        self.assertIn("passages", output.getvalue())

    def test_all_queues_every_approved_document(self):
        document = self.make_readable()
        with patch("apps.assistant.knowledge.ingest.enqueue_ingestion") as queued:
            call_command("reindex_knowledge", "--all", stdout=StringIO())
        queued.assert_called_once_with(document.pk)

    def test_a_draft_is_skipped_in_the_command(self):
        collection = self.make_collection("Draft shelf", status=KnowledgeStatus.DRAFT)
        document = self.make_readable(collection=collection)
        errors = StringIO()
        call_command("reindex_knowledge", str(document.pk), stderr=errors)
        self.assertIn("only approved documents", errors.getvalue().lower())

    def test_no_documents_is_reported_plainly(self):
        output = StringIO()
        call_command("reindex_knowledge", stdout=output)
        self.assertIn("No approved documents need reading", output.getvalue())
