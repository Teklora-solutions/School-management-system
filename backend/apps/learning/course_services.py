"""
apps/learning/course_services.py
----------------------------------
Write-side logic for the teacher course builder (Checkpoint A).
"""

from __future__ import annotations

import logging
import mimetypes
import os

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    CourseModule,
    Lesson,
    LessonAttachment,
    LESSON_ATTACHMENT_ALLOWED_EXTENSIONS,
    LESSON_ATTACHMENT_MAX_BYTES,
    LESSON_ATTACHMENT_MAX_COUNT,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _renumber_modules(school_class_id: int, subject_id: int, term_id: int) -> None:
    """Reassign sequential 0-based positions to all modules in one course."""
    qs = CourseModule.objects.filter(
        school_class_id=school_class_id,
        subject_id=subject_id,
        term_id=term_id,
    ).order_by("position", "id")
    for i, mod in enumerate(qs):
        if mod.position != i:
            mod.position = i
            mod.save(update_fields=["position"])


def _renumber_lessons(module_id: int) -> None:
    """Reassign sequential 0-based positions to all lessons in one module."""
    qs = Lesson.objects.filter(module_id=module_id).order_by("position", "id")
    for i, lesson in enumerate(qs):
        if lesson.position != i:
            lesson.position = i
            lesson.save(update_fields=["position"])


def _validate_attachment(uploaded_file) -> None:
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in LESSON_ATTACHMENT_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(LESSON_ATTACHMENT_ALLOWED_EXTENSIONS))
        raise ValidationError(
            f"File type '{ext}' is not allowed. Allowed types: {allowed}"
        )
    if uploaded_file.size > LESSON_ATTACHMENT_MAX_BYTES:
        mb = LESSON_ATTACHMENT_MAX_BYTES // (1024 * 1024)
        raise ValidationError(
            f"File '{uploaded_file.name}' is too large. Maximum size is {mb} MB."
        )


# ---------------------------------------------------------------------------
# Module operations
# ---------------------------------------------------------------------------


@transaction.atomic
def create_module(
    *,
    school_class,
    subject,
    term,
    title: str,
    actor,
) -> CourseModule:
    title = title.strip()
    if not title:
        raise ValidationError("Module title cannot be empty.")
    if len(title) > 200:
        raise ValidationError("Module title cannot exceed 200 characters.")

    # Position at the end
    last = (
        CourseModule.objects.filter(
            school_class=school_class, subject=subject, term=term
        ).order_by("-position").values_list("position", flat=True).first()
    )
    position = (last or 0) + 1

    module = CourseModule.objects.create(
        school_class=school_class,
        subject=subject,
        term=term,
        title=title,
        position=position,
        is_published=True,
    )
    logger.info("Module %s created by %s", module.pk, actor)
    return module


@transaction.atomic
def update_module(
    *,
    module: CourseModule,
    title: str,
    is_published: bool,
    actor,
) -> CourseModule:
    title = title.strip()
    if not title:
        raise ValidationError("Module title cannot be empty.")
    module.title = title
    module.is_published = is_published
    module.save(update_fields=["title", "is_published", "updated_at"])
    return module


@transaction.atomic
def delete_module(*, module: CourseModule, actor) -> None:
    school_class_id = module.school_class_id
    subject_id = module.subject_id
    term_id = module.term_id
    module.delete()
    _renumber_modules(school_class_id, subject_id, term_id)
    logger.info("Module deleted by %s", actor)


@transaction.atomic
def move_module(*, module: CourseModule, direction: str, actor) -> None:
    """Swap *module* with its neighbour. direction must be 'up' or 'down'."""
    siblings = list(
        CourseModule.objects.filter(
            school_class=module.school_class,
            subject=module.subject,
            term=module.term,
        ).order_by("position", "id")
    )
    idx = next((i for i, m in enumerate(siblings) if m.pk == module.pk), None)
    if idx is None:
        return

    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(siblings):
        return

    a, b = siblings[idx], siblings[swap_idx]
    a.position, b.position = b.position, a.position
    CourseModule.objects.bulk_update([a, b], ["position"])
    _renumber_modules(module.school_class_id, module.subject_id, module.term_id)


