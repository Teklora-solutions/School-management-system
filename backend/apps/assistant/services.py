"""
apps/assistant/services.py
--------------------------
All writes for assistant configuration, the provider connection test and the
admin test console. Permissions are checked in the view and re-checked by the
caller's role in the screens; secrets never reach the audit log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.conf import settings as django_settings
from django.db import transaction

from apps.core.audit_log import log_action

from . import crypto, gateway, limits, selectors
from .guardrails import build_messages, build_safe_reply, mask_text, screen
from .models import (
    AssistantSettings,
    CustomGuardrailRule,
    ProviderConfig,
    UsageEvent,
)
from .providers import registry
from .providers.base import ChatMessage, ProviderError

SETTINGS_FIELDS = (
    "enabled",
    "public_enabled",
    "signed_in_enabled",
    "student_tutor_enabled",
    "default_provider",
    "house_rules",
    "monthly_budget_ksh",
    "usd_to_ksh",
    "daily_message_limit",
    "public_visitor_daily_limit",
    "public_ip_daily_limit",
    "public_site_daily_limit",
    "safeguarding_contact_name",
    "safeguarding_contact_phone",
    "safeguarding_contact_email",
    "escalation_minutes",
    "working_hours_start",
    "working_hours_end",
    "alert_retention_months",
    "handoff_email",
    "consent_approved",
    "consent_approved_by",
    "consent_approved_on",
)
PROVIDER_FIELDS = (
    "name",
    "kind",
    "model_name",
    "fast_model_name",
    "embedding_model_name",
    "base_url",
    "timeout_seconds",
    "is_active",
    "priority",
    "input_usd_per_1m",
    "output_usd_per_1m",
    "embedding_usd_per_1m",
    "monthly_budget_ksh",
    "region_note",
)


class ProviderInUse(RuntimeError):
    """The provider cannot be deleted without leaving the school broken."""


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
    *, instance: AssistantSettings, cleaned_data: dict[str, Any], actor=None, request=None
) -> AssistantSettings:
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
        provider.api_key_ciphertext = crypto.encrypt(api_key)
        provider.api_key_last4 = crypto.last_four(api_key)
    provider.save()
    log_action(
        actor=actor,
        action="assistant.provider_created",
        content_type="assistant.ProviderConfig",
        object_id=provider.pk,
        change_summary={"name": provider.name, "kind": provider.kind, "key_set": provider.has_key},
        request=request,
    )
    return provider


@transaction.atomic
def update_provider(
    *, instance: ProviderConfig, cleaned_data: dict[str, Any], actor=None, request=None
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


@transaction.atomic
def delete_provider(*, provider: ProviderConfig, actor=None, request=None) -> None:
    settings_row = AssistantSettings.load()
    if settings_row.default_provider_id == provider.pk:
        raise ProviderInUse("Choose another default provider before deleting this one.")
    if provider.is_active and ProviderConfig.objects.filter(is_active=True).exclude(pk=provider.pk).count() == 0:
        raise ProviderInUse("Keep at least one active provider, or switch the assistant off first.")
    name = provider.name
    provider.delete()
    log_action(
        actor=actor,
        action="assistant.provider_deleted",
        content_type="assistant.ProviderConfig",
        object_id="",
        change_summary={"name": name},
        request=request,
    )


def test_provider(*, provider: ProviderConfig, actor=None, request=None):
    """
    A tiny harmless request through the real adapter. Uses the gateway so the
    same fallback/usage/breaker code is exercised; a single provider is passed.
    """
    messages = [ChatMessage(role="user", content="Reply with the word hello.")]
    result = gateway.call_chain(
        [provider],
        messages,
        actor=actor,
        profile=UsageEvent.Profile.CONSOLE,
        max_tokens=16,
    )
    log_action(
        actor=actor,
        action="assistant.provider_tested",
        content_type="assistant.ProviderConfig",
        object_id=provider.pk,
        change_summary={"ok": True, "provider": provider.name},
        request=request,
    )
    return result.reply


@dataclass
class ConsoleResult:
    question: str
    masked_question: str
    mask_counts: dict[str, int] = field(default_factory=dict)
    rules_fired: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    safe_reply: str = ""
    provider_name: str = ""
    answer: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    provider_called: bool = False
    error: str = ""


def run_console(*, question: str, provider_id: int | None, actor=None) -> ConsoleResult:
    """
    The admin test console. No real user data, no alert rows are created, and
    the safeguarding screening still runs first. The console is exempt from
    budget and rate limits so a provider can be checked before go-live; its
    usage is still recorded.
    """
    settings_row = AssistantSettings.load()
    custom = list(selectors.custom_rules_active())
    screening = screen(question, custom)
    masked = mask_text(question)

    if screening.triggered:
        return ConsoleResult(
            question=question,
            masked_question=masked.text,
            mask_counts=masked.counts,
            rules_fired=screening.rule_ids,
            categories=screening.categories,
            safe_reply=build_safe_reply(settings_row),
            provider_name="(not called)",
        )

    provider = None
    if provider_id:
        provider = ProviderConfig.objects.filter(pk=provider_id, is_active=True).first()
    else:
        ordered = selectors.providers_in_order()
        provider = ordered[0] if ordered else None
    if provider is None:
        return ConsoleResult(
            question=question,
            masked_question=masked.text,
            mask_counts=masked.counts,
            error="No active provider is configured. Add one first.",
        )

    messages = build_messages(
        school_name=getattr(django_settings, "SCHOOL_NAME", "the school"),
        house_rules=settings_row.house_rules,
        question=masked.text,
    )
    try:
        result = gateway.call_chain(
            [provider],
            messages,
            actor=actor,
            profile=UsageEvent.Profile.CONSOLE,
            max_tokens=300,
        )
    except gateway.AllProvidersUnavailable as exc:
        return ConsoleResult(
            question=question,
            masked_question=masked.text,
            mask_counts=masked.counts,
            provider_name=provider.name,
            error=getattr(exc, "detail", "") or str(exc),
        )

    return ConsoleResult(
        question=question,
        masked_question=masked.text,
        mask_counts=masked.counts,
        provider_name=provider.name,
        answer=result.reply.text,
        latency_ms=result.latency_ms,
        tokens_in=result.reply.tokens_in,
        tokens_out=result.reply.tokens_out,
        provider_called=True,
    )


def provider_testable(provider: ProviderConfig) -> bool:
    try:
        registry.build(provider, purpose="chat")
        return True
    except ProviderError:
        return False


def active_provider() -> ProviderConfig | None:
    return selectors.active_provider()
