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
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView

from apps.academics.selectors import get_current_term
from apps.core.features import feature_disabled_message, feature_enabled
from apps.core.mixins import TeacherRequiredMixin
from apps.core.selectors import school_settings_values

from . import selectors, services
from .forms import HomeworkForm, HomeworkMarkForm
from .models import Homework, HomeworkMark
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
                    max_score=form.cleaned_data.get("max_score"),
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
                    max_score=form.cleaned_data.get("max_score"),
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
        grading_enabled = feature_enabled(
            "homework_grading", request=self.request
        )
        marks = (
            selectors.marks_for_homework(homework_id=homework.pk)
            if grading_enabled
            else {}
        )
        rows = [
            {
                "student": student,
                "submission": submissions.get(student.pk),
                "mark": marks.get(student.pk),
            }
            for student in roster
        ]
        submitted = [row for row in rows if row["submission"] is not None]
        late = [row for row in submitted if row["submission"].is_late]
        marked = [row for row in rows if row["mark"] and row["mark"].status]
        awaiting = [
            row
            for row in marked
            if not row["mark"].released_at
            and homework.marks_released_at is None
        ]

        ctx.update(
            {
                "homework": homework,
                "rows": rows,
                "roster_size": len(rows),
                "submitted_count": len(submitted),
                "late_count": len(late),
                "missing_count": len(rows) - len(submitted),
                "today": date.today(),
                "grading_enabled": grading_enabled,
                "marked_count": len(marked),
                "awaiting_release_count": len(awaiting),
                "marks_released": homework.marks_released_at is not None,
                "first_mark_student_id": (
                    rows[0]["student"].pk if rows else None
                ),
                "late_penalty_enabled": grading_enabled
                and school_settings_values(self.request)[
                    "homework_late_penalty_enabled"
                ],
                "filter_key": self.request.GET.get("filter", ""),
            }
        )
        return ctx


# ---------------------------------------------------------------------------
# Marking and feedback
# ---------------------------------------------------------------------------


def _grading_enabled(request) -> bool:
    return feature_enabled("homework_grading", request=request)


def _mark_rows(homework: Homework, filter_key: str) -> list:
    """The roster with each pupil's submission and mark, filtered for paging."""
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
    marks = selectors.marks_for_homework(homework_id=homework.pk)
    rows = [
        {
            "student": student,
            "submission": submissions.get(student.pk),
            "mark": marks.get(student.pk),
        }
        for student in roster
    ]
    if filter_key == "submitted":
        return [row for row in rows if row["submission"] is not None]
    if filter_key == "missing":
        return [row for row in rows if row["submission"] is None]
    if filter_key == "late":
        return [
            row
            for row in rows
            if row["submission"] is not None and row["submission"].is_late
        ]
    return rows


def _resolve_mark_target(request, pk: int, student_id: int):
    """Homework inside the teacher's assignments, and a pupil on its roster."""
    homework = get_object_or_404(
        selectors.homework_for_teacher(teacher_user_id=request.user.pk),
        pk=pk,
    )
    student = get_object_or_404(
        selectors.class_roster(
            school_class_id=homework.school_class_id,
            stream_id=homework.stream_id,
        ),
        pk=student_id,
    )
    return homework, student


def _mark_initial(mark) -> dict:
    return {
        "status": mark.status if mark else "",
        "score": mark.score if mark else None,
        "feedback": mark.feedback if mark else "",
        "late_deduction": mark.late_deduction if mark else None,
        "resubmissions_allowed": (
            mark.resubmissions_allowed
            if mark and mark.status == HomeworkMark.Status.NEEDS_REVISION
            else 1
        ),
    }


def _mark_url(homework: Homework, student_id: int, filter_key: str) -> str:
    url = reverse("homework:teacher_mark", args=[homework.pk, student_id])
    return f"{url}?filter={filter_key}" if filter_key else url


