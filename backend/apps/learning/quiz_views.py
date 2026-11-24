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

from django.core.exceptions import ValidationError as _ValidationError
from . import course_selectors
from .models import Lesson

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


# ── Teacher quiz builder (Checkpoints C & Q1) ──────────────────────────────


def _parse_nairobi_dt(dt_str: str):
    """Parse an ISO/HTML datetime-local string into a Nairobi timezone-aware datetime."""
    if not dt_str or not dt_str.strip():
        return None
    from datetime import datetime
    import zoneinfo
    nairobi_tz = zoneinfo.ZoneInfo("Africa/Nairobi")
    try:
        dt = datetime.fromisoformat(dt_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=nairobi_tz)
        return dt
    except (ValueError, TypeError):
        return None


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
            "quiz_types": Quiz.QuizType.choices,
            "visibility_choices": Quiz.AnswerVisibility.choices,
            "policy_choices": Quiz.GradingPolicy.choices,
            "students": _students_for_targeting(sc),
        })

    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson = self._base(
            request, class_id, subject_id, module_id, lesson_id
        )
        try:
            _schedule_from_post(request)
            quiz_type = request.POST.get("quiz_type", Quiz.QuizType.LESSON_CHECK)
            open_dt = _parse_nairobi_dt(request.POST.get("open_date", ""))
            close_dt = _parse_nairobi_dt(request.POST.get("close_date", ""))

            pass_mark_raw = request.POST.get("pass_mark", "").strip()
            attempts_raw = request.POST.get("attempts_allowed", "").strip()

            quiz = quiz_services.create_quiz(
                lesson=lesson,
                title=request.POST.get("title", ""),
                quiz_type=quiz_type,
                instructions=request.POST.get("instructions", ""),
                pass_mark=int(pass_mark_raw) if pass_mark_raw else None,
                attempts_allowed=int(attempts_raw) if attempts_raw else None,
                time_limit_minutes=int(t) if (t := request.POST.get("time_limit_minutes", "").strip()) else None,
                shuffle_questions=request.POST.get("shuffle_questions") == "1",
                shuffle_choices=request.POST.get("shuffle_choices") == "1",
                show_correct_after=request.POST.get("show_correct_after") == "1",
                answer_visibility=request.POST.get("answer_visibility", Quiz.AnswerVisibility.AFTER_SUBMIT),
                open_date=open_dt,
                close_date=close_dt,
                grading_policy=request.POST.get("grading_policy", Quiz.GradingPolicy.BEST),
                actor=request.user,
            )
            _apply_quiz_release_and_targeting(request, quiz)
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
            "quiz_types": Quiz.QuizType.choices,
            "visibility_choices": Quiz.AnswerVisibility.choices,
            "policy_choices": Quiz.GradingPolicy.choices,
            "students": _students_for_targeting(sc),
            "assigned_student_ids": list(quiz.assigned_students.values_list("pk", flat=True)),
        })

    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson, quiz = self._get(
            request, class_id, subject_id, module_id, lesson_id
        )
        try:
            _schedule_from_post(request)
            open_dt = _parse_nairobi_dt(request.POST.get("open_date", ""))
            close_dt = _parse_nairobi_dt(request.POST.get("close_date", ""))

            quiz_services.update_quiz_settings(
                quiz=quiz,
                title=request.POST.get("title", ""),
                quiz_type=request.POST.get("quiz_type", quiz.quiz_type),
                instructions=request.POST.get("instructions", ""),
                pass_mark=int(request.POST.get("pass_mark", 50)),
                attempts_allowed=int(request.POST.get("attempts_allowed", 1)),
                time_limit_minutes=int(t) if (t := request.POST.get("time_limit_minutes", "").strip()) else None,
                shuffle_questions=request.POST.get("shuffle_questions") == "1",
                shuffle_choices=request.POST.get("shuffle_choices") == "1",
                show_correct_after=request.POST.get("show_correct_after") == "1",
                answer_visibility=request.POST.get("answer_visibility", quiz.answer_visibility),
                open_date=open_dt,
                close_date=close_dt,
                grading_policy=request.POST.get("grading_policy", quiz.grading_policy),
                answers_released=request.POST.get("answers_released") == "1",
                counts_for_gradebook=request.POST.get("counts_for_gradebook") == "1",
                actor=request.user,
            )
            _apply_quiz_release_and_targeting(request, quiz)
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
            elif action == "release_answers":
                quiz.answers_released = True
                quiz.save(update_fields=["answers_released", "updated_at"])
                messages.success(request, "Quiz answers and explanations released to students.")
            elif action == "duplicate":
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
            q_type = request.POST.get("question_type", QuizQuestion.QuestionType.MULTIPLE_CHOICE)
            points = int(request.POST.get("points", 1) or 1)
            explanation = request.POST.get("explanation", "")
            partial_credit = request.POST.get("partial_credit") == "1"
            allow_minor_typos = request.POST.get("allow_minor_typos") == "1"
            image = request.FILES.get("image")

            raw_accepted = request.POST.get("accepted_answers", "")
            accepted_answers = [a.strip() for a in re.split(r"[\n;,]+", raw_accepted) if a.strip()]
            tf_correct = request.POST.get("tf_correct", "True")

            q = quiz_services.add_question(
                quiz=quiz,
                body=request.POST.get("body", ""),
                question_type=q_type,
                points=points,
                image=image,
                partial_credit=partial_credit,
                accepted_answers=accepted_answers,
                allow_minor_typos=allow_minor_typos,
                explanation=explanation,
                tf_correct_choice=tf_correct,
                actor=request.user,
            )

            if q_type == QuizQuestion.QuestionType.MULTIPLE_CHOICE:
                choice_texts = request.POST.getlist("choice_text")
                correct_idx = request.POST.get("correct_choice", "0")
                for i, text in enumerate(choice_texts):
                    if text.strip():
                        quiz_services.add_choice(
                            question=q, text=text,
                            is_correct=(str(i) == str(correct_idx)),
                            actor=request.user,
                        )
            elif q_type == QuizQuestion.QuestionType.SELECT_ALL:
                choice_texts = request.POST.getlist("choice_text")
                correct_indices = set(request.POST.getlist("correct_choices"))
                for i, text in enumerate(choice_texts):
                    if text.strip():
                        quiz_services.add_choice(
                            question=q, text=text,
                            is_correct=(str(i) in correct_indices),
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
            "accepted_answers_str": "\n".join(question.accepted_answers),
        })

    def post(self, request, class_id, subject_id, module_id, lesson_id, question_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson, quiz, question = self._get(
            request, class_id, subject_id, module_id, lesson_id, question_id
        )
        try:
            points = int(request.POST.get("points", question.points) or 1)
            explanation = request.POST.get("explanation", "")
            partial_credit = request.POST.get("partial_credit") == "1"
            allow_minor_typos = request.POST.get("allow_minor_typos") == "1"
            image = request.FILES.get("image")

            raw_accepted = request.POST.get("accepted_answers", "")
            accepted_answers = [a.strip() for a in re.split(r"[\n;,]+", raw_accepted) if a.strip()]

            quiz_services.update_question(
                question=question,
                body=request.POST.get("body", ""),
                points=points,
                image=image,
                partial_credit=partial_credit,
                accepted_answers=accepted_answers,
                allow_minor_typos=allow_minor_typos,
                explanation=explanation,
                actor=request.user,
            )

            # Update choices
            choice_pks = request.POST.getlist("choice_pk")
            choice_texts = request.POST.getlist("choice_text")

            if question.question_type == QuizQuestion.QuestionType.SELECT_ALL:
                correct_pks = set(request.POST.getlist("correct_choice_pks"))
                for pk, text in zip(choice_pks, choice_texts):
                    try:
                        c = QuizChoice.objects.get(pk=int(pk), question=question)
                        quiz_services.update_choice(
                            choice=c, text=text,
                            is_correct=(str(pk) in correct_pks),
                            actor=request.user,
                        )
                    except QuizChoice.DoesNotExist:
                        pass
            else:
                correct_pk = request.POST.get("correct_choice_pk", "")
                for pk, text in zip(choice_pks, choice_texts):
                    try:
                        c = QuizChoice.objects.get(pk=int(pk), question=question)
                        quiz_services.update_choice(
                            choice=c, text=text,
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


class TeacherDuplicateQuestionView(
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
            quiz_services.duplicate_question(question=question, actor=request.user)
            messages.success(request, "Question duplicated.")
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


class TeacherQuizFastEntryView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    template_name = "features/learning/quiz_fast_entry.html"

    def _get(self, request, class_id, subject_id, module_id, lesson_id):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(CourseModule, pk=module_id, school_class=sc, subject=subj)
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
            "pasted_text": "", "parsed_questions": [], "parse_errors": [],
            "is_locked": quiz.has_submitted_attempts,
        })

    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson, quiz = self._get(
            request, class_id, subject_id, module_id, lesson_id
        )
        pasted_text = request.POST.get("pasted_text", "")
        action = request.POST.get("action", "preview")

        parsed_questions, parse_errors = quiz_services.parse_pasted_questions(pasted_text)

        if action == "save" and not parse_errors and parsed_questions:
            try:
                created = quiz_services.bulk_create_questions(
                    quiz=quiz, questions_data=parsed_questions, actor=request.user
                )
                messages.success(request, f"Successfully added {len(created)} questions.")
                return redirect(
                    "learning:teacher_quiz",
                    class_id=sc.pk, subject_id=subj.pk,
                    module_id=module.pk, lesson_id=lesson.pk,
                )
            except (QuizLockedError, _ValidationError) as exc:
                messages.error(request, str(exc))

        return self.render_to_response({
            "school_class": sc, "subject": subj, "module": module,
            "lesson": lesson, "quiz": quiz,
            "pasted_text": pasted_text,
            "parsed_questions": parsed_questions,
            "parse_errors": parse_errors,
            "is_locked": quiz.has_submitted_attempts,
        })


class TeacherQuizCsvTemplateView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, View
):
    def get(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        csv_data = quiz_services.generate_csv_template()
        response = HttpResponse(csv_data, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="quiz_questions_template.csv"'
        return response


class TeacherQuizCsvImportView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(CourseModule, pk=module_id, school_class=sc, subject=subj)
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        quiz = get_object_or_404(Quiz, lesson=lesson)

        csv_file = request.FILES.get("csv_file")
        if not csv_file:
            messages.error(request, "Please choose a CSV file to upload.")
            return redirect(
                "learning:teacher_quiz",
                class_id=sc.pk, subject_id=subj.pk,
                module_id=module.pk, lesson_id=lesson.pk,
            )

        count, errors = quiz_services.import_questions_from_csv(
            quiz=quiz, csv_file=csv_file, actor=request.user
        )
        if errors:
            messages.error(request, "CSV import errors: " + " | ".join(errors[:5]))
        else:
            messages.success(request, f"Imported {count} questions from CSV.")

        return redirect(
            "learning:teacher_quiz",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=module.pk, lesson_id=lesson.pk,
        )


class TeacherQuizRegradeView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    template_name = "features/learning/quiz_regrade.html"

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
        submitted_count = quiz.attempts.filter(submitted_at__isnull=False).count()
        return self.render_to_response({
            "school_class": sc, "subject": subj, "module": module,
            "lesson": lesson, "quiz": quiz, "question": question,
            "choices": choices, "submitted_count": submitted_count,
            "accepted_answers_str": "\n".join(question.accepted_answers),
        })

    def post(self, request, class_id, subject_id, module_id, lesson_id, question_id, *args, **kwargs):
        from django.shortcuts import redirect
        sc, subj, module, lesson, quiz, question = self._get(
            request, class_id, subject_id, module_id, lesson_id, question_id
        )
        reason = request.POST.get("reason", "").strip()
        if not reason:
            messages.error(request, "A reason is required to regrade submissions.")
            return redirect(request.path)

        correct_choice_pks = None
        if question.question_type in (QuizQuestion.QuestionType.MULTIPLE_CHOICE, QuizQuestion.QuestionType.TRUE_FALSE):
            chosen = request.POST.get("correct_choice_pk")
            if chosen:
                correct_choice_pks = [int(chosen)]
        elif question.question_type == QuizQuestion.QuestionType.SELECT_ALL:
            correct_choice_pks = [int(pk) for pk in request.POST.getlist("correct_choice_pks")]

        accepted_answers = None
        if question.question_type == QuizQuestion.QuestionType.FILL_BLANK:
            raw_text = request.POST.get("accepted_answers", "")
            accepted_answers = [a.strip() for a in re.split(r"[\n;,]+", raw_text) if a.strip()]

        try:
            res = quiz_services.fix_answer_and_regrade(
                quiz=quiz,
                question_id=question.pk,
                correct_choice_pks=correct_choice_pks,
                accepted_answers=accepted_answers,
                reason=reason,
                actor=request.user,
            )
            messages.success(
                request,
                f"Answer key updated. Regraded {res['attempts_regraded']} attempts "
                f"({res['scores_changed']} scores changed, {res['newly_passed']} newly passed)."
            )
        except (_ValidationError, Exception) as exc:
            messages.error(request, str(exc))

        return redirect(
            "learning:teacher_quiz",
            class_id=sc.pk, subject_id=subj.pk,
            module_id=module.pk, lesson_id=lesson.pk,
        )


class TeacherQuizPreviewView(
    TeacherRequiredMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
    template_name = "features/learning/quiz_take.html"

    def get(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        sc, subj = _get_teacher_assignment(request, class_id, subject_id)
        module = get_object_or_404(CourseModule, pk=module_id, school_class=sc, subject=subj)
        lesson = get_object_or_404(Lesson, pk=lesson_id, module=module)
        quiz = get_object_or_404(Quiz, lesson=lesson)
        if request.GET.get("student_id"):
            preview_student = next(
                (stu for stu in _students_for_targeting(sc)
                 if str(stu.pk) == request.GET["student_id"]), None,
            )
            if preview_student is None or not course_selectors.quiz_visible_to_student(quiz, preview_student):
                raise Http404("This quiz is not available to this student.")

        teacher_questions = quiz_selectors.quiz_questions_for_teacher(quiz=quiz)
        questions = []
        for qwc in teacher_questions:
            choices = [
                quiz_selectors.ChoiceData(pk=c.pk, text=c.text, is_correct=None)
                for c in qwc.choices
            ]
            questions.append(
                quiz_selectors.QuestionWithChoices(
                    question=qwc.question,
                    choices=choices,
                )
            )

        time_remaining = (quiz.time_limit_minutes * 60) if quiz.time_limit_minutes else None
        return self.render_to_response({
            "school_class": sc, "subject": subj,
            "module": module, "lesson": lesson,
            "quiz": quiz, "questions": questions,
            "time_remaining": time_remaining,
            "is_preview": True,
            "save_answer_url": "#",
        })

    def post(self, request, class_id, subject_id, module_id, lesson_id, *args, **kwargs):
        messages.info(request, "Preview mode complete. No student data was altered.")
        from django.shortcuts import redirect
        return redirect(
            "learning:teacher_quiz",
            class_id=class_id, subject_id=subject_id,
            module_id=module_id, lesson_id=lesson_id,
        )


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
        _assert_quiz_reachable_for_student(quiz=quiz, student=student)
        summary = quiz_selectors.student_attempt_summary(quiz=quiz, student=student)
        ctx.update({
            "school_class": sc, "subject": subj,
            "lesson": lesson, "quiz": quiz,
            "student": student, "summary": summary,
            "is_parent": self.request.user.role == "parent",
        })
        return ctx


class StudentTakeQuizView(
    StudentOrParentMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
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
            module__subject=subj,
            module__is_published=True,
            is_published=True,
        )
        if lesson.module.school_class_id != student.current_class_id:
            raise Http404
        quiz = get_object_or_404(Quiz, lesson=lesson, is_published=True)
        _assert_quiz_reachable_for_student(quiz=quiz, student=student)
        return student, sc, subj, lesson, quiz

    def get(self, request, class_id, subject_id, lesson_id, *args, **kwargs):
        if request.user.role == "parent":
            raise PermissionDenied("Parents cannot take quizzes.")
        student, sc, subj, lesson, quiz = self._get_quiz_and_student(
            request, class_id, subject_id, lesson_id
        )
        try:
            attempt = quiz_services.start_or_resume_attempt(quiz=quiz, student=student)
        except _ValidationError as exc:
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
        questions = quiz_selectors.quiz_questions_for_student(
            quiz=quiz, attempt=attempt
        )
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

        # Fallback to save any choices or text submitted in POST
        for q in quiz.questions.all():
            q_type = q.question_type
            if q_type in (QuizQuestion.QuestionType.MULTIPLE_CHOICE, QuizQuestion.QuestionType.TRUE_FALSE):
                c_id_raw = request.POST.get(f"answer_{q.pk}")
                if c_id_raw:
                    try:
                        quiz_services.save_answer(attempt=attempt, question_id=q.pk, choice_id=int(c_id_raw))
                    except (ValueError, TypeError):
                        pass
            elif q_type == QuizQuestion.QuestionType.SELECT_ALL:
                c_ids_raw = request.POST.getlist(f"answer_{q.pk}")
                choice_ids = [int(cid) for cid in c_ids_raw if cid.isdigit()]
                quiz_services.save_answer(attempt=attempt, question_id=q.pk, choice_ids=choice_ids)
            elif q_type in (QuizQuestion.QuestionType.FILL_BLANK, QuizQuestion.QuestionType.SHORT_ANSWER):
                text_ans = request.POST.get(f"answer_{q.pk}")
                if text_ans is not None:
                    quiz_services.save_answer(attempt=attempt, question_id=q.pk, answer_text=text_ans)

        attempt = quiz_services.submit_attempt(attempt=attempt, student=student)
        return redirect(
            "learning:quiz_results",
            class_id=sc.pk, subject_id=subj.pk,
            lesson_id=lesson.pk, attempt_id=attempt.pk,
        )


class SaveQuizAnswerView(StudentOrParentMixin, LearningEnabledMixin, QuizEnabledMixin, View):
    def post(self, request, class_id, subject_id, lesson_id, *args, **kwargs):
        if request.user.role == "parent":
            return JsonResponse({"ok": False, "error": "Parents cannot answer quizzes."}, status=403)
        student = _resolve_student(request)
        from apps.academics.models import SchoolClass, Subject
        sc = get_object_or_404(SchoolClass, pk=class_id)
        subj = get_object_or_404(Subject, pk=subject_id)
        lesson = get_object_or_404(
            Lesson, pk=lesson_id,
            module__school_class=sc, module__subject=subj, is_published=True,
        )
        if lesson.module.school_class_id != student.current_class_id:
            return JsonResponse({"ok": False, "error": "Not your class."}, status=403)
        quiz = get_object_or_404(Quiz, lesson=lesson, is_published=True)
        _assert_quiz_reachable_for_student(quiz=quiz, student=student)
        attempt = QuizAttempt.objects.filter(
            quiz=quiz, student=student, submitted_at__isnull=True
        ).first()
        if not attempt:
            return JsonResponse({"ok": False, "error": "No active attempt."})

        try:
            question_id = int(request.POST.get("question_id", 0))
            choice_id_raw = request.POST.get("choice_id", "")
            choice_id = int(choice_id_raw) if choice_id_raw else None

            has_choice_ids = "choice_ids[]" in request.POST or "choice_ids" in request.POST
            choice_ids_raw = request.POST.getlist("choice_ids[]") or request.POST.getlist("choice_ids")
            if has_choice_ids or (not choice_id_raw and answer_text is None):
                choice_ids = [int(cid) for cid in choice_ids_raw if cid.isdigit()]
            else:
                choice_ids = None

            answer_text = request.POST.get("answer_text")
        except (ValueError, TypeError):
            return JsonResponse({"ok": False, "error": "Invalid data."})

        quiz_services.save_answer(
            attempt=attempt,
            question_id=question_id,
            choice_id=choice_id,
            choice_ids=choice_ids,
            answer_text=answer_text,
        )
        return JsonResponse({"ok": True, "saved": question_id})

    def get(self, request, *args, **kwargs):
        raise Http404


class StudentQuizResultsView(
    StudentOrParentMixin, LearningEnabledMixin, QuizEnabledMixin, TemplateView
):
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
        if not course_selectors.quiz_visible_to_student(quiz, student):
            raise Http404
        attempt = get_object_or_404(
            QuizAttempt, pk=self.kwargs["attempt_id"],
            quiz=quiz, student=student,
        )
        if not attempt.is_submitted:
            raise Http404

        show_correct = attempt.is_submitted and quiz.can_view_answers_now
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
