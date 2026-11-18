"""
apps/assistant/limits.py
------------------------
Budgets and rate limits, checked before any provider is called.

Money
  Provider prices are USD per million tokens (as providers publish them).
  ``AssistantSettings.usd_to_ksh`` converts to the budget currency. If the
  rate is 0, estimated costs are 0 and a positive budget can never be
  consumed correctly — the settings form refuses that combination.

Public limits
  A visitor is identified by a signed cookie PLUS the IP address. Per-IP alone
  breaks schools and mobile networks where many families share one address;
  the site-wide cap and the budget are the real safety net. All three public
  limits are admin-editable.
"""

from __future__ import annotations

import datetime
import secrets
from dataclasses import dataclass
from decimal import Decimal

from django.core.cache import cache
from django.core.signing import BadSignature, SignatureExpired
from django.utils import timezone

from .models import UsageEvent

VISITOR_COOKIE = "assistant_visitor"
VISITOR_SALT = "assistant.visitor.v1"
VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


def _day_start():
    now = timezone.localtime()
    return timezone.make_aware(datetime.datetime.combine(now.date(), datetime.time.min))


def _month_start():
    now = timezone.localtime()
    return timezone.make_aware(datetime.datetime.combine(now.date().replace(day=1), datetime.time.min))


def cost_ksh(provider, settings_row, tokens_in: int, tokens_out: int) -> Decimal:
    if provider is None:
        return Decimal("0")
    in_usd = Decimal(tokens_in or 0) / Decimal(1_000_000) * (provider.input_usd_per_1m or 0)
    out_usd = Decimal(tokens_out or 0) / Decimal(1_000_000) * (provider.output_usd_per_1m or 0)
    rate = settings_row.usd_to_ksh or 0
    return ((in_usd + out_usd) * rate).quantize(Decimal("0.0001"))


def embedding_cost_ksh(provider, settings_row, tokens: int) -> Decimal:
    """Embedding prices are per million tokens, like chat prices."""
    if provider is None:
        return Decimal("0")
    usd = Decimal(tokens or 0) / Decimal(1_000_000) * (provider.embedding_usd_per_1m or 0)
    rate = settings_row.usd_to_ksh or 0
    return (usd * rate).quantize(Decimal("0.0001"))


