"""
apps/learning/engagement_selectors.py
--------------------------------------
Read-only queries for the engagement layer (Piece 1).

Four public functions:
  student_progress(student, term)
      Everything one student's progress page needs: per-module bars,
      overall course bar, streak, next-up lesson, and welcome-back flag.

  class_progress_overview(school_class, term)
      Teacher view: every student's overall completion %, who hasn't
      started, and who is behind (< 25 % through the course midpoint).

  engagement_summary_for_class(school_class, term)
      Lightweight version used by the director overview.

  director_engagement_overview(academic_year)
      One row per class in the year: active students, avg completion,
      whether engagement is switched on.

All functions are read-only (no writes, no @transaction.atomic).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional

from django.db.models import Count
from django.utils import timezone

from apps.academics.models import SchoolClass, Term
from apps.academics.selectors import get_current_term

from .models import (
    ClassEngagementSettings,
    CourseModule,
    Lesson,
    LearningStreak,
    LessonProgress,
)
from .course_selectors import lesson_targets_student
from .release import is_released_at
from .streak_utils import days_since_last_active


def _counts_for(lesson, student) -> bool:
    """A lesson counts toward a pupil's progress only when they can open it now."""
    return (
        lesson.is_published
        and is_released_at(lesson)
        and lesson_targets_student(lesson, student)
    )


# ---------------------------------------------------------------------------
# Data classes returned to views
# ---------------------------------------------------------------------------


@dataclass
class ModuleProgressData:
    module: CourseModule
    total_lessons: int
    completed_lessons: int

    @property
    def pct(self) -> int:
        if self.total_lessons == 0:
            return 0
        return round(self.completed_lessons * 100 / self.total_lessons)

    @property
    def is_complete(self) -> bool:
        return self.total_lessons > 0 and self.completed_lessons >= self.total_lessons


@dataclass
class StudentProgressData:
    """Everything the student/parent progress template needs."""

    student: object
    modules: list[ModuleProgressData] = field(default_factory=list)
    total_lessons: int = 0
    completed_lessons: int = 0
    current_streak: int = 0
    longest_streak: int = 0
    last_active_date: Optional[datetime.date] = None
    streak_broken_at: Optional[datetime.date] = None
    next_lesson: Optional[object] = None          # Lesson instance or None
    show_progress_bars: bool = True
    show_streak: bool = True
    engagement_active: bool = False

    @property
    def overall_pct(self) -> int:
        if self.total_lessons == 0:
            return 0
        return round(self.completed_lessons * 100 / self.total_lessons)

    @property
    def is_welcome_back(self) -> bool:
        """True when the student had a streak that was reset."""
        return self.streak_broken_at is not None

    @property
    def is_course_complete(self) -> bool:
        return self.total_lessons > 0 and self.completed_lessons >= self.total_lessons


@dataclass
class StudentClassRow:
    """One row in the teacher's class progress table."""

    student: object
    completed: int
    total: int
    last_active_date: Optional[datetime.date]
    current_streak: int

    @property
    def pct(self) -> int:
        if self.total == 0:
            return 0
        return round(self.completed * 100 / self.total)

    @property
    def nothing_assigned(self) -> bool:
        return self.total == 0

    @property
    def not_started(self) -> bool:
        return self.total > 0 and self.completed == 0

    @property
    def is_behind(self) -> bool:
        """Behind = less than 25 % done and more than half the course exists."""
        return self.total >= 4 and self.pct < 25


@dataclass
class ClassProgressData:
    """Teacher view: full class breakdown."""

    school_class: SchoolClass
    term: Term
    subject_name: str
    rows: list[StudentClassRow] = field(default_factory=list)
    total_lessons: int = 0
    engagement_settings: Optional[ClassEngagementSettings] = None

    @property
    def started_count(self) -> int:
        return sum(1 for r in self.rows if r.completed > 0)

    @property
    def not_started_count(self) -> int:
        return sum(1 for r in self.rows if r.not_started)

    @property
    def behind_count(self) -> int:
        return sum(1 for r in self.rows if r.is_behind)

    @property
    def avg_pct(self) -> int:
        assigned_rows = [r for r in self.rows if r.total > 0]
        if not assigned_rows:
            return 0
        return round(sum(r.pct for r in assigned_rows) / len(assigned_rows))


