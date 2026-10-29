"""
apps/finance/tests/test_expenses.py
Checkpoint 4a: expense governance, the approval queue and budgets.

The rule this file pins is A5 segregation: an officer who spends is not the
person who authorises. Above the school's threshold, a claim or write-off is
recorded as a pending ask with NO ledger line. Only an Admin's decision — made
through the ordinary posting service — creates the journal entry. Every approval
test here checks both sides: what exists before the decision, and what the
ledger looks like after.

The other rule the file pins is budget variance is never stored. It is journal
debits minus the sanctioned number, read live, so a late posting cannot leave
a stale variance sitting next to a correct total.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

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

from .. import selectors, services
from ..chart_of_accounts import GENERAL_EXPENSES_CODE
from ..models import (
    Adjustment,
    ApprovalRequest,
    Budget,
    Expense,
    FinanceSettings,
    Vendor,
)

D = Decimal
CLAIM_DATE = datetime.date(2025, 9, 10)


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------


class ExpenseBase(TestCase):
    """
    One school, one term, one expense account, two people.

    ``admin`` can post anything; ``officer`` must ask when the amount is gated.
    Every test in this file inherits exactly those two actors.
    """

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.admin = make_user("admin", username="exp_admin")
        cls.officer = make_user("finance", username="exp_officer")
        cls.accounts = make_accounts()
        cls.expense_account = cls.accounts[GENERAL_EXPENSES_CODE]

    def claim(
        self,
        amount,
        actor=None,
        description="Generator fuel",
        payee_name="Acme Supplies",
        **kwargs,
    ):
        return services.record_expense(
            account=self.expense_account,
            amount=D(amount),
            expense_date=CLAIM_DATE,
            description=description,
            payee_name=payee_name,
            actor=actor if actor is not None else self.admin,
            **kwargs,
        )


class VendorTests(ExpenseBase):
    """A vendor is a supplier the school pays repeatedly — not a User."""

    def test_create_vendor_succeeds(self):
        vendor = services.create_vendor(
            name="Acme Supplies", contact_person="Kwame", actor=self.admin
        )
        self.assertEqual(vendor.name, "Acme Supplies")
        self.assertTrue(vendor.is_active)

    def test_duplicate_name_is_refused_case_insensitively(self):
        services.create_vendor(name="Acme Supplies", actor=self.admin)
        with self.assertRaises(ValidationError):
            services.create_vendor(name="acme supplies", actor=self.admin)

    def test_blank_name_is_refused(self):
        with self.assertRaises(ValidationError):
            services.create_vendor(name="   ", actor=self.admin)

    def test_retire_vendor_clears_is_active_without_deleting(self):
        vendor = services.create_vendor(name="Old Co", actor=self.admin)
        services.retire_vendor(vendor=vendor, actor=self.admin)
        vendor.refresh_from_db()
        self.assertFalse(vendor.is_active)
        self.assertTrue(Vendor.objects.filter(pk=vendor.pk).exists())

    def test_retired_vendor_not_in_active_selector(self):
        vendor = services.create_vendor(name="Old Co", actor=self.admin)
        services.retire_vendor(vendor=vendor, actor=self.admin)
        self.assertNotIn(vendor, selectors.vendor_list(active_only=True))
        self.assertIn(vendor, selectors.vendor_list(active_only=False))

    def test_update_vendor_touches_only_named_fields(self):
        vendor = services.create_vendor(
            name="Beta Ltd", phone="000", actor=self.admin
        )
        services.update_vendor(vendor=vendor, phone="111", actor=self.admin)
        vendor.refresh_from_db()
        self.assertEqual(vendor.phone, "111")
        self.assertEqual(vendor.name, "Beta Ltd")


class ExpenseNumberingTests(ExpenseBase):
    """
    A posted claim gets its reference from the expense sequence, not a UUID.

    The register a bursar reads must be gap-free: EXP-2025-000001,
    EXP-2025-000002, etc. A random UUID cannot answer "is there a hole in the
    register?" so it was replaced by the sequence.
    """

    def test_posted_claim_gets_sequence_reference(self):
        expense = self.claim("40.00")
        self.assertTrue(expense.reference)
        self.assertIn("2025", expense.reference)

    def test_two_claims_have_different_references(self):
        first = self.claim("40.00")
        second = self.claim("50.00")
        self.assertNotEqual(first.reference, second.reference)

    def test_pending_claim_has_no_reference(self):
        settings_row = FinanceSettings.load()
        settings_row.approval_expense_threshold = D("10.00")
        settings_row.save(update_fields=["approval_expense_threshold"])
        expense = self.claim("40.00", actor=self.officer)
        self.assertEqual(expense.status, Expense.Status.PENDING)
        self.assertIsNone(expense.reference)

    def test_posting_pending_claim_fills_reference(self):
        settings_row = FinanceSettings.load()
        settings_row.approval_expense_threshold = D("10.00")
        settings_row.save(update_fields=["approval_expense_threshold"])
        expense = self.claim("40.00", actor=self.officer)
        services.post_expense(expense=expense, actor=self.admin)
        expense.refresh_from_db()
        self.assertTrue(expense.reference)


class ExpenseApprovalGateTests(ExpenseBase):
    """
    Above the threshold, an officer's claim waits for an Admin.

    The gate fires only for an actor who is not an Admin, and only when the
    amount exceeds ``approval_expense_threshold``. An Admin posts immediately —
    same service, same two ledger lines — and a claim below the threshold also
    posts immediately whatever the actor.
    """

    def setUp(self):
        settings_row = FinanceSettings.load()
        settings_row.approval_expense_threshold = D("1000.00")
        settings_row.save(update_fields=["approval_expense_threshold"])

    def test_over_threshold_officer_claim_is_held_pending(self):
        expense = self.claim("1450.00", actor=self.officer)
        self.assertEqual(expense.status, Expense.Status.PENDING)
        self.assertIsNone(expense.journal_entry_id)

    def test_over_threshold_pending_claim_creates_approval_request(self):
        expense = self.claim("1450.00", actor=self.officer)
        ask = ApprovalRequest.objects.get(expense=expense)
        self.assertEqual(ask.kind, ApprovalRequest.Kind.EXPENSE)
        self.assertEqual(ask.status, ApprovalRequest.Status.PENDING)
        self.assertEqual(ask.amount, D("1450.00"))
        self.assertEqual(ask.requested_by, self.officer)

    def test_admin_over_threshold_posts_immediately(self):
        expense = self.claim("1450.00", actor=self.admin)
        self.assertEqual(expense.status, Expense.Status.POSTED)
        self.assertIsNotNone(expense.journal_entry_id)

    def test_under_threshold_officer_posts_immediately(self):
        expense = self.claim("380.00", actor=self.officer)
        self.assertEqual(expense.status, Expense.Status.POSTED)
        self.assertIsNotNone(expense.journal_entry_id)

    def test_exactly_at_threshold_officer_posts_immediately(self):
        # ``amount_needs_approval`` uses ``>``: at the threshold, not above it,
        # the gate does not fire. Pinned here so a future change to ``>=`` is
        # visible and not a silent tightening.
        expense = self.claim("1000.00", actor=self.officer)
        self.assertEqual(expense.status, Expense.Status.POSTED)

    def test_post_expense_by_non_admin_is_refused(self):
        expense = self.claim("1450.00", actor=self.officer)
        with self.assertRaises(ValidationError):
            services.post_expense(expense=expense, actor=self.officer)

    def test_post_expense_by_admin_creates_journal_entry(self):
        expense = self.claim("1450.00", actor=self.officer)
        services.post_expense(expense=expense, actor=self.admin)
        expense.refresh_from_db()
        self.assertEqual(expense.status, Expense.Status.POSTED)
        self.assertIsNotNone(expense.journal_entry_id)

    def test_posting_already_posted_claim_is_refused(self):
        expense = self.claim("380.00")
        with self.assertRaises(ValidationError):
            services.post_expense(expense=expense, actor=self.admin)

    def test_two_open_asks_on_one_claim_is_refused(self):
        expense = self.claim("1450.00", actor=self.officer)
        # The partial unique constraint in 0010 allows only one pending ask.
        with self.assertRaises(IntegrityError), transaction.atomic():
            ApprovalRequest.objects.create(
                kind=ApprovalRequest.Kind.EXPENSE,
                amount=D("1450.00"),
                reason="second officer",
                expense=expense,
                requested_by=self.officer,
            )


class WriteOffApprovalGateTests(ExpenseBase):
    """
    A write-off above the threshold, asked by an officer, waits for an Admin.

    Discount, scholarship and waiver adjustments are not gated — they are billing
    decisions the school makes routinely. Only WRITE_OFF checks the rails here,
    and only above the expense threshold or when ``write_off_always_needs_approval``
    is set.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student = make_student_user(cls.school_class, admission_number="WO-001")
        cls.structure = make_fee_structure(cls.school_class, cls.term, D("500.00"))
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, D("500.00")
        )

    def setUp(self):
        settings_row = FinanceSettings.load()
        settings_row.approval_expense_threshold = D("1000.00")
        settings_row.write_off_always_needs_approval = False
        settings_row.save(
            update_fields=[
                "approval_expense_threshold",
                "write_off_always_needs_approval",
            ]
        )

    def adjust(
        self, adjustment_type, amount="1500.00", actor=None, reason="Bad debt"
    ):
        return services.apply_adjustment(
            student=self.student,
            invoice=self.invoice,
            adjustment_type=adjustment_type,
            amount=D(amount),
            reason=reason,
            actor=actor if actor is not None else self.admin,
        )

    def test_over_threshold_officer_write_off_is_held_pending(self):
        adj = self.adjust(
            Adjustment.AdjustmentType.WRITE_OFF, actor=self.officer
        )
        self.assertEqual(adj.status, Adjustment.Status.PENDING)
        self.assertIsNone(adj.journal_entry_id)

    def test_pending_write_off_creates_approval_request(self):
        adj = self.adjust(
            Adjustment.AdjustmentType.WRITE_OFF, actor=self.officer
        )
        ask = ApprovalRequest.objects.get(adjustment=adj)
        self.assertEqual(ask.kind, ApprovalRequest.Kind.WRITE_OFF)
        self.assertEqual(ask.status, ApprovalRequest.Status.PENDING)

    def test_admin_write_off_posts_immediately(self):
        adj = self.adjust(Adjustment.AdjustmentType.WRITE_OFF)
        self.assertEqual(adj.status, Adjustment.Status.POSTED)
        self.assertIsNotNone(adj.journal_entry_id)

    def test_under_threshold_officer_write_off_posts_immediately(self):
        adj = self.adjust(
            Adjustment.AdjustmentType.WRITE_OFF,
            amount="500.00",
            actor=self.officer,
        )
        self.assertEqual(adj.status, Adjustment.Status.POSTED)

    def test_discount_by_officer_is_never_gated(self):
        adj = self.adjust(
            Adjustment.AdjustmentType.DISCOUNT,
            amount="1500.00",
            actor=self.officer,
        )
        self.assertEqual(adj.status, Adjustment.Status.POSTED)

    def test_always_flag_gates_any_write_off_amount(self):
        settings_row = FinanceSettings.load()
        settings_row.write_off_always_needs_approval = True
        settings_row.save(update_fields=["write_off_always_needs_approval"])
        adj = self.adjust(
            Adjustment.AdjustmentType.WRITE_OFF,
            amount="1.00",
            actor=self.officer,
        )
        self.assertEqual(adj.status, Adjustment.Status.PENDING)

    def test_post_adjustment_by_admin_creates_journal_entry(self):
        adj = self.adjust(
            Adjustment.AdjustmentType.WRITE_OFF, actor=self.officer
        )
        services.post_adjustment(adjustment=adj, actor=self.admin)
        adj.refresh_from_db()
        self.assertEqual(adj.status, Adjustment.Status.POSTED)
        self.assertIsNotNone(adj.journal_entry_id)

    def test_post_adjustment_by_non_admin_is_refused(self):
        adj = self.adjust(
            Adjustment.AdjustmentType.WRITE_OFF, actor=self.officer
        )
        with self.assertRaises(ValidationError):
            services.post_adjustment(adjustment=adj, actor=self.officer)


