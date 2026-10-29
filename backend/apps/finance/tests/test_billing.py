"""
apps/finance/tests/test_billing.py
----------------------------------
Checkpoint 3b — the billing half of Part B: a fee list that says which items are
optional and when each is due, a structure that can be copied into the next term,
recurring reductions applied by the run itself, the late-fee rule that is stored
but never posted, the arrears that follow a pupil up a class, and a payment plan
the receipts are measured against.

Two ideas run through the whole file.

*Preview is the run.* ``preview_invoice_generation`` and ``post_invoices`` read
one ``_billing_plan``, so the counts and money a Finance Officer approves are the
arithmetic the commit performs — not a description of it written twice.

*A reduction is an adjustment, not a different kind of money.* A discount lowers
what a pupil owes by posting the ordinary DR Discounts / CR Fees Receivable entry
against the ordinary posted invoice. The invoice still bills the full structure,
because that is what the structure said, and ``fee_totals`` already answers
"billed, collected, waived, outstanding" for it.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import ParentProfile
from apps.core.models import AuditLog
from apps.dashboards.tests.fixtures import (
    make_accounts,
    make_class,
    make_student_user,
    make_term,
    make_user,
    make_year,
)
from apps.students.models import PromotionRecord, Student, StudentGuardian
from apps.students.services import commit_promotion

from .. import selectors, services
from ..models import (
    REDUCTION_TYPES,
    Adjustment,
    FeeDiscountRule,
    FeeItem,
    FeeStructure,
    FinanceSettings,
    Invoice,
    JournalEntry,
    Payment,
    PaymentPlan,
    StudentFeeElection,
)

TODAY = datetime.date.today()


class BillingTestBase(TestCase):
    """One class, one term, one compulsory item and one optional one."""

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.accounts = make_accounts()
        cls.admin = make_user("admin", username="bil_admin")
        cls.officer = make_user("finance", username="bil_officer")
        cls.pupil = make_student_user(cls.school_class, admission_number="BIL-001")
        cls.classmate = make_student_user(
            cls.school_class, admission_number="BIL-002"
        )
        cls.structure = FeeStructure.objects.create(
            school_class=cls.school_class,
            term=cls.term,
            version=1,
            is_active=True,
            due_date=TODAY + datetime.timedelta(days=30),
        )
        cls.tuition = FeeItem.objects.create(
            structure=cls.structure,
            label="Tuition",
            amount=Decimal("400.00"),
            ordering=1,
        )
        cls.transport = FeeItem.objects.create(
            structure=cls.structure,
            label="Transport",
            amount=Decimal("100.00"),
            ordering=2,
            compulsory=False,
        )

    def bill_class(self, *, term=None, school_class=None):
        return services.post_invoices(
            term=term or self.term,
            school_class=school_class or self.school_class,
            actor=self.officer,
        )

    def enroll(self, student, fee_item):
        return services.set_student_fee_item(
            student=student, fee_item=fee_item, included=True, actor=self.officer
        )

    def pay(self, student, amount, reference, *, on=None):
        return services.record_payment(
            student=student,
            amount=Decimal(amount),
            method=Payment.Method.BANK,
            reference=reference,
            payment_date=on or TODAY,
            recorded_by=self.officer,
        )

    def lines_of(self, invoice):
        return list(invoice.lines.order_by("id"))


class ReductionTypesPinnedTests(TestCase):
    """
    The drift this test exists to catch: ``FeeDiscountRule`` cannot reach for
    ``Adjustment.AdjustmentType`` (that model is declared below it in
    ``models.py``), so the two lists are written separately and this pins them
    together. A rule that learned to say "write_off" would let a recurring
    discount give up a debt with no person deciding it.
    """

    def test_the_rule_choices_are_exactly_the_three_ways_a_bill_goes_down(self):
        self.assertEqual(
            [code for code, _ in REDUCTION_TYPES],
            [
                Adjustment.AdjustmentType.DISCOUNT.value,
                Adjustment.AdjustmentType.SCHOLARSHIP.value,
                Adjustment.AdjustmentType.WAIVER.value,
            ],
        )
        self.assertNotIn(
            Adjustment.AdjustmentType.WRITE_OFF.value,
            [code for code, _ in REDUCTION_TYPES],
        )
        self.assertNotIn(
            Adjustment.AdjustmentType.OTHER.value,
            [code for code, _ in REDUCTION_TYPES],
        )

    def test_every_rule_choice_is_a_real_adjustment_type(self):
        valid = {value for value, _ in Adjustment.AdjustmentType.choices}
        for code, _ in REDUCTION_TYPES:
            self.assertIn(code, valid, msg=f"{code} is not an Adjustment type")

    def test_a_write_off_cannot_be_a_recurring_rule(self):
        rule = FeeDiscountRule(
            name="Generous",
            basis=FeeDiscountRule.Basis.PERCENT,
            percent=Decimal("10.00"),
            adjustment_type="write_off",
        )
        with self.assertRaises(ValidationError) as caught:
            rule.full_clean(exclude=["students", "created_by"])
        self.assertIn("adjustment_type", caught.exception.message_dict)


class FeeStructureCopyTests(BillingTestBase):
    def test_copying_reproduces_every_item_and_marks_the_source(self):
        next_term = make_term(self.year, name="term_2", is_current=False)
        copy = services.copy_fee_structure(
            source=self.structure, term=next_term, actor=self.officer
        )
        self.assertEqual(copy.copied_from_id, self.structure.pk)
        self.assertEqual(
            [(item.label, item.amount) for item in copy.items.all()],
            [("Tuition", Decimal("400.00")), ("Transport", Decimal("100.00"))],
        )

    def test_an_optional_item_stays_optional_in_the_copy(self):
        """
        Losing the flag would silently bill every pupil for transport next term,
        and the first anyone would hear is a parent disputing the receipt.
        """
        next_term = make_term(self.year, name="term_2", is_current=False)
        copy = services.copy_fee_structure(source=self.structure, term=next_term)
        flags = {item.label: item.compulsory for item in copy.items.all()}
        self.assertEqual(flags, {"Tuition": True, "Transport": False})

    def test_a_deadline_from_last_term_is_not_a_deadline_for_the_next_one(self):
        next_term = make_term(self.year, name="term_2", is_current=False)
        copy = services.copy_fee_structure(source=self.structure, term=next_term)
        self.assertIsNone(copy.due_date)

    def test_copying_onto_a_term_that_already_bills_bumps_the_version(self):
        services.create_fee_structure(
            school_class=self.school_class,
            term=self.term,
            items=[{"label": "Tuition", "amount": Decimal("450.00")}],
        )
        self.assertEqual(FeeStructure.objects.filter(term=self.term).count(), 2)
        newest = FeeStructure.objects.filter(term=self.term).order_by("-version").first()
        self.assertEqual(newest.version, 2)
        self.assertTrue(newest.is_active)
        self.assertFalse(
            FeeStructure.objects.filter(term=self.term)
            .exclude(pk=newest.pk)
            .filter(is_active=True)
            .exists()
        )

    def test_a_structure_with_no_items_is_refused_rather_than_copied_as_nothing(self):
        empty = FeeStructure.objects.create(
            school_class=self.school_class,
            term=make_term(self.year, name="term_9", is_current=False),
            version=1,
        )
        next_term = make_term(self.year, name="term_2", is_current=False)
        with self.assertRaises(ValidationError) as caught:
            services.copy_fee_structure(source=empty, term=next_term)
        self.assertIn("no fee items", str(caught.exception))

    def test_editing_the_copy_cannot_move_an_invoice_posted_under_the_source(self):
        """
        B2's versioning rule, tested where it is actually at risk: the copy is a
        fresh set of rows, so the posted line keeps its own amount.
        """
        posted = self.bill_class()[0]
        next_term = make_term(self.year, name="term_2", is_current=False)
        copy = services.copy_fee_structure(source=self.structure, term=next_term)
        copy.items.update(amount=Decimal("9999.00"))
        posted.refresh_from_db()
        self.assertEqual(posted.total_amount, Decimal("400.00"))
        self.assertEqual(self.lines_of(posted)[0].amount, Decimal("400.00"))


class OptionalItemTests(BillingTestBase):
    def test_an_optional_item_is_not_billed_unless_the_pupil_is_entered_for_it(self):
        posted = self.bill_class()
        self.assertEqual([invoice.total_amount for invoice in posted], [
            Decimal("400.00"),
            Decimal("400.00"),
        ])
        self.assertEqual(
            [line.label for line in self.lines_of(posted[0])], ["Tuition"]
        )

    def test_an_elected_item_is_billed_to_that_pupil_and_nobody_else(self):
        self.enroll(self.pupil, self.transport)
        self.bill_class()
        mine = Invoice.objects.get(student=self.pupil)
        theirs = Invoice.objects.get(student=self.classmate)
        self.assertEqual(mine.total_amount, Decimal("500.00"))
        self.assertEqual(theirs.total_amount, Decimal("400.00"))
        self.assertEqual(
            [line.label for line in self.lines_of(mine)], ["Tuition", "Transport"]
        )

    def test_electing_a_pupil_out_and_back_in_leaves_one_row(self):
        services.set_student_fee_item(
            student=self.pupil, fee_item=self.transport, included=False
        )
        self.enroll(self.pupil, self.transport)
        self.assertEqual(StudentFeeElection.objects.count(), 1)

    def test_a_compulsory_item_cannot_be_elected_off(self):
        """
        An opt-out of tuition is a waiver, and a waiver is a ledger line somebody
        owns. It must go through ``apply_adjustment``, not a checkbox.
        """
        with self.assertRaises(ValidationError) as caught:
            services.set_student_fee_item(
                student=self.pupil,
                fee_item=self.tuition,
                included=False,
                actor=self.officer,
            )
        self.assertIn("waiver adjustment", str(caught.exception))
        self.assertEqual(StudentFeeElection.objects.count(), 0)

    def test_changing_an_election_after_posting_does_not_restate_the_invoice(self):
        posted = self.bill_class()[0]
        self.enroll(self.pupil, self.transport)
        posted.refresh_from_db()
        self.assertEqual(posted.total_amount, Decimal("400.00"))
        self.assertEqual(len(self.lines_of(posted)), 1)

    def test_preview_and_the_run_agree_on_every_item_and_amount(self):
        self.enroll(self.pupil, self.transport)
        preview = services.preview_invoice_generation(
            term=self.term, school_class=self.school_class
        )
        posted = self.bill_class()
        self.assertEqual(preview["counts"]["create"], 2)
        self.assertEqual(preview["billed"], Decimal("900.00"))
        self.assertEqual(
            preview["billed"], sum(invoice.total_amount for invoice in posted)
        )
        self.assertEqual(
            sorted(row["total_amount"] for row in preview["rows"]),
            sorted(str(invoice.total_amount) for invoice in posted),
        )


class DueDateTests(BillingTestBase):
    def test_the_structure_due_date_lands_on_the_invoice(self):
        invoice = self.bill_class()[0]
        self.assertEqual(invoice.due_date, self.structure.due_date)

    def test_an_item_keeps_its_own_deadline_on_the_line(self):
        when = TODAY + datetime.timedelta(days=7)
        self.transport.due_date = when
        self.transport.save(update_fields=["due_date"])
        self.transport.compulsory = True
        self.transport.save(update_fields=["compulsory"])
        invoice = self.bill_class()[0]
        lines = {line.label: line for line in self.lines_of(invoice)}
        self.assertEqual(lines["Transport"].due_date, when)
        self.assertEqual(lines["Tuition"].due_date, self.structure.due_date)

    def test_without_a_structure_date_the_earliest_item_date_becomes_the_bills(self):
        self.structure.due_date = None
        self.structure.save(update_fields=["due_date"])
        self.tuition.due_date = TODAY + datetime.timedelta(days=45)
        self.tuition.save(update_fields=["due_date"])
        invoice = self.bill_class()[0]
        self.assertEqual(invoice.due_date, self.tuition.due_date)

    def test_editing_the_structure_after_posting_leaves_the_snapshots_alone(self):
        """
        A deadline is a promise written on a document a family is holding. The
        invoice and its lines keep the dates they were posted with, so moving the
        fee list afterwards changes the next bill, not the one already issued.
        """
        invoice = self.bill_class()[0]
        posted_bill_date = invoice.due_date
        posted_line_date = self.lines_of(invoice)[0].due_date
        self.structure.due_date = TODAY + datetime.timedelta(days=1)
        self.structure.save(update_fields=["due_date"])
        self.tuition.due_date = TODAY + datetime.timedelta(days=2)
        self.tuition.save(update_fields=["due_date"])
        invoice.refresh_from_db()
        self.assertEqual(invoice.due_date, posted_bill_date)
        self.assertEqual(self.lines_of(invoice)[0].due_date, posted_line_date)


class DiscountRuleTests(BillingTestBase):
    def rule(self, **kwargs):
        return services.create_discount_rule(actor=self.officer, **kwargs)

    def test_a_percentage_rule_reduces_what_the_pupil_owes_not_what_was_billed(self):
        self.rule(name="Term bursary", basis="percent", percent=Decimal("10.00"))
        invoice = self.bill_class()[0]
        totals = selectors.fee_totals(term=self.term)
        self.assertEqual(totals["billed"], Decimal("800.00"))
        self.assertEqual(totals["adjusted"], Decimal("80.00"))
        self.assertEqual(totals["outstanding"], Decimal("720.00"))
        self.assertEqual(invoice.total_amount, Decimal("400.00"))

    def test_the_reduction_posts_the_ordinary_discount_entry(self):
        self.rule(name="Term bursary", basis="percent", percent=Decimal("10.00"))
        self.bill_class()
        entry = JournalEntry.objects.filter(
            source_type=JournalEntry.SourceType.ADJUSTMENT
        ).first()
        self.assertIsNotNone(entry)
        sides = {
            line.account.code: (line.debit_amount, line.credit_amount)
            for line in entry.lines.select_related("account")
        }
        self.assertEqual(sides["5000"], (Decimal("40.00"), Decimal("0.00")))
        self.assertEqual(sides["1100"], (Decimal("0.00"), Decimal("40.00")))
        balance = selectors.trial_balance_at()
        self.assertEqual(balance["total_debits"], balance["total_credits"])

    def test_a_fixed_rule_on_one_item_reduces_that_item_only(self):
        self.rule(
            name="Bus subsidy",
            basis="fixed",
            amount=Decimal("30.00"),
            fee_item=self.transport,
        )
        self.enroll(self.pupil, self.transport)
        self.bill_class()
        by_name = {
            row["admission_number"]: row
            for row in selectors.student_fee_rows(term=self.term)
        }
        # The pupil on transport gets 30 off; the one who never took the bus has
        # no such line on the bill, so there is nothing for the rule to reduce.
        self.assertEqual(by_name["BIL-001"]["adjusted"], Decimal("30.00"))
        self.assertEqual(by_name["BIL-002"]["adjusted"], Decimal("0.00"))

    def test_a_sibling_rule_reaches_only_children_who_share_a_guardian(self):
        mother = make_user("parent", username="bil_mother")
        profile = ParentProfile.objects.create(user=mother, occupation="Trader")
        StudentGuardian.objects.create(
            student=self.pupil, parent=profile, is_primary=True
        )
        self.rule(
            name="Sibling discount",
            basis="percent",
            percent=Decimal("10.00"),
            siblings_only=True,
        )
        self.bill_class()
        by_name = {
            row["admission_number"]: row
            for row in selectors.student_fee_rows(term=self.term)
        }
        self.assertEqual(by_name["BIL-001"]["adjusted"], Decimal("0.00"))
        self.assertEqual(by_name["BIL-002"]["adjusted"], Decimal("0.00"))

        brother = make_student_user(self.school_class, admission_number="BIL-003")
        StudentGuardian.objects.create(
            student=brother, parent=profile, is_primary=False
        )
        services.post_invoices(
            term=self.term, school_class=self.school_class, actor=self.officer
        )
        by_name = {
            row["admission_number"]: row
            for row in selectors.student_fee_rows(term=self.term)
        }
        # The brother, billed now under the rule published earlier, gets it.
        self.assertEqual(by_name["BIL-003"]["adjusted"], Decimal("40.00"))
        # The invoices already posted are not restated by a second run: a
        # document a family is holding does not change because a rule did. The
        # officer's route for that is a waiver, which names the amount, the
        # reason and the person who decided it.
        self.assertEqual(by_name["BIL-001"]["adjusted"], Decimal("0.00"))
        services.apply_adjustment(
            student=self.pupil,
            invoice=Invoice.objects.get(student=self.pupil, term=self.term),
            adjustment_type=Adjustment.AdjustmentType.WAIVER,
            amount=Decimal("40.00"),
            reason="Sibling discount missed the first billing run.",
            actor=self.officer,
        )
        by_name = {
            row["admission_number"]: row
            for row in selectors.student_fee_rows(term=self.term)
        }
        self.assertEqual(by_name["BIL-001"]["adjusted"], Decimal("40.00"))

    def test_a_named_list_reaches_only_the_pupils_on_it(self):
        self.rule(
            name="Scholarship 2026",
            basis="percent",
            percent=Decimal("50.00"),
            adjustment_type=Adjustment.AdjustmentType.SCHOLARSHIP,
            students=[self.pupil],
        )
        self.bill_class()
        self.assertEqual(
            Adjustment.objects.filter(
                adjustment_type=Adjustment.AdjustmentType.SCHOLARSHIP
            ).count(),
            1,
        )
        self.assertEqual(
            Adjustment.objects.get().student_id, self.pupil.pk
        )

    def test_a_rule_turned_off_stops_reducing_and_keeps_the_history(self):
        rule = self.rule(name="Term bursary", basis="percent", percent=Decimal("10.00"))
        self.bill_class()
        self.assertEqual(Adjustment.objects.count(), 2)
        services.set_discount_rule_active(rule=rule, active=False, actor=self.officer)

        next_term = make_term(self.year, name="term_2", is_current=False)
        services.create_fee_structure(
            school_class=self.school_class,
            term=next_term,
            items=[{"label": "Tuition", "amount": Decimal("400.00")}],
            actor=self.officer,
        )
        services.post_invoices(
            term=next_term, school_class=self.school_class, actor=self.officer
        )
        self.assertEqual(
            selectors.fee_totals(term=next_term)["adjusted"], Decimal("0.00")
        )
        # The two waivers already given stay on the record: switching a rule off
        # stops the future, it does not re-bill the past.
        self.assertEqual(Adjustment.objects.count(), 2)
        self.assertEqual(
            selectors.fee_totals(term=self.term)["adjusted"], Decimal("80.00")
        )

    def test_a_rule_outside_its_window_is_not_applied(self):
        rule = self.rule(
            name="Closed term", basis="percent", percent=Decimal("10.00")
        )
        rule.valid_from = TODAY - datetime.timedelta(days=90)
        rule.valid_to = TODAY - datetime.timedelta(days=1)
        rule.save(update_fields=["valid_from", "valid_to"])
        self.bill_class()
        self.assertEqual(Adjustment.objects.count(), 0)

    def test_two_rules_cannot_take_a_bill_below_zero(self):
        self.rule(name="Half", basis="percent", percent=Decimal("50.00"))
        self.rule(name="All of it", basis="percent", percent=Decimal("80.00"))
        preview = services.preview_invoice_generation(
            term=self.term, school_class=self.school_class
        )
        self.bill_class()
        for row in preview["rows"]:
            self.assertEqual(Decimal(row["waived"]), Decimal("400.00"))
            self.assertEqual(Decimal(row["net"]), Decimal("0.00"))
        totals = selectors.fee_totals(term=self.term)
        self.assertEqual(totals["billed"], Decimal("800.00"))
        self.assertEqual(totals["adjusted"], Decimal("800.00"))
        self.assertEqual(totals["outstanding"], Decimal("0.00"))
        self.assertEqual(selectors.arrears_rows(), [])
        # Whichever rule the run reads first, the second is capped by what is
        # left of the bill. Going past zero would need a journal line crediting
        # back income that was never charged.
        self.assertEqual(
            Adjustment.objects.exclude(amount__gt=Decimal("0.00")).count(), 0
        )

    def test_a_percentage_above_one_hundred_is_refused_before_it_is_saved(self):
        with self.assertRaises(ValidationError):
            self.rule(name="Impossible", basis="percent", percent=Decimal("150.00"))
        self.assertEqual(FeeDiscountRule.objects.count(), 0)

    def test_a_percentage_rule_must_carry_a_percent_and_no_fixed_amount(self):
        rule = self.rule(name="Term bursary", basis="percent", percent=Decimal("10.00"))
        self.assertIsNone(rule.amount)
        with self.assertRaises(ValidationError):
            self.rule(name="No amount", basis="fixed")

    def test_a_pupil_with_nothing_billable_is_reported_not_posted_as_zero(self):
        """
        A structure whose items are all optional can produce a pupil with no fee
        at all. An invoice for 0.00 is a document with no money in it: it burns a
        number from the gap-free sequence, and ``_post_journal_entry`` refuses an
        entry that balances at zero — so posting one would abort the run and take
        the rest of the class with it. The run reports the pupil instead.
        """
        FeeItem.objects.filter(pk=self.tuition.pk).update(compulsory=False)
        self.enroll(self.classmate, self.transport)
        self.rule(name="Everything", basis="percent", percent=Decimal("100.00"))
        preview = services.preview_invoice_generation(
            term=self.term, school_class=self.school_class
        )
        self.assertEqual(preview["counts"]["skip_no_fee"], 1)
        self.assertEqual(preview["counts"]["create"], 1)
        self.assertEqual(len(self.bill_class()), 1)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(Invoice.objects.get().total_amount, Decimal("100.00"))
        # The rule reduces the one real bill to nothing owed; the pupil with no
        # bill earns no 0.00 adjustment for an auditor to explain.
        self.assertEqual(Adjustment.objects.count(), 1)
        self.assertEqual(Adjustment.objects.get().amount, Decimal("100.00"))


class LateFeeRuleTests(BillingTestBase):
    def invoice_past_due(self, *, due, amount="500.00", student=None):
        return Invoice.objects.create(
            student=student or self.pupil,
            term=self.term,
            fee_structure=self.structure,
            invoice_number=f"LATE-{due:%d%m}-{student or self.pupil}",
            issued_date=due - datetime.timedelta(days=10),
            due_date=due,
            total_amount=Decimal(amount),
            status=Invoice.Status.POSTED,
        )

    def test_a_stored_rule_that_is_switched_off_proposes_nothing(self):
        rules = FinanceSettings.load()
        rules.late_fees_enabled = False
        rules.late_fee_percent = Decimal("5.00")
        rules.save(update_fields=["late_fees_enabled", "late_fee_percent"])
        self.invoice_past_due(due=TODAY - datetime.timedelta(days=40))
        proposals = selectors.late_fee_proposals(term=self.term)
        self.assertFalse(proposals["enabled"])
        self.assertEqual(proposals["rows"], [])
        self.assertEqual(proposals["total"], Decimal("0.00"))
        self.assertEqual(Adjustment.objects.count(), 0)

    def test_an_enabled_rule_proposes_and_still_posts_nothing(self):
        rules = FinanceSettings.load()
        rules.late_fees_enabled = True
        rules.late_fee_percent = Decimal("5.00")
        rules.late_fee_grace_days = 0
        rules.save(
            update_fields=[
                "late_fees_enabled",
                "late_fee_percent",
                "late_fee_grace_days",
            ]
        )
        overdue = self.invoice_past_due(due=TODAY - datetime.timedelta(days=40))
        proposals = selectors.late_fee_proposals(term=self.term)
        self.assertTrue(proposals["enabled"])
        self.assertEqual(len(proposals["rows"]), 1)
        row = proposals["rows"][0]
        self.assertEqual(row["invoice"].pk, overdue.pk)
        self.assertEqual(row["days_over"], 40)
        self.assertEqual(row["amount"], Decimal("25.00"))
        self.assertEqual(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.ADJUSTMENT
            ).count(),
            0,
            msg="A late fee is proposed, never charged by a reader.",
        )
        self.assertEqual(Adjustment.objects.count(), 0)

    def test_the_grace_window_is_respected(self):
        rules = FinanceSettings.load()
        rules.late_fees_enabled = True
        rules.late_fee_percent = Decimal("5.00")
        rules.late_fee_grace_days = 30
        rules.save(
            update_fields=[
                "late_fees_enabled",
                "late_fee_percent",
                "late_fee_grace_days",
            ]
        )
        self.invoice_past_due(due=TODAY - datetime.timedelta(days=10))
        self.assertEqual(selectors.late_fee_proposals(term=self.term)["rows"], [])

    def test_a_paid_bill_is_never_late(self):
        rules = FinanceSettings.load()
        rules.late_fees_enabled = True
        rules.late_fee_percent = Decimal("5.00")
        rules.save(
            update_fields=["late_fees_enabled", "late_fee_percent"]
        )
        overdue = self.invoice_past_due(due=TODAY - datetime.timedelta(days=40))
        self.pay(self.pupil, "500.00", "LATE-PAID")
        self.assertEqual(
            Payment.objects.filter(
                allocations__invoice=overdue
            ).count(),
            1,
        )
        self.assertEqual(selectors.late_fee_proposals(term=self.term)["rows"], [])


class ArrearsTests(BillingTestBase):
    def test_arrears_net_billed_collected_and_waived(self):
        services.create_discount_rule(
            name="Term bursary",
            basis="percent",
            percent=Decimal("10.00"),
            students=[self.pupil],
            actor=self.officer,
        )
        invoice = self.bill_class()[0]
        self.pay(self.pupil, "100.00", "ARR-001")
        rows = {row["admission_number"]: row for row in selectors.arrears_rows()}
        mine = rows["BIL-001"]
        self.assertEqual(mine["billed"], Decimal("400.00"))
        self.assertEqual(mine["collected"], Decimal("100.00"))
        self.assertEqual(mine["adjusted"], Decimal("40.00"))
        self.assertEqual(mine["outstanding"], Decimal("260.00"))
        # Pinned, not endorsed: ``Invoice.amount_outstanding`` is billed minus
        # receipts and knows nothing about adjustments, so on a waived bill it
        # reads 40.00 too high. Every interface figure comes from the selectors,
        # which do net the waiver — this property is the one place the two
        # vocabularies still differ, and it is listed in the checkpoint report.
        self.assertEqual(invoice.amount_outstanding, Decimal("300.00"))

    def test_a_pupil_who_left_school_still_owes_and_still_appears(self):
        """
        The point of reading arrears by pupil instead of by class: promotion and
        graduation move ``current_class``, and a class fee page selects on it.
        """
        self.bill_class()
        self.pupil.status = Student.Status.GRADUATED
        self.pupil.current_class = None
        self.pupil.save(update_fields=["status", "current_class", "updated_at"])
        rows = selectors.arrears_rows()
        self.assertEqual(
            [row["admission_number"] for row in rows], ["BIL-001", "BIL-002"]
        )
        self.assertEqual(rows[0]["class_name"], "—")
        self.assertEqual(
            selectors.arrears_summary()["total"], Decimal("800.00")
        )

    def test_money_held_as_credit_is_not_counted_as_a_payment(self):
        """
        A surplus the school is holding has not been put against any invoice, so
        it is not arrears relief. The pupil it covers leaves the chase list; the
        classmate who owes real money stays on it — which is the distinction the
        list exists to make.
        """
        self.bill_class()
        self.pay(self.pupil, "600.00", "ARR-CREDIT")
        rows = {row["admission_number"]: row for row in selectors.arrears_rows()}
        self.assertNotIn("BIL-001", rows)
        self.assertEqual(rows["BIL-002"]["collected"], Decimal("0.00"))
        self.assertEqual(rows["BIL-002"]["outstanding"], Decimal("400.00"))
        self.assertEqual(selectors.arrears_summary()["total"], Decimal("400.00"))

    def test_carry_forward_arrears_records_what_each_pupil_walks_in_with(self):
        self.bill_class()
        before = selectors.student_balance(student_id=self.pupil.pk)
        result = services.carry_forward_arrears(
            student_ids=[self.pupil.pk, self.classmate.pk],
            actor=self.officer,
            context={"batch": "1", "from_year": "2025/2026", "to_year": "2026/2027"},
        )
        self.assertTrue(result["checked"])
        self.assertEqual(result["total"], Decimal("800.00"))
        entry = AuditLog.objects.get(action="finance.arrears_carried_forward")
        self.assertEqual(entry.change_summary["total"], "800.00")
        self.assertEqual(entry.change_summary["to_year"], "2026/2027")
        self.assertEqual(
            selectors.student_balance(student_id=self.pupil.pk), before
        )

    def test_class_promotion_calls_the_hook_and_the_balance_survives(self):
        """
        B3 asks for a hook so promotion never loses balances. This is that hook,
        tested on the real path: a committed promotion, the audit row it wrote,
        and the same debt on the other side of the move.
        """
        to_year = make_year(name="2026/2027", is_current=False)
        next_class = make_class(
            to_year, name="Grade 6", level_order=6
        )
        self.bill_class()
        before = selectors.arrears_summary()["total"]

        commit_promotion(
            from_year=self.year,
            to_year=to_year,
            term=self.term,
            override_unpublished=True,
            override_reason="Results are out of scope for this test.",
            actor=self.admin,
        )

        self.assertEqual(
            PromotionRecord.objects.count(), 2
        )  # both pupils moved
        self.assertEqual(
            AuditLog.objects.filter(
                action="finance.arrears_carried_forward"
            ).count(),
            1,
        )
        self.assertEqual(selectors.arrears_summary()["total"], before)
        # The invoices did not move with the class pointer, which is the whole
        # reason nothing had to be copied forward.
        self.assertEqual(
            Invoice.objects.filter(
                student__current_class=next_class, term=self.term
            ).count(),
            2,
        )


class PaymentPlanTests(BillingTestBase):
    def plan(self, installments, *, student=None, note=""):
        return services.create_payment_plan(
            student=student or self.pupil,
            term=self.term,
            installments=installments,
            note=note,
            actor=self.officer,
        )

    def two_installments(self):
        return [
            {"amount": Decimal("100.00"), "due_date": TODAY - datetime.timedelta(days=30)},
            {"amount": Decimal("100.00"), "due_date": TODAY + datetime.timedelta(days=30)},
        ]

    def test_an_agreed_plan_books_no_money(self):
        self.bill_class()
        entries = JournalEntry.objects.count()
        plan = self.plan(self.two_installments())
        self.assertEqual(plan.total_amount, Decimal("200.00"))
        self.assertEqual(plan.scheduled_amount, Decimal("200.00"))
        self.assertEqual(plan.installments.count(), 2)
        self.assertEqual(JournalEntry.objects.count(), entries)

    def test_an_empty_or_broken_schedule_is_refused(self):
        for installments, why in [
            ([], "no installments"),
            ([{"amount": Decimal("0.00"), "due_date": TODAY}], "zero amount"),
            ([{"amount": Decimal("100.00"), "due_date": None}], "no date"),
        ]:
            with self.subTest(case=why), self.assertRaises(ValidationError):
                self.plan(installments)
        self.assertEqual(PaymentPlan.objects.count(), 0)

    def test_one_running_plan_per_pupil_and_term(self):
        self.plan(self.two_installments())
        with self.assertRaises(ValidationError) as caught:
            self.plan(self.two_installments())
        self.assertIn("already has a plan", str(caught.exception))

    def test_a_withdrawn_plan_frees_the_pupil_to_agree_another(self):
        first = self.plan(self.two_installments())
        services.withdraw_payment_plan(
            plan=first, reason="Family re-scheduled.", actor=self.officer
        )
        second = self.plan(self.two_installments())
        self.assertNotEqual(second.pk, first.pk)
        self.assertEqual(
            PaymentPlan.objects.filter(student=self.pupil).count(), 2
        )

    def test_withdrawing_needs_a_reason_and_keeps_the_schedule(self):
        plan = self.plan(self.two_installments())
        with self.assertRaises(ValidationError):
            services.withdraw_payment_plan(plan=plan, reason="  ", actor=self.officer)
        services.withdraw_payment_plan(
            plan=plan, reason="Family moved away.", actor=self.officer
        )
        plan.refresh_from_db()
        self.assertEqual(plan.status, PaymentPlan.Status.WITHDRAWN)
        self.assertEqual(plan.installments.count(), 2)
        self.assertIn("Family moved away", plan.note)

    def test_adherence_marks_the_late_installment_and_only_that_one(self):
        self.bill_class()
        self.plan(self.two_installments())
        self.pay(self.pupil, "100.00", "PLAN-001")
        row = selectors.payment_plan_rows(term=self.term)["rows"][0]
        self.assertEqual(row["received"], Decimal("100.00"))
        self.assertEqual(row["state"], "agreed")
        self.assertTrue(row["installments"][0]["met"])
        self.assertFalse(row["installments"][1]["met"])
        self.assertEqual(row["overdue_count"], 0)

        late = self.plan(
            [
                {
                    "amount": Decimal("100.00"),
                    "due_date": TODAY - datetime.timedelta(days=5),
                }
            ],
            student=self.classmate,
        )
        late_row = next(
            row
            for row in selectors.payment_plan_rows(term=self.term)["rows"]
            if row["plan"] == late
        )
        self.assertEqual(late_row["state"], "overdue")
        self.assertEqual(late_row["overdue_count"], 1)
        self.assertEqual(
            late_row["next_due_date"], late.installments.first().due_date
        )

    def test_a_reversed_receipt_does_not_keep_an_installment_met(self):
        self.bill_class()
        plan = self.plan(
            [
                {
                    "amount": Decimal("100.00"),
                    "due_date": TODAY - datetime.timedelta(days=10),
                }
            ]
        )
        payment = self.pay(self.pupil, "100.00", "PLAN-REV")
        row = selectors.payment_plan_rows(term=self.term)["rows"][0]
        self.assertTrue(row["installments"][0]["met"])

        services.reverse_payment(
            payment=payment, reason="Wrong account.", actor=self.admin
        )
        after = selectors.payment_plan_rows(term=self.term)["rows"][0]
        self.assertEqual(after["plan"], plan)
        self.assertEqual(after["received"], Decimal("0.00"))
        self.assertFalse(after["installments"][0]["met"])
        self.assertEqual(after["state"], "overdue")

    def test_a_plan_paid_to_the_end_reads_settled(self):
        self.bill_class()
        self.plan(self.two_installments())
        self.pay(self.pupil, "200.00", "PLAN-FULL")
        row = selectors.payment_plan_rows(term=self.term)["rows"][0]
        self.assertEqual(row["state"], "settled")
        self.assertEqual(row["outstanding"], Decimal("0.00"))


class BillingRunIntegrityTests(BillingTestBase):
    def test_the_run_balances_the_ledger_and_is_idempotent(self):
        services.create_discount_rule(
            name="Term bursary",
            basis="percent",
            percent=Decimal("25.00"),
            actor=self.officer,
        )
        first = self.bill_class()
        balance = selectors.trial_balance_at()
        self.assertEqual(balance["total_debits"], balance["total_credits"])
        self.assertEqual(len(first), 2)

        again = self.bill_class()
        self.assertEqual(
            [invoice.invoice_number for invoice in again],
            [invoice.invoice_number for invoice in first],
        )
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertEqual(Adjustment.objects.count(), 2)

    def test_a_class_with_no_structure_is_reported_not_billed_as_zero(self):
        other = make_class(self.year, name="Grade 9", level_order=9)
        orphan = make_student_user(other, admission_number="BIL-009")
        preview = services.preview_invoice_generation(
            term=self.term, school_class=other
        )
        self.assertEqual(preview["counts"]["skip_no_structure"], 1)
        self.assertEqual(preview["billed"], Decimal("0.00"))
        self.assertEqual(
            services.post_invoices(term=self.term, school_class=other), []
        )
        self.assertEqual(Invoice.objects.filter(student=orphan).count(), 0)

    def test_the_run_whole_school_bills_every_class_of_the_terms_year(self):
        other = make_class(self.year, name="Grade 6", level_order=6)
        make_student_user(other, admission_number="BIL-006")
        services.create_fee_structure(
            school_class=other,
            term=self.term,
            items=[{"label": "Tuition", "amount": Decimal("300.00")}],
        )
        posted = services.post_invoices(term=self.term, actor=self.officer)
        self.assertEqual(len(posted), 3)
        self.assertEqual(
            selectors.fee_totals(term=self.term)["billed"], Decimal("1100.00")
        )
