"""apps/assistant/selectors.py — read-only queries the assistant screens use."""

from __future__ import annotations

from .models import (
    AssistantSettings,
    AssistantJobFailure,
    CustomGuardrailRule,
    ProviderConfig,
    SafeguardingAlert,
    UsageEvent,
)


def assistant_settings() -> AssistantSettings:
    return AssistantSettings.load()


def provider_list():
    return ProviderConfig.objects.order_by("priority", "name")


def providers_in_order():
    """
    The fallback order: the configured default first, then everything else by
    priority and name. Inactive providers are excluded.
    """
    settings_row = assistant_settings()
    providers = list(ProviderConfig.objects.filter(is_active=True).order_by("priority", "name"))
    default_id = settings_row.default_provider_id
    if default_id:
        providers.sort(key=lambda provider: 0 if provider.pk == default_id else 1)
    return providers


def active_provider() -> ProviderConfig | None:
    ordered = providers_in_order()
    return ordered[0] if ordered else None


def latest_usage(limit: int = 10):
    return UsageEvent.objects.select_related("provider", "actor").order_by("-created_at")[:limit]


def usage_for_month():
    return UsageEvent.objects.order_by("-created_at")


def custom_rules_active():
    return CustomGuardrailRule.objects.filter(is_active=True).order_by("name")


def custom_rules_all():
    return CustomGuardrailRule.objects.order_by("-is_active", "name")


def open_alerts():
    return SafeguardingAlert.objects.filter(
        status=SafeguardingAlert.Status.OPEN
    ).select_related("user", "acknowledged_by")


def all_alerts(limit: int = 100):
    return SafeguardingAlert.objects.select_related("user", "acknowledged_by")[:limit]


def recent_failures(limit: int = 50):
    return AssistantJobFailure.objects.filter(resolved_at__isnull=True)[:limit]
