"""
apps/homework/views.py
-----------------------
Student (and parent-via-child-switcher) homework views.

Security: every view resolves the active student from the session
(set by the child-switcher for parents) and never accepts a student_id
from query params — preventing horizontal privilege escalation.
"""

from __future__ import annotations

import mimetypes
from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.academics.selectors import get_current_term
from apps.core.mixins import RoleRequiredMixin

from . import selectors, services
from .models import (
    MAX_SUBMISSION_ATTACHMENTS,
    Homework,
    HomeworkAttachment,
    HomeworkSubmissionAttachment,
)

ACCEPTED_FILE_TYPES = ".jpg, .jpeg, .png, .webp, .gif, .pdf, .doc, .docx"


class StudentOrParentMixin(RoleRequiredMixin):
    """Allow both student and parent roles."""

    allowed_roles = ["student", "parent"]


class HomeworkFileMixin(RoleRequiredMixin):
    """Files may be opened by the family roles and by the setting teacher."""

    allowed_roles = ["student", "parent", "teacher"]


def _resolve_student(request):
    """
    Return the Student instance the current user should see homework for.
    - Students: their own student_profile.
    - Parents: the child stored in session (set by the child-switcher).
    Raises PermissionDenied if no valid student can be resolved.
    """
    from apps.students.models import Student

    if request.user.role == "student":
        if not hasattr(request.user, "student_profile"):
            raise PermissionDenied("No student profile linked to this account.")
        return request.user.student_profile

    if request.user.role == "parent":
        if not hasattr(request.user, "parent_profile"):
            raise PermissionDenied("No parent profile linked to this account.")
        selected_id = request.session.get("selected_child_id")
        child_ids = list(
            request.user.parent_profile.children_links.values_list(
                "student_id", flat=True
            )
        )
        if not child_ids:
            raise PermissionDenied("No children are linked to your account.")
        # Default to primary child if none selected
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
    raise PermissionDenied("Your role cannot access homework.")


def homework_detail_context(homework: Homework, *, preview: bool = False) -> dict:
    """Shared context for the student detail page and the teacher preview."""
    attachments = list(homework.attachments.all())
    return {
        "homework": homework,
        "attachments": attachments,
        "images": [a for a in attachments if a.is_image],
        "pdfs": [a for a in attachments if a.is_pdf],
        "documents": [
            a for a in attachments if not a.is_image and not a.is_pdf
        ],
        "preview": preview,
        "today": date.today(),
        "is_overdue": homework.due_date < date.today(),
        "is_new": bool(
            homework.published_at
            and homework.published_at >= timezone.now() - timedelta(days=7)
        ),
    }


def submission_context(request, homework: Homework, student) -> dict:
    """
    Everything the detail page needs to show (and, for the student, edit) the
    submission. Parents see the status and files read-only.
    """
    submission = selectors.submission_for(
        homework_id=homework.pk, student_id=student.pk
    )
    today = date.today()
    is_student = getattr(request.user, "role", "") == "student"
    locked = bool(
        submission
        and (submission.is_late or today > homework.due_date)
    )
    submission_attachments = (
        list(submission.attachments.all()) if submission is not None else []
    )
    if submission is None:
        state = "overdue" if today > homework.due_date else "missing"
    else:
        state = "late" if submission.is_late else "submitted"
    return {
        "student": student,
        "submission": submission,
        "submission_attachments": submission_attachments,
        "submission_state": state,
        "submission_locked": locked,
        "can_submit": is_student,
        "show_submission_form": is_student and not locked,
        "late_warning": submission is None and today > homework.due_date,
        "accepted_types": ACCEPTED_FILE_TYPES,
        "max_submission_files": MAX_SUBMISSION_ATTACHMENTS,
    }



