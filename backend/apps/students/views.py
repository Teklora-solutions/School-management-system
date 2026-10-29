"""
apps/students/views.py
----------------------
Student list, detail, add, bulk import, PDF admission form, and promotion.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from apps.academics.models import AcademicYear, Term
from apps.core.mixins import AdminRequiredMixin, SchoolReadMixin

from . import selectors, services
from .forms import (
    BulkImportCommitForm,
    BulkImportForm,
    PromotionCommitForm,
    PromotionPreviewForm,
    StudentCreateForm,
)
from .models import BulkImportJob, Student


class StudentListView(SchoolReadMixin, ListView):
    template_name = "features/students/student_list.html"
    context_object_name = "students"
    paginate_by = 30

    def get_queryset(self):
        search = self.request.GET.get("search", "")
        class_id = self.request.GET.get("class") or None
        status = self.request.GET.get("status", "")
        self.search = search
        self.class_id = int(class_id) if class_id else None
        self.status = status
        return selectors.student_list(
            search=search, class_id=self.class_id, status=status
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.academics.selectors import class_list

        ctx["classes"] = class_list()
        ctx["search"] = getattr(self, "search", "")
        ctx["selected_class_id"] = getattr(self, "class_id", None)
        ctx["selected_status"] = getattr(self, "status", "")
        ctx["status_choices"] = Student.Status.choices
        return ctx


class StudentDetailView(AdminRequiredMixin, DetailView):
    template_name = "features/students/student_detail.html"
    context_object_name = "student"

    def get_object(self, queryset=None):
        return selectors.get_student(self.kwargs["pk"])


class StudentCreateView(AdminRequiredMixin, View):
    template_name = "features/students/student_form.html"

    def _ctx(self, form):
        return {"form": form}

    def get(self, request):
        return render(request, self.template_name, self._ctx(StudentCreateForm()))

    def post(self, request):
        form = StudentCreateForm(request.POST)
        if form.is_valid():
            try:
                student = services.create_student_with_guardian(
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    email=form.cleaned_data["email"],
                    admission_number=form.cleaned_data["admission_number"],
                    date_of_birth=form.cleaned_data.get("date_of_birth"),
                    gender=form.cleaned_data.get("gender", ""),
                    current_class=form.cleaned_data.get("current_class"),
                    current_stream=form.cleaned_data.get("current_stream"),
                    address=form.cleaned_data.get("address", ""),
                    guardian_first_name=form.cleaned_data["guardian_first_name"],
                    guardian_last_name=form.cleaned_data["guardian_last_name"],
                    guardian_email=form.cleaned_data["guardian_email"],
                    guardian_phone=form.cleaned_data.get("guardian_phone", ""),
                    guardian_relationship=form.cleaned_data.get(
                        "relationship", "guardian"
                    ),
                    password=form.cleaned_data.get("password") or None,
                    must_change_password=form.cleaned_data["must_change_password"],
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                if hasattr(exc, "message_dict"):
                    for field, errs in exc.message_dict.items():
                        for err in errs:
                            form.add_error(field if field in form.fields else None, err)
                else:
                    form.add_error(None, exc)
            else:
                initial_password = (
                    form.cleaned_data.get("password") or student.admission_number
                )
                messages.success(
                    request,
                    f"Student {student.user.full_name} ({student.admission_number}) created. "
                    f"Login: username = '{student.user.username}', "
                    f"password = '{initial_password}'"
                    + (
                        " (student must change on first login)."
                        if student.user.must_change_password
                        else "."
                    ),
                )
                return redirect("students:detail", pk=student.pk)
        return render(request, self.template_name, self._ctx(form))


class StudentPDFView(AdminRequiredMixin, View):
    """Printable PDF admission form (pre-filled)."""

    def get(self, request, pk):
        student = selectors.get_student(pk)
        return _render_admission_pdf(student)


class StudentBlankPDFView(AdminRequiredMixin, View):
    """Blank printable admission form."""

    def get(self, request):
        return _render_admission_pdf(None)


def _render_admission_pdf(student):
    """
    Admission form as PDF. When the PDF engine cannot run, ``pdf_response``
    returns an honest 503 rather than a page of HTML dressed up as a document.
    """
    from django.conf import settings
    from django.template.loader import render_to_string

    from apps.core.pdf import pdf_response

    html_str = render_to_string(
        "features/students/admission_form_pdf.html",
        {
            "student": student,
            "SCHOOL_NAME": settings.SCHOOL_NAME,
            "SCHOOL_MOTTO": getattr(settings, "SCHOOL_MOTTO", ""),
            "SCHOOL_PHONE": getattr(settings, "SCHOOL_PHONE", ""),
            "SCHOOL_EMAIL": getattr(settings, "SCHOOL_EMAIL", ""),
        },
    )
    fname = f"admission_{student.admission_number}" if student else "admission_blank"
    return pdf_response(html=html_str, filename=f"{fname}.pdf", inline=False)


# ── Bulk Import ────────────────────────────────────────────────────────────


class BulkImportView(AdminRequiredMixin, View):
    template_name = "features/students/bulk_import.html"

    def get(self, request):
        jobs = BulkImportJob.objects.filter(uploaded_by=request.user).order_by(
            "-created_at"
        )[:10]
        return render(
            request,
            self.template_name,
            {
                "form": BulkImportForm(),
                "jobs": jobs,
            },
        )

    def post(self, request):
        form = BulkImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                job = services.start_bulk_import(
                    uploaded_by=request.user,
                    uploaded_file=request.FILES["file"],
                    mode=form.cleaned_data["mode"],
                    target_class=form.cleaned_data["target_class"],
                )
                messages.info(
                    request,
                    f"Upload started (Job #{job.pk}). Validation is running in the background.",
                )
                return redirect("students:bulk_import_preview", job_pk=job.pk)
            except Exception as exc:  # noqa: BLE001
                form.add_error(None, str(exc))
        jobs = BulkImportJob.objects.filter(uploaded_by=request.user).order_by(
            "-created_at"
        )[:10]
        return render(request, self.template_name, {"form": form, "jobs": jobs})


class BulkImportPreviewView(AdminRequiredMixin, View):
    template_name = "features/students/bulk_import_preview.html"

    def get(self, request, job_pk):
        job = get_object_or_404(BulkImportJob, pk=job_pk)
        return render(
            request,
            self.template_name,
            {
                "job": job,
                "commit_form": BulkImportCommitForm(initial={"job_id": job.pk}),
                **selectors.bulk_import_rows(job),
            },
        )

    def post(self, request, job_pk):
        job = get_object_or_404(BulkImportJob, pk=job_pk)
        form = BulkImportCommitForm(request.POST)
        if form.is_valid():
            try:
                job = services.commit_bulk_import(
                    job=job,
                    actor=request.user,
                    request=request,
                )
                summary = job.summary or {}
                messages.success(
                    request,
                    f"Import committed: {summary.get('created', 0)} students added, "
                    f"{summary.get('skipped_existing', 0)} already existed (skipped).",
                )
                return redirect("students:list")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        return render(
            request,
            self.template_name,
            {"job": job, "commit_form": form, **selectors.bulk_import_rows(job)},
        )


class BulkImportTemplateView(AdminRequiredMixin, View):
    """Download the Excel import template."""

    def get(self, request):
        return services.generate_import_template_response()


class BulkImportErrorReportView(AdminRequiredMixin, View):
    """Download error report for a job."""

    def get(self, request, job_pk):
        job = get_object_or_404(BulkImportJob, pk=job_pk)
        return services.generate_error_report_response(job, actor=request.user)


# ── Promotion ──────────────────────────────────────────────────────────────


class PromotionView(AdminRequiredMixin, View):
    template_name = "features/students/promote.html"

    def get(self, request):
        form = PromotionPreviewForm()
        return render(request, self.template_name, {"form": form, "preview": None})

    def post(self, request):
        action = request.POST.get("action", "preview")

        if action == "preview":
            form = PromotionPreviewForm(request.POST)
            if form.is_valid():
                repeat_raw = form.cleaned_data.get("repeat_students", "")
                repeat_set = {x.strip() for x in repeat_raw.split(",") if x.strip()}
                try:
                    preview = services.preview_promotion(
                        from_year=form.cleaned_data["from_year"],
                        to_year=form.cleaned_data["to_year"],
                        term=form.cleaned_data["term"],
                        repeat_admission_numbers=repeat_set,
                    )
                    commit_form = PromotionCommitForm(
                        initial={
                            "from_year": form.cleaned_data["from_year"].pk,
                            "to_year": form.cleaned_data["to_year"].pk,
                            "term": form.cleaned_data["term"].pk,
                            "repeat_students": repeat_raw,
                        }
                    )
                    return render(
                        request,
                        self.template_name,
                        {
                            "form": form,
                            "preview": preview,
                            "commit_form": commit_form,
                        },
                    )
                except ValidationError as exc:
                    form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "preview": None})

        elif action == "commit":
            commit_form = PromotionCommitForm(request.POST)
            if commit_form.is_valid():
                cd = commit_form.cleaned_data
                repeat_raw = cd.get("repeat_students", "")
                repeat_set = {x.strip() for x in repeat_raw.split(",") if x.strip()}
                try:
                    from_year = AcademicYear.objects.get(pk=cd["from_year"])
                    to_year = AcademicYear.objects.get(pk=cd["to_year"])
                    term = Term.objects.get(pk=cd["term"])
                    batch = services.commit_promotion(
                        from_year=from_year,
                        to_year=to_year,
                        term=term,
                        repeat_admission_numbers=repeat_set,
                        override_unpublished=cd.get("override_unpublished", False),
                        override_reason=cd.get("override_reason", ""),
                        actor=request.user,
                        request=request,
                    )
                    if batch.is_committed:
                        messages.success(
                            request, f"Promotion committed. Batch #{batch.pk}."
                        )
                    else:
                        messages.info(
                            request, "Promotion already committed (idempotent)."
                        )
                    return redirect("students:promote")
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    form = PromotionPreviewForm(
                        initial={
                            "from_year": cd["from_year"],
                            "to_year": cd["to_year"],
                            "term": cd["term"],
                        }
                    )
                    return render(
                        request,
                        self.template_name,
                        {
                            "form": form,
                            "preview": None,
                            "commit_form": commit_form,
                        },
                    )

        return redirect("students:promote")


class PromotionBatchListView(AdminRequiredMixin, ListView):
    template_name = "features/students/promotion_history.html"
    context_object_name = "batches"
    paginate_by = 20

    def get_queryset(self):
        return selectors.promotion_batch_list()
