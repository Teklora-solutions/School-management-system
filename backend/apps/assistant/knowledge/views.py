"""
Admin screens for the knowledge base.

K1 shows what exists and lets an admin add, edit, approve, archive and download
documents. K2 reads an approved document in the background and lets an admin
ask for a fresh read. K3 answers from the passages in the answer desk, shows
the gaps the assistant could not answer, and gives each document a personal-data
check and a per-document search.
"""

from __future__ import annotations

from pathlib import Path

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.core.audit_log import log_action
from apps.core.mixins import AdminRequiredMixin

from .. import alerts, selectors
from ..guardrails import build_safe_reply, screen
from ..models import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeGap,
    KnowledgeStatus,
    UsageEvent,
)
from . import answers, forms as knowledge_forms, gaps, personal_data, retrieval, scope, services

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}


# ── Collections ──────────────────────────────────────────────────────────────


class CollectionListView(AdminRequiredMixin, ListView):
    template_name = "features/assistant/knowledge/collections.html"
    context_object_name = "collections"

    def get_queryset(self):
        return KnowledgeCollection.objects.prefetch_related("classes").annotate(
            document_count=Count("documents"),
            approved_count=Count(
                "documents", filter=Q(documents__status=KnowledgeStatus.APPROVED)
            ),
        )


class CollectionCreateView(AdminRequiredMixin, CreateView):
    template_name = "features/assistant/knowledge/collection_form.html"
    form_class = knowledge_forms.KnowledgeCollectionForm

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.owner = self.request.user
        self.object.save()
        form.save_m2m()
        log_action(
            actor=self.request.user,
            action="assistant.knowledge_collection_saved",
            content_type="assistant.KnowledgeCollection",
            object_id=self.object.pk,
            change_summary={
                "name": self.object.name,
                "audience": self.object.audience,
                "created": True,
            },
            request=self.request,
        )
        messages.success(self.request, f"Collection “{self.object.name}” added.")
        services.sync_collection_after_save(self.object, previous_ready=False)
        return redirect("assistant:knowledge_collections")


class CollectionUpdateView(AdminRequiredMixin, UpdateView):
    template_name = "features/assistant/knowledge/collection_form.html"
    form_class = knowledge_forms.KnowledgeCollectionForm

    def get_queryset(self):
        return KnowledgeCollection.objects.all()

    def form_valid(self, form):
        previous = KnowledgeCollection.objects.get(pk=form.instance.pk)
        previous_ready = previous.is_approved and previous.effective_on()
        self.object = form.save(commit=False)
        if not self.object.owner_id:
            self.object.owner = self.request.user
        self.object.save()
        form.save_m2m()
        log_action(
            actor=self.request.user,
            action="assistant.knowledge_collection_saved",
            content_type="assistant.KnowledgeCollection",
            object_id=self.object.pk,
            change_summary={
                "name": self.object.name,
                "audience": self.object.audience,
                "status": self.object.status,
            },
            request=self.request,
        )
        services.sync_collection_after_save(self.object, previous_ready=previous_ready)
        messages.success(self.request, f"Collection “{self.object.name}” saved.")
        return redirect("assistant:knowledge_collections")


# ── Documents ────────────────────────────────────────────────────────────────


class DocumentListView(AdminRequiredMixin, ListView):
    template_name = "features/assistant/knowledge/documents.html"
    context_object_name = "documents"

    def get_queryset(self):
        queryset = (
            KnowledgeDocument.objects.select_related("collection")
            .prefetch_related("classes")
            .order_by("collection__name", "title")
        )
        collection_id = self.request.GET.get("collection", "")
        status = self.request.GET.get("status", "")
        source_type = self.request.GET.get("type", "")
        query = self.request.GET.get("q", "").strip()
        if collection_id.isdigit():
            queryset = queryset.filter(collection_id=int(collection_id))
        if status in dict(KnowledgeStatus.choices):
            queryset = queryset.filter(status=status)
        if source_type in dict(KnowledgeDocument.SourceType.choices):
            queryset = queryset.filter(source_type=source_type)
        if query:
            queryset = queryset.filter(title__icontains=query)
        return queryset

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["collections"] = KnowledgeCollection.objects.order_by("name")
        ctx["statuses"] = KnowledgeStatus.choices
        ctx["source_types"] = KnowledgeDocument.SourceType.choices
        ctx["filters"] = {
            "collection": self.request.GET.get("collection", ""),
            "status": self.request.GET.get("status", ""),
            "type": self.request.GET.get("type", ""),
            "q": self.request.GET.get("q", ""),
        }
        ctx["documents_total"] = KnowledgeDocument.objects.count()
        ctx["approved_total"] = KnowledgeDocument.objects.filter(
            status=KnowledgeStatus.APPROVED
        ).count()
        return ctx


