"""
Tests for the human inbox (CP5) and conversation management (CP6).

Covers:
  * Inbox scoping: staff only see their own handoffs; admin sees all; director read-only
  * Assign, note, staff reply, resolve
  * SLA escalation: reminder sent, then escalated to admin; critical never dropped
  * Conversation export and deletion
  * Mandatory safeguarding rule cannot be deleted via queryset delete
"""

from __future__ import annotations

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.assistant.handoff.routing import create_handoff
from apps.assistant.handoff.services import (
    add_note,
    assign,
    escalate_overdue,
    resolve,
    staff_reply,
)
from apps.assistant.models import (
    CannedReply,
    ChatConversation,
    ChatMessage,
    Handoff,
    HandoffEvent,
    HandoffNote,
    HandoffPriority,
    HandoffRule,
    HandoffTeam,
)
from apps.dashboards.tests.fixtures import make_user


def _rule(**overrides) -> HandoffRule:
    data = {
        "name": f"inbox-rule-{HandoffRule.objects.count()}",
        "priority": HandoffPriority.NORMAL,
        "topics": "|fee_dispute|",
        "destination_type": HandoffRule.DestinationType.ROLE,
        "destination_role": "admin",
        "response_target_minutes": 60,
    }
    data.update(overrides)
    r = HandoffRule(**data)
    r.full_clean()
    r.save()
    return r


def _conv(key: str = None) -> ChatConversation:
    import hashlib
    k = key or hashlib.sha256(str(ChatConversation.objects.count()).encode()).hexdigest()
    return ChatConversation.objects.create(visitor_key=k)


def _handoff(rule=None, priority=HandoffPriority.NORMAL, **overrides) -> Handoff:
    conv = _conv()
    r = rule or _rule()
    h = Handoff.objects.create(
        conversation=conv,
        rule=r,
        priority=priority,
        sla_due_at=timezone.now() + datetime.timedelta(hours=1),
        **overrides,
    )
    return h


