"""
apps/dashboards/views_director.py
-----------------------------------
All Director-specific sub-page views.

Access rule: every view uses DirectorRequiredMixin — only role=director
(and is_superuser) may reach these pages.  No POST to any data-mutating
endpoint is permitted; the only POST allowed is creating an announcement
(which is delegated to communications.services).

The director CAN:
  - Read all analytics, finance reports, staff directory, announcements.
  - Create / schedule announcements.
  - Export reports to Excel or PDF.

The director CANNOT:
  - Modify student records, results, fees, or any other data entity.
"""

from __future__ import annotations

from datetime import date

from django.conf import settings as _settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView, View

from apps.academics.selectors import (
    get_current_term,
    get_current_year,
)
from apps.core.exports import (
    append_rows,
    stamp_document,
    start_workbook,
    xlsx_response,
)
from apps.core.features import feature_disabled_message, feature_enabled
from apps.core.mixins import DirectorRequiredMixin, RoleRequiredMixin
from apps.core.money import currency_code

from .cache import get_cached_school_kpis
from .selectors import (
    class_fee_debtors,
    class_fee_summary,
    class_performance,
    class_performance_trend,
    income_vs_expenses_by_month,
    staff_directory,
)


# Admin + Director can create announcements
class AnnouncementWriteMixin(RoleRequiredMixin):
    allowed_roles = ["admin", "director"]


# ---------------------------------------------------------------------------
# School Analysis Home  (replaces the stub DirectorHomeView)
# ---------------------------------------------------------------------------


class DirectorHomeView(DirectorRequiredMixin, TemplateView):
    template_name = "dashboards/director/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        term = get_current_term()
        ctx["current_term"] = term
        ctx["currency"] = currency_code(self.request)

        if term:
            kpis = get_cached_school_kpis(term=term)
            ctx.update(kpis)

            # Enrollment by class as JSON for Chart.js
            import json

            class_labels = [r["class_name"] for r in kpis["student_by_class"]]
            class_counts = [r["count"] for r in kpis["student_by_class"]]
            ctx["chart_enrollment_labels"] = json.dumps(class_labels)
            ctx["chart_enrollment_data"] = json.dumps(class_counts)

            # Fee collection bar chart data
            fee_rows = class_fee_summary(term_id=term.pk)
            ctx["fee_rows"] = fee_rows
            ctx["chart_fee_labels"] = json.dumps([r["class_name"] for r in fee_rows])
            ctx["chart_fee_billed"] = json.dumps([float(r["billed"]) for r in fee_rows])
            ctx["chart_fee_paid"] = json.dumps(
                [float(r["collected"]) for r in fee_rows]
            )

        from apps.communications.selectors import announcements_for_user

        ctx["recent_announcements"] = announcements_for_user(user=self.request.user)[:5]
        return ctx


# ---------------------------------------------------------------------------
# Class Performance
# ---------------------------------------------------------------------------


class ClassPerformanceView(DirectorRequiredMixin, TemplateView):
    template_name = "features/director/class_performance.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        term = get_current_term()
        ctx["current_term"] = term

        term_id_param = self.request.GET.get("term")
        from apps.academics.models import Term

        all_terms = Term.objects.select_related("academic_year").order_by(
            "-academic_year__start_date", "name"
        )
        ctx["all_terms"] = all_terms

        selected_term_id = (
            int(term_id_param) if term_id_param else (term.pk if term else None)
        )
        ctx["selected_term_id"] = selected_term_id

        rows = []
        if selected_term_id:
            rows = class_performance(term_id=selected_term_id)
        ctx["rows"] = rows

        # Chart data: class names vs avg score
        import json

        ctx["chart_labels"] = json.dumps([r["class_name"] for r in rows])
        ctx["chart_avg"] = json.dumps([r["avg_score"] or 0 for r in rows])
        ctx["chart_pass_rate"] = json.dumps([r["pass_rate"] or 0 for r in rows])
        return ctx


