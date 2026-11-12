"""
apps/finance/tests/test_finance_pages.py
----------------------------------------
Module 6 checkpoint F7 — the screens themselves.

Until this checkpoint every finance page was a template file that did not exist.
The sidebar hid that (an unrenderable link became ``href="#"``) and
``test_navigation_integrity.py`` exempted each entry in ``KNOWN_BROKEN``. Those
exemptions are now deleted, so this file has to carry what they excused: a page
is not finished when its template exists, it is finished when it renders the
school's real figures and no role that can reach it walks into a 500.

What a selector test cannot catch, and this can:
  * a template reading a context key the view never sets — blank on one machine,
    a hard error on another;
  * a page whose arithmetic is done in markup, so it drifts from the number the
    Director's page shows for the same term;
  * a deep link (``?student=``, ``?term=``, ``?invoice=``) the view ignores, or a
    hand-edited one that turns a money page into a server error.

The books these pages read:

    billed this term       1,500.00   three posted invoices of 500.00
    collected                700.00   200.00 part-paid, 500.00 settled
    adjusted (waiver)         50.00   posted, contra Discounts
    outstanding              750.00   250.00 on PG-001 + 500.00 on PG-002
"""

from __future__ import annotations

import datetime
import os
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import TestCase
from django.urls import reverse

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
from ..models import Adjustment, FeeStructureUploadJob, Invoice, Payment

PAYMENT_DATE = datetime.date(2025, 9, 10)
D = Decimal

TEMPLATES_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "frontend",
        "templates",
        "features",
        "finance",
    )
)


class _FakeHTML:
    def __init__(self, string=None, base_url=None):
        self.string = string

    def write_pdf(self):
        return b"%PDF-1.4\nstub\n"


#: Reachability here is a question about templates and roles, not about this
#: host's PDF libraries, so the engine is stubbed — the same trade
#: ``test_navigation_integrity.py`` makes. The stub still proves the template
#: rendered: ``pdf_response`` only returns a PDF once ``render_to_string`` has
#: succeeded, and its refusal branch answers 503, which fails these assertions.
_engine = patch.dict(sys.modules, {"weasyprint": SimpleNamespace(HTML=_FakeHTML)})


def read_template(name: str) -> str:
    with open(os.path.join(TEMPLATES_DIR, name), encoding="utf-8") as handle:
        return handle.read()


class FinancePageTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.other_term = make_term(
            cls.year,
            name="pg_term_2",
            start_date=datetime.date(2026, 1, 10),
            end_date=datetime.date(2026, 4, 10),
            is_current=False,
        )
        cls.school_class = make_class(cls.year)
        cls.admin = make_user("admin", username="pg_admin")
        cls.director = make_user("director", username="pg_director")
        cls.officer = make_user("finance", username="pg_officer")
        cls.student = make_student_user(cls.school_class, admission_number="PG-001")
        cls.classmate = make_student_user(cls.school_class, admission_number="PG-002")
        cls.settled = make_student_user(cls.school_class, admission_number="PG-003")
        cls.accounts = make_accounts()
        cls.structure = make_fee_structure(cls.school_class, cls.term, D("500.00"))
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, D("500.00")
        )
        cls.their_invoice = make_posted_invoice(
            cls.classmate, cls.term, cls.structure, D("500.00")
        )
        cls.settled_invoice = make_posted_invoice(
            cls.settled, cls.term, cls.structure, D("500.00")
        )
        cls.payment = services.record_payment(
            student=cls.student,
            amount=D("200.00"),
            method=Payment.Method.BANK,
            reference="PG-REF-1",
            payment_date=PAYMENT_DATE,
            recorded_by=cls.officer,
        )
        services.record_payment(
            student=cls.settled,
            amount=D("500.00"),
            method=Payment.Method.BANK,
            reference="PG-REF-2",
            payment_date=PAYMENT_DATE,
            recorded_by=cls.officer,
        )
        cls.adjustment = services.apply_adjustment(
            student=cls.student,
            invoice=cls.invoice,
            adjustment_type="waiver",
            amount=D("50.00"),
            reason="Bursary waiver",
            actor=cls.officer,
        )
        cls.expense = services.record_expense(
            account=cls.accounts["6000"],
            amount=D("40.00"),
            expense_date=PAYMENT_DATE,
            description="Generator fuel",
            payee_name="Acme Supplies",
            actor=cls.admin,
        )

    # -- helpers ------------------------------------------------------------

    def url(self, name: str, *, kwargs: dict | None = None, query: str = "") -> str:
        path = reverse(f"finance:{name}", kwargs=kwargs or {})
        return f"{path}?{query}" if query else path

    def get_page(
        self, name: str, *, kwargs: dict | None = None, query: str = ""
    ):
        return self.client.get(self.url(name, kwargs=kwargs, query=query))

    def as_officer(self):
        self.client.force_login(self.officer)

    def pages(self) -> list[tuple[str, dict, str]]:
        """Every finance URL that answers a GET, with the filter it needs."""
        term = f"term={self.term.pk}"
        return [
            ("overview", {}, ""),
            ("account_list", {}, ""),
            ("fee_structure_list", {}, ""),
            ("fee_structure_detail", {"pk": self.structure.pk}, ""),
            ("fee_structure_template", {}, ""),
            ("fee_structure_upload", {}, ""),
            ("fee_structure_upload_preview", {"pk": self.upload_job_pk()}, ""),
            ("invoice_list", {}, ""),
            ("invoice_detail", {"pk": self.invoice.pk}, ""),
            ("invoice_generate_preview", {}, term),
            ("payment_list", {}, ""),
            ("payment_detail", {"pk": self.payment.pk}, ""),
            ("payment_record", {}, ""),
            ("payment_receipt_pdf", {"pk": self.payment.pk}, ""),
            ("payment_reverse", {"pk": self.payment.pk}, ""),
            (
                "adjustment_create",
                {},
                f"student={self.student.pk}&invoice={self.invoice.pk}",
            ),
            ("ledger", {}, ""),
            ("trial_balance", {}, ""),
            ("report_summary", {}, term),
            ("outstanding_fees", {}, term),
            ("outstanding_export", {}, term),
            ("student_statement", {"pk": self.student.pk}, ""),
            ("student_statement_pdf", {"pk": self.student.pk}, ""),
            ("class_summary", {"pk": self.school_class.pk}, term),
            ("class_reports", {}, term),
            ("school_summary", {}, term),
            ("expense_list", {}, ""),
            ("expense_record", {}, ""),
            ("send_reminders", {}, ""),
        ]

    def upload_job_pk(self) -> int:
        """
        A READY job with parsed rows, so the preview page has something to draw.

        ``file`` is given a path instead of an uploaded file: the preview renders
        ``preview_rows``, and writing a throwaway .xlsx into MEDIA_ROOT for a
        rendering test would leave a stranger's file behind.
        """
        if not hasattr(self, "_upload_job"):
            self._upload_job = FeeStructureUploadJob.objects.create(
                uploaded_by=self.officer,
                file="fee_structure_uploads/pg-preview-only.xlsx",
                target_class=self.school_class,
                term=self.term,
                status=FeeStructureUploadJob.Status.READY,
                preview_rows=[{"label": "Tuition", "amount": "300.00", "_row": 2}],
            )
        return self._upload_job.pk


