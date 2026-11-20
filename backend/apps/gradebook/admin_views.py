"""
apps/gradebook/admin_views.py
-----------------------------
The administrator's side of the gradebook: lock or reopen one term.

The lock belongs to the school, not to a teacher, so it covers every classroom
in that term. It is deliberately narrow — result sheets, marks entry and the
homework screens keep their own locks.
"""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView

from apps.academics.models import Term
from apps.core.mixins import AdminRequiredMixin

from . import services
from .models import GradebookSetup
from .views import GradebookEnabledMixin


class AdminGradebookLockView(AdminRequiredMixin, GradebookEnabledMixin, TemplateView):
    template_name = "features/gradebook/admin_locks.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        terms = list(
            Term.objects.select_related("academic_year")
            .prefetch_related("gradebook_lock")
            .order_by("-academic_year__start_date", "start_date", "name")
        )
        counts = {
            row["term_id"]: row["n"]
            for row in GradebookSetup.objects.filter(term__in=terms)
            .values("term_id")
            .annotate(n=Count("pk"))
        }
        ctx["rows"] = [
            {
                "term": term,
                "lock": getattr(term, "gradebook_lock", None),
                "gradebook_count": counts.get(term.pk, 0),
            }
            for term in terms
        ]
        return ctx

    def post(self, request):
        term = get_object_or_404(
            Term.objects.select_related("academic_year"),
            pk=request.POST.get("term_id"),
        )
        action = request.POST.get("action")
        if action not in ("lock", "unlock"):
            messages.error(request, "Unknown action.")
            return redirect("gradebook:admin_locks")

        locking = action == "lock"
        services.set_term_lock(
            term=term,
            locked=locking,
            reason=request.POST.get("reason", ""),
            actor=request.user,
            request=request,
        )
        messages.success(
            request,
            (
                f"{term.academic_year.name} · {term.get_name_display} is now "
                "locked. Teachers can read the gradebook but cannot change it."
            )
            if locking
            else (
                f"{term.academic_year.name} · {term.get_name_display} is open "
                "again. Teachers can change and release marks."
            ),
        )
        return redirect("gradebook:admin_locks")


class AdminGradebookSetupListView(AdminRequiredMixin, GradebookEnabledMixin, TemplateView):
    """
    Admin view: list all GradebookSetups for a term so the admin can link
    each setup to a result sheet (the exam_sheet FK).

    URL: /gradebook/admin/setups/?term=<pk>
    """

    template_name = "features/gradebook/admin_setup_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.academics.models import Term
        from apps.results.models import ResultSheet

        terms = list(
            Term.objects.select_related("academic_year")
            .order_by("-academic_year__start_date", "start_date", "name")
        )
        ctx["terms"] = terms

        term_id = self.request.GET.get("term")
        selected_term = None
        if term_id:
            selected_term = next((t for t in terms if str(t.pk) == term_id), None)
        if selected_term is None and terms:
            selected_term = next(
                (t for t in terms if getattr(t, "is_current", False)), terms[0]
            )
        ctx["selected_term"] = selected_term

        rows = []
        if selected_term:
            setups = (
                GradebookSetup.objects.filter(term=selected_term)
                .select_related(
                    "school_class", "subject", "stream", "exam_sheet__exam"
                )
                .order_by("school_class__level_order", "school_class__name", "subject__name")
            )
            for setup in setups:
                # Candidate result sheets: same class + subject + term
                candidates = list(
                    ResultSheet.objects.filter(
                        school_class=setup.school_class,
                        subject=setup.subject,
                        term=selected_term,
                    )
                    .select_related("exam")
                    .order_by("exam__order", "pk")
                )
                rows.append({"setup": setup, "candidates": candidates})
        ctx["rows"] = rows
        return ctx

    def post(self, request):
        from apps.results.models import ResultSheet

        setup_id = request.POST.get("setup_id")
        sheet_id = request.POST.get("exam_sheet") or None
        setup = get_object_or_404(GradebookSetup, pk=setup_id)

        exam_sheet = None
        if sheet_id:
            exam_sheet = get_object_or_404(
                ResultSheet,
                pk=sheet_id,
                school_class=setup.school_class,
                subject=setup.subject,
                term=setup.term,
            )

        setup.exam_sheet = exam_sheet
        setup.save(update_fields=["exam_sheet", "updated_at"])

        from apps.core.audit_log import log_action
        log_action(
            actor=request.user,
            action="gradebook.exam_sheet_linked" if exam_sheet else "gradebook.exam_sheet_unlinked",
            content_type="gradebook.GradebookSetup",
            object_id=setup.pk,
            change_summary={
                "class": setup.school_class.name,
                "subject": setup.subject.code,
                "sheet_id": exam_sheet.pk if exam_sheet else None,
            },
            request=request,
        )

        messages.success(
            request,
            f"{'Linked' if exam_sheet else 'Unlinked'} result sheet for "
            f"{setup.school_class.name} · {setup.subject.name}."
        )
        return redirect(
            f"{request.path}?term={setup.term_id}"
        )
