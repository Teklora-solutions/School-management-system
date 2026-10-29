"""
apps/communications/selectors.py
----------------------------------
Read-only queries for announcements and message threads.
"""

from __future__ import annotations

from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from .models import Announcement, AnnouncementRead, MessageThread, ThreadMessage

# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------


def announcements_for_user(*, user) -> QuerySet:
    """
    Published, not-expired announcements visible to this user.
    Filters by audience (everyone / by_role / by_class).
    """
    now = timezone.now()
    qs = (
        Announcement.objects.filter(is_published=True)
        .filter(Q(publish_at__isnull=True) | Q(publish_at__lte=now))
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .select_related("created_by")
        .prefetch_related("target_classes")
        .order_by("-created_at")
    )

    # Annotate read status for this user
    read_subq = AnnouncementRead.objects.filter(announcement=OuterRef("pk"), user=user)
    qs = qs.annotate(is_read=Exists(read_subq))

    # Admin, director, finance see everything. Teachers are scoped to the
    # classes they teach or lead, mirroring the staff announcement rule.
    if user.role in ("admin", "director", "finance"):
        return qs

    role = user.role
    class_ids: set[int] = set()
    if role == "teacher":
        teacher = getattr(user, "teacher_profile", None)
        if teacher is not None:
            from apps.teachers.models import TeacherAssignment

            class_ids = set(
                TeacherAssignment.objects.filter(teacher=teacher).values_list(
                    "school_class_id", flat=True
                )
            )
        class_ids |= set(user.homeroom_classes.values_list("pk", flat=True))
    else:
        student = getattr(user, "student_profile", None)
        if student is not None and student.current_class_id:
            class_ids.add(student.current_class_id)
        elif user.role == "parent":
            parent_profile = getattr(user, "parent_profile", None)
            if parent_profile is not None:
                class_ids = set(
                    parent_profile.children_links.exclude(
                        student__current_class__isnull=True
                    ).values_list("student__current_class_id", flat=True)
                )

    audience_q = Q(audience="everyone")
    # JSONField ``contains`` is unsupported on SQLite, so match the quoted role
    # slug inside the serialized JSON list instead (works on SQLite + Postgres).
    audience_q |= Q(audience="by_role", target_roles__icontains=f'"{role}"')
    if class_ids:
        audience_q |= Q(audience="by_class", target_classes__id__in=class_ids)

    return qs.filter(audience_q).distinct()


def unread_announcement_count(*, user) -> int:
    return announcements_for_user(user=user).filter(is_read=False).count()


def get_announcement(pk: int) -> Announcement:
    return (
        Announcement.objects.select_related("created_by")
        .prefetch_related("target_classes")
        .get(pk=pk)
    )


def mark_announcement_read(*, announcement_id: int, user) -> None:
    AnnouncementRead.objects.get_or_create(announcement_id=announcement_id, user=user)


# ---------------------------------------------------------------------------
# Message threads
# ---------------------------------------------------------------------------


def threads_for_sender(*, user, status: str = "") -> QuerySet:
    """All threads where the user is the sender."""
    qs = (
        MessageThread.objects.filter(sender=user)
        .select_related("on_behalf_of__user", "assigned_to")
        .annotate(message_count=Count("messages"))
        .order_by("-last_reply_at", "-created_at")
    )
    if status:
        qs = qs.filter(status=status)
    return qs


def threads_inbox(
    *, status: str = "", message_type: str = "", search: str = ""
) -> QuerySet:
    """Staff inbox — all threads with optional filters."""
    qs = (
        MessageThread.objects.select_related(
            "sender", "on_behalf_of__user", "assigned_to"
        )
        .annotate(message_count=Count("messages"))
        .order_by("-last_reply_at", "-created_at")
    )
    if status:
        qs = qs.filter(status=status)
    if message_type:
        qs = qs.filter(message_type=message_type)
    if search:
        qs = qs.filter(
            Q(subject__icontains=search)
            | Q(sender__first_name__icontains=search)
            | Q(sender__last_name__icontains=search)
        )
    return qs


def get_thread(pk: int) -> MessageThread:
    return MessageThread.objects.select_related(
        "sender", "on_behalf_of__user", "assigned_to"
    ).get(pk=pk)


def thread_messages(*, thread_id: int) -> QuerySet:
    return (
        ThreadMessage.objects.filter(thread_id=thread_id)
        .select_related("author")
        .order_by("created_at")
    )