class EveryPageLoadsTests(FinancePageTestBase):
    """
    The checkpoint's bar is a bursar being able to click each entry, so the
    first assertion is simply that none of them 500s.
    """

    def setUp(self):
        super().setUp()
        self.as_officer()

    def test_every_finance_page_renders(self):
        failures = []
        with _engine:
            for name, kwargs, query in self.pages():
                response = self.get_page(name, kwargs=kwargs, query=query)
                if response.status_code != 200:
                    failures.append(f"  {name}: {response.status_code}")
        if failures:
            self.fail("Finance pages that did not render:\n" + "\n".join(failures))

    def test_pdf_pages_return_a_pdf_not_markup(self):
        """The loud-PDF ruling (D1a): never dress HTML up as a document."""
        with _engine:
            for name, kwargs in [
                ("payment_receipt_pdf", {"pk": self.payment.pk}),
                ("student_statement_pdf", {"pk": self.student.pk}),
            ]:
                response = self.get_page(name, kwargs=kwargs)
                self.assertEqual(response.status_code, 200, name)
                self.assertEqual(response["Content-Type"], "application/pdf")

    def test_download_pages_are_xlsx(self):
        for name, query in [
            ("fee_structure_template", ""),
            ("outstanding_export", f"term={self.term.pk}"),
        ]:
            response = self.get_page(name, query=query)
            self.assertEqual(response.status_code, 200, name)
            self.assertIn("spreadsheetml", response["Content-Type"])

    def test_read_only_roles_are_kept_off_the_write_pages(self):
        """
        Admin and Director may read the books; only Finance may post to them. A
        viewer handed a form it cannot save is worse than no page at all, so the
        403 is part of the contract being pinned here.
        """
        for user in (self.director, self.admin):
            self.client.force_login(user)
            for name in ("payment_record", "expense_record", "adjustment_create"):
                self.assertEqual(self.get_page(name).status_code, 403, name)
            self.assertEqual(self.get_page("payment_list").status_code, 200)
            self.assertEqual(self.get_page("trial_balance").status_code, 200)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        response = self.get_page("ledger")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(reverse("accounts:login")),
            response["Location"],
        )

    def test_a_hand_edited_filter_does_not_break_a_money_page(self):
        """
        Filters live in the query string, and people edit the query string.
        ``?term=abc`` used to reach ``filter(term_id="abc")`` and 500.
        """
        with _engine:
            for query in ("term=abc", "student=abc", "invoice=999999", "class=&term="):
                for name, kwargs in [
                    ("student_statement", {"pk": self.student.pk}),
                    ("invoice_list", {}),
                    ("fee_structure_list", {}),
                    ("payment_record", {}),
                    ("adjustment_create", {}),
                    ("class_summary", {"pk": self.school_class.pk}),
                    ("outstanding_fees", {}),
                    ("invoice_generate_preview", {}),
                ]:
                    path = self.url(name, kwargs=kwargs, query=query)
                    # Either the page loaded or the role gate refused it. A 500
                    # is the only answer a filter may never cause.
                    self.assertLess(self.client.get(path).status_code, 500, path)


