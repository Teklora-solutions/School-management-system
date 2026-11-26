"""
apps/learning/quiz_services.py
--------------------------------
Write-side logic for the quiz system (Checkpoints C & Q1).

Public API
----------
Teacher operations:
  create_quiz(lesson, title, ...)                → Quiz
  update_quiz_settings(quiz, *, actor, **fields) → Quiz
  add_question(quiz, body, question_type, ...)   → QuizQuestion
  update_question(question, *, actor, **fields)  → QuizQuestion  [edit-locked]
  duplicate_question(question, actor)            → QuizQuestion  [edit-locked]
  delete_question(question, actor)               → None          [edit-locked]
  add_choice(question, text, is_correct, ...)    → QuizChoice    [edit-locked]
  update_choice(choice, *, actor, **fields)      → QuizChoice    [edit-locked]
  delete_choice(choice, actor)                   → None          [edit-locked]
  move_question(question, direction, actor)      → None
  duplicate_quiz(quiz, actor)                    → Quiz  (new draft copy)
  publish_quiz(quiz, actor)                      → Quiz
  unpublish_quiz(quiz, actor)                    → Quiz
  parse_pasted_questions(text)                   → (list[dict], list[str])
  bulk_create_questions(quiz, data, actor)       → list[QuizQuestion]
  generate_csv_template()                        → str
  import_questions_from_csv(quiz, file, actor)   → (int, list[str])
  fix_answer_and_regrade(quiz, question_id, ...) → dict

Student operations:
  start_or_resume_attempt(quiz, student)         → QuizAttempt
  save_answer(attempt, question_id, ...)         → QuizAttemptAnswer
  submit_attempt(attempt, student)               → QuizAttempt  (marked)
  auto_submit_timed_out(attempt)                 → QuizAttempt  (server-side)
"""

from __future__ import annotations

import csv
import io
import logging
import os
import random
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    Quiz,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizAuditLog,
    QuizChoice,
    QuizQuestion,
    QUIZ_QUESTION_IMAGE_ALLOWED_EXTENSIONS,
    QUIZ_QUESTION_IMAGE_MAX_BYTES,
)

logger = logging.getLogger(__name__)

MAX_QUESTIONS = 20


def max_questions_for_quiz(quiz: Quiz) -> int:
    """Return max questions allowed based on quiz type."""
    if quiz.quiz_type == Quiz.QuizType.FINAL_EXAM:
        return 100
    elif quiz.quiz_type == Quiz.QuizType.MODULE_QUIZ:
        return 50
    return 20


class QuizLockedError(Exception):
    """Raised when a teacher tries to edit a quiz that has submitted attempts."""


# ---------------------------------------------------------------------------
# Internal helpers & text normalization
# ---------------------------------------------------------------------------


def _assert_not_locked(quiz: Quiz) -> None:
    if quiz.has_submitted_attempts:
        raise QuizLockedError(
            "This quiz has already been taken by students. "
            "You cannot add, reorder, or delete questions after submissions exist. "
            "Use 'Fix answer key and regrade' to correct answers on existing questions, "
            "or 'Duplicate quiz' to create an editable copy."
        )


def _validate_question_image(uploaded_file) -> None:
    if not uploaded_file:
        return
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in QUIZ_QUESTION_IMAGE_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(QUIZ_QUESTION_IMAGE_ALLOWED_EXTENSIONS))
        raise ValidationError(f"Image type '{ext}' is not allowed. Allowed types: {allowed}")
    if uploaded_file.size > QUIZ_QUESTION_IMAGE_MAX_BYTES:
        mb = QUIZ_QUESTION_IMAGE_MAX_BYTES // (1024 * 1024)
        raise ValidationError(f"Image '{uploaded_file.name}' is too large. Maximum size is {mb} MB.")


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


def normalize_answer_text(text: str) -> str:
    """Lowercase, collapse multiple spaces, trim whitespace and edges."""
    if not text:
        return ""
    return " ".join(text.strip().lower().split())


def is_within_one_edit(s1: str, s2: str) -> bool:
    """True if Levenshtein distance between s1 and s2 is <= 1."""
    if abs(len(s1) - len(s2)) > 1:
        return False
    if s1 == s2:
        return True
    if len(s1) == len(s2):
        return sum(1 for a, b in zip(s1, s2) if a != b) == 1
    short, long_ = (s1, s2) if len(s1) < len(s2) else (s2, s1)
    i = j = diff = 0
    while i < len(short) and j < len(long_):
        if short[i] == long_[j]:
            i += 1
            j += 1
        else:
            diff += 1
            if diff > 1:
                return False
            j += 1
    return True


