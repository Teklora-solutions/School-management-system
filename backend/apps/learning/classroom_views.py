"""
apps/learning/views.py
----------------------
L0 — the classroom shell. Classrooms are computed from existing school data
(see selectors); no content is stored yet. Lessons, quizzes, labs and live
lessons plug into these classrooms in later checkpoints.

Security mirrors the homework module: the active student is resolved from the
session (never a posted id), another class's classroom is a 404 not a 403, and
a teacher may only open a classroom they are assigned to.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views import View
from django.views.generic import TemplateView

from apps.core.features import feature_disabled_message, feature_enabled
from apps.core.mixins import RoleRequiredMixin, TeacherRequiredMixin
from apps.homework.selectors import class_roster
from apps.students.models import Student

from . import selectors

logger = logging.getLogger(__name__)


class LearningEnabledMixin:
    """Show a friendly notice when the school has not switched Learning on."""

    def dispatch(self, request, *args, **kwargs):
        if not feature_enabled("learning", request=request):
            return render(
                request,
                "features/learning/disabled.html",
                {"disabled_message": feature_disabled_message("learning")},
            )
        return super().dispatch(request, *args, **kwargs)


class StudentOrParentMixin(RoleRequiredMixin):
    """Allow both student and parent roles (parent via the child switcher)."""

    allowed_roles = ["student", "parent"]


def _resolve_student(request) -> Student:
    """The student the current user may see: own profile, or the active child."""
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
            Student.objects.select_related(
                "user", "current_class", "current_stream"
            ),
            pk=student_id,
        )
    raise PermissionDenied("Your role cannot access learning.")


def _students_for_targeting(school_class) -> list:
    """Pupils of a class, for the assign-to-chosen-students pickers."""
    return list(
        Student.objects.filter(current_class=school_class)
        .select_related("user", "current_stream")
        .order_by("user__last_name", "user__first_name", "pk")
    )


def _get_term(request):
    from apps.academics.selectors import get_current_term
    from apps.academics.models import Term
    term_id = request.POST.get("term_id") or request.GET.get("term_id")
    if term_id:
        try:
            return Term.objects.get(pk=int(term_id))
        except (Term.DoesNotExist, ValueError):
            pass
    term = get_current_term()
    if term is None:
        raise Http404("No current term is configured.")
    return term


def _get_all_terms_for_class(school_class):
    """All terms for the class's academic year, ordered."""
    from apps.academics.models import Term
    return list(
        Term.objects.filter(academic_year=school_class.academic_year)
        .order_by("start_date")
    )


class TeacherClassroomsView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    """The teacher's classrooms, already populated with their enrolled pupils."""

    template_name = "features/learning/teacher_classrooms.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            selectors.classrooms_for_teacher(teacher_user_id=self.request.user.pk)
        )
        return ctx


