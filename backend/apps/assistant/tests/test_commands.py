"""apps/assistant/tests/test_commands.py — key rotation and key generation."""

from __future__ import annotations

import datetime
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.assistant import crypto
from apps.assistant.models import AssistantSettings, ProviderConfig, SafeguardingAlert
from apps.assistant.services import create_provider

KEY = "sk-test-1234567890-wxyz"


class ReencryptCommandTests(TestCase):
    def setUp(self):
        create_provider(
            cleaned_data={
                "name": "Main",
                "kind": "fake",
                "model_name": "fake",
                "api_key": KEY,
            }
        )

    def test_without_apply_nothing_is_written(self):
        provider = ProviderConfig.objects.get()
        before = provider.api_key_ciphertext
        out = StringIO()
        call_command("assistant_reencrypt_keys", stdout=out)
        provider.refresh_from_db()
        self.assertEqual(provider.api_key_ciphertext, before)
        self.assertIn("would be re-encrypted", out.getvalue())

    def test_apply_keeps_the_same_secret(self):
        provider = ProviderConfig.objects.get()
        before = provider.api_key_ciphertext
        out = StringIO()
        call_command("assistant_reencrypt_keys", "--apply", stdout=out)
        provider.refresh_from_db()
        self.assertNotEqual(provider.api_key_ciphertext, before)
        self.assertEqual(crypto.decrypt(provider.api_key_ciphertext), KEY)
        self.assertEqual(provider.api_key_last4, "wxyz")
        self.assertIn("Re-encrypted 1", out.getvalue())

    @override_settings(ASSISTANT_ENCRYPTION_KEY="")
    def test_without_a_key_the_command_refuses(self):
        with self.assertRaises(CommandError):
            call_command("assistant_reencrypt_keys")


class PurgeAlertExcerptsCommandTests(TestCase):
    def setUp(self):
        row = AssistantSettings.load()
        row.alert_retention_months = 12
        row.save()

    def make_closed(self, *, days_ago=400, status=SafeguardingAlert.Status.CLOSED):
        return SafeguardingAlert.objects.create(
            profile="public",
            category="abuse",
            excerpt="He touches me. [PHONE]",
            outcome="Referred to the safeguarding lead.",
            status=status,
            closed_at=timezone.now() - datetime.timedelta(days=days_ago),
        )

    def test_dry_run_reports_without_writing(self):
        alert = self.make_closed()
        out = StringIO()
        call_command("assistant_purge_alert_excerpts", stdout=out)
        alert.refresh_from_db()
        self.assertNotEqual(alert.excerpt, "")
        self.assertIn("would be erased", out.getvalue())

    def test_apply_erases_the_excerpt_and_keeps_the_row(self):
        alert = self.make_closed()
        out = StringIO()
        call_command("assistant_purge_alert_excerpts", "--apply", stdout=out)
        alert.refresh_from_db()
        self.assertEqual(alert.excerpt, "")
        self.assertIsNotNone(alert.excerpt_erased_at)
        self.assertEqual(alert.outcome, "Referred to the safeguarding lead.")
        self.assertIn("Erased 1", out.getvalue())

    def test_zero_months_turns_retention_off(self):
        alert = self.make_closed()
        row = AssistantSettings.load()
        row.alert_retention_months = 0
        row.save()
        out = StringIO()
        call_command("assistant_purge_alert_excerpts", "--apply", stdout=out)
        alert.refresh_from_db()
        self.assertNotEqual(alert.excerpt, "")
        self.assertIn("Retention is off", out.getvalue())

    def test_alert_id_refuses_an_unclosed_alert(self):
        alert = self.make_closed(status=SafeguardingAlert.Status.OPEN, days_ago=0)
        with self.assertRaises(CommandError):
            call_command("assistant_purge_alert_excerpts", "--alert-id", str(alert.pk))

    def test_alert_id_erases_one_closed_alert_early(self):
        alert = self.make_closed(days_ago=1)
        out = StringIO()
        call_command(
            "assistant_purge_alert_excerpts", "--alert-id", str(alert.pk), "--apply", stdout=out
        )
        alert.refresh_from_db()
        self.assertEqual(alert.excerpt, "")
        self.assertIn("record was kept", out.getvalue())
