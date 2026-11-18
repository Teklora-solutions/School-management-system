"""
The admin answer desk, per-document check, gaps, personal-data gate and the
console's own limits (K3).

Only the fake provider is used. The console tests prove that the test tools are
exempt from the public switches but never from the stop switch or their own
limits, and that a safeguarding test sends no real alert.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.assistant import gateway
from apps.assistant.forms import AssistantSettingsForm
from apps.assistant.knowledge import answers, gaps, ingest, personal_data, scope
from apps.assistant.knowledge import services as knowledge_services
from apps.assistant.models import (
    AssistantSettings,
    KnowledgeDocument,
    KnowledgeGap,
    KnowledgeStatus,
    SafeguardingAlert,
    UsageEvent,
)
from apps.assistant.providers.base import ChatMessage
from apps.core.models import AuditLog

from .knowledge_helpers import AnswerTestCase

BACKEND = "apps.accounts.backends.EmailOrAdmissionBackend"

FEE_BODY = "Grade 4 term fee is KSh 4,500 per term, payable in January."
PHONE_BODY = (
    "Pupil: Jane Wanjiku, guardian phone 0712 345 678. "
    "The term fee is KSh 4,500 and is due in January."
)


def messages():
    return [ChatMessage(role="user", content="a test question")]


# ── Answer desk ──────────────────────────────────────────────────────────────


class AnswerDeskViewTests(AnswerTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin, backend=BACKEND)
        self.collection = self.make_collection("Signed-in fees")
        self.document = self.make_ready(
            FEE_BODY, title="Signed-in fees", collection=self.collection
        )
        self.url = reverse("assistant:knowledge_answer_desk")

    def test_only_an_admin_may_open_the_desk(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.force_login(self.teacher, backend=BACKEND)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_previewing_as_a_visitor_never_returns_a_signed_in_document(self):
        response = self.client.post(
            self.url, {"question": "Grade 4 term fee", "audience": "visitor"}
        )
        self.assertEqual(response.status_code, 200)
        result = response.context["result"]
        self.assertTrue(result.refused)
        self.assertEqual(result.sources, [])
        self.assertNotIn(FEE_BODY, response.content.decode())

    def test_previewing_as_a_parent_of_class_a_never_returns_class_b(self):
        for school_class, label in ((self.form2, "A"), (self.form3, "B")):
            collection = self.make_collection(
                f"Class {label} parents", roles=["parent"], classes=[school_class]
            )
            body = f"Parents of class {label} pay KSh 4,500 for Grade 4."
            self.make_ready(
                body,
                title=f"Class {label}",
                collection=collection,
                roles=["parent"],
                classes=[school_class],
            )

        inside = self.client.post(
            self.url,
            {
                "question": "Grade 4 term fee",
                "audience": "parent",
                "preview_class": self.form2.pk,
            },
        ).context["result"]
        outside = self.client.post(
            self.url,
            {
                "question": "Grade 4 term fee",
                "audience": "parent",
                "preview_class": self.form3.pk,
            },
        ).context["result"]

        inside_titles = {source.document_title for source in inside.sources}
        outside_titles = {source.document_title for source in outside.sources}
        self.assertIn("Class A", inside_titles)
        self.assertNotIn("Class B", inside_titles)
        self.assertIn("Class B", outside_titles)
        self.assertNotIn("Class A", outside_titles)

    def test_previewing_as_a_student_never_returns_finance_or_teacher_material(self):
        for name, roles in (("Finance", ["admin"]), ("Teacher notes", ["teacher"])):
            collection = self.make_collection(name, roles=roles)
            self.make_ready(
                "The Grade 4 term fee is KSh 4,500 in staff notes.",
                title=name,
                collection=collection,
                roles=roles,
            )
        result = self.client.post(
            self.url,
            {"question": "Grade 4 term fee", "audience": "student"},
        ).context["result"]
        titles = {source.document_title for source in result.sources}
        self.assertNotIn("Finance", titles)
        self.assertNotIn("Teacher notes", titles)

    def test_a_safeguarding_test_shows_what_would_happen_and_sends_nothing(self):
        response = self.client.post(
            self.url,
            {"question": "I want to kill myself", "audience": "visitor"},
        )
        result = response.context["result"]
        self.assertTrue(result.screened)
        self.assertTrue(result.alert_preview)
        self.assertFalse(result.provider_called)
        self.assertEqual(SafeguardingAlert.objects.count(), 0)
        self.assertIn("no alert", response.content.decode().lower())


# ── Per-document check ───────────────────────────────────────────────────────


class DocumentCheckViewTests(AnswerTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin, backend=BACKEND)
        self.document = self.make_ready(FEE_BODY, title="Grade 4 fees")
        self.url = reverse(
            "assistant:knowledge_document_check", args=[self.document.pk]
        )

    def test_the_page_says_only_the_question_is_embedded(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "only provider")
        self.assertContains(response, "no answer is generated")

    def test_checking_a_document_never_calls_a_chat_model(self):
        with patch(
            "apps.assistant.gateway.call_chain",
            side_effect=AssertionError("no chat call"),
        ):
            response = self.client.post(self.url, {"question": "Grade 4 term fee"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["result"])
        self.assertEqual(
            UsageEvent.objects.filter(profile=UsageEvent.Profile.CONSOLE).count(), 1
        )

    def test_a_safeguarding_question_sends_no_alert_from_the_check(self):
        response = self.client.post(self.url, {"question": "I want to kill myself"})
        self.assertTrue(response.context["screened"])
        self.assertEqual(SafeguardingAlert.objects.count(), 0)


# ── Console switches and limits ──────────────────────────────────────────────


class ConsoleControlTests(AnswerTestCase):
    def row(self):
        return AssistantSettings.load()

    def test_the_console_works_when_every_live_switch_is_off(self):
        row = self.row()
        row.enabled = False
        row.public_enabled = False
        row.signed_in_enabled = False
        row.save()
        result = gateway.call_chain(
            [self.provider], messages(), profile=UsageEvent.Profile.CONSOLE
        )
        self.assertTrue(result.reply.text)
        self.assertTrue(
            UsageEvent.objects.filter(profile=UsageEvent.Profile.CONSOLE).exists()
        )

    def test_the_live_assistant_still_obeys_the_main_switch(self):
        row = self.row()
        row.enabled = False
        row.save()
        with self.assertRaises(gateway.AllProvidersUnavailable) as caught:
            gateway.call_chain(
                [self.provider], messages(), profile=UsageEvent.Profile.PUBLIC
            )
        self.assertIn("switched off", str(caught.exception).lower())

    def test_the_stop_switch_always_wins_over_the_console(self):
        row = self.row()
        row.kill_switch = True
        row.save()
        with self.assertRaises(gateway.AllProvidersUnavailable) as caught:
            gateway.call_chain(
                [self.provider], messages(), profile=UsageEvent.Profile.CONSOLE
            )
        self.assertIn("stop switch", str(caught.exception).lower())
        self.assertEqual(UsageEvent.objects.count(), 0)

    def test_the_consoles_daily_cap_stops_further_test_requests(self):
        row = self.row()
        row.console_daily_limit = 1
        row.save()
        gateway.call_chain(
            [self.provider], messages(), profile=UsageEvent.Profile.CONSOLE
        )
        with self.assertRaises(gateway.AllProvidersUnavailable) as caught:
            gateway.call_chain(
                [self.provider], messages(), profile=UsageEvent.Profile.CONSOLE
            )
        self.assertIn("today", str(caught.exception).lower())

    def test_the_consoles_monthly_ceiling_is_separate_and_enforced(self):
        row = self.row()
        row.console_monthly_budget_ksh = Decimal("0")
        row.save()
        with self.assertRaises(gateway.AllProvidersUnavailable) as caught:
            gateway.call_chain(
                [self.provider], messages(), profile=UsageEvent.Profile.CONSOLE
            )
        self.assertIn("ceiling", str(caught.exception).lower())

    def test_console_usage_is_recorded_as_an_admin_test(self):
        gateway.call_chain(
            [self.provider], messages(), profile=UsageEvent.Profile.CONSOLE
        )
        usage = UsageEvent.objects.get(profile=UsageEvent.Profile.CONSOLE)
        self.assertEqual(usage.source, UsageEvent.Source.ADMIN_TEST)

    def test_the_settings_form_exposes_the_stop_switch_and_console_limits(self):
        form = AssistantSettingsForm()
        for name in ("kill_switch", "console_daily_limit", "console_monthly_budget_ksh"):
            self.assertIn(name, form.fields)

    def test_a_test_run_records_a_gap_marked_as_a_test(self):
        audience = scope.audience_for_preview("visitor")
        result = answers.answer_question(
            "Grade 4 term fee",
            audience=audience,
            actor=self.admin,
            profile=UsageEvent.Profile.CONSOLE,
            use_cache=False,
            is_test=True,
        )
        self.assertTrue(result.refused)
        gap = KnowledgeGap.objects.get(pk=result.gap_id)
        self.assertTrue(gap.is_test)


# ── No real alert from any console ───────────────────────────────────────────


class ConsoleAlertTests(AnswerTestCase):
    def test_the_test_console_only_previews_the_alert(self):
        from apps.assistant import services

        with patch("django_q.tasks.async_task") as queued, patch(
            "apps.assistant.tasks.send_alert_email"
        ) as email:
            result = services.run_console(
                question="I want to kill myself",
                provider_id=None,
                actor=self.admin,
            )
        self.assertTrue(result.rules_fired)
        self.assertTrue(result.alert_preview)
        self.assertFalse(result.provider_called)
        self.assertEqual(SafeguardingAlert.objects.count(), 0)
        self.assertEqual(queued.call_count, 0)
        self.assertEqual(email.call_count, 0)


# ── Personal data in documents ───────────────────────────────────────────────


class PersonalDataTests(AnswerTestCase):
    def test_the_scan_finds_and_masks_emails_phones_and_names(self):
        text = (
            "Contact the office: fees@school.example or 0712 345 678. "
            "Pupil: Jane Wanjiku needs the form."
        )
        findings = personal_data.scan_text(text, heading="Contacts", passage=3)
        kinds = {finding.kind for finding in findings}
        self.assertTrue({"email", "phone", "name"} <= kinds)
        for finding in findings:
            self.assertNotIn("@", finding.snippet)
            self.assertNotIn("0712", finding.snippet)
            self.assertNotIn("Jane", finding.snippet)
            self.assertEqual(finding.passage, 3)
            self.assertEqual(finding.heading, "Contacts")

    def test_the_scan_records_found_locations_and_never_the_value(self):
        document = self.make_document(
            self.make_collection("Contacts"),
            title="Contacts",
            body_text=PHONE_BODY,
        )
        personal_data.record_scan(
            document,
            personal_data.scan_text(PHONE_BODY, heading="Contacts", passage=0),
        )
        document.refresh_from_db()
        self.assertEqual(document.pii_state, KnowledgeDocument.PIIState.FOUND)
        self.assertIsNotNone(document.pii_scanned_at)
        self.assertTrue(document.pii_findings)
        stored = str(document.pii_findings)
        self.assertNotIn("0712", stored)
        self.assertNotIn("Jane", stored)

    def test_ingestion_refuses_to_embed_a_document_with_personal_data(self):
        document = self.make_document(
            self.make_collection("Contacts"),
            title="Contacts",
            body_text=PHONE_BODY,
        )
        with self.assertRaises(ingest.IngestionFailed) as caught:
            ingest.ingest_document(document.pk)
        document.refresh_from_db()
        self.assertEqual(document.pii_state, KnowledgeDocument.PIIState.FOUND)
        self.assertEqual(document.chunks.count(), 0)
        self.assertIn("Personal data", str(caught.exception))
        self.assertFalse(
            UsageEvent.objects.filter(profile=UsageEvent.Profile.INGESTION).exists()
        )

    def test_approval_is_blocked_until_the_admin_decides(self):
        document = self.make_document(
            self.make_collection("Contacts"),
            title="Contacts",
            body_text=PHONE_BODY,
            status=KnowledgeStatus.DRAFT,
        )
        personal_data.record_scan(
            document, [personal_data.Finding("phone", "", 0, "…[PHONE]…")]
        )
        with self.assertRaises(ValidationError):
            knowledge_services.set_status(
                document,
                KnowledgeStatus.APPROVED,
                actor=self.admin,
                request=None,
            )

    def test_confirming_records_who_when_and_the_exact_words_then_allows_reading(self):
        document = self.make_document(
            self.make_collection("Contacts"),
            title="Contacts",
            body_text=PHONE_BODY,
        )
        with self.assertRaises(ingest.IngestionFailed):
            ingest.ingest_document(document.pk)
        document.refresh_from_db()

        knowledge_services.confirm_personal_data(document, actor=self.admin)
        document.refresh_from_db()
        self.assertEqual(document.pii_state, KnowledgeDocument.PIIState.CONFIRMED)
        self.assertEqual(document.pii_confirmed_by, self.admin)
        self.assertIsNotNone(document.pii_confirmed_at)
        self.assertEqual(
            document.pii_confirmation_text, personal_data.CONFIRMATION_TEXT
        )

        ingest.ingest_document(document.pk)
        document.refresh_from_db()
        self.assertEqual(
            document.ingestion_state, KnowledgeDocument.IngestionState.READY
        )

    def test_a_clean_document_is_recorded_as_clean_and_reads(self):
        document = self.make_ready(FEE_BODY, title="Grade 4 fees")
        self.assertEqual(document.pii_state, KnowledgeDocument.PIIState.CLEAN)
        self.assertTrue(document.chunks.exists())

    def test_the_confirmation_screen_records_the_decision(self):
        self.client.force_login(self.admin, backend=BACKEND)
        document = self.make_document(
            self.make_collection("Contacts"),
            title="Contacts",
            body_text=PHONE_BODY,
        )
        personal_data.record_scan(
            document, [personal_data.Finding("phone", "", 0, "…[PHONE]…")]
        )
        response = self.client.post(
            reverse("assistant:knowledge_document_confirm_pii", args=[document.pk])
        )
        self.assertEqual(response.status_code, 302)
        document.refresh_from_db()
        self.assertEqual(document.pii_state, KnowledgeDocument.PIIState.CONFIRMED)

    def test_a_pasted_document_is_scanned_when_it_is_added(self):
        document = self.make_document(
            self.make_collection("Contacts"),
            title="Contacts",
            body_text=PHONE_BODY,
        )
        findings = knowledge_services.scan_for_personal_data(document)
        document.refresh_from_db()
        self.assertTrue(findings)
        self.assertEqual(document.pii_state, KnowledgeDocument.PIIState.FOUND)


# ── Gaps ─────────────────────────────────────────────────────────────────────


class GapTests(AnswerTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin, backend=BACKEND)

    def make_gap(self, **overrides):
        data = {
            "question": "What is the bus fee?",
            "role": "parent",
            "class_signature": "7",
            "language_hint": "en",
            "reason": KnowledgeGap.Reason.NO_MATCH,
            "top_score": 0.12,
            "is_test": True,
        }
        data.update(overrides)
        return KnowledgeGap.objects.create(**data)

    def test_a_gap_stores_nothing_about_the_person_who_asked(self):
        field_names = {field.name for field in KnowledgeGap._meta.fields}
        self.assertEqual(
            field_names,
            {
                "id",
                "created_at",
                "updated_at",
                "question",
                "role",
                "class_signature",
                "language_hint",
                "reason",
                "top_score",
                "is_test",
                "resolved_at",
                "resolved_by",
                "resolution_note",
            },
        )

    def test_the_recorded_question_is_the_masked_one(self):
        audience = scope.audience_for_preview("parent", [self.form2.pk])
        gap = answers.record_gap(
            question="My child Jane needs 0712 345 678",
            masked_question="My child [NAME] needs [PHONE]",
            audience=audience,
            reason=KnowledgeGap.Reason.NO_MATCH,
            top_score=0.1,
            is_test=True,
        )
        self.assertEqual(gap.question, "My child [NAME] needs [PHONE]")
        self.assertEqual(gap.role, "parent")
        self.assertEqual(gap.class_signature, str(self.form2.pk))

    def test_resolving_records_who_when_and_a_note(self):
        gap = self.make_gap()
        gaps.resolve(gap, actor=self.admin, note="Bus policy added.")
        gap.refresh_from_db()
        self.assertIsNotNone(gap.resolved_at)
        self.assertEqual(gap.resolved_by, self.admin)
        self.assertEqual(gap.resolution_note, "Bus policy added.")

    def test_the_admin_can_delete_any_gap(self):
        gap = self.make_gap()
        gaps.erase(gap)
        self.assertFalse(KnowledgeGap.objects.filter(pk=gap.pk).exists())

    def test_the_draft_retention_period_is_ninety_days(self):
        old = self.make_gap(
            resolved_at=timezone.now() - datetime.timedelta(days=91)
        )
        recent = self.make_gap(
            resolved_at=timezone.now() - datetime.timedelta(days=10)
        )
        erasable = set(gaps.erasable_gaps().values_list("pk", flat=True))
        self.assertIn(old.pk, erasable)
        self.assertNotIn(recent.pk, erasable)

    def test_the_list_hides_test_gaps_by_default(self):
        test_gap = self.make_gap()
        real_gap = self.make_gap(is_test=False, question="A real gap")
        response = self.client.get(reverse("assistant:knowledge_gaps"))
        listed = set(response.context["gaps"].values_list("pk", flat=True))
        self.assertIn(real_gap.pk, listed)
        self.assertNotIn(test_gap.pk, listed)

        response = self.client.get(
            reverse("assistant:knowledge_gaps"), {"state": "all", "tests": "1"}
        )
        listed = set(response.context["gaps"].values_list("pk", flat=True))
        self.assertIn(test_gap.pk, listed)

    def test_the_delete_button_removes_the_gap_and_audits_it(self):
        gap = self.make_gap()
        response = self.client.post(
            reverse("assistant:knowledge_gap_delete", args=[gap.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(KnowledgeGap.objects.filter(pk=gap.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action="assistant.knowledge_gap_deleted"
            ).exists()
        )

    def test_the_resolve_button_marks_the_gap_answered(self):
        gap = self.make_gap()
        response = self.client.post(
            reverse("assistant:knowledge_gap_resolve", args=[gap.pk]),
            {"note": "Answered from the fees policy."},
        )
        self.assertEqual(response.status_code, 302)
        gap.refresh_from_db()
        self.assertIsNotNone(gap.resolved_at)
        self.assertEqual(gap.resolution_note, "Answered from the fees policy.")
