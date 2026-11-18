"""Model-level guarantees for Module 3: routing, retention and dedupe.

No screens or services are built yet. These tests pin the invariants the later
checkpoints rely on: a handoff rule must have exactly one destination, the
safeguarding route cannot be deleted or switched off, reminders cannot be sent
twice, and deleting a conversation removes the personal text with it.
"""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from apps.assistant.models import (
    AnswerFeedback,
    AssistantSettings,
    CallbackRequest,
    CannedReply,
    ChatConversation,
    ChatMessage,
    Handoff,
    HandoffEvent,
    HandoffNote,
    HandoffPriority,
    HandoffRule,
    HandoffTeam,
    SuggestedQuestion,
    within_time_window,
)
from apps.dashboards.tests.fixtures import make_user


class HandoffModelTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="m3_admin")
        cls.director = make_user("director", username="m3_director")
        cls.teacher = make_user("teacher", username="m3_teacher")
        cls.finance_team = HandoffTeam.objects.create(name="Finance Office")
        cls.office_team = HandoffTeam.objects.create(name="School Office")

    def rule(self, **overrides) -> HandoffRule:
        data = {
            "name": "Fee dispute",
            "priority": HandoffPriority.URGENT,
            "topics": "|fee_dispute|",
            "destination_type": HandoffRule.DestinationType.TEAM,
            "destination_team": self.finance_team,
        }
        data.update(overrides)
        rule = HandoffRule(**data)
        rule.full_clean()
        rule.save()
        return rule

    def required_rule(self, **overrides) -> HandoffRule:
        data = {
            "name": "Safeguarding concern",
            "priority": HandoffPriority.CRITICAL,
            "topics": "|bullying|",
            "is_required": True,
            "builtin_code": "safeguarding",
            "destination_type": HandoffRule.DestinationType.ROLE,
            "destination_role": "admin",
        }
        data.update(overrides)
        rule = HandoffRule(**data)
        rule.full_clean()
        rule.save()
        return rule

    def conversation(self, **overrides) -> ChatConversation:
        data = {"visitor_key": "a" * 64}
        data.update(overrides)
        return ChatConversation.objects.create(**data)

    def handoff(self, conv=None, rule=None) -> Handoff:
        return Handoff.objects.create(
            conversation=conv or self.conversation(),
            rule=rule or self.rule(),
            priority=HandoffPriority.URGENT,
        )


class SettingsDefaultsTests(HandoffModelTestCase):
    def test_widget_and_automation_switches_are_off_by_default(self):
        row = AssistantSettings.load()
        self.assertFalse(row.widget_enabled)

    def test_protective_checks_default_on(self):
        row = AssistantSettings.load()
        self.assertTrue(row.widget_feedback_enabled)
        self.assertTrue(row.widget_pow_enabled)

    def test_approved_retention_defaults(self):
        row = AssistantSettings.load()
        self.assertEqual(row.conversation_retention_days, 90)
        self.assertEqual(row.handoff_retention_months, 12)
        self.assertEqual(row.callback_retention_days, 30)
        self.assertEqual(row.feedback_reason_retention_days, 90)

    def test_service_level_defaults(self):
        row = AssistantSettings.load()
        self.assertEqual(row.sla_normal_minutes, 480)
        self.assertEqual(row.sla_urgent_minutes, 240)
        self.assertEqual(row.sla_critical_minutes, 15)
        self.assertEqual(row.sla_reminder_percent, 75)


class TeamTests(HandoffModelTestCase):
    def test_a_slug_is_made_and_kept_unique(self):
        second = HandoffTeam.objects.create(name="Finance Office 2")
        self.assertEqual(second.slug, "finance-office-2")
        self.assertEqual(self.finance_team.slug, "finance-office")


