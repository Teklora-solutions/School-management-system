"""
apps/learning/tests/test_engagement.py
---------------------------------------
Tests for Piece 1: progress bars and fair streaks.

Coverage:
  A. streak_utils — pure date arithmetic (no DB)
  B. engagement_services.refresh_streak — the state machine
  C. engagement_selectors.student_progress — progress aggregation
  D. Views — student progress, teacher class progress, director overview,
             mark-lesson-complete, engagement settings
"""

from __future__ import annotations

import datetime

from django.test import TestCase
from django.urls import reverse

from apps.academics.models import ClassSubject
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
    ClassEngagementSettings,
    CourseModule,
    Lesson,
    LearningStreak,
    LessonProgress,
)
from apps.learning.streak_utils import (
    is_school_day,
    school_days_between,
    streak_is_alive,
)
from apps.teachers.models import TeacherAssignment, TeacherProfile


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def enable_both():
    """Turn on both learning and engagement school-wide."""
    row = SchoolSettings.load()
    row.learning_enabled = True
    row.engagement_enabled = True
    row.save(update_fields=["learning_enabled", "engagement_enabled", "updated_at"])


def make_teacher(username, staff_id):
    user = make_user("teacher", username=username)
    profile = TeacherProfile.objects.create(user=user, staff_id=staff_id)
    return user, profile


def make_module(school_class, subject, term, title="Module 1", position=0):
    return CourseModule.objects.create(
        school_class=school_class,
        subject=subject,
        term=term,
        title=title,
        position=position,
    )


def make_lesson(module, title="Lesson 1", position=0, published=True):
    return Lesson.objects.create(
        module=module,
        title=title,
        position=position,
        is_published=published,
    )


def make_term_obj(year, name="term_1", start=None, end=None):
    """Make a term with explicit dates for streak tests."""
    start = start or datetime.date(2025, 9, 1)
    end = end or datetime.date(2025, 12, 19)
    from apps.academics.models import Term
    return Term.objects.create(
        academic_year=year,
        name=name,
        start_date=start,
        end_date=end,
        is_current=True,
    )


# ============================================================================
# A. streak_utils — pure functions
# ============================================================================


class IsSchoolDayTests(TestCase):
    """is_school_day() — weekdays inside a term are school days."""

    @classmethod
    def setUpTestData(cls):
        year = make_year()
        # Term: Mon 1 Sep 2025 – Fri 19 Dec 2025
        from apps.academics.models import Term
        cls.term = Term.objects.create(
            academic_year=year,
            name="term_1",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 19),
            is_current=True,
        )
        cls.terms = [cls.term]

    def test_monday_inside_term_is_school_day(self):
        # 1 Sep 2025 is a Monday
        self.assertTrue(is_school_day(datetime.date(2025, 9, 1), self.terms))

    def test_friday_inside_term_is_school_day(self):
        self.assertTrue(is_school_day(datetime.date(2025, 9, 5), self.terms))

    def test_saturday_inside_term_is_not_school_day(self):
        self.assertFalse(is_school_day(datetime.date(2025, 9, 6), self.terms))

    def test_sunday_inside_term_is_not_school_day(self):
        self.assertFalse(is_school_day(datetime.date(2025, 9, 7), self.terms))

    def test_weekday_outside_term_is_not_school_day(self):
        # 25 Aug 2025 is a Monday before term starts
        self.assertFalse(is_school_day(datetime.date(2025, 8, 25), self.terms))

    def test_weekday_on_term_end_is_school_day(self):
        self.assertTrue(is_school_day(datetime.date(2025, 12, 19), self.terms))

    def test_weekday_day_after_term_ends_is_not(self):
        self.assertFalse(is_school_day(datetime.date(2025, 12, 22), self.terms))


class SchoolDaysBetweenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        year = make_year()
        from apps.academics.models import Term
        cls.term = Term.objects.create(
            academic_year=year,
            name="term_1",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 19),
            is_current=True,
        )
        cls.terms = [cls.term]

    def test_same_day_returns_zero(self):
        d = datetime.date(2025, 9, 3)
        self.assertEqual(school_days_between(d, d, self.terms), 0)

    def test_end_before_start_returns_zero(self):
        self.assertEqual(
            school_days_between(
                datetime.date(2025, 9, 5),
                datetime.date(2025, 9, 3),
                self.terms,
            ),
            0,
        )

    def test_mon_to_tue_is_one_school_day(self):
        # Mon 1 Sep → Tue 2 Sep: exactly 1 school day after Mon
        self.assertEqual(
            school_days_between(
                datetime.date(2025, 9, 1),
                datetime.date(2025, 9, 2),
                self.terms,
            ),
            1,
        )

    def test_fri_to_mon_skips_weekend(self):
        # Fri 5 Sep → Mon 8 Sep: only Mon counts, weekend skipped → 1
        self.assertEqual(
            school_days_between(
                datetime.date(2025, 9, 5),
                datetime.date(2025, 9, 8),
                self.terms,
            ),
            1,
        )

    def test_whole_week_is_five_days(self):
        # Mon 1 Sep → Fri 5 Sep: 4 days after Mon (Tue–Fri)
        self.assertEqual(
            school_days_between(
                datetime.date(2025, 9, 1),
                datetime.date(2025, 9, 5),
                self.terms,
            ),
            4,
        )

    def test_span_across_holiday_gap(self):
        # If end of term is 19 Dec and start of next is e.g. 12 Jan,
        # a date in that gap is not a school day.
        from apps.academics.models import AcademicYear, Term
        year2 = AcademicYear.objects.create(
            name="2025/2026b",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=False,
        )
        t2 = Term.objects.create(
            academic_year=year2,
            name="term_2",
            start_date=datetime.date(2026, 1, 12),
            end_date=datetime.date(2026, 3, 27),
            is_current=False,
        )
        terms = [self.term, t2]
        # 19 Dec (last day term 1) → 12 Jan (first day term 2)
        # Only 12 Jan counts (holiday gap not counted)
        result = school_days_between(
            datetime.date(2025, 12, 19),
            datetime.date(2026, 1, 12),
            terms,
        )
        self.assertEqual(result, 1)


class StreakIsAliveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        year = make_year()
        from apps.academics.models import Term
        cls.term = Term.objects.create(
            academic_year=year,
            name="term_1",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 19),
            is_current=True,
        )
        cls.terms = [cls.term]

    def test_no_previous_activity_is_not_alive(self):
        self.assertFalse(
            streak_is_alive(None, datetime.date(2025, 9, 3), 1, self.terms)
        )

    def test_active_today_is_alive(self):
        d = datetime.date(2025, 9, 3)
        self.assertTrue(streak_is_alive(d, d, 1, self.terms))

    def test_active_yesterday_grace_1_is_alive(self):
        # Mon active, check Tue: missed=1 school day, (1-1)=0 <= grace 1 → alive
        self.assertTrue(
            streak_is_alive(
                datetime.date(2025, 9, 1),
                datetime.date(2025, 9, 2),
                grace_days=1,
                terms=self.terms,
            )
        )

    def test_missed_one_school_day_with_grace_1_is_alive(self):
        # Active Mon, check Wed: missed 2 school days (Tue+Wed), (2-1)=1 <= 1 → alive
        self.assertTrue(
            streak_is_alive(
                datetime.date(2025, 9, 1),
                datetime.date(2025, 9, 3),
                grace_days=1,
                terms=self.terms,
            )
        )

    def test_missed_two_school_days_with_grace_1_is_broken(self):
        # Active Mon, check Thu: missed 3 school days (Tue, Wed, Thu), (3-1)=2 > 1 → broken
        self.assertFalse(
            streak_is_alive(
                datetime.date(2025, 9, 1),
                datetime.date(2025, 9, 4),
                grace_days=1,
                terms=self.terms,
            )
        )

    def test_weekend_gap_does_not_break_streak(self):
        # Active Fri 5 Sep, check Mon 8 Sep: no school days in between → alive
        self.assertTrue(
            streak_is_alive(
                datetime.date(2025, 9, 5),
                datetime.date(2025, 9, 8),
                grace_days=0,  # even with zero grace
                terms=self.terms,
            )
        )

    def test_friday_to_monday_with_grace_0_still_alive(self):
        # 0 grace but the gap contains only weekend → still alive
        self.assertTrue(
            streak_is_alive(
                datetime.date(2025, 9, 5),
                datetime.date(2025, 9, 8),
                grace_days=0,
                terms=self.terms,
            )
        )

    def test_term_break_does_not_break_streak(self):
        # Active last day of term 1, check first day of term 2.
        # Holiday between is not school days → gap = 0 or 1 school day.
        from apps.academics.models import AcademicYear, Term
        year2 = AcademicYear.objects.create(
            name="2025/2026c",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=False,
        )
        t2 = Term.objects.create(
            academic_year=year2,
            name="term_2",
            start_date=datetime.date(2026, 1, 12),
            end_date=datetime.date(2026, 3, 27),
            is_current=False,
        )
        terms = [self.term, t2]
        # Last day term 1: Fri 19 Dec, first day term 2: Mon 12 Jan
        # school_days_between(19 Dec, 12 Jan) = 1 (only 12 Jan itself)
        # streak_is_alive: (1-1)=0 <= grace 1 → alive
        self.assertTrue(
            streak_is_alive(
                datetime.date(2025, 12, 19),
                datetime.date(2026, 1, 12),
                grace_days=1,
                terms=terms,
            )
        )


# ============================================================================
# B. engagement_services.refresh_streak
# ============================================================================


class RefreshStreakTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        from apps.academics.models import Term
        cls.term = Term.objects.create(
            academic_year=cls.year,
            name="term_1",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 19),
            is_current=True,
        )
        cls.terms = [cls.term]
        cls.student = make_student_user(
            make_class(cls.year, name="Eng 5"), admission_number="ENG-001"
        )

    def _refresh(self, today, grace=1):
        from apps.learning.engagement_services import refresh_streak
        return refresh_streak(
            student=self.student,
            today=today,
            terms=self.terms,
            grace_days=grace,
        )

    def test_first_completion_creates_streak_of_one(self):
        streak = self._refresh(datetime.date(2025, 9, 1))
        self.assertEqual(streak.current_streak, 1)
        self.assertEqual(streak.longest_streak, 1)
        self.assertEqual(streak.last_active_date, datetime.date(2025, 9, 1))
        self.assertIsNone(streak.streak_broken_at)

    def test_second_completion_same_day_is_idempotent(self):
        self._refresh(datetime.date(2025, 9, 1))
        streak = self._refresh(datetime.date(2025, 9, 1))
        self.assertEqual(streak.current_streak, 1)

    def test_consecutive_days_increment_streak(self):
        self._refresh(datetime.date(2025, 9, 1))
        self._refresh(datetime.date(2025, 9, 2))
        streak = self._refresh(datetime.date(2025, 9, 3))
        self.assertEqual(streak.current_streak, 3)
        self.assertEqual(streak.longest_streak, 3)

    def test_weekend_gap_does_not_reset_streak(self):
        self._refresh(datetime.date(2025, 9, 5))  # Friday
        streak = self._refresh(datetime.date(2025, 9, 8))  # Monday
        self.assertEqual(streak.current_streak, 2)

    def test_one_missed_school_day_with_grace_1_survives(self):
        self._refresh(datetime.date(2025, 9, 1))  # Mon
        streak = self._refresh(datetime.date(2025, 9, 3))  # Wed (skipped Tue)
        self.assertEqual(streak.current_streak, 2)
        self.assertIsNone(streak.streak_broken_at)

    def test_two_missed_school_days_resets_streak(self):
        self._refresh(datetime.date(2025, 9, 1))  # Mon
        streak = self._refresh(datetime.date(2025, 9, 4))  # Thu (skipped Tue+Wed)
        self.assertEqual(streak.current_streak, 1)
        self.assertEqual(streak.streak_broken_at, datetime.date(2025, 9, 4))

    def test_longest_streak_never_decrements(self):
        self._refresh(datetime.date(2025, 9, 1))
        self._refresh(datetime.date(2025, 9, 2))
        self._refresh(datetime.date(2025, 9, 3))
        # Break it
        self._refresh(datetime.date(2025, 9, 8))  # Mon after big skip
        streak = LearningStreak.objects.get(student=self.student)
        self.assertEqual(streak.longest_streak, 3)
        self.assertEqual(streak.current_streak, 1)

    def test_streak_broken_at_cleared_on_next_completion(self):
        self._refresh(datetime.date(2025, 9, 1))
        self._refresh(datetime.date(2025, 9, 8))  # break
        streak = self._refresh(datetime.date(2025, 9, 9))  # next day
        # Alive now (only 1 school day gap Mon→Tue with grace 1)
        self.assertIsNone(streak.streak_broken_at)


