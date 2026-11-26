from __future__ import annotations

import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views import View
from django.views.generic import TemplateView

from apps.core.features import feature_disabled_message, feature_enabled
from apps.core.mixins import (
    AdminOrDirectorMixin,
    DirectorRequiredMixin,
    RoleRequiredMixin,
    TeacherRequiredMixin,
)
from apps.homework.selectors import class_roster
from apps.students.models import Student

from . import selectors
from .classroom_views import (
    EngagementEnabledMixin,
    LearningEnabledMixin,
    StudentOrParentMixin,
    _resolve_student,
)

logger = logging.getLogger(__name__)

from . import points_selectors
from .points_services import reverse_award, teacher_bonus
from .models import PointLedger, StudentLevel


class StudentBadgesView(
    StudentOrParentMixin, LearningEnabledMixin, EngagementEnabledMixin, TemplateView
):
    """
    Student / parent view: level card, progress bar, badge icons, recent history.
    """
    template_name = "features/learning/student_badges.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = _resolve_student(self.request)
        summary = points_selectors.student_points_summary(student=student)
        ctx.update({
            "summary": summary,
            "student": student,
            "is_parent": self.request.user.role == "parent",
        })
        return ctx


class TeacherClassPointsView(
    TeacherRequiredMixin, LearningEnabledMixin, EngagementEnabledMixin, TemplateView
):
    """
    Teacher view: per-student points table for one class, with bonus and
    reversal controls.
    """
    template_name = "features/learning/teacher_class_points.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.academics.models import SchoolClass
        school_class = get_object_or_404(SchoolClass, pk=self.kwargs["class_id"])
        # Guard: teacher must be assigned to this class or be class teacher
        from . import feed_selectors as fs
        if not fs.can_view_feed(self.request.user, school_class):
            raise Http404
        rows = points_selectors.class_points_overview(school_class=school_class)
        ctx.update({
            "school_class": school_class,
            "rows": rows,
        })
        return ctx


class TeacherStudentLedgerView(
    TeacherRequiredMixin, LearningEnabledMixin, EngagementEnabledMixin, TemplateView
):
    """
    Teacher view: full ledger for one student — every award and reversal.
    """
    template_name = "features/learning/teacher_student_ledger.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.students.models import Student
        from apps.academics.models import SchoolClass
        student = get_object_or_404(Student, pk=self.kwargs["student_id"])
        school_class = get_object_or_404(SchoolClass, pk=self.kwargs["class_id"])
        from . import feed_selectors as fs
        if not fs.can_view_feed(self.request.user, school_class):
            raise Http404
        ledger = points_selectors.ledger_for_student(student=student, limit=100)
        summary = points_selectors.student_points_summary(student=student)
        ctx.update({
            "student": student,
            "school_class": school_class,
            "ledger": ledger,
            "summary": summary,
        })
        return ctx


class TeacherBonusAwardView(
    TeacherRequiredMixin, LearningEnabledMixin, EngagementEnabledMixin, TemplateView
):
    """
    POST-only view: award a manual bonus point to a student.
    GET redirects to the student ledger page.
    """
    template_name = "features/learning/teacher_bonus_form.html"

    def _get_student_and_class(self, kwargs):
        from apps.students.models import Student
        from apps.academics.models import SchoolClass
        student = get_object_or_404(Student, pk=kwargs["student_id"])
        school_class = get_object_or_404(SchoolClass, pk=kwargs["class_id"])
        return student, school_class

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student, school_class = self._get_student_and_class(self.kwargs)
        ctx.update({"student": student, "school_class": school_class})
        return ctx

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect
        student, school_class = self._get_student_and_class(kwargs)
        from . import feed_selectors as fs
        if not fs.can_view_feed(request.user, school_class):
            raise Http404

        try:
            pts = int(request.POST.get("points", 5))
        except (ValueError, TypeError):
            pts = 5
        description = request.POST.get("description", "").strip()

        try:
            teacher_bonus(
                student=student,
                awarded_by=request.user,
                points=pts,
                description=description,
            )
            messages.success(
                request,
                f"Bonus of {pts} point(s) awarded to {student.user.get_full_name()}.",
            )
        except (ValueError, PermissionDenied) as exc:
            messages.error(request, str(exc))

        return redirect(
            "learning:teacher_student_ledger",
            class_id=school_class.pk,
            student_id=student.pk,
        )


class TeacherReverseAwardView(
    TeacherRequiredMixin, LearningEnabledMixin, EngagementEnabledMixin, TemplateView
):
    """
    POST-only view: reverse a specific ledger entry.
    """

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect
        from apps.academics.models import SchoolClass
        school_class = get_object_or_404(SchoolClass, pk=self.kwargs["class_id"])
        from . import feed_selectors as fs
        if not fs.can_view_feed(request.user, school_class):
            raise Http404

        ledger_row = get_object_or_404(
            PointLedger,
            pk=self.kwargs["ledger_id"],
            student__current_class=school_class,
        )
        reason_text = request.POST.get("reason", "").strip()

        try:
            reverse_award(
                ledger_row=ledger_row,
                reversed_by=request.user,
                reason_text=reason_text,
            )
            messages.success(request, "Award reversed.")
        except ValueError as exc:
            messages.error(request, str(exc))

        return redirect(
            "learning:teacher_student_ledger",
            class_id=school_class.pk,
            student_id=ledger_row.student_id,
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class DirectorPointsOverviewView(
    DirectorRequiredMixin, LearningEnabledMixin, EngagementEnabledMixin, TemplateView
):
    """
    Director view: one row per class showing avg points, top earner,
    engagement on/off.
    """
    template_name = "features/learning/director_points_overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.academics.selectors import get_current_year
        year = get_current_year()
        rows = (
            points_selectors.director_points_overview(academic_year=year)
            if year else []
        )
        ctx.update({"rows": rows, "year": year})
        return ctx


# ---------------------------------------------------------------------------
# Course builder views — Checkpoint A
# ---------------------------------------------------------------------------
# All additive. Nothing above this line is touched.
# Gated behind learning_enabled. All teacher actions verify assignment.
# ---------------------------------------------------------------------------

