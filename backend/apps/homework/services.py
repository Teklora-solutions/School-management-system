"""
apps/homework/services.py
-------------------------
Write-side logic for homework: create, edit, publish/unpublish, delete.

Every entry point takes the ``TeacherAssignment`` (or the already-scoped
``Homework``) the calling view resolved, so authorisation lives in one place —
the teacher may only touch homework for a class + subject they are allocated
in the current academic year.
"""

from __future__ import annotations

import os

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.audit_log import log_action
from apps.core.validators import (
    validate_homework_upload,
    validate_submission_upload,
)

from .models import (
    MAX_HOMEWORK_ATTACHMENTS,
    MAX_SUBMISSION_ATTACHMENTS,
    Homework,
    HomeworkAttachment,
    HomeworkSubmission,
    HomeworkSubmissionAttachment,
)

CONTENT_TYPE = "homework.Homework"
SUBMISSION_CONTENT_TYPE = "homework.HomeworkSubmission"


def check_uploads(
    files,
    existing_count: int = 0,
    *,
    limit: int = MAX_HOMEWORK_ATTACHMENTS,
    label: str = "A homework",
    validators=None,
) -> list:
    """
    Validate the files about to be attached and return them as a list.

    Runs the shared size / MIME / extension validators (max 5 files, 10 MB
    each) and raises ``ValidationError`` with the first problem found. The
    view turns that into a form error; nothing is written until this passes.
    """
    files = list(files)
    if existing_count + len(files) > limit:
        raise ValidationError(
            f"{label} can carry at most {limit} files. Remove one first."
        )
    for uploaded in files:
        for validator in validators or validate_homework_upload:
            validator(uploaded)
    return files


def _save_attachments(homework: Homework, files, start: int = 0) -> None:
    for offset, uploaded in enumerate(files):
        HomeworkAttachment.objects.create(
            homework=homework,
            file=uploaded,
            original_name=os.path.basename(uploaded.name)[:255],
            file_size=uploaded.size,
            content_type=(getattr(uploaded, "content_type", "") or "")[:100],
            ordering=start + offset,
        )


def _delete_attachment_files(homework: Homework) -> None:
    for attachment in homework.attachments.all():
        attachment.file.delete(save=False)
    for submission in homework.submissions.all():
        for attachment in submission.attachments.all():
            attachment.file.delete(save=False)
    if homework.attachment:
        homework.attachment.delete(save=False)


def _summary(homework: Homework, change: dict | None = None) -> dict:
    data = {
        "title": homework.title,
        "class": homework.school_class.name,
        "subject": homework.subject.code,
        "due_date": homework.due_date.isoformat(),
        "attachments": homework.attachments.count(),
    }
    if change:
        data.update(change)
    return data


@transaction.atomic
def create_homework(
    *,
    assignment,
    term,
    title: str,
    description: str = "",
    due_date,
    files=None,
    publish: bool = False,
    actor,
    request=None,
) -> Homework:
    """Create a homework for one of the teacher's assignments."""
    if term.academic_year_id != assignment.academic_year_id:
        raise ValidationError("That term is not in the same academic year.")
    files = check_uploads(files or [])

    homework = Homework.objects.create(
        title=title,
        description=description,
        subject=assignment.subject,
        school_class=assignment.school_class,
        stream=assignment.stream,
        term=term,
        set_by=actor,
        due_date=due_date,
        status=Homework.Status.PUBLISHED if publish else Homework.Status.DRAFT,
        published_at=timezone.now() if publish else None,
    )
    _save_attachments(homework, files)

    log_action(
        actor=actor,
        action="homework.published" if publish else "homework.created",
        content_type=CONTENT_TYPE,
        object_id=homework.pk,
        change_summary=_summary(homework),
        request=request,
    )
    return homework


@transaction.atomic
def update_homework(
    *,
    homework: Homework,
    assignment,
    term,
    title: str,
    description: str,
    due_date,
    new_files=None,
    remove_ids=None,
    action: str = "save",
    actor,
    request=None,
) -> Homework:
    """
    Update a homework. ``action`` is one of:

    ``save``      keep the current status (an edit of a published homework
                  stays published and visible to students);
    ``publish``   publish it (and stamp ``published_at``);
    ``unpublish`` move it back to draft so students stop seeing it.
    """
    if term.academic_year_id != assignment.academic_year_id:
        raise ValidationError("That term is not in the same academic year.")

    remove_ids = {int(pk) for pk in (remove_ids or [])}
    removals = [a for a in homework.attachments.all() if a.pk in remove_ids]
    remaining = homework.attachments.exclude(pk__in=remove_ids).count()
    new_files = check_uploads(new_files or [], existing_count=remaining)

    old_status = homework.status
    if action == "publish":
        homework.status = Homework.Status.PUBLISHED
        if old_status != Homework.Status.PUBLISHED or homework.published_at is None:
            homework.published_at = timezone.now()
    elif action == "unpublish":
        homework.status = Homework.Status.DRAFT

    homework.title = title
    homework.description = description
    homework.school_class = assignment.school_class
    homework.subject = assignment.subject
    homework.stream = assignment.stream
    homework.term = term
    homework.due_date = due_date
    homework.save(
        update_fields=[
            "title",
            "description",
            "school_class",
            "subject",
            "stream",
            "term",
            "due_date",
            "status",
            "published_at",
            "updated_at",
        ]
    )

    if removals:
        for attachment in removals:
            attachment.file.delete(save=False)
        HomeworkAttachment.objects.filter(pk__in=[a.pk for a in removals]).delete()

    if new_files:
        _save_attachments(homework, new_files, start=remaining)

    if homework.status != old_status:
        audit_action = (
            "homework.published"
            if homework.status == Homework.Status.PUBLISHED
            else "homework.unpublished"
        )
        change = {"status": {"from": old_status, "to": homework.status}}
    else:
        audit_action = "homework.updated"
        change = {}

    log_action(
        actor=actor,
        action=audit_action,
        content_type=CONTENT_TYPE,
        object_id=homework.pk,
        change_summary=_summary(homework, change),
        request=request,
    )
    return homework