# ============================================================================
# C. engagement_selectors.student_progress
# ============================================================================


class StudentProgressSelectorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_both()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.subject = make_subject()
        ClassSubject.objects.create(
            school_class=cls.school_class, subject=cls.subject
        )
        cls.student = make_student_user(
            cls.school_class, admission_number="SP-001"
        )
        # Engagement settings for the class
        ClassEngagementSettings.objects.create(
            school_class=cls.school_class,
            is_active=True,
        )
        # Course: 2 modules, 3 lessons total
        cls.mod1 = make_module(cls.school_class, cls.subject, cls.term, "Module 1", 0)
        cls.mod2 = make_module(cls.school_class, cls.subject, cls.term, "Module 2", 1)
        cls.l1 = make_lesson(cls.mod1, "Lesson 1", 0)
        cls.l2 = make_lesson(cls.mod1, "Lesson 2", 1)
        cls.l3 = make_lesson(cls.mod2, "Lesson 3", 0)

    def _progress(self):
        from apps.learning.engagement_selectors import student_progress
        return student_progress(student=self.student, term=self.term)

    def test_no_completions_gives_zero_percent(self):
        p = self._progress()
        self.assertEqual(p.overall_pct, 0)
        self.assertEqual(p.completed_lessons, 0)
        self.assertEqual(p.total_lessons, 3)

    def test_next_lesson_is_first_unpublished(self):
        p = self._progress()
        self.assertEqual(p.next_lesson, self.l1)

    def test_partial_completion_reflects_in_pct(self):
        LessonProgress.objects.create(
            student=self.student,
            lesson=self.l1,
            completed=True,
        )
        LessonProgress.objects.create(
            student=self.student,
            lesson=self.l2,
            completed=True,
        )
        p = self._progress()
        self.assertEqual(p.completed_lessons, 2)
        self.assertEqual(p.overall_pct, 67)  # 2/3 rounded

    def test_next_lesson_advances_past_completed(self):
        LessonProgress.objects.create(
            student=self.student,
            lesson=self.l1,
            completed=True,
        )
        p = self._progress()
        self.assertEqual(p.next_lesson, self.l2)

    def test_unpublished_lesson_excluded_from_count(self):
        make_lesson(self.mod1, "Hidden", 99, published=False)
        p = self._progress()
        self.assertEqual(p.total_lessons, 3)  # unpublished not counted

    def test_all_complete_sets_is_course_complete(self):
        for lesson in [self.l1, self.l2, self.l3]:
            LessonProgress.objects.create(
                student=self.student, lesson=lesson, completed=True
            )
        p = self._progress()
        self.assertTrue(p.is_course_complete)
        self.assertIsNone(p.next_lesson)

    def test_streak_shown_from_model(self):
        LearningStreak.objects.create(
            student=self.student,
            current_streak=5,
            longest_streak=7,
            last_active_date=datetime.date(2025, 9, 10),
        )
        p = self._progress()
        self.assertEqual(p.current_streak, 5)
        self.assertEqual(p.longest_streak, 7)

    def test_welcome_back_flag_when_streak_broken(self):
        LearningStreak.objects.create(
            student=self.student,
            current_streak=1,
            longest_streak=5,
            last_active_date=datetime.date(2025, 9, 10),
            streak_broken_at=datetime.date(2025, 9, 10),
        )
        p = self._progress()
        self.assertTrue(p.is_welcome_back)