class ClassPerformanceDrilldownView(DirectorRequiredMixin, TemplateView):
    template_name = "features/director/class_performance_drilldown.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_id = kwargs["class_id"]

        term = get_current_term()
        ctx["current_term"] = term

        term_id_param = self.request.GET.get("term")
        selected_term_id = (
            int(term_id_param) if term_id_param else (term.pk if term else None)
        )

        if selected_term_id:
            all_rows = class_performance(term_id=selected_term_id)
            rows_for_class = [r for r in all_rows if r["class_id"] == class_id]
            if rows_for_class:
                ctx["class_row"] = rows_for_class[0]

        # Trend chart across all terms
        trend = class_performance_trend(class_id=class_id)
        import json

        ctx["trend"] = trend
        ctx["chart_trend_labels"] = json.dumps([t["term_label"] for t in trend])
        ctx["chart_trend_avg"] = json.dumps([t["avg_score"] or 0 for t in trend])

        from apps.academics.models import SchoolClass

        ctx["school_class"] = get_object_or_404(SchoolClass, pk=class_id)
        ctx["selected_term_id"] = selected_term_id

        from apps.academics.models import Term

        ctx["all_terms"] = Term.objects.select_related("academic_year").order_by(
            "-academic_year__start_date", "name"
        )
        return ctx


# ---------------------------------------------------------------------------
# Class Fee Analysis
# ---------------------------------------------------------------------------


class ClassFeeOverviewView(DirectorRequiredMixin, TemplateView):
    template_name = "features/director/class_fee_overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        term = get_current_term()

        term_id_param = self.request.GET.get("term")
        from apps.academics.models import Term

        all_terms = Term.objects.select_related("academic_year").order_by(
            "-academic_year__start_date", "name"
        )
        ctx["all_terms"] = all_terms

        selected_term_id = (
            int(term_id_param) if term_id_param else (term.pk if term else None)
        )
        ctx["selected_term_id"] = selected_term_id
        ctx["current_term"] = term
        ctx["currency"] = currency_code(self.request)

        ctx["fees_enabled"] = feature_enabled("fees")
        rows = []
        if not ctx["fees_enabled"]:
            # An empty table would read as "nobody owes anything", which is a
            # different sentence from "this school has not switched fees on".
            ctx["fees_disabled_message"] = feature_disabled_message("fees")
        elif selected_term_id:
            rows = class_fee_summary(term_id=selected_term_id)
        ctx["rows"] = rows

        import json

        ctx["chart_labels"] = json.dumps([r["class_name"] for r in rows])
        ctx["chart_billed"] = json.dumps([float(r["billed"]) for r in rows])
        ctx["chart_paid"] = json.dumps([float(r["collected"]) for r in rows])
        ctx["chart_outstanding"] = json.dumps([float(r["outstanding"]) for r in rows])
        return ctx


class ClassFeeDebtorsView(DirectorRequiredMixin, TemplateView):
    template_name = "features/director/class_fee_debtors.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_id = kwargs["class_id"]
        term = get_current_term()

        term_id_param = self.request.GET.get("term")
        selected_term_id = (
            int(term_id_param) if term_id_param else (term.pk if term else None)
        )
        ctx["selected_term_id"] = selected_term_id
        ctx["currency"] = currency_code(self.request)

        from apps.academics.models import SchoolClass, Term

        ctx["school_class"] = get_object_or_404(SchoolClass, pk=class_id)
        ctx["all_terms"] = Term.objects.select_related("academic_year").order_by(
            "-academic_year__start_date", "name"
        )

        ctx["fees_enabled"] = feature_enabled("fees")
        debtors = []
        if not ctx["fees_enabled"]:
            ctx["fees_disabled_message"] = feature_disabled_message("fees")
        elif selected_term_id:
            debtors = class_fee_debtors(class_id=class_id, term_id=selected_term_id)
        ctx["debtors"] = debtors
        return ctx


