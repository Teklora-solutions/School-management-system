"""
apps/learning/quiz_services.py
--------------------------------
Write-side logic for the quiz system (Checkpoint C).

Public API
----------
Teacher operations:
  create_quiz(lesson, title, ...)                → Quiz
  update_quiz_settings(quiz, *, actor, **fields) → Quiz
  add_question(quiz, body, question_type, ...)   → QuizQuestion
  update_question(question, *, actor, **fields)  → QuizQuestion  [edit-locked]
  delete_question(question, actor)               → None          [edit-locked]
  add_choice(question, text, is_correct, ...)    → QuizChoice    [edit-locked]
  update_choice(choice, *, actor, **fields)      → QuizChoice    [edit-locked]
  delete_choice(choice, actor)                   → None          [edit-locked]
  move_question(question, direction, actor)      → None
  duplicate_quiz(quiz, actor)                    → Quiz  (new draft copy)
  publish_quiz(quiz, actor)                      → Quiz
  unpublish_quiz(quiz, actor)                    → Quiz

Student operations:
  start_or_resume_attempt(quiz, student)         → QuizAttempt
  save_answer(attempt, question_id, choice_id)   → QuizAttemptAnswer
  submit_attempt(attempt, student)               → QuizAttempt  (marked)
  auto_submit_timed_out(attempt)                 → QuizAttempt  (server-side)

Design rules enforced here:
  - Edit-lock: questions and choices cannot be mutated once any student has
    a *submitted* attempt. QuizLockedError is raised; the view shows the
    teacher a clear message and a "Duplicate quiz" button.
  - One in-progress attempt per student per quiz (enforced by start_or_resume).
  - Attempt limits enforced before starting a new attempt.
  - Marking is 100% server-side. is_correct on QuizAttemptAnswer is set only
    inside submit_attempt(), never in a view or template.
  - Time limit is server-clock-based. submit_attempt() refuses to accept answers
    arriving after the clock has expired (auto_submit_timed_out handles that).
  - Shuffle seeds are stored in attempt.question_order on start, so resumed
    attempts always show the same question order.
"""

from __future__ import annotations

import logging
import random

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    Quiz,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizChoice,
    QuizQuestion,
)

logger = logging.getLogger(__name__)

MAX_QUESTIONS = 20


