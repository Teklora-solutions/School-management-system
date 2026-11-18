"""
apps/assistant/crypto.py
------------------------
Encryption for provider API keys at rest.

The encryption key never lives in the database. It is read from the
``ASSISTANT_ENCRYPTION_KEY`` environment variable: one or more Fernet keys
separated by commas, newest first. Encryption always uses the first key;
decryption tries every key in turn. That is what makes rotation possible
without re-saving every provider: add the new key in front, let old ciphertext
keep decrypting with the key behind it, and remove the old one once the rows
have been re-saved.

This module deliberately contains no logging. A plaintext key exists only for
the duration of the call that needs it.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings


class EncryptionUnavailable(RuntimeError):
    """No usable key is configured on this server."""


class DecryptionFailed(RuntimeError):
    """The stored ciphertext cannot be opened with any configured key."""


def _configured_keys() -> list[str]:
    raw = getattr(settings, "ASSISTANT_ENCRYPTION_KEY", "") or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


def is_configured() -> bool:
    """True when at least one encryption key is present in the environment."""
    return bool(_configured_keys())


def _multi() -> MultiFernet:
    keys = _configured_keys()
    if not keys:
        raise EncryptionUnavailable(
            "No encryption key is configured. Set ASSISTANT_ENCRYPTION_KEY."
        )
    try:
        fernets = [Fernet(key.encode()) for key in keys]
    except (TypeError, ValueError) as exc:
        # The message names the setting, never the value.
        raise EncryptionUnavailable(
            "ASSISTANT_ENCRYPTION_KEY is not a valid Fernet key."
        ) from exc
    return MultiFernet(fernets)


def encrypt(plaintext: str) -> str:
    """Return an opaque token for ``plaintext`` using the current (first) key."""
    if not plaintext:
        raise ValueError("Refusing to encrypt an empty value.")
    return _multi().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Open a token produced by :func:`encrypt`, using any configured key."""
    if not token:
        raise DecryptionFailed("There is no stored key to read.")
    try:
        return _multi().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionFailed(
            "The stored key cannot be read with the configured encryption key."
        ) from exc


def last_four(secret: str) -> str:
    """The only fragment of an API key that is ever stored or displayed."""
    return secret.strip()[-4:]