def match_accepted_answer(
    student_text: str, accepted_answers: list[str], allow_minor_typos: bool
) -> tuple[bool, str]:
    """
    Match student text against list of accepted answers.
    Returns (matched: bool, matched_target: str).
    """
    s_norm = normalize_answer_text(student_text)
    if not s_norm:
        return False, ""
    for accepted in accepted_answers:
        a_norm = normalize_answer_text(accepted)
        if not a_norm:
            continue
        if s_norm == a_norm:
            return True, accepted
        if allow_minor_typos and len(a_norm) >= 4 and is_within_one_edit(s_norm, a_norm):
            return True, accepted
    return False, ""


# ---------------------------------------------------------------------------
# Teacher — quiz settings
# ---------------------------------------------------------------------------


@transaction.atomic
def create_quiz(
    *,
    lesson,
    title: str,
    quiz_type: str = Quiz.QuizType.LESSON_CHECK,
    instructions: str = "",
    pass_mark: int | None = None,
    attempts_allowed: int | None = None,
    time_limit_minutes: int | None = None,
    shuffle_questions: bool = False,
    shuffle_choices: bool = False,
    show_correct_after: bool = True,
    answer_visibility: str | None = None,
    open_date = None,
    close_date = None,
    grading_policy: str = Quiz.GradingPolicy.BEST,
    actor,
) -> Quiz:
    if hasattr(lesson, "quiz"):
        raise ValidationError("This lesson already has a quiz.")
    title = title.strip()
    if not title:
        raise ValidationError("Quiz title cannot be empty.")

    # Apply quiz_type defaults if not explicitly given
    if quiz_type == Quiz.QuizType.FINAL_EXAM:
        attempts_allowed = attempts_allowed if attempts_allowed is not None else 1
        answer_visibility = answer_visibility or Quiz.AnswerVisibility.WHEN_RELEASED
        pass_mark = pass_mark if pass_mark is not None else 70
        show_correct_after = False
    elif quiz_type == Quiz.QuizType.MODULE_QUIZ:
        attempts_allowed = attempts_allowed if attempts_allowed is not None else 3
        answer_visibility = answer_visibility or Quiz.AnswerVisibility.AFTER_SUBMIT
        pass_mark = pass_mark if pass_mark is not None else 60
    else:  # LESSON_CHECK
        attempts_allowed = attempts_allowed if attempts_allowed is not None else Quiz.Attempts.UNLIMITED
        answer_visibility = answer_visibility or Quiz.AnswerVisibility.AFTER_SUBMIT
        pass_mark = pass_mark if pass_mark is not None else 50

    return Quiz.objects.create(
        lesson=lesson,
        title=title,
        quiz_type=quiz_type,
        instructions=instructions.strip(),
        pass_mark=max(0, min(100, pass_mark)),
        attempts_allowed=attempts_allowed,
        time_limit_minutes=time_limit_minutes or None,
        shuffle_questions=shuffle_questions,
        shuffle_choices=shuffle_choices,
        show_correct_after=show_correct_after,
        answer_visibility=answer_visibility,
        open_date=open_date,
        close_date=close_date,
        grading_policy=grading_policy,
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
    quiz_type: str = Quiz.QuizType.LESSON_CHECK,
    answer_visibility: str = Quiz.AnswerVisibility.AFTER_SUBMIT,
    open_date = None,
    close_date = None,
    grading_policy: str = Quiz.GradingPolicy.BEST,
    answers_released: bool = False,
    actor,
) -> Quiz:
    if quiz.counts_for_gradebook != counts_for_gradebook and (
        quiz.counts_for_gradebook or counts_for_gradebook
    ):
        from apps.gradebook.services import ensure_tick_writable
        ensure_tick_writable(quiz.lesson.module.term)

    quiz.title = title.strip() or quiz.title
    quiz.quiz_type = quiz_type
    quiz.instructions = instructions.strip()
    quiz.pass_mark = max(0, min(100, pass_mark))
    quiz.attempts_allowed = attempts_allowed
    quiz.time_limit_minutes = time_limit_minutes or None
    quiz.shuffle_questions = shuffle_questions
    quiz.shuffle_choices = shuffle_choices
    quiz.show_correct_after = show_correct_after
    quiz.answer_visibility = answer_visibility
    quiz.open_date = open_date
    quiz.close_date = close_date
    quiz.grading_policy = grading_policy
    quiz.answers_released = answers_released
    quiz.counts_for_gradebook = counts_for_gradebook
    quiz.save()

    if quiz.has_submitted_attempts:
        QuizAuditLog.objects.create(
            quiz=quiz,
            actor=actor if hasattr(actor, "pk") else None,
            action="settings_update",
            details={
                "title": quiz.title,
                "pass_mark": quiz.pass_mark,
                "attempts_allowed": quiz.attempts_allowed,
                "answer_visibility": quiz.answer_visibility,
                "answers_released": quiz.answers_released,
            },
            reason="Teacher updated quiz settings while submissions exist.",
        )
    return quiz


