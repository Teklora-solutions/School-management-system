"""
apps/gradebook/director_views.py
--------------------------------
The director's gradebook overview: how each released classroom performed, read
from the frozen release snapshots.

Class, subject and counts only. A pupil's name never appears on this page or in
its exports — the director is looking at how a class is doing, and the figures
need no child attached to them.
"""

from __future__ import annotations

from django.http import Http404, HttpResponse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from apps.academics.selectors import get_current_term
from apps.academics.models import Term
from apps.core.exports import (
    append_rows,
    csv_response,
    format_column_widths,
    stamp_document,
    start_workbook,
    xlsx_response,
)
from apps.core.mixins import DirectorRequiredMixin

from . import selectors
from .views import GradebookEnabledMixin

EXPORT_HEADER = [
    "Class",
    "Stream",
    "Subject",
    "Version",
    "Released",
    "Pupils",
    "Average (%)",
    "Missing marks",
    "Marks excused",
    "Marks scored",
]


def _terms_with_gradebooks() -> list:
    return list(
        Term.objects.filter(gradebook_setups__isnull=False)
        .select_related("academic_year")
        .distinct()
        .order_by("-academic_year__start_date", "-start_date")
    )


def _resolve_term(request, terms: list):
    requested = request.GET.get("term")
    chosen = next(
        (term for term in terms if requested and str(term.pk) == requested), None
    )
    if chosen is not None:
        return chosen
    current = get_current_term()
    chosen = next(
        (term for term in terms if current and term.pk == current.pk), None
    )
    return chosen or (terms[0] if terms else None)


def _export_rows(overview: dict) -> list[list]:
    rows = [EXPORT_HEADER]
    for row in overview["rows"]:
        setup = row["setup"]
        rows.append(
            [
                setup.school_class.name,
                setup.stream.name if setup.stream else "",
                setup.subject.name,
                row["release"].version,
                timezone.localtime(row["release"].created_at).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                row["pupils"],
                "" if row["average"] is None else float(row["average"]),
                row["missing"],
                row["excused"],
                row["scored"],
            ]
        )
    return rows


class DirectorGradebookView(DirectorRequiredMixin, GradebookEnabledMixin, TemplateView):
    template_name = "features/gradebook/director_overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        terms = _terms_with_gradebooks()
        term = _resolve_term(self.request, terms)
        overview = (
            selectors.term_release_overview(term)
            if term is not None
            else {"rows": [], "summary": None}
        )
        ctx.update(
            {
                "terms": terms,
                "term": term,
                "rows": overview["rows"],
                "summary": overview["summary"],
                "has_rows": bool(overview["rows"]),
            }
        )
        return ctx


class DirectorGradebookExportView(DirectorRequiredMixin, GradebookEnabledMixin, View):
    def get(self, request, fmt: str):
        if fmt not in ("csv", "xlsx"):
            raise Http404("Unknown export format.")
        terms = _terms_with_gradebooks()
        term = _resolve_term(request, terms)
        if term is None:
            return HttpResponse("No gradebook has been set up yet.", status=400)

        rows = _export_rows(selectors.term_release_overview(term))
        filename = (
            f"gradebook_{term.academic_year.name}_{term.name}.{fmt}"
            .replace("/", "-")
            .replace(" ", "_")
        )
        if fmt == "csv":
            return csv_response(rows, filename)

        workbook = start_workbook("Gradebook releases")
        worksheet = workbook.active
        worksheet.append(rows[0])
        append_rows(worksheet, rows[1:])
        format_column_widths(worksheet)
        stamp_document(
            workbook,
            title=(
                f"Gradebook releases — {term.academic_year.name} "
                f"{term.get_name_display}"
            ),
            source="Director > Gradebook",
            actor=request.user,
        )
        return xlsx_response(workbook, filename)