class TeacherClassroomDetailView(
    TeacherRequiredMixin, LearningEnabledMixin, TemplateView
):
    """One classroom's roster and course outline, with term switching."""

    template_name = "features/learning/teacher_classroom_detail.html"

    def get_template_names(self):
        if self.request.GET.get("student_id"):
            return ["features/learning/classroom_detail.html"]
        return super().get_template_names()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        assignment = selectors.teacher_classroom(
            teacher_user_id=self.request.user.pk,
            class_id=self.kwargs["class_id"],
            subject_id=self.kwargs["subject_id"],
        )
        if assignment is None:
            raise Http404("Classroom not found.")
        roster = class_roster(
            school_class_id=assignment.school_class_id,
            stream_id=assignment.stream_id,
        )

        # Support term switching: ?term_id=X or fall back to current term
        term = _get_term(self.request)
        all_terms = _get_all_terms_for_class(assignment.school_class)

        from . import course_selectors
        if self.request.GET.get("student_id"):
            students = _students_for_targeting(assignment.school_class)
            student = next(
                (s for s in students if str(s.pk) == self.request.GET["student_id"]), None,
            )
            if student is None:
                raise Http404("Student not found in this class.")
            modules = course_selectors.course_for_student(
                school_class=assignment.school_class, subject=assignment.subject,
                term=term, student=student,
            )
            groups, untopic = course_selectors.topics_for_student(
                school_class=assignment.school_class, subject=assignment.subject,
                term=term, student=student, modules=modules,
            )
            total = sum(m.total_lessons for m in modules)
            done = sum(m.completed_lessons for m in modules)
            ctx.update({
                "classroom": selectors.Classroom(
                    school_class=assignment.school_class, subject=assignment.subject,
                    term=term, stream=assignment.stream,
                    teacher_name=assignment.teacher.user.full_name,
                    student_count=roster.count(),
                ),
                "student": student, "is_preview": True,
                "preview_students": students,
                "modules": modules, "total_lessons": total,
                "total_completed": done, "overall_pct": round(done * 100 / total) if total else 0,
                "topic_groups": [g for g in groups if g.lessons],
                "untopic_lessons": untopic,
                "quizzes_enabled": feature_enabled("quizzes", request=self.request),
            })
            return ctx
        modules = course_selectors.course_for_teacher(
            school_class=assignment.school_class,
            subject=assignment.subject,
            term=term,
        ) if term else []
        total_lessons = sum(m.total_lessons for m in modules)
        published_lessons = sum(m.published_lessons for m in modules)

        # Topics: each with every lesson grouped under it (drafts included),
        # ordered by topic_position — the teacher's organiser view.
        from .models import Topic as _Topic
        topics = list(
            _Topic.objects.filter(
                school_class=assignment.school_class,
                subject=assignment.subject,
                term=term,
            ).order_by("position", "id")
        ) if term else []
        lessons_by_topic = {t.pk: [] for t in topics}
        for mwl in modules:
            for lp in mwl.lessons:
                if lp.lesson.topic_id in lessons_by_topic:
                    lessons_by_topic[lp.lesson.topic_id].append(lp)
        topic_groups = [
            {
                "topic": t,
                "lessons": sorted(
                    lessons_by_topic[t.pk],
                    key=lambda lp: (lp.lesson.topic_position, lp.lesson.pk),
                ),
            }
            for t in topics
        ]
        from . import comment_services as _comment_services

        ctx.update(
            {
                "assignment": assignment,
                "classroom": selectors.Classroom(
                    school_class=assignment.school_class,
                    subject=assignment.subject,
                    term=term,
                    stream=assignment.stream,
                    teacher_name=assignment.teacher.user.full_name,
                    student_count=roster.count(),
                ),
                "roster": roster,
                "modules": modules,
                "total_lessons": total_lessons,
                "published_lessons": published_lessons,
                "all_terms": all_terms,
                "selected_term": term,
                "quizzes_enabled": feature_enabled("quizzes", request=self.request),
                "topic_groups": topic_groups,
                "lesson_comments_enabled": _comment_services.comments_enabled(
                    assignment.school_class
                ),
                "preview_students": _students_for_targeting(assignment.school_class),
            }
        )
        return ctx


class StudentClassroomsView(StudentOrParentMixin, LearningEnabledMixin, TemplateView):
    """The student's (or active child's) enrolled classrooms."""

    template_name = "features/learning/classrooms.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = _resolve_student(self.request)
        ctx.update(selectors.classrooms_for_student(student=student))
        ctx["is_parent"] = self.request.user.role == "parent"
        return ctx