@transaction.atomic
def publish_quiz(*, quiz: Quiz, actor) -> Quiz:
    if quiz.question_count == 0:
        raise ValidationError("Add at least one question before publishing.")
    quiz.is_published = True
    quiz.save(update_fields=["is_published", "updated_at"])
    QuizAuditLog.objects.create(
        quiz=quiz,
        actor=actor if hasattr(actor, "pk") else None,
        action="publish",
        details={"question_count": quiz.question_count},
        reason="Quiz published",
    )
    return quiz


@transaction.atomic
def unpublish_quiz(*, quiz: Quiz, actor) -> Quiz:
    quiz.is_published = False
    quiz.save(update_fields=["is_published", "updated_at"])
    QuizAuditLog.objects.create(
        quiz=quiz,
        actor=actor if hasattr(actor, "pk") else None,
        action="unpublish",
        details={},
        reason="Quiz unpublished",
    )
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
    points: int = 1,
    image = None,
    partial_credit: bool = False,
    accepted_answers: list[str] | None = None,
    allow_minor_typos: bool = False,
    explanation: str = "",
    tf_correct_choice: str = "True",
    actor,
) -> QuizQuestion:
    _assert_not_locked(quiz)
    limit = max_questions_for_quiz(quiz)
    if quiz.question_count >= limit:
        raise ValidationError(f"A quiz may have at most {limit} questions.")
    body = body.strip()
    if not body:
        raise ValidationError("Question body cannot be empty.")
    if image:
        _validate_question_image(image)

    last = (
        QuizQuestion.objects.filter(quiz=quiz)
        .order_by("-position")
        .values_list("position", flat=True)
        .first()
    )
    cleaned_answers = [a.strip() for a in (accepted_answers or []) if a.strip()]

    q = QuizQuestion.objects.create(
        quiz=quiz,
        body=body,
        question_type=question_type,
        points=max(1, points),
        image=image,
        partial_credit=partial_credit,
        accepted_answers=cleaned_answers,
        allow_minor_typos=allow_minor_typos,
        explanation=explanation.strip(),
        position=(last + 1) if last is not None else 0,
    )

    # True/False choices
    if question_type == QuizQuestion.QuestionType.TRUE_FALSE:
        is_true_correct = (str(tf_correct_choice).strip().lower() == "true")
        QuizChoice.objects.create(question=q, text="True",  is_correct=is_true_correct, position=0)
        QuizChoice.objects.create(question=q, text="False", is_correct=(not is_true_correct), position=1)
    return q


@transaction.atomic
def update_question(
    *,
    question: QuizQuestion,
    body: str,
    points: int = 1,
    image = None,
    partial_credit: bool = False,
    accepted_answers: list[str] | None = None,
    allow_minor_typos: bool = False,
    explanation: str = "",
    actor,
) -> QuizQuestion:
    _assert_not_locked(question.quiz)
    body = body.strip()
    if not body:
        raise ValidationError("Question body cannot be empty.")
    if image:
        _validate_question_image(image)
        question.image = image

    cleaned_answers = [a.strip() for a in (accepted_answers or []) if a.strip()]
    question.body = body
    question.points = max(1, points)
    question.partial_credit = partial_credit
    question.accepted_answers = cleaned_answers
    question.allow_minor_typos = allow_minor_typos
    question.explanation = explanation.strip()
    question.save()
    return question


