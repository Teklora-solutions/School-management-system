"""
apps/communications/models.py
------------------------------
Announcements (school-wide) and the Message/Thread system for
student and parent Complaints, Inquiries, and Satisfaction messages.

Message status workflow: Open → In Progress → Resolved
"""

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------


class Announcement(BaseModel):
    class Audience(models.TextChoices):
        EVERYONE = "everyone", "Everyone"
        BY_ROLE = "by_role", "By role"
        BY_CLASS = "by_class", "By class"

    title = models.CharField(max_length=200)
    body = models.TextField()
    audience = models.CharField(
        max_length=20, choices=Audience.choices, default=Audience.EVERYONE
    )
    # Comma-separated role slugs when audience=by_role
    target_roles = models.JSONField(default=list, blank=True)
    target_classes = models.ManyToManyField(
        "academics.SchoolClass",
        blank=True,
        related_name="announcements",
    )
    attachment = models.FileField(upload_to="announcements/", blank=True, null=True)
    publish_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="If set, announcement is scheduled; null means publish immediately.",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    is_published = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="announcements_created",
    )

    class Meta:
        db_table = "communications_announcement"
        ordering = ["-publish_at", "-created_at"]

    def __str__(self) -> str:
        return self.title


class AnnouncementRead(BaseModel):
    """Tracks which users have read which announcements (for unread badge)."""

    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name="reads",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="announcement_reads",
    )

    class Meta:
        db_table = "communications_announcement_read"
        unique_together = [("announcement", "user")]

    def __str__(self) -> str:
        return f"{self.user_id} read {self.announcement_id}"


# ---------------------------------------------------------------------------
# Message Threads (Complaints / Inquiries / Satisfaction)
# ---------------------------------------------------------------------------


class MessageThread(BaseModel):
    """
    A conversation thread initiated by a student or parent.
    Staff can reply, update status, and assign to a staff member.
    """

    class MessageType(models.TextChoices):
        COMPLAINT = "complaint", "Complaint"
        INQUIRY = "inquiry", "Inquiry"
        SATISFACTION = "satisfaction", "Satisfaction / Feedback"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"

    # The user who opened the thread (student or parent)
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="message_threads_sent",
    )
    # Optional: parent writing on behalf of a specific child
    on_behalf_of = models.ForeignKey(
        "students.Student",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="message_threads",
        help_text="Populated when a parent writes on behalf of a child.",
    )
    message_type = models.CharField(
        max_length=20,
        choices=MessageType.choices,
        db_index=True,
    )
    subject = models.CharField(max_length=200)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="message_threads_assigned",
        limit_choices_to={"role__in": ["admin", "director"]},
    )
    # Cached count for quick unread display
    last_reply_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "communications_message_thread"
        ordering = ["-last_reply_at", "-created_at"]
        indexes = [
            models.Index(fields=["sender", "status"]),
            models.Index(fields=["status", "message_type"]),
        ]

    def __str__(self) -> str:
        return f"[{self.get_message_type_display()}] {self.subject} ({self.get_status_display()})"


class ThreadMessage(BaseModel):
    """
    A single message (or reply) within a MessageThread.
    The first message is the original from the sender.
    Subsequent messages are replies (from either the sender or a staff member).
    """

    thread = models.ForeignKey(
        MessageThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="thread_messages",
    )
    body = models.TextField()
    attachment = models.FileField(upload_to="messages/", blank=True, null=True)
    # True for the opening message, False for replies
    is_opening = models.BooleanField(default=False)

    class Meta:
        db_table = "communications_thread_message"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Thread#{self.thread_id} by {self.author_id} at {self.created_at:%Y-%m-%d %H:%M}"