@dataclass
class DirectorClassRow:
    """One row in the director engagement overview."""

    school_class: SchoolClass
    engagement_on: bool
    student_count: int
    active_student_count: int      # completed >= 1 lesson
    avg_completion_pct: int
    total_lessons: int


# ---------------------------------------------------------------------------
# Public selectors
# ---------------------------------------------------------------------------


def _get_eng(school_class) -> Optional[ClassEngagementSettings]:
    try:
        return school_class.engagement_settings
    except ClassEngagementSettings.DoesNotExist:
        return None


def student_progress(*, student, term: Optional[Term] = None) -> StudentProgressData:
    """
    Everything the student (or parent) progress page needs.

    Falls back gracefully when engagement is off or the class has no
    course content yet.
    """
    if term is None:
        term = get_current_term()

    school_class = student.current_class
    eng = _get_eng(school_class) if school_class else None

    # Build the base data object (safe even with no content).
    data = StudentProgressData(
        student=student,
        engagement_active=bool(eng and eng.is_active),
        show_progress_bars=bool(eng and eng.show_progress_bars) if eng else True,
        show_streak=bool(eng and eng.show_streak) if eng else True,
    )

    if not school_class or not term:
        return data

    # Streak
    try:
        streak = student.learning_streak
        data.current_streak = streak.current_streak
        data.longest_streak = streak.longest_streak
        data.last_active_date = streak.last_active_date
        data.streak_broken_at = streak.streak_broken_at
    except LearningStreak.DoesNotExist:
        pass

    # Modules and lessons for this class/term (no subject filter — all subjects)
    modules = list(
        CourseModule.objects.filter(
            school_class=school_class,
            term=term,
        )
        .prefetch_related("lessons__assigned_students")
        .order_by("position", "id")
    )
    modules = [m for m in modules if is_released_at(m)]

    if not modules:
        return data

    # Completed lesson IDs for this student
    all_lesson_ids = [
        lesson.pk
        for mod in modules
        for lesson in mod.lessons.all()
        if _counts_for(lesson, student)
    ]
    data.total_lessons = len(all_lesson_ids)

    completed_ids = set(
        LessonProgress.objects.filter(
            student=student,
            lesson_id__in=all_lesson_ids,
            completed=True,
        ).values_list("lesson_id", flat=True)
    )
    data.completed_lessons = len(completed_ids)

    # Per-module breakdown
    for mod in modules:
        published = [l for l in mod.lessons.all() if _counts_for(l, student)]
        done = sum(1 for l in published if l.pk in completed_ids)
        data.modules.append(
            ModuleProgressData(
                module=mod,
                total_lessons=len(published),
                completed_lessons=done,
            )
        )

    # Next-up lesson: first published lesson not yet completed, in order
    data.next_lesson = _find_next_lesson(modules, completed_ids, student)

    return data


def _find_next_lesson(modules, completed_ids: set, student):
    """First released, assigned, incomplete lesson across all modules in order."""
    for mod in modules:
        for lesson in sorted(mod.lessons.all(), key=lambda l: (l.position, l.pk)):
            if lesson.pk not in completed_ids and _counts_for(lesson, student):
                return lesson
    return None


