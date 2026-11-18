"""
apps/assistant/tests/test_limits.py
-----------------------------------
Daily limits: signed-in per person, and the public visitor-cookie + IP + site
combination. The site ceiling is the real cost safety net.
"""

from __future__ import annotations

from django.core import signing
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from apps.assistant import limits
from apps.assistant.models import AssistantSettings, UsageEvent
from apps.dashboards.tests.fixtures import make_user


def signed_visitor_request(visitor_key="visitor-1", ip="10.0.0.9"):
    request = RequestFactory().get("/")
    # Exactly what response.set_signed_cookie(key, value, salt=VISITOR_SALT) writes.
    request.COOKIES[limits.VISITOR_COOKIE] = signing.get_cookie_signer(
        salt=limits.VISITOR_COOKIE + limits.VISITOR_SALT
    ).sign(visitor_key)
    request.META["REMOTE_ADDR"] = ip
    return request


class SignedInLimitTests(TestCase):
    def setUp(self):
        self.user = make_user("parent", username="assistant_parent")
        self.row = AssistantSettings.load()
        self.row.enabled = True
        self.row.signed_in_enabled = True
        self.row.daily_message_limit = 2
        self.row.save()

    def record(self, outcome=UsageEvent.Outcome.SUCCESS):
        return UsageEvent.objects.create(
            source=UsageEvent.Source.CHAT,
            profile=UsageEvent.Profile.SIGNED_IN,
            actor=self.user,
            outcome=outcome,
        )

    def test_allowed_below_the_limit(self):
        self.record()
        self.assertTrue(limits.signed_in_allowed(self.row, self.user).allowed)

    def test_blocked_at_the_limit(self):
        self.record()
        self.record()
        decision = limits.signed_in_allowed(self.row, self.user)
        self.assertFalse(decision.allowed)
        self.assertIn("message limit", decision.reason)

    def test_errors_do_not_count(self):
        self.record(UsageEvent.Outcome.ERROR)
        self.record(UsageEvent.Outcome.ERROR)
        self.assertTrue(limits.signed_in_allowed(self.row, self.user).allowed)

    def test_zero_limit_means_no_messages(self):
        self.row.daily_message_limit = 0
        self.row.save()
        self.assertFalse(limits.signed_in_allowed(self.row, self.user).allowed)

    def test_switches_off_block(self):
        self.row.signed_in_enabled = False
        self.row.save()
        self.assertFalse(limits.signed_in_allowed(self.row, self.user).allowed)


class PublicLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.row = AssistantSettings.load()
        self.row.enabled = True
        self.row.public_enabled = True
        self.row.public_visitor_daily_limit = 2
        self.row.public_ip_daily_limit = 3
        self.row.public_site_daily_limit = 100
        self.row.save()

    def record_public(self, count=1):
        for _ in range(count):
            UsageEvent.objects.create(
                source=UsageEvent.Source.CHAT,
                profile=UsageEvent.Profile.PUBLIC,
                outcome=UsageEvent.Outcome.SUCCESS,
            )

    def test_a_visitor_cookie_counts_per_visitor(self):
        request = signed_visitor_request("visitor-a")
        self.assertTrue(limits.check_public_rate(request, self.row).allowed)
        limits.record_public_attempt(request)
        limits.record_public_attempt(request)
        self.assertFalse(limits.check_public_rate(request, self.row).allowed)

    def test_two_visitors_on_one_ip_have_their_own_allowance(self):
        first = signed_visitor_request("visitor-a", ip="10.0.0.9")
        second = signed_visitor_request("visitor-b", ip="10.0.0.9")
        limits.record_public_attempt(first)
        limits.record_public_attempt(first)
        self.assertFalse(limits.check_public_rate(first, self.row).allowed)
        self.assertTrue(limits.check_public_rate(second, self.row).allowed)

    def test_the_ip_backstop_blocks_a_cookie_clearer(self):
        for index in range(3):
            limits.record_public_attempt(
                signed_visitor_request(f"visitor-{index}", ip="10.0.0.9")
            )
        fresh = signed_visitor_request("visitor-new", ip="10.0.0.9")
        decision = limits.check_public_rate(fresh, self.row)
        self.assertFalse(decision.allowed)
        self.assertIn("network", decision.reason)

    def test_the_site_ceiling_stops_everyone(self):
        self.row.public_site_daily_limit = 2
        self.row.save()
        self.record_public(2)
        decision = limits.check_public_rate(signed_visitor_request(), self.row)
        self.assertFalse(decision.allowed)
        self.assertIn("site-wide", decision.reason)

    def test_public_switch_off_blocks(self):
        self.row.public_enabled = False
        self.row.save()
        self.assertFalse(
            limits.check_public_rate(signed_visitor_request(), self.row).allowed
        )

    def test_a_tampered_cookie_is_replaced_not_trusted(self):
        request = RequestFactory().get("/")
        request.COOKIES[limits.VISITOR_COOKIE] = "forged:1:signature"
        key, is_new = limits.get_visitor_key(request)
        self.assertTrue(is_new)
        self.assertNotEqual(key, "forged")

    def test_ip_comes_from_the_forwarded_header_behind_the_proxy(self):
        request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="41.1.2.3, 10.0.0.1")
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        self.assertEqual(limits.client_ip(request), "41.1.2.3")
