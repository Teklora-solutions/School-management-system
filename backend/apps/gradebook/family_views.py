"""
apps/gradebook/family_views.py
------------------------------
What a pupil and a parent may read: the newest release of each subject's
gradebook, for the child the signed-in account is looking at.

Nothing here is computed live. Families see the frozen numbers a teacher
released, which is why a correction made after a release does not move a mark
they have already been shown.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView

from apps.core.mixins import RoleRequiredMixin

from . import selectors, services
from .views import GradebookEnabledMixin


class StudentOrParentMixin(RoleRequiredMixin):
    """Allow both family roles; the child is resolved per account."""

    allowed_roles = ["student", "parent"]


def _resolve_student(request):
    """
    The pupil this account is viewing.

    A pupil sees themselves; a parent sees the child held in the session by the
    child-switcher, falling back to their primary child and then the first
    linked child. No id comes from the URL, so a parent can never widen this.
    """
    from apps.students.models import Student

    if request.user.role == "student":
        if not hasattr(request.user, "student_profile"):
            raise PermissionDenied("No student profile linked to this account.")
        return request.user.student_profile

    if request.user.role == "parent":
        if not hasattr(request.user, "parent_profile"):
            raise PermissionDenied("No parent profile linked to this account.")
        child_ids = list(
            request.user.parent_profile.children_links.values_list(
                "student_id", flat=True
            )
        )
        if not child_ids:
            raise PermissionDenied("No children are linked to your account.")
        selected_id = request.session.get("selected_child_id")
        if selected_id and int(selected_id) in child_ids:
            student_id = int(selected_id)
        else:
            primary = (
                request.user.parent_profile.children_links.filter(is_primary=True)
                .values_list("student_id", flat=True)
                .first()
            )
            student_id = primary or child_ids[0]
        return get_object_or_404(
            Student.objects.select_related("user", "current_class", "current_stream"),
            pk=student_id,
        )

    raise PermissionDenied("Your role cannot access the gradebook.")


class MyGradebookView(StudentOrParentMixin, GradebookEnabledMixin, TemplateView):
    template_name = "features/gradebook/my_gradebook.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = _resolve_student(self.request)
        releases = selectors.family_releases(student)
        terms = selectors.family_terms(releases)

        requested = self.request.GET.get("term")
        selected = next(
            (term for term in terms if requested and str(term.pk) == requested),
            None,
        )
        if selected is None and terms:
            selected = terms[0]

        rows = []
        for entry in releases:
            if selected is not None and entry["setup"].term_id != selected.pk:
                continue
            result = entry["result"]
            bucket_pcts = result.bucket_pcts or {}
            rows.append(
                {
                    **entry,
                    "buckets": [
                        {"key": key, "label": label, "pct": bucket_pcts.get(key)}
                        for key, label, _field in services.BUCKETS
                        if key in bucket_pcts
                    ],
                }
            )

        ctx.update(
            {
                "student": student,
                "terms": terms,
                "selected_term": selected,
                "rows": rows,
                "has_releases": bool(releases),
            }
        )
        return ctx
