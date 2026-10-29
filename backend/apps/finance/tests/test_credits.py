"""
apps/finance/tests/test_credits.py
----------------------------------
Money the school is holding: an overpayment booked to Student Credit Balances,
that credit spent on the next invoice (B4), the hand-built allocation override,
receipt verification and reprints, and what a reversal does now that it no
longer deletes.

The reversal tests sit here because that is the change Module 6 ruled in (A2):
the allocation rows stay and ``.live()`` is what nets them, so "restores
balances exactly" has to be proven against rows that are still on screen.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import AuditLog
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
from ..chart_of_accounts import CREDIT_BALANCES_CODE, FEES_RECEIVABLE_CODE
from ..models import (
    Invoice,
    JournalEntry,
    NumberSeries,
    Payment,
    PaymentAllocation,
)

TODAY = datetime.date(2025, 9, 10)


class FinanceTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.admin = make_user("admin", username="cred_admin")
        cls.officer = make_user("finance", username="cred_officer")
        cls.student = make_student_user(cls.school_class, admission_number="CRD-001")
        cls.classmate = make_student_user(cls.school_class, admission_number="CRD-002")
        cls.accounts = make_accounts()
        cls.structure = make_fee_structure(
            cls.school_class, cls.term, Decimal("500.00")
        )
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, Decimal("500.00")
        )
        cls.their_invoice = make_posted_invoice(
            cls.classmate, cls.term, cls.structure, Decimal("500.00")
        )

    def next_term(self, amount="300.00"):
        """A new term with its own fee structure, ready for a billing run."""
        term = make_term(self.year, name="term_2", is_current=False)
        make_fee_structure(self.school_class, term, Decimal(amount))
        return term

    def second_invoice(self, amount="400.00", issued=TODAY):
        """A second posted invoice for the same pupil, issued later."""
        return Invoice.objects.create(
            student=self.student,
            term=self.next_term(),
            fee_structure=self.structure,
            issued_date=issued,
            total_amount=Decimal(amount),
            status=Invoice.Status.POSTED,
        )

    def pay(self, amount, reference, student=None, allocations=None, method=None):
        return services.record_payment(
            student=student or self.student,
            amount=Decimal(amount),
            method=method or Payment.Method.BANK,
            reference=reference,
            payment_date=TODAY,
            recorded_by=self.officer,
            allocations=allocations,
        )

    def entry_lines(self, entry):
        """
        {account code: (debit total, credit total)}.

        Summed per account because a four-line overpayment entry touches Fees
        Receivable twice — once credited for the bill, once debited for the
        surplus — and the question these tests ask is what the account nets to.
        """
        totals: dict[str, tuple[Decimal, Decimal]] = {}
        for line in entry.lines.select_related("account"):
            debit, credit = totals.get(line.account.code, (Decimal(0), Decimal(0)))
            totals[line.account.code] = (
                debit + line.debit_amount,
                credit + line.credit_amount,
            )
        return {
            code: sides for code, sides in totals.items() if any(sides)
        }


class CreditBalanceTests(FinanceTestBase):
    def test_an_overpayment_is_booked_as_credit_not_left_in_fees_receivable(self):
        payment = self.pay("600.00", "CRD-OVER")
        self.assertEqual(payment.unapplied_amount, Decimal("100.00"))
        entry = JournalEntry.objects.get(
            source_type=JournalEntry.SourceType.PAYMENT, source_id=str(payment.pk)
        )
        self.assertEqual(
            self.entry_lines(entry),
            {
                "1010": (Decimal("600.00"), Decimal("0.00")),
                "1100": (Decimal("100.00"), Decimal("600.00")),
                "2100": (Decimal("0.00"), Decimal("100.00")),
            },
        )

    def test_the_credit_is_spent_on_the_next_invoice(self):
        """
        B4: "overpayments (credit to Student Credit Balances, auto-applied to
        the next invoice)". The next invoice is a new term's, made by the
        ordinary billing run.
        """
        self.pay("600.00", "CRD-OVER")
        term = self.next_term("300.00")

        services.post_invoices(term=term, actor=self.admin)

        new_invoice = Invoice.objects.get(student=self.student, term=term)
        self.assertEqual(new_invoice.amount_paid, Decimal("100.00"))
        self.assertEqual(new_invoice.amount_outstanding, Decimal("200.00"))
        self.assertEqual(
            PaymentAllocation.objects.get(invoice=new_invoice).allocated_amount,
            Decimal("100.00"),
        )

    def test_applying_a_credit_moves_the_ledger_without_touching_income(self):
        self.pay("600.00", "CRD-OVER")
        services.post_invoices(term=self.next_term("300.00"), actor=self.admin)

        entries = JournalEntry.objects.filter(
            source_type=JournalEntry.SourceType.CREDIT
        )
        self.assertEqual(entries.count(), 1, msg="One application, one entry.")
        self.assertEqual(
            self.entry_lines(entries.first()),
            {
                CREDIT_BALANCES_CODE: (Decimal("100.00"), Decimal("0.00")),
                FEES_RECEIVABLE_CODE: (Decimal("0.00"), Decimal("100.00")),
            },
        )

    def test_spending_the_credit_does_not_double_count_it_as_collection(self):
        """
        The money was collected once, when it arrived. Applying it to the next
        invoice moves the balance; it must not make ``collected`` more than the
        cash the school actually received.
        """
        self.pay("600.00", "CRD-OVER")
        term = self.next_term("300.00")
        services.post_invoices(term=term, actor=self.admin)

        totals = selectors.fee_totals(term=term)
        self.assertEqual(totals["billed"], Decimal("600.00"))
        self.assertEqual(totals["collected"], Decimal("100.00"))
        self.assertEqual(totals["outstanding"], Decimal("500.00"))

    def test_a_student_with_no_credit_costs_no_entry(self):
        self.pay("500.00", "CRD-EXACT")
        services.post_invoices(term=self.next_term("300.00"), actor=self.admin)
        self.assertFalse(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.CREDIT
            ).exists()
        )

    def test_a_reversed_receipt_is_not_a_credit_to_spend(self):
        payment = self.pay("600.00", "CRD-OVER")
        services.reverse_payment(
            payment=payment, reason="paid the wrong bill", actor=self.admin
        )

        term = self.next_term("300.00")
        services.post_invoices(term=term, actor=self.admin)
        self.assertEqual(
            Invoice.objects.get(student=self.student, term=term).amount_paid,
            Decimal("0.00"),
        )
        self.assertFalse(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.CREDIT
            ).exists()
        )

    def test_two_surpluses_are_spent_oldest_receipt_first(self):
        """
        The order it was received is the order it is spent, so a statement shows
        the earlier receipt being used up before the later one moves at all.
        """
        early = self.pay("600.00", "CRD-EARLY")
        late = self.pay("300.00", "CRD-LATE", method=Payment.Method.MOBILE_MONEY)

        term = self.next_term("150.00")
        services.post_invoices(term=term, actor=self.admin)
        new_invoice = Invoice.objects.get(student=self.student, term=term)

        rows = PaymentAllocation.objects.filter(invoice=new_invoice).order_by("pk")
        self.assertEqual([row.allocated_amount for row in rows], [Decimal("100.00"), Decimal("50.00")])
        early.refresh_from_db()
        late.refresh_from_db()
        self.assertEqual(early.unapplied_amount, Decimal("0.00"))
        self.assertEqual(late.unapplied_amount, Decimal("250.00"))


class AllocationOverrideTests(FinanceTestBase):
    def override(self, rows, amount="200.00", reference="CRD-OVR"):
        return self.pay(amount, reference, allocations=rows)

    def test_the_front_desk_can_choose_which_invoice_the_money_is_for(self):
        """
        B4's manual override: "oldest first" is the default, not a rule the
        recorder is not allowed to question — a parent hands over money and says
        it is for next term's bill.
        """
        later = self.second_invoice()
        payment = self.override([{"invoice": later, "amount": Decimal("200.00")}])

        self.invoice.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("0.00"))
        self.assertEqual(later.amount_paid, Decimal("200.00"))
        self.assertEqual(payment.allocations.total(), Decimal("200.00"))

    def test_an_allocation_against_another_students_invoice_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self.override([{"invoice": self.their_invoice, "amount": Decimal("50.00")}])
        self.assertIn("does not belong", str(caught.exception))

    def test_a_draft_invoice_cannot_take_money(self):
        draft = Invoice.objects.create(
            student=self.student,
            term=self.next_term(),
            fee_structure=self.structure,
            issued_date=TODAY,
            total_amount=Decimal("100.00"),
            status=Invoice.Status.DRAFT,
        )
        with self.assertRaises(ValidationError) as caught:
            self.override([{"invoice": draft, "amount": Decimal("50.00")}])
        self.assertIn("not a posted invoice", str(caught.exception))

    def test_listing_the_same_invoice_twice_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self.override(
                [
                    {"invoice": self.invoice, "amount": Decimal("50.00")},
                    {"invoice_id": self.invoice.pk, "amount": Decimal("50.00")},
                ]
            )
        self.assertIn("listed twice", str(caught.exception))

    def test_an_allocation_bigger_than_the_invoice_still_owes_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self.override([{"invoice": self.invoice, "amount": Decimal("501.00")}])
        self.assertIn("more than it can take", str(caught.exception))

    def test_allocations_that_add_up_to_more_than_the_money_received_are_refused(self):
        later = self.second_invoice()
        with self.assertRaises(ValidationError) as caught:
            self.override(
                [
                    {"invoice": self.invoice, "amount": Decimal("150.00")},
                    {"invoice": later, "amount": Decimal("100.00")},
                ]
            )
        self.assertIn("only 200.00", str(caught.exception))

    def test_a_zero_or_negative_allocation_is_refused(self):
        for amount in ("0.00", "-10.00"):
            with (
                self.subTest(amount=amount),
                self.assertRaises(ValidationError),
            ):
                self.override([{"invoice": self.invoice, "amount": Decimal(amount)}])

    def test_a_refused_override_costs_no_receipt_and_no_number(self):
        """
        The list is checked before a row exists, so a mistyped override cannot
        leave a receipt whose ledger disagrees with its own allocations, and
        cannot take a number from the sequence for a document that was refused.
        """
        before = Payment.objects.count()
        with self.assertRaises(ValidationError):
            self.override([{"invoice": self.invoice, "amount": Decimal("900.00")}])
        self.assertEqual(Payment.objects.count(), before)
        self.assertFalse(
            NumberSeries.objects.filter(
                kind=NumberSeries.Kind.RECEIPT, year=TODAY.year
            ).exists(),
            msg="The number must only be taken for a receipt that is really made.",
        )

    def test_the_default_is_still_oldest_invoice_first(self):
        later = self.second_invoice()
        payment = self.pay("550.00", "CRD-OLDEST")
        self.assertEqual(
            [row.invoice_id for row in payment.allocations.order_by("pk")],
            [self.invoice.pk, later.pk],
        )
        self.assertEqual(
            [row.allocated_amount for row in payment.allocations.order_by("pk")],
            [Decimal("500.00"), Decimal("50.00")],
        )


class ReceiptProofTests(FinanceTestBase):
    def test_every_receipt_carries_its_own_verification_code(self):
        first = self.pay("50.00", "CRD-V1")
        second = self.pay("50.00", "CRD-V2")
        self.assertNotEqual(first.verification_code, second.verification_code)
        self.assertEqual(len(first.verification_code), 8)
        self.assertEqual(first.verification_code, first.verification_code.upper())
        int(first.verification_code, 16)

    def test_a_directly_created_receipt_still_gets_a_code(self):
        """
        The seeder and the fixtures create rows without going through services,
        so the field default has to carry it — otherwise the counter has nothing
        to check against.
        """
        row = Payment.objects.create(
            student=self.student,
            reference="CRD-DIRECT",
            payment_date=TODAY,
            method=Payment.Method.BANK,
            amount=Decimal("10.00"),
        )
        self.assertTrue(row.verification_code)
        self.assertTrue(row.receipt_number)

    def test_the_first_copy_is_the_receipt_and_the_second_is_a_duplicate(self):
        payment = self.pay("50.00", "CRD-PRT")
        services.mark_receipt_printed(payment=payment, actor=self.officer)
        self.assertEqual(payment.print_count, 1)
        self.assertIsNotNone(payment.last_printed_at)

        services.mark_receipt_printed(payment=payment, actor=self.officer)
        self.assertEqual(
            payment.print_count,
            2,
            msg="The count is the control: a receipt presented twice as proof of "
            "two payments is the oldest trick in a fee office.",
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action="finance.receipt_printed", object_id=str(payment.pk)
            ).count(),
            2,
        )

    def test_a_request_for_fewer_copies_than_one_still_prints_one(self):
        payment = self.pay("50.00", "CRD-PRT0")
        services.mark_receipt_printed(payment=payment, copies=0, actor=self.officer)
        self.assertEqual(payment.print_count, 1)


class ReversalKeepsHistoryTests(FinanceTestBase):
    def reverse(self, payment, **kwargs):
        return services.reverse_payment(
            payment=payment, reason="bank error", **kwargs
        )

    def test_a_reversal_restores_the_balance_exactly_with_the_rows_still_there(self):
        payment = self.pay("500.00", "CRD-REV")
        self.assertEqual(self.invoice.amount_outstanding, Decimal("0.00"))

        self.reverse(payment, actor=self.admin)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_outstanding, Decimal("500.00"))
        self.assertEqual(PaymentAllocation.objects.filter(payment=payment).count(), 1)
        self.assertEqual(
            PaymentAllocation.objects.filter(payment=payment).live().count(), 0
        )
        rows = selectors.trial_balance()
        self.assertEqual(
            sum(r["debit_total"] for r in rows), sum(r["credit_total"] for r in rows)
        )

    def test_original_and_reversal_are_linked_and_both_stay_in_the_register(self):
        payment = self.pay("200.00", "CRD-LINK")
        reversal = self.reverse(payment, actor=self.admin)
        payment.refresh_from_db()
        self.assertTrue(payment.is_reversed)
        self.assertEqual(payment.reversal_reason, "bank error")
        self.assertEqual(reversal.reverses_id, payment.pk)
        self.assertEqual(payment.reversal.pk, reversal.pk)
        self.assertEqual(Payment.objects.filter(student=self.student).count(), 2)
        self.assertNotEqual(payment.receipt_number, reversal.receipt_number)

    def test_an_over_threshold_reversal_needs_an_admin_and_says_so(self):
        """A5: a big give-back is not a one-person job (threshold 500)."""
        payment = self.pay("600.00", "CRD-BIG")
        with self.assertRaises(ValidationError) as caught:
            self.reverse(payment, actor=self.officer)
        self.assertIn("approval threshold", str(caught.exception))
        payment.refresh_from_db()
        self.assertFalse(payment.is_reversed)

    def test_an_admin_named_as_approver_lets_the_officer_reverse(self):
        payment = self.pay("600.00", "CRD-BIG2")
        reversal = self.reverse(payment, actor=self.officer, approved_by=self.admin)
        self.assertEqual(reversal.amount, Decimal("-600.00"))

    def test_a_non_admin_cannot_be_named_as_the_approver(self):
        payment = self.pay("600.00", "CRD-BIG3")
        with self.assertRaises(ValidationError) as caught:
            self.reverse(payment, actor=self.officer, approved_by=self.officer)
        self.assertIn("Only an Admin", str(caught.exception))

    def test_a_small_reversal_needs_no_second_signature(self):
        payment = self.pay("100.00", "CRD-SMALL")
        self.reverse(payment, actor=self.officer)
        payment.refresh_from_db()
        self.assertTrue(payment.is_reversed)

    def test_reversing_an_overpayment_gives_the_surplus_back_from_credit(self):
        """
        The entry mirrors the original line by line, so the 100 that was sitting
        in Student Credit Balances goes back out of it rather than out of Fees
        Receivable, where it was never held.
        """
        payment = self.pay("600.00", "CRD-MIRROR")
        reversal = self.reverse(payment, actor=self.admin)
        entry = JournalEntry.objects.get(
            source_type=JournalEntry.SourceType.REVERSAL, source_id=str(payment.pk)
        )
        self.assertEqual(
            self.entry_lines(entry),
            {
                "1010": (Decimal("0.00"), Decimal("600.00")),
                "1100": (Decimal("600.00"), Decimal("100.00")),
                "2100": (Decimal("100.00"), Decimal("0.00")),
            },
        )
        self.assertEqual(reversal.amount, Decimal("-600.00"))
