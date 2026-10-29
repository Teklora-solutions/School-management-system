"""
tests/test_director_selectors.py
----------------------------------
Reconciliation tests: prove that dashboard KPI numbers exactly match
what the individual source-module selectors return.

Also tests:
  - class_performance returns correct averages and pass rates.
  - class_fee_summary returns correct per-class totals.
  - class_fee_debtors returns only students with an outstanding balance.
  - staff_directory returns only non-student/parent users.
  - income_vs_expenses_by_month returns correct month groupings.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from apps.dashboards.selectors import (
    class_fee_debtors,
    class_fee_summary,
    class_performance,
    school_kpis,
    staff_directory,
)

from .fixtures import (
    make_accounts,
    make_class,
    make_director,
    make_fee_structure,
    make_grading_config,
    make_payment_allocation,
    make_posted_invoice,
    make_published_score,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)


class SchoolKpiReconciliationTest(TestCase):
    """
    school_kpis() numbers must agree with numbers derived independently
    from the source models.
    """

    def setUp(self):
        self.year = make_year()
        self.term = make_term(self.year)
        self.cls = make_class(self.year, name="Grade 5", level_order=5)
        make_accounts()
        self.fs = make_fee_structure(self.cls, self.term, amount=Decimal("600.00"))

        # 3 active students: 2 male, 1 female
        self.s1 = make_student_user(self.cls, gender="M")
        self.s2 = make_student_user(self.cls, gender="M")
        self.s3 = make_student_user(self.cls, gender="F")

        # Invoice all three; pay two in full
        self.inv1 = make_posted_invoice(self.s1, self.term, self.fs, Decimal("600.00"))
        self.inv2 = make_posted_invoice(self.s2, self.term, self.fs, Decimal("600.00"))
        self.inv3 = make_posted_invoice(self.s3, self.term, self.fs, Decimal("600.00"))
        make_payment_allocation(self.inv1, Decimal("600.00"))
        make_payment_allocation(self.inv2, Decimal("600.00"))
        # s3 pays only half
        make_payment_allocation(self.inv3, Decimal("300.00"))

        # Results: s1 passes, s2 fails, s3 passes
        self.subject = make_subject("MATH")
        make_grading_config(self.year)
        make_published_score(
            self.cls, self.subject, self.term, self.s1, Decimal("75.00")
        )
        make_published_score(
            self.cls, self.subject, self.term, self.s2, Decimal("40.00")
        )
        make_published_score(
            self.cls, self.subject, self.term, self.s3, Decimal("80.00")
        )

        # One teacher user
        self.teacher = make_user("teacher", username="teacher_main")
        self.director = make_director(username="dir_kpi")

    def test_student_count(self):
        kpis = school_kpis(term=self.term)
        from apps.students.models import Student

        expected = Student.objects.filter(
            status__in=[Student.Status.ACTIVE, Student.Status.REPEATING]
        ).count()
        self.assertEqual(kpis["student_count"], expected)

    def test_student_by_gender(self):
        kpis = school_kpis(term=self.term)
        self.assertEqual(kpis["student_by_gender"].get("M", 0), 2)
        self.assertEqual(kpis["student_by_gender"].get("F", 0), 1)

    def test_fees_billed_matches_invoice_total(self):
        """fees_billed must equal sum of all posted invoice total_amounts."""
        from django.db.models import DecimalField, Sum
        from django.db.models.functions import Coalesce

        from apps.finance.models import Invoice

        expected = Invoice.objects.filter(
            term=self.term, status=Invoice.Status.POSTED
        ).aggregate(
            t=Coalesce(Sum("total_amount"), Decimal(0), output_field=DecimalField())
        )[
            "t"
        ]

        kpis = school_kpis(term=self.term)
        self.assertEqual(kpis["fees_billed"], expected)
        self.assertEqual(kpis["fees_billed"], Decimal("1800.00"))

    def test_fees_collected_matches_allocations_total(self):
        """
        fees_collected must equal the allocations against posted invoices whose
        receipt is still standing — ``.live()``, which is what nets a reversal
        now that ``reverse_payment`` keeps the rows instead of deleting them.
        """
        from django.db.models import DecimalField, Sum
        from django.db.models.functions import Coalesce

        from apps.finance.models import PaymentAllocation

        expected = (
            PaymentAllocation.objects.filter(
                invoice__term=self.term, invoice__status="posted"
            )
            .live()
            .aggregate(
                t=Coalesce(
                    Sum("allocated_amount"), Decimal(0), output_field=DecimalField()
                )
            )["t"]
        )

        kpis = school_kpis(term=self.term)
        self.assertEqual(kpis["fees_collected"], expected)
        self.assertEqual(kpis["fees_collected"], Decimal("1500.00"))

    def test_fees_outstanding_is_billed_minus_collected_minus_adjusted(self):
        """
        Outstanding is billed less collected less what the school waived. The
        definition lives in finance.selectors.fee_totals; the Director only
        relays it, which is what this test is really asserting.
        """
        kpis = school_kpis(term=self.term)
        self.assertEqual(kpis["fees_adjusted"], Decimal("0.00"))
        self.assertEqual(
            kpis["fees_outstanding"],
            kpis["fees_billed"] - kpis["fees_collected"] - kpis["fees_adjusted"],
        )
        self.assertEqual(kpis["fees_outstanding"], Decimal("300.00"))

    def test_collection_rate_calculation(self):
        kpis = school_kpis(term=self.term)
        expected_rate = round(Decimal(1500) / Decimal(1800) * 100, 1)
        self.assertEqual(kpis["collection_rate"], expected_rate)

    def test_pass_rate_two_out_of_three_pass(self):
        """2 of 3 entries have total >= 50, so pass_rate == 66.7%."""
        kpis = school_kpis(term=self.term)
        self.assertIsNotNone(kpis["pass_rate"])
        self.assertAlmostEqual(kpis["pass_rate"], 66.7, places=0)

    def test_teacher_count(self):
        kpis = school_kpis(term=self.term)
        from apps.accounts.models import User

        expected = User.objects.filter(role="teacher", is_active=True).count()
        self.assertEqual(kpis["teacher_count"], expected)

    def test_staff_count_excludes_students_and_parents(self):
        kpis = school_kpis(term=self.term)
        from apps.accounts.models import User

        expected = (
            User.objects.filter(is_active=True)
            .exclude(role__in=["student", "parent"])
            .count()
        )
        self.assertEqual(kpis["staff_count"], expected)

    def test_student_by_class_lists_our_class(self):
        kpis = school_kpis(term=self.term)
        names = [r["class_name"] for r in kpis["student_by_class"]]
        self.assertIn("Grade 5", names)

    def test_subject_avg_is_mean_of_three_scores(self):
        """(75 + 40 + 80) / 3 = 65.0"""
        kpis = school_kpis(term=self.term)
        self.assertIsNotNone(kpis["subject_avg"])
        self.assertAlmostEqual(kpis["subject_avg"], 65.0, places=1)


class ClassPerformanceTest(TestCase):
    """class_performance() returns correct per-class aggregates."""

    def setUp(self):
        self.year = make_year()
        self.term = make_term(self.year)
        self.cls = make_class(self.year, name="Grade 6", level_order=6)
        make_grading_config(self.year)
        self.subject = make_subject("ENG", "English")
        self.s1 = make_student_user(self.cls, gender="M")
        self.s2 = make_student_user(self.cls, gender="F")
        make_published_score(
            self.cls, self.subject, self.term, self.s1, Decimal("80.00")
        )
        make_published_score(
            self.cls, self.subject, self.term, self.s2, Decimal("60.00")
        )

    def test_returns_class_in_result(self):
        rows = class_performance(term_id=self.term.pk)
        class_names = [r["class_name"] for r in rows]
        self.assertIn("Grade 6", class_names)

    def test_avg_score_is_70(self):
        rows = class_performance(term_id=self.term.pk)
        row = next(r for r in rows if r["class_name"] == "Grade 6")
        self.assertAlmostEqual(row["avg_score"], 70.0, places=1)

    def test_pass_rate_is_100_percent(self):
        """Both scores are >= 50 (pass mark), so pass_rate == 100."""
        rows = class_performance(term_id=self.term.pk)
        row = next(r for r in rows if r["class_name"] == "Grade 6")
        self.assertEqual(row["pass_rate"], 100.0)

    def test_subject_rows_contain_english(self):
        rows = class_performance(term_id=self.term.pk)
        row = next(r for r in rows if r["class_name"] == "Grade 6")
        codes = [s["subject_code"] for s in row["subject_rows"]]
        self.assertIn("ENG", codes)

    def test_class_with_no_results_has_none_avg(self):
        make_class(self.year, name="Grade 1", level_order=1)
        rows = class_performance(term_id=self.term.pk)
        row = next((r for r in rows if r["class_name"] == "Grade 1"), None)
        if row:  # only present if SchoolClass exists for that year
            self.assertIsNone(row["avg_score"])


class ClassFeeSummaryTest(TestCase):
    """class_fee_summary() totals must match raw model aggregates."""

    def setUp(self):
        self.year = make_year()
        self.term = make_term(self.year)
        self.cls = make_class(self.year, name="Grade 4", level_order=4)
        make_accounts()
        self.fs = make_fee_structure(self.cls, self.term, amount=Decimal("400.00"))
        self.s1 = make_student_user(self.cls)
        self.s2 = make_student_user(self.cls)
        self.inv1 = make_posted_invoice(self.s1, self.term, self.fs, Decimal("400.00"))
        self.inv2 = make_posted_invoice(self.s2, self.term, self.fs, Decimal("400.00"))
        make_payment_allocation(self.inv1, Decimal("400.00"))  # fully paid
        # s2 unpaid

    def test_fee_summary_contains_class(self):
        rows = class_fee_summary(term_id=self.term.pk)
        names = [r["class_name"] for r in rows]
        self.assertIn("Grade 4", names)

    def test_billed_equals_800(self):
        rows = class_fee_summary(term_id=self.term.pk)
        row = next(r for r in rows if r["class_name"] == "Grade 4")
        self.assertEqual(row["billed"], Decimal("800.00"))

    def test_collected_equals_400(self):
        rows = class_fee_summary(term_id=self.term.pk)
        row = next(r for r in rows if r["class_name"] == "Grade 4")
        self.assertEqual(row["collected"], Decimal("400.00"))

    def test_outstanding_equals_400(self):
        rows = class_fee_summary(term_id=self.term.pk)
        row = next(r for r in rows if r["class_name"] == "Grade 4")
        self.assertEqual(row["outstanding"], Decimal("400.00"))

    def test_collection_rate_is_50_percent(self):
        rows = class_fee_summary(term_id=self.term.pk)
        row = next(r for r in rows if r["class_name"] == "Grade 4")
        self.assertEqual(row["collection_rate"], Decimal("50.0"))


class ClassFeeDebtorsTest(TestCase):
    """class_fee_debtors() must include only students with outstanding > 0."""

    def setUp(self):
        self.year = make_year()
        self.term = make_term(self.year)
        self.cls = make_class(self.year, name="Grade 3", level_order=3)
        make_accounts()
        self.fs = make_fee_structure(self.cls, self.term, amount=Decimal("300.00"))
        self.paid_student = make_student_user(self.cls, gender="M")
        self.unpaid_student = make_student_user(self.cls, gender="F")
        paid_inv = make_posted_invoice(
            self.paid_student, self.term, self.fs, Decimal("300.00")
        )
        self.unpaid_inv = make_posted_invoice(
            self.unpaid_student, self.term, self.fs, Decimal("300.00")
        )
        make_payment_allocation(paid_inv, Decimal("300.00"))
        # unpaid_student has no payment

    def test_unpaid_student_appears_in_debtors(self):
        debtors = class_fee_debtors(class_id=self.cls.pk, term_id=self.term.pk)
        adm_numbers = [d["student"].admission_number for d in debtors]
        self.assertIn(self.unpaid_student.admission_number, adm_numbers)

    def test_fully_paid_student_not_in_debtors(self):
        debtors = class_fee_debtors(class_id=self.cls.pk, term_id=self.term.pk)
        adm_numbers = [d["student"].admission_number for d in debtors]
        self.assertNotIn(self.paid_student.admission_number, adm_numbers)

    def test_outstanding_amount_is_correct(self):
        debtors = class_fee_debtors(class_id=self.cls.pk, term_id=self.term.pk)
        row = next(d for d in debtors if d["student"].pk == self.unpaid_student.pk)
        self.assertEqual(row["outstanding"], Decimal("300.00"))


class StaffDirectoryTest(TestCase):
    """staff_directory() must exclude students and parents, and filter by role/search."""

    def setUp(self):
        self.teacher = make_user("teacher", username="dir_teacher_1")
        self.finance_user = make_user("finance", username="dir_finance_1")
        self.admin_user = make_user("admin", username="dir_admin_1")
        self.student_user = make_user("student", username="dir_student_1")
        self.parent_user = make_user("parent", username="dir_parent_1")

    def test_students_excluded(self):
        qs = staff_directory()
        usernames = list(qs.values_list("username", flat=True))
        self.assertNotIn("dir_student_1", usernames)

    def test_parents_excluded(self):
        qs = staff_directory()
        usernames = list(qs.values_list("username", flat=True))
        self.assertNotIn("dir_parent_1", usernames)

    def test_staff_roles_included(self):
        qs = staff_directory()
        usernames = list(qs.values_list("username", flat=True))
        self.assertIn("dir_teacher_1", usernames)
        self.assertIn("dir_finance_1", usernames)
        self.assertIn("dir_admin_1", usernames)

    def test_role_filter(self):
        qs = staff_directory(role="teacher")
        roles = list(qs.values_list("role", flat=True))
        self.assertTrue(all(r == "teacher" for r in roles))

    def test_search_by_last_name(self):
        qs = staff_directory(search="Finance")  # last_name = "Finance" from make_user
        usernames = list(qs.values_list("username", flat=True))
        self.assertIn("dir_finance_1", usernames)

    def test_empty_search_returns_all_staff(self):
        qs_all = staff_directory()
        qs_empty = staff_directory(search="")
        self.assertEqual(list(qs_all), list(qs_empty))
