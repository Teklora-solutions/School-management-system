"""
apps/assistant/services.py
--------------------------
Writes for assistant configuration and the one outbound path that exists in
this build: the admin's connection test, which uses the fake provider.

Rules that live here, not in the views:
  * a provider key is encrypted before it is stored and only its last four
    characters are kept in the clear;
  * a blank key on an edit means "keep the one already saved";
  * every save is audit-logged by field name, never by secret value;
  * usage is written for every gateway call, successful or not.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.core.audit_log import log_action

from . import crypto, selectors
from .models import AssistantSettings, ProviderConfig, UsageEvent
from .providers import ChatMessage, ProviderUnavailable, build_provider

SETTINGS_FIELDS = (
    "enabled",
    "default_provider",
    "house_rules",
    "monthly_budget_ksh",
    "daily_message_limit",
    "handoff_email",
)
PROVIDER_FIELDS = ("name", "kind", "model_name", "base_url", "timeout_seconds", "is_active")


def _clean_key(cleaned_data: dict[str, Any]) -> str:
    return str(cleaned_data.pop("api_key", "") or "").strip()


def _compare(stored, field: str, cleaned_data: dict[str, Any]) -> bool:
    new = cleaned_data[field]
    if field == "default_provider":
        new = getattr(new, "pk", None)
    old = getattr(stored, field, None) if stored is not None else None
    if field == "default_provider":
        old = getattr(stored, "default_provider_id", None) if stored is not None else None
    return new != old


def update_assistant_settings(
    *,
    instance: AssistantSettings,
    cleaned_data: dict[str, Any],
    actor=None,
    request=None,
) -> AssistantSettings:
    """Save the singleton and audit the names of fields that actually changed."""
    stored = AssistantSettings.objects.filter(pk=instance.pk).first()
    changed = [
        field
        for field in SETTINGS_FIELDS
        if field in cleaned_data and _compare(stored, field, cleaned_data)
    ]
    instance.save()
    if changed:
        log_action(
            actor=actor,
            action="assistant.settings_updated",
            content_type="assistant.AssistantSettings",
            object_id=instance.pk,
            change_summary={"fields": changed},
            request=request,
        )
    return instance


@transaction.atomic
def create_provider(*, cleaned_data: dict[str, Any], actor=None, request=None) -> ProviderConfig:
    api_key = _clean_key(cleaned_data)
    provider = ProviderConfig(
        **{field: cleaned_data[field] for field in PROVIDER_FIELDS if field in cleaned_data}
    )
    if api_key:
        # Raises EncryptionUnavailable when the server has no key configured.
        provider.api_key_ciphertext = crypto.encrypt(api_key)
        provider.api_key_last4 = crypto.last_four(api_key)
    provider.save()
    log_action(
        actor=actor,
        action="assistant.provider_created",
        content_type="assistant.ProviderConfig",
        object_id=provider.pk,
        change_summary={
            "name": provider.name,
            "kind": provider.kind,
            "key_set": provider.has_key,
        },
        request=request,
    )
    return provider


@transaction.atomic
def update_provider(
    *,
    instance: ProviderConfig,
    cleaned_data: dict[str, Any],
    actor=None,
    request=None,
) -> ProviderConfig:
    api_key = _clean_key(cleaned_data)
    stored = ProviderConfig.objects.filter(pk=instance.pk).first()
    changed = [
        field
        for field in PROVIDER_FIELDS
        if field in cleaned_data and getattr(stored, field) != cleaned_data[field]
    ]
    instance.save()
    key_updated = False
    if api_key:
        instance.api_key_ciphertext = crypto.encrypt(api_key)
        instance.api_key_last4 = crypto.last_four(api_key)
        instance.save(update_fields=["api_key_ciphertext", "api_key_last4", "updated_at"])
        key_updated = True
    if changed or key_updated:
        log_action(
            actor=actor,
            action="assistant.provider_updated",
            content_type="assistant.ProviderConfig",
            object_id=instance.pk,
            change_summary={
                "fields": changed,
                "key_updated": key_updated,
                "key_last4": instance.api_key_last4,
            },
            request=request,
        )
    return instance


def test_provider(*, provider: ProviderConfig, actor=None, request=None):
    """
    Send one canned prompt through the gateway and record what happened.

    Only the fake provider can answer in this build; the registry refuses any
    other kind with a clear message rather than attempting a network call.
    """
    messages = [
        ChatMessage(role="user", content="Say hello to confirm the gateway works."),
    ]
    try:
        reply = build_provider(kind=provider.kind, model_name=provider.model_name).generate(
            messages
        )
    except ProviderUnavailable as exc:
        UsageEvent.objects.create(
            provider=provider,
            provider_label=provider.name,
            actor=actor,
            source=UsageEvent.Source.ADMIN_TEST,
            outcome=UsageEvent.Outcome.ERROR,
            detail=str(exc)[:200],
        )
        log_action(
            actor=actor,
            action="assistant.provider_tested",
            content_type="assistant.ProviderConfig",
            object_id=provider.pk,
            change_summary={"ok": False},
            request=request,
        )
        raise

    UsageEvent.objects.create(
        provider=provider,
        provider_label=provider.name,
        actor=actor,
        source=UsageEvent.Source.ADMIN_TEST,
        tokens_in=reply.tokens_in,
        tokens_out=reply.tokens_out,
        estimated_cost_ksh=0,
        outcome=UsageEvent.Outcome.SUCCESS,
    )
    log_action(
        actor=actor,
        action="assistant.provider_tested",
        content_type="assistant.ProviderConfig",
        object_id=provider.pk,
        change_summary={
            "ok": True,
            "tokens_in": reply.tokens_in,
            "tokens_out": reply.tokens_out,
        },
        request=request,
    )
    return reply


def active_provider() -> ProviderConfig | None:
    return selectors.active_provider()
