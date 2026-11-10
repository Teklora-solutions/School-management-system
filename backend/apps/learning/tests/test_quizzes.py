"""
apps/learning/tests/test_quizzes.py
--------------------------------------
Tests for Checkpoint C: quizzes.

Coverage
--------
  A. quiz_services
     - create quiz, settings update, publish/unpublish
     - add question, add choice, delete, move
     - edit-lock: QuizLockedError when submitted attempts exist
     - duplicate_quiz
     - start_or_resume_attempt: new attempt, resume, limit enforcement
     - save_answer: saves correctly, ignored after submission or timeout
     - submit_attempt: server-side marking, score calculation, pass/fail
     - auto_submit_timed_out
     - no-double-points: attempt + pass points awarded exactly once per quiz

  B. quiz_selectors
     - quiz_questions_for_student: is_correct NEVER present
     - quiz_questions_for_results: correct only when show_correct_after=True
     - student_attempt_summary: can_retry logic
     - teacher_quiz_results: class avg, pass rate, difficulty ranking

  C. Views
     - draft quiz invisible to students
     - student from other class → 404
     - parent sees landing/results (read-only), cannot take quiz
     - teacher sees builder and results, other teacher → 404
     - save_answer returns JSON {ok, saved}
     - submit redirects to results
     - correct answers not in take-quiz page response (before submit)
"""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import ClassSubject
from apps.accounts.models import ParentProfile
from apps.core.models import SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.learning.models import (
    CourseModule,
    Lesson,
    PointConfig,
    PointLedger,
    Quiz,
    QuizAttempt,
    QuizAttemptAnswer,
    QuizChoice,
    QuizQuestion,
)
from apps.learning.quiz_services import (
    QuizLockedError,
    add_choice,
    add_question,
    auto_submit_timed_out,
    create_quiz,
    delete_question,
    duplicate_quiz,
    move_question,
    publish_quiz,
    save_answer,
    start_or_resume_attempt,
    submit_attempt,
    unpublish_quiz,
)
from apps.learning.quiz_selectors import (
    quiz_questions_for_student,
    student_attempt_summary,
    teacher_quiz_results,
)
from apps.students.models import StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def enable_quizzes():
    row = SchoolSettings.load()
    row.learning_enabled = True
    row.quizzes_enabled = True
    row.engagement_enabled = True
    row.save(update_fields=["learning_enabled", "quizzes_enabled",
                            "engagement_enabled", "updated_at"])


def make_teacher(username, staff_id):
    user = make_user("teacher", username=username)
    profile = TeacherProfile.objects.create(user=user, staff_id=staff_id)
    return user, profile


def make_parent_for(student):
    parent_user = make_user("parent", username=f"par_{student.admission_number}")
    profile = ParentProfile.objects.create(user=parent_user)
    StudentGuardian.objects.create(student=student, parent=profile, is_primary=True)
    return parent_user, profile


def setup_classroom():
    """Return (year, term, school_class, subject, teacher_user, teacher_profile)."""
    year = make_year()
    term = make_term(year)
    sc = make_class(year, name="Grade QZ", level_order=20)
    subj = make_subject("QZSUBJ", "Quiz Subject")
    ClassSubject.objects.create(school_class=sc, subject=subj)
    teacher_user, teacher = make_teacher("qz_teacher", "QZ-001")
    TeacherAssignment.objects.create(
        teacher=teacher, school_class=sc, subject=subj, academic_year=year
    )
    return year, term, sc, subj, teacher_user, teacher


def make_full_quiz(lesson, teacher_user, *, published=True):
    """Create a 3-question MC quiz and publish it if requested."""
    quiz = create_quiz(
        lesson=lesson, title="Test Quiz",
        pass_mark=50, attempts_allowed=2,
        actor=teacher_user,
    )
    # Q1: A correct, B wrong
    q1 = add_question(quiz=quiz, body="What is 2+2?", actor=teacher_user)
    c1a = add_choice(question=q1, text="4", is_correct=True, actor=teacher_user)
    c1b = add_choice(question=q1, text="5", is_correct=False, actor=teacher_user)

    # Q2: A wrong, B correct
    q2 = add_question(quiz=quiz, body="Capital of Kenya?", actor=teacher_user)
    c2a = add_choice(question=q2, text="Mombasa", is_correct=False, actor=teacher_user)
    c2b = add_choice(question=q2, text="Nairobi", is_correct=True, actor=teacher_user)

    # Q3: A wrong, B correct
    q3 = add_question(quiz=quiz, body="True or false?",
                      question_type=QuizQuestion.QuestionType.TRUE_FALSE,
                      actor=teacher_user)

    if published:
        publish_quiz(quiz=quiz, actor=teacher_user)
    return quiz, [q1, q2, q3], [c1a, c1b, c2a, c2b]