# ============================================================================
# D. Views
# ============================================================================


class StudentProgressViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_both()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.subject = make_subject()
        ClassSubject.objects.create(
            school_class=cls.school_class, subject=cls.subject
        )
        cls.student = make_student_user(
            cls.school_class, admission_number="VP-001"
        )
        ClassEngagementSettings.objects.create(
            school_class=cls.school_class, is_active=True
        )

    def test_student_can_view_progress_page(self):
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("learning:student_progress"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Progress")

    def test_parent_can_view_childs_progress(self):
        from apps.accounts.models import ParentProfile
        from apps.students.models import StudentGuardian
        parent_user = make_user("parent", username="eng_parent")
        profile = ParentProfile.objects.create(user=parent_user)
        StudentGuardian.objects.create(
            student=self.student, parent=profile, is_primary=True
        )
        self.client.force_login(parent_user)
        response = self.client.get(reverse("learning:student_progress"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Progress")

    def test_teacher_cannot_access_student_progress_page(self):
        teacher_user, _ = make_teacher("eng_teach_vp", "STF-VP1")
        self.client.force_login(teacher_user)
        response = self.client.get(reverse("learning:student_progress"))
        self.assertEqual(response.status_code, 403)

    def test_engagement_off_redirects(self):
        row = SchoolSettings.load()
        row.engagement_enabled = False
        row.save(update_fields=["engagement_enabled", "updated_at"])
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("learning:student_progress"))
        # Redirects to dashboard
        self.assertEqual(response.status_code, 302)
        # Re-enable for other tests
        row.engagement_enabled = True
        row.save(update_fields=["engagement_enabled", "updated_at"])

    def test_anonymous_is_redirected(self):
        response = self.client.get(reverse("learning:student_progress"))
        self.assertEqual(response.status_code, 302)


class TeacherClassProgressViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_both()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 6", level_order=6)
        cls.subject = make_subject("ENG", "English")
        ClassSubject.objects.create(
            school_class=cls.school_class, subject=cls.subject
        )
        cls.teacher_user, cls.teacher = make_teacher("cp_teacher", "STF-CP1")
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.subject,
            academic_year=cls.year,
        )
        ClassEngagementSettings.objects.create(
            school_class=cls.school_class, is_active=True
        )
        cls.student = make_student_user(
            cls.school_class, admission_number="CP-001"
        )

    def _url(self):
        return reverse(
            "learning:teacher_class_progress",
            args=[self.school_class.pk, self.subject.pk],
        )

    def test_teacher_sees_class_progress_page(self):
        self.client.force_login(self.teacher_user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Class Progress")
        self.assertContains(response, "CP-001")

    def test_teacher_for_other_class_gets_404(self):
        other_teacher_user, _ = make_teacher("cp_other", "STF-CP2")
        self.client.force_login(other_teacher_user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 404)

    def test_student_cannot_access(self):
        self.client.force_login(self.student.user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 403)


class MarkLessonCompleteViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_both()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 7", level_order=7)
        cls.subject = make_subject("SCI", "Science")
        ClassSubject.objects.create(
            school_class=cls.school_class, subject=cls.subject
        )
        ClassEngagementSettings.objects.create(
            school_class=cls.school_class, is_active=True
        )
        cls.student = make_student_user(
            cls.school_class, admission_number="ML-001"
        )
        mod = make_module(cls.school_class, cls.subject, cls.term)
        cls.lesson = make_lesson(mod)
        # Lesson in another class — must be rejected
        other_class = make_class(cls.year, name="Grade 8", level_order=8)
        other_mod = make_module(other_class, cls.subject, cls.term, "Other mod")
        cls.other_lesson = make_lesson(other_mod, "Other lesson")

    def _url(self, lesson=None):
        lesson = lesson or self.lesson
        return reverse("learning:mark_lesson_complete", args=[lesson.pk])

    def test_post_marks_lesson_complete(self):
        self.client.force_login(self.student.user)
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            LessonProgress.objects.filter(
                student=self.student, lesson=self.lesson, completed=True
            ).exists()
        )

    def test_post_creates_streak(self):
        self.client.force_login(self.student.user)
        self.client.post(self._url())
        self.assertTrue(
            LearningStreak.objects.filter(student=self.student).exists()
        )

    def test_post_to_other_class_lesson_is_404(self):
        self.client.force_login(self.student.user)
        response = self.client.post(self._url(self.other_lesson))
        self.assertEqual(response.status_code, 404)

    def test_get_is_404(self):
        self.client.force_login(self.student.user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 404)

    def test_teacher_cannot_post(self):
        teacher_user, _ = make_teacher("ml_teacher", "STF-ML1")
        self.client.force_login(teacher_user)
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 403)


class ClassEngagementSettingsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_both()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 5", level_order=5)
        cls.subject = make_subject("MATH2", "Mathematics II")
        cls.teacher_user, cls.teacher = make_teacher("es_teacher", "STF-ES1")
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.subject,
            academic_year=cls.year,
        )

    def _url(self):
        return reverse(
            "learning:class_engagement_settings",
            args=[self.school_class.pk, self.subject.pk],
        )

    def test_get_shows_settings_form(self):
        self.client.force_login(self.teacher_user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Engagement Settings")

    def test_post_creates_and_saves_settings(self):
        self.client.force_login(self.teacher_user)
        self.client.post(
            self._url(),
            {
                "is_active": "1",
                "show_progress_bars": "1",
                "show_streak": "1",
                "streak_grace_days": "2",
            },
        )
        eng = ClassEngagementSettings.objects.get(school_class=self.school_class)
        self.assertTrue(eng.is_active)
        self.assertEqual(eng.streak_grace_days, 2)

    def test_post_can_turn_off_engagement(self):
        ClassEngagementSettings.objects.create(
            school_class=self.school_class, is_active=True
        )
        self.client.force_login(self.teacher_user)
        self.client.post(
            self._url(),
            {"streak_grace_days": "1"},  # no is_active checkbox → off
        )
        eng = ClassEngagementSettings.objects.get(school_class=self.school_class)
        self.assertFalse(eng.is_active)

    def test_student_cannot_access_settings(self):
        student = make_student_user(self.school_class, admission_number="ES-001")
        self.client.force_login(student.user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 403)


class DirectorEngagementOverviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_both()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 4", level_order=4)
        ClassEngagementSettings.objects.create(
            school_class=cls.school_class, is_active=True
        )
        cls.director_user = make_user("director", username="eng_director")

    def test_director_sees_overview(self):
        self.client.force_login(self.director_user)
        response = self.client.get(reverse("learning:director_engagement"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Learning Engagement")
        self.assertContains(response, "Grade 4")

    def test_teacher_cannot_access(self):
        teacher_user, _ = make_teacher("dir_teacher", "STF-DIR1")
        self.client.force_login(teacher_user)
        response = self.client.get(reverse("learning:director_engagement"))
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_access(self):
        student = make_student_user(self.school_class, admission_number="DIR-001")
        self.client.force_login(student.user)
        response = self.client.get(reverse("learning:director_engagement"))
        self.assertEqual(response.status_code, 403)