def publish_homework(*, homework: Homework, actor, request=None) -> Homework:
    if homework.status != Homework.Status.PUBLISHED:
        homework.status = Homework.Status.PUBLISHED
        homework.published_at = timezone.now()
        homework.save(update_fields=["status", "published_at", "updated_at"])
        log_action(
            actor=actor,
            action="homework.published",
            content_type=CONTENT_TYPE,
            object_id=homework.pk,
            change_summary=_summary(homework),
            request=request,
        )
    return homework


def unpublish_homework(*, homework: Homework, actor, request=None) -> Homework:
    if homework.status != Homework.Status.DRAFT:
        homework.status = Homework.Status.DRAFT
        homework.save(update_fields=["status", "updated_at"])
        log_action(
            actor=actor,
            action="homework.unpublished",
            content_type=CONTENT_TYPE,
            object_id=homework.pk,
            change_summary=_summary(homework),
            request=request,
        )
    return homework


@transaction.atomic
def delete_homework(*, homework: Homework, actor, request=None) -> None:
    """Only drafts may be deleted; published homework is unpublished first."""
    if homework.status != Homework.Status.DRAFT:
        raise ValidationError(
            "Only draft homework can be deleted. Unpublish it first to hide it from students."
        )
    summary = _summary(homework)
    homework_id = homework.pk
    _delete_attachment_files(homework)
    homework.delete()
    log_action(
        actor=actor,
        action="homework.deleted",
        content_type=CONTENT_TYPE,
        object_id=homework_id,
        change_summary=summary,
        request=request,
    )


# ---------------------------------------------------------------------------
# Student submissions
# ---------------------------------------------------------------------------

MAX_SUBMISSION_NOTE = 2000


def _save_submission_attachments(submission, files, start: int = 0) -> None:
    for offset, uploaded in enumerate(files):
        HomeworkSubmissionAttachment.objects.create(
            submission=submission,
            file=uploaded,
            original_name=os.path.basename(uploaded.name)[:255],
            file_size=uploaded.size,
            content_type=(getattr(uploaded, "content_type", "") or "")[:100],
            ordering=start + offset,
        )


@transaction.atomic
def submit_homework(
    *,
    homework: Homework,
    student,
    note: str = "",
    files=None,
    remove_ids=None,
    actor,
    request=None,
) -> HomeworkSubmission:
    """
    Create or replace one student's submission.

    Rules:
    - only published homework is open for submissions;
    - while a submission is on time and the due date has not passed, the
      student may replace its note and files in place;
    - once the due date has passed, a *first* submission is still accepted and
      flagged ``is_late`` — but a late or already-due submission can no longer
      be changed by the student;
    - an empty submission (no note and no files) is refused.
    """
    if homework.status != Homework.Status.PUBLISHED:
        raise ValidationError("This homework is not open for submissions.")

    existing = (
        HomeworkSubmission.objects.filter(homework=homework, student=student)
        .prefetch_related("attachments")
        .first()
    )
    today = timezone.localdate()
    if existing and (existing.is_late or today > homework.due_date):
        raise ValidationError(
            "The due date has passed, so this submission can no longer be changed."
        )

    note = (note or "").strip()
    if len(note) > MAX_SUBMISSION_NOTE:
        raise ValidationError(
            f"Keep the note under {MAX_SUBMISSION_NOTE} characters."
        )

    remove_ids = {int(pk) for pk in (remove_ids or [])}
    owned = (
        list(existing.attachments.all()) if existing is not None else []
    )
    removals = [a for a in owned if a.pk in remove_ids]
    remaining = [a for a in owned if a.pk not in remove_ids]
    new_files = check_uploads(
        files or [],
        existing_count=len(remaining),
        limit=MAX_SUBMISSION_ATTACHMENTS,
        label="A submission",
        validators=validate_submission_upload,
    )

    if not note and not remaining and not new_files:
        raise ValidationError("Attach a file or write a note before submitting.")

    is_late = today > homework.due_date
    submission = existing or HomeworkSubmission(
        homework=homework, student=student
    )
    submission.note = note
    submission.submitted_at = timezone.now()
    submission.is_late = is_late
    submission.save()

    if removals:
        for attachment in removals:
            attachment.file.delete(save=False)
        HomeworkSubmissionAttachment.objects.filter(
            pk__in=[a.pk for a in removals]
        ).delete()
    if new_files:
        _save_submission_attachments(submission, new_files, start=len(remaining))

    log_action(
        actor=actor,
        action="homework.resubmitted" if existing else "homework.submitted",
        content_type=SUBMISSION_CONTENT_TYPE,
        object_id=submission.pk,
        change_summary={
            "homework": homework.title,
            "student": str(student),
            "late": is_late,
            "attachments": submission.attachments.count(),
        },
        request=request,
    )
    return submission