class InboxScopingTests(TestCase):
    """Staff see only their handoffs; admin/director see all."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="ib_admin")
        cls.director = make_user("director", username="ib_director")
        cls.teacher = make_user("teacher", username="ib_teacher")
        cls.finance = make_user("finance", username="ib_finance")

        cls.team = HandoffTeam.objects.create(name="Finance Team")
        cls.team.members.add(cls.finance)

        rule = _rule()
        cls.teacher_handoff = _handoff(rule=rule, assigned_user=cls.teacher)
        cls.team_handoff = _handoff(rule=rule, assigned_team=cls.team)
        cls.unassigned = _handoff(rule=rule, destination_role="admin")

    def _ids(self, user):
        from apps.assistant.handoff.services import inbox_for_user
        return set(inbox_for_user(user).values_list("pk", flat=True))

    def test_admin_sees_all_open_handoffs(self):
        ids = self._ids(self.admin)
        self.assertIn(self.teacher_handoff.pk, ids)
        self.assertIn(self.team_handoff.pk, ids)
        self.assertIn(self.unassigned.pk, ids)

    def test_director_sees_all_open_handoffs(self):
        ids = self._ids(self.director)
        self.assertIn(self.teacher_handoff.pk, ids)
        self.assertIn(self.team_handoff.pk, ids)

    def test_teacher_sees_only_own_handoff(self):
        ids = self._ids(self.teacher)
        self.assertIn(self.teacher_handoff.pk, ids)
        self.assertNotIn(self.team_handoff.pk, ids)

    def test_finance_sees_team_handoff_via_membership(self):
        ids = self._ids(self.finance)
        self.assertIn(self.team_handoff.pk, ids)
        self.assertNotIn(self.teacher_handoff.pk, ids)


class AssignTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="as_admin")
        self.teacher = make_user("teacher", username="as_teacher")
        self.handoff = _handoff()

    def test_assign_to_user_changes_status(self):
        h = assign(self.handoff, user=self.teacher, actor=self.admin)
        self.assertEqual(h.status, Handoff.Status.ASSIGNED)
        self.assertEqual(h.assigned_user, self.teacher)

    def test_assign_creates_event(self):
        assign(self.handoff, user=self.teacher, actor=self.admin)
        self.assertTrue(
            HandoffEvent.objects.filter(
                handoff=self.handoff, kind=HandoffEvent.Kind.ASSIGNED
            ).exists()
        )

    def test_assign_to_team(self):
        team = HandoffTeam.objects.create(name="Assign Team")
        h = assign(self.handoff, team=team, actor=self.admin)
        self.assertEqual(h.assigned_team, team)


class NoteTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="nt_admin")
        self.handoff = _handoff()

    def test_add_note_saves_body(self):
        note = add_note(self.handoff, body="Check fee receipt", actor=self.admin)
        self.assertIsInstance(note, HandoffNote)
        self.assertEqual(note.body, "Check fee receipt")
        self.assertEqual(note.author, self.admin)

    def test_blank_note_raises(self):
        with self.assertRaises(ValidationError):
            add_note(self.handoff, body="   ", actor=self.admin)


class StaffReplyTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="sr_admin")
        self.handoff = _handoff()

    def test_reply_stores_staff_message(self):
        msg = staff_reply(self.handoff, body="Thank you for contacting us", actor=self.admin)
        self.assertEqual(msg.role, ChatMessage.Role.STAFF)
        self.assertEqual(msg.staff_user, self.admin)
        self.assertIn("Thank you", msg.body)

    def test_reply_advances_status_to_in_progress(self):
        staff_reply(self.handoff, body="Hello", actor=self.admin)
        self.handoff.refresh_from_db()
        self.assertEqual(self.handoff.status, Handoff.Status.IN_PROGRESS)

    def test_first_response_at_set_once(self):
        staff_reply(self.handoff, body="First", actor=self.admin)
        self.handoff.refresh_from_db()
        first_time = self.handoff.first_response_at
        staff_reply(self.handoff, body="Second", actor=self.admin)
        self.handoff.refresh_from_db()
        self.assertEqual(self.handoff.first_response_at, first_time)

    def test_blank_reply_raises(self):
        with self.assertRaises(ValidationError):
            staff_reply(self.handoff, body="  ", actor=self.admin)


class ResolveTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="res_admin")
        self.handoff = _handoff()

    def test_resolve_sets_status_and_resolved_at(self):
        h = resolve(self.handoff, actor=self.admin, note="All sorted")
        self.assertEqual(h.status, Handoff.Status.RESOLVED)
        self.assertIsNotNone(h.resolved_at)
        self.assertEqual(h.resolution_note, "All sorted")

    def test_resolve_creates_event(self):
        resolve(self.handoff, actor=self.admin)
        self.assertTrue(
            HandoffEvent.objects.filter(
                handoff=self.handoff, kind=HandoffEvent.Kind.RESOLVED
            ).exists()
        )


class SLAEscalationTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="sla_admin")

    def _overdue(self, priority=HandoffPriority.NORMAL):
        h = _handoff(priority=priority)
        h.sla_due_at = timezone.now() - datetime.timedelta(minutes=30)
        h.save(update_fields=["sla_due_at", "updated_at"])
        return h

    def test_first_overdue_check_sends_reminder(self):
        h = self._overdue()
        escalated = escalate_overdue(h)
        self.assertFalse(escalated)  # first call: reminder, not escalation
        self.assertTrue(
            HandoffEvent.objects.filter(
                handoff=h, kind=HandoffEvent.Kind.SLA_REMINDER
            ).exists()
        )

    def test_second_overdue_check_escalates(self):
        h = self._overdue()
        escalate_overdue(h)   # first: reminder
        escalated = escalate_overdue(h)  # second: escalation
        self.assertTrue(escalated)
        self.assertTrue(
            HandoffEvent.objects.filter(
                handoff=h, kind=HandoffEvent.Kind.SLA_ESCALATED
            ).exists()
        )

    def test_resolved_handoff_is_not_escalated(self):
        h = self._overdue()
        h.status = Handoff.Status.RESOLVED
        h.save(update_fields=["status", "updated_at"])
        result = escalate_overdue(h)
        self.assertFalse(result)
        self.assertFalse(
            HandoffEvent.objects.filter(handoff=h, kind=HandoffEvent.Kind.SLA_ESCALATED).exists()
        )

    def test_critical_handoff_is_never_silently_dropped(self):
        """Escalate is called; it records an event even if no admin exists."""
        h = self._overdue(priority=HandoffPriority.CRITICAL)
        escalate_overdue(h)   # reminder
        result = escalate_overdue(h)  # escalation
        self.assertTrue(result)
        self.assertTrue(
            HandoffEvent.objects.filter(
                handoff=h, kind=HandoffEvent.Kind.SLA_ESCALATED
            ).exists()
        )

    def test_no_duplicate_reminder(self):
        h = self._overdue()
        escalate_overdue(h)
        escalate_overdue(h)  # second call escalates, doesn't add another reminder
        reminder_count = HandoffEvent.objects.filter(
            handoff=h, kind=HandoffEvent.Kind.SLA_REMINDER
        ).count()
        self.assertEqual(reminder_count, 1)


class InboxViewScopingTests(TestCase):
    """HTTP-level: director is read-only, staff cannot see another's handoff."""

    def setUp(self):
        self.admin = make_user("admin", username="ivs_admin")
        self.director = make_user("director", username="ivs_director")
        self.teacher = make_user("teacher", username="ivs_teacher")
        self.other_teacher = make_user("teacher", username="ivs_other")
        rule = _rule()
        self.own = _handoff(rule=rule, assigned_user=self.teacher)
        self.other = _handoff(rule=rule, assigned_user=self.other_teacher)

    def test_inbox_requires_login(self):
        resp = self.client.get(reverse("assistant:inbox"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"].lower())

    def test_admin_can_access_inbox(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("assistant:inbox"))
        self.assertEqual(resp.status_code, 200)

    def test_director_can_access_inbox_read_only(self):
        self.client.force_login(self.director)
        resp = self.client.get(reverse("assistant:inbox"))
        self.assertEqual(resp.status_code, 200)

    def test_director_cannot_resolve_handoff(self):
        self.client.force_login(self.director)
        resp = self.client.post(
            reverse("assistant:inbox_detail", kwargs={"pk": self.own.pk}),
            {"action": "resolve", "note": "Done"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_teacher_cannot_access_another_teachers_handoff(self):
        self.client.force_login(self.teacher)
        resp = self.client.get(
            reverse("assistant:inbox_detail", kwargs={"pk": self.other.pk})
        )
        self.assertEqual(resp.status_code, 403)

    def test_visitor_cannot_access_inbox(self):
        resp = self.client.get(reverse("assistant:inbox"))
        self.assertEqual(resp.status_code, 302)


class ConversationDeletionTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="del_admin")
        self.teacher = make_user("teacher", username="del_teacher")
        self.conv = _conv()
        ChatMessage.objects.create(
            conversation=self.conv, role=ChatMessage.Role.VISITOR, body="Hello"
        )

    def test_admin_can_delete_conversation(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("assistant:conversation_delete", kwargs={"pk": self.conv.pk}),
            {"reason": "GDPR request"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ChatConversation.objects.filter(pk=self.conv.pk).exists())

    def test_teacher_cannot_delete_conversation(self):
        self.client.force_login(self.teacher)
        resp = self.client.post(
            reverse("assistant:conversation_delete", kwargs={"pk": self.conv.pk}),
            {"reason": "test"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_messages_cascade_delete_with_conversation(self):
        msg_ids = list(ChatMessage.objects.filter(conversation=self.conv).values_list("pk", flat=True))
        self.conv.delete()
        for mid in msg_ids:
            self.assertFalse(ChatMessage.objects.filter(pk=mid).exists())


class ConversationExportTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="exp_admin")
        self.conv = _conv()
        ChatMessage.objects.create(
            conversation=self.conv, role=ChatMessage.Role.VISITOR, body="Hello export"
        )

    def test_admin_can_export_csv(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse("assistant:conversation_export", kwargs={"pk": self.conv.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("Hello export", resp.content.decode())

    def test_export_has_correct_headers(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse("assistant:conversation_export", kwargs={"pk": self.conv.pk})
        )
        first_line = resp.content.decode().split("\r\n")[0]
        self.assertIn("timestamp", first_line)
        self.assertIn("role", first_line)
        self.assertIn("body", first_line)


class MandatoryRuleProtectionTests(TestCase):
    """Required rules cannot be bulk-deleted via queryset either."""

    def test_queryset_delete_blocked_when_required_rule_included(self):
        required = HandoffRule(
            name="Required rule",
            topics="|bullying|",
            is_required=True,
            builtin_code="qs_delete_test",
            priority=HandoffPriority.CRITICAL,
            destination_type=HandoffRule.DestinationType.ROLE,
            destination_role="admin",
        )
        required.full_clean()
        required.save()
        with self.assertRaises(ValidationError):
            HandoffRule.objects.filter(pk=required.pk).delete()

    def test_seed_does_not_overwrite_existing_required_rule(self):
        from apps.assistant.handoff.seed import seed_starter_rules
        # Seed once to create all rules
        seed_starter_rules()
        required = HandoffRule.objects.filter(
            builtin_code="safeguarding_critical"
        ).first()
        # Seed again — required rule skipped
        counts = seed_starter_rules()
        self.assertGreater(counts["skipped"], 0)
        # Required rule untouched
        after = HandoffRule.objects.filter(builtin_code="safeguarding_critical").first()
        self.assertEqual(required.pk, after.pk)
