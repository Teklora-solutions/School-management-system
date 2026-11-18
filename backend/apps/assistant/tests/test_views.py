"""
apps/assistant/tests/test_views.py
----------------------------------
The admin screens: who may open them, what a save shows afterwards, and the
fact that a saved key never appears in a response.
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.assistant.models import (
    AssistantJobFailure,
    AssistantSettings,
    CustomGuardrailRule,
    ProviderConfig,
    SafeguardingAlert,
    UsageEvent,
)
from apps.core.models import AuditLog
from apps.dashboards.tests.fixtures import make_user

BACKEND = "apps.accounts.backends.EmailOrAdmissionBackend"
API_KEY = "sk-test-1234567890-wxyz"


class ScreenAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="assistant_admin")
        cls.teacher = make_user("teacher", username="assistant_teacher")
        cls.parent = make_user("parent", username="assistant_parent")

    def test_admin_can_open_settings_and_providers(self):
        self.client.force_login(self.admin, backend=BACKEND)
        for name in ("assistant:settings", "assistant:providers", "assistant:provider_create"):
            with self.subTest(screen=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_other_roles_are_refused(self):
        for user in (self.teacher, self.parent):
            self.client.force_login(user, backend=BACKEND)
            for name in ("assistant:settings", "assistant:providers"):
                with self.subTest(role=user.role, screen=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_a_visitor_is_sent_to_login(self):
        response = self.client.get(reverse("assistant:settings"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_other_roles_cannot_post_a_provider(self):
        self.client.force_login(self.teacher, backend=BACKEND)
        response = self.client.post(
            reverse("assistant:provider_create"),
            {"name": "Sneaky", "kind": "fake", "timeout_seconds": 30, "is_active": "on"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ProviderConfig.objects.count(), 0)


class ProviderScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="assistant_admin")

    def setUp(self):
        cache.clear()
        self.client.force_login(self.admin, backend=BACKEND)

    def create(self, api_key=API_KEY, follow=False, **overrides):
        data = {
            "name": "Fake provider",
            "kind": "fake",
            "model_name": "fake-1",
            "base_url": "",
            "timeout_seconds": 30,
            "is_active": "on",
            "api_key": api_key,
            "input_usd_per_1m": "0",
            "output_usd_per_1m": "0",
            "embedding_usd_per_1m": "0",
            "priority": "100",
        }
        data.update(overrides)
        return self.client.post(reverse("assistant:provider_create"), data, follow=follow)

    def test_saving_a_key_shows_only_the_last_four(self):
        response = self.create(follow=True)
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("\u2022\u2022\u2022\u2022 wxyz", page)
        self.assertNotIn(API_KEY, page)

        settings_page = self.client.get(reverse("assistant:settings")).content.decode()
        self.assertNotIn(API_KEY, settings_page)

    def test_the_key_is_not_written_to_the_audit_trail(self):
        self.create()
        self.assertNotIn(API_KEY, str(list(AuditLog.objects.values_list("change_summary", flat=True))))

    def test_editing_without_pasting_a_key_keeps_it(self):
        self.create()
        provider = ProviderConfig.objects.get()
        response = self.client.post(
            reverse("assistant:provider_update", args=[provider.pk]),
            {
                "name": "Renamed provider",
                "kind": "fake",
                "model_name": "fake-1",
                "base_url": "",
                "timeout_seconds": 30,
                "is_active": "on",
                "api_key": "",
                "input_usd_per_1m": "0",
                "output_usd_per_1m": "0",
                "embedding_usd_per_1m": "0",
                "priority": "100",
            },
        )
        self.assertRedirects(response, reverse("assistant:providers"))
        provider.refresh_from_db()
        self.assertEqual(provider.name, "Renamed provider")
        self.assertEqual(provider.api_key_last4, "wxyz")

    def test_test_button_records_usage(self):
        self.create()
        provider = ProviderConfig.objects.get()
        response = self.client.post(
            reverse("assistant:provider_test", args=[provider.pk]), follow=True
        )
        self.assertContains(response, "Fake assistant reply")
        self.assertEqual(UsageEvent.objects.filter(provider=provider).count(), 1)

    def test_an_inactive_provider_is_not_tested(self):
        self.create(is_active="")
        provider = ProviderConfig.objects.get()
        response = self.client.post(
            reverse("assistant:provider_test", args=[provider.pk]), follow=True
        )
        self.assertContains(response, "is switched off")
        self.assertEqual(UsageEvent.objects.count(), 0)

    def test_an_inactive_provider_is_not_offered_as_default(self):
        self.create(is_active="")
        provider = ProviderConfig.objects.get()
        page = self.client.get(reverse("assistant:settings")).content.decode()
        self.assertNotIn(f'<option value="{provider.pk}"', page)

    @override_settings(ASSISTANT_ENCRYPTION_KEY="")
    def test_without_a_server_key_the_form_explains_and_saves_nothing(self):
        response = self.create()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no encryption key configured")
        self.assertEqual(ProviderConfig.objects.count(), 0)

    @override_settings(ASSISTANT_ENCRYPTION_KEY="")
    def test_the_warning_is_visible_before_any_save(self):
        response = self.client.get(reverse("assistant:settings"))
        self.assertContains(response, "No encryption key is configured")


class SettingsScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="assistant_admin")

    def setUp(self):
        self.client.force_login(self.admin, backend=BACKEND)

    def test_defaults_render_switched_off(self):
        response = self.client.get(reverse("assistant:settings"))
        self.assertContains(response, "Every switch starts off")
        self.assertFalse(AssistantSettings.load().enabled)

    def test_saving_the_switch_on_is_audited(self):
        response = self.client.post(
            reverse("assistant:settings"),
            {
                "enabled": "on",
                "default_provider": "",
                "house_rules": "Be warm and brief.",
                "monthly_budget_ksh": "1500",
                "usd_to_ksh": "130",
                "daily_message_limit": "20",
                "public_visitor_daily_limit": "10",
                "public_ip_daily_limit": "30",
                "public_site_daily_limit": "300",
                "escalation_minutes": "15",
                "working_hours_start": "07:00",
                "working_hours_end": "17:00",
                "alert_retention_months": "12",
                "handoff_email": "office@school.example",
            },
        )
        self.assertRedirects(response, reverse("assistant:settings"))
        row = AssistantSettings.load()
        self.assertTrue(row.enabled)
        self.assertEqual(str(row.monthly_budget_ksh), "1500.00")
        entry = AuditLog.objects.get(action="assistant.settings_updated")
        self.assertIn("enabled", entry.change_summary["fields"])


class NewScreenAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="screens_admin")
        cls.director = make_user("director", username="screens_director")
        cls.teacher = make_user("teacher", username="screens_teacher")

    def test_admin_opens_every_assistant_screen(self):
        self.client.force_login(self.admin, backend=BACKEND)
        for name in (
            "assistant:settings",
            "assistant:providers",
            "assistant:guardrails",
            "assistant:guardrail_create",
            "assistant:alerts",
            "assistant:console",
            "assistant:jobs",
        ):
            with self.subTest(screen=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_director_sees_alerts_but_not_admin_screens(self):
        self.client.force_login(self.director, backend=BACKEND)
        self.assertEqual(self.client.get(reverse("assistant:alerts")).status_code, 200)
        for name in ("assistant:settings", "assistant:console", "assistant:guardrails"):
            with self.subTest(screen=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_teacher_cannot_see_alerts(self):
        self.client.force_login(self.teacher, backend=BACKEND)
        self.assertEqual(self.client.get(reverse("assistant:alerts")).status_code, 403)

    def test_the_missing_contact_warning_is_persistent_for_admin_only(self):
        self.client.force_login(self.admin, backend=BACKEND)
        self.assertContains(
            self.client.get(reverse("assistant:settings")),
            "Safeguarding contact missing",
        )

        self.client.force_login(self.director, backend=BACKEND)
        self.assertNotContains(
            self.client.get(reverse("assistant:alerts")),
            "Safeguarding contact missing",
        )


class TestConsoleScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="console_admin")

    def setUp(self):
        cache.clear()
        self.client.force_login(self.admin, backend=BACKEND)
        self.provider = ProviderConfig.objects.create(
            name="Fake", kind=ProviderConfig.Kind.FAKE, model_name="fake"
        )

    def test_a_question_gets_an_answer_and_shows_the_masked_request(self):
        response = self.client.post(
            reverse("assistant:console"),
            {
                "provider": self.provider.pk,
                "question": "My name is Jane Wanjiku. What time does school open?",
            },
        )
        self.assertContains(response, "Fake assistant reply")
        self.assertContains(response, "[NAME]")
        self.assertContains(response, "Masked before sending")
        # The question the admin typed stays in the form; only the masked copy
        # is shown as what would leave the server.
        page = response.content.decode()
        outbound = page.split("What would be sent to the provider", 1)[1]
        self.assertNotIn("Jane Wanjiku", outbound)
        self.assertEqual(UsageEvent.objects.count(), 1)

    def test_a_safeguarding_question_gets_the_safe_reply_and_no_alert(self):
        response = self.client.post(
            reverse("assistant:console"),
            {"provider": self.provider.pk, "question": "I want to die"},
        )
        self.assertContains(response, "Safeguarding triggered")
        self.assertContains(response, "999 or 112")
        self.assertEqual(SafeguardingAlert.objects.count(), 0)
        self.assertEqual(UsageEvent.objects.count(), 0)

    def test_the_missing_contact_is_explained(self):
        self.assertContains(
            self.client.get(reverse("assistant:console")),
            "No safeguarding contact is set",
        )


class GuardrailScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="rules_admin")

    def setUp(self):
        self.client.force_login(self.admin, backend=BACKEND)

    def test_the_mandatory_pack_is_shown_as_unremovable(self):
        response = self.client.get(reverse("assistant:guardrails"))
        self.assertContains(response, "Cannot be removed")
        self.assertContains(response, "M-SELF-HARM-1")

    def test_an_admin_can_add_and_switch_a_rule(self):
        response = self.client.post(
            reverse("assistant:guardrail_create"),
            {
                "name": "Local phrase",
                "category": CustomGuardrailRule.Category.ABUSE,
                "patterns": "amenifanyia vibaya\n",
                "note": "Heard by the class teacher.",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("assistant:guardrails"))
        rule = CustomGuardrailRule.objects.get()
        self.assertTrue(rule.is_active)

        self.client.post(reverse("assistant:guardrail_toggle", args=[rule.pk]))
        rule.refresh_from_db()
        self.assertFalse(rule.is_active)
        self.assertTrue(
            AuditLog.objects.filter(action="assistant.guardrail_rule_toggled").exists()
        )


class AlertScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="alerts_admin")
        cls.director = make_user("director", username="alerts_director")

    def setUp(self):
        self.alert = SafeguardingAlert.objects.create(
            profile=UsageEvent.Profile.SIGNED_IN,
            category="abuse",
            rule_id="M-ABUSE-1",
            excerpt="[PHONE]",
        )

    def test_the_page_shows_a_masked_alert_and_the_banner(self):
        self.client.force_login(self.admin, backend=BACKEND)
        response = self.client.get(reverse("assistant:alerts"))
        self.assertContains(response, "Acknowledge")
        self.assertContains(response, "Abuse or exploitation")
        self.assertContains(response, "1 open alert")

    def test_a_director_can_acknowledge(self):
        self.client.force_login(self.director, backend=BACKEND)
        response = self.client.post(
            reverse("assistant:alert_acknowledge", args=[self.alert.pk])
        )
        self.assertRedirects(response, reverse("assistant:alerts"))
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, SafeguardingAlert.Status.ACKNOWLEDGED)
        self.assertEqual(self.alert.acknowledged_by, self.director)

    def test_the_close_screen_shows_the_outcome_form_and_draft_retention(self):
        self.client.force_login(self.admin, backend=BACKEND)
        response = self.client.get(reverse("assistant:alert_close", args=[self.alert.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Outcome / action taken")
        self.assertContains(response, "DRAFT")
        self.assertContains(response, "12 months")

    def test_a_director_can_close_with_an_outcome(self):
        self.client.force_login(self.director, backend=BACKEND)
        response = self.client.post(
            reverse("assistant:alert_close", args=[self.alert.pk]),
            {"outcome": "Called the family; referred to the safeguarding lead."},
        )
        self.assertRedirects(response, reverse("assistant:alerts"))
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, SafeguardingAlert.Status.CLOSED)
        self.assertEqual(self.alert.closed_by, self.director)
        self.assertIn("safeguarding lead", self.alert.outcome)

    def test_closing_without_an_outcome_is_refused(self):
        self.client.force_login(self.admin, backend=BACKEND)
        response = self.client.post(
            reverse("assistant:alert_close", args=[self.alert.pk]), {"outcome": ""}
        )
        self.assertEqual(response.status_code, 200)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, SafeguardingAlert.Status.OPEN)

    def test_a_teacher_cannot_close(self):
        teacher = make_user("teacher", username="alerts_teacher")
        self.client.force_login(teacher, backend=BACKEND)
        response = self.client.get(reverse("assistant:alert_close", args=[self.alert.pk]))
        self.assertEqual(response.status_code, 403)


class JobFailureScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="jobs_admin")

    def setUp(self):
        self.client.force_login(self.admin, backend=BACKEND)

    def test_a_failure_is_visible_and_resolvable(self):
        failure = AssistantJobFailure.objects.create(
            task_name="assistant.safeguarding_email", reason="SMTP is down", attempts=1
        )
        self.assertContains(self.client.get(reverse("assistant:jobs")), "SMTP is down")
        self.client.post(reverse("assistant:job_resolve", args=[failure.pk]))
        failure.refresh_from_db()
        self.assertIsNotNone(failure.resolved_at)


class ProviderDeleteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="delete_admin")

    def setUp(self):
        self.client.force_login(self.admin, backend=BACKEND)

    def test_the_default_provider_cannot_be_deleted(self):
        provider = ProviderConfig.objects.create(name="Main", kind="fake")
        row = AssistantSettings.load()
        row.default_provider = provider
        row.save()
        response = self.client.post(
            reverse("assistant:provider_delete", args=[provider.pk]), follow=True
        )
        self.assertContains(response, "Choose another default provider")
        self.assertTrue(ProviderConfig.objects.filter(pk=provider.pk).exists())

    def test_a_spare_provider_can_be_deleted(self):
        ProviderConfig.objects.create(name="Keep", kind="fake", is_active=True)
        spare = ProviderConfig.objects.create(name="Spare", kind="fake", is_active=False)
        self.client.post(reverse("assistant:provider_delete", args=[spare.pk]))
        self.assertFalse(ProviderConfig.objects.filter(pk=spare.pk).exists())


class ProviderTestFailureScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="failtest_admin")

    def setUp(self):
        cache.clear()
        self.client.force_login(self.admin, backend=BACKEND)

    def test_a_failed_test_is_explained_and_audited(self):
        provider = ProviderConfig.objects.create(
            name="Bad", kind=ProviderConfig.Kind.FAKE, model_name="fake-auth"
        )
        response = self.client.post(
            reverse("assistant:provider_test", args=[provider.pk]), follow=True
        )
        self.assertContains(response, "Fake invalid key")
        self.assertTrue(
            AuditLog.objects.filter(
                action="assistant.provider_test_failed",
                object_id=str(provider.pk),
            ).exists()
        )


class SettingsContactValidationScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="validation_admin")

    def setUp(self):
        self.client.force_login(self.admin, backend=BACKEND)

    def test_signed_in_mode_cannot_be_saved_without_a_contact(self):
        response = self.client.post(
            reverse("assistant:settings"),
            {
                "enabled": "on",
                "signed_in_enabled": "on",
                "public_visitor_daily_limit": "10",
                "public_ip_daily_limit": "30",
                "public_site_daily_limit": "300",
                "daily_message_limit": "20",
                "monthly_budget_ksh": "0",
                "usd_to_ksh": "0",
                "escalation_minutes": "15",
                "working_hours_start": "07:00",
                "working_hours_end": "17:00",
                "alert_retention_months": "12",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "named safeguarding contact is required")
        self.assertFalse(AssistantSettings.load().signed_in_enabled)
