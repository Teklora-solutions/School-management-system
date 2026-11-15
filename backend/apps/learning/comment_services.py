from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

from .course_selectors import lesson_targets_student
from .models import ClassEngagementSettings, Lesson, LessonComment
from .release import is_released_at
from .topic_services import assert_course_teacher

logger = logging.getLogger(__name__)


def _audit(*, actor, action: str, obj, summary: dict, request=None) -> None:
    """Best-effort audit write — a logging failure must never undo moderation."""
    try:
        from apps.core.audit_log import log_action

        log_action(
            actor=actor,
            action=action,
            content_type=obj._meta.label,
            object_id=obj.pk,
            change_summary=summary,
            request=request,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Audit log write failed for %s %s", action, obj.pk)


def comments_enabled(school_class) -> bool:
    """The per-class lesson-comments switch (default off)."""
    settings = (
        ClassEngagementSettings.objects.filter(school_class=school_class)
        .only("lesson_comments_enabled")
        .first()
    )
    return bool(settings and settings.lesson_comments_enabled)


def _assert_enabled(school_class) -> None:
    if not comments_enabled(school_class):
        raise PermissionDenied(
            "Lesson comments are not switched on for this class."
        )


def visible_comments(*, lesson: Lesson) -> list[LessonComment]:
    """Comments shown on the lesson page: not removed, oldest first."""
    return list(
        lesson.comments.select_related("student__user")
        .filter(removed_at__isnull=True)
        .order_by("created_at", "id")
    )


@transaction.atomic
def add_comment(
    *,
    lesson: Lesson,
    student,
    body: str,
    request=None,
) -> LessonComment:
    if student.user.role != "student":
        raise PermissionDenied("Only students can add lesson comments.")
    if request is not None and request.user != student.user:
        raise PermissionDenied("You cannot post a comment as another student.")
    module = lesson.module
    now = timezone.now()
    if (
        student.current_class_id != module.school_class_id
        or not is_released_at(module, now=now)
        or not is_released_at(lesson, now=now)
        or not lesson_targets_student(lesson, student)
    ):
        raise PermissionDenied("This lesson is not available to you.")
    _assert_enabled(module.school_class)

    body = (strip_tags(body or "")).strip()
    if not body:
        raise ValidationError("Write a question or comment first.")
    if len(body) > 2000:
        raise ValidationError("Comments are limited to 2 000 characters.")

    comment = LessonComment.objects.create(
        lesson=lesson, student=student, body=body
    )

    _audit(
        actor=student.user,
        action="lesson.comment.added",
        obj=comment,
        summary={
            "lesson_id": lesson.pk,
            "lesson_title": lesson.title,
            "excerpt": body[:120],
        },
        request=request,
    )
    return comment


@transaction.atomic
def remove_comment(*, comment: LessonComment, actor, request=None) -> None:
    """Teacher/admin moderation — soft delete, audit-logged."""
    module = comment.lesson.module
    assert_course_teacher(
        actor=actor, school_class=module.school_class, subject=module.subject,
    )
    if comment.removed_at is not None:
        return
    comment.removed_by = actor
    comment.removed_at = timezone.now()
    comment.save(update_fields=["removed_by", "removed_at", "updated_at"])
    _audit(
        actor=actor,
        action="lesson.comment.removed",
        obj=comment,
        summary={
            "lesson_id": comment.lesson_id,
            "student": str(comment.student),
            "excerpt": comment.body[:120],
        },
        request=request,
    )


@transaction.atomic
def restore_comment(*, comment: LessonComment, actor, request=None) -> None:
    """Undo a removal (teacher/admin only)."""
    module = comment.lesson.module
    assert_course_teacher(
        actor=actor, school_class=module.school_class, subject=module.subject,
    )
    if comment.removed_at is None:
        return
    comment.removed_by = None
    comment.removed_at = None
    comment.save(update_fields=["removed_by", "removed_at", "updated_at"])
    _audit(
        actor=actor,
        action="lesson.comment.restored",
        obj=comment,
        summary={"lesson_id": comment.lesson_id},
        request=request,
    )
