"""
apps/learning/digest_tasks.py
-------------------------------
The morning digest of unanswered class-feed questions.

Rules (agreed for F2a)
----------------------
* One digest per teacher per school day, at about 07:00 Nairobi time. A
  teacher with three classes gets one notification, not three. No questions
  waiting means no digest at all.
* A question qualifies only when the community feed is on for its class (both
  the school switch and the class switch), the question is not removed, and it
  has no visible reply.
* ``digest_sent_at`` stops a question appearing in consecutive digests. It is
  stamped only when the question was actually included in a digest, so a
  question whose recipients were all skipped today is retried tomorrow.
* A digest never hides the back of the queue: it ends with
  "N older questions are still waiting" and links to the Unanswered filter.

Who receives a question
-----------------------
* every teacher with a TeacherAssignment for that class in the class's
  academic year;
* the class teacher, even when they hold no subject assignment;
* when the question is linked to a lesson, the teacher of that lesson's
  subject is added explicitly (normally already in the first group).

For a question with **no lesson link**, the recipients are the class's assigned
teachers plus the class teacher. No subject is inferred, so no extra teacher is
added.

Schedule and time zone
----------------------
A ``django_q.Schedule`` row of type "D" (daily) is created by migration
``0012_digest_schedule`` with its first ``next_run`` at the next 07:00 in
Africa/Nairobi. django-q stores datetimes in UTC; the clock time is fixed in
Nairobi, which has no daylight saving, so the interval never drifts. The task
also refuses Saturday/Sunday (``force=True`` bypasses that for the manual
command).

Worker down at 07:00
--------------------
``Q_CLUSTER["catch_up"]`` is False: django-q skips the missed slot and does not
run it later. Teachers simply get no digest that day; the questions stay
unanswered and unstamped, so the next run includes them. The F2d heartbeat
screen is where that missed slot becomes visible.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from django.db.models import Q
from django.utils import timezone

from apps.core.notifications import notify

from .models import ClassFeedSettings, ClassPost

logger = logging.getLogger(__name__)

DIGEST_CATEGORY = "digest"


def _school_feed_on() -> bool:
    from apps.core.selectors import school_settings_values

    return bool(school_settings_values().get("feed_community_enabled"))


def _active_feed_class_ids() -> list[int]:
    if not _school_feed_on():
        return []
    return list(
        ClassFeedSettings.objects.filter(is_active=True).values_list(
            "school_class_id", flat=True
        )
    )


def get_digest_recipients(post: ClassPost) -> set:
    """
    The users who should hear about *post*. Exposed for tests.

    Class membership is read from the class's own academic year, not the
    "current year" switch, so an old class feed cannot quietly notify teachers
    who never taught it.
    """
    from apps.accounts.models import User
    from apps.teachers.models import TeacherAssignment

    if post.school_class_id is None:
        return set()

    assignments = TeacherAssignment.objects.filter(
        school_class=post.school_class,
        academic_year=post.school_class.academic_year,
    ).select_related("teacher__user")

    recipients = {a.teacher.user for a in assignments if a.teacher.user_id}

    if post.school_class.class_teacher_id:
        class_teacher = User.objects.filter(
            pk=post.school_class.class_teacher_id
        ).first()
        if class_teacher is not None:
            recipients.add(class_teacher)

    if post.lesson_id:
        # Subject teacher of the linked lesson. Usually already assigned
        # above; adding explicitly makes the rule hold even if the base list
        # changes.
        subject_id = post.lesson.module.subject_id
        for assignment in assignments:
            if assignment.subject_id == subject_id:
                recipients.add(assignment.teacher.user)

    return recipients


def _new_questions(active_class_ids: list[int]):
    """
    Visible, unanswered, not-yet-digested questions in feed-on classes.

    "Unanswered" is computed from visible replies only, so a removed reply puts
    the question back in the digest.
    """
    if not active_class_ids:
        return ClassPost.objects.none()
    visible_reply_post_ids = (
        ClassPost.objects.filter(replies__is_removed=False)
        .values_list("pk", flat=True)
    )
    return (
        ClassPost.objects.filter(
            school_class_id__in=active_class_ids,
            post_type=ClassPost.PostType.QUESTION,
            is_removed=False,
            digest_sent_at__isnull=True,
        )
        .exclude(pk__in=visible_reply_post_ids)
        .select_related("school_class", "lesson__module__subject", "author")
        .order_by("created_at", "pk")
    )


def _older_waiting_count(recipient, new_pks: set[int]) -> int:
    """
    Unanswered questions in this teacher's feed-on classes that were already
    digested once and are still waiting. These are the ones the digest must not
    let anyone forget.
    """
    from apps.teachers.models import TeacherAssignment
    from apps.accounts.models import User

    visible_reply_post_ids = (
        ClassPost.objects.filter(replies__is_removed=False)
        .values_list("pk", flat=True)
    )
    query = (
        ClassPost.objects.filter(
            post_type=ClassPost.PostType.QUESTION,
            is_removed=False,
            digest_sent_at__isnull=False,
        )
        .exclude(pk__in=new_pks)
        .exclude(pk__in=visible_reply_post_ids)
    )

    if getattr(recipient, "role", "") == "teacher":
        from django.db.models import F

        from apps.academics.models import SchoolClass
        from apps.teachers.models import TeacherAssignment

        class_ids = set(
            TeacherAssignment.objects.filter(
                teacher__user=recipient,
                academic_year=F("school_class__academic_year"),
            ).values_list("school_class_id", flat=True)
        )
        class_ids |= set(
            SchoolClass.objects.filter(class_teacher=recipient).values_list(
                "pk", flat=True
            )
        )
        query = query.filter(school_class_id__in=class_ids)
    else:
        # Admins/directors are not scheduled as recipients, but keep a sane
        # answer if this helper is called for one.
        query = query.none()

    active_ids = set(_active_feed_class_ids())
    return query.filter(school_class_id__in=active_ids).count()


def _digest_already_sent_today(recipient) -> bool:
    from apps.core.models import Notification

    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return Notification.objects.filter(
        recipient=recipient,
        category=DIGEST_CATEGORY,
        created_at__gte=start,
    ).exists()


def _unanswered_posts_for_digest():
    """Compatibility wrapper used by older callers/tests."""
    return _new_questions(_active_feed_class_ids())


def send_teacher_digests(*, force: bool = False) -> int:
    """
    Build and send today's digests. Returns the number of notifications sent.

    ``force=True`` is for the manual management command: it ignores the
    weekday check so a teacher can test on a Saturday. It does not bypass the
    one-per-teacher-per-day guard or the "something is waiting" rule.
    """
    local_now = timezone.localtime()
    if not force and local_now.weekday() >= 5:
        return 0

    active_class_ids = _active_feed_class_ids()
    posts = list(_new_questions(active_class_ids))
    if not posts:
        return 0

    # teacher -> their new questions across every class
    by_teacher: dict = defaultdict(list)
    for post in posts:
        for teacher in get_digest_recipients(post):
            by_teacher[teacher].append(post)

    sent = 0
    stamped: set[int] = set()
    for teacher, teacher_posts in by_teacher.items():
        if _digest_already_sent_today(teacher):
            continue

        # Group by class for the message, preserving first-seen order.
        per_class: dict[int, list[ClassPost]] = {}
        for post in teacher_posts:
            per_class.setdefault(post.school_class_id, []).append(post)

        total = len(teacher_posts)
        noun = "question" if total == 1 else "questions"
        title = f"{total} student {noun} waiting for an answer"

        lines = []
        for class_posts in per_class.values():
            class_name = class_posts[0].school_class.name
            lines.append(
                f"{class_name}: {len(class_posts)} waiting"
            )

        older = _older_waiting_count(teacher, {p.pk for p in teacher_posts})
        if older:
            if older == 1:
                lines.append("1 older question is still waiting")
            else:
                lines.append(f"{older} older questions are still waiting")

        # Link to the Unanswered queue of the class holding most new questions.
        target_class_id = max(
            per_class,
            key=lambda cid: (len(per_class[cid]), -cid),
        )
        link = (
            f"/learning/class/{target_class_id}/feed/?filter=unanswered"
        )

        try:
            notify(
                recipient=teacher,
                title=title,
                message="\n".join(lines),
                link=link,
                category=DIGEST_CATEGORY,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Digest send failed for teacher %s", teacher.pk, exc_info=True
            )
            continue

        sent += 1
        stamped.update(p.pk for p in teacher_posts)

    if stamped:
        ClassPost.objects.filter(pk__in=stamped).update(
            digest_sent_at=timezone.now()
        )

    return sent