class RuleDestinationTests(HandoffModelTestCase):
    def test_a_team_destination_is_valid(self):
        rule = self.rule()
        self.assertEqual(rule.destination_label, "Finance Office")
        self.assertEqual(rule.channel_codes, ["in_app"])

    def test_a_destination_is_required(self):
        with self.assertRaises(ValidationError) as caught:
            HandoffRule(
                name="No destination",
                topics="|medical|",
                destination_type=HandoffRule.DestinationType.TEAM,
            ).full_clean()
        self.assertIn("destination_type", caught.exception.message_dict)

    def test_two_destinations_are_refused(self):
        with self.assertRaises(ValidationError) as caught:
            HandoffRule(
                name="Two destinations",
                topics="|medical|",
                destination_type=HandoffRule.DestinationType.TEAM,
                destination_team=self.finance_team,
                destination_role="admin",
            ).full_clean()
        self.assertIn("destination_type", caught.exception.message_dict)

    def test_a_role_destination_must_be_a_staff_role(self):
        with self.assertRaises(ValidationError) as caught:
            HandoffRule(
                name="Role destination",
                topics="|medical|",
                destination_type=HandoffRule.DestinationType.ROLE,
                destination_role="student",
            ).full_clean()
        self.assertIn("destination_role", caught.exception.message_dict)

    def test_a_person_destination_is_valid(self):
        rule = self.rule(
            name="Director route",
            destination_type=HandoffRule.DestinationType.PERSON,
            destination_team=None,
            destination_user=self.director,
        )
        self.assertEqual(
            rule.destination_label,
            self.director.get_full_name() or self.director.username,
        )


class RuleTriggerTests(HandoffModelTestCase):
    def test_unknown_topics_are_refused(self):
        with self.assertRaises(ValidationError) as caught:
            HandoffRule(
                name="Odd topic",
                topics="|teleportation|",
                destination_type=HandoffRule.DestinationType.TEAM,
                destination_team=self.finance_team,
            ).full_clean()
        self.assertIn("topics", caught.exception.message_dict)

    def test_the_keyword_signal_needs_keywords(self):
        with self.assertRaises(ValidationError) as caught:
            HandoffRule(
                name="Keyword signal only",
                signals="|keyword|",
                destination_type=HandoffRule.DestinationType.TEAM,
                destination_team=self.finance_team,
            ).full_clean()
        self.assertIn("keywords", caught.exception.message_dict)

    def test_a_rule_with_no_trigger_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            HandoffRule(
                name="Always fires",
                destination_type=HandoffRule.DestinationType.TEAM,
                destination_team=self.finance_team,
            ).full_clean()
        self.assertIn("topics", caught.exception.message_dict)

    def test_specific_roles_are_canonicalised(self):
        rule = self.rule(name="Teacher route", specific_roles="teacher|director")
        self.assertEqual(rule.specific_roles, "|director|teacher|")

    def test_user_types_match_by_director_and_role(self):
        director_rule = self.rule(name="Director route", any_director=True)
        self.assertTrue(director_rule.user_matches(self.director))
        self.assertFalse(director_rule.user_matches(self.teacher))

        teacher_rule = self.rule(name="Teacher route", specific_roles="teacher")
        self.assertTrue(teacher_rule.user_matches(self.teacher))
        self.assertFalse(teacher_rule.user_matches(self.director))

    def test_a_named_account_matches(self):
        rule = self.rule(name="Named account route", any_director=True)
        rule.specific_users.add(self.teacher)
        self.assertTrue(rule.user_matches(self.teacher))
        self.assertFalse(rule.user_matches(self.admin))


