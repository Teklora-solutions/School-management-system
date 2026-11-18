"""
apps/assistant/selectors.py
---------------------------
Read-only queries the admin screens use.
"""

from __future__ import annotations

from .models import AssistantSettings, ProviderConfig, UsageEvent


def assistant_settings() -> AssistantSettings:
    return AssistantSettings.load()


def provider_list():
    return ProviderConfig.objects.order_by("name")


def active_provider() -> ProviderConfig | None:
    """The provider a new conversation would use, or None when none is usable."""
    settings_row = assistant_settings()
    provider = settings_row.default_provider
    if provider is not None and provider.is_active:
        return provider
    return None


def latest_usage(limit: int = 10):
    return UsageEvent.objects.select_related("provider", "actor").order_by(
        "-created_at"
    )[:limit]
