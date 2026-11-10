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

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.generic import TemplateView

from apps.core.features import feature_disabled_message, feature_enabled
from apps.core.mixins import RoleRequiredMixin, TeacherRequiredMixin
from apps.homework.selectors import class_roster
from apps.students.models import Student

from . import selectors


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
        modules = course_selectors.course_for_teacher(
            school_class=assignment.school_class,
            subject=assignment.subject,
            term=term,
        ) if term else []
        total_lessons = sum(m.total_lessons for m in modules)
        published_lessons = sum(m.published_lessons for m in modules)

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
        if classroom.term:
            modules = course_selectors.course_for_student(
                school_class=classroom.school_class,
                subject=classroom.subject,
                term=classroom.term,
                student=student,
            )
            total_lessons = sum(m.published_lessons for m in modules)
            total_completed = sum(m.completed_lessons for m in modules)

        overall_pct = (
            round(total_completed * 100 / total_lessons)
            if total_lessons > 0 else 0
        )

        # Find the next uncompleted lesson for the Continue button
        next_lesson = None
        for mwl in modules:
            for lp in mwl.lessons:
                if not lp.completed:
                    next_lesson = lp
                    break
            if next_lesson:
                break

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

        student = _resolve_student(request)
        lesson = get_object_or_404(Lesson, pk=self.kwargs["lesson_id"], is_published=True)

        # Guard: lesson must belong to the student's class
        if lesson.module.school_class_id != student.current_class_id:
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

from . import feed_selectors, feed_services
from .models import ClassPost, ClassPostReaction


class _FeedClassMixin:
    """
    Shared helper: resolve the SchoolClass for feed views and guard access.

    URL kwargs: class_id (int)
    Sets self.school_class on the instance.
    """

    def _resolve_class(self, request, class_id: int):
        from apps.academics.models import SchoolClass
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        if not feed_selectors.can_view_feed(request.user, school_class):
            raise Http404  # 404, not 403 — don't reveal the class exists
        return school_class


class ClassFeedView(_FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView):
    """
    Main class feed page.  Shows all posts for a class.
    Teachers see removed posts (greyed-out). Students and parents do not.
    Contains the new-post form inline (POST handled here too).
    """

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]
    template_name = "features/learning/class_feed.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        school_class = self._resolve_class(self.request, self.kwargs["class_id"])
        posts = feed_selectors.feed_for_classroom(
            school_class=school_class, viewer=self.request.user
        )
        ctx.update(
            {
                "school_class": school_class,
                "posts": posts,
                "can_post": feed_selectors.can_post(self.request.user, school_class),
                "is_moderator": feed_selectors.is_moderator(self.request.user),
                "post_types": ClassPost.PostType.choices,
                "student_allowed_types": list(ClassPost.STUDENT_ALLOWED_TYPES),
                "emoji_choices": ClassPostReaction.Emoji.choices,
                "is_teacher": self.request.user.role in ("teacher", "admin", "director"),
                "is_parent": self.request.user.role == "parent",
            }
        )
        return ctx

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect
        school_class = self._resolve_class(request, self.kwargs["class_id"])

        body = request.POST.get("body", "").strip()
        post_type = request.POST.get("post_type", ClassPost.PostType.ANNOUNCEMENT)

        if not body:
            messages.error(request, "Post body cannot be empty.")
            return redirect("learning:class_feed", class_id=school_class.pk)

        # Coerce student to QUESTION regardless of what was submitted
        if request.user.role == "student":
            post_type = ClassPost.PostType.QUESTION

        try:
            feed_services.create_post(
                author=request.user,
                school_class=school_class,
                body=body,
                post_type=post_type,
            )
            messages.success(request, "Your post has been added to the class feed.")
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        except ValueError as exc:
            messages.error(request, str(exc))

        return redirect("learning:class_feed", class_id=school_class.pk)


class ClassFeedPostActionView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """
    POST-only view for moderation actions on a single post:
      action=remove   → soft-delete
      action=restore  → un-remove
      action=pin      → toggle pin
      action=react    → add/toggle reaction (emoji in POST data)

    Returns a redirect back to the feed.  The reaction action also accepts
    X-Requested-With: XMLHttpRequest for a lightweight JSON response so the
    reaction buttons can update in-place without a full reload.
    """

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect
        from django.http import JsonResponse

        school_class = self._resolve_class(request, self.kwargs["class_id"])
        post = get_object_or_404(
            ClassPost, pk=self.kwargs["post_id"], school_class=school_class
        )
        action = request.POST.get("action", "")
        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        try:
            if action == "remove":
                feed_services.remove_post(post=post, removed_by=request.user)
                messages.success(request, "Post removed.")

            elif action == "restore":
                feed_services.restore_post(post=post, restored_by=request.user)
                messages.success(request, "Post restored.")

            elif action == "pin":
                feed_services.toggle_pin(post=post, actor=request.user)
                label = "pinned" if post.is_pinned else "unpinned"
                messages.success(request, f"Post {label}.")

            elif action == "react":
                emoji = request.POST.get("emoji") or None
                reaction = feed_services.set_reaction(
                    post=post, user=request.user, emoji=emoji
                )
                if is_xhr:
                    from apps.learning.feed_selectors import reaction_summary
                    counts = reaction_summary(post=post)
                    return JsonResponse(
                        {
                            "ok": True,
                            "counts": counts,
                            "viewer_emoji": reaction.emoji if reaction else None,
                        }
                    )
            else:
                messages.error(request, "Unknown action.")

        except PermissionDenied as exc:
            if is_xhr:
                return JsonResponse({"ok": False, "error": str(exc)}, status=403)
            messages.error(request, str(exc))

        return redirect("learning:class_feed", class_id=school_class.pk)

    def get(self, request, *args, **kwargs):
        raise Http404