# ---------------------------------------------------------------------------
# Lesson operations
# ---------------------------------------------------------------------------


@transaction.atomic
def create_lesson(
    *,
    module: CourseModule,
    title: str,
    notes: str = "",
    notes_format: str = "plain",
    video_url: str = "",
    is_published: bool = True,
    actor,
) -> Lesson:
    title = title.strip()
    if not title:
        raise ValidationError("Lesson title cannot be empty.")
    if len(title) > 200:
        raise ValidationError("Lesson title cannot exceed 200 characters.")

    last = (
        Lesson.objects.filter(module=module)
        .order_by("-position").values_list("position", flat=True).first()
    )
    position = (last or 0) + 1

    lesson = Lesson.objects.create(
        module=module,
        title=title,
        notes=notes.strip(),
        notes_format=notes_format,
        video_url=video_url.strip(),
        position=position,
        is_published=is_published,
    )
    logger.info("Lesson %s created by %s", lesson.pk, actor)
    return lesson


@transaction.atomic
def update_lesson(
    *,
    lesson: Lesson,
    title: str,
    notes: str,
    notes_format: str,
    video_url: str,
    is_published: bool,
    actor,
) -> Lesson:
    title = title.strip()
    if not title:
        raise ValidationError("Lesson title cannot be empty.")
    lesson.title = title
    lesson.notes = notes.strip()
    lesson.notes_format = notes_format
    lesson.video_url = video_url.strip()
    lesson.is_published = is_published
    lesson.save(
        update_fields=["title", "notes", "notes_format", "video_url",
                       "is_published", "updated_at"]
    )
    return lesson


@transaction.atomic
def delete_lesson(*, lesson: Lesson, actor) -> None:
    # Delete stored attachment files
    for att in lesson.lesson_attachments.all():
        att.file.delete(save=False)
    if lesson.attachment:
        lesson.attachment.delete(save=False)
    module_id = lesson.module_id
    lesson.delete()
    _renumber_lessons(module_id)
    logger.info("Lesson deleted by %s", actor)


@transaction.atomic
def move_lesson(*, lesson: Lesson, direction: str, actor) -> None:
    """Swap *lesson* with its neighbour in the same module."""
    siblings = list(
        Lesson.objects.filter(module=lesson.module).order_by("position", "id")
    )
    idx = next((i for i, l in enumerate(siblings) if l.pk == lesson.pk), None)
    if idx is None:
        return

    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(siblings):
        return

    a, b = siblings[idx], siblings[swap_idx]
    a.position, b.position = b.position, a.position
    Lesson.objects.bulk_update([a, b], ["position"])
    _renumber_lessons(lesson.module_id)


# ---------------------------------------------------------------------------
# Attachment operations
# ---------------------------------------------------------------------------


@transaction.atomic
def add_attachment(
    *,
    lesson: Lesson,
    uploaded_file,
    actor,
) -> LessonAttachment:
    existing_count = lesson.lesson_attachments.count()
    if existing_count >= LESSON_ATTACHMENT_MAX_COUNT:
        raise ValidationError(
            f"A lesson may have at most {LESSON_ATTACHMENT_MAX_COUNT} attachments. "
            f"Remove one before adding another."
        )
    _validate_attachment(uploaded_file)

    content_type = (
        mimetypes.guess_type(uploaded_file.name)[0] or "application/octet-stream"
    )
    att = LessonAttachment.objects.create(
        lesson=lesson,
        file=uploaded_file,
        original_name=uploaded_file.name[:255],
        file_size=uploaded_file.size,
        content_type=content_type,
        ordering=existing_count,
    )
    return att


@transaction.atomic
def delete_attachment(*, attachment: LessonAttachment, actor) -> None:
    attachment.file.delete(save=False)
    attachment.delete()
    logger.info("Lesson attachment deleted by %s", actor)