class PendingAdjustmentExcludedFromMoneyTests(ExpenseBase):
    """
    A pending write-off does not reduce the pupil's debt until it is posted.

    Every money reader uses ``Adjustment.objects.live()`` which filters
    ``status=POSTED``. Before posting, the ask exists as a queue row only.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student = make_student_user(cls.school_class, admission_number="LIVE-001")
        cls.structure = make_fee_structure(cls.school_class, cls.term, D("500.00"))
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, D("500.00")
        )

    def setUp(self):
        settings_row = FinanceSettings.load()
        settings_row.approval_expense_threshold = D("10.00")
        settings_row.write_off_always_needs_approval = True
        settings_row.save(
            update_fields=[
                "approval_expense_threshold",
                "write_off_always_needs_approval",
            ]
        )

    def pending_write_off(self):
        return services.apply_adjustment(
            student=self.student,
            invoice=self.invoice,
            adjustment_type=Adjustment.AdjustmentType.WRITE_OFF,
            amount=D("200.00"),
            reason="Awaiting approval",
            actor=self.officer,
        )

    def test_fee_totals_excludes_pending_adjustment(self):
        self.pending_write_off()
        totals = selectors.fee_totals(term=self.term)
        self.assertEqual(totals["adjusted"], D("0.00"))
        self.assertEqual(
            totals["outstanding"], D("500.00")
        )

    def test_fee_totals_includes_posted_adjustment(self):
        adj = self.pending_write_off()
        services.post_adjustment(adjustment=adj, actor=self.admin)
        totals = selectors.fee_totals(term=self.term)
        self.assertEqual(totals["adjusted"], D("200.00"))
        self.assertEqual(totals["outstanding"], D("300.00"))

    def test_arrears_rows_excludes_pending_adjustment(self):
        # A 200 write-off that would bring the pupil below zero must not do so
        # until posted: the arrears list must reflect the debt as it truly stands.
        adj = self.pending_write_off()
        rows = selectors.arrears_rows(term=self.term)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["adjusted"], D("0.00"))
        services.post_adjustment(adjustment=adj, actor=self.admin)
        rows = selectors.arrears_rows(term=self.term)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["adjusted"], D("200.00"))


class DecideApprovalTests(ExpenseBase):
    """
    Approving performs through the ordinary service.

    The queue cannot post money that the posting path would refuse: the approval
    calls ``post_expense``, ``post_adjustment``, ``reverse_payment`` or
    ``reopen_period``, and the same rules apply. Self-approval is refused: the
    person who asked is not the person who signs.
    """

    def setUp(self):
        settings_row = FinanceSettings.load()
        settings_row.approval_expense_threshold = D("10.00")
        settings_row.save(update_fields=["approval_expense_threshold"])

    def pending_claim_ask(self):
        expense = self.claim("100.00", actor=self.officer)
        return expense, ApprovalRequest.objects.get(expense=expense)

    def test_admin_approves_claim_posts_the_expense(self):
        expense, ask = self.pending_claim_ask()
        services.decide_approval(
            approval=ask,
            decision=ApprovalRequest.Status.APPROVED,
            actor=self.admin,
        )
        expense.refresh_from_db()
        self.assertEqual(expense.status, Expense.Status.POSTED)
        self.assertIsNotNone(expense.journal_entry_id)

    def test_admin_rejects_claim_leaves_no_journal_entry(self):
        expense, ask = self.pending_claim_ask()
        services.decide_approval(
            approval=ask,
            decision=ApprovalRequest.Status.REJECTED,
            note="No budget for this.",
            actor=self.admin,
        )
        expense.refresh_from_db()
        self.assertEqual(expense.status, Expense.Status.REJECTED)
        self.assertIsNone(expense.journal_entry_id)

    def test_non_admin_cannot_decide(self):
        _, ask = self.pending_claim_ask()
        with self.assertRaises(ValidationError):
            services.decide_approval(
                approval=ask,
                decision=ApprovalRequest.Status.APPROVED,
                actor=self.officer,
            )

    def test_self_approval_is_refused(self):
        # An Admin who created a gated claim (via a threshold set to zero, say)
        # still cannot be the one who approves it.
        _expense, ask = self.pending_claim_ask()
        ask.requested_by = self.admin
        ask.save(update_fields=["requested_by"])
        with self.assertRaises(ValidationError):
            services.decide_approval(
                approval=ask,
                decision=ApprovalRequest.Status.APPROVED,
                actor=self.admin,
            )

    def test_already_decided_ask_cannot_be_reopen(self):
        _, ask = self.pending_claim_ask()
        services.decide_approval(
            approval=ask,
            decision=ApprovalRequest.Status.REJECTED,
            actor=self.admin,
        )
        with self.assertRaises(ValidationError):
            services.decide_approval(
                approval=ask,
                decision=ApprovalRequest.Status.APPROVED,
                actor=self.admin,
            )

    def test_approval_records_approver_and_note(self):
        _, ask = self.pending_claim_ask()
        services.decide_approval(
            approval=ask,
            decision=ApprovalRequest.Status.APPROVED,
            note="Approved by Finance Committee.",
            actor=self.admin,
        )
        ask.refresh_from_db()
        self.assertEqual(ask.decided_by, self.admin)
        self.assertEqual(ask.decision_note, "Approved by Finance Committee.")
        self.assertIsNotNone(ask.decided_at)

    def test_write_off_ask_approved(self):
        settings_row = FinanceSettings.load()
        settings_row.write_off_always_needs_approval = True
        settings_row.save(update_fields=["write_off_always_needs_approval"])
        student = make_student_user(
            self.school_class, admission_number="WO-DECIDE"
        )
        structure = make_fee_structure(self.school_class, self.term, D("300.00"))
        make_posted_invoice(student, structure.term, structure, D("300.00"))
        adj = services.apply_adjustment(
            student=student,
            invoice=None,
            adjustment_type=Adjustment.AdjustmentType.WRITE_OFF,
            amount=D("100.00"),
            reason="Bad debt decision",
            actor=self.officer,
        )
        ask = ApprovalRequest.objects.get(adjustment=adj)
        services.decide_approval(
            approval=ask,
            decision=ApprovalRequest.Status.APPROVED,
            actor=self.admin,
        )
        adj.refresh_from_db()
        self.assertEqual(adj.status, Adjustment.Status.POSTED)
        self.assertIsNotNone(adj.journal_entry_id)


class ApprovalQueueSelectorTests(ExpenseBase):
    """The queue is what the Admin sees when they open the page."""

    def setUp(self):
        settings_row = FinanceSettings.load()
        settings_row.approval_expense_threshold = D("10.00")
        settings_row.save(update_fields=["approval_expense_threshold"])

    def test_approval_queue_returns_pending_asks(self):
        self.claim("100.00", actor=self.officer)
        queue = selectors.approval_queue(status=ApprovalRequest.Status.PENDING)
        self.assertEqual(queue.count(), 1)

    def test_decided_ask_drops_out_of_pending_queue(self):
        expense = self.claim("100.00", actor=self.officer)
        ask = ApprovalRequest.objects.get(expense=expense)
        services.decide_approval(
            approval=ask,
            decision=ApprovalRequest.Status.APPROVED,
            actor=self.admin,
        )
        queue = selectors.approval_queue(status=ApprovalRequest.Status.PENDING)
        self.assertEqual(queue.count(), 0)

    def test_queue_sorted_oldest_first(self):
        settings_row = FinanceSettings.load()
        settings_row.approval_expense_threshold = D("10.00")
        settings_row.save(update_fields=["approval_expense_threshold"])
        first = self.claim("100.00", actor=self.officer, description="First claim")
        second = self.claim("200.00", actor=self.officer, description="Second claim")
        # Force distinct created_at values.
        first_ask = ApprovalRequest.objects.get(expense=first)
        second_ask = ApprovalRequest.objects.get(expense=second)
        queue = list(selectors.approval_queue(status=ApprovalRequest.Status.PENDING))
        self.assertEqual(queue[0].pk, first_ask.pk)
        self.assertEqual(queue[1].pk, second_ask.pk)

    def test_queue_filter_by_kind(self):
        self.claim("100.00", actor=self.officer)
        expense_asks = selectors.approval_queue(kind=ApprovalRequest.Kind.EXPENSE)
        write_off_asks = selectors.approval_queue(
            kind=ApprovalRequest.Kind.WRITE_OFF
        )
        self.assertEqual(expense_asks.count(), 1)
        self.assertEqual(write_off_asks.count(), 0)


class RequestReversalTests(ExpenseBase):
    """
    An officer asks for a reversal rather than reversing it unilaterally.

    ``reverse_payment`` already refuses a large reversal by a non-Admin.
    ``request_reversal`` is the route that refusal points to: the ask sits in
    the queue, the receipt keeps taking money off the balance until the Admin
    approves and the reversal actually runs.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student = make_student_user(cls.school_class, admission_number="REV-001")
        cls.structure = make_fee_structure(cls.school_class, cls.term, D("300.00"))
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, D("300.00")
        )

    def setUp(self):
        settings_row = FinanceSettings.load()
        settings_row.approval_reversal_threshold = D("10.00")
        settings_row.save(update_fields=["approval_reversal_threshold"])

    def make_payment(self):
        return services.record_payment(
            student=self.student,
            amount=D("300.00"),
            method="bank",
            payment_date=CLAIM_DATE,
            reference="REV-PAY-001",
            allocations=[{"invoice": self.invoice, "amount": D("300.00")}],
            recorded_by=self.admin,
        )

    def test_request_reversal_creates_approval_row(self):
        payment = self.make_payment()
        ask = services.request_reversal(
            payment=payment, reason="Wrong pupil", actor=self.officer
        )
        self.assertEqual(ask.kind, ApprovalRequest.Kind.REVERSAL)
        self.assertEqual(ask.status, ApprovalRequest.Status.PENDING)
        self.assertEqual(ask.payment_id, payment.pk)

    def test_request_reversal_does_not_reverse(self):
        payment = self.make_payment()
        services.request_reversal(
            payment=payment, reason="Wrong pupil", actor=self.officer
        )
        payment.refresh_from_db()
        self.assertFalse(payment.is_reversed)

    def test_already_reversed_payment_is_refused(self):
        payment = self.make_payment()
        services.reverse_payment(
            payment=payment, reason="Done by admin", actor=self.admin
        )
        with self.assertRaises(ValidationError):
            services.request_reversal(
                payment=payment, reason="Wrong pupil", actor=self.officer
            )

    def test_approve_reversal_ask_runs_the_reversal(self):
        payment = self.make_payment()
        ask = services.request_reversal(
            payment=payment, reason="Wrong pupil", actor=self.officer
        )
        services.decide_approval(
            approval=ask,
            decision=ApprovalRequest.Status.APPROVED,
            actor=self.admin,
        )
        payment.refresh_from_db()
        self.assertTrue(payment.is_reversed)


