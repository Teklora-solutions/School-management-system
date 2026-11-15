from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .models import CourseModule, Lesson, Topic

logger = logging.getLogger(__name__)


def assert_course_teacher(*, actor, school_class, subject) -> None:
    from .selectors import teacher_classroom

    if actor.role in ("admin", "director"):
        return
    if actor.role != "teacher" or teacher_classroom(
        teacher_user_id=actor.pk, class_id=school_class.pk, subject_id=subject.pk,
    ) is None:
        raise PermissionDenied("You are not assigned to this classroom.")


def _renumber_topics(school_class_id: int, subject_id: int, term_id: int) -> None:
    qs = Topic.objects.filter(
        school_class_id=school_class_id,
        subject_id=subject_id,
        term_id=term_id,
    ).order_by("position", "id")
    for i, topic in enumerate(qs):
        if topic.position != i:
            topic.position = i
            topic.save(update_fields=["position"])


def _renumber_topic_lessons(topic_id: int) -> None:
    qs = Lesson.objects.filter(topic_id=topic_id).order_by("topic_position", "id")
    for i, lesson in enumerate(qs):
        if lesson.topic_position != i:
            lesson.topic_position = i
            lesson.save(update_fields=["topic_position"])


@transaction.atomic
def create_topic(*, school_class, subject, term, title: str, actor) -> Topic:
    assert_course_teacher(actor=actor, school_class=school_class, subject=subject)
    title = title.strip()
    if not title:
        raise ValidationError("Topic title cannot be empty.")
    if len(title) > 200:
        raise ValidationError("Topic title cannot exceed 200 characters.")
    last = (
        Topic.objects.filter(
            school_class=school_class, subject=subject, term=term
        ).order_by("-position").values_list("position", flat=True).first()
    )
    topic = Topic.objects.create(
        school_class=school_class,
        subject=subject,
        term=term,
        title=title,
        position=(last or 0) + 1,
    )
    logger.info("Topic %s created by %s", topic.pk, actor)
    return topic


@transaction.atomic
def rename_topic(*, topic: Topic, title: str, actor) -> Topic:
    assert_course_teacher(
        actor=actor, school_class=topic.school_class, subject=topic.subject,
    )
    title = title.strip()
    if not title:
        raise ValidationError("Topic title cannot be empty.")
    if len(title) > 200:
        raise ValidationError("Topic title cannot exceed 200 characters.")
    topic.title = title
    topic.save(update_fields=["title", "updated_at"])
    logger.info("Topic %s renamed by %s", topic.pk, actor)
    return topic


@transaction.atomic
def delete_topic(*, topic: Topic, actor) -> None:
    """Delete a topic; its lessons are kept but lose their grouping."""
    assert_course_teacher(
        actor=actor, school_class=topic.school_class, subject=topic.subject,
    )
    school_class_id = topic.school_class_id
    subject_id = topic.subject_id
    term_id = topic.term_id
    Lesson.objects.filter(topic=topic).update(topic=None, topic_position=0)
    topic.delete()
    _renumber_topics(school_class_id, subject_id, term_id)
    logger.info("Topic %s deleted by %s", school_class_id, actor)


@transaction.atomic
def move_topic(*, topic: Topic, direction: str, actor) -> None:
    """Swap *topic* with its neighbour. direction must be 'up' or 'down'."""
    assert_course_teacher(
        actor=actor, school_class=topic.school_class, subject=topic.subject,
    )
    _renumber_topics(topic.school_class_id, topic.subject_id, topic.term_id)
    siblings = list(
        Topic.objects.filter(
            school_class=topic.school_class,
            subject=topic.subject,
            term=topic.term,
        ).order_by("position", "id")
    )
    idx = next((i for i, t in enumerate(siblings) if t.pk == topic.pk), None)
    if idx is None:
        return
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(siblings):
        return
    a, b = siblings[idx], siblings[swap_idx]
    a.position, b.position = b.position, a.position
    Topic.objects.bulk_update([a, b], ["position"])
    _renumber_topics(topic.school_class_id, topic.subject_id, topic.term_id)


