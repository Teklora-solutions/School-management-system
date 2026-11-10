"""
apps/learning/lesson_notes_services.py
-----------------------------------------
Write-side logic for student private lesson notes (Checkpoint B).
"""

from __future__ import annotations

from django.db import transaction

from .models import StudentLessonNote

MAX_NOTE_LENGTH = 5000  # characters


@transaction.atomic
def save_lesson_note(*, student, lesson, content: str) -> StudentLessonNote:
    """
    Create or update the student's private note for *lesson*.

    The note is silently capped at MAX_NOTE_LENGTH characters so a runaway
    paste never exceeds the column size. Saving an empty string is allowed
    (it clears the note).
    """
    content = (content or "").strip()[:MAX_NOTE_LENGTH]
    note, _ = StudentLessonNote.objects.update_or_create(
        student=student,
        lesson=lesson,
        defaults={"content": content},
    )
    return note


def get_lesson_note(*, student, lesson) -> StudentLessonNote | None:
    """Return the student's note for this lesson, or None if they haven't written one."""
    return StudentLessonNote.objects.filter(student=student, lesson=lesson).first()