@transaction.atomic
def duplicate_question(*, question: QuizQuestion, actor) -> QuizQuestion:
    _assert_not_locked(question.quiz)
    limit = max_questions_for_quiz(question.quiz)
    if question.quiz.question_count >= limit:
        raise ValidationError(f"A quiz may have at most {limit} questions.")

    new_q = QuizQuestion.objects.create(
        quiz=question.quiz,
        body=f"{question.body} (copy)",
        question_type=question.question_type,
        points=question.points,
        image=question.image,
        partial_credit=question.partial_credit,
        accepted_answers=list(question.accepted_answers),
        allow_minor_typos=question.allow_minor_typos,
        explanation=question.explanation,
        position=question.position + 1,
    )
    for c in question.choices.order_by("position"):
        QuizChoice.objects.create(
            question=new_q,
            text=c.text,
            is_correct=c.is_correct,
            position=c.position,
        )
    _renumber_questions(question.quiz_id)
    return new_q


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
    if question.choices.count() >= 8:
        raise ValidationError("A question may have at most 8 choices.")
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
        quiz_type=quiz.quiz_type,
        instructions=quiz.instructions,
        pass_mark=quiz.pass_mark,
        attempts_allowed=quiz.attempts_allowed,
        time_limit_minutes=quiz.time_limit_minutes,
        shuffle_questions=quiz.shuffle_questions,
        shuffle_choices=quiz.shuffle_choices,
        show_correct_after=quiz.show_correct_after,
        answer_visibility=quiz.answer_visibility,
        grading_policy=quiz.grading_policy,
        is_published=False,
    )
    for q in quiz.questions.prefetch_related("choices").order_by("position"):
        new_q = QuizQuestion.objects.create(
            quiz=new_quiz,
            body=q.body,
            question_type=q.question_type,
            points=q.points,
            image=q.image,
            partial_credit=q.partial_credit,
            accepted_answers=list(q.accepted_answers),
            allow_minor_typos=q.allow_minor_typos,
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
# Teacher — Fast Entry & CSV Import
# ---------------------------------------------------------------------------


def parse_pasted_questions(text: str) -> tuple[list[dict], list[str]]:
    """
    Parse a multiline text block in the approved format.
    Returns (questions_data, errors).
    """
    errors: list[str] = []
    questions: list[dict] = []
    blocks = re.split(r"\n\s*\n", text.strip())

    for block_idx, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        q_body = None
        q_type = None
        points = 1
        explanation = ""
        partial_credit = False
        allow_minor_typos = False
        choices: list[tuple[str, bool]] = []
        accepted_answers: list[str] = []

        for line_no, line in enumerate(lines, start=1):
            if line.startswith("Q:"):
                header = line[2:].strip()
                # Check for tags [select_all], [fill_blank], [tf], [partial], [typos]
                # Handles both [select_all] [partial] and combined [select_all, partial]
                tag_matches = re.findall(r"\[(.*?)\]", header)
                for tag_group in tag_matches:
                    tags = [t.strip().lower() for t in tag_group.split(",")]
                    if "partial" in tags:
                        partial_credit = True
                    if "typos" in tags:
                        allow_minor_typos = True
                    if "select_all" in tags:
                        q_type = QuizQuestion.QuestionType.SELECT_ALL
                    elif "fill_blank" in tags:
                        q_type = QuizQuestion.QuestionType.FILL_BLANK
                    elif "tf" in tags:
                        q_type = QuizQuestion.QuestionType.TRUE_FALSE
                    elif "short_answer" in tags:
                        q_type = QuizQuestion.QuestionType.SHORT_ANSWER
                header = re.sub(r"\[.*?\]", "", header).strip()
                q_body = header
            elif line.lower().startswith("points:"):
                try:
                    points = max(1, int(line.split(":", 1)[1].strip()))
                except ValueError:
                    errors.append(f"Block {block_idx}: Invalid points value '{line}'.")
            elif line.lower().startswith("explanation:"):
                explanation = line.split(":", 1)[1].strip()
            elif line.startswith("="):
                # Accepted answer for fill-in-the-blank
                val = line[1:].strip()
                if val:
                    accepted_answers.append(val)
            elif line.startswith("*"):
                # Correct choice
                choice_text = line[1:].strip()
                if choice_text:
                    choices.append((choice_text, True))
            else:
                # Regular choice (strip optional leading dash)
                choice_text = line.lstrip("-").strip()
                if choice_text:
                    choices.append((choice_text, False))

        if not q_body:
            errors.append(f"Block {block_idx}: Missing 'Q: question text'.")
            continue

        # Auto-detect question type if not explicitly set
        if q_type is None:
            if accepted_answers:
                q_type = QuizQuestion.QuestionType.FILL_BLANK
            elif choices:
                correct_count = sum(1 for _, is_c in choices if is_c)
                choice_texts = [c[0].lower() for c in choices]
                if set(choice_texts) == {"true", "false"} and len(choices) == 2:
                    q_type = QuizQuestion.QuestionType.TRUE_FALSE
                elif correct_count > 1:
                    q_type = QuizQuestion.QuestionType.SELECT_ALL
                else:
                    q_type = QuizQuestion.QuestionType.MULTIPLE_CHOICE
            else:
                q_type = QuizQuestion.QuestionType.SHORT_ANSWER

        # Validation per question type
        if q_type == QuizQuestion.QuestionType.FILL_BLANK:
            if not accepted_answers:
                errors.append(f"Block {block_idx} ('{q_body[:30]}'): Fill-in-the-blank questions need at least one accepted answer starting with '='.")
        elif q_type in (QuizQuestion.QuestionType.MULTIPLE_CHOICE, QuizQuestion.QuestionType.SELECT_ALL, QuizQuestion.QuestionType.TRUE_FALSE):
            if len(choices) < 2:
                errors.append(f"Block {block_idx} ('{q_body[:30]}'): Needs at least 2 answer choices.")
            elif not any(is_c for _, is_c in choices):
                errors.append(f"Block {block_idx} ('{q_body[:30]}'): Must mark at least one correct choice with '*'.")
            elif q_type == QuizQuestion.QuestionType.MULTIPLE_CHOICE and sum(1 for _, is_c in choices if is_c) > 1:
                # If teacher marked multiple with '*', promote to select_all
                q_type = QuizQuestion.QuestionType.SELECT_ALL

        questions.append({
            "body": q_body,
            "question_type": q_type,
            "points": points,
            "explanation": explanation,
            "partial_credit": partial_credit,
            "accepted_answers": accepted_answers,
            "allow_minor_typos": allow_minor_typos,
            "choices": choices,
        })

    return questions, errors


@transaction.atomic
def bulk_create_questions(
    *, quiz: Quiz, questions_data: list[dict], actor
) -> list[QuizQuestion]:
    _assert_not_locked(quiz)
    limit = max_questions_for_quiz(quiz)
    if quiz.question_count + len(questions_data) > limit:
        raise ValidationError(f"Adding these questions would exceed the maximum of {limit} questions.")

    created: list[QuizQuestion] = []
    last_pos = (
        QuizQuestion.objects.filter(quiz=quiz)
        .order_by("-position")
        .values_list("position", flat=True)
        .first()
    )
    current_pos = (last_pos + 1) if last_pos is not None else 0

    for qd in questions_data:
        q = QuizQuestion.objects.create(
            quiz=quiz,
            body=qd["body"],
            question_type=qd["question_type"],
            points=qd.get("points", 1),
            partial_credit=qd.get("partial_credit", False),
            accepted_answers=qd.get("accepted_answers", []),
            allow_minor_typos=qd.get("allow_minor_typos", False),
            explanation=qd.get("explanation", ""),
            position=current_pos,
        )
        current_pos += 1
        for idx, (choice_text, is_correct) in enumerate(qd.get("choices", [])):
            QuizChoice.objects.create(
                question=q,
                text=choice_text,
                is_correct=is_correct,
                position=idx,
            )
        created.append(q)
    return created


def generate_csv_template() -> str:
    """Returns downloadable CSV template for bulk question import."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "question_text",
        "question_type",
        "points",
        "choices",
        "correct_choices",
        "accepted_answers",
        "explanation",
        "partial_credit",
        "allow_minor_typos",
    ])
    writer.writerow([
        "What layer of the OSI model does an IP router operate at?",
        "mc",
        "1",
        "Network; Transport; Data Link; Physical",
        "Network",
        "",
        "Routers operate at Layer 3 using IP packets.",
        "0",
        "0",
    ])
    writer.writerow([
        "Switches operate at the Network layer.",
        "tf",
        "1",
        "True; False",
        "False",
        "",
        "Switches are Layer 2 Data Link devices.",
        "0",
        "0",
    ])
    writer.writerow([
        "Which protocols use TCP for reliable transmission?",
        "select_all",
        "2",
        "HTTP; SSH; DNS; FTP",
        "HTTP; SSH; FTP",
        "",
        "HTTP, SSH, and FTP use TCP byte streams.",
        "1",
        "0",
    ])
    writer.writerow([
        "What protocol resolves IPv4 addresses to physical MAC addresses?",
        "fill_blank",
        "1",
        "",
        "",
        "ARP; Address Resolution Protocol",
        "ARP maps IP to Ethernet addresses.",
        "0",
        "1",
    ])
    return output.getvalue()


@transaction.atomic
def import_questions_from_csv(
    *, quiz: Quiz, csv_file, actor
) -> tuple[int, list[str]]:
    """
    Import questions from CSV. Returns (imported_count, errors).
    """
    _assert_not_locked(quiz)
    errors: list[str] = []
    questions_data: list[dict] = []

    try:
        content = csv_file.read().decode("utf-8-sig")
    except Exception as exc:
        return 0, [f"Failed to read CSV file: {exc}"]

    reader = csv.DictReader(io.StringIO(content))
    for row_no, row in enumerate(reader, start=2):
        body = (row.get("question_text") or "").strip()
        if not body:
            continue
        q_type = (row.get("question_type") or "mc").strip().lower()
        if q_type not in QuizQuestion.QuestionType.values:
            errors.append(f"Row {row_no}: Unknown question type '{q_type}'.")
            continue

        try:
            points = max(1, int(row.get("points") or 1))
        except ValueError:
            points = 1

        partial_credit = str(row.get("partial_credit") or "").strip().lower() in ("1", "true", "yes")
        allow_minor_typos = str(row.get("allow_minor_typos") or "").strip().lower() in ("1", "true", "yes")
        explanation = (row.get("explanation") or "").strip()

        choices: list[tuple[str, bool]] = []
        raw_choices = [c.strip() for c in (row.get("choices") or "").split(";") if c.strip()]
        raw_correct = [c.strip().lower() for c in (row.get("correct_choices") or "").split(";") if c.strip()]

        for c_text in raw_choices:
            is_c = c_text.lower() in raw_correct
            choices.append((c_text, is_c))

        accepted = [a.strip() for a in (row.get("accepted_answers") or "").split(";") if a.strip()]

        if q_type == QuizQuestion.QuestionType.FILL_BLANK and not accepted:
            errors.append(f"Row {row_no}: Fill-in-the-blank requires accepted_answers.")
            continue
        if q_type in (QuizQuestion.QuestionType.MULTIPLE_CHOICE, QuizQuestion.QuestionType.SELECT_ALL, QuizQuestion.QuestionType.TRUE_FALSE):
            if len(choices) < 2:
                errors.append(f"Row {row_no}: Multiple choice/TF requires at least 2 choices separated by semicolon (;).")
                continue
            if not any(is_c for _, is_c in choices):
                errors.append(f"Row {row_no}: Must specify at least one correct choice in correct_choices.")
                continue

        questions_data.append({
            "body": body,
            "question_type": q_type,
            "points": points,
            "choices": choices,
            "accepted_answers": accepted,
            "explanation": explanation,
            "partial_credit": partial_credit,
            "allow_minor_typos": allow_minor_typos,
        })

    if errors:
        return 0, errors

    created = bulk_create_questions(quiz=quiz, questions_data=questions_data, actor=actor)
    return len(created), []


# ---------------------------------------------------------------------------
# Teacher — Fix Answer Key & Regrade
# ---------------------------------------------------------------------------


@transaction.atomic
def fix_answer_and_regrade(
    *,
    quiz: Quiz,
    question_id: int,
    correct_choice_pks: list[int] | None = None,
    accepted_answers: list[str] | None = None,
    reason: str,
    actor,
) -> dict:
    """
    Safely correct an answer key and re-score all existing submitted attempts.
    Logs who, when, and why to QuizAuditLog.
    """
    reason = reason.strip()
    if not reason:
        raise ValidationError("A written reason is required for regrading.")

    try:
        question = QuizQuestion.objects.get(pk=question_id, quiz=quiz)
    except QuizQuestion.DoesNotExist:
        raise ValidationError("Question not found in this quiz.")

    prior_choices: list[dict] = []
    prior_accepted = list(question.accepted_answers)

    # Update choices if provided
    if correct_choice_pks is not None:
        pks_set = set(correct_choice_pks)
        for choice in question.choices.all():
            prior_choices.append({"pk": choice.pk, "text": choice.text, "is_correct": choice.is_correct})
            new_is_correct = (choice.pk in pks_set)
            if choice.is_correct != new_is_correct:
                choice.is_correct = new_is_correct
                choice.save(update_fields=["is_correct", "updated_at"])

    # Update accepted answers if provided
    if accepted_answers is not None:
        question.accepted_answers = [a.strip() for a in accepted_answers if a.strip()]
        question.save(update_fields=["accepted_answers", "updated_at"])

    # Re-score all submitted attempts
    submitted_attempts = list(
        QuizAttempt.objects.filter(quiz=quiz, submitted_at__isnull=False)
    )
    scores_changed = 0
    newly_passed = 0

    for attempt in submitted_attempts:
        old_pct = attempt.score_pct
        old_passed = attempt.passed

        _mark_and_save(attempt, auto_submitted=attempt.answers_auto_submitted, is_regrade=True)

        attempt.regraded_at = timezone.now()
        attempt.regraded_by = actor if hasattr(actor, "pk") else None
        attempt.regrade_reason = reason
        attempt.save(update_fields=["regraded_at", "regraded_by", "regrade_reason", "updated_at"])

        if attempt.score_pct != old_pct:
            scores_changed += 1
        if not old_passed and attempt.passed:
            newly_passed += 1

    QuizAuditLog.objects.create(
        quiz=quiz,
        actor=actor if hasattr(actor, "pk") else None,
        action="regrade",
        details={
            "question_id": question.pk,
            "prior_choices": prior_choices,
            "new_correct_choice_pks": correct_choice_pks,
            "prior_accepted_answers": prior_accepted,
            "new_accepted_answers": question.accepted_answers,
            "attempts_regraded": len(submitted_attempts),
            "scores_changed": scores_changed,
            "newly_passed": newly_passed,
        },
        reason=reason,
    )

    return {
        "attempts_regraded": len(submitted_attempts),
        "scores_changed": scores_changed,
        "newly_passed": newly_passed,
    }


# ---------------------------------------------------------------------------
# Student — attempt lifecycle
# ---------------------------------------------------------------------------


@transaction.atomic
def start_or_resume_attempt(*, quiz: Quiz, student) -> QuizAttempt:
    """
    Return the student's current in-progress attempt, or start a new one.
    """
    if not quiz.is_published:
        raise ValidationError("This quiz is not available.")
    if not quiz.is_open_now:
        raise ValidationError("This quiz is not currently open.")

    Quiz.objects.select_for_update().filter(pk=quiz.pk).exists()

    # Resume any existing in-progress attempt
    in_progress = QuizAttempt.objects.filter(
        quiz=quiz, student=student, submitted_at__isnull=True
    ).first()
    if in_progress:
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
    choice_id: int | None = None,
    choice_ids: list[int] | None = None,
    answer_text: str | None = None,
) -> QuizAttemptAnswer | None:
    """
    Save student's answer for single-choice, multi-select, or text questions.
    """
    if attempt.is_submitted:
        return None
    if attempt.is_timed_out:
        auto_submit_timed_out(attempt)
        return None

    try:
        question = QuizQuestion.objects.get(pk=question_id, quiz=attempt.quiz)
    except QuizQuestion.DoesNotExist:
        return None

    answer, _ = QuizAttemptAnswer.objects.get_or_create(
        attempt=attempt,
        question=question,
    )

    if question.question_type in (QuizQuestion.QuestionType.MULTIPLE_CHOICE, QuizQuestion.QuestionType.TRUE_FALSE):
        choice = None
        if choice_id is not None:
            try:
                choice = QuizChoice.objects.get(pk=choice_id, question=question)
            except QuizChoice.DoesNotExist:
                return None
        answer.selected_choice = choice
        answer.save(update_fields=["selected_choice", "updated_at"])

    elif question.question_type == QuizQuestion.QuestionType.SELECT_ALL:
        if choice_ids is not None:
            valid_choices = list(QuizChoice.objects.filter(question=question, pk__in=choice_ids))
            answer.selected_choices.set(valid_choices)

    elif question.question_type in (QuizQuestion.QuestionType.FILL_BLANK, QuizQuestion.QuestionType.SHORT_ANSWER):
        if answer_text is not None:
            answer.answer_text = answer_text.strip()
            answer.save(update_fields=["answer_text", "updated_at"])

    return answer


@transaction.atomic
def submit_attempt(*, attempt: QuizAttempt, student) -> QuizAttempt:
    """
    Mark all answers and compute the score. Server-side only.
    """
    if attempt.is_submitted:
        return attempt
    if attempt.student_id != student.pk:
        raise PermissionError("You cannot submit another student's attempt.")

    if attempt.is_timed_out:
        return auto_submit_timed_out(attempt)

    return _mark_and_save(attempt, auto_submitted=False)


@transaction.atomic
def auto_submit_timed_out(attempt: QuizAttempt) -> QuizAttempt:
    """
    Server-side forced submission when the time limit has expired.
    """
    if attempt.is_submitted:
        return attempt
    return _mark_and_save(attempt, auto_submitted=True)


def _mark_and_save(
    attempt: QuizAttempt, *, auto_submitted: bool, is_regrade: bool = False
) -> QuizAttempt:
    """
    Core marking logic for all question types. Server-side only.
    """
    questions = list(
        QuizQuestion.objects.filter(quiz=attempt.quiz).prefetch_related("choices")
    )
    total_possible = sum(q.points for q in questions)
    total_earned = Decimal("0.00")

    existing_answers = {
        ans.question_id: ans
        for ans in QuizAttemptAnswer.objects.filter(attempt=attempt)
        .select_related("selected_choice")
        .prefetch_related("selected_choices")
    }

    for q in questions:
        answer = existing_answers.get(q.pk)
        if answer is None:
            answer = QuizAttemptAnswer.objects.create(
                attempt=attempt,
                question=q,
                selected_choice=None,
                points_awarded=Decimal("0.00"),
                is_correct=False,
            )

        q_type = q.question_type
        points_val = Decimal(q.points)

        # 1. Multiple Choice & True/False
        if q_type in (QuizQuestion.QuestionType.MULTIPLE_CHOICE, QuizQuestion.QuestionType.TRUE_FALSE):
            is_c = bool(answer.selected_choice and answer.selected_choice.is_correct)
            awarded = points_val if is_c else Decimal("0.00")
            answer.is_correct = is_c
            answer.points_awarded = awarded
            total_earned += awarded

        # 2. Select All That Apply
        elif q_type == QuizQuestion.QuestionType.SELECT_ALL:
            correct_pks = set(c.pk for c in q.choices.all() if c.is_correct)
            student_pks = set(c.pk for c in answer.selected_choices.all())
            if q.partial_credit:
                tp = len(student_pks & correct_pks)
                fp = len(student_pks - correct_pks)
                if correct_pks:
                    fraction = max(0.0, (tp - fp) / len(correct_pks))
                    awarded = round(Decimal(str(fraction)) * points_val, 2)
                else:
                    awarded = Decimal("0.00")
                is_c = (tp == len(correct_pks) and fp == 0)
                answer.is_correct = is_c
                answer.points_awarded = awarded
                total_earned += awarded
            else:
                is_c = (student_pks == correct_pks and len(correct_pks) > 0)
                awarded = points_val if is_c else Decimal("0.00")
                answer.is_correct = is_c
                answer.points_awarded = awarded
                total_earned += awarded

        # 3. Fill in the Blank / Short Exact Answer
        elif q_type == QuizQuestion.QuestionType.FILL_BLANK:
            matched, matched_str = match_accepted_answer(
                answer.answer_text, q.accepted_answers, q.allow_minor_typos
            )
            awarded = points_val if matched else Decimal("0.00")
            answer.is_correct = matched
            answer.points_awarded = awarded
            answer.accepted_answer_matched = matched_str
            total_earned += awarded

        # 4. Short Answer / Paragraph (marked manually by teacher in Q2)
        elif q_type == QuizQuestion.QuestionType.SHORT_ANSWER:
            if not answer.is_override:
                answer.is_correct = None
                answer.points_awarded = Decimal("0.00")
            total_earned += answer.points_awarded

        answer.save(update_fields=[
            "is_correct", "points_awarded", "accepted_answer_matched", "updated_at"
        ])

    score_pct = int((total_earned * 100) / total_possible) if total_possible > 0 else 0
    passed = score_pct >= attempt.quiz.pass_mark

    if not attempt.submitted_at:
        attempt.submitted_at = timezone.now()
    attempt.score_pct = score_pct
    attempt.score_points = total_earned
    attempt.total_possible_points = total_possible
    attempt.passed = passed
    attempt.answers_auto_submitted = auto_submitted
    attempt.save(
        update_fields=[
            "submitted_at", "score_pct", "score_points", "total_possible_points",
            "passed", "answers_auto_submitted", "updated_at",
        ]
    )

    try:
        _award_quiz_points(attempt)
    except Exception:  # noqa: BLE001
        logger.warning("Points award failed for quiz attempt %s", attempt.pk)

    return attempt


def _award_quiz_points(attempt: QuizAttempt) -> None:
    """
    Award pts_quiz_attempt (first attempt only) and pts_quiz_pass (first pass).
    Uses idempotency keys so retakes and regrades never farm points.
    """
    from .points_config import get_point_config
    from .points_services import award_points
    from .models import PointLedger

    cfg = get_point_config()
    student = attempt.student
    quiz = attempt.quiz

    award_points(
        student=student,
        reason=PointLedger.Reason.BONUS,
        points=cfg.pts_quiz_attempt,
        description=f"Completed quiz: {quiz.title}",
        idempotency_key=f"quiz_attempt:{quiz.pk}:{student.pk}",
    )

    if attempt.passed:
        award_points(
            student=student,
            reason=PointLedger.Reason.BONUS,
            points=cfg.pts_quiz_pass,
            description=f"Passed quiz: {quiz.title}",
            idempotency_key=f"quiz_pass:{quiz.pk}:{student.pk}",
        )

