"""
apps/core/tests/test_notifications.py
--------------------------------------
The in-app bell, from both ends: an enquiry puts a notification in front of
every active admin, and a signed-in user can only ever see and open their own.
"""

from __future__ import annotations

from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import Notification
from apps.public.models import ContactMessage


def _user(role: str, username: str, *, is_active: bool = True) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@test.school",
        password="Testpass1!",
        role=role,
        must_change_password=False,
        is_active=is_active,
    )


def _enquiry(client, **overrides):
    payload = {
        "name": "Ama Mensah",
        "email": "ama@example.test",
        "phone": "+233 20 000 0000",
        "message": "Which class would my daughter join?",
        "website": "",
    }
    payload.update(overrides)
    return client.post(reverse("public:contact"), payload)


class EnquiryNotificationTest(TestCase):
    """One bell entry per active admin, pointing at the enquiry itself."""

    def setUp(self):
        cache.clear()
        self.admin_a = _user("admin", "bell_admin_a")
        self.admin_b = _user("admin", "bell_admin_b")
        self.inactive_admin = _user("admin", "bell_admin_off", is_active=False)
        self.director = _user("director", "bell_director")
        self.mail = patch(
            "apps.public.views.ContactView.mail_to_school", autospec=True
        )
        self.mail.start()
        self.addCleanup(self.mail.stop)

    def test_every_active_admin_is_notified_and_nobody_else(self):
        response = _enquiry(self.client)
        self.assertEqual(response.status_code, 302)

        enquiry = ContactMessage.objects.get()
        notifications = Notification.objects.filter(category="enquiry")
        self.assertCountEqual(
            notifications.values_list("recipient_id", flat=True),
            [self.admin_a.pk, self.admin_b.pk],
        )

        notification = notifications.first()
        self.assertEqual(notification.title, "New website enquiry")
        self.assertIn("Ama Mensah", notification.message)
        self.assertIn("ama@example.test", notification.message)
        self.assertEqual(
            notification.link,
            reverse("public_admin:enquiry_detail", args=[enquiry.pk]),
        )
        self.assertFalse(notification.is_read)

    def test_the_visitor_is_told_saved_even_when_notifying_fails(self):
        with patch(
            "apps.core.notifications.notify_many",
            side_effect=RuntimeError("db hiccup"),
        ):
            response = _enquiry(self.client)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContactMessage.objects.count(), 1)
        self.assertFalse(Notification.objects.exists())

    def test_a_honeypot_submission_saves_and_notifies_nothing(self):
        response = _enquiry(self.client, website="https://spam.example")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ContactMessage.objects.exists())
        self.assertFalse(Notification.objects.exists())


class NotificationViewTest(TestCase):
    """The list, opening one, and clearing the badge."""

    def setUp(self):
        self.user = _user("admin", "bell_owner")
        self.other = _user("teacher", "bell_other")

    def _notification(self, recipient, *, link="", is_read=False, title="Hello"):
        return Notification.objects.create(
            recipient=recipient,
            title=title,
            message="Body text",
            link=link,
            category="feed",
            is_read=is_read,
        )

    def test_the_list_shows_only_your_own_notifications(self):
        self._notification(self.user, title="Mine to read")
        self._notification(self.other, title="Someone else's")
        self.client.force_login(self.user)

        response = self.client.get(reverse("core_notifications:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mine to read")
        self.assertNotContains(response, "Someone else")

    def test_opening_marks_it_read_and_follows_its_link(self):
        notification = self._notification(self.user, link="/website/enquiries/7/")
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("core_notifications:open", args=[notification.pk])
        )

        self.assertRedirects(
            response, "/website/enquiries/7/", fetch_redirect_response=False
        )
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_opening_without_a_link_returns_to_the_list(self):
        notification = self._notification(self.user)
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("core_notifications:open", args=[notification.pk])
        )

        self.assertRedirects(response, reverse("core_notifications:list"))
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_another_users_notification_does_not_exist_for_you(self):
        notification = self._notification(self.other)
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("core_notifications:open", args=[notification.pk])
        )

        self.assertEqual(response.status_code, 404)
        notification.refresh_from_db()
        self.assertFalse(notification.is_read)

    def test_mark_all_read_clears_only_your_own(self):
        mine_unread = self._notification(self.user)
        mine_read = self._notification(self.user, is_read=True)
        theirs = self._notification(self.other)
        self.client.force_login(self.user)

        response = self.client.post(reverse("core_notifications:mark_all_read"))

        self.assertRedirects(response, reverse("core_notifications:list"))
        mine_unread.refresh_from_db()
        mine_read.refresh_from_db()
        theirs.refresh_from_db()
        self.assertTrue(mine_unread.is_read)
        self.assertTrue(mine_read.is_read)
        self.assertFalse(theirs.is_read)

    def test_mark_all_read_refuses_get(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core_notifications:mark_all_read"))
        self.assertEqual(response.status_code, 405)

    def test_the_navbar_bell_carries_the_unread_count(self):
        self._notification(self.user)
        self._notification(self.user)
        self._notification(self.user, is_read=True)
        self.client.force_login(self.user)

        response = self.client.get(reverse("core_notifications:list"))

        self.assertEqual(response.context["unread_notification_count"], 2)
        self.assertContains(response, 'class="navbar-bell__badge"')
        self.assertContains(response, ">2</span>")

    def test_an_anonymous_visitor_is_sent_to_sign_in(self):
        response = self.client.get(reverse("core_notifications:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse(settings.LOGIN_URL), response["Location"])