# ---------------------------------------------------------------------------
# Points, levels and badges views — Piece 3
# ---------------------------------------------------------------------------
# All additive.  Nothing above this line is touched.
# Gated behind "engagement" feature flag.
# ---------------------------------------------------------------------------

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

import mimetypes as _mimetypes

from django.core.exceptions import ValidationError as _ValidationError
from django.http import FileResponse as _FileResponse

from . import course_selectors, course_services
from .models import (
    CourseModule, Lesson, LessonAttachment,
    LESSON_ATTACHMENT_ALLOWED_EXTENSIONS,
)


def _get_teacher_assignment(request, class_id: int, subject_id: int):
    """Return assignment or raise Http404. Admins/directors bypass the check."""
    role = getattr(request.user, "role", "")
    if role in ("admin", "director"):
        from apps.academics.models import SchoolClass, Subject
        sc = get_object_or_404(SchoolClass, pk=class_id)
        subj = get_object_or_404(Subject, pk=subject_id)
        return sc, subj

    assignment = selectors.teacher_classroom(
        teacher_user_id=request.user.pk,
        class_id=class_id,
        subject_id=subject_id,
    )
    if assignment is None:
        raise Http404("Classroom not found.")
    return assignment.school_class, assignment.subject


def _get_term(request):
    from apps.academics.selectors import get_current_term
    from apps.academics.models import Term
    # Allow explicit term selection via GET or POST
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


# ── Module views ─────────────────────────────────────────────────────────────