class HomeworkListView(StudentOrParentMixin, TemplateView):
    template_name = "features/homework/homework_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = _resolve_student(self.request)
        current_term = get_current_term()

        term_id_param = self.request.GET.get("term")
        subject_id_param = self.request.GET.get("subject")

        # Use requested term or fall back to current
        term_id = (
            int(term_id_param)
            if term_id_param
            else (current_term.pk if current_term else None)
        )

        hw_qs = (
            selectors.homework_for_student(
                school_class_id=student.current_class_id,
                stream_id=student.current_stream_id,
                term_id=term_id,
                subject_id=int(subject_id_param) if subject_id_param else None,
            )
            if student.current_class_id
            else Homework.objects.none()
        )

        # Annotate overdue
        today = date.today()
        homework = selectors.mark_new(list(hw_qs))
        for hw in homework:
            hw.is_overdue = hw.due_date < today

        # Build term list for filter dropdown
        from apps.academics.models import Term

        terms = Term.objects.select_related("academic_year").order_by(
            "-academic_year__start_date", "name"
        )

        # Subjects the student currently has (for filter dropdown)
        from apps.academics.models import ClassSubject

        subjects = []
        if student.current_class_id:
            subjects = list(
                ClassSubject.objects.filter(
                    school_class_id=student.current_class_id,
                )
                .select_related("subject")
                .values_list("subject__id", "subject__name")
                .distinct()
            )

        ctx.update(
            {
                "student": student,
                "homework_list": homework,
                "today": today,
                "current_term": current_term,
                "selected_term_id": term_id,
                "terms": terms,
                "subjects": subjects,
                "selected_subject_id": (
                    int(subject_id_param) if subject_id_param else None
                ),
            }
        )
        return ctx


class HomeworkDetailView(StudentOrParentMixin, TemplateView):
    """One published homework, with image / PDF / Word attachments."""

    template_name = "features/homework/homework_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        homework = get_object_or_404(
            Homework.objects.select_related(
                "subject", "term", "school_class", "stream", "set_by"
            ).prefetch_related("attachments"),
            pk=self.kwargs["pk"],
            status=Homework.Status.PUBLISHED,
        )
        student = _resolve_student(self.request)

        # Another class's homework is a 404, not a 403: it does not exist for
        # this student.
        if student.current_class_id != homework.school_class_id:
            raise Http404("Homework not found.")
        if homework.stream_id and homework.stream_id != student.current_stream_id:
            raise Http404("Homework not found.")

        ctx.update(homework_detail_context(homework))
        ctx.update(submission_context(self.request, homework, student))
        return ctx