class StudentClassroomDetailView(
    StudentOrParentMixin, LearningEnabledMixin, TemplateView
):
    """One classroom for the student/parent — course outline with completion."""

    template_name = "features/learning/classroom_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = _resolve_student(self.request)
        classroom = selectors.student_classroom(
            student=student,
            class_id=self.kwargs["class_id"],
            subject_id=self.kwargs["subject_id"],
        )
        if classroom is None:
            raise Http404("Classroom not found.")

        from . import course_selectors
        modules = []
        total_lessons = 0
        total_completed = 0
        topic_groups, untopic_lessons = [], []
        if classroom.term:
            modules = course_selectors.course_for_student(
                school_class=classroom.school_class,
                subject=classroom.subject,
                term=classroom.term,
                student=student,
            )
            total_lessons = sum(m.published_lessons for m in modules)
            total_completed = sum(m.completed_lessons for m in modules)
            topic_groups, untopic_lessons = course_selectors.topics_for_student(
                school_class=classroom.school_class,
                subject=classroom.subject,
                term=classroom.term,
                student=student,
                modules=modules,
            )
            # Topics with nothing released for this student are not shown.
            topic_groups = [g for g in topic_groups if g.lessons]

        overall_pct = (
            round(total_completed * 100 / total_lessons)
            if total_lessons > 0 else 0
        )

        next_lesson = next(
            (lp for lp in course_selectors.ordered_course_lessons(modules) if not lp.completed),
            None,
        )

        ctx.update(
            {
                "classroom": classroom,
                "student": student,
                "is_parent": self.request.user.role == "parent",
                "modules": modules,
                "total_lessons": total_lessons,
                "total_completed": total_completed,
                "overall_pct": overall_pct,
                "next_lesson": next_lesson,
                "quizzes_enabled": feature_enabled("quizzes", request=self.request),
                "topic_groups": topic_groups,
                "untopic_lessons": untopic_lessons,
            }
        )
        return ctx


# ---------------------------------------------------------------------------
# Engagement views — Piece 1 (progress bars and streaks)
# ---------------------------------------------------------------------------
# These views are NEW and additive.  Nothing above this line is touched.
# All gated behind the "engagement" feature flag.
# ---------------------------------------------------------------------------

from django.contrib import messages
from django.http import JsonResponse

from apps.core.mixins import AdminOrDirectorMixin, DirectorRequiredMixin

from . import engagement_selectors
from .engagement_services import record_lesson_completion
from .models import ClassEngagementSettings


class EngagementEnabledMixin:
    """Redirect with a notice when the engagement feature is off."""

    def dispatch(self, request, *args, **kwargs):
        if not feature_enabled("engagement", request=request):
            from django.shortcuts import redirect
            messages.info(
                request,
                "The progress and streak feature is not yet activated for this school.",
            )
            return redirect("accounts:dashboard_redirect")
        return super().dispatch(request, *args, **kwargs)


class StudentProgressView(
    StudentOrParentMixin, LearningEnabledMixin, EngagementEnabledMixin, TemplateView
):
    """
    The student's progress dashboard: overall bar, per-module bars, streak,
    next-up lesson, and an encouraging welcome-back message when returning
    after a gap.
    """

    template_name = "features/learning/student_progress.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = _resolve_student(self.request)
        from apps.academics.selectors import get_current_term
        term = get_current_term()
        progress = engagement_selectors.student_progress(student=student, term=term)
        ctx.update(
            {
                "progress": progress,
                "student": student,
                "term": term,
                "is_parent": self.request.user.role == "parent",
            }
        )
        return ctx


class TeacherClassProgressView(
    TeacherRequiredMixin, LearningEnabledMixin, EngagementEnabledMixin, TemplateView
):
    """
    Teacher view: per-student progress table for one classroom showing
    completion %, streak, who hasn't started, and who is behind.
    """

    template_name = "features/learning/teacher_class_progress.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.academics.selectors import get_current_term
        from . import selectors as learning_selectors

        assignment = learning_selectors.teacher_classroom(
            teacher_user_id=self.request.user.pk,
            class_id=self.kwargs["class_id"],
            subject_id=self.kwargs["subject_id"],
        )
        if assignment is None:
            raise Http404("Classroom not found.")

        term = get_current_term()
        overview = engagement_selectors.class_progress_overview(
            school_class=assignment.school_class,
            subject_id=assignment.subject_id,
            term=term,
        )
        ctx.update(
            {
                "overview": overview,
                "assignment": assignment,
                "term": term,
            }
        )
        return ctx


class DirectorEngagementOverviewView(
    DirectorRequiredMixin, LearningEnabledMixin, EngagementEnabledMixin, TemplateView
):
    """
    Director view: one row per class showing engagement on/off, student
    count, active learners, and average completion.
    """

    template_name = "features/learning/director_engagement_overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.academics.selectors import get_current_year
        year = get_current_year()
        rows = (
            engagement_selectors.director_engagement_overview(academic_year=year)
            if year
            else []
        )
        ctx.update({"rows": rows, "year": year})
        return ctx


