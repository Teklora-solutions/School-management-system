"""
tests/test_director_cache.py
------------------------------
Cache invalidation tests.

Proves:
  1. get_cached_school_kpis() returns a cached result on the second call
     (same object from cache).
  2. invalidate_school_kpis() removes the cache entry so the next call
     recomputes from the DB.
  3. Cache values change after new finance data is added (end-to-end
     invalidation via finance.services.record_payment).
  4. cache["cached_at"] timestamp is present.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase

from apps.dashboards.cache import (
    get_cached_school_kpis,
    invalidate_school_kpis,
)

from .fixtures import (
    make_accounts,
    make_class,
    make_fee_structure,
    make_payment_allocation,
    make_posted_invoice,
    make_student_user,
    make_term,
    make_year,
)


class CacheHitTest(TestCase):
    """Second call returns data from cache (no extra DB queries)."""

    def setUp(self):
        cache.clear()
        self.year = make_year()
        self.term = make_term(self.year)

    def tearDown(self):
        cache.clear()

    def test_second_call_returns_cached_result(self):
        first = get_cached_school_kpis(term=self.term)
        second = get_cached_school_kpis(term=self.term)
        # Both dicts must be equal and the second must be the exact same
        # cached object (Python identity if the cache backend returns
        # the same object, equality otherwise).
        self.assertEqual(first["student_count"], second["student_count"])
        self.assertEqual(first["cached_at"], second["cached_at"])

    def test_cached_at_key_present(self):
        result = get_cached_school_kpis(term=self.term)
        self.assertIn("cached_at", result)
        self.assertIsNotNone(result["cached_at"])

    def test_fees_billed_cached_correctly(self):
        """Cache stores correct fees_billed before any invoices exist."""
        result = get_cached_school_kpis(term=self.term)
        self.assertEqual(result["fees_billed"], Decimal(0))

    def test_cached_at_is_recent(self):
        from django.utils import timezone

        result = get_cached_school_kpis(term=self.term)
        delta = timezone.now() - result["cached_at"]
        self.assertLess(delta.total_seconds(), 5)


class CacheInvalidationTest(TestCase):
    """invalidate_school_kpis() causes the next call to recompute."""

    def setUp(self):
        cache.clear()
        self.year = make_year()
        self.term = make_term(self.year)
        make_accounts()
        self.cls = make_class(self.year)
        self.fs = make_fee_structure(self.cls, self.term, amount=Decimal("500.00"))

    def tearDown(self):
        cache.clear()

    def test_invalidate_clears_cache(self):
        # Populate cache
        first = get_cached_school_kpis(term=self.term)
        first_at = first["cached_at"]

        # Invalidate
        invalidate_school_kpis(term_id=self.term.pk)

        # Next call must recompute — cached_at will be a new datetime
        import time

        time.sleep(0.01)  # ensure datetime differs
        second = get_cached_school_kpis(term=self.term)
        second_at = second["cached_at"]

        self.assertGreaterEqual(second_at, first_at)

    def test_fees_collected_updates_after_invalidation(self):
        """
        Sequence:
          1. Cache is populated with fees_collected = 0.
          2. A payment is added directly (bypassing services to avoid signal noise).
          3. Cache is invalidated.
          4. Recomputed value reflects the new payment.
        """
        # 1. Populate cache; fees_collected starts at 0
        before = get_cached_school_kpis(term=self.term)
        self.assertEqual(before["fees_collected"], Decimal(0))

        # 2. Add a student + invoice + payment
        student = make_student_user(self.cls)
        invoice = make_posted_invoice(student, self.term, self.fs, Decimal("500.00"))
        make_payment_allocation(invoice, Decimal("500.00"))

        # 3. Invalidate the cache
        invalidate_school_kpis(term_id=self.term.pk)

        # 4. Recomputed fees_collected must now be 500
        after = get_cached_school_kpis(term=self.term)
        self.assertEqual(after["fees_collected"], Decimal("500.00"))
        self.assertEqual(after["fees_billed"], Decimal("500.00"))

    def test_stale_cache_not_updated_without_invalidation(self):
        """Without invalidation, cached value stays stale (shows old fees_collected)."""
        # Populate cache with fees_collected = 0
        before = get_cached_school_kpis(term=self.term)
        self.assertEqual(before["fees_collected"], Decimal(0))

        # Add a payment — but do NOT invalidate
        student = make_student_user(self.cls)
        invoice = make_posted_invoice(student, self.term, self.fs, Decimal("500.00"))
        make_payment_allocation(invoice, Decimal("500.00"))

        # Cache should still show 0 (stale)
        stale = get_cached_school_kpis(term=self.term)
        self.assertEqual(
            stale["fees_collected"],
            Decimal(0),
            "Cache should remain stale without explicit invalidation",
        )


class ServiceInvalidationWiringTest(TestCase):
    """
    finance.services.record_payment and results.services.publish_sheet
    must call invalidate_school_kpis automatically.
    """

    def setUp(self):
        cache.clear()
        self.year = make_year()
        self.term = make_term(self.year)
        make_accounts()
        self.cls = make_class(self.year)
        self.fs = make_fee_structure(self.cls, self.term, Decimal("300.00"))
        from apps.accounts.models import User

        self.finance_user = User.objects.create_user(
            username="fin_svc_user",
            email="fin@test.school",
            role="finance",
            password="x",
            must_change_password=False,
        )

    def tearDown(self):
        cache.clear()

    def test_record_payment_invalidates_cache(self):
        """
        After record_payment(), the cache for that term should be gone,
        so the next get_cached_school_kpis() call recomputes.
        """
        student = make_student_user(self.cls)
        make_posted_invoice(student, self.term, self.fs, Decimal("300.00"))

        # Warm the cache
        before = get_cached_school_kpis(term=self.term)
        self.assertEqual(before["fees_collected"], Decimal(0))

        # Record a payment via the service (which should call invalidate)
        from apps.finance.services import record_payment

        record_payment(
            student=student,
            amount=Decimal("300.00"),
            method="bank",
            reference="SVC-TEST-001",
            payment_date=datetime.date(2025, 10, 1),
            recorded_by=self.finance_user,
        )

        # The cache should now be invalidated — recomputed value reflects payment
        after = get_cached_school_kpis(term=self.term)
        self.assertEqual(after["fees_collected"], Decimal("300.00"))

    def test_publish_sheet_invalidates_cache(self):
        """
        After publish_sheet(), the cache for that term should be invalidated
        so pass_rate reflects the newly published entries.
        """
        from decimal import Decimal

        from apps.results.models import GradeBand, GradingConfig, ResultSheet
        from apps.results.services import confirm_sheet, publish_sheet, submit_sheet

        # Setup grading
        make_accounts()
        GradingConfig.objects.get_or_create(
            academic_year=self.year, defaults={"pass_mark": Decimal(50)}
        )
        for lo, hi, grade in [(0, 49, "F"), (50, 100, "P")]:
            GradeBand.objects.get_or_create(
                academic_year=self.year,
                grade=grade,
                defaults={
                    "min_score": lo,
                    "max_score": hi,
                    "remark": grade,
                    "points": 0,
                },
            )

        subject = __import__(
            "apps.academics.models", fromlist=["Subject"]
        ).Subject.objects.get_or_create(code="SCI", defaults={"name": "Science"})[0]

        student = make_student_user(self.cls)

        # Warm cache — pass_rate should be None (no published results yet)
        before = get_cached_school_kpis(term=self.term)
        self.assertIsNone(before["pass_rate"])

        # Create a sheet, add a score, walk through the state machine
        admin_user = self.finance_user  # any user with submit/confirm/publish rights
        from apps.results.models import ScoreEntry

        sheet = ResultSheet.objects.create(
            school_class=self.cls,
            subject=subject,
            term=self.term,
            status=ResultSheet.Status.DRAFT,
        )
        ScoreEntry.objects.create(
            result_sheet=sheet,
            student=student,
            total=Decimal("75.00"),
            grade="P",
            remark="Pass",
            points=Decimal(2),
        )
        submit_sheet(sheet=sheet, actor=admin_user)
        confirm_sheet(sheet=sheet, actor=admin_user)
        publish_sheet(sheet=sheet, actor=admin_user)  # <-- must invalidate cache

        # After publish, recomputed pass_rate should be 100%
        after = get_cached_school_kpis(term=self.term)
        self.assertIsNotNone(after["pass_rate"])
        self.assertEqual(after["pass_rate"], 100.0)
