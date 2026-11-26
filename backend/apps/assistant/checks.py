"""
apps/assistant/checks.py
------------------------
A startup check for the one configuration the assistant cannot run without.

If a provider key is stored (or the assistant is switched on) and
``ASSISTANT_ENCRYPTION_KEY`` is missing, ``manage.py check`` fails with
``assistant.E001`` and the process refuses to start. There is no weak default
and no plaintext fallback.

On a fresh install (no assistant row, no providers) the check is silent, so the
rest of the site starts normally before the key is configured.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, register


@register()
def assistant_encryption_key_check(app_configs, **kwargs):
    from . import crypto
    from .models import AssistantSettings, ProviderConfig

    if crypto.is_configured():
        try:
            crypto._multi()
            return []
        except crypto.EncryptionUnavailable as exc:
            return [
                Error(
                    str(exc),
                    hint=(
                        "Run `python manage.py assistant_make_key`, put the printed value in the "
                        "server environment as ASSISTANT_ENCRYPTION_KEY, and restart."
                    ),
                    id="assistant.E001",
                )
            ]

    try:
        has_saved_keys = ProviderConfig.objects.exclude(api_key_ciphertext="").exists()
        enabled = bool(
            AssistantSettings.objects.filter(pk=AssistantSettings.SINGLETON_PK)
            .values_list("enabled", flat=True)
            .first()
        )
    except Exception:
        # Tables not migrated yet: nothing can depend on a saved key.
        return []

    if not has_saved_keys and not enabled:
        return []

    return [
        Error(
            "The AI assistant cannot start safely: ASSISTANT_ENCRYPTION_KEY is not set.",
            hint=(
                "Run `python manage.py assistant_make_key`, put the printed value in the "
                "server environment as ASSISTANT_ENCRYPTION_KEY, and restart. Without it "
                "saved provider keys cannot be decrypted and no assistant call may be made."
            ),
            id="assistant.E001",
        )
    ]
