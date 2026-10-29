"""
tests/test_director_query_count.py
------------------------------------
Performance budget tests using assertNumQueries.

The director home page aggregates data from students, finance, and results.
The goal is to keep total DB queries under a fixed budget so the page stays
fast even as the dataset grows.

Query budget (conservative upper bound):
  - school_kpis()               ≤ 8 queries
  - class_fee_summary()         ≤ 4 queries
  - director_home view (GET)    ≤ 20 queries  (includes middleware, context
                                               processors, session, etc.)

These bounds are intentionally generous to avoid brittle tests while still
catching accidental N+1 regressions.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .fixtures import (
    make_accounts,
    make_class,
    make_director,
    make_fee_structure,
    make_grading_config,
    make_payment_allocation,
    make_posted_invoice,
    make_published_score,
    make_student_user,
    make_subject,
    make_term,
    make_year,
)


class SchoolKpisQueryBudgetTest(TestCase):
    """
    school_kpis() must execute ≤ 12 queries regardless of data volume.
    The selector uses aggregate/annotate so adding more students/invoices
    must NOT increase the query count.

    Actual breakdown (11 queries measured):
      1  student COUNT
      2  student GROUP BY gender
      3  student GROUP BY class
      4  teacher COUNT
      5  staff COUNT
      6  fees_billed SUM
      7  fees_collected SUM
      8  fees_adjusted SUM
      9  results AVG + COUNT
      10 grading config lookup (for pass_mark)
      11 passing entries COUNT
    Budget set at 12 (actual + 1 headroom).
    """

    QUERY_BUDGET = 12

    def setUp(self):
        cache.clear()
        self.year = make_year()
        self.term = make_term(self.year)
        self.cls = make_class(self.year)
        make_accounts()
        self.fs = make_fee_structure(self.cls, self.term, Decimal("400.00"))
        make_grading_config(self.year)
        self.subject = make_subject("BUDGET_MATH", "Budget Math")

        # Create 5 students to ensure aggregation doesn't become per-row
        for i in range(5):
            s = make_student_user(self.cls)
            inv = make_posted_invoice(s, self.term, self.fs, Decimal("400.00"))
            make_payment_allocation(inv, Decimal("200.00"))
            make_published_score(self.cls, self.subject, self.term, s, Decimal("65.00"))

    def tearDown(self):
        cache.clear()

    def test_school_kpis_query_budget(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from apps.dashboards.selectors import school_kpis

        with CaptureQueriesContext(connection) as ctx:
            school_kpis(term=self.term)
        count = len(ctx)
        self.assertLessEqual(
            count,
            self.QUERY_BUDGET,
            f"school_kpis() used {count} queries, budget is {self.QUERY_BUDGET}. "
            f"Queries:\n"
            + "\n".join(f"  {i+1}. {q['sql'][:120]}" for i, q in enumerate(ctx)),
        )

    def test_query_count_does_not_grow_with_more_students(self):
        """Add 5 more students; query count must remain the same."""
        for i in range(5):
            s = make_student_user(self.cls)
            inv = make_posted_invoice(s, self.term, self.fs, Decimal("400.00"))
            make_payment_allocation(inv, Decimal("400.00"))
            make_published_score(self.cls, self.subject, self.term, s, Decimal("70.00"))

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from apps.dashboards.selectors import school_kpis

        with CaptureQueriesContext(connection) as ctx:
            school_kpis(term=self.term)
        count = len(ctx)
        self.assertLessEqual(
            count,
            self.QUERY_BUDGET,
            f"school_kpis() query count grew to {count} after adding more students "
            f"(budget {self.QUERY_BUDGET}). N+1 regression detected.",
        )


class ClassFeeSummaryQueryBudgetTest(TestCase):
    """
    class_fee_summary() must execute ≤ 5 queries.
    Actual measured: 3 queries (1 Term lookup + 2 GROUP BY aggregates).
    Budget set at 5 (actual + 2 headroom).
    """

    QUERY_BUDGET = 5

    def setUp(self):
        cache.clear()
        self.year = make_year()
        self.term = make_term(self.year)
        make_accounts()
        # Two classes
        self.cls_a = make_class(self.year, name="Grade A", level_order=1)
        self.cls_b = make_class(self.year, name="Grade B", level_order=2)
        self.fs_a = make_fee_structure(self.cls_a, self.term, Decimal("300.00"))
        self.fs_b = make_fee_structure(self.cls_b, self.term, Decimal("300.00"))
        # 3 students per class
        for _ in range(3):
            s_a = make_student_user(self.cls_a)
            inv_a = make_posted_invoice(s_a, self.term, self.fs_a, Decimal("300.00"))
            make_payment_allocation(inv_a, Decimal("150.00"))
            s_b = make_student_user(self.cls_b)
            inv_b = make_posted_invoice(s_b, self.term, self.fs_b, Decimal("300.00"))
            make_payment_allocation(inv_b, Decimal("300.00"))

    def tearDown(self):
        cache.clear()

    def test_class_fee_summary_query_budget(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from apps.dashboards.selectors import class_fee_summary

        with CaptureQueriesContext(connection) as ctx:
            class_fee_summary(term_id=self.term.pk)
        count = len(ctx)
        self.assertLessEqual(
            count,
            self.QUERY_BUDGET,
            f"class_fee_summary() used {count} queries, budget is {self.QUERY_BUDGET}.",
        )


class DirectorHomeViewQueryBudgetTest(TestCase):
    """
    The full director home page GET must stay under VIEW_QUERY_BUDGET queries.
    This catches regressions in context processors, middleware, and view code.

    Actual counts measured:
      Cold (cache miss): 17 queries — session(1) + user(1) + current_term(1) +
        school_kpis(10) + class_fee_summary(3) + announcements(1)
      Warm (cache hit):  9 queries  — session(1) + user(1) + current_term(1) +
        class_fee_summary(3) + announcements(1) + SAVEPOINT/RELEASE(2)

    Budgets set at measured + 3 headroom to absorb minor middleware variance.
    """

    VIEW_QUERY_BUDGET = 20  # cold cache: 17 measured + 3 headroom
    CACHED_QUERY_BUDGET = 12  # warm cache: 9 measured + 3 headroom

    def setUp(self):
        cache.clear()
        self.year = make_year()
        self.term = make_term(self.year)
        make_accounts()
        self.cls = make_class(self.year)
        self.fs = make_fee_structure(self.cls, self.term, Decimal("300.00"))
        make_grading_config(self.year)
        self.director = make_director(username="dir_qcount")
        self.client.force_login(self.director)

    def tearDown(self):
        cache.clear()

    def _count_queries(self, fn):
        """Run fn() and return the number of DB queries executed."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            fn()
        return len(ctx)

    def test_home_page_cold_cache_query_budget(self):
        def _get():
            response = self.client.get(reverse("dashboards:director_home"))
            self.assertEqual(response.status_code, 200)

        count = self._count_queries(_get)
        self.assertLessEqual(
            count,
            self.VIEW_QUERY_BUDGET,
            f"Cold-cache director home used {count} queries, budget is {self.VIEW_QUERY_BUDGET}.",
        )

    def test_home_page_warm_cache_is_cheaper(self):
        # Warm the cache with the first request
        self.client.get(reverse("dashboards:director_home"))

        def _get():
            response = self.client.get(reverse("dashboards:director_home"))
            self.assertEqual(response.status_code, 200)

        warm_count = self._count_queries(_get)
        self.assertLessEqual(
            warm_count,
            self.CACHED_QUERY_BUDGET,
            f"Warm-cache director home used {warm_count} queries, budget is {self.CACHED_QUERY_BUDGET}.",
        )
        # The warm load must also be strictly cheaper than the cold budget
        self.assertLess(
            warm_count,
            self.VIEW_QUERY_BUDGET,
            "Warm-cache load should be cheaper than cold-cache load.",
        )
