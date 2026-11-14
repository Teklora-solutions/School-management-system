"""
apps/gradebook/views.py
-----------------------
The teacher's gradebook screens.

Role first, then the feature switch — the same order as every other feature
view, so a pupil who guesses the URL gets 403 rather than a placeholder, and a
teacher at a school that has not switched the gradebook on gets the sentence
that explains why.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from apps.academics.models import SchoolClass, Subject
from apps.academics.selectors import get_current_term
from apps.core.features import feature_disabled_message, feature_enabled
from apps.core.mixins import TeacherRequiredMixin

from . import selectors, services


class GradebookEnabledMixin:
    """Render the placeholder when the school has not switched the module on."""

    def dispatch(self, request, *args, **kwargs):
        if not feature_enabled("gradebook", request=request):
            return render(
                request,
                "features/gradebook/disabled.html",
                {"disabled_message": feature_disabled_message("gradebook")},
            )
        return super().dispatch(request, *args, **kwargs)


class TeacherGradebookListView(TeacherRequiredMixin, GradebookEnabledMixin, TemplateView):
    """One line per classroom this teacher may keep a gradebook for."""

    template_name = "features/gradebook/teacher_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if getattr(self.request.user, "teacher_profile", None) is None:
            raise PermissionDenied("No teacher profile is linked to your account.")

        current = get_current_term()
        classrooms = []
        for assignment in selectors.teacher_classrooms(
            teacher_user_id=self.request.user.pk
        ):
            terms = list(selectors.terms_for_assignment(assignment))
            term = next(
                (t for t in terms if current and t.pk == current.pk),
                terms[-1] if terms else None,
            )
            classrooms.append(
                {
                    "assignment": assignment,
                    "term": term,
                    "href": (
                        reverse(
                            "gradebook:teacher_grid",
                            args=[assignment.school_class_id, assignment.subject_id],
                        )
                        + f"?term={term.pk}"
                        if term
                        else ""
                    ),
                }
            )

        ctx.update(
            {
                "classrooms": classrooms,
                "current_term": current,
                "has_classrooms": bool(classrooms),
            }
        )
        return ctx


class TeacherGradebookView(TeacherRequiredMixin, GradebookEnabledMixin, View):
    """The grid for one class + subject + term, computed live."""

    template_name = "features/gradebook/teacher_grid.html"

    def get(self, request, class_id: int, subject_id: int):
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        subject = get_object_or_404(Subject, pk=subject_id)

        assignment = selectors.classroom_assignment(
            teacher_user_id=request.user.pk,
            school_class_id=school_class.pk,
            subject_id=subject.pk,
        )
        if assignment is None:
            raise PermissionDenied("You are not assigned to this classroom.")

        terms = list(selectors.terms_for_assignment(assignment))
        if not terms:
            return render(
                request,
                self.template_name,
                {
                    "school_class": school_class,
                    "subject": subject,
                    "assignment": assignment,
                    "no_terms": True,
                },
            )

        current = get_current_term()
        requested = request.GET.get("term")
        term = next(
            (t for t in terms if requested and str(t.pk) == requested),
            None,
        )
        if term is None:
            term = next(
                (t for t in terms if current and t.pk == current.pk),
                terms[-1],
            )

        services.resolve_classroom(
            teacher_user_id=request.user.pk,
            school_class_id=school_class.pk,
            subject_id=subject.pk,
            term=term,
        )
        setup = services.get_or_create_setup(
            school_class=school_class,
            subject=subject,
            term=term,
            stream=assignment.stream,
            actor=request.user,
            request=request,
        )
        grid = services.build_grid(setup)

        return render(
            request,
            self.template_name,
            {
                "school_class": school_class,
                "subject": subject,
                "assignment": assignment,
                "setup": setup,
                "term": term,
                "terms": terms,
                "grid": grid,
                "latest_release": services.latest_release(setup),
                "no_terms": False,
            },
        )