def estimated_embedding_cost(provider, settings_row, prompt_chars: int) -> Decimal:
    tokens = max(1, prompt_chars // 4)
    return embedding_cost_ksh(provider, settings_row, tokens)


def estimated_chat_cost(provider, settings_row, prompt_chars: int, max_tokens: int) -> Decimal:
    """A cautious pre-call reservation so a budget cannot be blown by one call."""
    prompt_tokens = max(1, prompt_chars // 4)
    return cost_ksh(provider, settings_row, prompt_tokens, max_tokens)


def month_spend_ksh(*, provider=None) -> Decimal:
    rows = UsageEvent.objects.filter(created_at__gte=_month_start())
    if provider is not None:
        rows = rows.filter(provider=provider)
    total = Decimal("0")
    for value in rows.values_list("estimated_cost_ksh", flat=True):
        total += value or Decimal("0")
    return total.quantize(Decimal("0.0001"))


def check_budget(settings_row, provider, *, profile: str, prompt_chars: int = 800, max_tokens: int = 400) -> Decision:
    if profile == UsageEvent.Profile.CONSOLE:
        # The admin test console is exempt so a provider can be verified before
        # the assistant is switched on. Its usage is still recorded.
        return Decision(True)

    budget = settings_row.monthly_budget_ksh or Decimal("0")
    if budget <= 0:
        return Decision(False, "The assistant has no spending budget set.")
    spent = month_spend_ksh()
    reserve = estimated_chat_cost(provider, settings_row, prompt_chars, max_tokens)
    if spent + reserve > budget:
        return Decision(False, "The assistant has reached its monthly budget.")

    if provider is not None and provider.monthly_budget_ksh is not None:
        provider_spent = month_spend_ksh(provider=provider)
        if provider_spent + reserve > provider.monthly_budget_ksh:
            return Decision(False, f"“{provider.name}” has reached its own monthly budget.")
    return Decision(True)


def provider_over_budget(settings_row, provider) -> bool:
    if provider is None or provider.monthly_budget_ksh is None:
        return False
    return month_spend_ksh(provider=provider) >= provider.monthly_budget_ksh


def check_embedding_budget(settings_row, provider, *, prompt_chars: int = 800) -> Decision:
    """
    Ingestion pays the same budget. It does not require the assistant to be
    switched on: an admin may prepare the base before go-live.
    """
    budget = settings_row.monthly_budget_ksh or Decimal("0")
    if budget <= 0:
        return Decision(False, "The assistant has no spending budget set.")
    spent = month_spend_ksh()
    reserve = estimated_embedding_cost(provider, settings_row, prompt_chars)
    if spent + reserve > budget:
        return Decision(False, "The assistant has reached its monthly budget.")

    if provider is not None and provider.monthly_budget_ksh is not None:
        provider_spent = month_spend_ksh(provider=provider)
        if provider_spent + reserve > provider.monthly_budget_ksh:
            return Decision(False, f"“{provider.name}” has reached its own monthly budget.")
    return Decision(True)


def signed_in_allowed(settings_row, user) -> Decision:
    if not (settings_row.enabled and settings_row.signed_in_enabled):
        return Decision(False, "The signed-in assistant is switched off.")
    limit = settings_row.daily_message_limit or 0
    if limit == 0:
        return Decision(False, "The daily message limit is set to zero.")
    used = UsageEvent.objects.filter(
        actor=user,
        profile=UsageEvent.Profile.SIGNED_IN,
        outcome=UsageEvent.Outcome.SUCCESS,
        created_at__gte=_day_start(),
    ).count()
    if used >= limit:
        return Decision(False, "You have reached today's message limit.")
    return Decision(True)


def get_visitor_key(request) -> tuple[str, bool]:
    """(key, is_new). The caller sets the cookie when is_new is True."""
    try:
        key = request.get_signed_cookie(VISITOR_COOKIE, salt=VISITOR_SALT, default="")
    except (BadSignature, SignatureExpired):
        key = ""
    if key:
        return key, False
    return secrets.token_hex(16), True


def client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _counter_key(scope: str, value: str) -> str:
    day = timezone.localdate().isoformat()
    return f"assistant:rate:{scope}:{value}:{day}"


def _count(scope: str, value: str) -> int:
    return int(cache.get(_counter_key(scope, value), 0) or 0)


def _increment(scope: str, value: str) -> None:
    key = _counter_key(scope, value)
    cache.add(key, 0, timeout=60 * 60 * 26)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=60 * 60 * 26)


def site_questions_today() -> int:
    return UsageEvent.objects.filter(
        profile=UsageEvent.Profile.PUBLIC,
        outcome=UsageEvent.Outcome.SUCCESS,
        created_at__gte=_day_start(),
    ).count()


def check_public_rate(request, settings_row) -> Decision:
    if not (settings_row.enabled and settings_row.public_enabled):
        return Decision(False, "The public assistant is switched off.")
    if settings_row.public_site_daily_limit and site_questions_today() >= settings_row.public_site_daily_limit:
        return Decision(False, "The public assistant has reached today's site-wide limit.")

    visitor_key, _ = get_visitor_key(request)
    visitor_limit = settings_row.public_visitor_daily_limit or 0
    if visitor_limit == 0:
        return Decision(False, "The public assistant is unavailable today.")
    if _count("visitor", visitor_key) >= visitor_limit:
        return Decision(False, "You have reached today's question limit.")

    ip_limit = settings_row.public_ip_daily_limit or 0
    ip = client_ip(request)
    if ip_limit and ip and _count("ip", ip) >= ip_limit:
        return Decision(False, "This network has reached today's question limit.")
    return Decision(True)


def record_public_attempt(request) -> None:
    visitor_key, _ = get_visitor_key(request)
    _increment("visitor", visitor_key)
    ip = client_ip(request)
    if ip:
        _increment("ip", ip)