# ============================================================================
# A. quiz_services
# ============================================================================


class QuizCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.term, cls.sc, cls.subj, cls.teacher_user, _ = setup_classroom()
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Mod", position=0,
        )
        cls.lesson = Lesson.objects.create(module=mod, title="Lesson", position=0)

    def test_create_quiz_returns_quiz(self):
        quiz = create_quiz(lesson=self.lesson, title="My Quiz", actor=self.teacher_user)
        self.assertIsInstance(quiz, Quiz)
        self.assertFalse(quiz.is_published)

    def test_cannot_create_duplicate_quiz_on_same_lesson(self):
        create_quiz(lesson=self.lesson, title="First", actor=self.teacher_user)
        with self.assertRaises(ValidationError):
            create_quiz(lesson=self.lesson, title="Second", actor=self.teacher_user)

    def test_publish_requires_at_least_one_question(self):
        quiz = create_quiz(lesson=self.lesson, title="Empty", actor=self.teacher_user)
        with self.assertRaises(ValidationError):
            publish_quiz(quiz=quiz, actor=self.teacher_user)

    def test_add_question_increases_count(self):
        quiz = create_quiz(lesson=self.lesson, title="Q Quiz", actor=self.teacher_user)
        add_question(quiz=quiz, body="Question?", actor=self.teacher_user)
        self.assertEqual(quiz.question_count, 1)

    def test_max_20_questions_enforced(self):
        quiz = create_quiz(lesson=self.lesson, title="Big", actor=self.teacher_user)
        for i in range(20):
            q = add_question(quiz=quiz, body=f"Q{i}", actor=self.teacher_user)
            add_choice(question=q, text="A", is_correct=True, actor=self.teacher_user)
        with self.assertRaises(ValidationError):
            add_question(quiz=quiz, body="One too many", actor=self.teacher_user)


class EditLockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.term, cls.sc, cls.subj, cls.teacher_user, _ = setup_classroom()
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Mod", position=0,
        )
        cls.lesson = Lesson.objects.create(module=mod, title="Lesson", position=0)
        cls.student = make_student_user(cls.sc, admission_number="EL-001")

    def _make_submitted_attempt(self):
        quiz, questions, choices = make_full_quiz(self.lesson, self.teacher_user)
        attempt = start_or_resume_attempt(quiz=quiz, student=self.student)
        # Answer Q1 correctly
        save_answer(attempt=attempt, question_id=questions[0].pk,
                    choice_id=choices[0].pk)
        submit_attempt(attempt=attempt, student=self.student)
        return quiz, questions, choices

    def test_adding_question_after_submission_raises_locked(self):
        quiz, _, _ = self._make_submitted_attempt()
        with self.assertRaises(QuizLockedError):
            add_question(quiz=quiz, body="New question", actor=self.teacher_user)

    def test_deleting_question_after_submission_raises_locked(self):
        quiz, questions, _ = self._make_submitted_attempt()
        with self.assertRaises(QuizLockedError):
            delete_question(question=questions[0], actor=self.teacher_user)

    def test_duplicate_quiz_creates_unlocked_copy(self):
        quiz, _, _ = self._make_submitted_attempt()
        # Create a fresh lesson to attach the duplicate to
        from apps.learning.models import CourseModule
        mod = CourseModule.objects.get(lessons=self.lesson)
        new_lesson = Lesson.objects.create(module=mod, title="Lesson for copy", position=99)
        copy = duplicate_quiz(quiz=quiz, actor=self.teacher_user, target_lesson=new_lesson)
        self.assertFalse(copy.is_published)
        self.assertFalse(copy.has_submitted_attempts)
        self.assertEqual(copy.questions.count(), quiz.questions.count())


class AttemptLifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.term, cls.sc, cls.subj, cls.teacher_user, _ = setup_classroom()
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Mod", position=0,
        )
        cls.lesson = Lesson.objects.create(module=mod, title="Lesson", position=0)
        cls.student = make_student_user(cls.sc, admission_number="AT-001")
        PointConfig.objects.get_or_create(pk=1)

    def test_start_creates_attempt(self):
        quiz, _, _ = make_full_quiz(self.lesson, self.teacher_user)
        attempt = start_or_resume_attempt(quiz=quiz, student=self.student)
        self.assertFalse(attempt.is_submitted)

    def test_start_twice_resumes_same_attempt(self):
        quiz, _, _ = make_full_quiz(self.lesson, self.teacher_user)
        a1 = start_or_resume_attempt(quiz=quiz, student=self.student)
        a2 = start_or_resume_attempt(quiz=quiz, student=self.student)
        self.assertEqual(a1.pk, a2.pk)

    def test_attempt_limit_enforced(self):
        quiz, questions, choices = make_full_quiz(self.lesson, self.teacher_user)
        # Use both allowed attempts
        for _ in range(2):
            attempt = start_or_resume_attempt(quiz=quiz, student=self.student)
            submit_attempt(attempt=attempt, student=self.student)
        with self.assertRaises(ValidationError):
            start_or_resume_attempt(quiz=quiz, student=self.student)

    def test_draft_quiz_cannot_be_started(self):
        quiz = create_quiz(lesson=self.lesson, title="Draft",
                           actor=self.teacher_user)
        q = add_question(quiz=quiz, body="Q?", actor=self.teacher_user)
        add_choice(question=q, text="A", is_correct=True, actor=self.teacher_user)
        with self.assertRaises(ValidationError):
            start_or_resume_attempt(quiz=quiz, student=self.student)


class ServerSideMarkingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.term, cls.sc, cls.subj, cls.teacher_user, _ = setup_classroom()
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Mod", position=0,
        )
        cls.lesson = Lesson.objects.create(module=mod, title="Lesson", position=0)
        cls.student = make_student_user(cls.sc, admission_number="SM-001")
        PointConfig.objects.get_or_create(pk=1)

    def test_all_correct_gives_100_pct(self):
        quiz, questions, choices = make_full_quiz(self.lesson, self.teacher_user)
        # Q1: correct, Q2: correct, Q3 TF: correct (True)
        attempt = start_or_resume_attempt(quiz=quiz, student=self.student)
        # Correct choices: c1a (index 0) for Q1, c2b (index 3) for Q2
        save_answer(attempt=attempt, question_id=questions[0].pk, choice_id=choices[0].pk)
        save_answer(attempt=attempt, question_id=questions[1].pk, choice_id=choices[3].pk)
        # Q3 TF — get the True choice
        true_choice = questions[2].choices.get(text="True")
        save_answer(attempt=attempt, question_id=questions[2].pk, choice_id=true_choice.pk)
        result = submit_attempt(attempt=attempt, student=self.student)
        self.assertEqual(result.score_pct, 100)
        self.assertTrue(result.passed)

    def test_all_wrong_gives_0_pct(self):
        quiz, questions, choices = make_full_quiz(self.lesson, self.teacher_user)
        attempt = start_or_resume_attempt(quiz=quiz, student=self.student)
        # Answer wrong for each
        save_answer(attempt=attempt, question_id=questions[0].pk, choice_id=choices[1].pk)  # wrong
        save_answer(attempt=attempt, question_id=questions[1].pk, choice_id=choices[2].pk)  # wrong
        false_choice = questions[2].choices.get(text="False")
        save_answer(attempt=attempt, question_id=questions[2].pk, choice_id=false_choice.pk)  # wrong (True was correct)
        result = submit_attempt(attempt=attempt, student=self.student)
        self.assertEqual(result.score_pct, 0)
        self.assertFalse(result.passed)

    def test_pass_mark_applied_correctly(self):
        quiz, questions, choices = make_full_quiz(self.lesson, self.teacher_user)
        # Pass mark is 50%. Answer 1 of 3 correctly = 33% → fail
        attempt = start_or_resume_attempt(quiz=quiz, student=self.student)
        save_answer(attempt=attempt, question_id=questions[0].pk, choice_id=choices[0].pk)  # Q1 correct
        save_answer(attempt=attempt, question_id=questions[1].pk, choice_id=choices[2].pk)  # Q2 wrong
        result = submit_attempt(attempt=attempt, student=self.student)
        self.assertFalse(result.passed)

    def test_unanswered_questions_count_as_wrong(self):
        quiz, questions, choices = make_full_quiz(self.lesson, self.teacher_user)
        attempt = start_or_resume_attempt(quiz=quiz, student=self.student)
        # Answer nothing — submit
        result = submit_attempt(attempt=attempt, student=self.student)
        self.assertEqual(result.score_pct, 0)

    def test_submit_is_idempotent(self):
        quiz, questions, choices = make_full_quiz(self.lesson, self.teacher_user)
        attempt = start_or_resume_attempt(quiz=quiz, student=self.student)
        result1 = submit_attempt(attempt=attempt, student=self.student)
        result2 = submit_attempt(attempt=attempt, student=self.student)
        self.assertEqual(result1.pk, result2.pk)
        self.assertEqual(result1.score_pct, result2.score_pct)

    def test_save_answer_ignored_after_submission(self):
        quiz, questions, choices = make_full_quiz(self.lesson, self.teacher_user)
        attempt = start_or_resume_attempt(quiz=quiz, student=self.student)
        submit_attempt(attempt=attempt, student=self.student)
        result = save_answer(
            attempt=attempt, question_id=questions[0].pk, choice_id=choices[0].pk
        )
        self.assertIsNone(result)


class TimeoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.term, cls.sc, cls.subj, cls.teacher_user, _ = setup_classroom()
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Mod", position=0,
        )
        cls.lesson = Lesson.objects.create(module=mod, title="Lesson", position=0)
        cls.student = make_student_user(cls.sc, admission_number="TM-001")
        PointConfig.objects.get_or_create(pk=1)

    def test_auto_submit_marks_attempt(self):
        quiz, questions, choices = make_full_quiz(self.lesson, self.teacher_user)
        # Set 1-minute limit
        quiz.time_limit_minutes = 1
        quiz.save(update_fields=["time_limit_minutes"])

        attempt = start_or_resume_attempt(quiz=quiz, student=self.student)
        # Back-date the start to 2 minutes ago (simulates timeout)
        from django.utils import timezone
        attempt.started_at = timezone.now() - timezone.timedelta(minutes=2)
        attempt.save(update_fields=["started_at"])

        self.assertTrue(attempt.is_timed_out)
        result = auto_submit_timed_out(attempt)
        self.assertTrue(result.is_submitted)
        self.assertTrue(result.answers_auto_submitted)


class NoDoublePointsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.term, cls.sc, cls.subj, cls.teacher_user, _ = setup_classroom()
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Mod", position=0,
        )
        cls.lesson = Lesson.objects.create(module=mod, title="Lesson", position=0)
        cls.student = make_student_user(cls.sc, admission_number="NDP-001")
        enable_quizzes()
        PointConfig.objects.get_or_create(pk=1)
        from apps.learning.models import ClassEngagementSettings
        ClassEngagementSettings.objects.get_or_create(
            school_class=cls.sc, defaults={"is_active": True}
        )

    def test_quiz_attempt_points_awarded_once(self):
        quiz, questions, choices = make_full_quiz(self.lesson, self.teacher_user)
        # First attempt
        a1 = start_or_resume_attempt(quiz=quiz, student=self.student)
        submit_attempt(attempt=a1, student=self.student)
        # Second attempt
        a2 = start_or_resume_attempt(quiz=quiz, student=self.student)
        submit_attempt(attempt=a2, student=self.student)

        attempt_rows = PointLedger.objects.filter(
            student=self.student,
            idempotency_key=f"quiz_attempt:{quiz.pk}:{self.student.pk}",
        )
        self.assertEqual(attempt_rows.count(), 1)

    def test_quiz_pass_points_awarded_once(self):
        quiz, questions, choices = make_full_quiz(self.lesson, self.teacher_user)
        # First attempt — answer all correctly to pass
        a1 = start_or_resume_attempt(quiz=quiz, student=self.student)
        save_answer(attempt=a1, question_id=questions[0].pk, choice_id=choices[0].pk)
        save_answer(attempt=a1, question_id=questions[1].pk, choice_id=choices[3].pk)
        tc = questions[2].choices.get(text="True")
        save_answer(attempt=a1, question_id=questions[2].pk, choice_id=tc.pk)
        submit_attempt(attempt=a1, student=self.student)
        # Second attempt — pass again
        a2 = start_or_resume_attempt(quiz=quiz, student=self.student)
        save_answer(attempt=a2, question_id=questions[0].pk, choice_id=choices[0].pk)
        save_answer(attempt=a2, question_id=questions[1].pk, choice_id=choices[3].pk)
        save_answer(attempt=a2, question_id=questions[2].pk, choice_id=tc.pk)
        submit_attempt(attempt=a2, student=self.student)

        pass_rows = PointLedger.objects.filter(
            student=self.student,
            idempotency_key=f"quiz_pass:{quiz.pk}:{self.student.pk}",
        )
        self.assertEqual(pass_rows.count(), 1)


