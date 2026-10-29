"""
apps/results/views.py
----------------------
Student-facing result views (published sheets only) and the admin
results-governance screens (sheet list / detail / confirm / publish /
reopen, grading configuration).

Security:
  - StudentResultsView and ReportCardPdfView resolve the student via role;
    a student can only see their own results, a parent only sees children
    they are linked to via StudentGuardian. IDs in URLs are validated
    against the authenticated user's own data.
  - All governance screens are AdminRequiredMixin — non-admins get 403.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView, View

from apps.academics.models import AcademicYear
from apps.core.mixins import AdminRequiredMixin, RoleRequiredMixin

from . import selectors, services
from .forms import (
    GradeBandFormSet,
    GradeScaleForm,
    GradingConfigForm,
    PublishClassTermForm,
    ReopenSheetForm,
    ResultSheetFilterForm,
)
from .models import ResultSheet


class StudentOrParentMixin(RoleRequiredMixin):
    allowed_roles = ["student", "parent"]


def _resolve_student_for_results(request, student_id: int | None = None):
    """
    Resolve the student whose results should be displayed.

    - Students: always their own profile; if student_id is provided and
      doesn't match → 403.
    - Parents: student_id must belong to one of their linked children.
    - Admins / staff: any student_id (used by staff result views later).
    """
    from apps.students.models import Student

    if request.user.role == "student":
        if not hasattr(request.user, "student_profile"):
            raise PermissionDenied("No student profile linked to your account.")
        student = request.user.student_profile
        if student_id and student.pk != int(student_id):
            raise PermissionDenied("You may only view your own results.")
        return student

    if request.user.role == "parent":
        if not hasattr(request.user, "parent_profile"):
            raise PermissionDenied("No parent profile linked to your account.")
        child_ids = list(
            request.user.parent_profile.children_links.values_list(
                "student_id", flat=True
            )
        )
        if not child_ids:
            raise PermissionDenied("No children are linked to your account.")

        # Resolve which child to show
        if student_id:
            if int(student_id) not in child_ids:
                raise PermissionDenied(
                    "You may only view results for your own children."
                )
            target_id = int(student_id)
        else:
            selected_id = request.session.get("selected_child_id")
            if selected_id and int(selected_id) in child_ids:
                target_id = int(selected_id)
            else:
                primary = (
                    request.user.parent_profile.children_links.filter(is_primary=True)
                    .values_list("student_id", flat=True)
                    .first()
                )
                target_id = primary or child_ids[0]
        return get_object_or_404(
            Student.objects.select_related("user", "current_class"),
            pk=target_id,
        )

    if (
        request.user.role in ("admin", "director", "teacher", "finance")
        or request.user.is_superuser
    ):
        if student_id:
            return get_object_or_404(
                Student.objects.select_related("user", "current_class"), pk=student_id
            )
        raise PermissionDenied("Student ID required.")

    raise PermissionDenied("Your role cannot access results.")


class StudentResultsView(StudentOrParentMixin, TemplateView):
    template_name = "features/results/student_results.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = _resolve_student_for_results(self.request)

        # Term filter
        term_id_param = self.request.GET.get("term")
        term_id = int(term_id_param) if term_id_param else None

        terms = selectors.student_terms_with_published_results(student_id=student.pk)

        # Default to first (most recent) term if none selected
        if not term_id and terms.exists():
            term_id = terms.first().pk

        entries = selectors.student_published_results(
            student_id=student.pk,
            term_id=term_id,
        )

        # Per-entry class positions
        positions = {}
        if term_id:
            positions = selectors.student_class_positions(
                student_id=student.pk, term_id=term_id
            )

        # Overall summary for the selected term
        summary = None
        if term_id:
            summary = selectors.student_latest_result_summary(student_id=student.pk)
            # If the returned summary is for a different term, recompute without caching
            if summary and summary["term"].pk != term_id:
                summary = None

        selected_term = None
        if term_id:
            selected_term = terms.filter(pk=term_id).first()

        ctx.update(
            {
                "student": student,
                "terms": terms,
                "entries": entries,
                "positions": positions,
                "summary": summary,
                "selected_term": selected_term,
                "selected_term_id": term_id,
            }
        )
        return ctx


class ReportCardPdfView(LoginRequiredMixin, View):
    """
    Generate a printable PDF report card for a student's published results
    in a given term.

    Access: student (own only) | parent (own children only) | staff (any).
    """

    def get(self, request, student_id: int, term_id: int, *args, **kwargs):
        student = _resolve_student_for_results(request, student_id=student_id)

        # Ensure there are published results for this term
        entries = selectors.student_published_results(
            student_id=student.pk, term_id=term_id
        )
        if not entries.exists():
            from django.http import Http404

            raise Http404("No published results for this term.")

        positions = selectors.student_class_positions(
            student_id=student.pk, term_id=term_id
        )

        from apps.academics.models import Term

        term = get_object_or_404(
            Term.objects.select_related("academic_year"), pk=term_id
        )

        # Compute summary data for the PDF header
        from django.db.models import Avg

        avg_result = entries.aggregate(avg=Avg("total"))
        avg = round(float(avg_result["avg"] or 0), 1)

        from django.conf import settings as _settings
        from django.template.loader import render_to_string

        html = render_to_string(
            "features/results/report_card_pdf.html",
            {
                "student": student,
                "term": term,
                "entries": list(entries),
                "positions": positions,
                "average": avg,
                "SCHOOL_NAME": _settings.SCHOOL_NAME,
                "SCHOOL_MOTTO": getattr(_settings, "SCHOOL_MOTTO", ""),
                "request": request,
            },
        )
        from apps.core.pdf import pdf_response

        return pdf_response(
            html=html,
            filename=f"report_card_{student.admission_number}_{term_id}.pdf",
            base_url=request.build_absolute_uri("/"),
        )


# ---------------------------------------------------------------------------
# Results governance (Admin) — Module 1
# ---------------------------------------------------------------------------


class ResultSheetListView(AdminRequiredMixin, TemplateView):
    """Admin: browse result sheets, filter by status / year / term / class."""

    template_name = "features/results/sheet_list.html"

    def get_context_data(self, **kwargs):
        from django.db.models import Count

        ctx = super().get_context_data(**kwargs)
        form = ResultSheetFilterForm(self.request.GET or None)
        status = year_id = term_id = class_id = None
        if form.is_valid():
            status = form.cleaned_data.get("status") or None
            year = form.cleaned_data.get("year")
            term = form.cleaned_data.get("term")
            school_class = form.cleaned_data.get("school_class")
            year_id = year.pk if year else None
            term_id = term.pk if term else None
            class_id = school_class.pk if school_class else None

        ctx["filter_form"] = form
        ctx["sheets"] = selectors.result_sheet_list(
            status=status or "",
            class_id=class_id,
            term_id=term_id,
            year_id=year_id,
        )[:200]
        counts = dict(ResultSheet.objects.values_list("status").annotate(n=Count("id")))
        ctx["status_tabs"] = [
            {
                "value": value,
                "label": label,
                "count": counts.get(value, 0),
                "query": f"?status={value}",
            }
            for value, label in ResultSheet.Status.choices
        ]
        ctx["publish_form"] = PublishClassTermForm()
        return ctx


class ClassBroadsheetView(AdminRequiredMixin, TemplateView):
    """Admin: class broadsheet for one year + class + term + exam."""

    template_name = "features/results/broadsheet.html"

    @staticmethod
    def _get_int(request, key: str) -> int | None:
        try:
            return int(request.GET.get(key))
        except (TypeError, ValueError):
            return None

    def _pick(self, options: list, key: str, request):
        wanted = self._get_int(request, key)
        for option in options:
            if wanted is not None and option.pk == wanted:
                return option
        return options[0] if options else None

    def get_context_data(self, **kwargs):
        from apps.academics.models import SchoolClass, Term

        ctx = super().get_context_data(**kwargs)
        request = self.request

        years = list(AcademicYear.objects.order_by("-start_date"))
        wanted_year = self._get_int(request, "year")
        year = next((y for y in years if y.pk == wanted_year), None)
        if year is None:
            year = next((y for y in years if y.is_current), years[0] if years else None)

        class_options: list = []
        school_class = None
        term_options: list = []
        term = None
        exam_options: list = []
        exam = None
        broadsheet = None

        if year is not None:
            class_options = list(
                SchoolClass.objects.filter(academic_year=year).order_by(
                    "level_order", "name"
                )
            )
            school_class = self._pick(class_options, "class", request)

        if school_class is not None:
            term_options = list(
                Term.objects.filter(academic_year=year).order_by("name")
            )
            wanted_term = self._get_int(request, "term")
            term = next((t for t in term_options if t.pk == wanted_term), None)
            if term is None:
                term = next(
                    (t for t in term_options if t.is_current),
                    term_options[0] if term_options else None,
                )

        if term is not None:
            exam_options = services.ensure_exams_for_term(term)
            exam = self._pick(exam_options, "exam", request)

        if school_class is not None and term is not None and exam is not None:
            broadsheet = selectors.exam_broadsheet(
                school_class=school_class, term=term, exam=exam
            )

        ctx.update(
            {
                "years": years,
                "selected_year": year,
                "class_options": class_options,
                "selected_class": school_class,
                "term_options": term_options,
                "selected_term": term,
                "exam_options": exam_options,
                "selected_exam": exam,
                "broadsheet": broadsheet,
            }
        )
        return ctx


class ResultSheetDetailView(AdminRequiredMixin, TemplateView):
    """Admin: view one sheet and its score entries, with governance actions."""

    template_name = "features/results/sheet_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sheet = get_object_or_404(selectors.result_sheet_list(), pk=kwargs["pk"])
        ctx["sheet"] = sheet
        ctx["entries"] = sheet.entries.select_related("student__user")
        ctx["can_confirm"] = sheet.can_transition_to(ResultSheet.Status.CONFIRMED)
        ctx["can_publish"] = sheet.can_transition_to(ResultSheet.Status.PUBLISHED)
        ctx["can_reopen"] = sheet.status != ResultSheet.Status.DRAFT
        ctx["reopen_form"] = ReopenSheetForm()
        return ctx


class ResultSheetConfirmView(AdminRequiredMixin, View):
    """Admin: CONFIRM a submitted sheet. Locked once confirmed."""

    def post(self, request, pk):
        sheet = get_object_or_404(ResultSheet, pk=pk)
        try:
            services.confirm_sheet(sheet=sheet, actor=request.user, request=request)
            messages.success(request, f"Sheet {sheet} confirmed. It is now locked.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("results:sheet_detail", pk=pk)


class ResultSheetPublishView(AdminRequiredMixin, View):
    """Admin: PUBLISH one confirmed sheet. This is what makes it visible to the student."""

    def post(self, request, pk):
        sheet = get_object_or_404(ResultSheet, pk=pk)
        try:
            services.publish_sheet(sheet=sheet, actor=request.user, request=request)
            messages.success(
                request,
                f"Sheet {sheet} published. The student can now see these marks.",
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("results:sheet_detail", pk=pk)


class ResultSheetReopenView(AdminRequiredMixin, View):
    """Admin: reopen a confirmed/published sheet back to Draft. Reason required + audited."""

    def post(self, request, pk):
        sheet = get_object_or_404(ResultSheet, pk=pk)
        form = ReopenSheetForm(request.POST)
        if form.is_valid():
            try:
                services.reopen_sheet(
                    sheet=sheet,
                    reason=form.cleaned_data["reason"],
                    actor=request.user,
                    request=request,
                )
                messages.warning(
                    request,
                    f"Sheet {sheet} reopened to Draft. The reason has been audit-logged.",
                )
                return redirect("results:sheet_detail", pk=pk)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "A reason is required to reopen a sheet.")
        return redirect("results:sheet_detail", pk=pk)


class PublishClassTermView(AdminRequiredMixin, View):
    """Admin: PUBLISH every confirmed sheet for one class + term."""

    def post(self, request):
        form = PublishClassTermForm(request.POST)
        if form.is_valid():
            school_class = form.cleaned_data["school_class"]
            term = form.cleaned_data["term"]
            try:
                count = services.publish_class_term(
                    school_class=school_class,
                    term=term,
                    actor=request.user,
                    request=request,
                )
                if count:
                    messages.success(
                        request,
                        f"Published {count} confirmed sheet(s) for {school_class} — {term}.",
                    )
                else:
                    messages.info(
                        request,
                        f"No confirmed sheets pending publication for {school_class} — {term}.",
                    )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Choose both a class and a term to publish.")
        return redirect("results:sheet_list")


# ── Grading configuration ────────────────────────────────────────────────────


class GradingConfigListView(AdminRequiredMixin, TemplateView):
    """Admin: pass mark and grading system overview per academic year."""

    template_name = "features/results/grading_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        years = []
        for year in AcademicYear.objects.all().order_by("-start_date"):
            data = selectors.grading_for_year(year.pk)
            years.append(
                {
                    "year": year,
                    "config": data["config"],
                    "scale": data["scale"],
                    "band_count": len(data["bands"]),
                    "class_count": len(data["classes"]),
                    "selected_count": len(data["selected_class_ids"]),
                }
            )
        ctx["years"] = years
        return ctx


class GradingConfigEditView(AdminRequiredMixin, TemplateView):
    """Admin: the year's pass mark. Bands are edited on the grading system."""

    template_name = "features/results/grading_config.html"

    def _get_year(self, year_id) -> AcademicYear:
        return get_object_or_404(AcademicYear, pk=year_id)

    def get(self, request, year_id):
        year = self._get_year(year_id)
        data = selectors.grading_for_year(year.pk)
        config = data["config"]
        form = GradingConfigForm(
            initial={"pass_mark": config.pass_mark if config else 50}
        )
        return self.render_to_response(
            {"year": year, "form": form, "scale": data["scale"]}
        )

    def post(self, request, year_id):
        year = self._get_year(year_id)
        form = GradingConfigForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please fix the errors below.")
            return self.render_to_response(
                {
                    "year": year,
                    "form": form,
                    "scale": selectors.grading_for_year(year.pk)["scale"],
                }
            )

        services.save_grading_config(
            academic_year=year,
            pass_mark=form.cleaned_data["pass_mark"],
            actor=request.user,
            request=request,
        )
        messages.success(request, f"Pass mark for {year.name} saved.")
        return redirect("results:grading_list")