def class_progress_overview(
    *,
    school_class: SchoolClass,
    subject_id: int,
    term: Optional[Term] = None,
) -> ClassProgressData:
    """
    Teacher view: per-student progress for one classroom (class + subject).
    """
    if term is None:
        term = get_current_term()

    eng = _get_eng(school_class)

    data = ClassProgressData(
        school_class=school_class,
        term=term,
        subject_name="",
        engagement_settings=eng,
    )

    if not term:
        return data

    # Course modules for this class/subject/term
    modules = list(
        CourseModule.objects.filter(
            school_class=school_class,
            subject_id=subject_id,
            term=term,
        ).prefetch_related("lessons__assigned_students")
    )
    modules = [m for m in modules if is_released_at(m)]

    released_lessons = [
        l
        for mod in modules
        for l in mod.lessons.all()
        if l.is_published and is_released_at(l)
    ]
    lesson_ids = [l.pk for l in released_lessons]
    data.total_lessons = len(lesson_ids)

    # Try to get subject name from first module
    if modules:
        try:
            from apps.academics.models import Subject
            subj = Subject.objects.filter(pk=subject_id).first()
            data.subject_name = subj.name if subj else ""
        except Exception:  # noqa: BLE001
            pass

    # Students in this class
    from apps.students.models import Student
    students = list(
        Student.objects.filter(
            current_class=school_class,
            status=Student.Status.ACTIVE,
        ).select_related("user", "learning_streak")
    )

    if not lesson_ids:
        # No lessons yet — show roster with zeroes
        for stu in students:
            data.rows.append(
                StudentClassRow(
                    student=stu,
                    completed=0,
                    total=0,
                    last_active_date=None,
                    current_streak=0,
                )
            )
        return data

    # Bulk-fetch all completions for these students and lessons
    completions: dict[int, set[int]] = {stu.pk: set() for stu in students}
    for lp in LessonProgress.objects.filter(
        student_id__in=[s.pk for s in students],
        lesson_id__in=lesson_ids,
        completed=True,
    ).values("student_id", "lesson_id"):
        completions[lp["student_id"]].add(lp["lesson_id"])

    # Streak data (already prefetched via select_related above)
    for stu in students:
        # Lessons assigned to chosen pupils only count for those pupils.
        mine = {l.pk for l in released_lessons if lesson_targets_student(l, stu)}
        done = len(completions.get(stu.pk, set()) & mine)
        try:
            streak = stu.learning_streak
            last_active = streak.last_active_date
            current_streak = streak.current_streak
        except LearningStreak.DoesNotExist:
            last_active = None
            current_streak = 0

        data.rows.append(
            StudentClassRow(
                student=stu,
                completed=done,
                total=len(mine),
                last_active_date=last_active,
                current_streak=current_streak,
            )
        )

    # Sort: most progress first, then alphabetically
    data.rows.sort(key=lambda r: (-r.pct, r.student.user.last_name, r.student.user.first_name))
    return data


def director_engagement_overview(*, academic_year) -> list[DirectorClassRow]:
    """
    One row per class in *academic_year* for the director's overview panel.
    Lessons, targeting and completions are fetched in bulk per enabled class.
    """
    from apps.students.models import Student

    classes = list(
        SchoolClass.objects.filter(academic_year=academic_year)
        .prefetch_related("engagement_settings")
        .order_by("level_order", "name")
    )
    if not classes:
        return []

    term = get_current_term()

    # Student counts per class
    student_counts: dict[int, int] = {
        row["current_class_id"]: row["count"]
        for row in Student.objects.filter(
            current_class__academic_year=academic_year,
            status=Student.Status.ACTIVE,
        ).values("current_class_id").annotate(count=Count("id"))
    }

    rows = []
    for cls in classes:
        eng = _get_eng(cls)

        # Lesson count for this class in the current term
        total_lessons = 0
        avg_pct = 0
        active_students = 0

        if term and eng and eng.is_active:
            now = timezone.now()
            released_lessons = [
                lesson
                for lesson in Lesson.objects.filter(
                    module__school_class=cls,
                    module__term=term,
                    is_published=True,
                    module__is_published=True,
                ).select_related("module").prefetch_related("assigned_students")
                if is_released_at(lesson.module, now=now)
                and is_released_at(lesson, now=now)
            ]
            lesson_ids = [lesson.pk for lesson in released_lessons]
            total_lessons = len(lesson_ids)

            if lesson_ids:
                students = list(Student.objects.filter(
                    current_class=cls,
                    status=Student.Status.ACTIVE,
                ))
                completions: dict[int, set[int]] = {stu.pk: set() for stu in students}
                for student_id, lesson_id in LessonProgress.objects.filter(
                    student_id__in=completions,
                    lesson_id__in=lesson_ids,
                    completed=True,
                ).values_list("student_id", "lesson_id"):
                    completions[student_id].add(lesson_id)

                percentages = []
                for stu in students:
                    assigned_ids = {
                        lesson.pk for lesson in released_lessons
                        if lesson_targets_student(lesson, stu)
                    }
                    if not assigned_ids:
                        continue
                    completed = len(completions[stu.pk] & assigned_ids)
                    active_students += int(completed > 0)
                    percentages.append(completed * 100 / len(assigned_ids))
                if percentages:
                    avg_pct = round(sum(percentages) / len(percentages))

        rows.append(
            DirectorClassRow(
                school_class=cls,
                engagement_on=bool(eng and eng.is_active),
                student_count=student_counts.get(cls.pk, 0),
                active_student_count=active_students,
                avg_completion_pct=avg_pct,
                total_lessons=total_lessons,
            )
        )

    return rows