class RequestPeriodReopenTests(ExpenseBase):
    """
    A reopening ask carries no amount — the risk is the whole window.

    The queue row is there so the Admin sees the ask; the period stays closed
    until the ask is approved.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from ..models import FinancialPeriod

        cls.period = FinancialPeriod.objects.create(
            kind=FinancialPeriod.Kind.MONTH,
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 9, 30),
            status=FinancialPeriod.Status.CLOSED,
            closed_by=cls.admin,
        )

    def test_period_reopen_ask_has_no_amount(self):
        ask = services.request_period_reopen(
            period=self.period, reason="Late receipt", actor=self.officer
        )
        self.assertIsNone(ask.amount)
        self.assertEqual(ask.kind, ApprovalRequest.Kind.PERIOD_REOPEN)

    def test_period_stays_closed_after_ask(self):
        services.request_period_reopen(
            period=self.period, reason="Late receipt", actor=self.officer
        )
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, "closed")

    def test_open_period_reopen_ask_is_refused(self):
        self.period.status = "open"
        self.period.save(update_fields=["status"])
        with self.assertRaises(ValidationError):
            services.request_period_reopen(
                period=self.period, reason="Why", actor=self.officer
            )


class BudgetTests(ExpenseBase):
    """
    Budget is the sanctioned number; variance is never stored.

    A late posting must not leave a stale variance next to a correct total, so
    the variance is read live from journal debits against the Budget figure.
    """

    def test_set_budget_creates_row(self):
        budget = services.set_budget(
            term=self.term,
            account=self.expense_account,
            amount=D("5000.00"),
            note="Approved by Board.",
            actor=self.admin,
        )
        self.assertEqual(budget.amount, D("5000.00"))
        self.assertEqual(budget.term, self.term)

    def test_re_set_budget_updates_existing_row(self):
        services.set_budget(
            term=self.term,
            account=self.expense_account,
            amount=D("5000.00"),
            actor=self.admin,
        )
        services.set_budget(
            term=self.term,
            account=self.expense_account,
            amount=D("6000.00"),
            actor=self.admin,
        )
        self.assertEqual(Budget.objects.count(), 1)
        budget = Budget.objects.get(term=self.term, account=self.expense_account)
        self.assertEqual(budget.amount, D("6000.00"))

    def test_duplicate_term_account_is_rejected_at_db_level(self):
        Budget.objects.create(
            term=self.term, account=self.expense_account, amount=D("5000.00")
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Budget.objects.create(
                term=self.term,
                account=self.expense_account,
                amount=D("9000.00"),
            )

    def test_zero_budget_is_refused(self):
        with self.assertRaises(ValidationError):
            services.set_budget(
                term=self.term,
                account=self.expense_account,
                amount=D("0.00"),
                actor=self.admin,
            )

    def test_negative_budget_is_refused(self):
        with self.assertRaises(ValidationError):
            services.set_budget(
                term=self.term,
                account=self.expense_account,
                amount=D("-1.00"),
                actor=self.admin,
            )

    def test_budget_on_income_account_is_refused(self):
        income_account = self.accounts["4000"]
        with self.assertRaises(ValidationError):
            services.set_budget(
                term=self.term,
                account=income_account,
                amount=D("1000.00"),
                actor=self.admin,
            )

    def test_budget_variance_reflects_posted_expense(self):
        services.set_budget(
            term=self.term,
            account=self.expense_account,
            amount=D("5000.00"),
            actor=self.admin,
        )
        self.claim("1200.00")  # admin, posted immediately
        rows = selectors.budget_variance(term_id=self.term.pk)
        row = rows[0]
        self.assertEqual(row["budget"], D("5000.00"))
        self.assertEqual(row["spent"], D("1200.00"))
        self.assertEqual(row["variance"], D("3800.00"))

    def test_budget_variance_excludes_pending_claim_from_spent(self):
        settings_row = FinanceSettings.load()
        settings_row.approval_expense_threshold = D("10.00")
        settings_row.save(update_fields=["approval_expense_threshold"])
        services.set_budget(
            term=self.term,
            account=self.expense_account,
            amount=D("5000.00"),
            actor=self.admin,
        )
        self.claim("100.00", actor=self.officer)  # gated: pending
        rows = selectors.budget_variance(term_id=self.term.pk)
        self.assertEqual(rows[0]["spent"], D("0.00"))

    def test_budget_alert_fires_at_configured_percent(self):
        settings_row = FinanceSettings.load()
        settings_row.budget_alert_percent = D("80.00")
        settings_row.save(update_fields=["budget_alert_percent"])
        services.set_budget(
            term=self.term,
            account=self.expense_account,
            amount=D("1000.00"),
            actor=self.admin,
        )
        self.claim("850.00")  # 85% > 80% → alert
        rows = selectors.budget_variance(term_id=self.term.pk)
        self.assertTrue(rows[0]["alert"])

    def test_budget_alert_does_not_fire_below_threshold(self):
        settings_row = FinanceSettings.load()
        settings_row.budget_alert_percent = D("80.00")
        settings_row.save(update_fields=["budget_alert_percent"])
        services.set_budget(
            term=self.term,
            account=self.expense_account,
            amount=D("1000.00"),
            actor=self.admin,
        )
        self.claim("500.00")  # 50% < 80% → no alert
        rows = selectors.budget_variance(term_id=self.term.pk)
        self.assertFalse(rows[0]["alert"])


class ExpenseListSelectorTests(ExpenseBase):
    """``expense_list`` filters by status and vendor."""

    def setUp(self):
        settings_row = FinanceSettings.load()
        settings_row.approval_expense_threshold = D("10.00")
        settings_row.save(update_fields=["approval_expense_threshold"])

    def test_status_filter_pending(self):
        self.claim("100.00", actor=self.officer)  # pending
        self.claim("5.00", actor=self.officer)  # posted (under threshold)
        pending = selectors.expense_list(status=Expense.Status.PENDING)
        posted = selectors.expense_list(status=Expense.Status.POSTED)
        self.assertEqual(pending.count(), 1)
        self.assertEqual(posted.count(), 1)

    def test_vendor_filter(self):
        vendor = services.create_vendor(name="Vendor A", actor=self.admin)
        services.record_expense(
            account=self.expense_account,
            amount=D("50.00"),
            expense_date=CLAIM_DATE,
            description="Test",
            vendor=vendor,
            actor=self.admin,
        )
        services.record_expense(
            account=self.expense_account,
            amount=D("30.00"),
            expense_date=CLAIM_DATE,
            description="No vendor",
            payee_name="Random",
            actor=self.admin,
        )
        rows = selectors.expense_list(vendor_id=vendor.pk)
        self.assertEqual(rows.count(), 1)


class ExpensePayeePropertyTests(ExpenseBase):
    """``Expense.payee`` returns the vendor name, payee_name, or em dash."""

    def test_payee_prefers_vendor_name(self):
        vendor = services.create_vendor(name="Beta Ltd", actor=self.admin)
        expense = services.record_expense(
            account=self.expense_account,
            amount=D("50.00"),
            expense_date=CLAIM_DATE,
            description="Test",
            vendor=vendor,
            payee_name="ignored when vendor set",
            actor=self.admin,
        )
        self.assertEqual(expense.payee, "Beta Ltd")

    def test_payee_falls_back_to_payee_name(self):
        expense = self.claim("40.00", payee_name="Casual worker")
        self.assertEqual(expense.payee, "Casual worker")

    def test_payee_returns_dash_when_nothing_set(self):
        expense = services.record_expense(
            account=self.expense_account,
            amount=D("20.00"),
            expense_date=CLAIM_DATE,
            description="Old claim",
            actor=self.admin,
        )
        self.assertEqual(expense.payee, "—")
