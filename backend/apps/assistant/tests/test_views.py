"""
apps/assistant/tests/test_views.py
----------------------------------
The admin screens: who may open them, what a save shows afterwards, and the
fact that a saved key never appears in a response.
"""

from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.assistant.models import AssistantSettings, ProviderConfig, UsageEvent
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
        self.assertContains(response, "switched off")
        self.assertFalse(AssistantSettings.load().enabled)

    def test_saving_the_switch_on_is_audited(self):
        response = self.client.post(
            reverse("assistant:settings"),
            {
                "enabled": "on",
                "default_provider": "",
                "house_rules": "Be warm and brief.",
                "monthly_budget_ksh": "1500",
                "daily_message_limit": "20",
                "handoff_email": "office@school.example",
            },
        )
        self.assertRedirects(response, reverse("assistant:settings"))
        row = AssistantSettings.load()
        self.assertTrue(row.enabled)
        self.assertEqual(str(row.monthly_budget_ksh), "1500.00")
        entry = AuditLog.objects.get(action="assistant.settings_updated")
        self.assertIn("enabled", entry.change_summary["fields"])