class DocumentNewView(AdminRequiredMixin, View):
    template_name = "features/assistant/knowledge/document_new.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {
                "collection": request.GET.get("collection", ""),
                "collection_count": KnowledgeCollection.objects.count(),
            },
        )


class DocumentCreateView(AdminRequiredMixin, View):
    mode = "upload"
    template_name = "features/assistant/knowledge/document_form.html"

    def build_form_class(self):
        return {
            "upload": knowledge_forms.KnowledgeUploadForm,
            "url": knowledge_forms.KnowledgeUrlForm,
            "paste": knowledge_forms.KnowledgePasteForm,
            "faq": knowledge_forms.KnowledgeFAQForm,
        }[self.mode]

    def get_form(self, data=None, files=None):
        initial = {}
        collection = self.request.GET.get("collection", "")
        if collection.isdigit():
            initial["collection"] = int(collection)
        return self.build_form_class()(data, files, initial=initial)

    def get_formset(self, data=None):
        if self.mode != "faq":
            return None
        return knowledge_forms.KnowledgeQAPairFormSet(
            data, instance=KnowledgeDocument()
        )

    def get(self, request):
        return self.render_page(request, self.get_form(), self.get_formset())

    def post(self, request):
        form = self.get_form(request.POST, request.FILES)
        formset = self.get_formset(request.POST)
        if form.is_valid() and (formset is None or formset.is_valid()):
            rows = services.qa_rows_from_formset(formset) if formset else []
            if self.mode == "faq" and not rows:
                form.add_error(None, "Add at least one question and a full answer.")
            else:
                try:
                    document = services.create_document(
                        form,
                        source_type=self.source_type(form),
                        actor=request.user,
                        request=request,
                    )
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    if formset is not None:
                        services.save_qa_pairs(document, rows)
                        services.scan_for_personal_data(document)
                    messages.success(request, f"“{document.title}” added.")
                    return redirect("assistant:knowledge_document", pk=document.pk)
        return self.render_page(request, form, formset)

    def source_type(self, form) -> str:
        if self.mode == "url":
            return KnowledgeDocument.SourceType.URL
        if self.mode == "paste":
            return KnowledgeDocument.SourceType.PASTED
        if self.mode == "faq":
            return KnowledgeDocument.SourceType.FAQ
        suffix = Path(form.cleaned_data["file"].name).suffix.lower()
        return {
            ".pdf": KnowledgeDocument.SourceType.PDF,
            ".docx": KnowledgeDocument.SourceType.DOCX,
        }.get(suffix, KnowledgeDocument.SourceType.TEXT)

    def render_page(self, request, form, formset):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "formset": formset,
                "mode": self.mode,
                "multipart": self.mode == "upload",
                "title": {
                    "upload": "Upload a document",
                    "url": "Add a web page",
                    "paste": "Paste text",
                    "faq": "Add questions and answers",
                }[self.mode],
            },
        )


class DocumentUploadView(DocumentCreateView):
    mode = "upload"


class DocumentUrlView(DocumentCreateView):
    mode = "url"


class DocumentPasteView(DocumentCreateView):
    mode = "paste"


class DocumentFAQView(DocumentCreateView):
    mode = "faq"


class DocumentDetailView(AdminRequiredMixin, View):
    template_name = "features/assistant/knowledge/document_detail.html"

    def get(self, request, pk):
        document = get_object_or_404(
            KnowledgeDocument.objects.select_related(
                "collection", "owner", "duplicate_of"
            ).prefetch_related("classes"),
            pk=pk,
        )
        return render(
            request,
            self.template_name,
            {
                "document": document,
                "versions": document.versions.select_related("created_by"),
                "qa_pairs": document.qa_pairs.all(),
                "ingest_runs": document.ingest_runs.all()[:5],
                "status_form": knowledge_forms.KnowledgeStatusForm(
                    initial={"status": document.status}
                ),
                "downloadable": document.has_file,
                "collection_approved": document.collection.is_approved,
                "pii_confirmation_text": personal_data.CONFIRMATION_TEXT,
            },
        )


