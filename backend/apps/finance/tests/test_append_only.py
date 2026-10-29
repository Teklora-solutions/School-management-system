"""
apps/finance/tests/test_append_only.py
----------------------------------------
The ledger refuses to be edited, at every layer a writer can reach it from.

Three layers, because each one can be bypassed and the next cannot:

  - ``JournalEntry.save()`` / ``delete()`` — the row object,
  - ``AppendOnlyManager`` — the queryset a view or a management command would
    use to change fifty rows in one statement, and
  - the database triggers from migration 0007 — anyone holding the SQLite file
    open in a browser, or a future service that forgets the first two.

A posted receipt is evidence. If the school can quietly change what was
recorded, nothing downstream (a statement shown to a parent, a term's total in
a report, an audit of why the fee book changed) can be relied on either.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
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
from ..models import JournalEntry, JournalLine, Payment


class AppendOnlyTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.admin = make_user("admin", username="append_admin")
        cls.student = make_student_user(cls.school_class, admission_number="APD-001")
        cls.accounts = make_accounts()
        cls.structure = make_fee_structure(
            cls.school_class, cls.term, Decimal("500.00")
        )
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, Decimal("500.00")
        )

    def post(self, reference="APD-PAY-1", amount="150.00"):
        services.record_payment(
            student=self.student,
            amount=Decimal(amount),
            method=Payment.Method.BANK,
            reference=reference,
            payment_date=datetime.date(2025, 9, 10),
            recorded_by=self.admin,
        )
        return JournalEntry.objects.get(source_type=JournalEntry.SourceType.PAYMENT)


class QuerysetGuardTests(AppendOnlyTestBase):
    def test_a_bulk_update_is_refused(self):
        self.post()
        with self.assertRaises(ValidationError):
            JournalEntry.objects.update(description="rewritten history")

    def test_a_bulk_delete_is_refused(self):
        self.post()
        with self.assertRaises(ValidationError):
            JournalLine.objects.delete()

    def test_a_bulk_update_of_values_is_refused(self):
        self.post()
        line = JournalLine.objects.first()
        line.debit_amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            JournalLine.objects.bulk_update([line], ["debit_amount"])

    def test_the_refusal_names_the_correction_it_wants_instead(self):
        self.post()
        with self.assertRaises(ValidationError) as caught:
            JournalEntry.objects.all().delete()
        self.assertIn("revers", str(caught.exception).lower())


class DatabaseTriggerTests(AppendOnlyTestBase):
    """
    Raw SQL is the case the Python guards cannot cover.

    Skipped on a backend without the triggers: migration 0007 only installs
    them on SQLite, which is what this school runs. The Python guards above are
    what protect any other backend.
    """

    def setUp(self):
        if connection.vendor != "sqlite":
            self.skipTest("append-only triggers are SQLite-only")

    def test_raw_update_of_an_entry_is_refused(self):
        entry = self.post()
        with transaction.atomic(), self.assertRaises(IntegrityError):
            connection.cursor().execute(
                "UPDATE finance_journal_entry SET description = 'tampered' WHERE id = %s",
                [entry.pk],
            )

    def test_raw_delete_of_a_line_is_refused(self):
        self.post()
        line = JournalLine.objects.first()
        with transaction.atomic(), self.assertRaises(IntegrityError):
            connection.cursor().execute(
                "DELETE FROM finance_journal_line WHERE id = %s", [line.pk]
            )

    def test_raw_insert_is_still_allowed(self):
        """The ledger is append-only, not read-only — a posting must still work."""
        before = JournalEntry.objects.count()
        services._post_journal_entry(
            reference="APD-MANUAL-1",
            entry_date=datetime.date(2025, 9, 11),
            description="Cash withdrawal for the petty float",
            posted_by=self.admin,
            lines=[
                {
                    "account": self.accounts["1000"],
                    "debit": Decimal("20.00"),
                    "credit": Decimal(0),
                },
                {
                    "account": self.accounts["1010"],
                    "debit": Decimal(0),
                    "credit": Decimal("20.00"),
                },
            ],
            source_type=JournalEntry.SourceType.MANUAL,
        )
        self.assertEqual(JournalEntry.objects.count(), before + 1)

    def test_a_refused_edit_leaves_the_original_row_intact(self):
        entry = self.post()
        with self.assertRaises(IntegrityError), transaction.atomic():
            connection.cursor().execute(
                "UPDATE finance_journal_entry SET description = 'tampered' WHERE id = %s",
                [entry.pk],
            )
        self.assertEqual(
            JournalEntry.objects.get(pk=entry.pk).description, entry.description
        )
