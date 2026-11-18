"""
Queue (or, with ``--now``, perform) a fresh read of approved documents.

Examples
  python manage.py reindex_knowledge --stale
  python manage.py reindex_knowledge 12 15
  python manage.py reindex_knowledge --all --now

Without arguments only documents already flagged as needing a re-index are
chosen. Drafts and archived documents are never read. Queueing needs the
background worker (``python manage.py qcluster``) to be running.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.assistant.knowledge import ingest
from apps.assistant.models import KnowledgeDocument, KnowledgeStatus


class Command(BaseCommand):
    help = "Queue a fresh read of approved knowledge documents."

    def add_arguments(self, parser):
        parser.add_argument(
            "document_ids",
            nargs="*",
            type=int,
            help="Read these document ids only.",
        )
        parser.add_argument(
            "--stale",
            action="store_true",
            help="Only documents flagged as needing a re-index (the default).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all_documents",
            help="Every approved document on an approved shelf.",
        )
        parser.add_argument(
            "--now",
            action="store_true",
            help="Read immediately instead of queueing. Needs a provider and budget.",
        )

    def handle(self, *args, **options):
        documents = KnowledgeDocument.objects.select_related("collection")
        if options["document_ids"]:
            documents = documents.filter(pk__in=options["document_ids"])
        elif options["all_documents"]:
            documents = documents.filter(
                status=KnowledgeStatus.APPROVED,
                collection__status=KnowledgeStatus.APPROVED,
            )
        else:
            documents = documents.filter(
                is_stale=True,
                status=KnowledgeStatus.APPROVED,
                collection__status=KnowledgeStatus.APPROVED,
            )

        targets = []
        for document in documents.order_by("pk"):
            if not (document.is_approved and document.collection.is_approved):
                self.stderr.write(
                    f"Skipped {document.pk} “{document.title}”: only approved documents "
                    "on approved shelves are read."
                )
                continue
            targets.append(document)

        if not targets:
            self.stdout.write("No approved documents need reading.")
            return

        if options["now"]:
            for document in targets:
                try:
                    run = ingest.ingest_document(document.pk)
                except ingest.IngestionFailed as exc:
                    self.stderr.write(
                        self.style.ERROR(f"{document.pk}: {exc.safe_message}")
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"{document.pk}: {run.get_status_display().lower()} "
                            f"({run.chunks_written} passages)"
                        )
                    )
            return

        for document in targets:
            ingest.request_ingestion(document, on_commit=False)
        self.stdout.write(
            self.style.SUCCESS(
                f"Queued {len(targets)} document{'' if len(targets) == 1 else 's'}. "
                "The background worker reads them."
            )
        )
