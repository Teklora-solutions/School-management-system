"""
apps/assistant/gateway.py
-------------------------
The one outbound path: ordered providers, retries, a circuit breaker and a
usage row for every attempt.

No adapter sees a database row beyond the config it is built from; no key is
logged. All failures become ``ProviderError`` with a plain-language message.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from django.core.cache import cache

from . import limits
from .models import UsageEvent
from .providers import registry
from .providers.base import ChatMessage, ErrorKind, ProviderError

BREAKER_THRESHOLD = 3
BREAKER_WINDOW = 60
BREAKER_OPEN_SECONDS = 300
DEFAULT_RETRIES = 2
BACKOFF_SECONDS = (1, 3)

ALL_DOWN_MESSAGE = (
    "I can't answer right now. Please contact the school office and we will help you."
)


class AllProvidersUnavailable(RuntimeError):
    def __init__(self, message: str = ALL_DOWN_MESSAGE, *, detail: str = "") -> None:
        self.safe_message = message
        self.detail = detail
        super().__init__(message)


@dataclass
class GatewayResult:
    reply: object
    provider: object
    attempts: int = 1
    latency_ms: int = 0
    used_fallback: bool = False
    skipped: list[str] = field(default_factory=list)


def _breaker_key(provider) -> str:
    return f"assistant:circuit:{provider.pk}"


def breaker_is_open(provider) -> bool:
    return bool(cache.get(_breaker_key(provider)))


def breaker_record_failure(provider) -> None:
    key = _breaker_key(provider)
    state = cache.get(key) or {"fails": 0, "first": time.time()}
    if time.time() - state["first"] > BREAKER_WINDOW:
        state = {"fails": 0, "first": time.time()}
    state["fails"] += 1
    if state["fails"] >= BREAKER_THRESHOLD:
        cache.set(key, {"open_until": time.time() + BREAKER_OPEN_SECONDS}, BREAKER_OPEN_SECONDS)
    else:
        cache.set(key, state, BREAKER_WINDOW)


def breaker_record_success(provider) -> None:
    cache.delete(_breaker_key(provider))


def _record_usage(
    *,
    provider,
    actor,
    profile,
    source,
    model_name,
    reply=None,
    error: ProviderError | None = None,
    latency_ms=0,
    detail="",
):
    settings_row = _settings_row()
    tokens_in = getattr(reply, "tokens_in", 0) if reply else 0
    tokens_out = getattr(reply, "tokens_out", 0) if reply else 0
    UsageEvent.objects.create(
        provider=provider,
        provider_label=getattr(provider, "name", "") or "",
        actor=actor,
        source=source,
        profile=profile,
        model_name=model_name or "",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        estimated_cost_ksh=limits.cost_ksh(provider, settings_row, tokens_in, tokens_out),
        latency_ms=max(0, int(latency_ms)),
        outcome=UsageEvent.Outcome.ERROR if error else UsageEvent.Outcome.SUCCESS,
        detail=(str(error.safe_message) if error else detail)[:200],
    )


def _settings_row():
    from .models import AssistantSettings

    return AssistantSettings.load()


def call_chain(
    providers,
    messages: list[ChatMessage],
    *,
    actor=None,
    profile: str = UsageEvent.Profile.CONSOLE,
    max_tokens: int = 400,
    retries: int = DEFAULT_RETRIES,
    sleep=time.sleep,
) -> GatewayResult:
    """
    Try each provider in order. Retryable failures retry with backoff, then the
    next provider is tried. An open circuit skips a provider without calling it.
    """
    skipped: list[str] = []
    settings_row = _settings_row()
    last_error: ProviderError | None = None

    if profile != UsageEvent.Profile.CONSOLE:
        if not settings_row.enabled:
            raise AllProvidersUnavailable("The assistant is switched off.")
        budget = limits.check_budget(
            settings_row,
            providers[0] if providers else None,
            profile=profile,
            prompt_chars=sum(len(message.content) for message in messages),
            max_tokens=max_tokens,
        )
        if not budget.allowed:
            raise AllProvidersUnavailable(budget.reason)

    for index, provider in enumerate(providers):
        label = getattr(provider, "name", "provider")

        if breaker_is_open(provider):
            skipped.append(f"{label}: circuit open")
            continue
        if limits.provider_over_budget(settings_row, provider):
            skipped.append(f"{label}: provider budget reached")
            continue

        for attempt in range(retries + 1):
            started = time.monotonic()
            try:
                adapter = registry.build(provider, purpose="chat")
                reply = adapter.chat(
                    messages,
                    model=registry.model_for(provider, "chat"),
                    timeout=provider.timeout_seconds or 30,
                    max_tokens=max_tokens,
                )
            except ProviderError as exc:
                elapsed = (time.monotonic() - started) * 1000
                _record_usage(
                    provider=provider,
                    actor=actor,
                    profile=profile,
                    source=UsageEvent.Source.ADMIN_TEST
                    if profile == UsageEvent.Profile.CONSOLE
                    else UsageEvent.Source.CHAT,
                    model_name=registry.model_for(provider, "chat"),
                    error=exc,
                    latency_ms=elapsed,
                )
                last_error = exc
                breaker_record_failure(provider)
                if exc.retryable and attempt < retries:
                    sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
                    continue
                break
            except Exception:  # defensive: adapters should only raise ProviderError
                elapsed = (time.monotonic() - started) * 1000
                error = ProviderError(ErrorKind.MALFORMED)
                _record_usage(
                    provider=provider,
                    actor=actor,
                    profile=profile,
                    source=UsageEvent.Source.ADMIN_TEST
                    if profile == UsageEvent.Profile.CONSOLE
                    else UsageEvent.Source.CHAT,
                    model_name=registry.model_for(provider, "chat"),
                    error=error,
                    latency_ms=elapsed,
                )
                last_error = error
                breaker_record_failure(provider)
                break
            else:
                elapsed = (time.monotonic() - started) * 1000
                breaker_record_success(provider)
                _record_usage(
                    provider=provider,
                    actor=actor,
                    profile=profile,
                    source=UsageEvent.Source.ADMIN_TEST
                    if profile == UsageEvent.Profile.CONSOLE
                    else UsageEvent.Source.CHAT,
                    model_name=registry.model_for(provider, "chat"),
                    reply=reply,
                    latency_ms=elapsed,
                )
                return GatewayResult(
                    reply=reply,
                    provider=provider,
                    attempts=attempt + 1,
                    latency_ms=int(elapsed),
                    used_fallback=index > 0,
                    skipped=skipped,
                )

    if last_error is not None:
        # Users get one fixed message; the admin screens read ``detail``.
        raise AllProvidersUnavailable(detail=last_error.safe_message)
    raise AllProvidersUnavailable()
