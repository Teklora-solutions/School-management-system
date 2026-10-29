"""
apps/dashboards/tests/test_director_finance_reconciliation.py
--------------------------------------------------------------
Proves the Director portal and the Finance module give the *same* answer.

Each test asks the Director's own selector for a figure, then asks
``apps.finance.selectors`` the same question independently, and requires them to
be equal. The static contract test forbids the Director from re-deriving money;
this one catches the subtler failure — a Director selector that calls Finance
but then reshapes, re-labels or re-filters what comes back.

Reconciliation is also asserted through a real page render, because a selector
can agree with Finance while the screen beside it prints something else.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.dashboards.selectors import (
    class_fee_debtors,
    class_fee_summary,
    income_vs_expenses_by_month,
    school_kpis,
)
from apps.dashboards.tests.fixtures import (
    make_accounts,
    make_class,
    make_director,
    make_fee_structure,
    make_payment_allocation,
    make_posted_invoice,
    make_student_user,
    make_term,
    make_user,
    make_year,
)
from apps.finance import selectors as finance
from apps.finance import services as finance_services
from apps.finance.models import Invoice, Payment


class MoneyReconciliationBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cache.clear()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.cls_a = make_class(cls.year, name="Basic 5", level_order=5)
        cls.cls_b = make_class(cls.year, name="Basic 6", level_order=6)
        cls.accounts = make_accounts()
        cls.admin = make_user("admin", username="recon_admin")
        cls.director = make_director(username="recon_director")
        cls.fs_a = make_fee_structure(cls.cls_a, cls.term, Decimal("500.00"))
        cls.fs_b = make_fee_structure(cls.cls_b, cls.term, Decimal("300.00"))

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @classmethod
    def bill(cls, student, structure, amount):
        return make_posted_invoice(student, cls.term, structure, Decimal(amount))


class SchoolKpisReconciliationTests(MoneyReconciliationBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.paid = make_student_user(cls.cls_a, "RECON-P1")
        cls.waived = make_student_user(cls.cls_a, "RECON-P2")
        cls.unpaid = make_student_user(cls.cls_b, "RECON-P3")
        cls.bill(cls.paid, cls.fs_a, "500.00")
        cls.bill(cls.waived, cls.fs_a, "500.00")
        cls.bill(cls.unpaid, cls.fs_b, "300.00")
        make_payment_allocation(Invoice.objects.get(student=cls.paid), Decimal("200.00"))
        finance_services.apply_adjustment(
            student=cls.waived,
            invoice=Invoice.objects.get(student=cls.waived),
            adjustment_type="waiver",
            amount=Decimal("100.00"),
            reason="Sibling waiver",
            actor=cls.admin,
        )

    def test_fee_figures_are_finance_answers_not_dashboard_re_calculations(self):
        kpis = school_kpis(term=self.term)
        fees = finance.fee_totals(term=self.term)
        self.assertEqual(kpis["fees_billed"], fees["billed"])
        self.assertEqual(kpis["fees_collected"], fees["collected"])
        self.assertEqual(kpis["fees_adjusted"], fees["adjusted"])
        self.assertEqual(kpis["fees_outstanding"], fees["outstanding"])
        self.assertEqual(kpis["collection_rate"], fees["collection_rate"])

    def test_a_waived_amount_is_not_debt_on_either_side(self):
        """The definition D1 standardised: waived money is not owed money."""
        kpis = school_kpis(term=self.term)
        self.assertEqual(kpis["fees_billed"], Decimal("1300.00"))
        self.assertEqual(kpis["fees_outstanding"], Decimal("1000.00"))
        self.assertEqual(
            kpis["fees_outstanding"],
            finance.fee_totals(term=self.term)["outstanding"],
        )

    def test_reversing_a_payment_moves_both_figures_back_together(self):
        student = make_student_user(self.cls_a, "RECON-P4")
        invoice = self.bill(student, self.fs_a, "250.00")
        payment = finance_services.record_payment(
            student=student,
            amount=Decimal("250.00"),
            method=Payment.Method.BANK,
            reference="RECON-REV",
            payment_date=datetime.date(2025, 9, 10),
            recorded_by=self.admin,
        )
        self.assertEqual(invoice.amount_outstanding, Decimal("0.00"))
        self.assertEqual(
            school_kpis(term=self.term)["fees_collected"],
            finance.fee_totals(term=self.term)["collected"],
        )
        finance_services.reverse_payment(
            payment=payment, reason="wrong payer", actor=self.admin
        )
        kpis = school_kpis(term=self.term)
        fees = finance.fee_totals(term=self.term)
        self.assertEqual(kpis["fees_collected"], fees["collected"])
        self.assertEqual(
            kpis["fees_outstanding"],
            Decimal("1250.00"),
            "the waived 100 is still not a debt after the reversal",
        )


class ClassRowReconciliationTests(MoneyReconciliationBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.a1 = make_student_user(cls.cls_a, "RECON-C1")
        cls.b1 = make_student_user(cls.cls_b, "RECON-C2")
        cls.bill(cls.a1, cls.fs_a, "500.00")
        cls.bill(cls.b1, cls.fs_b, "300.00")
        make_payment_allocation(
            Invoice.objects.get(student=cls.a1), Decimal("100.00")
        )

    def test_per_class_rows_are_the_same_objects_finance_builds(self):
        self.assertEqual(
            class_fee_summary(term_id=self.term.pk),
            finance.class_fee_rows(term=self.term),
        )

    def test_per_class_rows_reconcile_with_the_school_totals(self):
        rows = class_fee_summary(term_id=self.term.pk)
        totals = finance.fee_totals(term=self.term)
        for key in ("billed", "collected", "adjusted", "outstanding"):
            self.assertEqual(sum((r[key] for r in rows), Decimal("0.00")), totals[key])

    def test_debtor_list_is_finance_debtor_list(self):
        self.assertEqual(
            class_fee_debtors(class_id=self.cls_a.pk, term_id=self.term.pk),
            finance.student_fee_rows(
                term=self.term, class_id=self.cls_a.pk, only_debtors=True
            ),
        )

    def test_a_fully_paid_class_lists_no_debtors(self):
        invoice = Invoice.objects.get(student=self.b1)
        make_payment_allocation(invoice, Decimal("300.00"))
        self.assertEqual(
            class_fee_debtors(class_id=self.cls_b.pk, term_id=self.term.pk), []
        )


class BooksReconciliationTests(MoneyReconciliationBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student = make_student_user(cls.cls_a, "RECON-B1")
        finance_services.post_invoices(
            term=cls.term, school_class=cls.cls_a, actor=cls.admin
        )
        finance_services.record_payment(
            student=cls.student,
            amount=Decimal("150.00"),
            method=Payment.Method.BANK,
            reference="RECON-BOOKS",
            payment_date=datetime.date(2025, 9, 10),
            recorded_by=cls.admin,
        )

    def test_monthly_income_and_expense_come_from_the_ledger(self):
        self.assertEqual(
            income_vs_expenses_by_month(year_id=self.year.pk),
            finance.monthly_income_expense(
                date_from=self.year.start_date, date_to=self.year.end_date
            ),
        )

    def test_trial_balance_rows_and_the_balanced_flag_are_finance_own(self):
        balance = finance.trial_balance_at()
        rows = finance.trial_balance()
        self.assertEqual(len(rows), len(balance["rows"]))
        self.assertEqual(balance["total_debits"], balance["total_credits"])


class DirectorPagesShowInterfaceNumbers(MoneyReconciliationBase):
    """The screen, not just the function: a page must print what Finance says."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.a1 = make_student_user(cls.cls_a, "RECON-V1")
        cls.bill(cls.a1, cls.fs_a, "500.00")
        make_payment_allocation(
            Invoice.objects.get(student=cls.a1), Decimal("200.00")
        )

    def test_home_page_context_matches_the_interface(self):
        self.client.force_login(self.director)
        response = self.client.get(reverse("dashboards:director_home"))
        self.assertEqual(response.status_code, 200)
        fees = finance.fee_totals(term=self.term)
        self.assertEqual(response.context["fees_billed"], fees["billed"])
        self.assertEqual(response.context["fees_collected"], fees["collected"])
        self.assertEqual(response.context["fees_outstanding"], fees["outstanding"])

    def test_fee_overview_page_matches_finance_class_rows(self):
        self.client.force_login(self.director)
        response = self.client.get(reverse("dashboards:director_fees"))
        self.assertEqual(
            response.context["rows"], finance.class_fee_rows(term=self.term)
        )

    def test_debtor_page_matches_finance_student_rows(self):
        self.client.force_login(self.director)
        response = self.client.get(
            reverse("dashboards:director_fee_debtors", args=[self.cls_a.pk])
        )
        self.assertEqual(
            response.context["debtors"],
            finance.student_fee_rows(
                term=self.term, class_id=self.cls_a.pk, only_debtors=True
            ),
        )

    def test_trial_balance_page_prints_the_ledgers_own_verdict(self):
        make_student_user(self.cls_b, "RECON-V2")
        finance_services.post_invoices(
            term=self.term, school_class=self.cls_b, actor=self.admin
        )
        self.client.force_login(self.director)
        response = self.client.get(
            reverse("dashboards:director_books_trial_balance")
        )
        trial = finance.trial_balance_at()
        self.assertTrue(trial["rows"], "the ledger should have something to show")
        self.assertEqual(response.context["rows"], trial["rows"])
        self.assertEqual(response.context["total_debits"], trial["total_debits"])
        self.assertEqual(response.context["as_of"], trial["as_of"])
        self.assertTrue(response.context["balanced"])
