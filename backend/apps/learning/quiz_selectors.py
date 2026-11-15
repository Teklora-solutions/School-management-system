"""
apps/learning/quiz_selectors.py
---------------------------------
Read-only queries for the quiz system (Checkpoints C & Q1).

Public API
----------
  quiz_for_lesson(lesson_id)               → Quiz | None  (teacher, includes draft)
  published_quiz_for_lesson(lesson_id)     → Quiz | None  (student, published only)

  quiz_questions_for_student(quiz, attempt)
      → list[QuestionWithChoices]
      Choices NEVER include is_correct. Question order follows attempt.question_order.
      Choice order shuffled per attempt if quiz.shuffle_choices.

  quiz_questions_for_teacher(quiz) → list[QuestionWithChoices]  (includes is_correct)
  quiz_questions_for_results(quiz, attempt, show_correct) → list[StudentResultRow]

  student_attempt_summary(quiz, student)  → AttemptSummary
  teacher_quiz_results(quiz, school_class) → TeacherResultsData

  best_attempt(quiz, student) → QuizAttempt | None
  attempts_remaining(quiz, student) → int | None  (None = unlimited)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal
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
    student_answer_pk: Optional[int] = None      # for MC / TF
    student_answer_pks: list[int] = field(default_factory=list)  # for select_all
    student_answer_text: str = ""                # for fill_blank / short_answer
    is_correct: Optional[bool] = None            # None before submission


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
    show_correct: bool              # True only if student is allowed to see answers now
    policy_score: Optional[int] = None


@dataclass
class StudentResultRow:
    question: QuizQuestion
    student_answer_pk: Optional[int] = None
    correct_choice_pk: Optional[int] = None
    student_answer_pks: list[int] = field(default_factory=list)
    correct_choice_pks: list[int] = field(default_factory=list)
    student_answer_text: str = ""
    accepted_answers: list[str] = field(default_factory=list)
    accepted_matched: str = ""
    points_awarded: Decimal = Decimal("0.00")
    points_possible: int = 1
    is_correct: Optional[bool] = None
    is_override: bool = False
    override_reason: str = ""
    explanation: str = ""


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
    """
    questions_by_pk = {
        q.pk: q
        for q in QuizQuestion.objects.filter(quiz=quiz).prefetch_related("choices")
    }

    # Follow stored question order (shuffle seed)
    order = attempt.question_order or list(questions_by_pk.keys())
    ordered = [questions_by_pk[pk] for pk in order if pk in questions_by_pk]

    # Pre-fetch saved answers
    answers_qs = (
        QuizAttemptAnswer.objects.filter(attempt=attempt)
        .prefetch_related("selected_choices")
    )
    saved_answers = {a.question_id: a for a in answers_qs}

    result = []
    for q in ordered:
        choices_qs = list(q.choices.order_by("position", "id"))
        if quiz.shuffle_choices and not attempt.is_submitted:
            rng = random.Random(attempt.pk + q.pk)
            rng.shuffle(choices_qs)
        choices = [
            ChoiceData(pk=c.pk, text=c.text, is_correct=None)
            for c in choices_qs
        ]
        ans = saved_answers.get(q.pk)
        selected_pk = ans.selected_choice_id if ans else None
        selected_pks = [c.pk for c in ans.selected_choices.all()] if ans else []
        text_val = ans.answer_text if ans else ""

        result.append(
            QuestionWithChoices(
                question=q,
                choices=choices,
                student_answer_pk=selected_pk,
                student_answer_pks=selected_pks,
                student_answer_text=text_val,
            )
        )
    return result


def quiz_questions_for_results(
    *, quiz: Quiz, attempt: QuizAttempt, show_correct: bool
) -> list[StudentResultRow]:
    """
    Questions with marking data for the student result page.
    """
    answers = {
        a.question_id: a
        for a in QuizAttemptAnswer.objects.filter(attempt=attempt)
        .select_related("selected_choice")
        .prefetch_related("selected_choices")
    }
    rows = []
    for q in quiz.questions.prefetch_related("choices").order_by("position", "id"):
        ans = answers.get(q.pk)
        correct_pk = None
        correct_pks: list[int] = []
        accepted: list[str] = []

        if show_correct:
            correct_choices = [c for c in q.choices.all() if c.is_correct]
            if correct_choices:
                correct_pk = correct_choices[0].pk
                correct_pks = [c.pk for c in correct_choices]
            accepted = list(q.accepted_answers)

        selected_pks = [c.pk for c in ans.selected_choices.all()] if ans else []
        rows.append(StudentResultRow(
            question=q,
            student_answer_pk=ans.selected_choice_id if ans else None,
            correct_choice_pk=correct_pk,
            student_answer_pks=selected_pks,
            correct_choice_pks=correct_pks,
            student_answer_text=ans.answer_text if ans else "",
            accepted_answers=accepted,
            accepted_matched=ans.accepted_answer_matched if ans else "",
            points_awarded=ans.points_awarded if ans else Decimal("0.00"),
            points_possible=q.points,
            is_correct=ans.is_correct if ans else False,
            is_override=ans.is_override if ans else False,
            override_reason=ans.override_reason if ans else "",
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
        (in_progress is not None or limit == Quiz.Attempts.UNLIMITED or used < limit)
        and quiz.is_open_now
    )
    current = in_progress or (submitted[0] if submitted else None)
    show = bool(
        current and current.is_submitted and quiz.can_view_answers_now
    )

    # Calculate policy score
    policy_score = None
    if submitted:
        if quiz.grading_policy == Quiz.GradingPolicy.BEST:
            policy_score = best.score_pct if best else 0
        elif quiz.grading_policy == Quiz.GradingPolicy.LATEST:
            policy_score = submitted[0].score_pct if submitted else 0
        elif quiz.grading_policy == Quiz.GradingPolicy.AVERAGE:
            policy_score = round(sum(a.score_pct or 0 for a in submitted) / len(submitted))

    return AttemptSummary(
        quiz=quiz,
        attempt=current,
        all_attempts=all_attempts,
        best_attempt=best,
        attempts_used=used,
        attempts_allowed=limit,
        can_retry=can_retry,
        show_correct=show,
        policy_score=policy_score,
    )


def teacher_quiz_results(*, quiz: Quiz, school_class) -> TeacherResultsData:
    """
    Full results for the teacher screen.
    """
    from apps.students.models import Student
    from .release import is_released_at

    students = list(
        Student.objects.filter(
            current_class=school_class, status=Student.Status.ACTIVE
        ).select_related("user")
    )
    targeted = {s.pk for s in students}
    if not quiz.assign_all:
        targeted &= set(quiz.assigned_students.values_list("pk", flat=True))
    if not quiz.lesson.assign_all:
        targeted &= set(quiz.lesson.assigned_students.values_list("pk", flat=True))
    if not all(is_released_at(obj) for obj in (quiz, quiz.lesson, quiz.lesson.module)):
        targeted.clear()
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
            # Pupils the quiz (or its lesson) isn't assigned to aren't "missing".
            if stu.pk in targeted:
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

    all_answers = list(
        QuizAttemptAnswer.objects.filter(
            attempt__quiz=quiz,
            attempt__submitted_at__isnull=False,
        ).values("question_id", "is_correct")
    )
    from collections import defaultdict
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

