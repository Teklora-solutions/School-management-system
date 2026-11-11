"""
apps/learning/feed_services.py
--------------------------------
Write-side logic for the class community feed.

Public API
----------
  create_post(author, school_class, body, post_type, lesson=None, quiz=None)
      Validates that the author is allowed to post this type in this class,
      then creates the ClassPost row and sends notifications to the class.

  remove_post(post, removed_by, reason="", request=None)
      Soft-deletes a post (sets is_removed=True).  Only teachers and above
      may remove; attempting as a student raises PermissionDenied.

  restore_post(post, restored_by, request=None)
      Un-removes a post.

  toggle_pin(post, actor)
      Flips is_pinned.  Only teachers and above may pin.

  set_reaction(post, user, emoji)
      Adds or replaces the user's reaction on a post.  Passing emoji=None
      or the same emoji the user already holds removes the reaction entirely
      (toggle behaviour).  Returns the saved ClassPostReaction or None.
      Parents are read-only in the feed and cannot react.

  create_reply(post, author, body)
      One-level reply under a post.  Community feed only.

  mark_best_answer(reply, actor)
      The question's author or a teacher chooses the one best answer.

  remove_reply(reply, removed_by, reason="", request=None)
      Teacher moderation of a reply.  If it held the best-answer flag the
      flag is cleared, which returns the question to "Waiting" unless
      another visible reply answers it.

Design rules:
  - No private 1:1 chat. Posts and replies are visible to everyone in the class.
  - Students may only post the types in ``STUDENT_ALLOWED_TYPES``; the two
    newer ones ("I figured it out", thank-you) need the community feed on.
    Attempts to post other types raise PermissionDenied.
  - Parents can read but never post, reply or react.
  - Reactions use a fixed child-safe set; no free-text.
  - Notifications go to the class teacher when a student posts.
  - All writes are @transaction.atomic.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.core.notifications import notify

from .models import ClassPost, ClassPostReply, ClassPostReaction

logger = logging.getLogger(__name__)

# Roles that may moderate (remove / pin) posts and replies.
MODERATOR_ROLES = {"admin", "director", "teacher"}

# Roles that may post at all.
POSTER_ROLES = {"admin", "director", "teacher", "student"}

# Teacher post types that only exist in the community feed.
COMMUNITY_ONLY_TYPES = {
    ClassPost.PostType.CHALLENGE,
    ClassPost.PostType.DISCUSSION_PROMPT,
    ClassPost.PostType.POLL,
}

# Student post types that only exist in the community feed.
COMMUNITY_ONLY_STUDENT_TYPES = {
    ClassPost.PostType.TIP,
    ClassPost.PostType.CELEBRATION,
}


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


def _assert_can_view(user, school_class) -> None:
    """Class-scoped guard shared by every feed action (mirrors the page check)."""
    from .feed_selectors import can_view_feed

    if not can_view_feed(user, school_class):
        raise PermissionDenied("You do not have access to this class feed.")


def _assert_community(school_class) -> None:
    """Community-only actions require both the school switch and the class switch."""
    from .feed_selectors import community_enabled_for

    if not community_enabled_for(school_class):
        raise PermissionDenied(
            "The class community feed is not switched on for this class."
        )


def _audit(*, actor, action: str, obj, summary: dict, request=None) -> None:
    """
    Best-effort audit write.  A logging failure must never undo a moderation
    action or leave half a transaction behind, so it is swallowed and reported.
    """
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


def _clean_link(link, school_class, kind: str):
    """Return a validated lesson/quiz object, or None when not supplied."""
    if link is None:
        return None
    if kind == "lesson":
        owner_class_id = link.module.school_class_id
    else:  # quiz
        owner_class_id = link.lesson.module.school_class_id
    if owner_class_id != school_class.pk:
        raise ValueError(f"That {kind} belongs to another class.")
    return link


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
    lesson=None,
    quiz=None,
    allow_poll: bool = False,
) -> ClassPost:
    """
    Create a class feed post.

    Raises PermissionDenied when:
      - The author's role cannot post (parent, finance, etc.).
      - A student tries to post a type they may not use.
      - A community-only type is used while the community feed is off.
      - The author is not connected to this class.
    """
    from .feed_selectors import community_enabled_for

    role = getattr(author, "role", "")

    if role not in POSTER_ROLES:
        raise PermissionDenied("Your role cannot post on the class feed.")

    valid_types = {choice[0] for choice in ClassPost.PostType.choices}
    if post_type not in valid_types:
        raise ValueError("Unknown post type.")
    if post_type == ClassPost.PostType.POLL and not allow_poll:
        raise PermissionDenied(
            "Polls are created with the poll form, not the post box."
        )

    community_on = community_enabled_for(school_class)

    if role == "student" and post_type not in ClassPost.STUDENT_ALLOWED_TYPES:
        raise PermissionDenied(
            "Students may only post questions and answers on the class feed."
        )
    if post_type in COMMUNITY_ONLY_TYPES and not community_on:
        raise PermissionDenied(
            "That post type needs the class community feed switched on."
        )
    if (
        role == "student"
        and post_type in COMMUNITY_ONLY_STUDENT_TYPES
        and not community_on
    ):
        raise PermissionDenied(
            "That post type needs the class community feed switched on."
        )

    _assert_in_class(author, school_class)

    if (lesson is not None or quiz is not None) and not community_on:
        raise PermissionDenied(
            "Lesson and quiz links need the class community feed switched on."
        )
    lesson = _clean_link(lesson, school_class, "lesson")
    quiz = _clean_link(quiz, school_class, "quiz")

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
        lesson=lesson,
        quiz=quiz,
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
def remove_post(
    *,
    post: ClassPost,
    removed_by,
    reason: str = "",
    request=None,
) -> ClassPost:
    """
    Soft-delete a post.  The post stays in the database (teachers see it
    greyed out) but students and parents no longer see it or its replies.
    """
    role = getattr(removed_by, "role", "")
    if role not in MODERATOR_ROLES:
        raise PermissionDenied("Only teachers and above may remove posts.")
    _assert_can_view(removed_by, post.school_class)

    was_removed = post.is_removed
    post.is_removed = True
    post.removed_by = removed_by
    post.removed_at = timezone.now()
    post.save(update_fields=["is_removed", "removed_by", "removed_at", "updated_at"])

    if not was_removed:
        _audit(
            actor=removed_by,
            action="feed.post.removed",
            obj=post,
            summary={
                "reason": reason,
                "post_type": post.post_type,
                "author_id": post.author_id,
                "class_id": post.school_class_id,
            },
            request=request,
        )

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
def restore_post(*, post: ClassPost, restored_by, request=None) -> ClassPost:
    """Un-remove a post. Only teachers and above."""
    role = getattr(restored_by, "role", "")
    if role not in MODERATOR_ROLES:
        raise PermissionDenied("Only teachers and above may restore posts.")
    _assert_can_view(restored_by, post.school_class)

    was_removed = post.is_removed
    post.is_removed = False
    post.removed_by = None
    post.removed_at = None
    post.save(update_fields=["is_removed", "removed_by", "removed_at", "updated_at"])

    if was_removed:
        _audit(
            actor=restored_by,
            action="feed.post.restored",
            obj=post,
            summary={
                "post_type": post.post_type,
                "author_id": post.author_id,
                "class_id": post.school_class_id,
            },
            request=request,
        )
    return post


@transaction.atomic
def toggle_pin(*, post: ClassPost, actor) -> ClassPost:
    """Pin or unpin a post. Only teachers and above."""
    role = getattr(actor, "role", "")
    if role not in MODERATOR_ROLES:
        raise PermissionDenied("Only teachers and above may pin posts.")
    _assert_can_view(actor, post.school_class)
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

    Parents are read-only in the feed: the server rejects their reactions even
    though the UI hides the buttons (F3 can revisit whether that changes).
    """
    role = getattr(user, "role", "")
    if role not in POSTER_ROLES:
        raise PermissionDenied("Your role cannot react to feed posts.")
    _assert_can_view(user, post.school_class)

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


