"""
apps/assistant/tests/test_crypto.py
-----------------------------------
The properties the key format must hold: a round trip, outright refusal when
the server has no key, and rotation without losing old ciphertext.
"""

from __future__ import annotations

from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from apps.assistant import crypto


class CryptoRoundTripTests(SimpleTestCase):
    def test_encrypt_then_decrypt_returns_the_key(self):
        token = crypto.encrypt("sk-live-secret-value")
        self.assertEqual(crypto.decrypt(token), "sk-live-secret-value")

    def test_ciphertext_does_not_contain_the_key(self):
        secret = "sk-live-secret-value"
        self.assertNotIn(secret, crypto.encrypt(secret))

    def test_last_four_is_the_only_fragment_kept(self):
        self.assertEqual(crypto.last_four("  sk-live-secret-abcd "), "abcd")

    @override_settings(ASSISTANT_ENCRYPTION_KEY="")
    def test_no_key_configured_refuses_to_encrypt(self):
        with self.assertRaises(crypto.EncryptionUnavailable):
            crypto.encrypt("sk-live-secret-value")

    @override_settings(ASSISTANT_ENCRYPTION_KEY="")
    def test_no_key_configured_refuses_to_decrypt(self):
        with self.assertRaises(crypto.EncryptionUnavailable):
            crypto.decrypt("any-token")

    def test_a_malformed_token_fails_cleanly(self):
        with self.assertRaises(crypto.DecryptionFailed):
            crypto.decrypt("not-a-fernet-token")


class KeyRotationTests(SimpleTestCase):
    def test_a_new_key_first_still_opens_old_ciphertext(self):
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        with override_settings(ASSISTANT_ENCRYPTION_KEY=old_key):
            old_token = crypto.encrypt("sk-old-secret")

        with override_settings(ASSISTANT_ENCRYPTION_KEY=f"{new_key},{old_key}"):
            self.assertEqual(crypto.decrypt(old_token), "sk-old-secret")
            new_token = crypto.encrypt("sk-new-secret")

        with override_settings(ASSISTANT_ENCRYPTION_KEY=new_key):
            self.assertEqual(crypto.decrypt(new_token), "sk-new-secret")
