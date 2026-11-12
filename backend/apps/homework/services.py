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

import logging
import os
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.audit_log import log_action
from apps.core.notifications import notify
from apps.core.validators import (
    validate_homework_upload,
    validate_submission_upload,
)

from .models import (
    MAX_HOMEWORK_ATTACHMENTS,
    MAX_SUBMISSION_ATTACHMENTS,
    Homework,
    HomeworkAttachment,
    HomeworkMark,
    HomeworkSubmission,
    HomeworkSubmissionAttachment,
    HomeworkSubmissionRevision,
)

CONTENT_TYPE = "homework.Homework"
SUBMISSION_CONTENT_TYPE = "homework.HomeworkSubmission"
MARK_CONTENT_TYPE = "homework.HomeworkMark"


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
        "max_score": str(homework.max_score) if homework.max_score is not None else "",
    }
    if change:
        data.update(change)
    return data


def _clean_max_score(value):
    """Turn the posted total into a Decimal, or ``None`` when it is blank."""
    if value in (None, ""):
        return None
    try:
        total = Decimal(str(value)).quantize(Decimal("0.1"))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Enter the total mark as a number.") from exc
    if total <= 0:
        raise ValidationError("The total mark must be greater than zero.")
    return total


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
    max_score=None,
    actor,
    request=None,
) -> Homework:
    """Create a homework for one of the teacher's assignments."""
    if term.academic_year_id != assignment.academic_year_id:
        raise ValidationError("That term is not in the same academic year.")
    files = check_uploads(files or [])
    max_score = _clean_max_score(max_score)

    homework = Homework.objects.create(
        title=title,
        description=description,
        subject=assignment.subject,
        school_class=assignment.school_class,
        stream=assignment.stream,
        term=term,
        set_by=actor,
        due_date=due_date,
        max_score=max_score,
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
    max_score=None,
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
    homework.max_score = _clean_max_score(max_score)
    homework.save(
        update_fields=[
            "title",
            "description",
            "school_class",
            "subject",
            "stream",
            "term",
            "due_date",
            "max_score",
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


def _archive_attempt(submission: HomeworkSubmission) -> HomeworkSubmissionRevision:
    """
    Keep the current note, timing and files as an earlier attempt.

    The files are moved onto the revision rather than copied: the submission
    row always holds the latest attempt, and an attachment's ``revision`` link
    says which attempt owns it. Nothing is duplicated on disk.
    """
    revision = HomeworkSubmissionRevision.objects.create(
        submission=submission,
        number=submission.revisions.count() + 1,
        note=submission.note,
        submitted_at=submission.submitted_at,
        is_late=submission.is_late,
    )
    submission.attachments.filter(revision__isnull=True).update(revision=revision)
    return revision


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
    - the one exception is a "Needs revision" mark the teacher has released
      with attempts still allowed: then the current attempt is kept as a
      revision and the pupil may hand in again, even after the due date;
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
    from .selectors import resubmission_window

    window_open, _used, _allowed = resubmission_window(
        homework=homework, student=student
    )
    if (
        existing
        and not window_open
        and (existing.is_late or today > homework.due_date)
    ):
        raise ValidationError(
            "The due date has passed, so this submission can no longer be changed."
        )

    note = (note or "").strip()
    if len(note) > MAX_SUBMISSION_NOTE:
        raise ValidationError(
            f"Keep the note under {MAX_SUBMISSION_NOTE} characters."
        )

    remove_ids = {int(pk) for pk in (remove_ids or [])}
    if existing is not None and window_open:
        _archive_attempt(existing)
    owned = (
        list(existing.attachments.filter(revision__isnull=True))
        if existing is not None
        else []
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

    # ── Piece 3: award points for homework submission ────────────────────
    # Only the FIRST submission earns points (existing is None).
    # Resubmissions before the due date never earn a second award.
    if not existing:
        try:
            from apps.learning.points_config import (
                REASON_HW_LATE,
                REASON_HW_ONTIME,
                get_point_config,
            )
            from apps.learning.points_services import award_points

            cfg = get_point_config()
            reason   = REASON_HW_LATE if is_late else REASON_HW_ONTIME
            pts      = cfg.pts_homework_late if is_late else cfg.pts_homework_ontime
            label    = "late" if is_late else "on time"
            idem_key = f"hw_{reason}:{homework.pk}:{student.pk}"

            award_points(
                student=student,
                reason=reason,
                points=pts,
                description=f"Homework submitted {label}: {homework.title}",
                idempotency_key=idem_key,
            )
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "Points award failed for homework %s student %s",
                homework.pk,
                student.pk,
            )

    return submission


# ---------------------------------------------------------------------------
# Marking and feedback
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

MAX_FEEDBACK = 4000
MAX_EXTRA_ATTEMPTS = 10


def _clean_feedback(value) -> str:
    text = (value or "").strip()
    if len(text) > MAX_FEEDBACK:
        raise ValidationError(f"Keep the feedback under {MAX_FEEDBACK} characters.")
    return text


def _as_decimal(value, message: str) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.1"))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(message) from exc


def _clean_deduction(*, value, score, homework, student) -> Decimal:
    """
    A lateness deduction, only where the school has switched deductions on.

    The school switch defaults to off, in which case late work simply carries
    no deduction — which is what "accept late work with no penalty" means.
    """
    from apps.core.selectors import school_settings_values

    if value in (None, ""):
        return Decimal("0")
    if not school_settings_values()["homework_late_penalty_enabled"]:
        raise ValidationError(
            "Lateness deductions are switched off for this school."
        )
    submission = HomeworkSubmission.objects.filter(
        homework=homework, student=student
    ).first()
    if submission is None or not submission.is_late:
        raise ValidationError(
            "This pupil's work was not late, so there is nothing to deduct."
        )
    deduction = _as_decimal(value, "Enter the deduction as a number.")
    if deduction < 0 or deduction > score:
        raise ValidationError(
            "The deduction must be between 0 and the mark given."
        )
    return deduction


@transaction.atomic
def mark_homework(
    *,
    homework: Homework,
    student,
    score=None,
    status: str,
    feedback: str = "",
    late_deduction=None,
    resubmissions_allowed=None,
    actor,
    request=None,
) -> HomeworkMark:
    """
    Save one pupil's mark and feedback.

    Marking never awards or removes points: homework points belong to the act
    of submitting, and a pupil must not be able to earn or lose them twice
    because a teacher changed their mind. A released mark stays released when
    it is corrected; the family simply sees the corrected version.
    """
    if status not in HomeworkMark.Status.values:
        raise ValidationError("Choose a status for this mark.")
    feedback = _clean_feedback(feedback)

    if status == HomeworkMark.Status.NOT_SUBMITTED:
        score = None
        late_deduction = Decimal("0")
        extra_attempts = 0
    else:
        if homework.max_score is None:
            raise ValidationError(
                "Set the total mark for this homework before marking it."
            )
        if score in (None, ""):
            raise ValidationError("Enter a mark, or choose Not submitted.")
        score = _as_decimal(score, "Enter the mark as a number.")
        if score < 0 or score > homework.max_score:
            raise ValidationError(
                f"The mark must be between 0 and {homework.max_score}."
            )
        late_deduction = _clean_deduction(
            value=late_deduction,
            score=score,
            homework=homework,
            student=student,
        )
        if status == HomeworkMark.Status.NEEDS_REVISION:
            if resubmissions_allowed in (None, ""):
                extra_attempts = 1
            else:
                try:
                    extra_attempts = int(resubmissions_allowed)
                except (TypeError, ValueError) as exc:
                    raise ValidationError(
                        "Enter how many extra attempts to allow as a whole number."
                    ) from exc
                if extra_attempts < 0:
                    raise ValidationError("Extra attempts cannot be negative.")
                extra_attempts = min(extra_attempts, MAX_EXTRA_ATTEMPTS)
        else:
            extra_attempts = 0

    mark = HomeworkMark.objects.filter(
        homework=homework, student=student
    ).first()
    previous = {
        "score": str(mark.score) if mark and mark.score is not None else None,
        "status": mark.status if mark else "",
        "released": bool(
            mark and (mark.released_at or homework.marks_released_at)
        ),
    }

    mark = mark or HomeworkMark(homework=homework, student=student)
    mark.score = score
    mark.status = status
    mark.feedback = feedback
    mark.late_deduction = late_deduction
    mark.resubmissions_allowed = extra_attempts
    mark.marked_by = actor
    mark.marked_at = timezone.now()
    mark.save()

    log_action(
        actor=actor,
        action="homework.marked",
        content_type=MARK_CONTENT_TYPE,
        object_id=mark.pk,
        change_summary={
            "homework": homework.title,
            "student": str(student),
            "from": previous,
            "to": {
                "score": str(mark.score) if mark.score is not None else None,
                "status": mark.status,
                "deduction": str(mark.late_deduction),
                "extra_attempts": mark.resubmissions_allowed,
            },
        },
        request=request,
    )

    # A class-wide release already happened, so this brand-new mark is visible
    # the moment it is saved — tell the pupil, exactly once.
    if _released(mark, homework) and not previous["released"]:
        _notify_feedback(student=student, homework=homework)

    return mark


def _notify_feedback(*, student, homework: Homework) -> None:
    user = getattr(student, "user", None)
    if user is None:
        return
    try:
        notify(
            recipient=user,
            title=f"Feedback on {homework.title}",
            message=(
                "Your teacher has shared feedback and a mark on your "
                "homework. Have a look when you are ready."
            ),
            link=reverse("homework:detail", args=[homework.pk]),
            category="homework",
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Feedback notification failed for homework %s student %s",
            homework.pk,
            getattr(student, "pk", "?"),
        )


def _released(mark: HomeworkMark, homework: Homework) -> bool:
    return mark.released_at is not None or homework.marks_released_at is not None


@transaction.atomic
def release_marks(*, homework: Homework, actor, request=None, students=None) -> int:
    """
    Make marked work visible: the whole class, or one pupil at a time.

    Returns how many marks became visible for the first time in this call.
    Only the pupils whose feedback has actually been released are notified,
    so a second press of the button does not send the class another message.
    """
    now = timezone.now()
    newly_released: list[HomeworkMark] = []

    if students is None:
        already = homework.marks_released_at is not None
        if not already:
            homework.marks_released_at = now
            homework.save(update_fields=["marks_released_at", "updated_at"])
        marks = list(
            homework.marks.exclude(status="").select_related("student__user")
        )
        if not already:
            # A pupil who was released individually already has the news.
            newly_released = [m for m in marks if m.released_at is None]
        scope = "class"
    else:
        student_ids = [getattr(s, "pk", s) for s in students]
        marks = list(
            homework.marks.filter(student_id__in=student_ids)
            .exclude(status="")
            .select_related("student__user")
        )
        for mark in marks:
            if not _released(mark, homework):
                newly_released.append(mark)
        for mark in newly_released:
            if mark.released_at is None:
                mark.released_at = now
                mark.save(update_fields=["released_at", "updated_at"])
        scope = "pupil"

    for mark in newly_released:
        _notify_feedback(student=mark.student, homework=homework)

    log_action(
        actor=actor,
        action="homework.marks_released",
        content_type=MARK_CONTENT_TYPE,
        object_id=homework.pk,
        change_summary={
            "homework": homework.title,
            "scope": scope,
            "released": len(newly_released),
        },
        request=request,
    )
    return len(newly_released)

