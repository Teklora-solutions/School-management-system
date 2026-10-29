"""
apps/finance/tests/test_numbering.py
------------------------------------
Gap-free invoice and receipt sequences (Part F: "sequential numbering must be
gap-free and unique under concurrency").

The register is the control an auditor uses: after RCP-2025-00041 comes
RCP-2025-00042, and a hole is a question about money that nobody can answer. So
these tests are about the shape of the sequence, what happens when a posting is
rolled back, and what happens when two writers want the same number.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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
from ..models import FinanceSettings, Invoice, NumberSeries, Payment


class NumberingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.admin = make_user("admin", username="num_admin")
        cls.student = make_student_user(cls.school_class, admission_number="NUM-001")
        cls.other = make_student_user(cls.school_class, admission_number="NUM-002")
        cls.accounts = make_accounts()
        cls.structure = make_fee_structure(
            cls.school_class, cls.term, Decimal("500.00")
        )
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, Decimal("500.00")
        )

    def setUp(self):
        # A receipt is numbered by the day it was paid, so the dated receipts
        # below land in 2025. An invoice is numbered on the day it is issued,
        # which is today, so anything made through ``post_invoices`` lands in
        # the real current year — read from the clock rather than hard-coded so
        # the file still passes next January.
        self.day = datetime.date(2025, 9, 10)
        self.today = datetime.date.today()

    def series(self, kind, year):
        return NumberSeries.objects.get(kind=kind, year=year)

    def pay(self, reference, day=None, student=None):
        return services.record_payment(
            student=student or self.student,
            amount=Decimal("50.00"),
            method=Payment.Method.BANK,
            reference=reference,
            payment_date=day or self.day,
            recorded_by=self.admin,
        )

    def test_the_first_receipt_is_number_one_in_the_configured_format(self):
        payment = self.pay("NUM-A")
        self.assertEqual(payment.receipt_number, "RCP-2025-00001")
        self.assertEqual(self.series(NumberSeries.Kind.RECEIPT, 2025).next_number, 2)

    def test_the_sequence_runs_without_a_hole_however_many_students_pay(self):
        receipts = [self.pay(f"NUM-{n}").receipt_number for n in range(1, 6)]
        self.assertEqual(
            receipts,
            [f"RCP-2025-{n:05d}" for n in range(1, 6)],
            msg="A receipts register with a hole in it is the thing numbering exists to stop.",
        )

    def test_a_back_dated_receipt_lands_in_the_year_it_belongs_to(self):
        payment = self.pay("NUM-LATE", day=datetime.date(2024, 12, 31))
        self.assertEqual(payment.receipt_number, "RCP-2024-00001")
        self.assertTrue(
            NumberSeries.objects.filter(
                kind=NumberSeries.Kind.RECEIPT, year=2024
            ).exists()
        )

    def test_an_invoice_and_a_receipt_do_not_share_a_counter(self):
        """One series per document type: the first receipt must not push INV to 2."""
        self.pay("NUM-SPLIT")
        services.post_invoices(
            term=self.term, school_class=self.school_class, actor=self.admin
        )
        made = Invoice.objects.get(student=self.other)
        self.assertEqual(made.invoice_number, f"INV-{self.today.year}-00001")

    def test_a_rolled_back_receipt_hands_its_number_back(self):
        self.pay("NUM-KEEP")
        with self.assertRaises(IntegrityError), transaction.atomic():
            payment = self.pay("NUM-ROLLBACK")
            # The number is taken inside the posting's transaction, so undoing
            # the posting undoes the take as well.
            payment.reference = "NUM-KEEP"
            payment.save()
        self.assertEqual(self.series(NumberSeries.Kind.RECEIPT, 2025).next_number, 2)
        self.assertEqual(self.pay("NUM-NEXT").receipt_number, "RCP-2025-00002")

    def test_a_number_contested_by_another_writer_is_taken_next_not_skipped(self):
        """
        The compare-and-set losing once must not burn a number.

        The other writer is simulated by updating the row between reading it and
        trying to claim it, which is what a committed concurrent transaction
        amounts to. SQLite cannot row-lock, so this is the whole mechanism, and
        it is tested against the mechanism rather than a lock that is not taken.
        """
        self.pay("NUM-FIRST")  # the series row must already exist to be read
        real_filter = NumberSeries.objects.filter
        contested = {"done": False}

        def filter_with_intruder(*args, **kwargs):
            queryset = real_filter(*args, **kwargs)
            if "next_number" in kwargs:
                return queryset
            original_first = queryset.first

            def first():
                row = original_first()
                if row is not None and not contested["done"]:
                    contested["done"] = True
                    real_filter(pk=row.pk).update(next_number=row.next_number + 1)
                return row

            queryset.first = first
            return queryset

        with mock.patch.object(NumberSeries.objects, "filter", filter_with_intruder):
            number = services.next_document_number(
                NumberSeries.Kind.RECEIPT, day=self.day
            )

        self.assertTrue(contested["done"], msg="The intruder never ran.")
        self.assertEqual(
            number,
            "RCP-2025-00003",
            msg="The intruder took 00002, so the re-read must take 00003.",
        )
        self.assertEqual(self.series(NumberSeries.Kind.RECEIPT, 2025).next_number, 4)

    def test_the_cas_primitive_refuses_to_issue_a_number_twice(self):
        series = NumberSeries.objects.create(
            kind=NumberSeries.Kind.INVOICE, year=2025, prefix="INV", next_number=1
        )
        won = NumberSeries.objects.filter(pk=series.pk, next_number=1).update(
            next_number=2
        )
        lost = NumberSeries.objects.filter(pk=series.pk, next_number=1).update(
            next_number=2
        )
        self.assertEqual((won, lost), (1, 0))

    def test_renaming_the_prefix_does_not_renumber_what_was_issued(self):
        first = self.pay("NUM-P1")
        services.update_finance_settings(receipt_prefix="TCH", actor=self.admin)
        second = self.pay("NUM-P2")
        self.assertEqual(first.receipt_number, "RCP-2025-00001")
        self.assertEqual(second.receipt_number, "RCP-2025-00002")

    def test_a_fresh_year_starts_at_one_again(self):
        first = self.pay("NUM-2025")
        number = services.next_document_number(
            NumberSeries.Kind.RECEIPT,
            day=datetime.date(2026, 1, 5),
            settings_row=FinanceSettings.load(),
        )
        self.assertEqual(number, "RCP-2026-00001")
        self.assertEqual(first.receipt_number, "RCP-2025-00001")
        self.assertEqual(
            self.series(NumberSeries.Kind.RECEIPT, 2025).next_number,
            2,
            msg="Crossing into a new year must not move the old year's counter.",
        )

    def test_an_unknown_series_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValidationError) as caught:
            services.next_document_number("cheque")
        self.assertIn("not a document series", str(caught.exception))

    def test_a_second_run_over_the_same_students_burns_no_invoice_numbers(self):
        """
        Idempotency and numbering have to agree.

        ``post_invoices`` skips pupils who already have this term's invoice, and
        a skipped run must not take numbers for documents it never made — that
        would put the holes back.
        """
        first = services.post_invoices(
            term=self.term, school_class=self.school_class, actor=self.admin
        )
        counter = self.series(NumberSeries.Kind.INVOICE, self.today.year).next_number
        self.assertEqual(counter, 2, msg="One new invoice out of two pupils.")

        again = services.post_invoices(
            term=self.term, school_class=self.school_class, actor=self.admin
        )
        self.assertEqual(
            self.series(NumberSeries.Kind.INVOICE, self.today.year).next_number,
            counter,
        )
        self.assertEqual(Invoice.objects.count(), len(first))
        self.assertEqual(
            [invoice.invoice_number for invoice in again],
            [invoice.invoice_number for invoice in first],
        )