class HomeworkSubmitView(StudentOrParentMixin, View):
    """POST only: the signed-in student hands in (or replaces) their work."""

    def post(self, request, pk, *args, **kwargs):
        if request.user.role != "student":
            raise PermissionDenied("Only the student can submit homework.")

        homework = get_object_or_404(
            Homework.objects.select_related("school_class"),
            pk=pk,
            status=Homework.Status.PUBLISHED,
        )
        student = _resolve_student(request)
        if student.current_class_id != homework.school_class_id:
            raise Http404("Homework not found.")
        if homework.stream_id and homework.stream_id != student.current_stream_id:
            raise Http404("Homework not found.")

        existing = selectors.submission_for(
            homework_id=homework.pk, student_id=student.pk
        )
        owned_ids = (
            set(existing.attachments.values_list("pk", flat=True))
            if existing is not None
            else set()
        )
        remove_ids = [
            int(value)
            for value in request.POST.getlist("remove_attachment")
            if value.isdigit() and int(value) in owned_ids
        ]

        try:
            submission = services.submit_homework(
                homework=homework,
                student=student,
                note=request.POST.get("note", ""),
                files=request.FILES.getlist("files"),
                remove_ids=remove_ids,
                actor=request.user,
                request=request,
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            if submission.is_late:
                messages.warning(
                    request,
                    "Submitted — the due date had passed, so it is marked late.",
                )
            elif existing is not None:
                messages.success(request, "Your submission was updated.")
            else:
                messages.success(request, "Homework submitted.")
        return redirect("homework:detail", pk=homework.pk)


class HomeworkSubmissionFileView(View):
    """
    Stream one file of a student's submission.

    The submitting student, their parents (for the active child) and the
    teacher whose assignment this homework belongs to may open it. Files are
    never linked to MEDIA_URL.
    """

    def get(self, request, pk, submission_id, attachment_id, *args, **kwargs):
        attachment = get_object_or_404(
            HomeworkSubmissionAttachment.objects.select_related(
                "submission__homework__school_class",
                "submission__student",
            ),
            pk=attachment_id,
            submission_id=submission_id,
            submission__homework_id=pk,
        )
        submission = attachment.submission
        homework = submission.homework
        role = getattr(request.user, "role", "")

        if role == "teacher":
            in_scope = selectors.homework_for_teacher(
                teacher_user_id=request.user.pk
            ).filter(pk=homework.pk)
            if not in_scope.exists():
                raise PermissionDenied(
                    "You do not have access to this submission file."
                )
        elif role in ("student", "parent"):
            if homework.status != Homework.Status.PUBLISHED:
                raise Http404("Submission file not found.")
            student = _resolve_student(request)
            if submission.student_id != student.pk:
                raise PermissionDenied(
                    "You do not have access to this submission file."
                )
        else:
            raise PermissionDenied("Your role cannot open submission files.")

        content_type = (
            attachment.content_type
            or mimetypes.guess_type(attachment.file.name)[0]
            or "application/octet-stream"
        )
        inline_ok = content_type == "application/pdf" or content_type.startswith(
            "image/"
        )
        as_attachment = request.GET.get("download") == "1" or not inline_ok

        try:
            file_handle = attachment.file.open("rb")
        except FileNotFoundError as exc:
            raise Http404("The file is missing from storage.") from exc

        response = FileResponse(
            file_handle,
            as_attachment=as_attachment,
            filename=attachment.original_name
            or attachment.file.name.split("/")[-1],
            content_type=content_type,
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response



class HomeworkAttachmentFileView(View):
    """
    Stream one attachment of a homework.

    Students and parents may open files of published homework for the
    student's own class only. Teachers may open files of any homework inside
    their own assignments — including drafts, so they can check a file before
    publishing. Files are never linked to MEDIA_URL.
    """

    def get(self, request, pk, attachment_id, *args, **kwargs):
        attachment = get_object_or_404(
            HomeworkAttachment.objects.select_related(
                "homework__school_class", "homework__stream"
            ),
            pk=attachment_id,
            homework_id=pk,
        )
        homework = attachment.homework
        role = getattr(request.user, "role", "")

        if role == "teacher":
            in_scope = selectors.homework_for_teacher(
                teacher_user_id=request.user.pk
            ).filter(pk=homework.pk)
            if not in_scope.exists():
                raise PermissionDenied(
                    "You do not have access to this homework file."
                )
        elif role in ("student", "parent"):
            if homework.status != Homework.Status.PUBLISHED:
                raise Http404("Homework file not found.")
            student = _resolve_student(request)
            if student.current_class_id != homework.school_class_id:
                raise PermissionDenied(
                    "You do not have access to this homework file."
                )
            if (
                homework.stream_id
                and homework.stream_id != student.current_stream_id
            ):
                raise PermissionDenied(
                    "You do not have access to this homework file."
                )
        else:
            raise PermissionDenied("Your role cannot open homework files.")

        content_type = (
            attachment.content_type
            or mimetypes.guess_type(attachment.file.name)[0]
            or "application/octet-stream"
        )
        inline_ok = content_type == "application/pdf" or content_type.startswith(
            "image/"
        )
        as_attachment = request.GET.get("download") == "1" or not inline_ok

        try:
            file_handle = attachment.file.open("rb")
        except FileNotFoundError as exc:
            raise Http404("The file is missing from storage.") from exc

        response = FileResponse(
            file_handle,
            as_attachment=as_attachment,
            filename=attachment.original_name
            or attachment.file.name.split("/")[-1],
            content_type=content_type,
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response


class HomeworkAttachmentDownloadView(HomeworkFileMixin, View):
    """
    Stream the legacy single attachment. Students and parents are checked
    against the homework's class; a teacher may download it for homework in
    their own assignments (so the preview works). Never link the raw media URL.
    """

    def get(self, request, pk, *args, **kwargs):
        hw = get_object_or_404(
            Homework.objects.select_related("school_class", "stream"),
            pk=pk,
        )

        if request.user.role == "teacher":
            in_scope = selectors.homework_for_teacher(
                teacher_user_id=request.user.pk
            ).filter(pk=hw.pk)
            if not in_scope.exists():
                raise PermissionDenied(
                    "You do not have access to this homework attachment."
                )
        else:
            if hw.status != Homework.Status.PUBLISHED:
                raise Http404("No attached file for this homework.")
            student = _resolve_student(request)

            # Verify student belongs to the homework's class
            if student.current_class_id != hw.school_class_id:
                raise PermissionDenied(
                    "You do not have access to this homework attachment."
                )
            if hw.stream_id and hw.stream_id != student.current_stream_id:
                raise PermissionDenied(
                    "You do not have access to this homework attachment."
                )

        if not hw.attachment:
            raise Http404("No attachment for this homework.")

        return FileResponse(
            hw.attachment.open("rb"),
            as_attachment=True,
            filename=hw.attachment.name.split("/")[-1],
        )
