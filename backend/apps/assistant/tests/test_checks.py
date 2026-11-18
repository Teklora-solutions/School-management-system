"""
apps/assistant/tests/test_checks.py
-----------------------------------
The startup refusal when the encryption key is missing: no weak default, no
plaintext fallback, and a message that names the setting.
"""

from __future__ import annotations

from django.test import TestCase, override_settings

from apps.assistant.checks import assistant_encryption_key_check
from apps.assistant.models import AssistantSettings
from apps.assistant.services import create_provider


class EncryptionKeyCheckTests(TestCase):
    def test_a_configured_key_passes(self):
        self.assertEqual(assistant_encryption_key_check(None), [])

    @override_settings(ASSISTANT_ENCRYPTION_KEY="")
    def test_a_fresh_install_without_a_key_passes(self):
        # No assistant row, no provider: the rest of the site must still start.
        AssistantSettings.objects.all().delete()
        self.assertEqual(assistant_encryption_key_check(None), [])

    def test_a_saved_provider_key_without_a_server_key_refuses_to_start(self):
        create_provider(
            cleaned_data={
                "name": "Main",
                "kind": "fake",
                "model_name": "fake",
                "api_key": "sk-test-1234567890-wxyz",
            }
        )
        with self.settings(ASSISTANT_ENCRYPTION_KEY=""):
            errors = assistant_encryption_key_check(None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "assistant.E001")
        self.assertIn("ASSISTANT_ENCRYPTION_KEY", errors[0].msg)
        self.assertIn("assistant_make_key", errors[0].hint)

    @override_settings(ASSISTANT_ENCRYPTION_KEY="")
    def test_an_enabled_assistant_without_a_key_refuses_to_start(self):
        AssistantSettings.objects.update_or_create(
            pk=AssistantSettings.SINGLETON_PK, defaults={"enabled": True}
        )
        errors = assistant_encryption_key_check(None)
        self.assertEqual([error.id for error in errors], ["assistant.E001"])
