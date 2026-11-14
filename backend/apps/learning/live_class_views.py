"""
apps/learning/live_class_views.py
---------------------------------
Views for live class session management, student join flow, and shared class calendar.

MRO rule (consistent with every other view in this app):
  Role mixin FIRST so wrong-role users get 403 before the feature-disabled
  page runs. This keeps the e2e security matrix clean.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.academics.models import SchoolClass, Subject
from apps.core.features import feature_disabled_message, feature_enabled
from apps.core.mixins import RoleRequiredMixin, TeacherRequiredMixin
from apps.learning import calendar_selectors, live_class_selectors, live_class_services
from apps.learning.live_class_forms import LiveClassRecordingForm, LiveClassSessionForm
from apps.learning.models import LiveClassSession
from apps.learning.views import LearningEnabledMixin, StudentOrParentMixin, _resolve_student


class LiveClassesEnabledMixin(LearningEnabledMixin):
    """Enforce both learning_enabled and live_classes_enabled switches."""

    def dispatch(self, request, *args, **kwargs):
        if not feature_enabled("live_classes", request=request):
            return render(
                request,
                "features/learning/disabled.html",
                {"disabled_message": feature_disabled_message("live_classes")},
            )
        return super().dispatch(request, *args, **kwargs)


# ── Teacher views ─────────────────────────────────────────────────────────


class TeacherLiveClassListView(TeacherRequiredMixin, LiveClassesEnabledMixin, View):
    template_name = "features/learning/live_sessions_list.html"

    def get(self, request, class_id: int, subject_id: int):
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        subject = get_object_or_404(Subject, pk=subject_id)
        if not live_class_services.teacher_can_access_classroom(request.user, school_class, subject):
            raise PermissionDenied("You are not assigned to this classroom.")
        sessions = live_class_selectors.get_live_sessions_for_classroom(class_id, subject_id)

        return render(
            request,
            self.template_name,
            {
                "school_class": school_class,
                "subject": subject,
                "sessions": sessions,
                "is_teacher": True,
            },
        )


class TeacherLiveClassCreateView(TeacherRequiredMixin, LiveClassesEnabledMixin, View):
    template_name = "features/learning/live_session_form.html"

    def get(self, request, class_id: int, subject_id: int):
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        subject = get_object_or_404(Subject, pk=subject_id)
        if not live_class_services.teacher_can_access_classroom(request.user, school_class, subject):
            raise PermissionDenied("You are not assigned to this classroom.")
        now = timezone.localtime()
        default_start = (now + timezone.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        form = LiveClassSessionForm(initial={"scheduled_start": default_start.strftime("%Y-%m-%dT%H:%M")})
        return render(
            request,
            self.template_name,
            {
                "school_class": school_class,
                "subject": subject,
                "form": form,
                "is_create": True,
            },
        )

    def post(self, request, class_id: int, subject_id: int):
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        subject = get_object_or_404(Subject, pk=subject_id)
        if not live_class_services.teacher_can_access_classroom(request.user, school_class, subject):
            raise PermissionDenied("You are not assigned to this classroom.")
        form = LiveClassSessionForm(request.POST)

        if form.is_valid():
            try:
                session = live_class_services.schedule_live_session(
                    school_class=school_class,
                    subject=subject,
                    teacher=request.user,
                    title=form.cleaned_data["title"],
                    scheduled_start=form.cleaned_data["scheduled_start"],
                    duration_minutes=form.cleaned_data["duration_minutes"],
                    platform=form.cleaned_data["platform"],
                    meeting_url=form.cleaned_data["meeting_url"],
                    meeting_id=form.cleaned_data["meeting_id"],
                    passcode=form.cleaned_data["passcode"],
                    description=form.cleaned_data["description"],
                    actor=request.user,
                    request=request,
                )
                messages.success(request, f"Live class '{session.title}' scheduled.")
                return redirect(
                    "learning:teacher_live_sessions",
                    class_id=class_id,
                    subject_id=subject_id,
                )
            except ValidationError as exc:
                form.add_error(None, exc)

        return render(
            request,
            self.template_name,
            {
                "school_class": school_class,
                "subject": subject,
                "form": form,
                "is_create": True,
            },
        )


class TeacherLiveClassEditView(TeacherRequiredMixin, LiveClassesEnabledMixin, View):
    template_name = "features/learning/live_session_form.html"

    def get(self, request, class_id: int, subject_id: int, session_id: int):
        session = live_class_selectors.get_live_session(session_id)
        if session.school_class_id != class_id or session.subject_id != subject_id:
            raise PermissionDenied("Session does not belong to this classroom.")
        if not live_class_services.teacher_can_access_classroom(
            request.user, session.school_class, session.subject
        ):
            raise PermissionDenied("You are not assigned to this classroom.")
        form = LiveClassSessionForm(instance=session)
        return render(
            request,
            self.template_name,
            {
                "school_class": session.school_class,
                "subject": session.subject,
                "session": session,
                "form": form,
                "is_create": False,
            },
        )

    def post(self, request, class_id: int, subject_id: int, session_id: int):
        session = live_class_selectors.get_live_session(session_id)
        if session.school_class_id != class_id or session.subject_id != subject_id:
            raise PermissionDenied("Session does not belong to this classroom.")
        if not live_class_services.teacher_can_access_classroom(
            request.user, session.school_class, session.subject
        ):
            raise PermissionDenied("You are not assigned to this classroom.")
        form = LiveClassSessionForm(request.POST, instance=session)
        if form.is_valid():
            try:
                live_class_services.update_live_session(
                    session=session,
                    title=form.cleaned_data["title"],
                    scheduled_start=form.cleaned_data["scheduled_start"],
                    duration_minutes=form.cleaned_data["duration_minutes"],
                    platform=form.cleaned_data["platform"],
                    meeting_url=form.cleaned_data["meeting_url"],
                    meeting_id=form.cleaned_data["meeting_id"],
                    passcode=form.cleaned_data["passcode"],
                    description=form.cleaned_data["description"],
                    actor=request.user,
                    request=request,
                )
                messages.success(request, f"Live class '{session.title}' updated.")
                return redirect(
                    "learning:teacher_live_sessions",
                    class_id=class_id,
                    subject_id=subject_id,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
        return render(
            request,
            self.template_name,
            {
                "school_class": session.school_class,
                "subject": session.subject,
                "session": session,
                "form": form,
                "is_create": False,
            },
        )


class TeacherLiveClassCancelView(TeacherRequiredMixin, LiveClassesEnabledMixin, View):
    def post(self, request, class_id: int, subject_id: int, session_id: int):
        session = live_class_selectors.get_live_session(session_id)
        if session.school_class_id != class_id or session.subject_id != subject_id:
            raise PermissionDenied("Session does not belong to this classroom.")
        if not live_class_services.teacher_can_access_classroom(
            request.user, session.school_class, session.subject
        ):
            raise PermissionDenied("You are not assigned to this classroom.")
        live_class_services.cancel_live_session(session=session, actor=request.user, request=request)
        messages.info(request, f"Session '{session.title}' was cancelled.")
        return redirect(
            "learning:teacher_live_sessions",
            class_id=class_id,
            subject_id=subject_id,
        )


class TeacherLiveClassActionView(TeacherRequiredMixin, LiveClassesEnabledMixin, View):
    def post(self, request, class_id: int, subject_id: int, session_id: int, action: str):
        session = live_class_selectors.get_live_session(session_id)
        if session.school_class_id != class_id or session.subject_id != subject_id:
            raise PermissionDenied("Session does not belong to this classroom.")
        if not live_class_services.teacher_can_access_classroom(
            request.user, session.school_class, session.subject
        ):
            raise PermissionDenied("You are not assigned to this classroom.")
        if action == "start":
            live_class_services.start_live_session(session=session, actor=request.user, request=request)
            messages.success(request, f"Session '{session.title}' is now LIVE.")
        elif action == "end":
            live_class_services.end_live_session(session=session, actor=request.user, request=request)
            messages.info(request, f"Session '{session.title}' marked as completed.")
        else:
            raise PermissionDenied("Unknown action.")
        return redirect("learning:live_session_detail", session_id=session.pk)


class TeacherLiveClassRecordingView(TeacherRequiredMixin, LiveClassesEnabledMixin, View):
    """Attach a recording link to a finished (or running) session."""

    template_name = "features/learning/live_recording_form.html"

    def _get_session(self, request, class_id, subject_id, session_id):
        session = live_class_selectors.get_live_session(session_id)
        if session.school_class_id != class_id or session.subject_id != subject_id:
            raise PermissionDenied("Session does not belong to this classroom.")
        if not live_class_services.teacher_can_access_classroom(
            request.user, session.school_class, session.subject
        ):
            raise PermissionDenied("You are not assigned to this classroom.")
        return session

    def get(self, request, class_id: int, subject_id: int, session_id: int):
        session = self._get_session(request, class_id, subject_id, session_id)
        form = LiveClassRecordingForm(
            instance=session,
            school_class=session.school_class,
            subject=session.subject,
        )
        return render(
            request,
            self.template_name,
            {"session": session, "form": form},
        )

    def post(self, request, class_id: int, subject_id: int, session_id: int):
        session = self._get_session(request, class_id, subject_id, session_id)
        form = LiveClassRecordingForm(
            request.POST,
            instance=session,
            school_class=session.school_class,
            subject=session.subject,
        )
        if form.is_valid():
            try:
                live_class_services.attach_recording(
                    session=session,
                    recording_url=form.cleaned_data["recording_url"],
                    lesson=form.cleaned_data["recording_lesson"],
                    notice_given=form.cleaned_data["notice_given"],
                    actor=request.user,
                    request=request,
                )
                messages.success(
                    request,
                    "Recording link saved. The class can now watch it"
                    + (
                        f", and it also appears on '{session.recording_lesson.title}'."
                        if session.recording_lesson_id
                        else "."
                    ),
                )
                return redirect("learning:live_session_detail", session_id=session.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
        return render(
            request,
            self.template_name,
            {"session": session, "form": form},
        )


class TeacherLiveClassRecordingRemoveView(TeacherRequiredMixin, LiveClassesEnabledMixin, View):
    """Remove a session's recording link."""

    def post(self, request, class_id: int, subject_id: int, session_id: int):
        session = live_class_selectors.get_live_session(session_id)
        if session.school_class_id != class_id or session.subject_id != subject_id:
            raise PermissionDenied("Session does not belong to this classroom.")
        if not live_class_services.teacher_can_access_classroom(
            request.user, session.school_class, session.subject
        ):
            raise PermissionDenied("You are not assigned to this classroom.")
        live_class_services.remove_recording(
            session=session, actor=request.user, request=request
        )
        messages.info(request, "Recording link removed.")
        return redirect("learning:live_session_detail", session_id=session.pk)


