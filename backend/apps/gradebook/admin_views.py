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
