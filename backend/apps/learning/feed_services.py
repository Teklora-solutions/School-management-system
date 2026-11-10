"""
apps/learning/feed_services.py
--------------------------------
Write-side logic for the class community feed (Piece 2).

Public API
----------
  create_post(author, school_class, body, post_type)
      Validates that the author is allowed to post this type in this class,
      then creates the ClassPost row and sends notifications to the class.

  remove_post(post, removed_by)
      Soft-deletes a post (sets is_removed=True).  Only teachers and above
      may remove; attempting as a student raises PermissionDenied.

  toggle_pin(post, actor)
      Flips is_pinned.  Only teachers and above may pin.

  set_reaction(post, user, emoji)
      Adds or replaces the user's reaction on a post.  Passing emoji=None
      or the same emoji the user already holds removes the reaction entirely
      (toggle behaviour).  Returns the saved ClassPostReaction or None.

Design rules:
  - No private 1:1 chat. Posts are visible to everyone in the class.
  - Students may only post type QUESTION. Attempts to post other types
    raise PermissionDenied.
  - Parents can read but never post. Attempts raise PermissionDenied.
  - Reactions use a fixed child-safe set; no free-text.
  - Notifications go to the class teacher when a student posts.
    Students get a notification when their own post is answered by a teacher
    (Piece 3 will expand this — for now we notify the teacher of every
    student question so nothing is missed).
  - All writes are @transaction.atomic.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.core.notifications import notify

from .models import ClassPost, ClassPostReaction

logger = logging.getLogger(__name__)

# Roles that may moderate (remove / pin) posts.
MODERATOR_ROLES = {"admin", "director", "teacher"}

# Roles that may post at all.
POSTER_ROLES = {"admin", "director", "teacher", "student"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _assert_in_class(user, school_class) -> None:
    """
    Raise PermissionDenied when *user* has no connection to *school_class*.

    Teachers are connected via TeacherAssignment.
    Students are connected via current_class.
    Admins and directors are connected to every class.
    Parents may not post.
    """
    role = getattr(user, "role", "")
    if role in ("admin", "director"):
        return
    if role == "teacher":
        from apps.teachers.models import TeacherAssignment
        from apps.academics.selectors import get_current_year
        year = get_current_year()
        if year is None:
            raise PermissionDenied("No current academic year.")
        assigned = TeacherAssignment.objects.filter(
            teacher__user=user,
            school_class=school_class,
            academic_year=year,
        ).exists()
        # Also allow the class teacher
        is_class_teacher = school_class.class_teacher_id == user.pk
        if not assigned and not is_class_teacher:
            raise PermissionDenied("You are not assigned to this class.")
        return
    if role == "student":
        student = getattr(user, "student_profile", None)
        if student is None or student.current_class_id != school_class.pk:
            raise PermissionDenied("You are not enrolled in this class.")
        return
    raise PermissionDenied("Your role cannot post on the class feed.")


def _notify_teacher(post: ClassPost) -> None:
    """Notify the class teacher when a student posts a question."""
    class_teacher_user = post.school_class.class_teacher
    if class_teacher_user is None:
        return
    if class_teacher_user == post.author:
        return
    try:
        notify(
            recipient=class_teacher_user,
            title=f"New question in {post.school_class.name}",
            message=(post.body[:120] + "…") if len(post.body) > 120 else post.body,
            link=f"/learning/class/{post.school_class_id}/feed/",
            category="feed",
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to notify class teacher for post %s", post.pk)


def _notify_student_answered(post: ClassPost, replying_teacher) -> None:
    """
    Notify the student author that a teacher posted after their question.
    Only fires when a teacher posts an announcement/tip in response; this is
    a lightweight signal, not a thread reply. Piece 3 (points & badges) will
    add a richer 'your question was answered' mechanic.
    """
    if post.author is None:
        return
    if post.author.role != "student":
        return
    try:
        notify(
            recipient=post.author,
            title=f"Your class teacher posted in {post.school_class.name}",
            message="A teacher added something to the class feed. Check it out.",
            link=f"/learning/class/{post.school_class_id}/feed/",
            category="feed",
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to notify student for post %s", post.pk)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@transaction.atomic
def create_post(
    *,
    author,
    school_class,
    body: str,
    post_type: str = ClassPost.PostType.ANNOUNCEMENT,
) -> ClassPost:
    """
    Create a class feed post.

    Raises PermissionDenied when:
      - The author's role cannot post (parent, finance, etc.).
      - A student tries to post a non-QUESTION type.
      - The author is not connected to this class.
    """
    role = getattr(author, "role", "")

    if role not in POSTER_ROLES:
        raise PermissionDenied("Your role cannot post on the class feed.")

    if role == "student" and post_type not in ClassPost.STUDENT_ALLOWED_TYPES:
        raise PermissionDenied(
            "Students may only post questions on the class feed."
        )

    _assert_in_class(author, school_class)

    body = body.strip()
    if not body:
        raise ValueError("Post body cannot be empty.")
    if len(body) > 2000:
        raise ValueError("Post body cannot exceed 2000 characters.")

    post = ClassPost.objects.create(
        author=author,
        school_class=school_class,
        post_type=post_type,
        body=body,
    )

    # Notifications
    if role == "student":
        _notify_teacher(post)

    # ── Piece 3: award points for student questions ──────────────────────
    # Only student questions earn points, not teacher posts.
    # The post must not be removed — if it is removed later, remove_post()
    # will issue a reversal.  The award fires here (before any moderation)
    # so the student sees immediate feedback; the reversal corrects it.
    if role == "student" and post_type == ClassPost.PostType.QUESTION:
        try:
            from .points_config import REASON_FEED_POST, get_point_config
            from .points_services import award_points

            cfg = get_point_config()
            # The student model is at author.student_profile
            student_profile = getattr(author, "student_profile", None)
            if student_profile is not None:
                award_points(
                    student=student_profile,
                    reason=REASON_FEED_POST,
                    points=cfg.pts_feed_question,
                    description=f"Asked a question in {school_class.name}",
                    idempotency_key=f"feed_post:{post.pk}:{student_profile.pk}",
                )
        except Exception:  # noqa: BLE001
            logger.warning("Points award failed for feed post %s", post.pk)

    return post


@transaction.atomic
def remove_post(*, post: ClassPost, removed_by) -> ClassPost:
    """
    Soft-delete a post.  The post stays in the database (teachers see it
    greyed out) but students and parents no longer see it.
    """
    role = getattr(removed_by, "role", "")
    if role not in MODERATOR_ROLES:
        raise PermissionDenied("Only teachers and above may remove posts.")

    # Students can remove their own post only; teachers can remove any post.
    if role == "student":
        raise PermissionDenied("Students cannot remove posts.")

    post.is_removed = True
    post.removed_by = removed_by
    post.removed_at = timezone.now()
    post.save(update_fields=["is_removed", "removed_by", "removed_at", "updated_at"])

    # ── Piece 3: revoke points when a post is removed ────────────────────
    # If the post was a student question that earned points, reverse the award
    # so a removed/inappropriate post never rewards the student.
    if post.author and post.author.role == "student":
        try:
            from .points_services import reverse_award
            from .models import PointLedger

            student_profile = getattr(post.author, "student_profile", None)
            if student_profile is not None:
                idem_key = f"feed_post:{post.pk}:{student_profile.pk}"
                original = (
                    PointLedger.objects.filter(idempotency_key=idem_key)
                    .select_related("reversed_by")
                    .first()
                )
                if original is not None:
                    # Only reverse if not already reversed
                    already = PointLedger.objects.filter(reversal_of=original).exists()
                    if not already:
                        reverse_award(
                            ledger_row=original,
                            reversed_by=removed_by,
                            reason_text=f"Post removed from {post.school_class.name} feed",
                        )
        except Exception:  # noqa: BLE001
            logger.warning("Points reversal failed for removed post %s", post.pk)

    return post


@transaction.atomic
def restore_post(*, post: ClassPost, restored_by) -> ClassPost:
    """Un-remove a post. Only teachers and above."""
    role = getattr(restored_by, "role", "")
    if role not in MODERATOR_ROLES:
        raise PermissionDenied("Only teachers and above may restore posts.")
    post.is_removed = False
    post.removed_by = None
    post.removed_at = None
    post.save(update_fields=["is_removed", "removed_by", "removed_at", "updated_at"])
    return post


@transaction.atomic
def toggle_pin(*, post: ClassPost, actor) -> ClassPost:
    """Pin or unpin a post. Only teachers and above."""
    role = getattr(actor, "role", "")
    if role not in MODERATOR_ROLES:
        raise PermissionDenied("Only teachers and above may pin posts.")
    post.is_pinned = not post.is_pinned
    post.save(update_fields=["is_pinned", "updated_at"])
    return post


@transaction.atomic
def set_reaction(
    *,
    post: ClassPost,
    user,
    emoji: str | None,
) -> ClassPostReaction | None:
    """
    Add, replace, or remove the user's reaction on *post*.

    Logic:
      - emoji=None  → remove existing reaction (if any).
      - emoji=same  → remove (toggle off).
      - emoji=new   → replace existing, or create.

    Parents can react (read-only role for posting, but reactions are fine).
    Only roles that can see the feed may react.
    """
    role = getattr(user, "role", "")
    if role not in {*POSTER_ROLES, "parent"}:
        raise PermissionDenied("Your role cannot react to feed posts.")

    # Validate emoji is in the allowed set
    valid_emojis = {choice[0] for choice in ClassPostReaction.Emoji.choices}
    if emoji is not None and emoji not in valid_emojis:
        raise ValueError(f"Emoji '{emoji}' is not allowed.")

    existing = ClassPostReaction.objects.filter(post=post, user=user).first()

    if emoji is None or (existing and existing.emoji == emoji):
        # Toggle off
        if existing:
            existing.delete()
        return None

    if existing:
        existing.emoji = emoji
        existing.save(update_fields=["emoji", "updated_at"])
        return existing

    return ClassPostReaction.objects.create(post=post, user=user, emoji=emoji)
