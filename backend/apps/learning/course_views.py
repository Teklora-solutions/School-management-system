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

import mimetypes as _mimetypes

from django.core.exceptions import ValidationError as _ValidationError
from django.http import FileResponse as _FileResponse

from . import comment_services, course_selectors, course_services, release, topic_services
from .models import (
    CourseModule, Lesson, LessonAttachment, LessonComment, Topic,
    LESSON_ATTACHMENT_ALLOWED_EXTENSIONS,
)


def _students_for_targeting(school_class) -> list:
    """Pupils of a class, for the assign-to-chosen-students pickers."""
    return list(
        Student.objects.filter(current_class=school_class)
        .select_related("user", "current_stream")
        .order_by("user__last_name", "user__first_name", "pk")
    )


def _assert_quiz_reachable_for_student(*, quiz, student) -> None:
    """
    A student may only open a quiz that is published, inside its publish/hide
    window (checked on read — worker independent), and targeted at them; the
    same must hold for the quiz's lesson and module.  Anything else is a 404,
    so unassigned students cannot reach it by typing the address.
    """
    if not course_selectors.quiz_visible_to_student(quiz, student):
        raise Http404


def _course_topics(module: CourseModule) -> list:
    """Topics for the lesson form's grouping dropdown."""
    return list(
        Topic.objects.filter(
            school_class=module.school_class,
            subject=module.subject,
            term=module.term,
        ).order_by("position", "id")
    )


def _schedule_from_post(request):
    publish_raw = request.POST.get("publish_at", "").strip()
    hide_raw = request.POST.get("hide_at", "").strip()
    publish_at = _parse_nairobi_dt(publish_raw)
    hide_at = _parse_nairobi_dt(hide_raw)
    if (publish_raw and publish_at is None) or (hide_raw and hide_at is None):
        raise _ValidationError("Please enter valid release dates in Nairobi time.")
    if publish_at and hide_at and hide_at <= publish_at:
        raise _ValidationError("'Hide on' must be after 'Publish on'.")
    return publish_at, hide_at


def _student_ids_from_post(request) -> list[int]:
    return [int(v) for v in request.POST.getlist("assigned_students") if v.isdigit()]


def _apply_lesson_release_and_targeting(request, lesson: Lesson) -> None:
    if "publish_at" in request.POST or "hide_at" in request.POST:
        lesson.publish_at, lesson.hide_at = _schedule_from_post(request)
        lesson.save(update_fields=["publish_at", "hide_at", "updated_at"])
    if "assign_all" in request.POST:
        topic_services.set_lesson_targets(
            lesson=lesson,
            assign_all=request.POST.get("assign_all") != "0",
            student_ids=_student_ids_from_post(request),
            actor=request.user,
        )
    if "topic_id" in request.POST:
        topic_id = request.POST.get("topic_id", "").strip()
        topic = None
        if topic_id:
            topic = next(
                (t for t in _course_topics(lesson.module) if str(t.pk) == topic_id),
                None,
            )
        if topic is None or topic.pk != lesson.topic_id:
            topic_services.set_lesson_topic(lesson=lesson, topic=topic, actor=request.user)


def _apply_quiz_release_and_targeting(request, quiz) -> None:
    """Apply publish-on / hide-on and the assign-to-students choice (quiz form)."""
    if "publish_at" in request.POST or "hide_at" in request.POST:
        quiz.publish_at, quiz.hide_at = _schedule_from_post(request)
        quiz.save(update_fields=["publish_at", "hide_at", "updated_at"])
    if "assign_all" in request.POST:
        topic_services.set_quiz_targets(
            quiz=quiz,
            assign_all=request.POST.get("assign_all") != "0",
            student_ids=_student_ids_from_post(request),
            actor=request.user,
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
            _schedule_from_post(request)
            module = course_services.create_module(
                school_class=sc, subject=subj, term=term,
                title=title, actor=request.user,
            )
            if "publish_at" in request.POST or "hide_at" in request.POST:
                course_services.update_module(
                    module=module, title=title,
                    is_published=request.POST.get("is_published") == "1", actor=request.user,
                )
                publish_at, hide_at = _schedule_from_post(request)
                topic_services.set_module_schedule(
                    module=module, publish_at=publish_at, hide_at=hide_at,
                    actor=request.user,
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
            _schedule_from_post(request)
            course_services.update_module(
                module=module, title=title,
                is_published=is_published, actor=request.user,
            )
            if "publish_at" in request.POST or "hide_at" in request.POST:
                publish_at, hide_at = _schedule_from_post(request)
                topic_services.set_module_schedule(
                    module=module,
                    publish_at=publish_at,
                    hide_at=hide_at,
                    actor=request.user,
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
             "allowed_ext": ", ".join(sorted(LESSON_ATTACHMENT_ALLOWED_EXTENSIONS)),
             "topics": _course_topics(module),
             "students": _students_for_targeting(sc)}
        )

    def post(self, request, class_id, subject_id, module_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module = self._get_module(request, class_id, subject_id, module_id)
        try:
            _schedule_from_post(request)
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
            _apply_lesson_release_and_targeting(request, lesson)
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
             "allowed_ext": ", ".join(sorted(LESSON_ATTACHMENT_ALLOWED_EXTENSIONS)),
             "topics": _course_topics(module),
             "students": _students_for_targeting(sc),
             "assigned_student_ids": list(lesson.assigned_students.values_list("pk", flat=True))}
        )

    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson, _ = self._get(
            request, class_id, subject_id, module_id, lesson_id
        )
        try:
            _schedule_from_post(request)
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
            _apply_lesson_release_and_targeting(request, lesson)
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