class RequiredRuleTests(HandoffModelTestCase):
    def test_a_required_rule_cannot_be_switched_off(self):
        with self.assertRaises(ValidationError) as caught:
            HandoffRule(
                name="Off safeguarding",
                topics="|bullying|",
                is_required=True,
                builtin_code="safeguarding-off",
                enabled=False,
                destination_type=HandoffRule.DestinationType.ROLE,
                destination_role="admin",
            ).full_clean()
        self.assertIn("enabled", caught.exception.message_dict)

    def test_a_required_rule_needs_a_code(self):
        with self.assertRaises(ValidationError) as caught:
            HandoffRule(
                name="No code",
                topics="|bullying|",
                is_required=True,
                destination_type=HandoffRule.DestinationType.ROLE,
                destination_role="admin",
            ).full_clean()
        self.assertIn("builtin_code", caught.exception.message_dict)

    def test_a_plain_rule_cannot_carry_a_builtin_code(self):
        with self.assertRaises(ValidationError) as caught:
            HandoffRule(
                name="Pretender",
                topics="|bullying|",
                builtin_code="pretender",
                destination_type=HandoffRule.DestinationType.ROLE,
                destination_role="admin",
            ).full_clean()
        self.assertIn("builtin_code", caught.exception.message_dict)

    def test_the_required_rule_cannot_be_deleted(self):
        rule = self.required_rule()
        with self.assertRaises(ValidationError):
            rule.delete()
        self.assertTrue(HandoffRule.objects.filter(pk=rule.pk).exists())

    def test_a_queryset_containing_the_required_rule_deletes_nothing(self):
        self.required_rule()
        normal = self.rule(name="Normal rule")
        with self.assertRaises(ValidationError):
            HandoffRule.objects.all().delete()
        self.assertTrue(HandoffRule.objects.filter(pk=normal.pk).exists())

    def test_a_normal_rule_deletes(self):
        rule = self.rule(name="Temporary rule")
        rule.delete()
        self.assertFalse(HandoffRule.objects.filter(pk=rule.pk).exists())

    def test_a_builtin_code_is_unique(self):
        self.required_rule()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                # Bypasses full_clean deliberately: this is the database's own guard.
                HandoffRule.objects.create(
                    name="Second safeguarding",
                    topics="|bullying|",
                    is_required=True,
                    builtin_code="safeguarding",
                    destination_type=HandoffRule.DestinationType.ROLE,
                    destination_role="admin",
                )


class RuleHoursAndTargetsTests(HandoffModelTestCase):
    def test_normal_working_hours(self):
        rule = self.rule(
            working_hours_start=datetime.time(8, 0),
            working_hours_end=datetime.time(17, 0),
        )
        self.assertTrue(rule.within_working_hours(datetime.datetime(2026, 11, 18, 9, 0)))
        self.assertFalse(rule.within_working_hours(datetime.datetime(2026, 11, 18, 20, 0)))

    def test_a_window_across_midnight_is_continuous(self):
        self.assertTrue(
            within_time_window(
                datetime.time(22, 0),
                datetime.time(6, 0),
                datetime.datetime(2026, 11, 18, 23, 30),
            )
        )
        self.assertTrue(
            within_time_window(
                datetime.time(22, 0),
                datetime.time(6, 0),
                datetime.datetime(2026, 11, 18, 5, 0),
            )
        )
        self.assertFalse(
            within_time_window(
                datetime.time(22, 0),
                datetime.time(6, 0),
                datetime.datetime(2026, 11, 18, 12, 0),
            )
        )

    def test_equal_start_and_end_means_always_outside(self):
        self.assertFalse(
            within_time_window(
                datetime.time(9, 0),
                datetime.time(9, 0),
                datetime.datetime(2026, 11, 18, 9, 0),
            )
        )

    def test_the_priority_target_applies_when_the_rule_has_none(self):
        settings_row = AssistantSettings(sla_urgent_minutes=123, sla_normal_minutes=480)
        rule = self.rule()
        self.assertEqual(rule.response_target_for(settings_row), 123)

    def test_a_rule_can_override_the_target(self):
        rule = self.rule(response_target_minutes=30)
        self.assertEqual(rule.response_target_for(), 30)


class ConversationStorageTests(HandoffModelTestCase):
    def test_deleting_a_conversation_removes_its_text_and_handoff(self):
        conv = self.conversation()
        message = ChatMessage.objects.create(
            conversation=conv, role=ChatMessage.Role.VISITOR, body="Hello"
        )
        handoff = Handoff.objects.create(
            conversation=conv, rule=self.rule(), priority=HandoffPriority.URGENT
        )
        HandoffNote.objects.create(handoff=handoff, body="Called the parent.")
        HandoffEvent.objects.create(handoff=handoff, kind=HandoffEvent.Kind.CREATED)

        conv.delete()

        self.assertFalse(ChatMessage.objects.filter(pk=message.pk).exists())
        self.assertFalse(Handoff.objects.filter(pk=handoff.pk).exists())
        self.assertFalse(HandoffNote.objects.exists())
        self.assertFalse(HandoffEvent.objects.exists())

    def test_a_rule_used_by_a_handoff_is_protected_from_deletion(self):
        rule = self.rule()
        self.handoff(rule=rule)
        with self.assertRaises(ProtectedError):
            rule.delete()

    def test_a_handoff_knows_when_it_is_late(self):
        handoff = self.handoff()
        handoff.sla_due_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            minutes=5
        )
        self.assertTrue(handoff.is_overdue)
        handoff.status = Handoff.Status.RESOLVED
        self.assertFalse(handoff.is_overdue)
        self.assertTrue(handoff.is_critical is False)