class DocumentEditView(AdminRequiredMixin, UpdateView):
    model = KnowledgeDocument
    form_class = knowledge_forms.KnowledgeDocumentEditForm
    template_name = "features/assistant/knowledge/document_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({"mode": "edit", "multipart": False, "formset": None, "title": "Edit document"})
        return ctx

    def form_valid(self, form):
        try:
            document = services.update_document_metadata(
                form,
                actor=self.request.user,
                request=self.request,
                change_note=form.cleaned_data.get("change_note", ""),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        messages.success(self.request, f"“{document.title}” saved.")
        return redirect("assistant:knowledge_document", pk=document.pk)


class DocumentStatusView(AdminRequiredMixin, View):
    def post(self, request, pk):
        document = get_object_or_404(KnowledgeDocument, pk=pk)
        form = knowledge_forms.KnowledgeStatusForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Choose a valid status.")
            return redirect("assistant:knowledge_document", pk=document.pk)
        try:
            services.set_status(
                document, form.cleaned_data["status"], actor=request.user, request=request
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("assistant:knowledge_document", pk=document.pk)
        messages.success(
            request, f"“{document.title}” is now {document.get_status_display().lower()}."
        )
        return redirect("assistant:knowledge_document", pk=document.pk)


class DocumentReindexView(AdminRequiredMixin, View):
    """Queue a fresh read of an approved document."""

    def post(self, request, pk):
        from . import ingest

        document = get_object_or_404(
            KnowledgeDocument.objects.select_related("collection"), pk=pk
        )
        if not (document.is_approved and document.collection.is_approved):
            messages.error(
                request,
                "Approve this document and its collection before it can be read.",
            )
            return redirect("assistant:knowledge_document", pk=document.pk)
        if document.ingestion_state == KnowledgeDocument.IngestionState.RUNNING:
            messages.info(request, f"“{document.title}” is already being read.")
            return redirect("assistant:knowledge_document", pk=document.pk)

        ingest.request_ingestion(document)
        log_action(
            actor=request.user,
            action="assistant.knowledge_document_reindex",
            content_type="assistant.KnowledgeDocument",
            object_id=document.pk,
            change_summary={"title": document.title},
            request=request,
        )
        messages.success(
            request,
            f"Reading of “{document.title}” is queued. Passages appear when the "
            "background worker finishes.",
        )
        return redirect("assistant:knowledge_document", pk=document.pk)


class DocumentDownloadView(AdminRequiredMixin, View):
    """The only way a knowledge file is ever served."""

    def get(self, request, pk):
        document = get_object_or_404(KnowledgeDocument, pk=pk)
        if not document.file:
            raise Http404("This document has no file.")
        suffix = Path(document.file.name).suffix.lower()
        filename = f"{slugify(document.title)[:80] or 'knowledge-document'}{suffix}"
        response = FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=filename,
            content_type=CONTENT_TYPES.get(suffix, "application/octet-stream"),
        )
        response["X-Content-Type-Options"] = "nosniff"
        log_action(
            actor=request.user,
            action="assistant.knowledge_document_downloaded",
            content_type="assistant.KnowledgeDocument",
            object_id=document.pk,
            change_summary={"title": document.title},
            request=request,
        )
        return response


# ── Answering (K3) ───────────────────────────────────────────────────────────


class AnswerDeskView(AdminRequiredMixin, View):
    """
    Ask a question as any audience and see exactly what a real reader would
    get. The preview calls the same scope and retrieval code a real request
    uses; there is no admin shortcut.
    """

    template_name = "features/assistant/knowledge/answer_desk.html"

    def get(self, request):
        return self._render(request, knowledge_forms.AnswerDeskForm())

    def post(self, request):
        form = knowledge_forms.AnswerDeskForm(request.POST)
        result = None
        if form.is_valid():
            audience = scope.audience_for_preview(
                form.cleaned_data["audience"], form.class_ids()
            )
            result = answers.answer_question(
                form.cleaned_data["question"],
                audience=audience,
                actor=request.user,
                profile=UsageEvent.Profile.CONSOLE,
                provider_id=getattr(form.cleaned_data.get("provider"), "pk", None),
                use_cache=False,
                record_gaps=True,
                is_test=True,
            )
        return self._render(request, form, result)

    def _render(self, request, form, result=None):
        settings_row = selectors.assistant_settings()
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "result": result,
                "settings_row": settings_row,
                "refusal_draft_en": answers.REFUSAL_EN,
                "refusal_draft_sw": answers.REFUSAL_SW,
                "safeguarding_contact_set": settings_row.has_safeguarding_contact,
                "open_gap_count": gaps.open_gaps().count(),
                "console_daily_limit": settings_row.console_daily_limit,
                "console_monthly_budget_ksh": settings_row.console_monthly_budget_ksh,
            },
        )


