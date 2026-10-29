"""
apps/finance/tests/test_fast_payment.py
---------------------------------------
Checkpoint FS-2 — taking a payment without hunting for the pupil.

The page this tests is the cashier's desk: a queue, a parent, and a receipt to
hand over before the next person steps up. Everything the officer is told on
that page — which pupils exist, what this term asks for, what last term still
asks for, what to type in the amount box — comes from one context read. If that
read drifts from the ledger the officer quotes a parent one figure and the
statement prints another, so these tests pin the figures to the same selectors
the statement uses.

What the fixture books:

    debt  (Grade 5, Zulu)      this term 500.00 billed, 200.00 paid  → 300.00 due
                               last term 400.00 billed, nothing paid → 400.00 arrears
    ape   (Grade 5, Appiah)    this term 500.00 billed, 500.00 paid  → settled
    mens  (Grade 3, Mensah)    never billed                          → zeros
    gone  (Grade 5, withdr.)   billed, but no longer a pupil we bill
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.academics.models import Term
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
from apps.finance.models import Invoice, Payment
from apps.students.models import Student

from .. import services

D = Decimal
PAYMENT_DATE = datetime.date(2025, 9, 10)


def _rename(student: Student, first: str, last: str) -> Student:
    student.user.first_name = first
    student.user.last_name = last
    student.user.save(update_fields=["first_name", "last_name"])
    return student


class FastPaymentTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)  # is_current=True
        cls.last_term = make_term(
            cls.year,
            name="previous_term",
            start_date=datetime.date(2025, 1, 10),
            end_date=datetime.date(2025, 4, 10),
            is_current=False,
        )
        cls.grade5 = make_class(cls.year, name="Grade 5", level_order=5)
        cls.grade3 = make_class(cls.year, name="Grade 3", level_order=3)
        cls.officer = make_user("finance", username="fp_officer")
        make_accounts()

        cls.debt = _rename(
            make_student_user(cls.grade5, admission_number="FP-001"),
            "Ama",
            "Zulu",
        )
        cls.ape = _rename(
            make_student_user(cls.grade5, admission_number="FP-002"),
            "Kofi",
            "Appiah",
        )
        cls.mens = _rename(
            make_student_user(cls.grade3, admission_number="FP-003"),
            "Yao",
            "Mensah",
        )
        cls.gone = _rename(
            make_student_user(cls.grade5, admission_number="FP-004"),
            "Adjoa",
            "Boateng",
        )
        cls.gone.status = Student.Status.WITHDRAWN
        cls.gone.save(update_fields=["status"])

        cls.structure = make_fee_structure(cls.grade5, cls.term, D("500.00"))
        cls.last_structure = make_fee_structure(cls.grade5, cls.last_term, D("400.00"))

        cls.debt_invoice = make_posted_invoice(
            cls.debt, cls.term, cls.structure, D("500.00")
        )
        make_payment_allocation(cls.debt_invoice, D("200.00"))
        cls.debt_last_invoice = make_posted_invoice(
            cls.debt, cls.last_term, cls.last_structure, D("400.00")
        )

        cls.ape_invoice = make_posted_invoice(
            cls.ape, cls.term, cls.structure, D("500.00")
        )
        make_payment_allocation(cls.ape_invoice, D("500.00"))

    def setUp(self):
        self.client.force_login(self.officer)

    def page(self, query: str = ""):
        path = reverse("finance:payment_record")
        response = self.client.get(f"{path}?{query}" if query else path)
        self.assertEqual(response.status_code, 200)
        return response

    def pupils(self, response) -> dict[int, dict]:
        return {row["id"]: row for row in response.context["pupil_data"]}


class PickerFiguresTests(FastPaymentTestBase):
    def test_the_card_separates_this_term_from_earlier_terms(self):
        row = self.pupils(self.page())[self.debt.pk]
        self.assertEqual(row["billed"], D("500.00"))
        self.assertEqual(row["paid"], D("200.00"))
        self.assertEqual(row["due"], D("300.00"))
        self.assertEqual(row["arrears"], D("400.00"))
        # The figure "Pay the whole balance" writes into the amount box.
        self.assertEqual(row["total"], D("700.00"))

    def test_a_settled_pupil_owes_nothing_anywhere(self):
        row = self.pupils(self.page())[self.ape.pk]
        self.assertEqual(row["due"], D("0.00"))
        self.assertEqual(row["arrears"], D("0.00"))
        self.assertEqual(row["total"], D("0.00"))

    def test_a_pupil_who_was_never_billed_reads_zero_not_missing(self):
        row = self.pupils(self.page())[self.mens.pk]
        self.assertEqual(row["billed"], D("0.00"))
        self.assertEqual(row["paid"], D("0.00"))
        self.assertEqual(row["due"], D("0.00"))
        self.assertEqual(row["total"], D("0.00"))

    def test_an_adjustment_lowers_the_due_exactly_as_the_statement_does(self):
        services.apply_adjustment(
            student=self.debt,
            invoice=self.debt_invoice,
            adjustment_type="waiver",
            amount=D("50.00"),
            reason="Bursary waiver",
            actor=self.officer,
        )
        row = self.pupils(self.page())[self.debt.pk]
        self.assertEqual(row["due"], D("250.00"))
        self.assertEqual(row["total"], D("650.00"))

    def test_a_withdrawn_pupil_is_not_offered_at_the_desk(self):
        response = self.page()
        self.assertNotIn(self.gone.pk, self.pupils(response))
        offered = [pk for pk, _ in response.context["form"]["student"].field.choices]
        self.assertNotIn(self.gone.pk, offered)

    def test_the_payload_and_the_dropdown_offer_the_same_pupils(self):
        """Two lists built from one roster; if they diverge a search hits a
        pupil the select cannot submit."""
        response = self.page()
        payload = set(self.pupils(response))
        offered = {
            pk
            for pk, _ in response.context["form"]["student"].field.choices
            if pk
        }
        self.assertEqual(payload, offered)


class ClassFilterTests(FastPaymentTestBase):
    def test_only_classes_with_a_pupil_we_bill_are_listed(self):
        options = {
            option["id"]: option for option in self.page().context["class_options"]
        }
        self.assertEqual(set(options), {self.grade5.pk, self.grade3.pk})
        self.assertEqual(options[self.grade5.pk]["count"], 2)  # `gone` is withdrawn
        self.assertEqual(options[self.grade3.pk]["count"], 1)
        self.assertEqual(options[self.grade5.pk]["name"], "Grade 5")

    def test_a_pupil_with_no_class_is_still_findable(self):
        self.mens.current_class = None
        self.mens.save(update_fields=["current_class"])
        response = self.page()
        row = self.pupils(response)[self.mens.pk]
        self.assertEqual(row["class_id"], 0)
        self.assertEqual(row["class_name"], "No class")
        options = {option["id"]: option for option in response.context["class_options"]}
        self.assertEqual(options[0]["count"], 1)


class DropdownOrderTests(FastPaymentTestBase):
    def test_the_dropdown_walks_the_school_from_the_youngest_class_up(self):
        choices = [
            pk
            for pk, _ in self.page().context["form"]["student"].field.choices
            if pk
        ]
        self.assertEqual(
            choices,
            # Grade 3 first, then Grade 5 by surname; `gone` is withdrawn.
            [self.mens.pk, self.ape.pk, self.debt.pk],
        )


class DeepLinkTests(FastPaymentTestBase):
    def test_a_student_query_string_opens_their_card(self):
        response = self.page(query=f"student={self.debt.pk}")
        self.assertEqual(response.context["selected"]["id"], self.debt.pk)
        self.assertEqual(response.context["selected"]["total"], D("700.00"))

    def test_an_unknown_or_junk_student_opens_the_empty_card(self):
        for query in (f"student={self.gone.pk}", "student=abc", "student="):
            with self.subTest(query=query):
                response = self.page(query=query)
                self.assertIsNone(response.context["selected"])

    def test_a_rejected_post_describes_the_pupil_that_was_submitted(self):
        """
        `?student=` is the bill the officer clicked through from; the form is
        what they typed. A re-render must show the latter, or the card quotes a
        balance for somebody else while the invalid amount sits underneath it.
        """
        response = self.client.post(
            reverse("finance:payment_record") + f"?student={self.debt.pk}",
            {
                "student": self.ape.pk,
                "amount": "not money",
                "method": Payment.Method.BANK,
                "reference": "FP-REF-BAD",
                "payment_date": str(PAYMENT_DATE),
                "idempotency_key": "1" * 32,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected"]["id"], self.ape.pk)


class NoCurrentTermTests(FastPaymentTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Term.objects.filter(pk=cls.term.pk).update(is_current=False)

    def test_with_no_current_term_the_page_does_not_claim_there_is_nothing_due(self):
        response = self.page()
        for row in response.context["pupil_data"]:
            self.assertIsNone(row["due"])
        row = self.pupils(response)[self.debt.pk]
        # With no term to call "this term", the whole debt is brought forward:
        # 300.00 this term + 400.00 last term.
        self.assertEqual(row["arrears"], D("700.00"))
        self.assertEqual(row["total"], D("700.00"))


class RecordingFromThePickerTests(FastPaymentTestBase):
    def test_the_page_ships_the_payload_and_the_filter_controls(self):
        response = self.page()
        self.assertContains(response, 'id="pupil-data"')
        self.assertContains(response, 'id="class-filter"')
        self.assertContains(response, 'id="pupil-search"')
        self.assertContains(response, 'id="pay-full"')

    def test_a_payment_recorded_from_the_page_lands_on_its_receipt(self):
        response = self.client.post(
            reverse("finance:payment_record"),
            {
                "student": self.debt.pk,
                "amount": "700.00",
                "method": Payment.Method.MOBILE_MONEY,
                "reference": "FP-REF-1",
                "payment_date": str(PAYMENT_DATE),
                "idempotency_key": "2" * 32,
            },
        )
        payment = Payment.objects.get(reference="FP-REF-1")
        self.assertRedirects(
            response,
            reverse("finance:payment_detail", kwargs={"pk": payment.pk}),
            fetch_redirect_response=False,
        )
        self.assertEqual(payment.amount, D("700.00"))
        # The money was applied, not merely received: both bills are settled.
        for invoice in (self.debt_invoice, self.debt_last_invoice):
            invoice.refresh_from_db()
            self.assertEqual(invoice.amount_outstanding, D("0.00"))

    def test_a_payment_cannot_name_a_pupil_the_page_never_offered(self):
        response = self.client.post(
            reverse("finance:payment_record"),
            {
                "student": self.gone.pk,
                "amount": "10.00",
                "method": Payment.Method.MOBILE_MONEY,
                "reference": "FP-REF-2",
                "payment_date": str(PAYMENT_DATE),
                "idempotency_key": "3" * 32,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.filter(reference="FP-REF-2").exists())
