"""
apps/dashboards/tests/test_fees_switch_gating.py
--------------------------------------------------
What the ``fees`` switch is for, screen by screen.

The rule the Admin sets on the School Settings page is not "hide the numbers",
it is "this school does not run fees". That distinction is why every test here
asserts on the *sentence* a screen prints rather than on an empty table: an
empty fee table tells a Director nobody owes anything, and a zero in front of a
parent tells them their child is paid up. Both are confident, both are wrong,
and neither fails a test that only checks for a placeholder.

The other half of the ruling is just as load-bearing: the Finance portal itself
is never gated. A school that stops *billing* still has receipts to reconcile
and a journal to close, and the officer who has to do that cannot be shown a
"module not activated" card.
"""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from apps.core.features import feature_disabled_message
from apps.core.models import SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_accounts,
    make_class,
    make_fee_structure,
    make_payment_allocation,
    make_posted_invoice,
    make_student_user,
    make_term,
    make_user,
    make_year,
)

PLACEHOLDER = feature_disabled_message("fees")


class FeesSwitchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        make_accounts()
        cls.structure = make_fee_structure(cls.school_class, cls.term)

        cls.student = make_student_user(
            cls.school_class, admission_number="GATE-001"
        )
        cls.invoice = make_posted_invoice(cls.student, cls.term, cls.structure)
        make_payment_allocation(cls.invoice, Decimal("200.00"))

        cls.director = make_user("director", username="gate_director")
        cls.finance = make_user("finance", username="gate_finance")

    def switch_fees_off(self) -> None:
        row = SchoolSettings.load()
        row.fees_enabled = False
        row.save()

    # ── The family-facing cards ─────────────────────────────────────────────

    def test_a_pupil_sees_their_balance_when_fees_are_on(self):
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("dashboards:student_home"))
        self.assertContains(response, "Balance Due")
        self.assertContains(response, reverse("finance:student_statement_self"))

    def test_a_pupil_sees_a_sentence_not_a_zero_when_fees_are_off(self):
        self.switch_fees_off()
        self.client.force_login(self.student.user)
        with mock.patch("apps.dashboards.views.student_balance") as ask:
            response = self.client.get(reverse("dashboards:student_home"))
        self.assertContains(response, PLACEHOLDER)
        self.assertNotContains(response, "Balance Due")
        ask.assert_not_called()

    def test_the_statement_link_disappears_with_the_feature(self):
        """A card that leads to a page with nothing on it is a broken card."""
        self.switch_fees_off()
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("dashboards:student_home"))
        self.assertNotContains(response, reverse("finance:student_statement_self"))

    def test_a_parent_is_told_the_same_thing(self):
        from apps.students.services import create_student_with_guardian

        child = create_student_with_guardian(
            admission_number="GATE-002",
            first_name="Gated",
            last_name="Child",
            email="gated.child@test.school",
            current_class=self.school_class,
            guardian_first_name="Gated",
            guardian_last_name="Parent",
            guardian_email="gated.parent@test.school",
            send_credentials=False,
        )
        parent_user = child.guardian_links.first().parent.user
        parent_user.must_change_password = False
        parent_user.save(update_fields=["must_change_password"])

        self.switch_fees_off()
        self.client.force_login(parent_user)
        response = self.client.get(reverse("dashboards:parent_home"))
        self.assertContains(response, PLACEHOLDER)
        self.assertNotContains(response, "Balance Due")

    # ── The Director's fee pages ────────────────────────────────────────────

    def test_the_fee_overview_stops_asking_finance_when_off(self):
        self.switch_fees_off()
        self.client.force_login(self.director)
        with mock.patch("apps.dashboards.views_director.class_fee_summary") as ask:
            response = self.client.get(reverse("dashboards:director_fees"))
        self.assertContains(response, PLACEHOLDER)
        self.assertNotContains(response, "No posted invoices")
        ask.assert_not_called()

    def test_the_fee_overview_exports_when_the_feature_is_on(self):
        """The export button is the tell that the page is really gated."""
        self.client.force_login(self.director)
        self.assertContains(
            self.client.get(reverse("dashboards:director_fees")), "Export Excel"
        )
        self.switch_fees_off()
        self.assertNotContains(
            self.client.get(reverse("dashboards:director_fees")), "Export Excel"
        )

    def test_debtors_says_the_module_is_off_not_that_everyone_has_paid(self):
        self.switch_fees_off()
        self.client.force_login(self.director)
        response = self.client.get(
            reverse("dashboards:director_fee_debtors", args=[self.school_class.pk])
        )
        self.assertContains(response, PLACEHOLDER)
        self.assertNotContains(response, "No outstanding balances")

    def test_the_books_page_placeholders_instead_of_a_blank_chart(self):
        self.switch_fees_off()
        self.client.force_login(self.director)
        response = self.client.get(reverse("dashboards:director_books"))
        self.assertContains(response, PLACEHOLDER)
        self.assertNotContains(response, "Select a term above")

    # ── What the switch does NOT reach ──────────────────────────────────────

    def test_the_finance_portal_is_never_gated(self):
        """
        The officer's own home keeps its figures with fees switched off: this
        school may have stopped billing, but it has not stopped having bills to
        settle, receipts to reverse and a term to close.
        """
        self.switch_fees_off()
        self.client.force_login(self.finance)
        response = self.client.get(reverse("dashboards:finance_home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, PLACEHOLDER)
        self.assertEqual(response.context["total_billed"], Decimal("500.00"))
        self.assertEqual(response.context["outstanding_total"], Decimal("300.00"))

    def test_the_journal_and_trial_balance_stay_readable(self):
        """Books of account are not a fee surface; a closed term is still a term."""
        self.switch_fees_off()
        self.client.force_login(self.director)
        for name in (
            "dashboards:director_books_ledger",
            "dashboards:director_books_trial_balance",
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, PLACEHOLDER)