class ModuleCreateView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    template_name = "features/learning/module_form.html"

    def get(self, request, class_id, subject_id, *args, **kwargs):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        term = _get_term(request)
        all_terms = _get_all_terms_for_class(sc)
        return self.render_to_response({
            "school_class": sc, "subject": subj,
            "term": term, "all_terms": all_terms,
            "module": None, "action": "Add",
        })

    def post(self, request, class_id, subject_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        term = _get_term(request)
        title = request.POST.get("title", "").strip()
        try:
            course_services.create_module(
                school_class=sc, subject=subj, term=term,
                title=title, actor=request.user,
            )
            messages.success(request, f'Module "{title}" added.')
        except _ValidationError as exc:
            messages.error(request, exc.message)
        return redirect(
            "learning:teacher_classroom",
            class_id=sc.pk, subject_id=subj.pk
        )


class ModuleEditView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    template_name = "features/learning/module_form.html"

    def _get_module(self, request, module_id, class_id, subject_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        return sc, subj, module

    def get(self, request, class_id, subject_id, module_id, *args, **kwargs):
        sc, subj, module = self._get_module(request, module_id, class_id, subject_id)
        term = _get_term(request)
        return self.render_to_response(
            {"school_class": sc, "subject": subj, "term": term,
             "module": module, "action": "Edit"}
        )

    def post(self, request, class_id, subject_id, module_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module = self._get_module(request, module_id, class_id, subject_id)
        title = request.POST.get("title", "").strip()
        is_published = request.POST.get("is_published") == "1"
        try:
            course_services.update_module(
                module=module, title=title,
                is_published=is_published, actor=request.user,
            )
            messages.success(request, "Module updated.")
        except _ValidationError as exc:
            messages.error(request, exc.message)
        return redirect("learning:teacher_classroom", class_id=sc.pk, subject_id=subj.pk)


class ModuleDeleteView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    """POST-only confirmation delete."""
    template_name = "features/learning/module_delete_confirm.html"

    def _get(self, request, class_id, subject_id, module_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        return sc, subj, module

    def get(self, request, class_id, subject_id, module_id, *args, **kwargs):
        sc, subj, module = self._get(request, class_id, subject_id, module_id)
        return self.render_to_response(
            {"school_class": sc, "subject": subj, "module": module}
        )

    def post(self, request, class_id, subject_id, module_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module = self._get(request, class_id, subject_id, module_id)
        lesson_count = module.lessons.count()
        course_services.delete_module(module=module, actor=request.user)
        messages.success(
            request,
            f'Module deleted along with its {lesson_count} lesson(s).'
        )
        return redirect("learning:teacher_classroom", class_id=sc.pk, subject_id=subj.pk)


class ModuleMoveView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    """POST-only: move a module up or down."""
    def post(self, request, class_id, subject_id, module_id, direction, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        if direction not in ("up", "down"):
            raise Http404
        course_services.move_module(module=module, direction=direction, actor=request.user)
        return redirect("learning:teacher_classroom", class_id=sc.pk, subject_id=subj.pk)

    def get(self, request, *args, **kwargs):
        raise Http404


# ── Lesson views ─────────────────────────────────────────────────────────────

class LessonCreateView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    template_name = "features/learning/lesson_form.html"

    def _get_module(self, request, class_id, subject_id, module_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        return sc, subj, module

    def get(self, request, class_id, subject_id, module_id, *args, **kwargs):
        sc, subj, module = self._get_module(request, class_id, subject_id, module_id)
        return self.render_to_response(
            {"school_class": sc, "subject": subj, "module": module,
             "lesson": None, "action": "Add",
             "allowed_ext": ", ".join(sorted(LESSON_ATTACHMENT_ALLOWED_EXTENSIONS))}
        )

    def post(self, request, class_id, subject_id, module_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module = self._get_module(request, class_id, subject_id, module_id)
        try:
            lesson = course_services.create_lesson(
                module=module,
                title=request.POST.get("title", ""),
                notes=request.POST.get("notes", ""),
                notes_format=request.POST.get("notes_format", "plain"),
                video_url=request.POST.get("video_url", ""),
                is_published=request.POST.get("is_published") == "1",
                actor=request.user,
            )
            # Handle file uploads
            errors = []
            for f in request.FILES.getlist("attachments"):
                try:
                    course_services.add_attachment(
                        lesson=lesson, uploaded_file=f, actor=request.user
                    )
                except _ValidationError as exc:
                    errors.append(exc.message)
            if errors:
                messages.warning(request, " ".join(errors))
            messages.success(request, f'Lesson "{lesson.title}" added.')
            return redirect(
                "learning:lesson_edit",
                class_id=sc.pk, subject_id=subj.pk, module_id=module.pk,
                lesson_id=lesson.pk,
            )
        except _ValidationError as exc:
            messages.error(request, exc.message)
            return redirect(
                "learning:teacher_classroom",
                class_id=sc.pk, subject_id=subj.pk
            )


class LessonEditView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    template_name = "features/learning/lesson_form.html"

    def _get(self, request, class_id, subject_id, module_id, lesson_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        attachments = list(lesson.lesson_attachments.all())
        return sc, subj, module, lesson, attachments

    def get(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        sc, subj, module, lesson, attachments = self._get(
            request, class_id, subject_id, module_id, lesson_id
        )
        return self.render_to_response(
            {"school_class": sc, "subject": subj, "module": module,
             "lesson": lesson, "attachments": attachments,
             "action": "Edit",
             "allowed_ext": ", ".join(sorted(LESSON_ATTACHMENT_ALLOWED_EXTENSIONS))}
        )

    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson, _ = self._get(
            request, class_id, subject_id, module_id, lesson_id
        )
        try:
            course_services.update_lesson(
                lesson=lesson,
                title=request.POST.get("title", ""),
                notes=request.POST.get("notes", ""),
                notes_format=request.POST.get("notes_format", "plain"),
                video_url=request.POST.get("video_url", ""),
                is_published=request.POST.get("is_published") == "1",
                actor=request.user,
            )
            # New file uploads
            upload_errors = []
            for f in request.FILES.getlist("attachments"):
                try:
                    course_services.add_attachment(
                        lesson=lesson, uploaded_file=f, actor=request.user
                    )
                except _ValidationError as exc:
                    upload_errors.append(exc.message)
            if upload_errors:
                messages.warning(request, " ".join(upload_errors))
            else:
                messages.success(request, "Lesson saved.")
        except _ValidationError as exc:
            messages.error(request, exc.message)
        return redirect(
            "learning:lesson_edit",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=module.pk, lesson_id=lesson.pk,
        )


class LessonDeleteView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    template_name = "features/learning/lesson_delete_confirm.html"

    def _get(self, request, class_id, subject_id, module_id, lesson_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        return sc, subj, module, lesson

    def get(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        sc, subj, module, lesson = self._get(
            request, class_id, subject_id, module_id, lesson_id
        )
        return self.render_to_response(
            {"school_class": sc, "subject": subj, "module": module, "lesson": lesson}
        )

    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson = self._get(
            request, class_id, subject_id, module_id, lesson_id
        )
        title = lesson.title
        course_services.delete_lesson(lesson=lesson, actor=request.user)
        messages.success(request, f'Lesson "{title}" deleted.')
        return redirect(
            "learning:teacher_classroom", class_id=sc.pk, subject_id=subj.pk
        )


class LessonMoveView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    def post(self, request, class_id, subject_id, module_id, lesson_id, direction, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        if direction not in ("up", "down"):
            raise Http404
        course_services.move_lesson(lesson=lesson, direction=direction, actor=request.user)
        return redirect(
            "learning:teacher_classroom", class_id=sc.pk, subject_id=subj.pk
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class LessonDuplicateView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        new_lesson = course_services.duplicate_lesson(lesson=lesson, actor=request.user)
        messages.success(request, f'Lesson duplicated as "{new_lesson.title}" (draft).')
        return redirect(
            "learning:teacher_classroom", class_id=sc.pk, subject_id=subj.pk
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class ModuleDuplicateView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    def post(self, request, class_id, subject_id, module_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        new_module = course_services.duplicate_module(module=module, actor=request.user)
        messages.success(
            request,
            f'Module "{new_module.title}" duplicated (all lessons set to draft).'
        )
        return redirect(
            "learning:teacher_classroom", class_id=sc.pk, subject_id=subj.pk
        )

    def get(self, request, *args, **kwargs):
        raise Http404


# ── Attachment views ──────────────────────────────────────────────────────────

class AttachmentDeleteView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    def post(self, request, class_id, subject_id, module_id, lesson_id, attachment_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        attachment = get_object_or_404(LessonAttachment, pk=attachment_id, lesson=lesson)
        course_services.delete_attachment(attachment=attachment, actor=request.user)
        messages.success(request, "Attachment removed.")
        return redirect(
            "learning:lesson_edit",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=module.pk, lesson_id=lesson.pk,
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class LessonAttachmentFileView(RoleRequiredMixin, TemplateView):
    """Stream a lesson attachment to authorised users."""
    allowed_roles = ["admin", "director", "teacher", "student", "parent"]

    def get(self, request, lesson_id, attachment_id, *args, **kwargs):
        attachment = get_object_or_404(
            LessonAttachment,
            pk=attachment_id,
            lesson_id=lesson_id,
        )
        lesson = attachment.lesson
        role = getattr(request.user, "role", "")

        # Verify access
        if role in ("teacher", "admin", "director"):
            if role == "teacher":
                if course_selectors.lesson_detail_for_teacher(
                    lesson_id=lesson.pk, teacher_user_id=request.user.pk
                ) is None:
                    raise Http404
        elif role in ("student", "parent"):
            student = _resolve_student(request)
            if course_selectors.lesson_detail_for_student(
                lesson_id=lesson.pk, student=student
            ) is None:
                raise Http404
        else:
            raise Http404

        content_type = (
            attachment.content_type
            or _mimetypes.guess_type(attachment.file.name)[0]
            or "application/octet-stream"
        )
        as_attachment = (
            request.GET.get("download") == "1"
            or not (
                content_type == "application/pdf"
                or content_type.startswith("image/")
                or content_type.startswith("audio/")
            )
        )
        try:
            fh = attachment.file.open("rb")
        except FileNotFoundError:
            raise Http404("File not found in storage.")

        resp = _FileResponse(
            fh,
            as_attachment=as_attachment,
            filename=attachment.original_name or attachment.file.name.split("/")[-1],
            content_type=content_type,
        )
        resp["X-Content-Type-Options"] = "nosniff"
        return resp


# ── Lesson preview (teacher sees student view) ────────────────────────────────

class LessonPreviewView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    """Teacher preview: renders the lesson exactly as students will see it."""
    template_name = "features/learning/lesson_view.html"

    def get(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        attachments = list(lesson.lesson_attachments.all())
        from .markdown_utils import render_notes
        rendered_notes = render_notes(lesson.notes, notes_format=lesson.notes_format)
        from .video_utils import parse_video_url
        video_info = parse_video_url(lesson.video_url)
        return self.render_to_response({
            "lesson": lesson,
            "module": module,
            "school_class": sc,
            "subject": subj,
            "attachments": attachments,
            "is_preview": True,
            "completed": False,
            "rendered_notes": rendered_notes,
            "video_info": video_info,
            "next_lp": None,
            "prev_lp": None,
            "student_note": None,
        })


# ── Copy course view ──────────────────────────────────────────────────────────

class CopyCourseView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    template_name = "features/learning/copy_course.html"

    def _get_base(self, request, class_id, subject_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        term = _get_term(request)
        return sc, subj, term

    def get(self, request, class_id, subject_id, *args, **kwargs):
        sc, subj, term = self._get_base(request, class_id, subject_id)
        from apps.academics.selectors import get_current_year
        year = get_current_year()
        target_terms = course_selectors.available_terms_for_copy(
            school_class=sc, subject=subj, exclude_term=term
        )
        target_classes = course_selectors.available_classes_for_copy(
            academic_year=year, exclude_class=sc
        ) if year else []
        return self.render_to_response({
            "school_class": sc, "subject": subj, "term": term,
            "target_terms": target_terms,
            "target_classes": target_classes,
        })

    def post(self, request, class_id, subject_id, *args, **kwargs):
        from django.shortcuts import redirect
        from apps.academics.models import SchoolClass, Term
        sc, subj, term = self._get_base(request, class_id, subject_id)

        target_term_id = request.POST.get("target_term_id")
        target_class_id = request.POST.get("target_class_id")
        target_subject_id = request.POST.get("target_subject_id")

        # Target class + subject (can be same class, different term)
        if target_class_id and target_subject_id:
            from apps.academics.models import Subject
            target_class = get_object_or_404(SchoolClass, pk=target_class_id)
            target_subj = get_object_or_404(Subject, pk=target_subject_id)
        else:
            target_class = sc
            target_subj = subj

        if not target_term_id:
            messages.error(request, "Please select a target term.")
            return redirect(
                "learning:copy_course", class_id=sc.pk, subject_id=subj.pk
            )

        target_term = get_object_or_404(Term, pk=target_term_id)

        count = course_services.copy_course(
            source_class=sc, source_subject=subj, source_term=term,
            target_class=target_class, target_subject=target_subj,
            target_term=target_term, actor=request.user,
        )
        if count == 0:
            messages.warning(request, "No content to copy — the source course is empty.")
        else:
            messages.success(
                request,
                f"{count} lesson(s) copied successfully. "
                f"All lessons are set to draft — review and publish when ready. "
                f"Note: file attachments are not copied."
            )
        return redirect(
            "learning:teacher_classroom", class_id=sc.pk, subject_id=subj.pk
        )


class StudentLessonView(StudentOrParentMixin, LearningEnabledMixin, TemplateView):
    """
    Student lesson page — Checkpoint B.
    Renders: safe HTML notes (Markdown or plain), robust video embed,
    next/prev navigation, student private notes, downloadable attachments.
    """
    template_name = "features/learning/lesson_view.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = _resolve_student(self.request)
        from . import course_selectors
        lesson = course_selectors.lesson_detail_for_student(
            lesson_id=self.kwargs["lesson_id"],
            student=student,
        )
        if lesson is None:
            raise Http404("Lesson not found.")

        from apps.learning.models import LessonProgress
        completed = LessonProgress.objects.filter(
            student=student, lesson=lesson, completed=True
        ).exists()

        # Next / prev navigation
        nav_kwargs = dict(
            lesson_id=lesson.pk,
            school_class=lesson.module.school_class,
            subject=lesson.module.subject,
            term=lesson.module.term,
            student=student,
        )
        next_lp = course_selectors.next_lesson_for_student(**nav_kwargs)
        prev_lp = course_selectors.prev_lesson_for_student(**nav_kwargs)

        # Student private note (None for parents — parents cannot write notes)
        student_note = None
        if self.request.user.role == "student":
            from .lesson_notes_services import get_lesson_note
            student_note = get_lesson_note(student=student, lesson=lesson)

        # Safe HTML notes
        from .markdown_utils import render_notes
        rendered_notes = render_notes(lesson.notes, notes_format=lesson.notes_format)

        # Video info — robust parsing for all YouTube/Vimeo formats
        from .video_utils import parse_video_url
        video_info = parse_video_url(lesson.video_url)

        ctx.update({
            "lesson": lesson,
            "module": lesson.module,
            "school_class": lesson.module.school_class,
            "subject": lesson.module.subject,
            "attachments": list(lesson.lesson_attachments.all()),
            "is_preview": False,
            "completed": completed,
            "is_parent": self.request.user.role == "parent",
            "next_lp": next_lp,
            "prev_lp": prev_lp,
            "student_note": student_note,
            "rendered_notes": rendered_notes,
            "video_info": video_info,
        })
        return ctx


# ---------------------------------------------------------------------------
# Checkpoint B — lesson note save and course search
# ---------------------------------------------------------------------------

from .lesson_notes_services import save_lesson_note
from .models import StudentLessonNote


class SaveLessonNoteView(StudentOrParentMixin, LearningEnabledMixin, TemplateView):
    """
    POST-only: save the student's private note for one lesson.
    Returns JSON for the inline autosave, or redirects for a normal form POST.
    Parents cannot save notes.
    """

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect

        if request.user.role == "parent":
            raise PermissionDenied("Parents cannot write lesson notes.")

        student = _resolve_student(request)
        from . import course_selectors
        lesson = course_selectors.lesson_detail_for_student(
            lesson_id=self.kwargs["lesson_id"], student=student
        )
        if lesson is None:
            raise Http404

        content = request.POST.get("content", "")
        note = save_lesson_note(student=student, lesson=lesson, content=content)

        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if is_xhr:
            return JsonResponse({"ok": True, "chars": len(note.content)})

        return redirect(
            "learning:lesson_view",
            class_id=lesson.module.school_class_id,
            subject_id=lesson.module.subject_id,
            lesson_id=lesson.pk,
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class CourseSearchView(StudentOrParentMixin, LearningEnabledMixin, TemplateView):
    """Search lessons by title or notes within one course."""
    template_name = "features/learning/course_search.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = _resolve_student(self.request)
        from . import course_selectors as cs
        from apps.academics.models import SchoolClass, Subject
        sc = get_object_or_404(SchoolClass, pk=self.kwargs["class_id"])
        subj = get_object_or_404(Subject, pk=self.kwargs["subject_id"])

        # Verify access: student must be in this class
        classroom = selectors.student_classroom(
            student=student,
            class_id=sc.pk,
            subject_id=subj.pk,
        )
        if classroom is None:
            raise Http404

        query = self.request.GET.get("q", "").strip()
        results = []
        if query and classroom.term:
            results = cs.search_course(
                school_class=sc, subject=subj,
                term=classroom.term, student=student,
                query=query,
            )
        ctx.update({
            "school_class": sc,
            "subject": subj,
            "classroom": classroom,
            "query": query,
            "results": results,
            "is_parent": self.request.user.role == "parent",
        })
        return ctx


# ---------------------------------------------------------------------------
# Quiz views — Checkpoint C
# ---------------------------------------------------------------------------
# All additive. Nothing above is touched.
# Gated behind "quizzes" feature flag (checks learning + quizzes both on).
# ---------------------------------------------------------------------------

from . import quiz_selectors, quiz_services
from .models import Quiz, QuizAttempt, QuizQuestion, QuizChoice
from .quiz_services import QuizLockedError


class QuizEnabledMixin:
    """Redirect to dashboard when quizzes_enabled is off."""
    def dispatch(self, request, *args, **kwargs):
        if not feature_enabled("quizzes", request=request):
            from django.shortcuts import redirect
            messages.info(request, "Quizzes are not yet activated for this school.")
            return redirect("accounts:dashboard_redirect")
        return super().dispatch(request, *args, **kwargs)


# ── Teacher quiz builder ──────────────────────────────────────────────────


class TeacherQuizDetailView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    """Quiz builder: settings + question list. GET only."""
    template_name = "features/learning/quiz_builder.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sc, subj = _get_teacher_assignment(
            self.request, self.kwargs["class_id"], self.kwargs["subject_id"]
        )
        module = get_object_or_404(
            CourseModule, pk=self.kwargs["module_id"], school_class=sc, subject=subj
        )
        lesson = get_object_or_404(Lesson, pk=self.kwargs["lesson_id"], module=module)
        quiz = quiz_selectors.quiz_for_lesson(lesson_id=lesson.pk)
        questions = (
            quiz_selectors.quiz_questions_for_teacher(quiz=quiz) if quiz else []
        )
        ctx.update({
            "school_class": sc, "subject": subj,
            "module": module, "lesson": lesson,
            "quiz": quiz, "questions": questions,
            "is_locked": quiz.has_submitted_attempts if quiz else False,
            "question_types": QuizQuestion.QuestionType.choices,
        })
        return ctx


class TeacherQuizCreateView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    template_name = "features/learning/quiz_settings_form.html"

    def _base(self, request, class_id, subject_id, module_id, lesson_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        return sc, subj, module, lesson

    def get(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        sc, subj, module, lesson = self._base(
            request, class_id, subject_id, module_id, lesson_id
        )
        return self.render_to_response({
            "school_class": sc, "subject": subj, "module": module,
            "lesson": lesson, "quiz": None,
            "attempt_choices": Quiz.Attempts.choices,
        })

    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson = self._base(
            request, class_id, subject_id, module_id, lesson_id
        )
        try:
            quiz = quiz_services.create_quiz(
                lesson=lesson,
                title=request.POST.get("title", ""),
                instructions=request.POST.get("instructions", ""),
                pass_mark=int(request.POST.get("pass_mark", 50)),
                attempts_allowed=int(request.POST.get("attempts_allowed", 1)),
                time_limit_minutes=int(t) if (t := request.POST.get("time_limit_minutes", "").strip()) else None,
                shuffle_questions=request.POST.get("shuffle_questions") == "1",
                shuffle_choices=request.POST.get("shuffle_choices") == "1",
                show_correct_after=request.POST.get("show_correct_after") == "1",
                actor=request.user,
            )
            messages.success(request, f'Quiz "{quiz.title}" created.')
        except (_ValidationError, Exception) as exc:
            messages.error(request, str(exc))
        return redirect(
            "learning:teacher_quiz",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=module.pk, lesson_id=lesson.pk,
        )


class TeacherQuizSettingsView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    template_name = "features/learning/quiz_settings_form.html"

    def _get(self, request, class_id, subject_id, module_id, lesson_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(
            CourseModule, pk=module_id, school_class=sc, subject=subj
        )
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        quiz = get_object_or_404(Quiz, lesson=lesson)
        return sc, subj, module, lesson, quiz

    def get(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        sc, subj, module, lesson, quiz = self._get(
            request, class_id, subject_id, module_id, lesson_id
        )
        return self.render_to_response({
            "school_class": sc, "subject": subj, "module": module,
            "lesson": lesson, "quiz": quiz,
            "attempt_choices": Quiz.Attempts.choices,
        })

    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson, quiz = self._get(
            request, class_id, subject_id, module_id, lesson_id
        )
        try:
            quiz_services.update_quiz_settings(
                quiz=quiz,
                title=request.POST.get("title", ""),
                instructions=request.POST.get("instructions", ""),
                pass_mark=int(request.POST.get("pass_mark", 50)),
                attempts_allowed=int(request.POST.get("attempts_allowed", 1)),
                time_limit_minutes=int(t) if (t := request.POST.get("time_limit_minutes", "").strip()) else None,
                shuffle_questions=request.POST.get("shuffle_questions") == "1",
                shuffle_choices=request.POST.get("shuffle_choices") == "1",
                show_correct_after=request.POST.get("show_correct_after") == "1",
                counts_for_gradebook=request.POST.get("counts_for_gradebook") == "1",
                actor=request.user,
            )
            messages.success(request, "Quiz settings saved.")
        except (_ValidationError, Exception) as exc:
            messages.error(request, str(exc))
        return redirect(
            "learning:teacher_quiz",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=module.pk, lesson_id=lesson.pk,
        )


class TeacherQuizPublishView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    def post(self, request, class_id, subject_id, module_id, lesson_id, action, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(CourseModule, pk=module_id, school_class=sc, subject=subj)
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        quiz = get_object_or_404(Quiz, lesson=lesson)
        try:
            if action == "publish":
                quiz_services.publish_quiz(quiz=quiz, actor=request.user)
                messages.success(request, "Quiz published — students can now take it.")
            elif action == "unpublish":
                quiz_services.unpublish_quiz(quiz=quiz, actor=request.user)
                messages.success(request, "Quiz hidden from students.")
            elif action == "duplicate":
                # Duplicating creates a copy on a new unpublished lesson in the same module
                new_lesson = Lesson.objects.create(
                    module=lesson.module,
                    title=f"{lesson.title} (copy)",
                    position=9999,
                    is_published=False,
                )
                quiz_services.duplicate_quiz(quiz=quiz, actor=request.user,
                                             target_lesson=new_lesson)
                messages.success(request, "Quiz duplicated as a new draft on a new lesson.")
        except (_ValidationError, Exception) as exc:
            messages.error(request, str(exc))
        return redirect(
            "learning:teacher_quiz",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=module.pk, lesson_id=lesson.pk,
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class TeacherAddQuestionView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    template_name = "features/learning/quiz_question_form.html"

    def _get_quiz(self, request, class_id, subject_id, module_id, lesson_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(CourseModule, pk=module_id, school_class=sc, subject=subj)
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        quiz = get_object_or_404(Quiz, lesson=lesson)
        return sc, subj, module, lesson, quiz

    def get(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        sc, subj, module, lesson, quiz = self._get_quiz(
            request, class_id, subject_id, module_id, lesson_id
        )
        return self.render_to_response({
            "school_class": sc, "subject": subj, "module": module,
            "lesson": lesson, "quiz": quiz, "question": None,
            "question_types": QuizQuestion.QuestionType.choices,
            "is_locked": quiz.has_submitted_attempts,
        })

    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson, quiz = self._get_quiz(
            request, class_id, subject_id, module_id, lesson_id
        )
        try:
            q = quiz_services.add_question(
                quiz=quiz,
                body=request.POST.get("body", ""),
                question_type=request.POST.get("question_type", QuizQuestion.QuestionType.MULTIPLE_CHOICE),
                explanation=request.POST.get("explanation", ""),
                actor=request.user,
            )
            # Add choices from POST (for MC: texts + which is correct)
            choice_texts = request.POST.getlist("choice_text")
            correct_idx = request.POST.get("correct_choice", "0")
            for i, text in enumerate(choice_texts):
                if text.strip():
                    quiz_services.add_choice(
                        question=q, text=text,
                        is_correct=(str(i) == str(correct_idx)),
                        actor=request.user,
                    )
            messages.success(request, "Question added.")
        except (QuizLockedError, _ValidationError) as exc:
            messages.error(request, str(exc))
        return redirect(
            "learning:teacher_quiz",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=module.pk, lesson_id=lesson.pk,
        )


class TeacherEditQuestionView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    template_name = "features/learning/quiz_question_form.html"

    def _get(self, request, class_id, subject_id, module_id, lesson_id, question_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(CourseModule, pk=module_id, school_class=sc, subject=subj)
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        quiz = get_object_or_404(Quiz, lesson=lesson)
        question = get_object_or_404(QuizQuestion, pk=question_id, quiz=quiz)
        return sc, subj, module, lesson, quiz, question

    def get(self, request, class_id, subject_id, module_id, lesson_id, question_id, *args, **kwargs):
        sc, subj, module, lesson, quiz, question = self._get(
            request, class_id, subject_id, module_id, lesson_id, question_id
        )
        choices = list(question.choices.order_by("position"))
        return self.render_to_response({
            "school_class": sc, "subject": subj, "module": module,
            "lesson": lesson, "quiz": quiz, "question": question,
            "choices": choices,
            "question_types": QuizQuestion.QuestionType.choices,
            "is_locked": quiz.has_submitted_attempts,
        })

    def post(self, request, class_id, subject_id, module_id, lesson_id, question_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson, quiz, question = self._get(
            request, class_id, subject_id, module_id, lesson_id, question_id
        )
        try:
            quiz_services.update_question(
                question=question,
                body=request.POST.get("body", ""),
                explanation=request.POST.get("explanation", ""),
                actor=request.user,
            )
            # Update choices
            choice_pks = request.POST.getlist("choice_pk")
            choice_texts = request.POST.getlist("choice_text")
            correct_pk = request.POST.get("correct_choice_pk", "")
            for pk, text in zip(choice_pks, choice_texts):
                try:
                    choice = QuizChoice.objects.get(pk=int(pk), question=question)
                    quiz_services.update_choice(
                        choice=choice, text=text,
                        is_correct=(str(pk) == str(correct_pk)),
                        actor=request.user,
                    )
                except QuizChoice.DoesNotExist:
                    pass
            messages.success(request, "Question updated.")
        except (QuizLockedError, _ValidationError) as exc:
            messages.error(request, str(exc))
        return redirect(
            "learning:teacher_quiz",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=module.pk, lesson_id=lesson.pk,
        )


class TeacherDeleteQuestionView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    def post(self, request, class_id, subject_id, module_id, lesson_id, question_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(CourseModule, pk=module_id, school_class=sc, subject=subj)
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        quiz = get_object_or_404(Quiz, lesson=lesson)
        question = get_object_or_404(QuizQuestion, pk=question_id, quiz=quiz)
        try:
            quiz_services.delete_question(question=question, actor=request.user)
            messages.success(request, "Question deleted.")
        except QuizLockedError as exc:
            messages.error(request, str(exc))
        return redirect(
            "learning:teacher_quiz",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=module.pk, lesson_id=lesson.pk,
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class TeacherMoveQuestionView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    def post(self, request, class_id, subject_id, module_id, lesson_id, question_id, direction, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(CourseModule, pk=module_id, school_class=sc, subject=subj)
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        quiz = get_object_or_404(Quiz, lesson=lesson)
        question = get_object_or_404(QuizQuestion, pk=question_id, quiz=quiz)
        if direction in ("up", "down"):
            quiz_services.move_question(question=question, direction=direction, actor=request.user)
        return redirect(
            "learning:teacher_quiz",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=module.pk, lesson_id=lesson.pk,
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class TeacherQuizResultsView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    template_name = "features/learning/quiz_teacher_results.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sc, subj = _get_teacher_assignment(
            self.request, self.kwargs["class_id"], self.kwargs["subject_id"]
        )
        module = get_object_or_404(
            CourseModule, pk=self.kwargs["module_id"], school_class=sc, subject=subj
        )
        lesson = get_object_or_404(Lesson, pk=self.kwargs["lesson_id"], module=module)
        quiz = get_object_or_404(Quiz, lesson=lesson)
        results = quiz_selectors.teacher_quiz_results(quiz=quiz, school_class=sc)
        ctx.update({
            "school_class": sc, "subject": subj,
            "module": module, "lesson": lesson,
            "quiz": quiz, "results": results,
        })
        return ctx


# ── Student quiz views ────────────────────────────────────────────────────


class StudentQuizLandingView(
    StudentOrParentMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    """
    Landing page before taking the quiz: shows instructions, attempt history,
    best score, and a Start/Continue/Retry button.
    """
    template_name = "features/learning/quiz_landing.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = _resolve_student(self.request)
        from apps.academics.models import SchoolClass, Subject
        sc = get_object_or_404(SchoolClass, pk=self.kwargs["class_id"])
        subj = get_object_or_404(Subject, pk=self.kwargs["subject_id"])
        lesson = get_object_or_404(
            Lesson,
            pk=self.kwargs["lesson_id"],
            module__school_class=sc,
            module__subject=subj,
            module__is_published=True,
            is_published=True,
        )
        if lesson.module.school_class_id != student.current_class_id:
            raise Http404
        quiz = get_object_or_404(Quiz, lesson=lesson, is_published=True)
        summary = quiz_selectors.student_attempt_summary(quiz=quiz, student=student)
        ctx.update({
            "school_class": sc, "subject": subj,
            "lesson": lesson, "quiz": quiz, "summary": summary,
            "is_parent": self.request.user.role == "parent",
        })
        return ctx


class StudentTakeQuizView(
    StudentOrParentMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    """
    The actual quiz-taking page. Correct answers are NEVER in the context.
    Answers save via AJAX to SaveQuizAnswerView; submit via POST here.
    """
    template_name = "features/learning/quiz_take.html"

    def _get_quiz_and_student(self, request, class_id, subject_id, lesson_id):
        student = _resolve_student(request)
        from apps.academics.models import SchoolClass, Subject
        sc = get_object_or_404(SchoolClass, pk=class_id)
        subj = get_object_or_404(Subject, pk=subject_id)
        lesson = get_object_or_404(
            Lesson,
            pk=lesson_id,
            module__school_class=sc,
            module__is_published=True,
            is_published=True,
        )
        if lesson.module.school_class_id != student.current_class_id:
            raise Http404
        quiz = get_object_or_404(Quiz, lesson=lesson, is_published=True)
        return student, sc, subj, lesson, quiz

    def get(self, request, class_id, subject_id, lesson_id, *args, **kwargs):
        if request.user.role == "parent":
            raise PermissionDenied("Parents cannot take quizzes.")
        student, sc, subj, lesson, quiz = self._get_quiz_and_student(
            request, class_id, subject_id, lesson_id
        )
        from django.core.exceptions import ValidationError as VE
        try:
            attempt = quiz_services.start_or_resume_attempt(quiz=quiz, student=student)
        except VE as exc:
            messages.error(request, str(exc))
            from django.shortcuts import redirect
            return redirect(
                "learning:quiz_landing",
                class_id=sc.pk, subject_id=subj.pk, lesson_id=lesson.pk,
            )
        if attempt.is_submitted:
            from django.shortcuts import redirect
            return redirect(
                "learning:quiz_results",
                class_id=sc.pk, subject_id=subj.pk,
                lesson_id=lesson.pk, attempt_id=attempt.pk,
            )
        questions = quiz_selectors.quiz_questions_for_student(quiz=quiz, attempt=attempt)
        ctx = {
            "school_class": sc, "subject": subj,
            "lesson": lesson, "quiz": quiz, "attempt": attempt,
            "questions": questions,
            "time_remaining": attempt.time_remaining_seconds,
            "save_answer_url": f"/learning/{sc.pk}/{subj.pk}/quiz/{lesson.pk}/save-answer/",
        }
        return self.render_to_response(ctx)

    def post(self, request, class_id, subject_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        student, sc, subj, lesson, quiz = self._get_quiz_and_student(
            request, class_id, subject_id, lesson_id
        )
        attempt = QuizAttempt.objects.filter(
            quiz=quiz, student=student, submitted_at__isnull=True
        ).first()
        if not attempt:
            return redirect(
                "learning:quiz_landing",
                class_id=sc.pk, subject_id=subj.pk, lesson_id=lesson.pk,
            )
        attempt = quiz_services.submit_attempt(attempt=attempt, student=student)
        return redirect(
            "learning:quiz_results",
            class_id=sc.pk, subject_id=subj.pk,
            lesson_id=lesson.pk, attempt_id=attempt.pk,
        )


class SaveQuizAnswerView(StudentOrParentMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView):
    """
    AJAX POST-only: save one answer as student clicks.
    Returns JSON {ok, saved}. Never returns is_correct.
    """
    def post(self, request, class_id, subject_id, lesson_id, *args, **kwargs):
        if request.user.role == "parent":
            return JsonResponse({"ok": False, "error": "Parents cannot answer quizzes."}, status=403)
        student = _resolve_student(request)
        from apps.academics.models import SchoolClass, Subject
        sc = get_object_or_404(SchoolClass, pk=class_id)
        subj = get_object_or_404(Subject, pk=subject_id)
        lesson = get_object_or_404(
            Lesson, pk=lesson_id,
            module__school_class=sc, is_published=True,
        )
        if lesson.module.school_class_id != student.current_class_id:
            return JsonResponse({"ok": False, "error": "Not your class."}, status=403)
        quiz = get_object_or_404(Quiz, lesson=lesson, is_published=True)
        attempt = QuizAttempt.objects.filter(
            quiz=quiz, student=student, submitted_at__isnull=True
        ).first()
        if not attempt:
            return JsonResponse({"ok": False, "error": "No active attempt."})
        try:
            question_id = int(request.POST.get("question_id", 0))
            choice_id_raw = request.POST.get("choice_id", "")
            choice_id = int(choice_id_raw) if choice_id_raw else None
        except (ValueError, TypeError):
            return JsonResponse({"ok": False, "error": "Invalid data."})
        quiz_services.save_answer(
            attempt=attempt, question_id=question_id, choice_id=choice_id
        )
        return JsonResponse({"ok": True, "saved": question_id})

    def get(self, request, *args, **kwargs):
        raise Http404


class StudentQuizResultsView(
    StudentOrParentMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    """
    Student results page for one attempt. Parents can view their child's results.
    Another student's attempt → 404.
    Correct answers only shown when quiz.show_correct_after=True AND submitted.
    """
    template_name = "features/learning/quiz_results.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = _resolve_student(self.request)
        from apps.academics.models import SchoolClass, Subject
        sc = get_object_or_404(SchoolClass, pk=self.kwargs["class_id"])
        subj = get_object_or_404(Subject, pk=self.kwargs["subject_id"])
        lesson = get_object_or_404(
            Lesson, pk=self.kwargs["lesson_id"],
            module__school_class=sc, is_published=True,
        )
        if lesson.module.school_class_id != student.current_class_id:
            raise Http404
        quiz = get_object_or_404(Quiz, lesson=lesson)
        attempt = get_object_or_404(
            QuizAttempt, pk=self.kwargs["attempt_id"],
            quiz=quiz, student=student,
        )
        if not attempt.is_submitted:
            raise Http404
        show_correct = attempt.is_submitted and quiz.show_correct_after
        rows = quiz_selectors.quiz_questions_for_results(
            quiz=quiz, attempt=attempt, show_correct=show_correct
        )
        summary = quiz_selectors.student_attempt_summary(quiz=quiz, student=student)
        ctx.update({
            "school_class": sc, "subject": subj,
            "lesson": lesson, "quiz": quiz,
            "attempt": attempt, "rows": rows,
            "summary": summary, "show_correct": show_correct,
            "is_parent": self.request.user.role == "parent",
        })
        return ctx