class GradeScaleEditView(AdminRequiredMixin, TemplateView):
    """Admin: create/edit the year's grading system, its bands and classes."""

    template_name = "features/results/grade_scale_form.html"

    def _get_year(self, year_id) -> AcademicYear:
        return get_object_or_404(AcademicYear, pk=year_id)

    def _context(self, year: AcademicYear, form=None, band_formset=None) -> dict:
        data = selectors.grading_for_year(year.pk)
        scale = data["scale"]
        if form is None:
            form = GradeScaleForm(initial={"name": scale.name if scale else ""})
        if band_formset is None:
            band_formset = GradeBandFormSet(
                initial=[
                    {
                        "min_score": band.min_score,
                        "max_score": band.max_score,
                        "grade": band.grade,
                        "remark": band.remark,
                        "points": band.points,
                    }
                    for band in (scale.bands.all() if scale else [])
                ],
                prefix="bands",
            )
        return {
            "year": year,
            "form": form,
            "band_formset": band_formset,
            "scale": scale,
            "classes": data["classes"],
            "selected_class_ids": data["selected_class_ids"],
        }

    def get(self, request, year_id):
        return self.render_to_response(self._context(self._get_year(year_id)))

    def post(self, request, year_id):
        year = self._get_year(year_id)
        form = GradeScaleForm(request.POST)
        band_formset = GradeBandFormSet(request.POST, prefix="bands")
        ctx = self._context(year, form=form, band_formset=band_formset)

        year_classes = {school_class.pk: school_class for school_class in ctx["classes"]}
        selected = []
        invalid = False
        for pk in request.POST.getlist("classes"):
            try:
                selected.append(year_classes[int(pk)])
            except (KeyError, ValueError):
                invalid = True
        if invalid:
            form.add_error(None, "Every selected class must belong to this year.")

        if invalid or not form.is_valid() or not band_formset.is_valid():
            messages.error(request, "Please fix the errors below.")
            return self.render_to_response(ctx)

        bands = [
            {
                "min_score": f.cleaned_data["min_score"],
                "max_score": f.cleaned_data["max_score"],
                "grade": f.cleaned_data["grade"],
                "remark": f.cleaned_data.get("remark") or "",
                "points": f.cleaned_data.get("points") or 0,
            }
            for f in band_formset
            if f.cleaned_data and not f.cleaned_data.get("DELETE")
        ]

        try:
            result = services.save_grade_scale(
                academic_year=year,
                name=form.cleaned_data["name"],
                bands=bands,
                classes=selected,
                actor=request.user,
                request=request,
            )
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                for field, errs in exc.message_dict.items():
                    if field == "bands":
                        band_formset.non_form_errors = band_formset.error_class(errs)
                    else:
                        for err in errs:
                            form.add_error(None, err)
            else:
                form.add_error(None, exc)
            return self.render_to_response(ctx)

        changed = result["regrade"]["changed"]
        messages.success(
            request,
            f"Grading system for {year.name} saved. "
            f"{len(selected)} class(es) selected; {changed} mark(s) re-graded.",
        )
        return redirect("results:grading_list")


class GradeScaleDeleteView(AdminRequiredMixin, View):
    """Admin: remove the year's grading system; classes fall back to year bands."""

    def post(self, request, year_id):
        year = get_object_or_404(AcademicYear, pk=year_id)
        scale = selectors.grading_for_year(year.pk)["scale"]
        if scale is None:
            messages.info(request, f"{year.name} has no grading system to remove.")
            return redirect("results:grading_list")

        services.delete_grade_scale(scale=scale, actor=request.user, request=request)
        messages.success(
            request,
            f"Grading system for {year.name} removed. "
            "Its classes now use the year's fallback grade bands.",
        )
        return redirect("results:grading_list")
