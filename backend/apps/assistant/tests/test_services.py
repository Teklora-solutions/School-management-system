"""
apps/assistant/tests/test_services.py
-------------------------------------
What the database is allowed to remember: ciphertext and a last-four, never the
key; audit entries by field name; usage rows with no prompt text.
"""

from __future__ import annotations

from django.test import TestCase, override_settings

from apps.assistant import crypto, services
from apps.assistant.models import AssistantSettings, ProviderConfig, UsageEvent
from apps.assistant.providers import ProviderUnavailable
from apps.assistant.services import (
    create_provider,
    update_assistant_settings,
    update_provider,
)
from apps.core.models import AuditLog
from apps.dashboards.tests.fixtures import make_user

API_KEY = "sk-test-1234567890-wxyz"


def provider_data(**overrides):
    data = {
        "name": "Fake provider",
        "kind": ProviderConfig.Kind.FAKE,
        "model_name": "fake-1",
        "base_url": "",
        "timeout_seconds": 30,
        "is_active": True,
        "api_key": "",
    }
    data.update(overrides)
    return data


class ProviderStorageTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="assistant_admin")

    def test_creating_with_a_key_stores_ciphertext_and_last_four(self):
        provider = create_provider(cleaned_data=provider_data(api_key=API_KEY))
        provider.refresh_from_db()
        self.assertNotIn(API_KEY, provider.api_key_ciphertext)
        self.assertEqual(provider.api_key_last4, "wxyz")
        self.assertEqual(crypto.decrypt(provider.api_key_ciphertext), API_KEY)
        self.assertEqual(provider.masked_key, "\u2022\u2022\u2022\u2022 wxyz")

    def test_creation_audit_entry_never_contains_the_key(self):
        provider = create_provider(cleaned_data=provider_data(api_key=API_KEY))
        entry = AuditLog.objects.get(
            action="assistant.provider_created", object_id=str(provider.pk)
        )
        self.assertNotIn(API_KEY, str(entry.change_summary))
        self.assertNotIn("wxyz", str(entry.change_summary))
        self.assertTrue(entry.change_summary["key_set"])

    def test_a_blank_key_on_edit_keeps_the_saved_one(self):
        provider = create_provider(cleaned_data=provider_data(api_key=API_KEY))
        before = provider.api_key_ciphertext
        update_provider(instance=provider, cleaned_data=provider_data(api_key=""))
        provider.refresh_from_db()
        self.assertEqual(provider.api_key_ciphertext, before)
        self.assertEqual(provider.api_key_last4, "wxyz")

    def test_a_new_key_on_edit_replaces_it_and_is_audited(self):
        provider = create_provider(cleaned_data=provider_data(api_key=API_KEY))
        update_provider(
            instance=provider,
            cleaned_data=provider_data(api_key="sk-replacement-0000-abcd"),
        )
        provider.refresh_from_db()
        self.assertEqual(provider.api_key_last4, "abcd")
        entry = AuditLog.objects.filter(
            action="assistant.provider_updated", object_id=str(provider.pk)
        ).latest("created_at")
        self.assertTrue(entry.change_summary["key_updated"])
        self.assertNotIn("sk-replacement", str(entry.change_summary))

    @override_settings(ASSISTANT_ENCRYPTION_KEY="")
    def test_without_a_server_key_a_provider_key_is_refused(self):
        with self.assertRaises(crypto.EncryptionUnavailable):
            create_provider(cleaned_data=provider_data(api_key=API_KEY))
        self.assertEqual(ProviderConfig.objects.count(), 0)

    def test_a_provider_without_a_key_is_allowed(self):
        provider = create_provider(cleaned_data=provider_data())
        self.assertFalse(provider.has_key)
        self.assertEqual(provider.masked_key, "Not set")


class SettingsTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="assistant_admin")

    def test_defaults_are_off(self):
        row = AssistantSettings.load()
        self.assertFalse(row.enabled)
        self.assertEqual(row.monthly_budget_ksh, 0)

    def test_only_changed_fields_are_audited_by_name(self):
        row = AssistantSettings.load()
        update_assistant_settings(
            instance=row,
            cleaned_data={"enabled": True, "daily_message_limit": 20},
            actor=self.admin,
        )
        entry = AuditLog.objects.get(action="assistant.settings_updated")
        self.assertEqual(entry.change_summary["fields"], ["enabled"])

    def test_saving_the_same_values_writes_no_audit_entry(self):
        row = AssistantSettings.load()
        update_assistant_settings(
            instance=row,
            cleaned_data={"enabled": False, "daily_message_limit": 20},
            actor=self.admin,
        )
        self.assertFalse(
            AuditLog.objects.filter(action="assistant.settings_updated").exists()
        )


class TestProviderTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="assistant_admin")

    def test_testing_the_fake_provider_records_usage_and_audit(self):
        provider = create_provider(cleaned_data=provider_data())
        reply = services.test_provider(provider=provider, actor=self.admin)
        self.assertIn("Fake assistant reply", reply.text)
        usage = UsageEvent.objects.get(source=UsageEvent.Source.ADMIN_TEST)
        self.assertEqual(usage.outcome, UsageEvent.Outcome.SUCCESS)
        self.assertEqual(usage.provider, provider)
        self.assertEqual(usage.actor, self.admin)
        self.assertGreater(usage.tokens_in, 0)
        self.assertTrue(
            AuditLog.objects.filter(
                action="assistant.provider_tested",
                object_id=str(provider.pk),
                change_summary__ok=True,
            ).exists()
        )

    def test_a_kind_with_no_adapter_is_refused_and_recorded(self):
        provider = create_provider(cleaned_data=provider_data())
        # Bypass the form's choices to prove the registry guard, not the widget.
        ProviderConfig.objects.filter(pk=provider.pk).update(kind="openai")
        provider.refresh_from_db()
        with self.assertRaises(ProviderUnavailable):
            services.test_provider(provider=provider, actor=self.admin)
        usage = UsageEvent.objects.get(source=UsageEvent.Source.ADMIN_TEST)
        self.assertEqual(usage.outcome, UsageEvent.Outcome.ERROR)
        self.assertNotIn("sk-", usage.detail)