# ---------------------------------------------------------------------------
# Staff Directory
# ---------------------------------------------------------------------------


class StaffDirectoryView(DirectorRequiredMixin, TemplateView):
    template_name = "features/director/staff_directory.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        search = self.request.GET.get("search", "")
        role = self.request.GET.get("role", "")
        ctx["staff"] = staff_directory(search=search, role=role)
        ctx["search"] = search
        ctx["selected_role"] = role
        from apps.accounts.models import User

        ctx["role_choices"] = [
            (r, label)
            for r, label in User.Role.choices
            if r not in ("student", "parent")
        ]
        return ctx


# ---------------------------------------------------------------------------
# Books of Account (read-only wrappers re-using finance selectors)
# ---------------------------------------------------------------------------


class BooksOverviewView(DirectorRequiredMixin, TemplateView):
    template_name = "features/director/books_overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        term = get_current_term()
        ctx["current_term"] = term
        ctx["currency"] = currency_code(self.request)

        term_id_param = self.request.GET.get("term")
        from apps.academics.models import Term

        all_terms = Term.objects.select_related("academic_year").order_by(
            "-academic_year__start_date", "name"
        )
        ctx["all_terms"] = all_terms

        selected_term_id = (
            int(term_id_param) if term_id_param else (term.pk if term else None)
        )
        ctx["selected_term_id"] = selected_term_id

        ctx["fees_enabled"] = feature_enabled("fees")
        if not ctx["fees_enabled"]:
            # This page *is* the term's money summary, so with the switch off
            # there is nothing here to print — a sentence, not a table of zeros.
            ctx["fees_disabled_message"] = feature_disabled_message("fees")
            return ctx

        if selected_term_id:
            from apps.finance.interface import report_summary

            ctx["summary"] = report_summary(term_id=selected_term_id)

        # Income vs expense by month for current academic year
        year = get_current_year()
        ctx["current_year"] = year
        if year:
            monthly = income_vs_expenses_by_month(year_id=year.pk)
            import json

            ctx["chart_monthly_labels"] = json.dumps(
                [m["month_label"] for m in monthly]
            )
            # The interface hands out Decimals; Chart.js needs JSON numbers, so
            # the cast happens here at the chart boundary and nowhere else.
            ctx["chart_monthly_income"] = json.dumps(
                [float(m["income"]) for m in monthly]
            )
            ctx["chart_monthly_expense"] = json.dumps(
                [float(m["expense"]) for m in monthly]
            )
        return ctx


class BooksLedgerView(DirectorRequiredMixin, TemplateView):
    template_name = "features/director/books_ledger.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["currency"] = currency_code(self.request)
        from apps.finance.interface import account_list, ledger_entries

        date_from_str = self.request.GET.get("date_from")
        date_to_str = self.request.GET.get("date_to")
        account_id = self.request.GET.get("account")

        date_from = date.fromisoformat(date_from_str) if date_from_str else None
        date_to = date.fromisoformat(date_to_str) if date_to_str else None
        account_id_int = int(account_id) if account_id else None

        ctx["lines"] = ledger_entries(
            account_id=account_id_int,
            date_from=date_from,
            date_to=date_to,
        )[:500]
        ctx["accounts"] = account_list()
        ctx["selected_account_id"] = account_id_int
        ctx["date_from"] = date_from_str or ""
        ctx["date_to"] = date_to_str or ""
        return ctx


class BooksTrialBalanceView(DirectorRequiredMixin, TemplateView):
    template_name = "features/director/books_trial_balance.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["currency"] = currency_code(self.request)

        from apps.finance import interface as finance

        # The totals come from Finance, not from re-adding the rows here: the
        # tick mark has to be Finance's own "does the ledger balance" answer.
        trial = finance.trial_balance_at()
        ctx["rows"] = trial["rows"]
        ctx["total_debits"] = trial["total_debits"]
        ctx["total_credits"] = trial["total_credits"]
        ctx["balanced"] = trial["total_debits"] == trial["total_credits"]
        ctx["as_of"] = trial["as_of"]
        return ctx


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