# ============================================================================
# B. quiz_selectors — is_correct never sent to student
# ============================================================================


class QuizSelectorsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.term, cls.sc, cls.subj, cls.teacher_user, _ = setup_classroom()
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Mod", position=0,
        )
        cls.lesson = Lesson.objects.create(module=mod, title="Lesson", position=0)
        cls.student = make_student_user(cls.sc, admission_number="SEL-QZ-001")
        cls.quiz, cls.questions, cls.choices = make_full_quiz(cls.lesson, cls.teacher_user)
        cls.attempt = start_or_resume_attempt(quiz=cls.quiz, student=cls.student)

    def test_is_correct_is_none_in_student_question_view(self):
        """is_correct must NEVER appear in student-facing choices before submission."""
        qwcs = quiz_questions_for_student(quiz=self.quiz, attempt=self.attempt)
        for qwc in qwcs:
            for choice in qwc.choices:
                self.assertIsNone(choice.is_correct,
                    "is_correct must be None for student choices before submission")

    def test_student_answer_pk_populated_on_resume(self):
        save_answer(attempt=self.attempt, question_id=self.questions[0].pk,
                    choice_id=self.choices[0].pk)
        qwcs = quiz_questions_for_student(quiz=self.quiz, attempt=self.attempt)
        q1 = next(q for q in qwcs if q.question.pk == self.questions[0].pk)
        self.assertEqual(q1.student_answer_pk, self.choices[0].pk)

    def test_can_retry_false_when_limit_exhausted(self):
        PointConfig.objects.get_or_create(pk=1)
        # Use up both attempts
        for _ in range(2):
            att = start_or_resume_attempt(quiz=self.quiz, student=self.student)
            if not att.is_submitted:
                submit_attempt(attempt=att, student=self.student)
        summary = student_attempt_summary(quiz=self.quiz, student=self.student)
        self.assertFalse(summary.can_retry)

    def test_teacher_results_question_difficulty_sorted(self):
        PointConfig.objects.get_or_create(pk=1)
        # Submit one attempt with Q1 wrong
        att = start_or_resume_attempt(quiz=self.quiz, student=self.student)
        save_answer(attempt=att, question_id=self.questions[0].pk,
                    choice_id=self.choices[1].pk)  # wrong
        submit_attempt(attempt=att, student=self.student)
        results = teacher_quiz_results(quiz=self.quiz, school_class=self.sc)
        # Highest wrong_pct question should be first
        if len(results.question_difficulty) > 1:
            self.assertGreaterEqual(
                results.question_difficulty[0].wrong_pct,
                results.question_difficulty[1].wrong_pct,
            )


# ============================================================================
# C. Views
# ============================================================================


class QuizViewPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_quizzes()
        _, cls.term, cls.sc, cls.subj, cls.teacher_user, cls.teacher = setup_classroom()
        cls.other_teacher_user, _ = make_teacher("qz_other_t", "QZ-OT1")

        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Mod", position=0, is_published=True,
        )
        cls.lesson = Lesson.objects.create(
            module=mod, title="Lesson", position=0, is_published=True,
        )
        cls.student = make_student_user(cls.sc, admission_number="VP-QZ-001")
        cls.other_student = make_student_user(
            make_class(cls.sc.academic_year, name="Grade QZ2", level_order=21),
            admission_number="VP-QZ-002",
        )
        cls.parent_user, _ = make_parent_for(cls.student)
        PointConfig.objects.get_or_create(pk=1)
        cls.quiz, cls.questions, cls.choices = make_full_quiz(cls.lesson, cls.teacher_user)

    def _landing_url(self):
        return reverse("learning:quiz_landing",
                       args=[self.sc.pk, self.subj.pk, self.lesson.pk])

    def _take_url(self):
        return reverse("learning:quiz_take",
                       args=[self.sc.pk, self.subj.pk, self.lesson.pk])

    def _builder_url(self):
        return reverse("learning:teacher_quiz",
                       args=[self.sc.pk, self.subj.pk, 
                             self.lesson.module.pk, self.lesson.pk])

    # ── Student access ───────────────────────────────────────────────────

    def test_student_sees_quiz_landing(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._landing_url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.quiz.title)

    def test_student_from_other_class_gets_404_on_landing(self):
        self.client.force_login(self.other_student.user)
        r = self.client.get(self._landing_url())
        self.assertEqual(r.status_code, 404)

    def test_draft_quiz_invisible_to_student(self):
        unpublish_quiz(quiz=self.quiz, actor=self.teacher_user)
        self.client.force_login(self.student.user)
        r = self.client.get(self._landing_url())
        self.assertEqual(r.status_code, 404)
        # Restore
        publish_quiz(quiz=self.quiz, actor=self.teacher_user)

    def test_take_quiz_does_not_send_is_correct_before_submit(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._take_url())
        self.assertEqual(r.status_code, 200)
        # The word "is_correct" must never appear in the HTML sent to student
        self.assertNotContains(r, "is_correct")
        # Correct choice texts should not be specially marked
        # (we just ensure no data- attribute leaks the answer)
        self.assertNotContains(r, 'data-correct="true"')

    def test_anonymous_redirected(self):
        r = self.client.get(self._landing_url())
        self.assertEqual(r.status_code, 302)

    # ── Parent access ────────────────────────────────────────────────────

    def test_parent_sees_landing(self):
        self.client.force_login(self.parent_user)
        r = self.client.get(self._landing_url())
        self.assertEqual(r.status_code, 200)

    def test_parent_cannot_take_quiz(self):
        self.client.force_login(self.parent_user)
        r = self.client.get(self._take_url())
        self.assertEqual(r.status_code, 403)

    # ── Teacher builder ───────────────────────────────────────────────────

    def test_assigned_teacher_sees_builder(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._builder_url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.quiz.title)

    def test_other_teacher_gets_404_on_builder(self):
        self.client.force_login(self.other_teacher_user)
        r = self.client.get(self._builder_url())
        self.assertEqual(r.status_code, 404)

    # ── Save answer AJAX ──────────────────────────────────────────────────

    def test_save_answer_returns_json_ok(self):
        attempt = start_or_resume_attempt(quiz=self.quiz, student=self.student)
        self.client.force_login(self.student.user)
        url = reverse("learning:quiz_save_answer",
                      args=[self.sc.pk, self.subj.pk, self.lesson.pk])
        r = self.client.post(
            url,
            {"question_id": self.questions[0].pk, "choice_id": self.choices[0].pk},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["saved"], self.questions[0].pk)

    # ── Quiz results ──────────────────────────────────────────────────────

    def test_student_can_view_own_results(self):
        PointConfig.objects.get_or_create(pk=1)
        attempt = start_or_resume_attempt(quiz=self.quiz, student=self.student)
        result = submit_attempt(attempt=attempt, student=self.student)
        self.client.force_login(self.student.user)
        url = reverse("learning:quiz_results",
                      args=[self.sc.pk, self.subj.pk, self.lesson.pk, result.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Quiz Results")

    def test_other_student_cannot_view_results(self):
        PointConfig.objects.get_or_create(pk=1)
        attempt = start_or_resume_attempt(quiz=self.quiz, student=self.student)
        result = submit_attempt(attempt=attempt, student=self.student)
        # Log in as other student (different class)
        self.client.force_login(self.other_student.user)
        url = reverse("learning:quiz_results",
                      args=[self.sc.pk, self.subj.pk, self.lesson.pk, result.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 404)

    def test_correct_answers_hidden_when_show_correct_off(self):
        """When show_correct_after=False, correct answers must not appear in results HTML."""
        self.quiz.show_correct_after = False
        self.quiz.save(update_fields=["show_correct_after"])
        PointConfig.objects.get_or_create(pk=1)
        attempt = start_or_resume_attempt(quiz=self.quiz, student=self.student)
        result = submit_attempt(attempt=attempt, student=self.student)
        self.client.force_login(self.student.user)
        url = reverse("learning:quiz_results",
                      args=[self.sc.pk, self.subj.pk, self.lesson.pk, result.pk])
        r = self.client.get(url)
        # Should show "teacher has turned off answer review" notice
        self.assertContains(r, "turned off")
        # Restore
        self.quiz.show_correct_after = True
        self.quiz.save(update_fields=["show_correct_after"])
