"""
apps/learning/quiz_selectors.py
---------------------------------
Read-only queries for the quiz system (Checkpoint C).

Public API
----------
  quiz_for_lesson(lesson_id)               → Quiz | None  (teacher, includes draft)
  published_quiz_for_lesson(lesson_id)     → Quiz | None  (student, published only)

  quiz_questions_for_student(quiz, attempt)
      → list[QuestionWithChoices]
      Choices NEVER include is_correct.  Question order follows attempt.question_order.
      Choice order shuffled per attempt if quiz.shuffle_choices.

  quiz_questions_for_teacher(quiz) → list[QuestionWithChoices]  (includes is_correct)

  student_attempt_summary(quiz, student)  → AttemptSummary
  teacher_quiz_results(quiz)              → TeacherResultsData

  best_attempt(quiz, student) → QuizAttempt | None
  attempts_remaining(quiz, student) → int | None  (None = unlimited)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .models import Quiz, QuizAttempt, QuizAttemptAnswer, QuizChoice, QuizQuestion


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ChoiceData:
    pk: int
    text: str
    is_correct: Optional[bool] = None   # None when hidden from student


@dataclass
class QuestionWithChoices:
    question: QuizQuestion
    choices: list[ChoiceData] = field(default_factory=list)
    student_answer_pk: Optional[int] = None  # selected choice pk, if any
    is_correct: Optional[bool] = None        # None before submission


@dataclass
class AttemptSummary:
    """Everything the student result page needs."""
    quiz: Quiz
    attempt: Optional[QuizAttempt]
    all_attempts: list[QuizAttempt]
    best_attempt: Optional[QuizAttempt]
    attempts_used: int
    attempts_allowed: int           # 0 = unlimited
    can_retry: bool
    show_correct: bool              # quiz.show_correct_after AND attempt submitted


@dataclass
class StudentResultRow:
    question: QuizQuestion
    student_answer_pk: Optional[int]
    correct_choice_pk: Optional[int]
    is_correct: Optional[bool]
    explanation: str


@dataclass
class TeacherStudentRow:
    student: object
    attempts: list[QuizAttempt]
    best_score: Optional[int]
    passed: bool


@dataclass
class QuestionDifficulty:
    question: QuizQuestion
    wrong_count: int
    total_count: int

    @property
    def wrong_pct(self) -> int:
        if not self.total_count:
            return 0
        return round(self.wrong_count * 100 / self.total_count)


@dataclass
class TeacherResultsData:
    quiz: Quiz
    student_rows: list[TeacherStudentRow] = field(default_factory=list)
    not_attempted: list = field(default_factory=list)  # Student objects
    class_avg: int = 0
    pass_rate: int = 0
    question_difficulty: list[QuestionDifficulty] = field(default_factory=list)

    @property
    def attempted_count(self) -> int:
        return len(self.student_rows)

    @property
    def not_attempted_count(self) -> int:
        return len(self.not_attempted)


# ---------------------------------------------------------------------------
# Public selectors
# ---------------------------------------------------------------------------


def quiz_for_lesson(*, lesson_id: int) -> Optional[Quiz]:
    """Teacher: return the quiz (any publish state), or None."""
    try:
        return (
            Quiz.objects.select_related("lesson__module__school_class",
                                       "lesson__module__subject")
            .prefetch_related("questions__choices")
            .get(lesson_id=lesson_id)
        )
    except Quiz.DoesNotExist:
        return None


def published_quiz_for_lesson(*, lesson_id: int) -> Optional[Quiz]:
    """Student: published quiz only."""
    try:
        return Quiz.objects.get(lesson_id=lesson_id, is_published=True)
    except Quiz.DoesNotExist:
        return None


def quiz_questions_for_student(
    *, quiz: Quiz, attempt: QuizAttempt
) -> list[QuestionWithChoices]:
    """
    Questions and choices for the take-quiz page.

    IMPORTANT: ``is_correct`` is NEVER included in choices here.
    ``student_answer_pk`` is populated so the UI can show which choice
    the student already selected (for resume support).
    """
    questions_by_pk = {
        q.pk: q
        for q in QuizQuestion.objects.filter(quiz=quiz).prefetch_related("choices")
    }

    # Follow the stored question order (shuffle seed)
    order = attempt.question_order or list(questions_by_pk.keys())
    ordered = [questions_by_pk[pk] for pk in order if pk in questions_by_pk]

    # Existing answers for resume
    saved_answers = {
        a["question_id"]: a["selected_choice_id"]
        for a in QuizAttemptAnswer.objects.filter(attempt=attempt)
        .values("question_id", "selected_choice_id")
    }

    result = []
    for q in ordered:
        choices_qs = list(q.choices.order_by("position", "id"))
        if quiz.shuffle_choices and not attempt.is_submitted:
            # Use attempt pk as stable seed so same student sees same order on resume
            rng = random.Random(attempt.pk + q.pk)
            rng.shuffle(choices_qs)
        choices = [
            ChoiceData(pk=c.pk, text=c.text, is_correct=None)   # is_correct hidden
            for c in choices_qs
        ]
        result.append(
            QuestionWithChoices(
                question=q,
                choices=choices,
                student_answer_pk=saved_answers.get(q.pk),
            )
        )
    return result


def quiz_questions_for_results(
    *, quiz: Quiz, attempt: QuizAttempt, show_correct: bool
) -> list[StudentResultRow]:
    """
    Questions with marking data for the student result page.

    ``show_correct`` is True only when attempt is submitted AND
    quiz.show_correct_after is True.  When False, correct_choice_pk and
    explanations are withheld.
    """
    answers = {
        a.question_id: a
        for a in QuizAttemptAnswer.objects.filter(attempt=attempt)
        .select_related("selected_choice")
    }
    rows = []
    for q in quiz.questions.prefetch_related("choices").order_by("position", "id"):
        ans = answers.get(q.pk)
        correct_pk = None
        if show_correct:
            correct_choice = q.choices.filter(is_correct=True).first()
            correct_pk = correct_choice.pk if correct_choice else None
        rows.append(StudentResultRow(
            question=q,
            student_answer_pk=ans.selected_choice_id if ans else None,
            correct_choice_pk=correct_pk,
            is_correct=ans.is_correct if ans else False,
            explanation=q.explanation if show_correct else "",
        ))
    return rows


def quiz_questions_for_teacher(*, quiz: Quiz) -> list[QuestionWithChoices]:
    """Teacher quiz builder: includes is_correct on choices."""
    result = []
    for q in quiz.questions.prefetch_related("choices").order_by("position", "id"):
        choices = [
            ChoiceData(pk=c.pk, text=c.text, is_correct=c.is_correct)
            for c in q.choices.order_by("position", "id")
        ]
        result.append(QuestionWithChoices(question=q, choices=choices))
    return result


def best_attempt(*, quiz: Quiz, student) -> Optional[QuizAttempt]:
    submitted = list(
        QuizAttempt.objects.filter(
            quiz=quiz, student=student, submitted_at__isnull=False
        )
    )
    if not submitted:
        return None
    return max(submitted, key=lambda a: (a.score_pct or 0))


def attempts_remaining(*, quiz: Quiz, student) -> Optional[int]:
    """None = unlimited."""
    if quiz.attempts_allowed == Quiz.Attempts.UNLIMITED:
        return None
    used = QuizAttempt.objects.filter(
        quiz=quiz, student=student, submitted_at__isnull=False
    ).count()
    return max(0, quiz.attempts_allowed - used)


def student_attempt_summary(*, quiz: Quiz, student) -> AttemptSummary:
    all_attempts = list(
        QuizAttempt.objects.filter(quiz=quiz, student=student)
        .order_by("-started_at")
    )
    submitted = [a for a in all_attempts if a.is_submitted]
    in_progress = next((a for a in all_attempts if not a.is_submitted), None)
    best = max(submitted, key=lambda a: a.score_pct or 0) if submitted else None
    used = len(submitted)
    limit = quiz.attempts_allowed
    can_retry = (
        in_progress is not None
        or limit == Quiz.Attempts.UNLIMITED
        or used < limit
    )
    current = in_progress or (submitted[0] if submitted else None)
    show = bool(
        current and current.is_submitted and quiz.show_correct_after
    )
    return AttemptSummary(
        quiz=quiz,
        attempt=current,
        all_attempts=all_attempts,
        best_attempt=best,
        attempts_used=used,
        attempts_allowed=limit,
        can_retry=can_retry,
        show_correct=show,
    )


def teacher_quiz_results(*, quiz: Quiz, school_class) -> TeacherResultsData:
    """
    Full results for the teacher screen.
    Two bulk queries: one for all attempts, one for all answers.
    """
    from apps.students.models import Student

    students = list(
        Student.objects.filter(
            current_class=school_class, status=Student.Status.ACTIVE
        ).select_related("user")
    )
    all_attempts = list(
        QuizAttempt.objects.filter(
            quiz=quiz, submitted_at__isnull=False
        ).select_related("student__user").order_by("-started_at")
    )
    by_student: dict[int, list[QuizAttempt]] = {}
    for att in all_attempts:
        by_student.setdefault(att.student_id, []).append(att)

    student_rows = []
    not_attempted = []
    scores = []

    for stu in students:
        attempts = by_student.get(stu.pk, [])
        if not attempts:
            not_attempted.append(stu)
            continue
        best = max(attempts, key=lambda a: a.score_pct or 0)
        scores.append(best.score_pct or 0)
        student_rows.append(TeacherStudentRow(
            student=stu,
            attempts=attempts,
            best_score=best.score_pct,
            passed=bool(best.passed),
        ))

    class_avg = round(sum(scores) / len(scores)) if scores else 0
    pass_rate = (
        round(sum(1 for s in scores if s >= quiz.pass_mark) * 100 / len(scores))
        if scores else 0
    )

    # Question difficulty: count wrong answers across all submitted attempts
    all_answers = list(
        QuizAttemptAnswer.objects.filter(
            attempt__quiz=quiz,
            attempt__submitted_at__isnull=False,
        ).values("question_id", "is_correct")
    )
    from collections import Counter, defaultdict
    total_map: dict[int, int] = defaultdict(int)
    wrong_map: dict[int, int] = defaultdict(int)
    for ans in all_answers:
        qid = ans["question_id"]
        total_map[qid] += 1
        if not ans["is_correct"]:
            wrong_map[qid] += 1

    difficulty = []
    for q in quiz.questions.order_by("position", "id"):
        difficulty.append(QuestionDifficulty(
            question=q,
            wrong_count=wrong_map.get(q.pk, 0),
            total_count=total_map.get(q.pk, 0),
        ))
    difficulty.sort(key=lambda d: -d.wrong_pct)

    return TeacherResultsData(
        quiz=quiz,
        student_rows=sorted(student_rows, key=lambda r: -(r.best_score or 0)),
        not_attempted=not_attempted,
        class_avg=class_avg,
        pass_rate=pass_rate,
        question_difficulty=difficulty,
    )
