"""
apps/homework/teacher_views.py
------------------------------
The teacher's homework workspace: list, create, edit, publish/unpublish,
delete and preview.

Authorisation is by ``TeacherAssignment``: every view resolves homework
through ``selectors.homework_for_teacher`` (or validates the posted
class + subject against the same list), so a teacher can only touch homework
in classes and subjects they are allocated this academic year.
"""

from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView

from apps.academics.selectors import get_current_term
from apps.core.mixins import TeacherRequiredMixin

from . import selectors, services
from .forms import HomeworkForm
from .models import Homework
from .views import ACCEPTED_FILE_TYPES


def _profile(request):
    profile = getattr(request.user, "teacher_profile", None)
    if profile is None:
        raise PermissionDenied("No teacher profile is linked to your account.")
    return profile


def _safe_next(request, fallback: str) -> str:
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, {request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return reverse(fallback)


def _scoped_homework(request, pk: int) -> Homework:
    return get_object_or_404(
        selectors.homework_for_teacher(teacher_user_id=request.user.pk),
        pk=pk,
    )


def _workspace_context(*, form: HomeworkForm, mode: str, homework=None):
    return {
        "form": form,
        "mode": mode,
        "homework": homework,
        "term": get_current_term(),
        "has_assignments": bool(form.assignments),
        "has_terms": bool(form.term_options),
        "max_attachments": 5,
        "accepted_types": ACCEPTED_FILE_TYPES,
        "existing_attachments": (
            list(homework.attachments.all()) if homework is not None else []
        ),
    }


class TeacherHomeworkListView(TeacherRequiredMixin, TemplateView):
    template_name = "features/homework/teacher_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        _profile(self.request)
        request = self.request

        term = get_current_term()
        assignments = selectors.teacher_assignments(
            teacher_user_id=request.user.pk
        )
        classes = {}
        for assignment in assignments:
            classes.setdefault(assignment.school_class_id, assignment.school_class)

        terms = (
            term.academic_year.terms.order_by("name")
            if term is not None
            else []
        )

        selected_term_id = request.GET.get("term")
        if selected_term_id:
            selected_term_id = int(selected_term_id) if selected_term_id.isdigit() else None
        else:
            selected_term_id = term.pk if term else None

        selected_class_id = request.GET.get("class")
        selected_class_id = (
            int(selected_class_id) if selected_class_id and selected_class_id.isdigit() else None
        )
        selected_status = request.GET.get("status") or ""
        if selected_status not in (Homework.Status.DRAFT, Homework.Status.PUBLISHED):
            selected_status = ""

        rows = list(
            selectors.homework_for_teacher(
                teacher_user_id=request.user.pk,
                term_id=selected_term_id,
                class_id=selected_class_id,
                status=selected_status or None,
            )
        )
        today = date.today()
        submission_counts = selectors.submission_counts(
            homework_ids=[row.pk for row in rows]
        )
        for row in rows:
            row.attachment_count = len(row.attachments.all())
            row.submission_count = submission_counts.get(row.pk, 0)
            row.is_overdue = (
                row.status == Homework.Status.PUBLISHED and row.due_date < today
            )

        all_rows = selectors.homework_for_teacher(
            teacher_user_id=request.user.pk
        )
        counts = {
            "draft": all_rows.filter(status=Homework.Status.DRAFT).count(),
            "published": all_rows.filter(status=Homework.Status.PUBLISHED).count(),
            "overdue": all_rows.filter(
                status=Homework.Status.PUBLISHED, due_date__lt=today
            ).count(),
        }

        ctx.update(
            {
                "term": term,
                "terms": terms,
                "rows": rows,
                "classes": sorted(
                    classes.values(), key=lambda c: (c.level_order, c.name)
                ),
                "selected_term_id": selected_term_id,
                "selected_class_id": selected_class_id,
                "selected_status": selected_status,
                "today": today,
                "counts": counts,
                "has_assignments": bool(assignments),
            }
        )
        return ctx


class TeacherHomeworkCreateView(TeacherRequiredMixin, View):
    template_name = "features/homework/teacher_form.html"

    def get(self, request):
        form = HomeworkForm(teacher_user_id=request.user.pk)
        return render(
            request,
            self.template_name,
            _workspace_context(form=form, mode="create"),
        )

    def post(self, request):
        form = HomeworkForm(request.POST, teacher_user_id=request.user.pk)
        files = request.FILES.getlist("attachments")
        publish = request.POST.get("action") == "publish"

        if form.is_valid():
            try:
                homework = services.create_homework(
                    assignment=form.assignment,
                    term=form.cleaned_data["term"],
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    due_date=form.cleaned_data["due_date"],
                    files=files,
                    publish=publish,
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                where = homework.school_class.name
                if publish:
                    messages.success(
                        request,
                        f"Homework published to {where}. Students can see it now.",
                    )
                else:
                    messages.success(
                        request,
                        f"Homework saved as a draft for {where}. Publish it when you are ready.",
                    )
                return redirect("homework:teacher_list")

        return render(
            request,
            self.template_name,
            _workspace_context(form=form, mode="create"),
        )


class TeacherHomeworkEditView(TeacherRequiredMixin, View):
    template_name = "features/homework/teacher_form.html"

    def get(self, request, pk):
        homework = _scoped_homework(request, pk)
        form = HomeworkForm(
            teacher_user_id=request.user.pk, instance=homework, is_create=False
        )
        return render(
            request,
            self.template_name,
            _workspace_context(form=form, mode="edit", homework=homework),
        )

    def post(self, request, pk):
        homework = _scoped_homework(request, pk)
        form = HomeworkForm(
            request.POST,
            teacher_user_id=request.user.pk,
            instance=homework,
            is_create=False,
        )
        files = request.FILES.getlist("attachments")
        action = request.POST.get("action", "save")
        if action not in ("save", "publish", "unpublish"):
            action = "save"

        if form.is_valid():
            owned_ids = set(
                homework.attachments.values_list("pk", flat=True)
            )
            remove_ids = [
                int(pk_value)
                for pk_value in request.POST.getlist("remove_attachment")
                if pk_value.isdigit() and int(pk_value) in owned_ids
            ]
            try:
                homework = services.update_homework(
                    homework=homework,
                    assignment=form.assignment,
                    term=form.cleaned_data["term"],
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    due_date=form.cleaned_data["due_date"],
                    new_files=files,
                    remove_ids=remove_ids,
                    action=action,
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                if action == "publish":
                    messages.success(
                        request, "Homework published. Students can see it now."
                    )
                elif action == "unpublish":
                    messages.success(
                        request,
                        "Homework moved back to draft. Students can no longer see it.",
                    )
                else:
                    messages.success(request, "Homework updated.")
                return redirect("homework:teacher_list")

        return render(
            request,
            self.template_name,
            _workspace_context(form=form, mode="edit", homework=homework),
        )


class TeacherHomeworkPublishView(TeacherRequiredMixin, View):
    """Publish or unpublish from the list page (POST only)."""

    def post(self, request, pk):
        homework = _scoped_homework(request, pk)
        action = request.POST.get("action")
        if action == "publish":
            services.publish_homework(
                homework=homework, actor=request.user, request=request
            )
            messages.success(request, "Homework published.")
        elif action == "unpublish":
            services.unpublish_homework(
                homework=homework, actor=request.user, request=request
            )
            messages.success(request, "Homework moved back to draft.")
        else:
            messages.error(request, "Unknown action.")
        return redirect(_safe_next(request, "homework:teacher_list"))


class TeacherHomeworkDeleteView(TeacherRequiredMixin, View):
    def post(self, request, pk):
        homework = _scoped_homework(request, pk)
        try:
            services.delete_homework(
                homework=homework, actor=request.user, request=request
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(request, "Draft deleted.")
        return redirect(_safe_next(request, "homework:teacher_list"))


class TeacherHomeworkPreviewView(TeacherRequiredMixin, TemplateView):
    """The student-facing detail page, drafts included, for the author."""

    template_name = "features/homework/homework_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .views import homework_detail_context

        homework = get_object_or_404(
            selectors.homework_for_teacher(
                teacher_user_id=self.request.user.pk
            ).prefetch_related("attachments"),
            pk=self.kwargs["pk"],
        )
        ctx.update(homework_detail_context(homework, preview=True))
        return ctx


class TeacherHomeworkSubmissionsView(TeacherRequiredMixin, TemplateView):
    """
    Who has handed in this homework.

    The roster is the homework's class (and stream when one is set) using the
    same "still expected to hand work in" state as the rest of the school, so
    a pupil who left is not shown as missing.
    """

    template_name = "features/homework/teacher_submissions.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        homework = get_object_or_404(
            selectors.homework_for_teacher(
                teacher_user_id=self.request.user.pk
            ).prefetch_related("attachments"),
            pk=self.kwargs["pk"],
        )

        roster = list(
            selectors.class_roster(
                school_class_id=homework.school_class_id,
                stream_id=homework.stream_id,
            )
        )
        submissions = {
            submission.student_id: submission
            for submission in selectors.submissions_for_homework(
                homework_id=homework.pk
            )
        }
        rows = [
            {"student": student, "submission": submissions.get(student.pk)}
            for student in roster
        ]
        submitted = [row for row in rows if row["submission"] is not None]
        late = [row for row in submitted if row["submission"].is_late]

        ctx.update(
            {
                "homework": homework,
                "rows": rows,
                "roster_size": len(rows),
                "submitted_count": len(submitted),
                "late_count": len(late),
                "missing_count": len(rows) - len(submitted),
                "today": date.today(),
            }
        )
        return ctx