class MarkLessonCompleteView(StudentOrParentMixin, LearningEnabledMixin, TemplateView):
    """
    POST-only view: mark a lesson complete and refresh the streak.
    Returns JSON so the button can update without a full page reload
    (graceful degradation: a normal POST also works via the redirect).
    """

    def post(self, request, *args, **kwargs):
        from .models import Lesson

        if request.user.role == "parent":
            raise PermissionDenied("Parents cannot mark lessons complete.")
        student = _resolve_student(request)
        lesson = course_selectors.lesson_detail_for_student(
            lesson_id=self.kwargs["lesson_id"], student=student,
        )
        if lesson is None:
            raise Http404

        record_lesson_completion(student=student, lesson=lesson)

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            try:
                streak = student.learning_streak
                streak_val = streak.current_streak
            except Exception:  # noqa: BLE001
                streak_val = 0
            return JsonResponse({"ok": True, "streak": streak_val})

        # Non-AJAX fallback: redirect back to the classroom
        from django.shortcuts import redirect
        return redirect(
            "learning:classroom",
            class_id=lesson.module.school_class_id,
            subject_id=lesson.module.subject_id,
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class ClassEngagementSettingsView(
    TeacherRequiredMixin, LearningEnabledMixin, EngagementEnabledMixin, TemplateView
):
    """
    Teacher (and admin) view to toggle engagement on/off for a class and
    adjust grace days.  Simple form — no DRF, no JS dependency.
    """

    template_name = "features/learning/class_engagement_settings.html"

    def _get_assignment_and_class(self, request):
        from . import selectors as learning_selectors
        assignment = learning_selectors.teacher_classroom(
            teacher_user_id=request.user.pk,
            class_id=self.kwargs["class_id"],
            subject_id=self.kwargs["subject_id"],
        )
        if assignment is None:
            raise Http404("Classroom not found.")
        return assignment

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        assignment = self._get_assignment_and_class(self.request)
        eng, _ = ClassEngagementSettings.objects.get_or_create(
            school_class=assignment.school_class,
            defaults={"is_active": False},
        )
        grace_choices = [
            (0, "0 — every school day (strict)"),
            (1, "1 day — recommended (covers sick days and power cuts)"),
            (2, "2 days — relaxed"),
            (3, "3 days — very relaxed"),
        ]
        ctx.update({"eng": eng, "assignment": assignment, "grace_choices": grace_choices})
        return ctx

    def post(self, request, *args, **kwargs):
        assignment = self._get_assignment_and_class(request)
        eng, _ = ClassEngagementSettings.objects.get_or_create(
            school_class=assignment.school_class,
            defaults={"is_active": False},
        )
        is_active = request.POST.get("is_active") == "1"
        show_progress_bars = request.POST.get("show_progress_bars") == "1"
        show_streak = request.POST.get("show_streak") == "1"
        try:
            grace = max(0, min(5, int(request.POST.get("streak_grace_days", 1))))
        except (ValueError, TypeError):
            grace = 1

        eng.is_active = is_active
        eng.show_progress_bars = show_progress_bars
        eng.show_streak = show_streak
        eng.streak_grace_days = grace
        eng.save(
            update_fields=[
                "is_active",
                "show_progress_bars",
                "show_streak",
                "streak_grace_days",
                "updated_at",
            ]
        )
        messages.success(
            request,
            f"Engagement settings updated for {assignment.school_class.name}.",
        )
        from django.shortcuts import redirect
        return redirect(
            "learning:teacher_classroom",
            class_id=self.kwargs["class_id"],
            subject_id=self.kwargs["subject_id"],
        )


# ---------------------------------------------------------------------------
# Class community feed views — Piece 2
# ---------------------------------------------------------------------------
# All additive.  Nothing above this line is touched.
# Gated behind "learning" (feed is part of the classroom, not a separate
# module switch — no new feature flag needed).
# ---------------------------------------------------------------------------