class FiguresOnThePageTests(FinancePageTestBase):
    """
    Each page shows the interface's number rather than doing its own arithmetic.
    This is what keeps a Finance screen and the Director's page telling one
    story about the same term — the entire point of the facade.
    """

    def setUp(self):
        super().setUp()
        self.as_officer()

    def test_overview_reports_the_terms_own_totals(self):
        response = self.get_page("overview")
        totals = selectors.fee_totals(term=self.term)
        self.assertEqual(response.context["total_billed"], totals["billed"])
        self.assertEqual(response.context["outstanding_total"], totals["outstanding"])
        self.assertEqual(totals["billed"], D("1500.00"))
        self.assertEqual(totals["outstanding"], D("750.00"))
        self.assertIn("1,500.00", response.content.decode())

    def test_invoice_detail_shows_the_bill_on_its_own_basis(self):
        """
        ``paid_here`` / ``adjusted_here`` / ``outstanding_here`` — never
        ``Invoice.amount_outstanding``, which cannot see a waiver.
        """
        content = self.get_page(
            "invoice_detail", kwargs={"pk": self.invoice.pk}
        ).content.decode()
        self.assertIn("PG-001", content)
        self.assertIn("200.00", content)  # paid on this bill
        self.assertIn("50.00", content)  # waived on this bill
        self.assertIn("250.00", content)  # still owing on this bill

    def test_statement_balance_is_the_interface_balance(self):
        response = self.get_page("student_statement", kwargs={"pk": self.student.pk})
        stmt = selectors.student_statement(student_id=self.student.pk)
        self.assertEqual(response.context["balance"], stmt["balance"])
        self.assertEqual(response.context["total_paid"], D("200.00"))
        self.assertIn("250.00", response.content.decode())

    def test_outstanding_headline_is_the_term_figure(self):
        """
        The headline comes from ``fee_totals``, the same figure the Director's
        fee page shows, and the chasing list is debtors only — a pupil who has
        settled is not chased.
        """
        response = self.get_page("outstanding_fees", query=f"term={self.term.pk}")
        totals = response.context["totals"]
        rows = list(response.context["rows"])
        self.assertEqual(totals["outstanding"], D("750.00"))
        self.assertEqual(
            totals["outstanding"],
            selectors.fee_totals(term=self.term)["outstanding"],
        )
        self.assertEqual(sum(row["amount_outstanding"] for row in rows), D("750.00"))
        self.assertEqual(
            # Biggest debt first — the order an officer chases in.
            [row["admission_number"] for row in rows],
            ["PG-002", "PG-001"],
        )
        content = response.content.decode()
        self.assertIn("PG-001", content)
        self.assertNotIn("PG-003", content)  # paid in full, not a debtor

    def test_report_summary_keeps_its_two_bases_apart(self):
        """
        The fee half is the term/allocation basis, the income half is the
        ledger/date basis. Both are shown, labelled, and neither is added to the
        other.
        """
        response = self.get_page("report_summary", query=f"term={self.term.pk}")
        summary = response.context["summary"]
        self.assertEqual(summary["total_billed"], D("1500.00"))
        self.assertEqual(summary["total_expenses"], D("40.00"))
        self.assertEqual(summary["outstanding"], D("750.00"))

    def test_trial_balance_balances_and_says_so(self):
        response = self.get_page("trial_balance")
        self.assertTrue(response.context["balanced"])
        self.assertEqual(
            response.context["total_debits"], response.context["total_credits"]
        )

    def test_class_and_school_summaries_agree_with_the_interface(self):
        term = f"term={self.term.pk}"
        klass = self.get_page(
            "class_summary", kwargs={"pk": self.school_class.pk}, query=term
        )
        school = self.get_page("school_summary", query=term)
        totals = selectors.fee_totals(term=self.term, class_id=self.school_class.pk)
        self.assertEqual(klass.context["summary"]["total_billed"], totals["billed"])
        self.assertEqual(school.context["summary"]["total_billed"], totals["billed"])
        self.assertEqual(school.context["summary"]["total_expenses"], D("40.00"))

    def test_ledger_shows_the_receipt_and_the_claim(self):
        content = self.get_page("ledger").content.decode()
        self.assertIn("Expense: Generator fuel", content)
        self.assertIn(self.payment.receipt_number, content)

    def test_expense_list_shows_the_claim_as_posted(self):
        content = self.get_page("expense_list").content.decode()
        self.assertIn("Generator fuel", content)
        self.assertIn("Acme Supplies", content)

    def test_fee_structure_pages_total_the_items(self):
        listing = self.get_page("fee_structure_list").content.decode()
        detail = self.get_page(
            "fee_structure_detail", kwargs={"pk": self.structure.pk}
        ).content.decode()
        self.assertIn("Tuition", detail)
        self.assertIn("500.00", detail)
        self.assertIn("v1", listing)