# ── Shared detail view ────────────────────────────────────────────────────


class LiveClassDetailView(RoleRequiredMixin, LiveClassesEnabledMixin, View):
    """Session detail, join link, and attendee list."""

    allowed_roles = ["teacher", "admin", "director", "student", "parent"]
    template_name = "features/learning/live_session_detail.html"

    def get(self, request, session_id: int):
        session = live_class_selectors.get_live_session(session_id)
        if not live_class_services.can_view_session_detail(request.user, session):
            raise PermissionDenied("You are not allowed to view this live class.")
        is_teacher = request.user.role in ("teacher", "admin", "director")
        attendances = live_class_selectors.get_session_attendances(session_id) if is_teacher else []
        has_joined = False
        if request.user.role == "student":
            student = _resolve_student(request)
            has_joined = live_class_selectors.has_student_joined(session, student)
        recording_visible = bool(session.recording_url) and (
            is_teacher
            or session.status != LiveClassSession.Status.CANCELLED
        )
        can_add_recording = (
            is_teacher
            and not session.recording_url
            and session.status != LiveClassSession.Status.CANCELLED
            and timezone.now() >= session.scheduled_start
        )
        return render(
            request,
            self.template_name,
            {
                "session": session,
                "attendances": attendances,
                "is_teacher": is_teacher,
                "has_joined": has_joined,
                "recording_visible": recording_visible,
                "can_add_recording": can_add_recording,
            },
        )


