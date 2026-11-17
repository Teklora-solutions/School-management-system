"""
Tests for the member map, member profiles and direct messages.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.community.models import (
    CommunityProfile,
    DirectConversation,
    DirectMessage,
)
from apps.community.selectors import direct_inbox, map_members
from apps.community.services import (
    clear_member_location,
    get_or_create_direct_conversation,
    grant_consent,
    mark_direct_conversation_read,
    send_direct_message,
    set_member_location,
    unread_direct_messages,
)
from apps.core.models import Notification, SchoolSettings

from .fixtures import disable_community, enable_community, make_user


class MapBase(TestCase):
    def setUp(self):
        enable_community()
        self.admin = make_user("admin", username="map_admin")
        self.teacher = make_user("teacher", username="map_teacher")
        self.parent = make_user("parent", username="map_parent")
        self.student = make_user("student", username="map_student")
        for user in (self.admin, self.teacher, self.parent):
            grant_consent(user)

    def tearDown(self):
        SchoolSettings.objects.filter(pk=1).update(community_enabled=False)


# ─── Location service ────────────────────────────────────────────────────────

class LocationServiceTests(MapBase):
    def test_set_location_copies_preset_coordinates(self):
        profile = set_member_location(
            actor=self.teacher, place_label="Nairobi, Kenya", shared=True
        )
        self.assertEqual(profile.location_label, "Nairobi, Kenya")
        self.assertEqual(profile.location_country, "Kenya")
        self.assertAlmostEqual(float(profile.latitude), 1.2921, places=3)
        self.assertAlmostEqual(float(profile.longitude), 36.8219, places=3)
        self.assertTrue(profile.location_shared)
        self.assertTrue(profile.is_on_map)

    def test_set_location_rejects_unknown_place(self):
        fresh = make_user("teacher", username="map_unknown_place")
        with self.assertRaises(ValueError):
            set_member_location(
                actor=fresh, place_label="Atlantis", shared=True
            )
        self.assertFalse(
            CommunityProfile.objects.filter(user=fresh).exists()
        )

    def test_hidden_location_is_saved_but_not_on_map(self):
        profile = set_member_location(
            actor=self.teacher, place_label="Mombasa, Kenya", shared=False
        )
        self.assertTrue(profile.has_location)
        self.assertFalse(profile.is_on_map)

    def test_clear_location_removes_pin(self):
        set_member_location(
            actor=self.teacher, place_label="Kisumu, Kenya", shared=True
        )
        clear_member_location(actor=self.teacher)
        profile = CommunityProfile.objects.get(user=self.teacher)
        self.assertFalse(profile.has_location)
        self.assertFalse(profile.location_shared)
        self.assertIsNone(profile.latitude)


# ─── Map selector ───────────────────────────────────────────────────────────

class MapSelectorTests(MapBase):
    def test_map_lists_only_shared_non_students(self):
        set_member_location(
            actor=self.teacher, place_label="Nairobi, Kenya", shared=True
        )
        set_member_location(
            actor=self.parent, place_label="Mombasa, Kenya", shared=False
        )
        set_member_location(
            actor=self.student, place_label="Kisumu, Kenya", shared=True
        )

        rows = map_members(self.admin)
        names = {row["name"] for row in rows}
        self.assertIn("Teacher User", names)
        self.assertNotIn("Parent User", names)
        self.assertNotIn("Student User", names)

    def test_anonymous_cannot_read_map_data(self):
        set_member_location(
            actor=self.teacher, place_label="Nairobi, Kenya", shared=True
        )
        self.assertEqual(map_members(None), [])


# ─── Map page + location form ───────────────────────────────────────────────

class MapViewTests(MapBase):
    def test_map_page_renders_for_signed_in_member(self):
        set_member_location(
            actor=self.teacher, place_label="Nairobi, Kenya", shared=True
        )
        self.client.force_login(self.parent)
        response = self.client.get(reverse("community:map"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Where our community calls home")
        self.assertContains(response, "Teacher User")

    def test_map_page_renders_sign_in_gate_for_visitors(self):
        response = self.client.get(reverse("community:map"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Members-only map")
        self.assertNotContains(response, "cmtyMapMembers")

    def test_location_update_requires_consent(self):
        fresh = make_user("teacher", username="map_noconsent")
        self.client.force_login(fresh)
        response = self.client.post(
            reverse("community:location_update"),
            {"place": "Nairobi, Kenya", "shared": "on"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("community:consent"), response["Location"])
        self.assertFalse(
            CommunityProfile.objects.filter(user=fresh).exists()
        )

    def test_location_update_saves_and_clears(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse("community:location_update"),
            {"place": "Nakuru, Kenya", "shared": "on"},
        )
        self.assertRedirects(response, reverse("community:map"))
        profile = CommunityProfile.objects.get(user=self.teacher)
        self.assertEqual(profile.location_label, "Nakuru, Kenya")
        self.assertTrue(profile.location_shared)

        response = self.client.post(
            reverse("community:location_update"), {"action": "clear"}
        )
        self.assertRedirects(response, reverse("community:map"))
        profile.refresh_from_db()
        self.assertFalse(profile.has_location)

    def test_location_update_rejects_unknown_place_without_writing(self):
        self.client.force_login(self.teacher)
        self.client.post(
            reverse("community:location_update"),
            {"place": "Nowhere", "shared": "on"},
        )
        profile = CommunityProfile.objects.get(user=self.teacher)
        self.assertFalse(profile.has_location)


# ─── Member profile ─────────────────────────────────────────────────────────

class MemberProfileViewTests(MapBase):
    def test_profile_renders_for_non_student(self):
        self.client.force_login(self.parent)
        response = self.client.get(
            reverse("community:member_profile", args=[self.teacher.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Teacher User")
        self.assertContains(response, "Message")

    def test_student_profile_is_404_for_other_members(self):
        self.client.force_login(self.teacher)
        response = self.client.get(
            reverse("community:member_profile", args=[self.student.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_student_can_view_own_profile(self):
        self.client.force_login(self.student)
        response = self.client.get(
            reverse("community:member_profile", args=[self.student.pk])
        )
        self.assertEqual(response.status_code, 200)


# ─── Direct messages ────────────────────────────────────────────────────────

class DirectConversationTests(MapBase):
    def test_pair_is_stored_once_in_id_order(self):
        first = get_or_create_direct_conversation(
            actor=self.teacher, other=self.parent
        )
        second = get_or_create_direct_conversation(
            actor=self.parent, other=self.teacher
        )
        self.assertEqual(first.pk, second.pk)
        self.assertLess(first.user_a_id, first.user_b_id)

    def test_students_cannot_message(self):
        with self.assertRaises(PermissionError):
            get_or_create_direct_conversation(
                actor=self.student, other=self.teacher
            )

    def test_send_message_notifies_and_sets_unread(self):
        conversation = get_or_create_direct_conversation(
            actor=self.teacher, other=self.parent
        )
        message = send_direct_message(
            actor=self.teacher, conversation=conversation, body="Hello there"
        )
        self.assertEqual(message.sender, self.teacher)
        self.assertEqual(unread_direct_messages(self.parent), 1)
        self.assertEqual(
            Notification.objects.filter(recipient=self.parent).count(), 1
        )
        conversation.refresh_from_db()
        self.assertEqual(conversation.last_message_at, message.created_at)

    def test_mark_read_clears_unread_for_recipient_only(self):
        conversation = get_or_create_direct_conversation(
            actor=self.teacher, other=self.parent
        )
        send_direct_message(
            actor=self.teacher, conversation=conversation, body="Hi"
        )
        mark_direct_conversation_read(
            conversation=conversation, user=self.parent
        )
        self.assertEqual(unread_direct_messages(self.parent), 0)
        self.assertEqual(unread_direct_messages(self.teacher), 0)

    def test_inbox_lists_conversation_with_snippet(self):
        conversation = get_or_create_direct_conversation(
            actor=self.teacher, other=self.parent
        )
        send_direct_message(
            actor=self.parent, conversation=conversation, body="Quick question"
        )
        rows = direct_inbox(self.teacher)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["other"], self.parent)
        self.assertEqual(rows[0]["last_body"], "Quick question")
        self.assertEqual(rows[0]["unread"], 1)

    def test_start_conversation_view_creates_and_redirects(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse("community:message_start", args=[self.parent.pk])
        )
        conversation = DirectConversation.objects.get()
        self.assertRedirects(
            response,
            reverse("community:message_thread", args=[conversation.pk]),
        )

    def test_thread_view_blocks_outsiders(self):
        conversation = get_or_create_direct_conversation(
            actor=self.teacher, other=self.parent
        )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("community:message_thread", args=[conversation.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_thread_post_sends_message(self):
        conversation = get_or_create_direct_conversation(
            actor=self.teacher, other=self.parent
        )
        self.client.force_login(self.parent)
        response = self.client.post(
            reverse("community:message_thread", args=[conversation.pk]),
            {"body": "Thanks for the reply"},
        )
        self.assertRedirects(
            response,
            reverse("community:message_thread", args=[conversation.pk]),
        )
        self.assertEqual(
            DirectMessage.objects.filter(conversation=conversation).count(), 1
        )

    def test_thread_marks_incoming_messages_read(self):
        conversation = get_or_create_direct_conversation(
            actor=self.teacher, other=self.parent
        )
        send_direct_message(
            actor=self.teacher, conversation=conversation, body="Please read"
        )
        self.client.force_login(self.parent)
        self.client.get(
            reverse("community:message_thread", args=[conversation.pk])
        )
        self.assertEqual(unread_direct_messages(self.parent), 0)
        self.assertIsNotNone(
            DirectMessage.objects.get().read_at
        )