class DeepLinkTests(FinancePageTestBase):
    """
    These pages are mostly reached from each other. A link that drops its
    subject makes the officer hunt for a child they were already looking at, and
    a printed statement that ignores the filter on the screen is a document that
    contradicts the page it came from.
    """

    def setUp(self):
        super().setUp()
        self.as_officer()

    def test_take_payment_prefills_the_pupil_from_the_query_string(self):
        response = self.get_page("payment_record", query=f"student={self.student.pk}")
        self.assertEqual(response.context["form"].initial["student"], self.student.pk)

    def test_take_payment_carries_an_idempotency_key(self):
        """The double-click guard has to be on the page before the first click."""
        key = self.get_page("payment_record").context["form"].initial[
            "idempotency_key"
        ]
        self.assertEqual(len(key), 32)

    def test_a_rejected_submission_keeps_the_key_it_was_posted_with(self):
        """
        Re-rendering after a failed POST must not mint a fresh key, or a
        corrected resubmission of the same receipt looks like a new payment to
        the service.
        """
        key = "0" * 32
        response = self.client.post(
            self.url("payment_record"),
            {
                "student": self.student.pk,
                "amount": "not money",
                "method": Payment.Method.BANK,
                "reference": "PG-REF-3",
                "payment_date": str(PAYMENT_DATE),
                "idempotency_key": key,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"]["idempotency_key"].value(), key)
        # Nothing was written: a rejected submission must not leave a receipt
        # that the officer then has to hunt down and delete.
        self.assertFalse(Payment.objects.filter(reference="PG-REF-3").exists())

    def test_adjustment_starts_from_the_bill_being_waived(self):
        response = self.get_page(
            "adjustment_create",
            query=f"student={self.student.pk}&invoice={self.invoice.pk}",
        )
        initial = response.context["form"].initial
        self.assertEqual(initial["student"], self.student.pk)
        self.assertEqual(initial["invoice"], self.invoice.pk)
        # The bill box is only useful once a pupil is chosen, and it must offer
        # the very bill the officer clicked through from.
        choices = [pk for pk, _ in response.context["form"]["invoice"].field.choices]
        self.assertIn(self.invoice.pk, choices)

    def test_the_printed_statement_honours_the_filter_on_the_screen(self):
        """?term= on the page must be the term on the paper."""
        query = f"term={self.other_term.pk}"
        with _engine:
            filtered = self.get_page(
                "student_statement", kwargs={"pk": self.student.pk}, query=query
            )
            self.assertEqual(filtered.status_code, 200)
            # Nothing was billed for the second term, so an empty bill list here
            # is what proves the filter reached the selector at all.
            self.assertEqual(filtered.context["invoices"], [])
            self.assertEqual(filtered.context["total_billed"], D("0.00"))
            pdf = self.get_page(
                "student_statement_pdf", kwargs={"pk": self.student.pk}, query=query
            )
            self.assertEqual(pdf.status_code, 200)

    def test_my_statement_sends_a_pupil_to_their_own(self):
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("finance:student_statement_self"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("finance:student_statement", kwargs={"pk": self.student.pk}),
        )


class WhatAPupilSeesTests(FinancePageTestBase):
    """
    A statement is the one finance page a family is pointed at, so it cannot
    assume the Finance role the rest of this module does.
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(self.student.user)

    def notes_url(self) -> str:
        return reverse("finance:student_finance_notes", kwargs={"pk": self.student.pk})

    def test_a_pupil_reads_their_own_statement(self):
        response = self.get_page("student_statement", kwargs={"pk": self.student.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn("PG-001", response.content.decode())

    def test_a_pupil_cannot_read_a_classmates(self):
        response = self.get_page("student_statement", kwargs={"pk": self.classmate.pk})
        self.assertEqual(response.status_code, 403)

    def test_a_pupil_is_not_handed_the_bursars_boxes(self):
        """
        Sponsor and payment-plan notes are Finance's fields. Showing a pupil an
        empty box that only an officer can save invites a change request that
        silently goes nowhere.
        """
        response = self.get_page("student_statement", kwargs={"pk": self.student.pk})
        self.assertFalse(response.context["is_finance_reader"])
        self.assertNotIn(self.notes_url(), response.content.decode())

    def test_an_officer_does_see_them(self):
        self.client.force_login(self.officer)
        response = self.get_page("student_statement", kwargs={"pk": self.student.pk})
        self.assertTrue(response.context["is_finance_reader"])
        self.assertIn(self.notes_url(), response.content.decode())


class UploadPreviewTests(FinancePageTestBase):
    """
    The preview page draws ``row._row`` and ``row._errors`` — keys the parser in
    ``validate_fee_structure_rows`` writes with a leading underscore. That is a
    template-lookup detail no other test here proves, so it is proved by
    rendering: a rejected row that shows no reason is worse than no preview.
    """

    def setUp(self):
        super().setUp()
        self.as_officer()

    def test_rejected_rows_render_their_reasons(self):
        job = FeeStructureUploadJob.objects.create(
            uploaded_by=self.officer,
            file="fee_structure_uploads/pg-rejected.xlsx",
            target_class=self.school_class,
            term=self.term,
            status=FeeStructureUploadJob.Status.FAILED,
            preview_rows=[{"label": "Tuition", "amount": "300.00", "_row": 2}],
            error_rows=[
                {
                    "label": "",
                    "amount": "abc",
                    "_row": 5,
                    "_errors": ["label is required", "amount 'abc' is not valid"],
                }
            ],
        )
        content = self.get_page(
            "fee_structure_upload_preview", kwargs={"pk": job.pk}
        ).content.decode()
        self.assertIn("label is required", content)
        self.assertIn("amount &#x27;abc&#x27; is not valid", content)
        self.assertIn("300.00", content)  # the one good row is still offered
        self.assertIn(">2<", content)  # row numbers come from _row
        self.assertIn(">5<", content)

    def test_a_ready_job_offers_the_commit_button(self):
        content = self.get_page(
            "fee_structure_upload_preview", kwargs={"pk": self.upload_job_pk()}
        ).content.decode()
        self.assertIn(
            reverse(
                "finance:fee_structure_upload_commit", kwargs={"pk": self.upload_job_pk()}
            ),
            content,
        )

    def test_a_job_still_being_checked_tells_the_officer_to_reload(self):
        job = FeeStructureUploadJob.objects.create(
            uploaded_by=self.officer,
            file="fee_structure_uploads/pg-pending.xlsx",
            target_class=self.school_class,
            term=self.term,
            status=FeeStructureUploadJob.Status.PENDING,
        )
        content = self.get_page(
            "fee_structure_upload_preview", kwargs={"pk": job.pk}
        ).content.decode()
        self.assertIn("reload", content.lower())


class RemindersPageTests(FinancePageTestBase):
    """
    The reminder page is the one screen whose promise was wider than the code
    behind it: ``send_fee_reminder_task`` ignores ``student_ids`` and publishes a
    by-role announcement. The honest version says so on the page instead of
    implying named pupils will be named.
    """

    def setUp(self):
        super().setUp()
        self.as_officer()

    def test_the_page_shows_the_debtors_and_admits_how_delivery_works(self):
        content = self.get_page("send_reminders").content.decode()
        self.assertIn("PG-001", content)
        self.assertIn("not wired up", content)
        self.assertIn("one announcement", content)


class MarkupCarriesNoArithmeticTests(FinancePageTestBase):
    """
    ``Invoice.amount_outstanding`` ignores waivers, so a template that reaches
    for it overstates a debt. Every page in this checkpoint reads the annotated
    figures instead; this keeps that decision from being quietly undone by an
    edit several checkpoints from now.
    """

    def test_no_template_reads_the_waiver_blind_invoice_property(self):
        offenders = [
            name
            for name in sorted(os.listdir(TEMPLATES_DIR))
            if name.endswith(".html")
            and "invoice.amount_outstanding" in read_template(name)
        ]
        self.assertEqual(offenders, [])

    def test_the_bill_pages_read_the_annotations(self):
        for name in ("invoice_detail.html", "student_statement.html"):
            text = read_template(name)
            for annotation in ("paid_here", "adjusted_here", "outstanding_here"):
                self.assertIn(annotation, text, f"{name} lost {annotation}")


class TermStatementTests(FinancePageTestBase):
    """
    FS-4: a statement for one term may only count that term's money.

    A Term 1 statement used to subtract Term 2's receipts, so the page could
    show a family a balance the term had already settled — or, worse, one that
    another term's payment had quietly reduced.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.term2_structure = make_fee_structure(
            cls.school_class, cls.other_term, D("500.00")
        )
        cls.term2_invoice = make_posted_invoice(
            cls.student, cls.other_term, cls.term2_structure, D("500.00")
        )
        cls.term2_payment = services.record_payment(
            student=cls.student,
            amount=D("300.00"),
            method=Payment.Method.BANK,
            reference="PG-REF-T2",
            payment_date=datetime.date(2026, 2, 10),
            recorded_by=cls.officer,
            allocations=[
                {"invoice": cls.term2_invoice, "amount": D("300.00")}
            ],
        )
        services.apply_adjustment(
            student=cls.student,
            invoice=cls.term2_invoice,
            adjustment_type="scholarship",
            amount=D("20.00"),
            reason="Term 2 bursary",
            actor=cls.officer,
        )
        # An ask awaiting a signature: listed, never deducted.
        Adjustment.objects.create(
            student=cls.student,
            invoice=cls.term2_invoice,
            adjustment_type=Adjustment.AdjustmentType.WRITE_OFF,
            status=Adjustment.Status.PENDING,
            amount=D("30.00"),
            reason="Awaiting an admin signature",
        )
        cls.draft_invoice = Invoice.objects.create(
            student=cls.classmate,
            term=cls.other_term,
            fee_structure=cls.term2_structure,
            issued_date=datetime.date(2026, 1, 12),
            total_amount=D("500.00"),
            status=Invoice.Status.DRAFT,
        )

    def statement(self, term=None, student=None) -> dict:
        return selectors.student_statement(
            student_id=(student or self.student).pk, term=term
        )

    def test_a_term_statement_counts_only_that_terms_money(self):
        term1 = self.statement(term=self.term)

        self.assertEqual(term1["total_billed"], D("500.00"))
        self.assertEqual(term1["total_paid"], D("200.00"))
        self.assertEqual(term1["total_adjusted"], D("50.00"))
        self.assertEqual(term1["balance"], D("250.00"))
        self.assertEqual(term1["brought_forward"], D("0.00"))
        self.assertEqual(term1["closing_balance"], D("250.00"))
        # The Term 2 receipt and bursary must not be on this statement.
        self.assertEqual([p.pk for p in term1["payments"]], [self.payment.pk])
        self.assertEqual([a.pk for a in term1["adjustments"]], [self.adjustment.pk])

    def test_the_later_term_carries_the_earlier_balance_forward(self):
        term2 = self.statement(term=self.other_term)

        self.assertEqual(term2["total_billed"], D("500.00"))
        self.assertEqual(term2["total_paid"], D("300.00"))
        # The pending 30.00 is listed but not deducted.
        self.assertEqual(term2["total_adjusted"], D("20.00"))
        self.assertEqual(term2["balance"], D("180.00"))
        self.assertEqual(term2["brought_forward"], D("250.00"))
        self.assertEqual(term2["closing_balance"], D("430.00"))
        self.assertEqual(len(term2["adjustments"]), 2)
        self.assertEqual(term2["term_status"], "Part-paid")

    def test_all_terms_keeps_its_original_arithmetic(self):
        everything = self.statement()

        self.assertEqual(everything["total_billed"], D("1000.00"))
        self.assertEqual(everything["total_paid"], D("500.00"))
        self.assertEqual(everything["total_adjusted"], D("70.00"))
        self.assertEqual(everything["balance"], D("430.00"))
        self.assertEqual(everything["brought_forward"], D("0.00"))
        self.assertEqual(everything["closing_balance"], D("430.00"))
        self.assertEqual(selectors.student_balance(student_id=self.student.pk), D("430.00"))

    def test_credit_held_is_reported_outside_the_terms(self):
        services.record_payment(
            student=self.settled,
            amount=D("100.00"),
            method=Payment.Method.MOBILE_MONEY,
            reference="PG-REF-CREDIT",
            payment_date=datetime.date(2026, 2, 11),
            recorded_by=self.officer,
        )

        term1 = self.statement(term=self.term, student=self.settled)

        self.assertEqual(term1["total_paid"], D("500.00"))
        self.assertEqual(term1["balance"], D("0.00"))
        self.assertEqual(term1["credit_held"], D("100.00"))
        self.assertEqual(term1["term_status"], "Settled")

    def test_a_draft_bill_is_listed_but_not_billed(self):
        draft = self.statement(term=self.other_term, student=self.classmate)

        self.assertEqual(draft["total_billed"], D("0.00"))
        self.assertEqual(draft["balance"], D("0.00"))
        self.assertEqual(draft["term_status"], "Draft only")
        self.assertEqual([i.pk for i in draft["invoices"]], [self.draft_invoice.pk])

    def test_the_page_and_printed_statement_show_the_terms_arithmetic(self):
        self.as_officer()
        query = f"term={self.other_term.pk}"

        response = self.get_page(
            "student_statement", kwargs={"pk": self.student.pk}, query=query
        )
        self.assertEqual(response.context["brought_forward"], D("250.00"))
        self.assertEqual(response.context["closing_balance"], D("430.00"))
        self.assertEqual(response.context["term_status"], "Part-paid")
        content = response.content.decode()
        self.assertIn("Brought forward", content)
        self.assertIn("Closing balance", content)
        self.assertIn("430.00", content)
        self.assertIn(self.term2_payment.receipt_number, content)
        self.assertNotIn(self.payment.receipt_number, content)
        self.assertIn("Awaiting approval", content)

        with _engine:
            rendered = self.get_page(
                "student_statement_pdf", kwargs={"pk": self.student.pk}, query=query
            )
        self.assertEqual(rendered.status_code, 200)

    def test_a_pupil_sees_the_term_filter_but_not_a_classmate(self):
        self.client.force_login(self.student.user)
        query = f"term={self.other_term.pk}"

        own = self.get_page(
            "student_statement", kwargs={"pk": self.student.pk}, query=query
        )
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.context["closing_balance"], D("430.00"))

        theirs = self.get_page(
            "student_statement", kwargs={"pk": self.classmate.pk}, query=query
        )
        self.assertEqual(theirs.status_code, 403)