class TeacherHomeworkMarkView(TeacherRequiredMixin, View):
    """
    Mark one pupil's work, with Previous/Next.

    The page exists to let a teacher get through a class in one sitting: the
    roster filter carries through every step, so "the missing ones" stays
    "the missing ones" from first pupil to last.
    """

    template_name = "features/homework/teacher_mark.html"

    def _context(self, request, homework, student, form, filter_key):
        rows = _mark_rows(homework, filter_key)
        ids = [row["student"].pk for row in rows]
        position = ids.index(student.pk) if student.pk in ids else None
        submission = selectors.submission_for(
            homework_id=homework.pk, student_id=student.pk
        )
        mark = selectors.mark_for(
            homework_id=homework.pk, student_id=student.pk
        )
        window_open, used, allowed = selectors.resubmission_window(
            homework=homework, student=student
        )
        return {
            "homework": homework,
            "student": student,
            "submission": submission,
            "submission_attachments": (
                submission.current_attachments if submission else []
            ),
            "revisions": (
                list(submission.revisions.prefetch_related("attachments"))
                if submission
                else []
            ),
            "mark": mark,
            "form": form,
            "filter_key": filter_key,
            "late_penalty": form.late_penalty,
            "position": (position + 1) if position is not None else None,
            "row_count": len(rows),
            "prev_url": (
                _mark_url(homework, ids[position - 1], filter_key)
                if position not in (None, 0)
                else ""
            ),
            "next_url": (
                _mark_url(homework, ids[position + 1], filter_key)
                if position is not None and position + 1 < len(ids)
                else ""
            ),
            "resubmission_open": window_open,
            "resubmission_used": used,
            "resubmission_allowed": allowed,
            "mark_is_stale": bool(
                mark
                and submission
                and mark.marked_at
                and mark.marked_at < submission.submitted_at
            ),
        }

    def _redirect_if_off(self, request, pk):
        if _grading_enabled(request):
            return None
        messages.info(request, feature_disabled_message("homework_grading"))
        return redirect("homework:teacher_submissions", pk=pk)

    def get(self, request, pk, student_id):
        blocked = self._redirect_if_off(request, pk)
        if blocked is not None:
            return blocked
        homework, student = _resolve_mark_target(request, pk, student_id)
        mark = selectors.mark_for(
            homework_id=homework.pk, student_id=student.pk
        )
        form = HomeworkMarkForm(
            initial=_mark_initial(mark),
            late_penalty=school_settings_values(request)[
                "homework_late_penalty_enabled"
            ],
        )
        return render(
            request,
            self.template_name,
            self._context(
                request, homework, student, form, request.GET.get("filter", "")
            ),
        )

    def post(self, request, pk, student_id):
        blocked = self._redirect_if_off(request, pk)
        if blocked is not None:
            return blocked
        homework, student = _resolve_mark_target(request, pk, student_id)
        filter_key = request.POST.get("filter", "")
        form = HomeworkMarkForm(
            request.POST,
            late_penalty=school_settings_values(request)[
                "homework_late_penalty_enabled"
            ],
        )
        if form.is_valid():
            data = form.cleaned_data
            try:
                services.mark_homework(
                    homework=homework,
                    student=student,
                    score=data.get("score"),
                    status=data["status"],
                    feedback=data.get("feedback", ""),
                    late_deduction=data.get("late_deduction"),
                    resubmissions_allowed=data.get("resubmissions_allowed"),
                    actor=request.user,
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                name = student.user.get_full_name() or student.user.username
                messages.success(request, f"Mark saved for {name}.")
                if request.POST.get("goto") == "next":
                    ids = [
                        row["student"].pk
                        for row in _mark_rows(homework, filter_key)
                    ]
                    if student.pk in ids:
                        position = ids.index(student.pk)
                        if position + 1 < len(ids):
                            return redirect(
                                _mark_url(
                                    homework, ids[position + 1], filter_key
                                )
                            )
                return redirect(_mark_url(homework, student.pk, filter_key))

        return render(
            request,
            self.template_name,
            self._context(request, homework, student, form, filter_key),
        )


class TeacherHomeworkMarkAutosaveView(TeacherRequiredMixin, View):
    """
    Save the marking form without leaving the pupil.

    Same form, same service as the Save button, so a typed mark cannot travel
    down a laxer path than a posted one. Nothing is saved until a status is
    chosen: the status is what makes the mark mean something.
    """

    def post(self, request, pk, student_id):
        if not _grading_enabled(request):
            return JsonResponse(
                {
                    "ok": False,
                    "errors": [feature_disabled_message("homework_grading")],
                },
                status=403,
            )
        homework, student = _resolve_mark_target(request, pk, student_id)
        form = HomeworkMarkForm(
            request.POST,
            late_penalty=school_settings_values(request)[
                "homework_late_penalty_enabled"
            ],
        )
        if not form.is_valid():
            return JsonResponse(
                {
                    "ok": False,
                    "errors": [
                        message
                        for field_errors in form.errors.values()
                        for message in field_errors
                    ],
                },
                status=400,
            )
        data = form.cleaned_data
        try:
            mark = services.mark_homework(
                homework=homework,
                student=student,
                score=data.get("score"),
                status=data["status"],
                feedback=data.get("feedback", ""),
                late_deduction=data.get("late_deduction"),
                resubmissions_allowed=data.get("resubmissions_allowed"),
                actor=request.user,
                request=request,
            )
        except ValidationError as exc:
            return JsonResponse({"ok": False, "errors": exc.messages}, status=400)
        return JsonResponse(
            {
                "ok": True,
                "saved_at": timezone.localtime(mark.marked_at).strftime("%H:%M"),
                "score": (
                    str(mark.final_score) if mark.final_score is not None else ""
                ),
                "status": mark.get_status_display(),
            }
        )


class TeacherHomeworkReleaseView(TeacherRequiredMixin, View):
    """Release marks for the whole class, or one pupil (POST only)."""

    def post(self, request, pk):
        if not _grading_enabled(request):
            messages.info(request, feature_disabled_message("homework_grading"))
            return redirect("homework:teacher_submissions", pk=pk)
        homework = _scoped_homework(request, pk)
        student_id = request.POST.get("student_id")
        students = None
        if student_id:
            students = [
                get_object_or_404(
                    selectors.class_roster(
                        school_class_id=homework.school_class_id,
                        stream_id=homework.stream_id,
                    ),
                    pk=student_id,
                )
            ]
        released = services.release_marks(
            homework=homework,
            actor=request.user,
            request=request,
            students=students,
        )
        if released:
            if students:
                messages.success(
                    request,
                    "Feedback released to this pupil and their parents.",
                )
            else:
                messages.success(
                    request,
                    f"Feedback released for {released} pupil"
                    f"{'' if released == 1 else 's'}.",
                )
        else:
            messages.info(
                request,
                "Nothing new to release — those marks were already visible.",
            )
        target = request.POST.get("next")
        if target and url_has_allowed_host_and_scheme(
            target, {request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(target)
        return redirect("homework:teacher_submissions", pk=homework.pk)

