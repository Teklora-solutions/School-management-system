"""
apps/finance/tests/test_money_interface.py
--------------------------------------------
Tests for the money read interface in ``selectors.py``.

Everything the Director portal, Finance's own pages and the exports print about
fees or the books comes out of that section, so these tests check it against
plain arithmetic, against the ledger, and against itself — never against a
snapshot of the selector's own output. Where a figure has two honest answers
(money per term vs money per date) the test says which one it is asserting.
"""

from __future__ import annotations

import datetime
from decimal import Decimal as D

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
from apps.finance.models import Account, Invoice, JournalEntry, Payment

from .. import selectors, services

TODAY = datetime.date.today()


class MoneyTestBase(TestCase):
    """One year, one term, two classes, one admin, the seeded accounts."""

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.cls_a = make_class(cls.year, name="Basic 5", level_order=5)
        cls.cls_b = make_class(cls.year, name="Basic 6", level_order=6)
        cls.accounts = make_accounts()
        cls.admin = make_user("admin", username="money_admin")
        cls.fs_a = make_fee_structure(cls.cls_a, cls.term, D("500.00"))
        cls.fs_b = make_fee_structure(cls.cls_b, cls.term, D("300.00"))

    @classmethod
    def pupil(cls, which, admission=None):
        school_class = cls.cls_a if which == "a" else cls.cls_b
        return make_student_user(school_class, admission_number=admission)

    @classmethod
    def bill(cls, student, structure, amount):
        return make_posted_invoice(student, cls.term, structure, D(amount))

    @classmethod
    def pay(cls, student, amount, reference, on=None, method=None):
        return services.record_payment(
            student=student,
            amount=D(amount),
            method=method or Payment.Method.BANK,
            reference=reference,
            payment_date=on or datetime.date(2025, 9, 10),
            recorded_by=cls.admin,
        )


class FeeTotalsDefinitionTests(MoneyTestBase):
    """billed / collected / adjusted / outstanding, and what each one means."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.paying = cls.pupil("a", "ADM-A1")
        cls.waived = cls.pupil("a", "ADM-A2")
        cls.owing = cls.pupil("b", "ADM-B1")
        cls.bill(cls.paying, cls.fs_a, "500.00")
        cls.bill(cls.waived, cls.fs_a, "500.00")
        cls.bill(cls.owing, cls.fs_b, "300.00")
        cls.pay(cls.paying, "200.00", "TOT-1")
        services.apply_adjustment(
            student=cls.waived,
            invoice=Invoice.objects.get(student=cls.waived),
            adjustment_type="waiver",
            amount=D("100.00"),
            reason="Staff child",
            actor=cls.admin,
        )

    def test_the_four_figures_add_up_the_way_the_interface_defines_them(self):
        totals = selectors.fee_totals(term=self.term)
        self.assertEqual(totals["billed"], D("1300.00"))
        self.assertEqual(totals["collected"], D("200.00"))
        self.assertEqual(totals["adjusted"], D("100.00"))
        self.assertEqual(totals["outstanding"], D("1000.00"))
        self.assertEqual(
            totals["outstanding"],
            totals["billed"] - totals["collected"] - totals["adjusted"],
        )

    def test_waived_money_is_not_debt(self):
        """
        The behaviour change D1 makes explicit: a waiver removes the amount from
        what anybody owes, not just from what has been paid.
        """
        totals = selectors.fee_totals(term=self.term, class_id=self.cls_a.pk)
        self.assertEqual(totals["billed"], D("1000.00"))
        self.assertEqual(totals["outstanding"], D("700.00"))

    def test_collection_rate_is_collected_over_billed(self):
        totals = selectors.fee_totals(term=self.term)
        self.assertEqual(totals["collection_rate"], D("15.4"))

    def test_a_term_with_no_billing_has_no_rate_rather_than_a_false_zero(self):
        empty_term = make_term(self.year, name="empty_term")
        totals = selectors.fee_totals(term=empty_term)
        self.assertEqual(totals["billed"], D("0.00"))
        self.assertEqual(totals["outstanding"], D("0.00"))
        self.assertIsNone(totals["collection_rate"])

    def test_draft_and_cancelled_invoices_are_not_billing(self):
        student = self.pupil("a", "ADM-A3")
        Invoice.objects.create(
            student=student,
            term=self.term,
            fee_structure=self.fs_a,
            issued_date=TODAY,
            total_amount=D("999.00"),
            status=Invoice.Status.DRAFT,
        )
        self.assertEqual(
            selectors.fee_totals(term=self.term)["billed"], D("1300.00")
        )

    def test_three_queries_whatever_the_size_of_the_school(self):
        with self.assertNumQueries(3):
            selectors.fee_totals(term=self.term)


class ReversalTests(MoneyTestBase):
    """A reversal must leave no trace in what the term collected."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student = cls.pupil("a", "ADM-REV")
        cls.bill(cls.student, cls.fs_a, "500.00")

    def test_collected_returns_to_zero_after_a_reversal(self):
        payment = self.pay(self.student, "200.00", "REV-1")
        self.assertEqual(
            selectors.fee_totals(term=self.term)["collected"], D("200.00")
        )
        services.reverse_payment(payment=payment, reason="bank error", actor=self.admin)
        totals = selectors.fee_totals(term=self.term)
        self.assertEqual(totals["collected"], D("0.00"))
        self.assertEqual(totals["outstanding"], D("500.00"))

    def test_the_reversal_still_shows_in_the_cash_window_it_was_made_in(self):
        payment = self.pay(self.student, "200.00", "REV-2")
        services.reverse_payment(payment=payment, reason="duplicate", actor=self.admin)
        cash = selectors.cash_received(
            date_from=datetime.date(2025, 9, 1),
            date_to=TODAY + datetime.timedelta(days=1),
        )
        self.assertEqual(cash["gross"], D("200.00"))
        self.assertEqual(cash["reversed"], D("200.00"))
        self.assertEqual(cash["net"], D("0.00"))