# ---------------------------------------------------------------------------
# Duplication
# ---------------------------------------------------------------------------


@transaction.atomic
def duplicate_lesson(*, lesson: Lesson, actor) -> Lesson:
    """
    Clone a lesson (title + notes + video_url + publish state) into the same
    module, placed directly after the original. Attachments are NOT copied.
    """
    new_lesson = Lesson.objects.create(
        module=lesson.module,
        title=f"{lesson.title} (copy)",
        notes=lesson.notes,
        notes_format=lesson.notes_format,
        video_url=lesson.video_url,
        position=lesson.position + 1,
        is_published=False,   # copies are always draft so teacher reviews first
    )
    # Push later siblings forward
    Lesson.objects.filter(
        module=lesson.module,
        position__gt=lesson.position,
    ).exclude(pk=new_lesson.pk).update(
        position=F("position") + 1
    )
    _renumber_lessons(lesson.module_id)
    logger.info("Lesson %s duplicated to %s by %s", lesson.pk, new_lesson.pk, actor)
    return new_lesson


@transaction.atomic
def duplicate_module(*, module: CourseModule, actor) -> CourseModule:
    """
    Clone a module (title + lessons) into the same course at the end.
    All cloned lessons are set to is_published=False.
    Attachments are NOT copied.
    """
    last = (
        CourseModule.objects.filter(
            school_class=module.school_class,
            subject=module.subject,
            term=module.term,
        ).order_by("-position").values_list("position", flat=True).first()
    )
    new_module = CourseModule.objects.create(
        school_class=module.school_class,
        subject=module.subject,
        term=module.term,
        title=f"{module.title} (copy)",
        position=(last or 0) + 1,
        is_published=False,
    )
    for lesson in module.lessons.order_by("position"):
        Lesson.objects.create(
            module=new_module,
            title=lesson.title,
            notes=lesson.notes,
            notes_format=lesson.notes_format,
            video_url=lesson.video_url,
            position=lesson.position,
            is_published=False,
        )
    logger.info("Module %s duplicated to %s by %s", module.pk, new_module.pk, actor)
    return new_module


# ---------------------------------------------------------------------------
# Copy whole course to another term / class
# ---------------------------------------------------------------------------


@transaction.atomic
def copy_course(
    *,
    source_class,
    source_subject,
    source_term,
    target_class,
    target_subject,
    target_term,
    actor,
) -> int:
    """
    Copy all modules and lessons from the source classroom to the target
    classroom, appending after any existing content in the target.

    Returns the total number of lessons copied.
    All copied modules and lessons are set to is_published=False so the
    teacher reviews them before students see anything.
    Attachments are NOT copied.
    """
    source_modules = list(
        CourseModule.objects.filter(
            school_class=source_class,
            subject=source_subject,
            term=source_term,
        ).prefetch_related("lessons").order_by("position")
    )
    if not source_modules:
        return 0

    # Find where to start appending positions in the target
    last_pos = (
        CourseModule.objects.filter(
            school_class=target_class,
            subject=target_subject,
            term=target_term,
        ).order_by("-position").values_list("position", flat=True).first()
    )
    offset = (last_pos + 1) if last_pos is not None else 0

    total_lessons = 0
    for i, src_mod in enumerate(source_modules):
        new_mod = CourseModule.objects.create(
            school_class=target_class,
            subject=target_subject,
            term=target_term,
            title=src_mod.title,
            position=offset + i,
            is_published=False,
        )
        for lesson in src_mod.lessons.order_by("position"):
            Lesson.objects.create(
                module=new_mod,
                title=lesson.title,
                notes=lesson.notes,
                notes_format=lesson.notes_format,
                video_url=lesson.video_url,
                position=lesson.position,
                is_published=False,
            )
            total_lessons += 1

    logger.info(
        "Course copied (%d lessons) to %s/%s/%s by %s",
        total_lessons, target_class, target_subject, target_term, actor,
    )
    return total_lessons
