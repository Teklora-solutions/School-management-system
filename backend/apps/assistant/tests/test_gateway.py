"""
apps/assistant/tests/test_gateway.py
------------------------------------
The outbound path with the fake provider only. No test here touches a network
or needs a real key.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase

from apps.assistant import gateway, limits
from apps.assistant.models import AssistantSettings, ProviderConfig, UsageEvent
from apps.assistant.providers import ChatMessage
from apps.dashboards.tests.fixtures import make_user

NO_SLEEP = lambda seconds: None  # noqa: E731 - keeps retry tests fast


def make_provider(name="Fake", model="fake", **overrides):
    data = {
        "name": name,
        "kind": ProviderConfig.Kind.FAKE,
        "model_name": model,
        "timeout_seconds": 5,
        "is_active": True,
    }
    data.update(overrides)
    return ProviderConfig.objects.create(**data)


def messages():
    return [ChatMessage(role="user", content="Hello school")]


class GatewaySuccessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.provider = make_provider()

    def test_fake_success_records_one_usage_row(self):
        result = gateway.call_chain([self.provider], messages(), sleep=NO_SLEEP)
        self.assertIn("Fake assistant reply", result.reply.text)
        self.assertFalse(result.used_fallback)
        row = UsageEvent.objects.get()
        self.assertEqual(row.outcome, UsageEvent.Outcome.SUCCESS)
        self.assertEqual(row.provider, self.provider)
        self.assertGreater(row.tokens_in, 0)

    def test_usage_never_contains_the_prompt(self):
        gateway.call_chain([self.provider], messages(), sleep=NO_SLEEP)
        row = UsageEvent.objects.get()
        self.assertNotIn("Hello school", row.detail)


class GatewayFailureTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_timeout_is_retried_then_falls_back(self):
        failing = make_provider("Slow", "fake-timeout", priority=1)
        good = make_provider("Good", "fake", priority=2)
        result = gateway.call_chain([failing, good], messages(), sleep=NO_SLEEP)
        self.assertTrue(result.used_fallback)
        self.assertIn("Fake assistant reply", result.reply.text)
        errors = UsageEvent.objects.filter(provider=failing)
        self.assertEqual(errors.count(), gateway.DEFAULT_RETRIES + 1)
        self.assertTrue(all(row.outcome == UsageEvent.Outcome.ERROR for row in errors))

    def test_rate_limit_is_retryable(self):
        provider = make_provider("Busy", "fake-rate-limit")
        with self.assertRaises(gateway.AllProvidersUnavailable):
            gateway.call_chain([provider], messages(), retries=0, sleep=NO_SLEEP)
        row = UsageEvent.objects.get()
        self.assertEqual(row.outcome, UsageEvent.Outcome.ERROR)
        self.assertIn("rate limit", row.detail)

    def test_auth_failure_is_not_retried_but_the_next_provider_is_tried(self):
        bad = make_provider("Bad key", "fake-auth", priority=1)
        good = make_provider("Good", "fake", priority=2)
        gateway.call_chain([bad, good], messages(), sleep=NO_SLEEP)
        self.assertEqual(UsageEvent.objects.filter(provider=bad).count(), 1)
        self.assertEqual(
            UsageEvent.objects.get(provider=good).outcome, UsageEvent.Outcome.SUCCESS
        )

    def test_malformed_reply_is_recorded(self):
        provider = make_provider("Odd", "fake-malformed")
        with self.assertRaises(gateway.AllProvidersUnavailable):
            gateway.call_chain([provider], messages(), retries=0, sleep=NO_SLEEP)
        self.assertEqual(UsageEvent.objects.get().outcome, UsageEvent.Outcome.ERROR)

    def test_all_down_message_is_plain(self):
        provider = make_provider("Odd", "fake-server")
        with self.assertRaises(gateway.AllProvidersUnavailable) as caught:
            gateway.call_chain([provider], messages(), retries=0, sleep=NO_SLEEP)
        self.assertEqual(str(caught.exception), gateway.ALL_DOWN_MESSAGE)
        # The admin screens can still say which part failed.
        self.assertIn("server error", caught.exception.detail.lower())


class CircuitBreakerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.provider = make_provider("Odd", "fake-server")

    def test_three_failures_open_the_circuit_and_skip_the_provider(self):
        for _ in range(gateway.BREAKER_THRESHOLD):
            with self.assertRaises(gateway.AllProvidersUnavailable):
                gateway.call_chain([self.provider], messages(), retries=0, sleep=NO_SLEEP)
        self.assertTrue(gateway.breaker_is_open(self.provider))
        before = UsageEvent.objects.count()
        with self.assertRaises(gateway.AllProvidersUnavailable):
            gateway.call_chain([self.provider], messages(), retries=0, sleep=NO_SLEEP)
        self.assertEqual(UsageEvent.objects.count(), before)

    def test_a_success_clears_the_circuit(self):
        gateway.breaker_record_failure(self.provider)
        gateway.breaker_record_success(self.provider)
        self.assertFalse(gateway.breaker_is_open(self.provider))


class GatewayLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.provider = make_provider()

    def test_the_global_switch_blocks_everything_but_the_console(self):
        with self.assertRaises(gateway.AllProvidersUnavailable):
            gateway.call_chain(
                [self.provider],
                messages(),
                profile=UsageEvent.Profile.PUBLIC,
                sleep=NO_SLEEP,
            )
        self.assertEqual(UsageEvent.objects.count(), 0)

        result = gateway.call_chain(
            [self.provider],
            messages(),
            profile=UsageEvent.Profile.CONSOLE,
            sleep=NO_SLEEP,
        )
        self.assertIn("Fake assistant reply", result.reply.text)

    def test_a_zero_budget_blocks_a_live_profile(self):
        row = AssistantSettings.load()
        row.enabled = True
        row.monthly_budget_ksh = Decimal("0")
        row.save()
        with self.assertRaises(gateway.AllProvidersUnavailable):
            gateway.call_chain(
                [self.provider],
                messages(),
                profile=UsageEvent.Profile.SIGNED_IN,
                sleep=NO_SLEEP,
            )

    def test_a_reached_budget_blocks_a_live_profile(self):
        row = AssistantSettings.load()
        row.enabled = True
        row.monthly_budget_ksh = Decimal("10")
        row.usd_to_ksh = Decimal("130")
        row.save()
        UsageEvent.objects.create(
            source=UsageEvent.Source.CHAT,
            profile=UsageEvent.Profile.SIGNED_IN,
            outcome=UsageEvent.Outcome.SUCCESS,
            estimated_cost_ksh=Decimal("50"),
        )
        with self.assertRaises(gateway.AllProvidersUnavailable):
            gateway.call_chain(
                [self.provider],
                messages(),
                profile=UsageEvent.Profile.SIGNED_IN,
                sleep=NO_SLEEP,
            )

    def test_a_provider_cap_skips_that_provider(self):
        row = AssistantSettings.load()
        row.enabled = True
        row.monthly_budget_ksh = Decimal("100")
        row.usd_to_ksh = Decimal("130")
        row.save()
        capped = make_provider("Capped", monthly_budget_ksh=Decimal("1"))
        UsageEvent.objects.create(
            source=UsageEvent.Source.CHAT,
            profile=UsageEvent.Profile.SIGNED_IN,
            provider=capped,
            outcome=UsageEvent.Outcome.SUCCESS,
            estimated_cost_ksh=Decimal("2"),
        )
        with self.assertRaises(gateway.AllProvidersUnavailable):
            gateway.call_chain(
                [capped],
                messages(),
                profile=UsageEvent.Profile.SIGNED_IN,
                sleep=NO_SLEEP,
            )
        self.assertEqual(UsageEvent.objects.filter(provider=capped).count(), 1)


class CostTests(TestCase):
    def test_cost_uses_the_usd_rate_and_provider_prices(self):
        row = AssistantSettings.load()
        row.usd_to_ksh = Decimal("130")
        row.save()
        provider = make_provider(
            input_usd_per_1m=Decimal("1"),
            output_usd_per_1m=Decimal("2"),
        )
        cost = limits.cost_ksh(provider, row, 1_000_000, 1_000_000)
        self.assertEqual(cost, Decimal("390.0000"))


class DayBoundaryTests(TestCase):
    def test_day_start_is_nairobi_midnight(self):
        start = limits._day_start()
        self.assertEqual(start.timetz().hour, 0)
        self.assertEqual(start.timetz().minute, 0)
        self.assertEqual(
            start.date(), limits.timezone.localdate()
        )
        self.assertIsInstance(start, datetime.datetime)
