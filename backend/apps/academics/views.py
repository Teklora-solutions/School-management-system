"""
apps/academics/views.py
-----------------------
Admin CRUD for academic structure. Thin views → services / selectors.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import DetailView, ListView

from apps.core.mixins import AdminRequiredMixin, SchoolReadMixin

from . import calendar, selectors, services
from .forms import (
    AcademicYearCreateForm,
    ClassSubjectForm,
    SchoolClassForm,
    StreamForm,
    SubjectForm,
    TermDatesForm,
    TermForm,
)
from .models import AcademicYear, ClassSubject, Subject, Term


def _safe_next(request) -> str:
    """A ?next= / form "next" value, but only if it points back at this site."""
    raw = request.POST.get("next") or request.GET.get("next") or ""
    if not raw:
        return ""
    if url_has_allowed_host_and_scheme(
        url=raw,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return raw
    return ""


def _schedule_payload() -> dict[str, dict]:
    """The derived plan behind each dropdown option, for the form's preview."""
    return {
        str(year): {
            "label": calendar.year_name(year),
            "terms": [
                {
                    "name": Term.TermName(slot).label,
                    "start": opening.isoformat(),
                    "end": closing.isoformat(),
                }
                for slot, opening, closing in calendar.term_windows(year)
            ],
        }
        for year in calendar.selectable_start_years()
    }


class YearListView(SchoolReadMixin, ListView):
    template_name = "features/academics/year_list.html"
    context_object_name = "years"

    def get_queryset(self):
        return selectors.academic_year_list()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["current_year"] = selectors.get_current_year()
        ctx["current_term"] = selectors.get_current_term()
        return ctx


class YearCreateView(AdminRequiredMixin, View):
    template_name = "features/academics/year_form.html"

    def _context(self, form):
        choices = form.fields["start_year"].choices
        schedules = _schedule_payload()
        preview_year = choices[0][0] if choices else None
        return {
            "form": form,
            "schedule": schedules,
            "preview_year": preview_year,
            "preview": schedules.get(preview_year, {}),
        }

    def get(self, request):
        return render(request, self.template_name, self._context(AcademicYearCreateForm()))

    def post(self, request):
        form = AcademicYearCreateForm(request.POST)
        if form.is_valid():
            start_year = form.cleaned_data["start_year"]
            try:
                year = services.create_academic_year_with_terms(
                    start_year=start_year,
                    is_current=form.cleaned_data["is_current"],
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"Academic year {year.name} created with its three terms.",
                )
                return redirect("academics:year_detail", pk=year.pk)
        return render(request, self.template_name, self._context(form))


class YearDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        year = get_object_or_404(AcademicYear, pk=pk)
        label = year.name
        try:
            services.delete_academic_year(
                year=year, actor=request.user, request=request
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Academic year {label} deleted.")
        return redirect("academics:year_list")


class YearDetailView(AdminRequiredMixin, DetailView):
    template_name = "features/academics/year_detail.html"
    context_object_name = "academic_year"

    def get_object(self, queryset=None):
        return selectors.year_with_structure(self.kwargs["pk"])


class YearSetCurrentView(AdminRequiredMixin, View):
    def post(self, request, pk):
        year = get_object_or_404(AcademicYear, pk=pk)
        services.set_current_year(year=year, actor=request.user, request=request)
        messages.success(request, f"{year.name} is now the current academic year.")
        return redirect("academics:year_detail", pk=pk)


class TermCreateView(AdminRequiredMixin, View):
    """Add a term to a year that is missing one. New years arrive with three."""

    template_name = "features/academics/term_form.html"

    def get(self, request):
        initial = {}
        year = None
        year_id = request.GET.get("year")
        if year_id:
            initial["academic_year"] = year_id
            year = AcademicYear.objects.filter(pk=year_id).first()
        form = TermForm(initial=initial, year=year)
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = TermForm(request.POST)
        if form.is_valid():
            try:
                term = services.create_term(
                    academic_year=form.cleaned_data["academic_year"],
                    name=form.cleaned_data["name"],
                    start_date=form.cleaned_data["start_date"],
                    end_date=form.cleaned_data["end_date"],
                    is_current=form.cleaned_data.get("is_current", False),
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Term {term} created.")
                return redirect("academics:year_detail", pk=term.academic_year_id)
        return render(request, self.template_name, {"form": form})


class TermUpdateView(AdminRequiredMixin, View):
    """Correct the dates a term was created with."""

    def post(self, request, pk):
        term = get_object_or_404(Term, pk=pk)
        year_pk = term.academic_year_id
        form = TermDatesForm(request.POST, instance=term)
        if form.is_valid():
            services.update_term_dates(
                term=term,
                start_date=form.cleaned_data["start_date"],
                end_date=form.cleaned_data["end_date"],
                actor=request.user,
                request=request,
            )
            messages.success(request, f"{term.get_name_display()} dates updated.")
        else:
            messages.error(
                request,
                "; ".join(
                    error for errors in form.errors.values() for error in errors
                ),
            )
        return redirect("academics:year_detail", pk=year_pk)


class TermSetCurrentView(AdminRequiredMixin, View):
    def post(self, request, pk):
        term = get_object_or_404(Term, pk=pk)
        services.set_current_term(term=term, actor=request.user, request=request)
        messages.success(request, f"{term} is now the current term.")
        return redirect("academics:year_detail", pk=term.academic_year_id)


class ClassListView(SchoolReadMixin, ListView):
    template_name = "features/academics/class_list.html"
    context_object_name = "classes"

    def get_queryset(self):
        year_id = self.request.GET.get("year") or None
        if year_id:
            try:
                year_id = int(year_id)
            except (TypeError, ValueError):
                year_id = None
        self.year_id = year_id
        return selectors.class_list(year_id=year_id)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["years"] = selectors.academic_year_list()
        ctx["selected_year_id"] = getattr(self, "year_id", None)
        return ctx


class ClassCreateView(AdminRequiredMixin, View):
    template_name = "features/academics/class_form.html"

    def get(self, request):
        initial = {}
        year_id = request.GET.get("year")
        if year_id:
            initial["academic_year"] = year_id
        return render(
            request, self.template_name, {"form": SchoolClassForm(initial=initial)}
        )

    def post(self, request):
        form = SchoolClassForm(request.POST)
        if form.is_valid():
            try:
                obj = services.create_class(
                    academic_year=form.cleaned_data["academic_year"],
                    name=form.cleaned_data["name"],
                    level_order=form.cleaned_data["level_order"],
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Class {obj.name} created.")
                return redirect("academics:class_detail", pk=obj.pk)
        return render(request, self.template_name, {"form": form})


class ClassDetailView(AdminRequiredMixin, DetailView):
    template_name = "features/academics/class_detail.html"
    context_object_name = "school_class"

    def get_object(self, queryset=None):
        return selectors.get_class(self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["class_subjects"] = selectors.class_subject_list(class_id=self.object.pk)
        return ctx


class StreamCreateView(AdminRequiredMixin, View):
    template_name = "features/academics/stream_form.html"

    def get(self, request):
        initial = {}
        class_id = request.GET.get("class")
        if class_id:
            initial["school_class"] = class_id
        return render(
            request, self.template_name, {"form": StreamForm(initial=initial)}
        )

    def post(self, request):
        form = StreamForm(request.POST)
        if form.is_valid():
            try:
                stream = services.create_stream(
                    school_class=form.cleaned_data["school_class"],
                    name=form.cleaned_data["name"],
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Stream {stream.name} created.")
                return redirect("academics:class_detail", pk=stream.school_class_id)
        return render(request, self.template_name, {"form": form})


class SubjectListView(SchoolReadMixin, ListView):
    template_name = "features/academics/subject_list.html"
    context_object_name = "subjects"

    def get_queryset(self):
        return selectors.subject_list(active_only=False)


class SubjectCreateView(AdminRequiredMixin, View):
    template_name = "features/academics/subject_form.html"
    success_url = reverse_lazy("academics:subject_list")

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": SubjectForm(), "heading": "Add Subject", "save_label": "Save Subject"},
        )

    def post(self, request):
        form = SubjectForm(request.POST)
        if form.is_valid():
            try:
                subject = services.create_subject(
                    name=form.cleaned_data["name"],
                    code=form.cleaned_data["code"],
                    actor=request.user,
                    request=request,
                )
                if not form.cleaned_data.get("is_active", True):
                    subject.is_active = False
                    subject.save(update_fields=["is_active", "updated_at"])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Subject {subject.code} created.")
                return redirect(self.success_url)
        return render(
            request,
            self.template_name,
            {"form": form, "heading": "Add Subject", "save_label": "Save Subject"},
        )


class SubjectUpdateView(AdminRequiredMixin, View):
    """Correct a subject's name, code or active flag."""

    template_name = "features/academics/subject_form.html"

    def _subject(self, pk):
        return get_object_or_404(Subject, pk=pk)

    def _context(self, subject, form):
        return {
            "form": form,
            "subject": subject,
            "heading": f"Edit {subject.code}",
            "save_label": "Save Changes",
            "back_url": reverse("academics:subject_update", args=[subject.pk]),
        }

    def get(self, request, pk):
        subject = self._subject(pk)
        return render(
            request, self.template_name, self._context(subject, SubjectForm(instance=subject))
        )

    def post(self, request, pk):
        subject = self._subject(pk)
        form = SubjectForm(request.POST, instance=subject)
        if form.is_valid():
            try:
                services.update_subject(
                    subject=subject,
                    name=form.cleaned_data["name"],
                    code=form.cleaned_data["code"],
                    is_active=form.cleaned_data["is_active"],
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Subject {subject.code} updated.")
                return redirect("academics:subject_list")
        return render(request, self.template_name, self._context(subject, form))


class SubjectToggleActiveView(AdminRequiredMixin, View):
    """Hide a subject from new pickers without touching its history."""

    def post(self, request, pk):
        subject = get_object_or_404(Subject, pk=pk)
        services.set_subject_active(
            subject=subject,
            is_active=not subject.is_active,
            actor=request.user,
            request=request,
        )
        state = "activated" if subject.is_active else "deactivated"
        messages.success(request, f"Subject {subject.code} {state}.")
        return redirect("academics:subject_list")


class SubjectDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        subject = get_object_or_404(Subject, pk=pk)
        label = subject.code
        try:
            services.delete_subject(subject=subject, actor=request.user, request=request)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("academics:subject_list")
        messages.success(request, f"Subject {label} deleted.")
        return redirect("academics:subject_list")


class ClassSubjectListView(SchoolReadMixin, ListView):
    template_name = "features/academics/class_subject_list.html"
    context_object_name = "mappings"

    def get_queryset(self):
        class_id = self.request.GET.get("class") or None
        year_id = self.request.GET.get("year") or None
        if class_id:
            try:
                class_id = int(class_id)
            except (TypeError, ValueError):
                class_id = None
        if year_id:
            try:
                year_id = int(year_id)
            except (TypeError, ValueError):
                year_id = None
        self.class_id = class_id
        self.year_id = year_id
        return selectors.class_subject_list(class_id=class_id, year_id=year_id)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["years"] = selectors.academic_year_list()
        ctx["classes"] = selectors.class_list()
        ctx["selected_year_id"] = getattr(self, "year_id", None)
        ctx["selected_class_id"] = getattr(self, "class_id", None)
        return ctx


class ClassSubjectCreateView(AdminRequiredMixin, View):
    template_name = "features/academics/class_subject_form.html"

    def get(self, request):
        initial = {}
        class_id = request.GET.get("class")
        if class_id:
            initial["school_class"] = class_id
        return render(
            request, self.template_name, {"form": ClassSubjectForm(initial=initial)}
        )

    def post(self, request):
        form = ClassSubjectForm(request.POST)
        if form.is_valid():
            try:
                mapping = services.map_class_subject(
                    school_class=form.cleaned_data["school_class"],
                    subject=form.cleaned_data["subject"],
                    stream=form.cleaned_data.get("stream"),
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Mapped {mapping}.")
                return redirect("academics:class_subject_list")
        return render(request, self.template_name, {"form": form})


class ClassSubjectUpdateView(AdminRequiredMixin, View):
    """Re-point a mapping at a different class, subject or stream."""

    template_name = "features/academics/class_subject_form.html"

    def _context(self, mapping, form, request):
        return {
            "form": form,
            "mapping": mapping,
            "heading": f"Edit Mapping — {mapping}",
            "save_label": "Save Changes",
            "back_url": _safe_next(request),
        }

    def _back(self, request, mapping):
        next_url = _safe_next(request)
        if next_url:
            return redirect(next_url)
        return redirect("academics:class_subject_list")

    def get(self, request, pk):
        mapping = get_object_or_404(ClassSubject, pk=pk)
        form = ClassSubjectForm(instance=mapping)
        return render(
            request, self.template_name, self._context(mapping, form, request)
        )

    def post(self, request, pk):
        mapping = get_object_or_404(ClassSubject, pk=pk)
        form = ClassSubjectForm(request.POST, instance=mapping)
        if form.is_valid():
            try:
                services.update_class_subject(
                    mapping=mapping,
                    school_class=form.cleaned_data["school_class"],
                    subject=form.cleaned_data["subject"],
                    stream=form.cleaned_data.get("stream"),
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, f"Mapping updated: {mapping}.")
            return self._back(request, mapping)
        return render(
            request, self.template_name, self._context(mapping, form, request)
        )


class ClassSubjectRemoveView(AdminRequiredMixin, View):
    def post(self, request, pk):
        mapping = get_object_or_404(ClassSubject, pk=pk)
        class_id = mapping.school_class_id
        services.remove_class_subject(
            mapping=mapping, actor=request.user, request=request
        )
        messages.success(request, "Class–subject mapping removed.")
        next_url = _safe_next(request)
        if next_url:
            return redirect(next_url)
        return redirect("academics:class_detail", pk=class_id)