class ExportClassPerformanceExcelView(DirectorRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        term = get_current_term()
        if not term:
            return HttpResponse("No current term.", status=400)
        term_id_param = request.GET.get("term")
        term_id = int(term_id_param) if term_id_param else term.pk

        rows = class_performance(term_id=term_id)

        wb = start_workbook("Class Performance")
        ws = wb.active
        ws.append(["Class", "Students", "Avg Score (%)", "Pass Rate (%)"])
        append_rows(
            ws,
            (
                [
                    r["class_name"],
                    r["student_count"],
                    r["avg_score"] or "",
                    r["pass_rate"] or "",
                ]
                for r in rows
            ),
        )
        stamp_document(
            wb,
            title="Class performance",
            source="Director > Exports > Class performance",
            actor=request.user,
        )
        return xlsx_response(wb, "class_performance.xlsx")


class ExportClassFeeExcelView(DirectorRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        term = get_current_term()
        if not term:
            return HttpResponse("No current term.", status=400)
        term_id_param = request.GET.get("term")
        term_id = int(term_id_param) if term_id_param else term.pk

        rows = class_fee_summary(term_id=term_id)

        wb = start_workbook("Fee Collection")
        ws = wb.active
        ws.append(
            [
                "Class",
                "Billed",
                "Collected",
                "Waived / Adjusted",
                "Outstanding",
                "Collection Rate (%)",
            ]
        )
        append_rows(
            ws,
            (
                [
                    r["class_name"],
                    float(r["billed"]),
                    float(r["collected"]),
                    float(r["adjusted"]),
                    float(r["outstanding"]),
                    float(r["collection_rate"])
                    if r["collection_rate"] is not None
                    else "",
                ]
                for r in rows
            ),
        )
        stamp_document(
            wb,
            title="Class fee collection",
            source="Director > Exports > Fee collection",
            actor=request.user,
        )
        return xlsx_response(wb, "class_fee_collection.xlsx")


class ExportStaffDirectoryExcelView(DirectorRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        staff = staff_directory()

        wb = start_workbook("Staff Directory")
        ws = wb.active
        ws.append(["Name", "Role", "Email", "Phone"])
        append_rows(
            ws,
            (
                [
                    u.get_full_name(),
                    u.get_role_display(),
                    u.email,
                    u.phone or "",
                ]
                for u in staff
            ),
        )
        stamp_document(
            wb,
            title="Staff directory",
            source="Director > Exports > Staff directory",
            actor=request.user,
        )
        return xlsx_response(wb, "staff_directory.xlsx")


class ExportBooksReportPdfView(DirectorRequiredMixin, View):
    """PDF of the income/expense summary for the current (or selected) term."""

    def get(self, request, *args, **kwargs):
        term = get_current_term()
        if not term:
            return HttpResponse("No current term.", status=400)
        term_id_param = request.GET.get("term")
        term_id = int(term_id_param) if term_id_param else term.pk

        from apps.finance.interface import report_summary

        summary = report_summary(term_id=term_id)

        from django.template.loader import render_to_string

        html = render_to_string(
            "features/director/books_report_pdf.html",
            {
                "summary": summary,
                "currency": currency_code(self.request),
                "SCHOOL_NAME": _settings.SCHOOL_NAME,
                "SCHOOL_MOTTO": getattr(_settings, "SCHOOL_MOTTO", ""),
                "request": request,
            },
        )
        from apps.core.pdf import pdf_response

        return pdf_response(
            html=html,
            filename=f"finance_report_{term_id}.pdf",
            base_url=request.build_absolute_uri("/"),
        )
