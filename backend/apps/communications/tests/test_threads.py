"""
apps/communications/tests/test_threads.py
-------------------------------------------
Message thread access control and reply notification (Module 4).
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.core.models import Notification
from apps.dashboards.tests.fixtures import make_user

from ..models import Announcement, AnnouncementRead, MessageThread, ThreadMessage
from ..selectors import unread_announcement_count
from ..services import create_announcement, reply_to_thread


class ThreadAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sender = make_user("student", username="thread_sender")
        cls.other_student = make_user("student", username="thread_other")
        cls.staff = make_user("admin", username="thread_admin")
        cls.thread = MessageThread.objects.create(
            sender=cls.sender,
            message_type=MessageThread.MessageType.COMPLAINT,
            subject="Library noise",
        )
        cls.opening = ThreadMessage.objects.create(
            thread=cls.thread,
            author=cls.sender,
            body="Too noisy in the library.",
            is_opening=True,
        )

    def test_sender_can_view_own_thread_and_template_renders(self):
        self.client.force_login(self.sender)
        response = self.client.get(
            reverse("communications:thread_detail", args=[self.thread.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too noisy in the library.")
        self.assertContains(response, "You")

    def test_staff_can_view_thread(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("communications:thread_detail", args=[self.thread.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_other_student_gets_403(self):
        self.client.force_login(self.other_student)
        response = self.client.get(
            reverse("communications:thread_detail", args=[self.thread.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(
            reverse("communications:thread_detail", args=[self.thread.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_staff_reply_notifies_sender(self):
        reply_to_thread(
            thread=self.thread, author=self.staff, body="We will fix this today."
        )
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.sender, category="message"
            ).exists()
        )

    def test_admin_can_assign_and_resolve_thread(self):
        self.client.force_login(self.staff)
        inbox_url = reverse("communications:inbox_thread", args=[self.thread.pk])
        assigned = self.client.post(
            inbox_url, {"action": "assign", "assignee_id": self.staff.pk}
        )
        self.assertEqual(assigned.status_code, 302)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.assigned_to, self.staff)
        self.assertEqual(self.thread.status, MessageThread.Status.IN_PROGRESS)

        resolved = self.client.post(
            inbox_url,
            {"action": "status", "new_status": MessageThread.Status.RESOLVED},
        )
        self.assertEqual(resolved.status_code, 302)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.status, MessageThread.Status.RESOLVED)

        self.client.force_login(self.sender)
        response = self.client.get(
            reverse("communications:thread_detail", args=[self.thread.pk])
        )
        self.assertContains(response, "Resolved")

    def test_announcement_unread_badge_clears_after_mark_read(self):
        announcement = create_announcement(
            title="Read me",
            body="Unread until opened.",
            audience=Announcement.Audience.EVERYONE,
            created_by=self.staff,
        )
        self.client.force_login(self.sender)
        self.assertEqual(unread_announcement_count(user=self.sender), 1)

        response = self.client.get(reverse("communications:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Read me")

        marked = self.client.post(
            reverse("communications:announcement_read", args=[announcement.pk])
        )
        self.assertEqual(marked.status_code, 302)
        self.assertTrue(
            AnnouncementRead.objects.filter(
                announcement=announcement, user=self.sender
            ).exists()
        )
        self.assertEqual(unread_announcement_count(user=self.sender), 0)

    def test_sender_reply_does_not_notify_self(self):
        reply_to_thread(thread=self.thread, author=self.sender, body="Any update?")
        self.assertFalse(
            Notification.objects.filter(
                recipient=self.sender, category="message"
            ).exists()
        )
