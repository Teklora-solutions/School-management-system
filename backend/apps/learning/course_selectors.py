"""
apps/learning/course_selectors.py
------------------------------------
Read-only queries for the course builder and student learning experience.

Public API
----------
  course_for_teacher(school_class, subject, term)
      → list[ModuleWithLessons]  (all modules + all lessons, including drafts)

  course_for_student(school_class, subject, term, student)
      → list[ModuleWithLessons]  (published modules + published lessons only)
      Each LessonWithProgress carries the student's completion status.

  lesson_detail_for_teacher(lesson_id, teacher_user_id)
      → Lesson | None  (any publish state)

  lesson_detail_for_student(lesson_id, student)
      → Lesson | None  (published only, class-checked)

  available_terms_for_copy(school_class, subject, exclude_term)
      → QuerySet[Term]   for the copy-course target dropdown

  available_classes_for_copy(academic_year, exclude_class)
      → QuerySet[SchoolClass]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import CourseModule, Lesson, LessonAttachment, LessonProgress


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class LessonWithProgress:
    lesson: Lesson
    completed: bool = False
    attachments: list = field(default_factory=list)


@dataclass
class ModuleWithLessons:
    module: CourseModule
    lessons: list[LessonWithProgress] = field(default_factory=list)

    @property
    def total_lessons(self) -> int:
        return len(self.lessons)

    @property
    def published_lessons(self) -> int:
        return sum(1 for lp in self.lessons if lp.lesson.is_published)

    @property
    def completed_lessons(self) -> int:
        return sum(1 for lp in self.lessons if lp.completed)

    @property
    def pct_complete(self) -> int:
        pub = self.published_lessons
        if pub == 0:
            return 0
        return round(self.completed_lessons * 100 / pub)


# ---------------------------------------------------------------------------
# Public selectors
# ---------------------------------------------------------------------------


def course_for_teacher(*, school_class, subject, term) -> list[ModuleWithLessons]:
    """
    All modules and lessons for a classroom, including drafts.
    Used by the teacher course builder and classroom detail page.
    """
    modules = list(
        CourseModule.objects.filter(
            school_class=school_class,
            subject=subject,
            term=term,
        )
        .prefetch_related("lessons__lesson_attachments")
        .order_by("position", "id")
    )
    result = []
    for mod in modules:
        lessons = [
            LessonWithProgress(
                lesson=l,
                completed=False,
                attachments=list(l.lesson_attachments.all()),
            )
            for l in sorted(mod.lessons.all(), key=lambda l: (l.position, l.pk))
        ]
        result.append(ModuleWithLessons(module=mod, lessons=lessons))
    return result


def course_for_student(
    *, school_class, subject, term, student
) -> list[ModuleWithLessons]:
    """
    Published modules and lessons only, annotated with the student's
    completion status.  One DB query for progress after the initial fetch.
    """
    modules = list(
        CourseModule.objects.filter(
            school_class=school_class,
            subject=subject,
            term=term,
            is_published=True,
        )
        .prefetch_related("lessons__lesson_attachments")
        .order_by("position", "id")
    )
    if not modules:
        return []

    # Collect published lesson PKs
    all_lesson_ids = [
        l.pk
        for mod in modules
        for l in mod.lessons.all()
        if l.is_published
    ]

    # Bulk-fetch completions
    completed_ids = set(
        LessonProgress.objects.filter(
            student=student,
            lesson_id__in=all_lesson_ids,
            completed=True,
        ).values_list("lesson_id", flat=True)
    )

    result = []
    for mod in modules:
        lessons = [
            LessonWithProgress(
                lesson=l,
                completed=l.pk in completed_ids,
                attachments=list(l.lesson_attachments.all()),
            )
            for l in sorted(mod.lessons.all(), key=lambda l: (l.position, l.pk))
            if l.is_published
        ]
        result.append(ModuleWithLessons(module=mod, lessons=lessons))
    return result


def lesson_detail_for_teacher(
    *, lesson_id: int, teacher_user_id: int
) -> Optional[Lesson]:
    """
    Return the lesson if it belongs to a classroom the teacher is assigned to.
    Returns None otherwise (view should raise Http404).
    """
    from apps.teachers.models import TeacherAssignment
    from apps.academics.selectors import get_current_term

    try:
        lesson = (
            Lesson.objects.select_related("module__school_class", "module__subject")
            .prefetch_related("lesson_attachments")
            .get(pk=lesson_id)
        )
    except Lesson.DoesNotExist:
        return None

    term = get_current_term()
    if term is None:
        return None

    assigned = TeacherAssignment.objects.filter(
        teacher__user_id=teacher_user_id,
        school_class=lesson.module.school_class,
        subject=lesson.module.subject,
        academic_year=term.academic_year,
    ).exists()

    # Also allow the class teacher (they oversee the whole class)
    is_class_teacher = (
        lesson.module.school_class.class_teacher_id == teacher_user_id
    )

    if not assigned and not is_class_teacher:
        return None
    return lesson


def lesson_detail_for_student(
    *, lesson_id: int, student
) -> Optional[Lesson]:
    """
    Return the published lesson if it belongs to the student's class.
    Returns None otherwise (view should raise Http404).
    """
    try:
        lesson = (
            Lesson.objects.select_related("module__school_class", "module__subject")
            .prefetch_related("lesson_attachments")
            .get(pk=lesson_id, is_published=True)
        )
    except Lesson.DoesNotExist:
        return None

    # Module must be published and belong to the student's class
    if not lesson.module.is_published:
        return None
    if lesson.module.school_class_id != student.current_class_id:
        return None
    return lesson


def available_terms_for_copy(*, school_class, subject, exclude_term):
    """Terms that already have at least one module for this class/subject,
    or any term for the class's academic year, excluding the current term."""
    from apps.academics.models import Term
    return (
        Term.objects.filter(
            academic_year=school_class.academic_year,
        )
        .exclude(pk=exclude_term.pk)
        .order_by("start_date")
    )