class _RecordingHTML(_FakeHTML):
    """The stub PDF engine, keeping the HTML it was asked to print."""

    last = None

    def __init__(self, string=None, base_url=None):
        super().__init__(string=string, base_url=base_url)
        type(self).last = string


class ReceiptPrintingTests(FinancePageTestBase):
    """
    FS-5: the printed receipt records itself.

    A receipt printed twice and presented twice as proof of two payments is the
    oldest trick in a fee office, so the second and later copies carry a
    DUPLICATE mark and the count is visible on the record.
    """

    def setUp(self):
        super().setUp()
        self.as_officer()
        self.payment.refresh_from_db()

    def printed_html(self, payment=None) -> str:
        _RecordingHTML.last = None
        with patch.dict(
            sys.modules, {"weasyprint": SimpleNamespace(HTML=_RecordingHTML)}
        ):
            response = self.get_page(
                "payment_receipt_pdf", kwargs={"pk": (payment or self.payment).pk}
            )
        self.assertEqual(response.status_code, 200)
        return _RecordingHTML.last

    def test_the_first_copy_is_the_receipt_and_the_next_is_a_duplicate(self):
        first = self.printed_html()

        self.assertNotIn("Duplicate copy", first)
        self.assertIn(self.payment.verification_code, first)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.print_count, 1)
        self.assertIsNotNone(self.payment.last_printed_at)

        second = self.printed_html()

        self.assertIn("Duplicate copy", second)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.print_count, 2)
        self.assertEqual(
            AuditLog.objects.filter(
                action="finance.receipt_printed", object_id=str(self.payment.pk)
            ).count(),
            2,
        )

    def test_a_failed_pdf_is_not_counted_as_a_print(self):
        with patch(
            "apps.core.pdf.pdf_response",
            return_value=HttpResponse(status=503),
        ):
            response = self.get_page(
                "payment_receipt_pdf", kwargs={"pk": self.payment.pk}
            )

        self.assertEqual(response.status_code, 503)
        self.payment.refresh_from_db()
        self.assertEqual(
            self.payment.print_count,
            0,
            msg="The count is the control, so a 503 that produced no paper must "
            "not burn a copy.",
        )

    def test_the_detail_page_offers_a_duplicate_reprint_once_printed(self):
        content = self.get_page(
            "payment_detail", kwargs={"pk": self.payment.pk}
        ).content.decode()
        self.assertIn("Print receipt", content)
        self.assertNotIn("Reprint (DUPLICATE)", content)

        services.mark_receipt_printed(payment=self.payment, actor=self.officer)

        content = self.get_page(
            "payment_detail", kwargs={"pk": self.payment.pk}
        ).content.decode()
        self.assertIn("Reprint (DUPLICATE)", content)
        self.assertIn("The next copy will be marked DUPLICATE.", content)

    def test_a_pupil_cannot_print_a_receipt_from_the_finance_pages(self):
        self.client.force_login(self.student.user)
        response = self.get_page(
            "payment_receipt_pdf", kwargs={"pk": self.payment.pk}
        )
        self.assertEqual(response.status_code, 403)