class EventDedupeTests(HandoffModelTestCase):
    def test_blank_keys_are_always_kept(self):
        handoff = self.handoff()
        HandoffEvent.objects.create(handoff=handoff, kind=HandoffEvent.Kind.ASSIGNED)
        HandoffEvent.objects.create(handoff=handoff, kind=HandoffEvent.Kind.ASSIGNED)
        self.assertEqual(handoff.events.count(), 2)

    def test_the_same_reminder_cannot_be_recorded_twice(self):
        handoff = self.handoff()
        HandoffEvent.objects.create(
            handoff=handoff,
            kind=HandoffEvent.Kind.SLA_REMINDER,
            dedupe_key="sla:75",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HandoffEvent.objects.create(
                    handoff=handoff,
                    kind=HandoffEvent.Kind.SLA_REMINDER,
                    dedupe_key="sla:75",
                )


class FeedbackAndCallbackTests(HandoffModelTestCase):
    def setUp(self):
        self.conv = self.conversation()
        self.message = ChatMessage.objects.create(
            conversation=self.conv, role=ChatMessage.Role.ASSISTANT, body="An answer"
        )

    def test_a_rating_needs_a_choice(self):
        row = AnswerFeedback(message=self.message, kind=AnswerFeedback.Kind.RATING)
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_a_report_does_not_carry_a_rating(self):
        row = AnswerFeedback(
            message=self.message,
            kind=AnswerFeedback.Kind.REPORT,
            rating=AnswerFeedback.Rating.HELPFUL,
        )
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_one_rating_and_one_report_are_allowed(self):
        AnswerFeedback.objects.create(
            message=self.message,
            kind=AnswerFeedback.Kind.RATING,
            rating=AnswerFeedback.Rating.NOT_HELPFUL,
        )
        AnswerFeedback.objects.create(
            message=self.message, kind=AnswerFeedback.Kind.REPORT, reason="Wrong fee"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AnswerFeedback.objects.create(
                    message=self.message,
                    kind=AnswerFeedback.Kind.RATING,
                    rating=AnswerFeedback.Rating.HELPFUL,
                )

    def test_a_callback_needs_a_way_to_reply(self):
        row = CallbackRequest(name="A parent", purpose="To call back about fees.")
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_a_callback_with_an_email_is_valid(self):
        row = CallbackRequest(
            name="A parent",
            email="parent@example.com",
            purpose="To call back about fees.",
        )
        row.full_clean()


class NotesAndCannedRepliesTests(HandoffModelTestCase):
    def test_a_blank_note_is_refused(self):
        note = HandoffNote(handoff=self.handoff(), body="   ")
        with self.assertRaises(ValidationError):
            note.full_clean()

    def test_a_blank_canned_reply_is_refused(self):
        reply = CannedReply(title="Empty", body="  ")
        with self.assertRaises(ValidationError):
            reply.full_clean()

    def test_canned_replies_follow_their_order(self):
        second = CannedReply.objects.create(title="Second", body="B", display_order=2)
        first = CannedReply.objects.create(title="First", body="A", display_order=1)
        self.assertEqual(list(CannedReply.objects.all()), [first, second])


class SuggestedQuestionTests(HandoffModelTestCase):
    def test_the_prompt_falls_back_to_the_english_text(self):
        question = SuggestedQuestion.objects.create(text_en="Fee structure")
        self.assertEqual(question.prompt_text, "Fee structure")

    def test_an_explicit_prompt_is_used(self):
        question = SuggestedQuestion.objects.create(
            text_en="Talk to the school", prompt="I would like to speak to a person"
        )
        self.assertEqual(question.prompt_text, "I would like to speak to a person")