class ClassRowTests(MoneyTestBase):
    """Per-class rows must reconcile with the school totals they sit under."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.a1 = cls.pupil("a", "ADM-C1")
        cls.a2 = cls.pupil("a", "ADM-C2")
        cls.b1 = cls.pupil("b", "ADM-C3")
        cls.bill(cls.a1, cls.fs_a, "500.00")
        cls.bill(cls.a2, cls.fs_a, "500.00")
        cls.bill(cls.b1, cls.fs_b, "300.00")
        cls.pay(cls.a1, "400.00", "CL-1")
        services.apply_adjustment(
            student=cls.a2,
            invoice=Invoice.objects.get(student=cls.a2),
            adjustment_type="discount",
            amount=D("50.00"),
            reason="Early payment",
            actor=cls.admin,
        )

    def test_rows_reconcile_with_school_totals(self):
        rows = selectors.class_fee_rows(term=self.term)
        totals = selectors.fee_totals(term=self.term)
        for key in ("billed", "collected", "adjusted", "outstanding"):
            self.assertEqual(sum((r[key] for r in rows), D("0.00")), totals[key], key)

    def test_a_row_is_ordered_by_class_level_and_carries_the_class_id(self):
        rows = selectors.class_fee_rows(term=self.term)
        self.assertEqual([r["class_name"] for r in rows], ["Basic 5", "Basic 6"])
        self.assertEqual(
            [r["class_id"] for r in rows], [self.cls_a.pk, self.cls_b.pk]
        )

    def test_keys_are_the_interface_vocabulary_the_director_consumes(self):
        self.assertEqual(
            set(selectors.class_fee_rows(term=self.term)[0]),
            {
                "class_id",
                "class_name",
                "level_order",
                "billed",
                "collected",
                "adjusted",
                "outstanding",
                "collection_rate",
            },
        )

    def test_a_class_that_has_never_billed_is_absent_not_zeroed(self):
        make_class(self.year, name="Basic 7", level_order=7)
        names = [r["class_name"] for r in selectors.class_fee_rows(term=self.term)]
        self.assertNotIn("Basic 7", names)

    def test_three_group_bys_for_the_whole_school(self):
        with self.assertNumQueries(3):
            selectors.class_fee_rows(term=self.term)


class StudentRowTests(MoneyTestBase):
    """Per-pupil position: one row per pupil, amounts summed across invoices."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.payer = cls.pupil("a", "ADM-S1")
        cls.credited = cls.pupil("a", "ADM-S2")
        cls.untouched = cls.pupil("a", "ADM-S3")
        cls.never_billed = cls.pupil("a", "ADM-S4")
        cls.bill(cls.payer, cls.fs_a, "500.00")
        Invoice.objects.create(
            student=cls.payer,
            term=cls.term,
            fee_structure=cls.fs_b,
            issued_date=datetime.date(2025, 9, 6),
            total_amount=D("300.00"),
            status=Invoice.Status.POSTED,
        )
        cls.bill(cls.credited, cls.fs_a, "500.00")
        cls.bill(cls.untouched, cls.fs_a, "500.00")
        cls.pay(cls.payer, "400.00", "ST-1", on=datetime.date(2025, 9, 10))
        cls.pay(cls.payer, "100.00", "ST-2", on=datetime.date(2025, 10, 15))
        cls.pay(cls.credited, "550.00", "ST-3")

    def test_two_invoices_collapse_into_one_row_that_adds_them(self):
        rows = selectors.student_fee_rows(term=self.term)
        row = next(r for r in rows if r["admission_number"] == "ADM-S1")
        self.assertEqual(row["billed"], D("800.00"))
        self.assertEqual(row["collected"], D("500.00"))
        self.assertEqual(row["outstanding"], D("300.00"))

    def test_last_payment_date_is_the_latest_allocation_in_the_term(self):
        rows = selectors.student_fee_rows(term=self.term)
        row = next(r for r in rows if r["admission_number"] == "ADM-S1")
        self.assertEqual(row["last_payment_date"], datetime.date(2025, 10, 15))

    def test_a_pupil_who_was_never_billed_is_not_a_row(self):
        adm_numbers = [r["admission_number"] for r in selectors.student_fee_rows(term=self.term)]
        self.assertNotIn("ADM-S4", adm_numbers)

    def test_debtors_only_drops_credit_balances(self):
        rows = selectors.student_fee_rows(term=self.term, only_debtors=True)
        adm_numbers = [r["admission_number"] for r in rows]
        self.assertIn("ADM-S1", adm_numbers)
        self.assertNotIn("ADM-S2", adm_numbers)  # -50.00: a credit, not a debt

    def test_untouched_pupil_is_a_debtor_for_the_whole_bill(self):
        rows = selectors.student_fee_rows(term=self.term, only_debtors=True)
        row = next(r for r in rows if r["admission_number"] == "ADM-S3")
        self.assertEqual(row["outstanding"], D("500.00"))

    def test_debtors_are_listed_largest_debt_first(self):
        rows = selectors.student_fee_rows(term=self.term, only_debtors=True)
        amounts = [r["outstanding"] for r in rows]
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_scoping_to_a_class_only_lists_that_class(self):
        rows = selectors.student_fee_rows(term=self.term, class_id=self.cls_b.pk)
        self.assertEqual(rows, [])

    def test_one_query_for_the_whole_school(self):
        with self.assertNumQueries(1):
            selectors.student_fee_rows(term=self.term)


