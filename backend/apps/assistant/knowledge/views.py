"""
Admin screens for the knowledge base.

K1 shows what exists and lets an admin add, edit, approve, archive and download
documents. Reading the documents, chunking them and answering from them are
K2/K3; the screens say so where it matters instead of implying more.
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

from ..models import (
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeStatus,
)
from . import forms as knowledge_forms
from . import services

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
        return redirect("assistant:knowledge_collections")


class CollectionUpdateView(AdminRequiredMixin, UpdateView):
    template_name = "features/assistant/knowledge/collection_form.html"
    form_class = knowledge_forms.KnowledgeCollectionForm

    def get_queryset(self):
        return KnowledgeCollection.objects.all()

    def form_valid(self, form):
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
                "status_form": knowledge_forms.KnowledgeStatusForm(
                    initial={"status": document.status}
                ),
                "downloadable": document.has_file,
                "collection_approved": document.collection.is_approved,
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
        services.set_status(
            document, form.cleaned_data["status"], actor=request.user, request=request
        )
        messages.success(
            request, f"“{document.title}” is now {document.get_status_display().lower()}."
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
