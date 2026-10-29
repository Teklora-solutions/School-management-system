"""
apps/finance/tests/test_ledger.py
----------------------------------
Ledger invariants for Module 3: append-only guards, balanced entries,
idempotent payment posting, reversal and trial-balance reconciliation.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import TestCase

from apps.dashboards.tests.fixtures import (
    make_accounts,
    make_class,
    make_fee_structure,
    make_posted_invoice,
    make_student_user,
    make_term,
    make_user,
    make_year,
)

from .. import selectors, services
from ..models import (
    Adjustment,
    Expense,
    Invoice,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentAllocation,
)


class LedgerTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.admin = make_user("admin", username="fin_admin")
        cls.student = make_student_user(cls.school_class, admission_number="FIN-001")
        cls.accounts = make_accounts()
        cls.structure = make_fee_structure(
            cls.school_class, cls.term, Decimal("500.00")
        )
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, Decimal("500.00")
        )

    def record(self, amount="200.00", reference="PAY-001"):
        return services.record_payment(
            student=self.student,
            amount=Decimal(amount),
            method=Payment.Method.BANK,
            reference=reference,
            payment_date=datetime.date(2025, 9, 10),
            recorded_by=self.admin,
        )


class AppendOnlyLedgerTests(LedgerTestBase):
    def test_journal_entry_cannot_be_updated(self):
        payment = self.record()
        entry = JournalEntry.objects.get(source_type=JournalEntry.SourceType.PAYMENT)
        entry.description = "tampered"
        with self.assertRaises(ValidationError):
            entry.save()
        assert payment.pk

    def test_journal_entry_cannot_be_deleted(self):
        self.record()
        entry = JournalEntry.objects.get(source_type=JournalEntry.SourceType.PAYMENT)
        with self.assertRaises(ValidationError):
            entry.delete()

    def test_journal_line_cannot_be_updated_or_deleted(self):
        self.record()
        line = JournalLine.objects.first()
        line.debit_amount = Decimal("999.00")
        with self.assertRaises(ValidationError):
            line.save()
        with self.assertRaises(ValidationError):
            line.delete()

    def test_negative_amounts_are_rejected_by_db_constraint(self):
        self.record()
        entry = JournalEntry.objects.first()
        with self.assertRaises(IntegrityError), transaction.atomic():
            JournalLine.objects.create(
                entry=entry,
                account=self.accounts["1000"],
                debit_amount=Decimal("-1.00"),
                credit_amount=Decimal("0.00"),
            )


class BalancedEntryTests(LedgerTestBase):
    def test_service_rejects_unbalanced_entry(self):
        # ValidationError, not ValueError: an unbalanced posting is a refused
        # form submission on the screens that call this, not a crash.
        with self.assertRaises(ValidationError):
            services._post_journal_entry(
                reference="UNBALANCED-1",
                entry_date=datetime.date(2025, 9, 10),
                description="bad",
                posted_by=self.admin,
                lines=[
                    {
                        "account": self.accounts["1000"],
                        "debit": Decimal(10),
                        "credit": Decimal(0),
                    },
                    {
                        "account": self.accounts["1100"],
                        "debit": Decimal(0),
                        "credit": Decimal(5),
                    },
                ],
                source_type=JournalEntry.SourceType.MANUAL,
            )

    def test_every_posted_entry_balances(self):
        self.record()
        for entry in JournalEntry.objects.all():
            totals = entry.lines.aggregate(
                debit=Sum("debit_amount"),
                credit=Sum("credit_amount"),
            )
            self.assertEqual(totals["debit"], totals["credit"], entry.reference)

    def test_trial_balance_debits_equal_credits(self):
        self.record()
        rows = selectors.trial_balance()
        self.assertEqual(
            sum(r["debit_total"] for r in rows),
            sum(r["credit_total"] for r in rows),
        )


class IdempotentPaymentTests(LedgerTestBase):
    def test_duplicate_reference_does_not_double_post(self):
        self.record(reference="PAY-DUP")
        entry_count = JournalEntry.objects.count()
        with self.assertRaises(ValidationError):
            self.record(reference="PAY-DUP")
        self.assertEqual(Payment.objects.filter(reference="PAY-DUP").count(), 1)
        self.assertEqual(JournalEntry.objects.count(), entry_count)


class ReversalTests(LedgerTestBase):
    def test_reversal_returns_reversal_record_and_restores_balance(self):
        payment = self.record(amount="200.00")
        self.assertEqual(self.invoice.amount_outstanding, Decimal("300.00"))

        cache.set(f"director_kpis_term_{self.term.pk}", {"fees_paid": 200})
        reversal = services.reverse_payment(
            payment=payment, reason="bank error", actor=self.admin
        )

        self.assertIsInstance(reversal, Payment)
        self.assertEqual(reversal.amount, Decimal("-200.00"))
        self.assertTrue(reversal.receipt_number)
        payment.refresh_from_db()
        self.assertTrue(payment.is_reversed)
        # The allocation row survives: a reversal is a correction on top of what
        # happened, not an erasure of it (A2). "Collected" nets it out through
        # ``.live()``, which is the only place the filter is stated.
        self.assertEqual(PaymentAllocation.objects.filter(payment=payment).count(), 1)
        self.assertEqual(
            PaymentAllocation.objects.filter(payment=payment).live().count(), 0
        )
        self.assertEqual(self.invoice.amount_outstanding, Decimal("500.00"))
        self.assertIsNone(cache.get(f"director_kpis_term_{self.term.pk}"))
        self.assertEqual(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.REVERSAL
            ).count(),
            1,
        )
        rows = selectors.trial_balance()
        self.assertEqual(
            sum(r["debit_total"] for r in rows),
            sum(r["credit_total"] for r in rows),
        )

    def test_reversal_requires_reason(self):
        payment = self.record()
        with self.assertRaises(ValidationError):
            services.reverse_payment(payment=payment, reason="  ", actor=self.admin)

    def test_reversal_cannot_run_twice(self):
        payment = self.record()
        services.reverse_payment(payment=payment, reason="first", actor=self.admin)
        payment.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.reverse_payment(payment=payment, reason="second", actor=self.admin)


class ReconciliationTests(LedgerTestBase):
    def test_class_totals_reconcile_with_school_totals(self):
        self.record(amount="200.00")
        cls = selectors.class_finance_summary(
            school_class_id=self.school_class.pk, term_id=self.term.pk
        )
        school = selectors.school_finance_summary(term_id=self.term.pk)
        self.assertEqual(cls["total_billed"], school["total_billed"])
        self.assertEqual(cls["total_paid"], school["total_paid"])
        self.assertEqual(cls["outstanding"], school["outstanding"])

    def test_payment_allocates_to_oldest_invoice_first(self):
        old = self.invoice
        from apps.finance.models import FeeStructure

        second_structure = FeeStructure.objects.create(
            school_class=self.school_class, term=self.term, version=2, is_active=True
        )
        new = Invoice.objects.create(
            student=self.student,
            term=self.term,
            fee_structure=second_structure,
            issued_date=datetime.date(2025, 10, 1),
            total_amount=Decimal("100.00"),
            status=Invoice.Status.POSTED,
        )
        self.record(amount="550.00", reference="PAY-OLDEST")
        allocations = PaymentAllocation.objects.order_by("invoice__issued_date")
        self.assertEqual(allocations[0].invoice, old)
        self.assertEqual(allocations[0].allocated_amount, Decimal("500.00"))
        self.assertEqual(allocations[1].invoice, new)
        self.assertEqual(allocations[1].allocated_amount, Decimal("50.00"))


class AdjustmentAndExpenseTests(LedgerTestBase):
    def test_adjustment_posts_balanced_entry_without_mutating_ledger(self):
        adjustment = services.apply_adjustment(
            student=self.student,
            adjustment_type=Adjustment.AdjustmentType.DISCOUNT,
            amount=Decimal("50.00"),
            reason="Sibling discount",
            actor=self.admin,
        )
        adjustment.refresh_from_db()
        entry = adjustment.journal_entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source_id, str(adjustment.pk))
        self.assertEqual(
            entry.lines.aggregate(total=Sum("debit_amount"))["total"],
            entry.lines.aggregate(total=Sum("credit_amount"))["total"],
        )
        with self.assertRaises(ValidationError):
            entry.save()

    def test_expense_posts_balanced_entry_and_links_source(self):
        expense = services.record_expense(
            account=self.accounts["6000"],
            amount=Decimal("75.00"),
            expense_date=datetime.date(2025, 9, 12),
            description="Cleaning supplies",
            approval_note="Approved by head teacher",
            actor=self.admin,
        )
        expense.refresh_from_db()
        entry = expense.journal_entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source_id, str(expense.pk))
        self.assertEqual(
            entry.lines.aggregate(total=Sum("debit_amount"))["total"],
            entry.lines.aggregate(total=Sum("credit_amount"))["total"],
        )
        self.assertTrue(Expense.objects.filter(pk=expense.pk).exists())