@transaction.atomic
def set_lesson_topic(
    *, lesson: Lesson, topic: Topic | None, actor
) -> Lesson:
    """Move a lesson into a topic (or out of all topics when topic is None)."""
    module = lesson.module
    assert_course_teacher(
        actor=actor, school_class=module.school_class, subject=module.subject,
    )
    if topic is not None and (
        topic.school_class_id != module.school_class_id
        or topic.subject_id != module.subject_id
        or topic.term_id != module.term_id
    ):
        raise ValidationError("That topic belongs to a different course.")
    if lesson.topic_id == (topic.pk if topic is not None else None):
        return lesson
    if topic is not None:
        last = (
            Lesson.objects.filter(topic=topic)
            .order_by("-topic_position")
            .values_list("topic_position", flat=True)
            .first()
        )
        lesson.topic = topic
        lesson.topic_position = (last or 0) + 1
    else:
        lesson.topic = None
        lesson.topic_position = 0
    lesson.save(update_fields=["topic", "topic_position", "updated_at"])
    return lesson


@transaction.atomic
def move_lesson_in_topic(*, lesson: Lesson, direction: str, actor) -> None:
    """Swap a lesson with its neighbour inside its topic ('up' or 'down')."""
    module = lesson.module
    assert_course_teacher(
        actor=actor, school_class=module.school_class, subject=module.subject,
    )
    if lesson.topic_id is None:
        return
    _renumber_topic_lessons(lesson.topic_id)
    siblings = list(
        Lesson.objects.filter(topic_id=lesson.topic_id).order_by(
            "topic_position", "id"
        )
    )
    idx = next((i for i, l in enumerate(siblings) if l.pk == lesson.pk), None)
    if idx is None:
        return
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(siblings):
        return
    a, b = siblings[idx], siblings[swap_idx]
    a.topic_position, b.topic_position = b.topic_position, a.topic_position
    Lesson.objects.bulk_update([a, b], ["topic_position"])
    _renumber_topic_lessons(lesson.topic_id)


@transaction.atomic
def set_lesson_targets(
    *, lesson: Lesson, assign_all: bool, student_ids: list[int], actor
) -> Lesson:
    module = lesson.module
    assert_course_teacher(
        actor=actor, school_class=module.school_class, subject=module.subject,
    )
    lesson.assign_all = assign_all
    lesson.save(update_fields=["assign_all", "updated_at"])
    if assign_all:
        lesson.assigned_students.clear()
        return lesson
    from apps.students.models import Student

    students = list(
        Student.objects.filter(
            pk__in=student_ids, current_class=lesson.module.school_class
        )
    )
    lesson.assigned_students.set(students)
    return lesson


@transaction.atomic
def set_quiz_targets(
    *, quiz, assign_all: bool, student_ids: list[int], actor
) -> None:
    """Choose who may see this quiz (same rule as :func:`set_lesson_targets`)."""
    module = quiz.lesson.module
    assert_course_teacher(
        actor=actor, school_class=module.school_class, subject=module.subject,
    )
    quiz.assign_all = assign_all
    quiz.save(update_fields=["assign_all", "updated_at"])
    if assign_all:
        quiz.assigned_students.clear()
        return
    from apps.students.models import Student

    students = list(
        Student.objects.filter(
            pk__in=student_ids,
            current_class=quiz.lesson.module.school_class,
        )
    )
    quiz.assigned_students.set(students)


@transaction.atomic
def set_module_schedule(
    *, module: CourseModule, publish_at, hide_at, actor
) -> CourseModule:
    assert_course_teacher(
        actor=actor, school_class=module.school_class, subject=module.subject,
    )
    if publish_at is not None and hide_at is not None and hide_at <= publish_at:
        raise ValidationError("Hide time must be after publish time.")
    module.publish_at = publish_at
    module.hide_at = hide_at
    module.save(update_fields=["publish_at", "hide_at", "updated_at"])
    return module
