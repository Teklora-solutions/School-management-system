"""
apps/finance/tests/test_reports.py
----------------------------------
FS-3 — year and term reports.

Two reports, three ways to ask each of them:

    a term          "how did Term 1 go?"
    a whole year    "how did the year go?"   (the "Whole year" period)
    a class         "how did one class do, in that term or year?"

The thing this file is really pinning is that those are one window, not three.
Before FS-3 the School Summary, the Income & Expense page and the class page
each chose their own period, so "billed" on one screen could be a term and on
the next a year. Every figure below comes from ``year_finance_summary``; the
tests compare the parts against each other, not against a number typed twice.

The books:

    Term 1     billed 1,000.00  collected 700.00  waived 50.00  due 250.00
    Term 2     billed   300.00  collected   0.00  waived  0.00  due 300.00
    whole year billed 1,300.00  collected 700.00  waived 50.00  due 550.00
    expenses       40.00 inside Term 1, 60.00 inside Term 2, nothing outside
    Grade 3         no invoice at all — the class a summary must not hide
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

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

from .. import interface, selectors, services
from ..forms import ReportPeriodForm
from ..models import Payment

D = Decimal


class ReportBooksTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.later_term = make_term(
            cls.year,
            name="term_2",
            start_date=datetime.date(2026, 1, 10),
            end_date=datetime.date(2026, 4, 10),
            is_current=False,
        )
        cls.class_a = make_class(cls.year, name="Grade 5", level_order=5)
        cls.class_b = make_class(cls.year, name="Grade 3", level_order=3)
        cls.officer = make_user("finance", username="rp_officer")
        cls.accounts = make_accounts()
        cls.structure = make_fee_structure(cls.class_a, cls.term, D("500.00"))
        cls.later_structure = make_fee_structure(
            cls.class_a, cls.later_term, D("300.00")
        )

        cls.pupil = make_student_user(cls.class_a, admission_number="RP-001")
        cls.classmate = make_student_user(cls.class_a, admission_number="RP-002")
        cls.unbilled = make_student_user(cls.class_b, admission_number="RP-003")

        cls.first_invoice = make_posted_invoice(
            cls.pupil, cls.term, cls.structure, D("500.00")
        )
        cls.classmate_invoice = make_posted_invoice(
            cls.classmate, cls.term, cls.structure, D("500.00")
        )
        # Recorded before the Term 2 invoice exists, so the oldest-first
        # allocation lands in Term 1 where these tests expect it.
        services.record_payment(
            student=cls.pupil,
            amount=D("200.00"),
            method=Payment.Method.BANK,
            reference="RP-REF-1",
            payment_date=datetime.date(2025, 9, 10),
            recorded_by=cls.officer,
        )
        services.record_payment(
            student=cls.classmate,
            amount=D("500.00"),
            method=Payment.Method.BANK,
            reference="RP-REF-2",
            payment_date=datetime.date(2025, 9, 10),
            recorded_by=cls.officer,
        )
        services.apply_adjustment(
            student=cls.pupil,
            invoice=cls.first_invoice,
            adjustment_type="waiver",
            amount=D("50.00"),
            reason="Bursary waiver",
            actor=cls.officer,
        )
        cls.later_invoice = make_posted_invoice(
            cls.pupil, cls.later_term, cls.later_structure, D("300.00")
        )
        services.record_expense(
            account=cls.accounts["6000"],
            amount=D("40.00"),
            expense_date=datetime.date(2025, 9, 10),
            description="Term 1 fuel",
            payee_name="Acme Supplies",
            actor=cls.officer,
        )
        services.record_expense(
            account=cls.accounts["6000"],
            amount=D("60.00"),
            expense_date=datetime.date(2026, 2, 10),
            description="Term 2 fuel",
            payee_name="Acme Supplies",
            actor=cls.officer,
        )

    def whole_year(self) -> dict:
        return selectors.year_finance_summary(year_id=self.year.pk)

    def one_term(self) -> dict:
        return selectors.year_finance_summary(
            year_id=self.year.pk, term_id=self.term.pk
        )

    def url(self, name: str, *, kwargs: dict | None = None, query: str = "") -> str:
        path = reverse(f"finance:{name}", kwargs=kwargs or {})
        return f"{path}?{query}" if query else path

    def get_page(self, name: str, *, kwargs: dict | None = None, query: str = ""):
        return self.client.get(self.url(name, kwargs=kwargs, query=query))


class YearSummaryFiguresTests(ReportBooksTestBase):
    def test_the_whole_year_adds_the_terms_up(self):
        summary = self.whole_year()
        self.assertIsNone(summary["term"])
        self.assertEqual(summary["total_billed"], D("1300.00"))
        self.assertEqual(summary["total_paid"], D("700.00"))
        self.assertEqual(summary["total_adjusted"], D("50.00"))
        self.assertEqual(summary["outstanding"], D("550.00"))

    def test_every_term_of_the_year_keeps_its_row_even_unbilled(self):
        rows = self.whole_year()["per_term"]
        self.assertEqual([row["term"] for row in rows], [self.term, self.later_term])
        first, second = rows
        self.assertEqual(
            (first["billed"], first["collected"], first["adjusted"], first["outstanding"]),
            (D("1000.00"), D("700.00"), D("50.00"), D("250.00")),
        )
        self.assertEqual(
            (second["billed"], second["collected"], second["adjusted"], second["outstanding"]),
            (D("300.00"), D("0.00"), D("0.00"), D("300.00")),
        )

    def test_the_per_term_rows_are_the_whole_year(self):
        summary = self.whole_year()
        for key, total in (
            ("billed", summary["total_billed"]),
            ("collected", summary["total_paid"]),
            ("adjusted", summary["total_adjusted"]),
            ("outstanding", summary["outstanding"]),
        ):
            with self.subTest(key=key):
                self.assertEqual(sum(row[key] for row in summary["per_term"]), total)

    def test_a_term_report_is_the_same_term_the_interface_reports(self):
        summary = self.one_term()
        fees = selectors.fee_totals(term=self.term)
        self.assertEqual(summary["term"], self.term)
        self.assertEqual(summary["total_billed"], fees["billed"])
        self.assertEqual(summary["total_paid"], fees["collected"])
        self.assertEqual(summary["outstanding"], fees["outstanding"])

    def test_a_term_report_still_lists_its_sibling_terms(self):
        self.assertEqual(
            [row["term"] for row in self.one_term()["per_term"]],
            [self.term, self.later_term],
            "a term report whose per-term table loses the other terms cannot show "
            "where the arrears came from",
        )

    def test_expenses_follow_the_period_they_are_dated_in(self):
        self.assertEqual(self.one_term()["total_expenses"], D("40.00"))
        self.assertEqual(self.whole_year()["total_expenses"], D("100.00"))
        self.assertEqual(self.one_term()["net_income"], D("660.00"))
        self.assertEqual(self.whole_year()["net_income"], D("600.00"))

    def test_the_year_and_the_term_disagree_only_by_the_terms_they_do_not_share(self):
        year, term = self.whole_year(), self.one_term()
        self.assertEqual(
            year["total_billed"] - term["total_billed"], D("300.00")
        )

    def test_a_term_from_another_year_is_refused(self):
        other_year = make_year("2024/2025", is_current=False)
        other_term = make_term(other_year, is_current=False)
        with self.assertRaises(ValueError):
            selectors.year_finance_summary(
                year_id=self.year.pk, term_id=other_term.pk
            )


class PerClassFiguresTests(ReportBooksTestBase):
    def test_the_whole_year_lists_only_classes_that_were_billed(self):
        rows = self.whole_year()["per_class"]
        self.assertEqual([row["class_id"] for row in rows], [self.class_a.pk])
        row = rows[0]
        self.assertEqual(row["billed"], D("1300.00"))
        self.assertEqual(row["paid"], D("700.00"))
        self.assertEqual(row["adjusted"], D("50.00"))
        self.assertEqual(row["outstanding"], D("550.00"))

    def test_a_class_report_carries_every_pupil_and_the_figures_add_up(self):
        summary = selectors.class_finance_summary(
            school_class_id=self.class_a.pk, year_id=self.year.pk
        )
        self.assertEqual(summary["total_billed"], D("1300.00"))
        self.assertEqual(summary["outstanding"], D("550.00"))
        self.assertEqual(summary["student_count"], 2)
        by_admission = {
            row["student"].admission_number: row for row in summary["per_student"]
        }
        pupil = by_admission["RP-001"]
        self.assertEqual(
            (pupil["billed"], pupil["paid"], pupil["adjusted"], pupil["outstanding"]),
            (D("800.00"), D("200.00"), D("50.00"), D("550.00")),
        )
        classmate = by_admission["RP-002"]
        self.assertEqual(classmate["outstanding"], D("0.00"))
        self.assertEqual(
            sum(row["outstanding"] for row in summary["per_student"]),
            summary["outstanding"],
        )

    def test_a_class_report_for_a_term_leaves_the_other_term_out(self):
        summary = selectors.class_finance_summary(
            school_class_id=self.class_a.pk, term_id=self.term.pk
        )
        self.assertEqual(summary["term"], self.term)
        self.assertEqual(summary["total_billed"], D("1000.00"))
        self.assertEqual(summary["outstanding"], D("250.00"))

    def test_a_class_with_nothing_billed_is_zeros_rather_than_an_error(self):
        summary = selectors.class_finance_summary(
            school_class_id=self.class_b.pk, year_id=self.year.pk
        )
        self.assertEqual(summary["total_billed"], D("0.00"))
        self.assertEqual(summary["outstanding"], D("0.00"))
        self.assertIsNone(summary["collection_rate"])
        self.assertEqual(summary["per_student"], [])

    def test_a_term_from_another_year_is_refused_for_a_class_too(self):
        other_year = make_year("2024/2025", is_current=False)
        other_term = make_term(other_year, is_current=False)
        with self.assertRaises(ValueError):
            selectors.class_finance_summary(
                school_class_id=self.class_a.pk,
                term_id=other_term.pk,
                year_id=self.year.pk,
            )

    def test_the_school_summary_and_the_class_page_agree(self):
        school = self.whole_year()
        for row in school["per_class"]:
            with self.subTest(school_class=row["class_name"]):
                per_class = selectors.class_finance_summary(
                    school_class_id=row["class_id"], year_id=self.year.pk
                )
                self.assertEqual(per_class["total_billed"], row["billed"])
                self.assertEqual(per_class["total_paid"], row["paid"])
                self.assertEqual(per_class["outstanding"], row["outstanding"])


class IncomeExpenseFiguresTests(ReportBooksTestBase):
    def test_the_whole_year_report_runs_over_the_year_dates(self):
        summary = selectors.report_summary(year_id=self.year.pk)
        self.assertIsNone(summary["term"])
        self.assertEqual(summary["date_from"], self.year.start_date)
        self.assertEqual(summary["date_to"], self.year.end_date)
        self.assertEqual(summary["total_expenses"], D("100.00"))

    def test_the_term_report_runs_over_the_term_dates(self):
        summary = selectors.report_summary(term_id=self.term.pk)
        self.assertEqual(summary["term"], self.term)
        self.assertEqual(summary["date_from"], self.term.start_date)
        self.assertEqual(summary["date_to"], self.term.end_date)
        self.assertEqual(summary["total_expenses"], D("40.00"))

    def test_the_fee_half_matches_the_school_summary_for_the_same_window(self):
        whole, term = self.whole_year(), self.one_term()
        year_report = selectors.report_summary(year_id=self.year.pk)
        term_report = selectors.report_summary(term_id=self.term.pk)
        self.assertEqual(year_report["total_billed"], whole["total_billed"])
        self.assertEqual(year_report["total_collected"], whole["total_paid"])
        self.assertEqual(term_report["total_billed"], term["total_billed"])
        self.assertEqual(term_report["outstanding"], term["outstanding"])

    def test_asking_for_no_period_is_a_programming_error_not_a_default(self):
        with self.assertRaises(ValueError):
            selectors.report_summary()


class ContractTests(TestCase):
    def test_the_year_summary_is_reachable_through_the_interface(self):
        self.assertIs(
            interface.year_finance_summary, selectors.year_finance_summary
        )

    def test_the_old_term_shaped_name_still_answers_the_same_question(self):
        cls = make_class(make_year("2030/2031"))
        term = make_term(cls.academic_year)
        self.assertEqual(
            selectors.school_finance_summary(term_id=term.pk)["total_billed"],
            selectors.fee_totals(term=term)["billed"],
        )


class PeriodFormTests(ReportBooksTestBase):
    def test_a_term_on_its_own_implies_its_year(self):
        form = ReportPeriodForm({"term": self.later_term.pk})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["year"], self.year)
        self.assertEqual(form.cleaned_data["term"], self.later_term)

    def test_an_empty_term_is_the_whole_year(self):
        form = ReportPeriodForm({"year": self.year.pk})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["year"], self.year)
        self.assertIsNone(form.cleaned_data["term"])

    def test_the_term_list_is_limited_to_the_chosen_year(self):
        other_year = make_year("2024/2025", is_current=False)
        make_term(other_year, is_current=False)
        form = ReportPeriodForm({"year": self.year.pk})
        self.assertEqual(
            set(form.fields["term"].queryset.values_list("pk", flat=True)),
            {self.term.pk, self.later_term.pk},
        )

    def test_the_year_select_lists_the_newest_year_first(self):
        make_year(
            "2024/2025",
            start_date=datetime.date(2024, 9, 1),
            end_date=datetime.date(2025, 7, 31),
            is_current=False,
        )
        form = ReportPeriodForm()
        names = list(form.fields["year"].queryset.values_list("name", flat=True))
        self.assertEqual(names[0], "2025/2026")


class ReportPageTests(ReportBooksTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.officer)

    def test_the_school_summary_opens_on_the_current_term_when_nothing_is_asked(self):
        content = self.get_page("school_summary").content.decode()
        self.assertIn("Term 1", content)
        self.assertNotIn("Whole year —", content)

    def test_the_school_summary_can_report_the_whole_year(self):
        content = self.get_page(
            "school_summary", query=f"year={self.year.pk}&term="
        ).content.decode()
        self.assertIn("Whole year — 2025/2026", content)
        self.assertIn("By term", content)
        self.assertIn("Term 2", content)
        self.assertIn("1,300.00", content)

    def test_a_term_only_link_still_opens_that_term(self):
        content = self.get_page(
            "school_summary", query=f"term={self.later_term.pk}"
        ).content.decode()
        self.assertIn("1,000.00", content, "Term 1 figures missing")
        self.assertIn("2025/2026 – Term 2", content)

    def test_a_term_from_another_year_is_shown_as_an_error_not_a_guess(self):
        other_year = make_year("2024/2025", is_current=False)
        other_term = make_term(other_year, is_current=False)
        response = self.get_page(
            "school_summary",
            query=f"year={self.year.pk}&term={other_term.pk}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].is_valid())
        self.assertIsNone(response.context["summary"])
        content = response.content.decode()
        self.assertNotIn("By term", content)

    def test_the_class_reports_page_shows_a_class_nothing_was_billed_to(self):
        content = self.get_page(
            "class_reports", query=f"year={self.year.pk}&term="
        ).content.decode()
        self.assertIn("Grade 5", content)
        self.assertIn("Grade 3", content)
        self.assertIn("Not invoiced", content)
        self.assertIn("1,300.00", content)

    def test_the_class_reports_page_narrows_to_a_term(self):
        content = self.get_page(
            "class_reports", query=f"year={self.year.pk}&term={self.term.pk}"
        ).content.decode()
        self.assertIn("1,000.00", content)
        self.assertNotIn("1,300.00", content)
        self.assertIn("Grade 3", content, "an uninvoiced class must not drop out")

    def test_the_class_page_reports_a_whole_year_and_links_its_pupils(self):
        content = self.get_page(
            "class_summary",
            kwargs={"pk": self.class_a.pk},
            query=f"year={self.year.pk}&term=",
        ).content.decode()
        self.assertIn("Whole year — 2025/2026", content)
        self.assertIn("RP-001", content)
        self.assertIn("RP-002", content)

    def test_the_income_and_expense_page_labels_its_window(self):
        content = self.get_page(
            "report_summary", query=f"year={self.year.pk}&term="
        ).content.decode()
        self.assertIn("Whole year — 2025/2026", content)
        self.assertIn("year dates", content)
        self.assertIn("100.00", content)

    def test_a_teacher_cannot_reach_the_reports(self):
        self.client.force_login(make_user("teacher", username="rp_teacher"))
        for name in ("school_summary", "report_summary", "class_reports"):
            with self.subTest(page=name):
                self.assertEqual(self.get_page(name).status_code, 403)