# ---------------------------------------------------------------------------
# Community feed — replies and best answer
# ---------------------------------------------------------------------------


@transaction.atomic
def create_reply(*, post: ClassPost, author, body: str) -> ClassPostReply:
    """
    Add one reply under a post.

    Replies are one level deep by design — there is no way to reply to a reply.
    No points are awarded for replies in F1.
    """
    role = getattr(author, "role", "")
    if role not in POSTER_ROLES:
        raise PermissionDenied("Parents and visitors cannot reply to the class feed.")
    _assert_community(post.school_class)
    _assert_in_class(author, post.school_class)

    if post.is_removed:
        raise PermissionDenied("This post has been removed.")

    body = body.strip()
    if not body:
        raise ValueError("A reply cannot be empty.")
    if len(body) > 1000:
        raise ValueError("A reply cannot exceed 1000 characters.")

    # Refuse a student's bare starter — it is a prompt, not an answer.
    # Teachers may send a guiding question on its own: that is the point of
    # their starter set.
    from .reply_starters import is_starter_only

    if is_starter_only(body, getattr(author, "role", "")):
        raise ValueError(
            "That looks like a prompt, not an answer. "
            "Add your own thoughts after the starter and try again."
        )

    reply = ClassPostReply.objects.create(post=post, author=author, body=body)

    # ── Notify the question's author on their FIRST reply ──────────────
    # Rules: only once (first visible reply), never the author's own reply,
    # only when the post is a question, reply is not removed.
    _notify_first_reply(post=post, reply=reply)

    return reply