class OutstandingAgingTests(MoneyTestBase):
    """Ageing is measured per invoice, from the due date, or from issue if none."""

    AS_OF = datetime.date(2025, 12, 31)

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # One pupil per invoice: a term may only hold one invoice per
        # (student, fee structure), and ageing is about the bills, not the pupils.
        cls.over_90 = cls.pupil("a", "ADM-AGE-90")
        for admission, days_over, amount in (
            ("AGE-0", -10, "100.00"),
            ("AGE-1", 10, "200.00"),
            ("AGE-2", 45, "300.00"),
            ("AGE-3", 75, "400.00"),
        ):
            student = cls.pupil("a", admission)
            Invoice.objects.create(
                student=student,
                term=cls.term,
                fee_structure=cls.fs_a,
                issued_date=cls.AS_OF - datetime.timedelta(days=days_over + 30),
                due_date=cls.AS_OF - datetime.timedelta(days=days_over),
                total_amount=D(amount),
                status=Invoice.Status.POSTED,
            )
        Invoice.objects.create(
            student=cls.over_90,
            term=cls.term,
            fee_structure=cls.fs_a,
            issued_date=cls.AS_OF - datetime.timedelta(days=120),
            due_date=cls.AS_OF - datetime.timedelta(days=120),
            total_amount=D("500.00"),
            status=Invoice.Status.POSTED,
        )
        # No due date at all: ages from the issue date instead.
        Invoice.objects.create(
            student=cls.pupil("a", "AGE-NODUE"),
            term=cls.term,
            fee_structure=cls.fs_b,
            issued_date=cls.AS_OF - datetime.timedelta(days=100),
            due_date=None,
            total_amount=D("600.00"),
            status=Invoice.Status.POSTED,
        )

    def age(self):
        return selectors.outstanding_aging(term=self.term, as_of=self.AS_OF)

    def test_every_bucket_gets_the_invoices_it_should(self):
        buckets = {b["key"]: b for b in self.age()["buckets"]}
        self.assertEqual(buckets["not_due"]["count"], 1)
        self.assertEqual(buckets["due_1_30"]["count"], 1)
        self.assertEqual(buckets["due_31_60"]["count"], 1)
        self.assertEqual(buckets["due_61_90"]["count"], 1)
        self.assertEqual(buckets["over_90"]["count"], 2)  # incl. the no-due-date bill

    def test_amounts_are_the_balance_owing_not_the_original_bill(self):
        self.pay(self.over_90, "150.00", "AGE-PAY")
        buckets = {b["key"]: b for b in self.age()["buckets"]}
        self.assertEqual(buckets["over_90"]["amount"], D("350.00") + D("600.00"))

    def test_the_buckets_totally_answer_the_same_question_as_fee_totals(self):
        self.assertEqual(
            self.age()["total"], selectors.fee_totals(term=self.term)["outstanding"]
        )

    def test_labels_and_order_come_from_the_registry(self):
        keys = [b["key"] for b in self.age()["buckets"]]
        self.assertEqual(keys, [k for k, _, _ in selectors.AGING_BUCKETS])

    def test_one_query_reads_the_whole_ledger_of_due_dates(self):
        with self.assertNumQueries(1):
            self.age()