def available_classes_for_copy(*, academic_year, exclude_class):
    """All classes in the current year except the source class."""
    from apps.academics.models import SchoolClass
    return (
        SchoolClass.objects.filter(academic_year=academic_year)
        .exclude(pk=exclude_class.pk)
        .order_by("level_order", "name")
    )


# ---------------------------------------------------------------------------
# Next / previous lesson navigation — Checkpoint B
# ---------------------------------------------------------------------------


def _flat_published_lessons(modules: list[ModuleWithLessons]) -> list[LessonWithProgress]:
    """Flat ordered list of all published LessonWithProgress objects."""
    result = []
    for mwl in modules:
        result.extend(mwl.lessons)
    return result


def next_lesson_for_student(
    *, lesson_id: int, school_class, subject, term, student
) -> Optional[LessonWithProgress]:
    """
    The next published lesson after *lesson_id* in the course, or None if this
    is the last lesson.  Uses the student's course view (published only).
    """
    modules = course_for_student(
        school_class=school_class, subject=subject, term=term, student=student
    )
    flat = _flat_published_lessons(modules)
    for i, lp in enumerate(flat):
        if lp.lesson.pk == lesson_id and i + 1 < len(flat):
            return flat[i + 1]
    return None


def prev_lesson_for_student(
    *, lesson_id: int, school_class, subject, term, student
) -> Optional[LessonWithProgress]:
    """
    The previous published lesson before *lesson_id*, or None if this is first.
    """
    modules = course_for_student(
        school_class=school_class, subject=subject, term=term, student=student
    )
    flat = _flat_published_lessons(modules)
    for i, lp in enumerate(flat):
        if lp.lesson.pk == lesson_id and i > 0:
            return flat[i - 1]
    return None


# ---------------------------------------------------------------------------
# Course search — Checkpoint B
# ---------------------------------------------------------------------------


def search_course(
    *,
    school_class,
    subject,
    term,
    student,
    query: str,
) -> list[LessonWithProgress]:
    """
    Case-insensitive search over published lesson titles and notes for *query*.
    Returns a flat list of matching LessonWithProgress objects.
    Runs entirely in Python over the already-fetched course data — no extra
    DB query needed.
    """
    query = query.strip().lower()
    if not query:
        return []

    modules = course_for_student(
        school_class=school_class, subject=subject, term=term, student=student
    )
    results = []
    for mwl in modules:
        for lp in mwl.lessons:
            if (
                query in lp.lesson.title.lower()
                or query in lp.lesson.notes.lower()
            ):
                results.append(lp)
    return results