def _notify_first_reply(*, post: ClassPost, reply: ClassPostReply) -> None:
    """
    Send one notification to the question's author when the first visible
    reply arrives.  Never fires for a removed reply, the author's own reply,
    or if a notification was already sent for an earlier reply on this post.
    """
    if post.post_type != ClassPost.PostType.QUESTION:
        return
    if post.author is None:
        return
    if reply.author_id == post.author_id:
        return   # never notify about your own reply
    if reply.is_removed:
        return

    # Is this the first visible reply? Counted after the save above, so the
    # new reply itself is excluded.
    visible_before = (
        ClassPostReply.objects.filter(post=post, is_removed=False)
        .exclude(pk=reply.pk)
        .count()
    )
    if visible_before > 0:
        return

    # Preference rows are created lazily on first use; a missing row means on.
    # Teachers' operational notices are not governed by this table.
    if getattr(post.author, "role", "") in ("student", "parent"):
        from apps.core.models import NotificationPreference

        pref = NotificationPreference.for_user(post.author)
        if not pref.notify_feed_reply:
            return

    replier_name = reply.author.first_name if reply.author else "Someone"
    class_name = post.school_class.name
    try:
        notify(
            recipient=post.author,
            title=f"{replier_name} replied to your question in {class_name}",
            message="",   # no reply content in notifications
            link=f"/learning/class/{post.school_class_id}/feed/post/{post.pk}/",
            category="feed_reply",
        )
    except Exception:   # noqa: BLE001
        logger.warning("First-reply notification failed for post %s", post.pk)


@transaction.atomic
def mark_best_answer(*, reply: ClassPostReply, actor) -> ClassPostReply:
    """
    Choose *reply* as the one best answer.

    Only the question's author or a teacher/admin/director may choose. Choosing
    a different reply replaces the previous choice; the partial unique index
    guarantees at most one best answer per post.
    """
    post = reply.post
    if post.post_type != ClassPost.PostType.QUESTION:
        raise ValueError("Only questions can have a best answer.")
    _assert_community(post.school_class)
    _assert_can_view(actor, post.school_class)
    if reply.is_removed or post.is_removed:
        raise ValueError("This reply is no longer available.")

    role = getattr(actor, "role", "")
    is_author = post.author_id is not None and post.author_id == actor.pk
    if role not in MODERATOR_ROLES and not is_author:
        raise PermissionDenied(
            "Only the question's author or a teacher can choose the best answer."
        )

    # Clear any previous choice first — the unique index allows only one.
    was_already_best = reply.is_best_answer
    ClassPostReply.objects.filter(
        post=post, is_best_answer=True
    ).exclude(pk=reply.pk).update(is_best_answer=False, updated_at=timezone.now())

    reply.is_best_answer = True
    reply.save(update_fields=["is_best_answer", "updated_at"])

    # ── Notifications ──────────────────────────────────────────────────
    # Re-clicking Best answer on the same reply must not notify twice.
    if not was_already_best:
        _notify_best_answer(post=post, reply=reply, chosen_by=actor)

    return reply