class CashWindowTests(MoneyTestBase):
    """What was received in a window, and where it went."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student = cls.pupil("a", "ADM-CASH")
        cls.bill(cls.student, cls.fs_a, "500.00")

    def window(self):
        # ``reverse_payment`` dates the reversal today, so the window has to
        # reach today for the netted figure to be testable.
        return selectors.cash_received(
            date_from=datetime.date(2025, 9, 1), date_to=TODAY + datetime.timedelta(days=1)
        )

    def test_an_overpayment_is_visible_as_unallocated_not_lost(self):
        self.pay(self.student, "700.00", "CASH-1")
        cash = self.window()
        self.assertEqual(cash["net"], D("700.00"))
        self.assertEqual(cash["applied_to_posted_invoices"], D("500.00"))
        self.assertEqual(cash["unallocated"], D("200.00"))

    def test_the_two_bases_answer_two_different_questions(self):
        """
        A payment received in September against an invoice billed for this term
        is the same money on both bases; the gap only appears when it is not, so
        the interface says so rather than pretending the figures must match.
        """
        self.pay(self.student, "500.00", "CASH-2")
        cash = self.window()
        fees = selectors.fee_totals(term=self.term)
        self.assertEqual(cash["net"], fees["collected"])
        self.assertEqual(cash["unallocated"], D("0.00"))

    def test_count_only_includes_real_receipts(self):
        payment = self.pay(self.student, "300.00", "CASH-3")
        services.reverse_payment(payment=payment, reason="error", actor=self.admin)
        cash = self.window()
        self.assertEqual(cash["count"], 1)
        self.assertEqual(cash["net"], D("0.00"))

    def test_receipts_register_total_nets_the_same_as_cash_received(self):
        window = {
            "date_from": datetime.date(2025, 9, 1),
            "date_to": TODAY + datetime.timedelta(days=1),
        }
        payment = self.pay(self.student, "300.00", "CASH-4")
        register = selectors.receipts_register(**window)
        self.assertEqual(register["total"], self.window()["net"])
        self.assertEqual(register["count"], 1)
        services.reverse_payment(payment=payment, reason="x", actor=self.admin)
        register = selectors.receipts_register(**window)
        self.assertEqual(register["total"], D("0.00"))
        self.assertEqual(register["count"], 2)  # the reversal is a row of its own

    def test_collected_by_month_reconciles_with_the_term_figure(self):
        self.pay(self.student, "200.00", "MON-1", on=datetime.date(2025, 9, 10))
        self.pay(self.student, "150.00", "MON-2", on=datetime.date(2025, 10, 4))
        months = selectors.collected_by_month(term=self.term)
        self.assertEqual([m["month_label"] for m in months], ["Sep 2025", "Oct 2025"])
        self.assertEqual(months[-1]["cumulative"], D("350.00"))
        self.assertEqual(
            months[-1]["cumulative"], selectors.fee_totals(term=self.term)["collected"]
        )


class BooksOfAccountTests(MoneyTestBase):
    """Ledger reads: holdings, income statement, trial balance."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student = cls.pupil("a", "ADM-BOOKS")
        services.post_invoices(term=cls.term, school_class=cls.cls_a, actor=cls.admin)

    def test_posting_an_invoice_journals_income_into_the_window_it_was_posted_in(self):
        statement = selectors.income_statement(
            date_from=TODAY - datetime.timedelta(days=1),
            date_to=TODAY + datetime.timedelta(days=1),
        )
        self.assertEqual(statement["total_income"], D("500.00"))
        fee_line = next(line for line in statement["income"] if line["code"] == "4000")
        self.assertEqual(fee_line["amount"], D("500.00"))

    def test_the_statement_window_is_the_ledger_not_the_term(self):
        """A term that has already closed shows no income for invoices posted today."""
        statement = selectors.income_statement(
            date_from=self.term.start_date, date_to=self.term.end_date
        )
        self.assertEqual(statement["total_income"], D("0.00"))

    def test_a_receipt_settles_into_the_account_for_its_method(self):
        """
        No fee is received in cash, so 1000 holds only the petty float.

        The float gets there by a withdrawal from the bank, not by a parent
        handing over notes: a receipt is dated against the wallet or the bank
        account it actually arrived in, which is what lets the day's takings be
        checked against a statement.
        """
        self.pay(self.student, "50.00", "BK-1", on=datetime.date(2025, 9, 2))
        services.record_payment(
            student=self.student,
            amount=D("100.00"),
            method=Payment.Method.BANK,
            reference="BK-2",
            payment_date=datetime.date(2025, 9, 11),
            recorded_by=self.admin,
        )
        self.pay(
            self.student,
            "60.00",
            "MM-1",
            on=datetime.date(2025, 9, 12),
            method=Payment.Method.MOBILE_MONEY,
        )
        services._post_journal_entry(
            reference="FLOAT-1",
            entry_date=datetime.date(2025, 9, 3),
            description="Withdrawal to float the petty cash box",
            posted_by=self.admin,
            lines=[
                {"account": self.accounts["1000"], "debit": D("40.00"), "credit": D(0)},
                {"account": self.accounts["1010"], "debit": D(0), "credit": D("40.00")},
            ],
            source_type=JournalEntry.SourceType.MANUAL,
        )
        balances = selectors.cash_and_bank_balances(as_of=datetime.date(2025, 9, 30))
        self.assertEqual(balances["cash"], D("40.00"))
        self.assertEqual(balances["bank"], D("110.00"))
        self.assertEqual(balances["mobile_money"], D("60.00"))
        self.assertEqual(balances["total"], D("210.00"))

    def test_holdings_as_of_an_earlier_date_do_not_see_later_money(self):
        self.pay(self.student, "200.00", "BK-3", on=datetime.date(2025, 9, 10))
        balances = selectors.cash_and_bank_balances(as_of=datetime.date(2025, 9, 1))
        self.assertEqual(balances["total"], D("0.00"))

    def test_a_refund_reduces_the_expense_instead_of_becoming_income(self):
        expense_account = self.accounts["6000"]
        services.record_expense(
            account=expense_account,
            amount=D("120.00"),
            expense_date=datetime.date(2025, 9, 10),
            description="Diesel",
            actor=self.admin,
        )
        services._post_journal_entry(
            reference="REFUND-1",
            entry_date=datetime.date(2025, 9, 12),
            description="Diesel supplier refund",
            posted_by=self.admin,
            lines=[
                {"account": expense_account, "debit": D(0), "credit": D("20.00")},
                {
                    "account": self.accounts["1000"],
                    "debit": D("20.00"),
                    "credit": D(0),
                },
            ],
            source_type=JournalEntry.SourceType.MANUAL,
        )
        statement = selectors.income_statement(
            date_from=datetime.date(2025, 9, 1), date_to=datetime.date(2025, 9, 30)
        )
        diesel = next(line for line in statement["expenses"] if line["code"] == "6000")
        self.assertEqual(diesel["amount"], D("100.00"))
        self.assertEqual(statement["total_expense"], D("100.00"))

    def test_top_expense_categories_is_largest_first_and_skips_zeroes(self):
        # 5100, not 5000: a waiver is income given up, so it is not a category
        # of spend. This test used to rank 5000 as the school's biggest expense.
        for code_amount in (("6000", "40.00"), ("5100", "90.00")):
            services.record_expense(
                account=self.accounts[code_amount[0]],
                amount=D(code_amount[1]),
                expense_date=datetime.date(2025, 9, 10),
                description=f"expense {code_amount[0]}",
                actor=self.admin,
            )
        top = selectors.top_expense_categories(
            date_from=datetime.date(2025, 9, 1), date_to=datetime.date(2025, 9, 30)
        )
        self.assertEqual([line["code"] for line in top], ["5100", "6000"])
        self.assertEqual(len(selectors.top_expense_categories(
            date_from=datetime.date(2025, 9, 1),
            date_to=datetime.date(2025, 9, 30),
            limit=1,
        )), 1)

    def test_monthly_series_is_charts_ready_and_adds_to_the_statement(self):
        self.pay(self.student, "100.00", "MTH-1", on=datetime.date(2025, 9, 10))
        services.record_expense(
            account=self.accounts["6000"],
            amount=D("30.00"),
            expense_date=datetime.date(2025, 9, 20),
            description="Generator fuel",
            actor=self.admin,
        )
        rows = selectors.monthly_income_expense(
            date_from=datetime.date(2025, 9, 1), date_to=datetime.date(2025, 10, 31)
        )
        self.assertEqual([r["month_label"] for r in rows], ["Sep 2025"])
        self.assertEqual(rows[0]["expense"], D("30.00"))
        # Money leaves the interface as a Decimal. A view that builds chart
        # JSON casts at that boundary; the figure itself is never a float.
        self.assertIsInstance(rows[0]["income"], D)

    def test_the_trial_balance_balances_and_says_so(self):
        self.pay(self.student, "100.00", "TB-1", on=datetime.date(2025, 9, 10))
        balance = selectors.trial_balance_at()
        self.assertEqual(balance["total_debits"], balance["total_credits"])
        self.assertEqual(
            sum(r["debit_total"] for r in balance["rows"]), balance["total_debits"]
        )
        self.assertTrue(all(r["account"] for r in balance["rows"]))

    def test_as_of_date_excludes_entries_after_it(self):
        """
        The invoice journals today (posting-date basis) and the payment back in
        September, so a September cutoff sees one leg and today sees both.
        """
        self.pay(self.student, "100.00", "TB-2", on=datetime.date(2025, 9, 10))
        before = selectors.trial_balance_at(date_to=datetime.date(2025, 9, 15))
        self.assertEqual(before["as_of"], datetime.date(2025, 9, 15))
        self.assertEqual(before["total_debits"], D("100.00"))
        after = selectors.trial_balance_at(date_to=TODAY)
        self.assertEqual(after["total_debits"], D("600.00"))

    def test_two_queries_not_two_per_account(self):
        with self.assertNumQueries(2):
            selectors.trial_balance_at()

    def test_account_balances_flip_to_the_accounts_own_side(self):
        balances = {b["code"]: b for b in selectors.account_balances()}
        self.assertGreater(balances["1100"]["balance"], D("0.00"))  # receivable, debit
        self.assertGreater(balances["4000"]["balance"], D("0.00"))  # income, credit
        self.assertEqual(balances["1100"]["type"], Account.Type.ASSET)

    def test_fee_item_breakdown_names_what_the_total_is_made_of(self):
        rows = selectors.fee_item_breakdown(term=self.term)
        self.assertEqual([r["label"] for r in rows], ["Tuition"])
        self.assertEqual(rows[0]["amount"], D("500.00"))
        self.assertEqual(rows[0]["line_count"], 1)


class ReportSummaryTests(MoneyTestBase):
    """The one place both bases are printed side by side."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student = cls.pupil("a", "ADM-RPT")
        services.post_invoices(term=cls.term, school_class=cls.cls_a, actor=cls.admin)
        cls.pay(cls.student, "200.00", "RPT-1", on=datetime.date(2025, 9, 10))

    def test_fee_half_is_the_terms_and_books_half_the_ledgers(self):
        summary = selectors.report_summary(term_id=self.term.pk)
        fees = selectors.fee_totals(term=self.term)
        statement = selectors.income_statement(
            date_from=self.term.start_date, date_to=self.term.end_date
        )
        self.assertEqual(summary["total_billed"], fees["billed"])
        self.assertEqual(summary["total_collected"], fees["collected"])
        self.assertEqual(summary["total_income"], statement["total_income"])
        self.assertEqual(summary["net"], statement["net"])
        self.assertEqual(
            summary["outstanding"], fees["billed"] - fees["collected"] - fees["adjusted"]
        )

    def test_the_report_carries_its_own_line_items_not_just_totals(self):
        summary = selectors.report_summary(term_id=self.term.pk)
        self.assertIn("income_lines", summary)
        self.assertIn("expense_lines", summary)