class DocumentCheckView(AdminRequiredMixin, View):
    """
    “Try a question against this document”: scores this document's own
    passages. The question embedding is the only provider call; no answer is
    generated.
    """

    template_name = "features/assistant/knowledge/document_check.html"

    def get(self, request, pk):
        document = get_object_or_404(KnowledgeDocument, pk=pk)
        return self._render(request, document, knowledge_forms.DocumentCheckForm())

    def post(self, request, pk):
        document = get_object_or_404(KnowledgeDocument, pk=pk)
        form = knowledge_forms.DocumentCheckForm(request.POST)
        result = None
        screened = None
        error = ""
        if form.is_valid():
            question = form.cleaned_data["question"]
            screening = screen(question, selectors.custom_rules_active())
            if screening.triggered:
                screened = {
                    "rules": screening.rule_ids,
                    "categories": screening.categories,
                    "safe_reply": build_safe_reply(selectors.assistant_settings()),
                    "alert_preview": alerts.would_notify(selectors.assistant_settings()),
                }
            else:
                try:
                    result = retrieval.score_document(
                        document, question, actor=request.user
                    )
                except retrieval.RetrievalUnavailable as exc:
                    error = exc.message
        return self._render(request, document, form, result=result, screened=screened, error=error)

    def _render(self, request, document, form, result=None, screened=None, error=""):
        return render(
            request,
            self.template_name,
            {
                "document": document,
                "form": form,
                "result": result,
                "screened": screened,
                "error": error,
                "checked": result is not None or screened is not None or bool(error),
                "passage_count": document.chunks.count(),
            },
        )


class DocumentConfirmPIIView(AdminRequiredMixin, View):
    """Record the admin's acceptance of sending a document that has personal data."""

    def post(self, request, pk):
        document = get_object_or_404(KnowledgeDocument, pk=pk)
        try:
            services.confirm_personal_data(document, actor=request.user, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request,
                "Personal-data decision recorded. The document can now be read and approved.",
            )
        return redirect("assistant:knowledge_document", pk=document.pk)


class GapListView(AdminRequiredMixin, ListView):
    template_name = "features/assistant/knowledge/gaps.html"
    context_object_name = "gaps"

    def get_queryset(self):
        state = self.request.GET.get("state", "open")
        include_tests = self.request.GET.get("tests") == "1"
        if state == "resolved":
            queryset = gaps.all_gaps(include_tests=include_tests).filter(
                resolved_at__isnull=False
            )
        elif state == "all":
            queryset = gaps.all_gaps(include_tests=include_tests)
        else:
            queryset = gaps.open_gaps(include_tests=include_tests)
        return queryset[:200]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["state"] = self.request.GET.get("state", "open")
        ctx["include_tests"] = self.request.GET.get("tests") == "1"
        ctx["open_count"] = gaps.open_gaps().count()
        ctx["open_test_count"] = gaps.open_gaps(include_tests=True).filter(is_test=True).count()
        ctx["retention_days"] = gaps.GAP_RETENTION_DAYS
        return ctx


class GapResolveView(AdminRequiredMixin, View):
    def post(self, request, pk):
        gap = get_object_or_404(KnowledgeGap, pk=pk)
        gaps.resolve(gap, actor=request.user, note=request.POST.get("note", ""))
        messages.success(request, "Gap marked as answered.")
        return redirect("assistant:knowledge_gaps")


class GapDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        gap = get_object_or_404(KnowledgeGap, pk=pk)
        gaps.erase(gap)
        log_action(
            actor=request.user,
            action="assistant.knowledge_gap_deleted",
            content_type="assistant.KnowledgeGap",
            object_id=pk,
            change_summary={"deleted": True},
            request=request,
        )
        messages.success(request, "Gap deleted.")
        return redirect("assistant:knowledge_gaps")