def _notify_best_answer(*, post: ClassPost, reply: ClassPostReply, chosen_by) -> None:
    """
    Two notifications on Best Answer:
    1. The reply's author: "Your answer was chosen" (always, unless it's their
       own reply — but marking your own reply is not allowed anyway).
    2. The question's author: notified only when a *teacher* (or admin/director)
       chose the best answer, so the student knows the teacher endorsed it.

    Neither fires if the reply or the post is removed.
    Quiet hours apply.  Reply content is never included in the text.
    """
    if reply.is_removed or post.is_removed:
        return

    chosen_by_role = getattr(chosen_by, "role", "")
    class_name = post.school_class.name
    feed_link = f"/learning/class/{post.school_class_id}/feed/post/{post.pk}/"
    chosen_by_name = chosen_by.first_name if chosen_by else "Your teacher"

    # 1. Notify the reply's author
    if reply.author and reply.author_id != chosen_by.pk:
        if getattr(reply.author, "role", "") in ("student", "parent"):
            from apps.core.models import NotificationPreference

            pref = NotificationPreference.for_user(reply.author)
            can_notify = pref.notify_feed_best_answer
        else:
            can_notify = True
        if can_notify:
            try:
                notify(
                    recipient=reply.author,
                    title=f"Your answer was chosen as Best Answer in {class_name}",
                    message="",
                    link=feed_link,
                    category="feed_best_answer",
                )
            except Exception:   # noqa: BLE001
                logger.warning("Best-answer notify failed for reply author, reply %s", reply.pk)

    # 2. Notify the question's author when a teacher/admin/director chose it
    if (
        chosen_by_role in MODERATOR_ROLES
        and post.author
        and post.author_id != chosen_by.pk
        and post.author_id != getattr(reply, "author_id", None)
    ):
        if getattr(post.author, "role", "") in ("student", "parent"):
            from apps.core.models import NotificationPreference

            pref = NotificationPreference.for_user(post.author)
            can_notify = pref.notify_feed_best_answer
        else:
            can_notify = True
        if can_notify:
            try:
                notify(
                    recipient=post.author,
                    title=f"{chosen_by_name} marked a Best Answer for your question in {class_name}",
                    message="",
                    link=feed_link,
                    category="feed_best_answer",
                )
            except Exception:   # noqa: BLE001
                logger.warning("Best-answer notify failed for question author, post %s", post.pk)


@transaction.atomic
def remove_reply(
    *,
    reply: ClassPostReply,
    removed_by,
    reason: str = "",
    request=None,
) -> ClassPostReply:
    """
    Teacher moderation of a reply.  If the reply held the best-answer flag the
    flag is cleared, which returns the question to "Waiting" unless another
    visible reply answers it (status is computed from visible replies).
    """
    role = getattr(removed_by, "role", "")
    if role not in MODERATOR_ROLES:
        raise PermissionDenied("Only teachers and above may remove replies.")
    _assert_community(reply.post.school_class)
    _assert_can_view(removed_by, reply.post.school_class)

    was_removed = reply.is_removed
    reply.is_removed = True
    reply.removed_by = removed_by
    reply.removed_at = timezone.now()
    reply.is_best_answer = False
    reply.save(
        update_fields=[
            "is_removed",
            "removed_by",
            "removed_at",
            "is_best_answer",
            "updated_at",
        ]
    )

    if not was_removed:
        _audit(
            actor=removed_by,
            action="feed.reply.removed",
            obj=reply,
            summary={
                "reason": reason,
                "post_id": reply.post_id,
                "author_id": reply.author_id,
                "class_id": reply.post.school_class_id,
            },
            request=request,
        )
    return reply