# ── Student views ─────────────────────────────────────────────────────────


class StudentLiveClassListView(StudentOrParentMixin, LiveClassesEnabledMixin, View):
    template_name = "features/learning/live_sessions_list.html"

    def get(self, request, class_id: int, subject_id: int):
        student = _resolve_student(request)
        if student.current_class_id != class_id:
            raise PermissionDenied("You are not enrolled in this class.")
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        subject = get_object_or_404(Subject, pk=subject_id)
        sessions = live_class_selectors.get_live_sessions_for_classroom(
            class_id, subject_id, include_cancelled=False
        )
        return render(
            request,
            self.template_name,
            {
                "school_class": school_class,
                "subject": subject,
                "sessions": sessions,
                "is_teacher": False,
                "student": student,
            },
        )


class StudentJoinLiveClassView(RoleRequiredMixin, LiveClassesEnabledMixin, View):
    """Endpoint when student clicks 'Join Live Class'."""

    allowed_roles = ["student"]

    def get(self, request, session_id: int):
        student = _resolve_student(request)
        session = live_class_selectors.get_live_session(session_id)
        if student.current_class_id != session.school_class_id:
            raise PermissionDenied("You are not enrolled in this class.")
        if not session.join_window_open:
            messages.warning(
                request,
                f"The meeting link for '{session.title}' is not active right now. "
                "You can join starting 15 minutes before scheduled start.",
            )
            return redirect("learning:live_session_detail", session_id=session.pk)
        live_class_services.record_student_join(session=session, student=student, request=request)
        return redirect(session.meeting_url)


# ── Calendar view ─────────────────────────────────────────────────────────


class ClassCalendarView(RoleRequiredMixin, LiveClassesEnabledMixin, View):
    """Shared class calendar aggregating Live sessions, Homework, Quizzes, and Terms."""

    allowed_roles = ["teacher", "student", "parent", "admin", "director"]
    template_name = "features/learning/class_calendar.html"

    def get(self, request, class_id: int):
        school_class = get_object_or_404(SchoolClass, pk=class_id)

        # Object-level access control
        if request.user.role == "student":
            student = _resolve_student(request)
            if student.current_class_id != class_id:
                raise PermissionDenied("You are not enrolled in this class.")
        elif request.user.role == "parent":
            student = _resolve_student(request)
            if student.current_class_id != class_id:
                raise PermissionDenied("Your child is not enrolled in this class.")
        elif request.user.role == "teacher":
            if not live_class_services.teacher_can_access_classroom(request.user, school_class):
                raise PermissionDenied("You are not assigned to this class.")

        year = request.GET.get("year")
        month = request.GET.get("month")
        year_int = int(year) if year and year.isdigit() else None
        month_int = int(month) if month and month.isdigit() else None

        calendar_data = calendar_selectors.get_class_calendar_data(
            school_class_id=class_id,
            year=year_int,
            month=month_int,
        )

        return render(
            request,
            self.template_name,
            {
                "school_class": school_class,
                **calendar_data,
            },
        )