class QuizLockedError(Exception):
    """Raised when a teacher tries to edit a quiz that has submitted attempts."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _assert_not_locked(quiz: Quiz) -> None:
    if quiz.has_submitted_attempts:
        raise QuizLockedError(
            "This quiz has already been taken by students. "
            "You cannot change questions or answers after submissions exist. "
            "Use 'Duplicate quiz' to create an editable copy."
        )


def _renumber_questions(quiz_id: int) -> None:
    qs = QuizQuestion.objects.filter(quiz_id=quiz_id).order_by("position", "id")
    for i, q in enumerate(qs):
        if q.position != i:
            q.position = i
            q.save(update_fields=["position"])


def _renumber_choices(question_id: int) -> None:
    qs = QuizChoice.objects.filter(question_id=question_id).order_by("position", "id")
    for i, c in enumerate(qs):
        if c.position != i:
            c.position = i
            c.save(update_fields=["position"])


# ---------------------------------------------------------------------------
# Teacher — quiz settings
# ---------------------------------------------------------------------------


@transaction.atomic
def create_quiz(
    *,
    lesson,
    title: str,
    instructions: str = "",
    pass_mark: int = 50,
    attempts_allowed: int = 1,
    time_limit_minutes: int | None = None,
    shuffle_questions: bool = False,
    shuffle_choices: bool = False,
    show_correct_after: bool = True,
    actor,
) -> Quiz:
    if hasattr(lesson, "quiz"):
        raise ValidationError("This lesson already has a quiz.")
    title = title.strip()
    if not title:
        raise ValidationError("Quiz title cannot be empty.")
    return Quiz.objects.create(
        lesson=lesson,
        title=title,
        instructions=instructions.strip(),
        pass_mark=max(0, min(100, pass_mark)),
        attempts_allowed=attempts_allowed,
        time_limit_minutes=time_limit_minutes or None,
        shuffle_questions=shuffle_questions,
        shuffle_choices=shuffle_choices,
        show_correct_after=show_correct_after,
        is_published=False,
    )


@transaction.atomic
def update_quiz_settings(
    *,
    quiz: Quiz,
    title: str,
    instructions: str,
    pass_mark: int,
    attempts_allowed: int,
    time_limit_minutes: int | None,
    shuffle_questions: bool,
    shuffle_choices: bool,
    show_correct_after: bool,
    counts_for_gradebook: bool,
    actor,
) -> Quiz:
    if quiz.counts_for_gradebook != counts_for_gradebook and (
        quiz.counts_for_gradebook or counts_for_gradebook
    ):
        from apps.gradebook.services import ensure_tick_writable

        ensure_tick_writable(quiz.lesson.module.term)

    quiz.title = title.strip() or quiz.title
    quiz.instructions = instructions.strip()
    quiz.pass_mark = max(0, min(100, pass_mark))
    quiz.attempts_allowed = attempts_allowed
    quiz.time_limit_minutes = time_limit_minutes or None
    quiz.shuffle_questions = shuffle_questions
    quiz.shuffle_choices = shuffle_choices
    quiz.show_correct_after = show_correct_after
    quiz.counts_for_gradebook = counts_for_gradebook
    quiz.save()
    return quiz


@transaction.atomic
def publish_quiz(*, quiz: Quiz, actor) -> Quiz:
    if quiz.question_count == 0:
        raise ValidationError("Add at least one question before publishing.")
    quiz.is_published = True
    quiz.save(update_fields=["is_published", "updated_at"])
    return quiz


@transaction.atomic
def unpublish_quiz(*, quiz: Quiz, actor) -> Quiz:
    quiz.is_published = False
    quiz.save(update_fields=["is_published", "updated_at"])
    return quiz


# ---------------------------------------------------------------------------
# Teacher — questions
# ---------------------------------------------------------------------------


@transaction.atomic
def add_question(
    *,
    quiz: Quiz,
    body: str,
    question_type: str = QuizQuestion.QuestionType.MULTIPLE_CHOICE,
    explanation: str = "",
    actor,
) -> QuizQuestion:
    _assert_not_locked(quiz)
    if quiz.question_count >= MAX_QUESTIONS:
        raise ValidationError(f"A quiz may have at most {MAX_QUESTIONS} questions.")
    body = body.strip()
    if not body:
        raise ValidationError("Question body cannot be empty.")
    last = (
        QuizQuestion.objects.filter(quiz=quiz)
        .order_by("-position")
        .values_list("position", flat=True)
        .first()
    )
    q = QuizQuestion.objects.create(
        quiz=quiz,
        body=body,
        question_type=question_type,
        explanation=explanation.strip(),
        position=(last + 1) if last is not None else 0,
    )
    # Auto-generate True/False choices
    if question_type == QuizQuestion.QuestionType.TRUE_FALSE:
        QuizChoice.objects.create(question=q, text="True",  is_correct=True,  position=0)
        QuizChoice.objects.create(question=q, text="False", is_correct=False, position=1)
    return q


@transaction.atomic
def update_question(
    *,
    question: QuizQuestion,
    body: str,
    explanation: str,
    actor,
) -> QuizQuestion:
    _assert_not_locked(question.quiz)
    body = body.strip()
    if not body:
        raise ValidationError("Question body cannot be empty.")
    question.body = body
    question.explanation = explanation.strip()
    question.save(update_fields=["body", "explanation", "updated_at"])
    return question


@transaction.atomic
def delete_question(*, question: QuizQuestion, actor) -> None:
    _assert_not_locked(question.quiz)
    quiz_id = question.quiz_id
    question.delete()
    _renumber_questions(quiz_id)


@transaction.atomic
def move_question(*, question: QuizQuestion, direction: str, actor) -> None:
    siblings = list(
        QuizQuestion.objects.filter(quiz=question.quiz).order_by("position", "id")
    )
    idx = next((i for i, q in enumerate(siblings) if q.pk == question.pk), None)
    if idx is None:
        return
    swap = idx - 1 if direction == "up" else idx + 1
    if swap < 0 or swap >= len(siblings):
        return
    a, b = siblings[idx], siblings[swap]
    a.position, b.position = b.position, a.position
    QuizQuestion.objects.bulk_update([a, b], ["position"])
    _renumber_questions(question.quiz_id)


# ---------------------------------------------------------------------------
# Teacher — choices
# ---------------------------------------------------------------------------


@transaction.atomic
def add_choice(
    *,
    question: QuizQuestion,
    text: str,
    is_correct: bool,
    actor,
) -> QuizChoice:
    _assert_not_locked(question.quiz)
    text = text.strip()
    if not text:
        raise ValidationError("Choice text cannot be empty.")
    if question.choices.count() >= 6:
        raise ValidationError("A question may have at most 6 choices.")
    last = (
        question.choices.order_by("-position")
        .values_list("position", flat=True)
        .first()
    )
    return QuizChoice.objects.create(
        question=question,
        text=text,
        is_correct=is_correct,
        position=(last + 1) if last is not None else 0,
    )


@transaction.atomic
def update_choice(
    *,
    choice: QuizChoice,
    text: str,
    is_correct: bool,
    actor,
) -> QuizChoice:
    _assert_not_locked(choice.question.quiz)
    text = text.strip()
    if not text:
        raise ValidationError("Choice text cannot be empty.")
    choice.text = text
    choice.is_correct = is_correct
    choice.save(update_fields=["text", "is_correct", "updated_at"])
    return choice


@transaction.atomic
def delete_choice(*, choice: QuizChoice, actor) -> None:
    _assert_not_locked(choice.question.quiz)
    question_id = choice.question_id
    choice.delete()
    _renumber_choices(question_id)


# ---------------------------------------------------------------------------
# Teacher — duplicate quiz
# ---------------------------------------------------------------------------


@transaction.atomic
def duplicate_quiz(*, quiz: Quiz, actor, target_lesson=None) -> Quiz:
    """
    Create a new draft copy of *quiz* with all its questions and choices.
    The copy has no attempts and is fully editable.

    ``target_lesson`` must be a different Lesson (the original lesson already
    has a quiz). If omitted the service raises ValidationError rather than
    silently creating an invalid record.
    """
    if target_lesson is None:
        raise ValidationError(
            "Provide a target lesson for the duplicate. "
            "The original lesson already has a quiz attached."
        )
    if target_lesson.pk == quiz.lesson_id:
        raise ValidationError("The duplicate must go on a different lesson.")
    if hasattr(target_lesson, "quiz"):
        raise ValidationError("That lesson already has a quiz.")

    new_quiz = Quiz.objects.create(
        lesson=target_lesson,
        title=f"Copy of {quiz.title}",
        instructions=quiz.instructions,
        pass_mark=quiz.pass_mark,
        attempts_allowed=quiz.attempts_allowed,
        time_limit_minutes=quiz.time_limit_minutes,
        shuffle_questions=quiz.shuffle_questions,
        shuffle_choices=quiz.shuffle_choices,
        show_correct_after=quiz.show_correct_after,
        is_published=False,
    )
    for q in quiz.questions.prefetch_related("choices").order_by("position"):
        new_q = QuizQuestion.objects.create(
            quiz=new_quiz,
            body=q.body,
            question_type=q.question_type,
            explanation=q.explanation,
            position=q.position,
        )
        for c in q.choices.order_by("position"):
            QuizChoice.objects.create(
                question=new_q,
                text=c.text,
                is_correct=c.is_correct,
                position=c.position,
            )
    logger.info("Quiz %s duplicated to %s by %s", quiz.pk, new_quiz.pk, actor)
    return new_quiz


# ---------------------------------------------------------------------------
# Student — attempt lifecycle
# ---------------------------------------------------------------------------


@transaction.atomic
def start_or_resume_attempt(*, quiz: Quiz, student) -> QuizAttempt:
    """
    Return the student's current in-progress attempt, or start a new one.

    Raises ValidationError when:
    - The quiz is not published.
    - The student has used all allowed attempts.
    """
    if not quiz.is_published:
        raise ValidationError("This quiz is not available.")

    # Resume any existing in-progress attempt
    in_progress = QuizAttempt.objects.filter(
        quiz=quiz, student=student, submitted_at__isnull=True
    ).first()
    if in_progress:
        # Auto-submit if timed out
        if in_progress.is_timed_out:
            return auto_submit_timed_out(in_progress)
        return in_progress

    # Count submitted attempts
    submitted_count = QuizAttempt.objects.filter(
        quiz=quiz, student=student, submitted_at__isnull=False
    ).count()

    limit = quiz.attempts_allowed
    if limit != Quiz.Attempts.UNLIMITED and submitted_count >= limit:
        raise ValidationError(
            f"You have used all {limit} attempt(s) for this quiz."
        )

    # Build question order (with shuffle if enabled)
    question_ids = list(
        QuizQuestion.objects.filter(quiz=quiz)
        .order_by("position", "id")
        .values_list("id", flat=True)
    )
    if quiz.shuffle_questions:
        random.shuffle(question_ids)

    attempt = QuizAttempt.objects.create(
        quiz=quiz,
        student=student,
        question_order=question_ids,
    )
    return attempt


@transaction.atomic
def save_answer(
    *,
    attempt: QuizAttempt,
    question_id: int,
    choice_id: int | None,
) -> QuizAttemptAnswer | None:
    """
    Save (or update) the student's answer for one question.

    Returns None silently when the attempt is already submitted or timed out,
    so a race condition between auto-submit and a late AJAX save is harmless.
    ``is_correct`` is NOT set here — only submit_attempt() sets it.
    """
    if attempt.is_submitted:
        return None
    if attempt.is_timed_out:
        auto_submit_timed_out(attempt)
        return None

    # Validate the question belongs to this quiz
    try:
        question = QuizQuestion.objects.get(pk=question_id, quiz=attempt.quiz)
    except QuizQuestion.DoesNotExist:
        return None

    # Validate the choice belongs to this question (if provided)
    choice = None
    if choice_id is not None:
        try:
            choice = QuizChoice.objects.get(pk=choice_id, question=question)
        except QuizChoice.DoesNotExist:
            return None

    answer, _ = QuizAttemptAnswer.objects.update_or_create(
        attempt=attempt,
        question=question,
        defaults={"selected_choice": choice},
    )
    return answer


@transaction.atomic
def submit_attempt(*, attempt: QuizAttempt, student) -> QuizAttempt:
    """
    Mark all answers and compute the score. Server-side only.

    Safety: if the attempt is already submitted, return it unchanged (idempotent).
    is_correct on QuizAttemptAnswer is set here and only here.
    """
    if attempt.is_submitted:
        return attempt
    if attempt.student_id != student.pk:
        raise PermissionError("You cannot submit another student's attempt.")

    # Auto-submit if already timed out
    if attempt.is_timed_out:
        return auto_submit_timed_out(attempt)

    return _mark_and_save(attempt, auto_submitted=False)


@transaction.atomic
def auto_submit_timed_out(attempt: QuizAttempt) -> QuizAttempt:
    """
    Server-side forced submission when the time limit has expired.
    Called transparently when the student loads the page after time is up.
    """
    if attempt.is_submitted:
        return attempt
    return _mark_and_save(attempt, auto_submitted=True)


def _mark_and_save(attempt: QuizAttempt, *, auto_submitted: bool) -> QuizAttempt:
    """
    Core marking logic. Runs inside an existing transaction.

    For each question, look up the student's selected_choice and compare
    is_correct. Score = correct / total * 100 (integer, rounded down).
    """
    questions = list(
        QuizQuestion.objects.filter(quiz=attempt.quiz)
        .prefetch_related("choices")
    )
    total = len(questions)
    correct_count = 0

    for q in questions:
        answer = (
            QuizAttemptAnswer.objects.filter(attempt=attempt, question=q)
            .select_related("selected_choice")
            .first()
        )
        if answer is None:
            # No answer saved — treat as wrong, create a blank record
            answer = QuizAttemptAnswer.objects.create(
                attempt=attempt,
                question=q,
                selected_choice=None,
                is_correct=False,
            )
        else:
            is_correct = bool(
                answer.selected_choice and answer.selected_choice.is_correct
            )
            answer.is_correct = is_correct
            answer.save(update_fields=["is_correct", "updated_at"])
            if is_correct:
                correct_count += 1

    score_pct = int(correct_count * 100 / total) if total > 0 else 0
    passed = score_pct >= attempt.quiz.pass_mark

    attempt.submitted_at = timezone.now()
    attempt.score_pct = score_pct
    attempt.passed = passed
    attempt.answers_auto_submitted = auto_submitted
    attempt.save(
        update_fields=[
            "submitted_at", "score_pct", "passed",
            "answers_auto_submitted", "updated_at",
        ]
    )

    # Award points (imported lazily to avoid circular imports)
    try:
        _award_quiz_points(attempt)
    except Exception:  # noqa: BLE001
        logger.warning("Points award failed for quiz attempt %s", attempt.pk)

    return attempt


def _award_quiz_points(attempt: QuizAttempt) -> None:
    """
    Award pts_quiz_attempt (first attempt only) and pts_quiz_pass (first pass).
    Uses existing ledger + idempotency keys so retakes never earn points again.
    """
    from .points_config import get_point_config
    from .points_services import award_points
    from .models import PointLedger

    cfg = get_point_config()
    student = attempt.student
    quiz = attempt.quiz

    # pts_quiz_attempt — first completed attempt only
    award_points(
        student=student,
        reason=PointLedger.Reason.BONUS,          # closest existing reason
        points=cfg.pts_quiz_attempt,
        description=f"Completed quiz: {quiz.title}",
        idempotency_key=f"quiz_attempt:{quiz.pk}:{student.pk}",
    )

    # pts_quiz_pass — first pass only
    if attempt.passed:
        award_points(
            student=student,
            reason=PointLedger.Reason.BONUS,
            points=cfg.pts_quiz_pass,
            description=f"Passed quiz: {quiz.title}",
            idempotency_key=f"quiz_pass:{quiz.pk}:{student.pk}",
        )
