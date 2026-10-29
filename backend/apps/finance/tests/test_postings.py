"""
apps/finance/tests/test_postings.py
------------------------------------
Where each posting actually lands in the chart of accounts.

Three rules hold the ledger together, and each has already been broken once in
this project's history:

* a receipt settles into the account named for *how it arrived*, so Mobile Money
  money is never counted as bank money;
* the double entry balances after the rounding step, not before it;
* a repeated submission posts once, whatever the button or the network did.

``cash`` is absent from the settlement map on purpose: this school does not
collect fees in cash. The Cash account is petty cash the school spends, and a
test that assumed a receipt could land there would be testing a rule the
bursary has already retired.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

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

from .. import services
from ..chart_of_accounts import (
    BAD_DEBT_CODE,
    BANK_CODE,
    CASH_CODE,
    CREDIT_BALANCES_CODE,
    DISCOUNTS_CODE,
    FEES_RECEIVABLE_CODE,
    MOBILE_MONEY_CODE,
)
from ..models import (
    Adjustment,
    Expense,
    JournalEntry,
    Payment,
)

D = Decimal
PAY_DATE = datetime.date(2025, 9, 10)


def debit_codes(entry: JournalEntry) -> dict[str, Decimal]:
    """The debit side of an entry, keyed by account code."""
    return {
        line.account.code: line.debit_amount
        for line in entry.lines.select_related("account")
        if line.debit_amount
    }


def credit_codes(entry: JournalEntry) -> dict[str, Decimal]:
    """The credit side of an entry, keyed by account code."""
    return {
        line.account.code: line.credit_amount
        for line in entry.lines.select_related("account")
        if line.credit_amount
    }


class PostingTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.admin = make_user("admin", username="post_admin")
        cls.student = make_student_user(cls.school_class, admission_number="POST-001")
        cls.accounts = make_accounts()
        cls.structure = make_fee_structure(
            cls.school_class, cls.term, D("500.00")
        )
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, D("500.00")
        )

    def pay(self, amount, reference, method=Payment.Method.BANK, **kwargs):
        return services.record_payment(
            student=self.student,
            amount=D(amount),
            method=method,
            reference=reference,
            payment_date=PAY_DATE,
            recorded_by=self.admin,
            **kwargs,
        )

    def entry_for(self, payment) -> JournalEntry:
        return JournalEntry.objects.get(
            source_type=JournalEntry.SourceType.PAYMENT, source_id=str(payment.pk)
        )


class SettlementTests(PostingTestBase):
    def test_each_method_settles_into_the_account_named_for_it(self):
        expected = {
            Payment.Method.BANK: BANK_CODE,
            Payment.Method.CHEQUE: BANK_CODE,
            Payment.Method.MOBILE_MONEY: MOBILE_MONEY_CODE,
            Payment.Method.OTHER: BANK_CODE,
        }
        for index, (method, code) in enumerate(expected.items(), start=1):
            payment = self.pay("50.00", f"SET-{index}", method=method)
            self.assertEqual(payment.settled_into.code, code)
            entry = self.entry_for(payment)
            self.assertEqual(debit_codes(entry), {code: D("50.00")})
            self.assertEqual(
                credit_codes(entry), {FEES_RECEIVABLE_CODE: D("50.00")}
            )

    def test_no_income_method_settles_into_petty_cash(self):
        self.assertNotIn(CASH_CODE, services.SETTLEMENT_ACCOUNT_CODES.values())

    def test_a_method_outside_the_settlement_map_is_refused_not_defaulted(self):
        with self.assertRaises(ValidationError) as ctx:
            self.pay("50.00", "SET-BAD", method="cash")
        self.assertIn("settle into", str(ctx.exception))
        self.assertFalse(Payment.objects.filter(reference="SET-BAD").exists())


class OverpaymentTests(PostingTestBase):
    def test_the_surplus_is_a_liability_to_the_student_not_income(self):
        payment = self.pay("600.00", "OVER-1")
        entry = self.entry_for(payment)
        self.assertEqual(
            debit_codes(entry), {BANK_CODE: D("600.00"), FEES_RECEIVABLE_CODE: D("100.00")}
        )
        self.assertEqual(
            credit_codes(entry),
            {FEES_RECEIVABLE_CODE: D("600.00"), CREDIT_BALANCES_CODE: D("100.00")},
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_outstanding, D("0.00"))

    def test_an_exact_payment_creates_no_credit_balance_line(self):
        payment = self.pay("500.00", "OVER-2")
        self.assertNotIn(
            CREDIT_BALANCES_CODE, credit_codes(self.entry_for(payment))
        )

    def test_every_payment_entry_balances(self):
        self.pay("600.00", "OVER-3")
        self.pay("40.00", "OVER-4")
        for entry in JournalEntry.objects.all():
            totals = entry.lines.aggregate(
                debit=Sum("debit_amount", default=D("0.00")),
                credit=Sum("credit_amount", default=D("0.00")),
            )
            self.assertEqual(totals["debit"], totals["credit"], entry.reference)

class IdempotencyTests(PostingTestBase):
    def test_a_double_submit_returns_the_first_receipt_and_posts_once(self):
        first = self.pay("100.00", "KEY-1", idempotency_key="form-abc")
        second = self.pay("100.00", "KEY-1-ignored", idempotency_key="form-abc")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(JournalEntry.objects.count(), 1)

    def test_two_different_keys_both_post(self):
        self.pay("10.00", "KEY-2", idempotency_key="form-1")
        self.pay("10.00", "KEY-3", idempotency_key="form-2")
        self.assertEqual(Payment.objects.count(), 2)

    def test_a_shared_key_is_refused_by_the_database(self):
        self.pay("10.00", "KEY-4", idempotency_key="form-x")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(
                student=self.student,
                reference="KEY-5",
                idempotency_key="form-x",
                payment_date=PAY_DATE,
                method=Payment.Method.BANK,
                amount=D("10.00"),
            )

    def test_receipts_without_a_key_never_collide(self):
        self.pay("10.00", "PLAIN-1")
        self.pay("10.00", "PLAIN-2")
        self.assertEqual(Payment.objects.filter(idempotency_key__isnull=True).count(), 2)

    def test_a_duplicate_reference_is_refused_before_anything_is_posted(self):
        self.pay("10.00", "DUP-1")
        with self.assertRaises(ValidationError):
            self.pay("10.00", "DUP-1")
        self.assertEqual(JournalEntry.objects.count(), 1)


class AdjustmentPostingTests(PostingTestBase):
    def adjust(self, adjustment_type, amount="50.00"):
        return services.apply_adjustment(
            student=self.student,
            invoice=self.invoice,
            adjustment_type=adjustment_type,
            amount=D(amount),
            reason="approved by bursar",
            actor=self.admin,
        )

    def test_a_discount_is_contra_income_while_the_debt_is_released(self):
        adjustment = self.adjust(Adjustment.AdjustmentType.DISCOUNT)
        entry = adjustment.journal_entry
        self.assertEqual(debit_codes(entry), {DISCOUNTS_CODE: D("50.00")})
        self.assertEqual(
            credit_codes(entry), {FEES_RECEIVABLE_CODE: D("50.00")}
        )

    def test_a_waiver_uses_the_same_account_as_a_discount(self):
        adjustment = self.adjust(Adjustment.AdjustmentType.WAIVER)
        self.assertIn(DISCOUNTS_CODE, debit_codes(adjustment.journal_entry))

    def test_a_write_off_is_a_bad_debt_not_a_discount(self):
        adjustment = self.adjust(Adjustment.AdjustmentType.WRITE_OFF)
        entry = adjustment.journal_entry
        self.assertEqual(debit_codes(entry), {BAD_DEBT_CODE: D("50.00")})
        self.assertNotIn(DISCOUNTS_CODE, debit_codes(entry))

    def test_an_adjustment_needs_a_reason_and_a_positive_amount(self):
        with self.assertRaises(ValidationError):
            services.apply_adjustment(
                student=self.student,
                invoice=self.invoice,
                adjustment_type=Adjustment.AdjustmentType.DISCOUNT,
                amount=D("10.00"),
                reason="   ",
                actor=self.admin,
            )
        with self.assertRaises(ValidationError):
            self.adjust(Adjustment.AdjustmentType.DISCOUNT, amount="0.00")
        self.assertEqual(Adjustment.objects.count(), 0)


class ExpensePostingTests(PostingTestBase):
    def expense(self, paid_via, amount="40.00"):
        return services.record_expense(
            account=self.accounts["6000"],
            amount=D(amount),
            expense_date=PAY_DATE,
            description="Fuel for the pickup",
            paid_via=paid_via,
            actor=self.admin,
        )

    def test_petty_cash_spending_reduces_cash(self):
        expense = self.expense(Expense.PaymentVia.CASH)
        self.assertEqual(
            credit_codes(expense.journal_entry), {CASH_CODE: D("40.00")}
        )

    def test_bank_spending_reduces_bank(self):
        expense = self.expense(Expense.PaymentVia.BANK)
        self.assertEqual(
            credit_codes(expense.journal_entry), {BANK_CODE: D("40.00")}
        )

    def test_the_expense_is_the_debit_whichever_side_paid_for_it(self):
        for paid_via in Expense.PaymentVia.values:
            expense = self.expense(paid_via)
            self.assertEqual(
                debit_codes(expense.journal_entry), {"6000": D("40.00")}
            )

    def test_zero_and_negative_expenses_are_refused(self):
        for bad in ("0.00", "-25.00"):
            with self.assertRaises(ValidationError):
                self.expense(Expense.PaymentVia.CASH, amount=bad)
        self.assertEqual(Expense.objects.count(), 0)


class LineRoundTripTests(PostingTestBase):
    def test_an_odd_amount_is_rounded_once_before_it_is_posted(self):
        """
        A receipt keyed in as 33.335 must not become 33.33 on the payment, 33.34
        on the allocation and 33.335 in the ledger. Every column holds the same
        two-decimal figure, so the statement and the trial balance agree.
        """
        payment = self.pay("33.335", "ROUND-1")
        entry = self.entry_for(payment)
        lines = list(entry.lines.select_related("account"))

        self.assertEqual(payment.amount, D("33.34"))
        self.assertEqual(
            [line.debit_amount or line.credit_amount for line in lines],
            [D("33.34"), D("33.34")],
        )
        self.assertEqual(
            payment.allocations.first().allocated_amount, D("33.34")
        )
        self.assertEqual(sum((l.debit_amount for l in lines), D("0.00")),
                         sum((l.credit_amount for l in lines), D("0.00")))

    def test_the_rounded_cent_survives_the_balance(self):
        self.pay("33.335", "ROUND-2")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_outstanding, D("466.66"))