# ── Topic views (organise lessons under named topics) ────────────────────────

class TopicCreateView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    """POST-only: add a named topic to this course."""

    def post(self, request, class_id, subject_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        term = _get_term(request)
        title = request.POST.get("title", "")
        try:
            topic_services.create_topic(
                school_class=sc, subject=subj, term=term,
                title=title, actor=request.user,
            )
            messages.success(request, f'Topic "{title.strip()}" added.')
        except _ValidationError as exc:
            messages.error(request, exc.message)
        return _term_redirect(sc, subj, term)

    def get(self, request, *args, **kwargs):
        raise Http404


class TopicEditView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    """POST-only: rename a topic."""

    def _get_topic(self, request, class_id, subject_id, topic_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        term = _get_term(request)
        topic = get_object_or_404(
            Topic, pk=topic_id, school_class=sc, subject=subj, term=term
        )
        return sc, subj, term, topic

    def post(self, request, class_id, subject_id, topic_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, term, topic = self._get_topic(
            request, class_id, subject_id, topic_id
        )
        title = request.POST.get("title", "")
        try:
            topic_services.rename_topic(
                topic=topic, title=title, actor=request.user
            )
            messages.success(request, "Topic renamed.")
        except _ValidationError as exc:
            messages.error(request, exc.message)
        return _term_redirect(sc, subj, term)

    def get(self, request, *args, **kwargs):
        raise Http404


class TopicDeleteView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    """POST-only: delete a topic.  Its lessons are kept, just ungrouped."""

    def post(self, request, class_id, subject_id, topic_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        term = _get_term(request)
        topic = get_object_or_404(
            Topic, pk=topic_id, school_class=sc, subject=subj, term=term
        )
        title = topic.title
        topic_services.delete_topic(topic=topic, actor=request.user)
        messages.success(
            request, f'Topic "{title}" deleted. Its lessons are still in the course.'
        )
        return _term_redirect(sc, subj, term)

    def get(self, request, *args, **kwargs):
        raise Http404


class TopicMoveView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    """POST-only: move a topic up or down (no JavaScript needed)."""

    def post(self, request, class_id, subject_id, topic_id, direction, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        term = _get_term(request)
        topic = get_object_or_404(
            Topic, pk=topic_id, school_class=sc, subject=subj, term=term
        )
        if direction not in ("up", "down"):
            raise Http404
        topic_services.move_topic(topic=topic, direction=direction, actor=request.user)
        return _term_redirect(sc, subj, term)

    def get(self, request, *args, **kwargs):
        raise Http404


class LessonTopicMoveView(TeacherRequiredMixin, LearningEnabledMixin, TemplateView):
    """POST-only: move a lesson up or down inside its topic."""

    def post(self, request, class_id, subject_id, lesson_id, direction, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        lesson = get_object_or_404(
            Lesson, pk=lesson_id, module__school_class=sc, module__subject=subj
        )
        if direction not in ("up", "down"):
            raise Http404
        topic_services.move_lesson_in_topic(
            lesson=lesson, direction=direction, actor=request.user
        )
        term = _get_term(request)
        return _term_redirect(sc, subj, term)

    def get(self, request, *args, **kwargs):
        raise Http404


def _term_redirect(sc, subj, term):
    """Redirect to the teacher classroom, preserving a non-current ?term_id=."""
    from django.shortcuts import redirect as _redirect
    from django.urls import reverse

    url = reverse(
        "learning:teacher_classroom",
        kwargs={"class_id": sc.pk, "subject_id": subj.pk},
    )
    current = None
    from apps.academics.selectors import get_current_term
    try:
        current = get_current_term()
    except Exception:  # noqa: BLE001
        current = None
    if term and current and term.pk != current.pk:
        url += f"?term_id={term.pk}"
    return _redirect(url)


# ── Lesson comments (student questions, teacher moderation) ──────────────────

class LessonCommentsToggleView(TeacherRequiredMixin, LearningEnabledMixin, View):
    """POST-only: switch lesson comments on/off for one class (default off)."""

    def post(self, request, class_id, subject_id, *args, **kwargs):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        eng, _ = ClassEngagementSettings.objects.get_or_create(
            school_class=sc, defaults={"is_active": False},
        )
        eng.lesson_comments_enabled = request.POST.get("enabled") == "1"
        eng.save(update_fields=["lesson_comments_enabled", "updated_at"])
        comment_services._audit(
            actor=request.user,
            action=(
                "lesson.comments.enabled"
                if eng.lesson_comments_enabled else "lesson.comments.disabled"
            ),
            obj=eng,
            summary={"school_class": sc.name},
            request=request,
        )
        messages.success(
            request,
            f"Lesson questions are now {'on' if eng.lesson_comments_enabled else 'off'} "
            f"for {sc.name}.",
        )
        return _term_redirect(sc, subj, _get_term(request))

    def get(self, request, *args, **kwargs):
        raise Http404


class LessonCommentCreateView(StudentOrParentMixin, LearningEnabledMixin, View):
    """POST-only: a student asks a question under a lesson."""

    def post(self, request, class_id, subject_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        if request.user.role == "parent":
            raise PermissionDenied("Parents cannot write lesson comments.")
        student = _resolve_student(request)
        lesson = course_selectors.lesson_detail_for_student(
            lesson_id=lesson_id, student=student
        )
        if lesson is None or (
            lesson.module.school_class_id != class_id
            or lesson.module.subject_id != subject_id
        ):
            raise Http404
        try:
            comment_services.add_comment(
                lesson=lesson, student=student,
                body=request.POST.get("body", ""),
                request=request,
            )
            messages.success(request, "Your question was posted.")
        except PermissionDenied:
            raise
        except _ValidationError as exc:
            messages.error(request, exc.message)
        return redirect(
            "learning:lesson_view",
            class_id=class_id, subject_id=subject_id, lesson_id=lesson_id,
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class LessonCommentRemoveView(TeacherRequiredMixin, LearningEnabledMixin, View):
    """POST-only: teacher moderates a lesson comment (soft delete, audited)."""

    def post(self, request, class_id, subject_id, lesson_id, comment_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        comment = get_object_or_404(
            LessonComment.objects.select_related("lesson__module"),
            pk=comment_id,
            lesson_id=lesson_id,
            lesson__module__school_class=sc,
            lesson__module__subject=subj,
        )
        comment_services.remove_comment(comment=comment, actor=request.user, request=request)
        messages.success(request, "Comment removed.")
        return redirect(
            "learning:lesson_preview",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=comment.lesson.module_id, lesson_id=lesson_id,
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class LessonCommentRestoreView(TeacherRequiredMixin, LearningEnabledMixin, View):
    """POST-only: undo a removal."""

    def post(self, request, class_id, subject_id, lesson_id, comment_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        comment = get_object_or_404(
            LessonComment.objects.select_related("lesson__module"),
            pk=comment_id,
            lesson_id=lesson_id,
            lesson__module__school_class=sc,
            lesson__module__subject=subj,
        )
        comment_services.restore_comment(comment=comment, actor=request.user, request=request)
        messages.success(request, "Comment restored.")
        return redirect(
            "learning:lesson_preview",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=comment.lesson.module_id, lesson_id=lesson_id,
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


class LessonAttachmentFileView(RoleRequiredMixin, LearningEnabledMixin, TemplateView):
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
        students = list(_students_for_targeting(sc))
        selected_id = request.GET.get("student_id")
        preview_student = next(
            (s for s in students if str(s.pk) == selected_id), None,
        )
        if selected_id and preview_student is None:
            raise Http404("Student not found in this class.")
        visible = not selected_id or bool(
            preview_student
            and course_selectors.lesson_detail_for_student(
                lesson_id=lesson.pk, student=preview_student,
            )
        )
        if selected_id and not visible:
            raise Http404("This lesson is not available to the selected student.")
        from .markdown_utils import render_notes
        from .video_utils import parse_video_url
        from . import live_class_selectors
        return self.render_to_response({
            "lesson": lesson,
            "preview_students": students,
            "preview_student": preview_student,
            "preview_visible": visible,
            "module": module,
            "school_class": sc,
            "subject": subj,
            "attachments": list(lesson.lesson_attachments.all()) if visible else [],
            "is_preview": True,
            "completed": False,
            "rendered_notes": render_notes(lesson.notes, notes_format=lesson.notes_format) if visible else "",
            "video_info": parse_video_url(lesson.video_url) if visible else None,
            "recordings": live_class_selectors.recordings_for_lesson(lesson) if visible else [],
            "next_lp": None,
            "prev_lp": None,
            "student_note": None,
            # Release state and targeting, so the preview banner can warn the
            # teacher about what students will (not) see right now.
            "preview_lesson_status": release.status_label(lesson),
            "preview_module_status": release.status_label(module),
            "preview_assign_summary": (
                "Whole class"
                if lesson.assign_all
                else f"{lesson.assigned_students.count()} chosen student(s)"
            ),
            "comments_enabled": comment_services.comments_enabled(sc),
            "comments": list(
                lesson.comments.select_related("student__user").order_by("created_at", "id")
            ) if visible else [],
            "can_moderate": True,
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

        from . import live_class_selectors
        recordings = live_class_selectors.recordings_for_lesson(lesson)

        # Lesson comments (class-level switch, default off).  Parents read,
        # students write; removed comments never render.
        comments_enabled = comment_services.comments_enabled(
            lesson.module.school_class
        )
        comments = (
            comment_services.visible_comments(lesson=lesson)
            if comments_enabled else []
        )

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
            "recordings": recordings,
            "comments_enabled": comments_enabled,
            "comments": comments,
            "can_comment": self.request.user.role == "student",
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

