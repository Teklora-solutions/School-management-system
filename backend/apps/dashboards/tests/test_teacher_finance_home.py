"""C2 — the teacher and finance dashboards show their own portal's numbers.

Until this checkpoint both pages were shells: four `–` cards and a "Phase 5"
placeholder for announcements. These tests pin the numbers to the data and make
sure a regression back to placeholder values fails loudly.
"""

from django.test import TestCase
from django.urls import reverse

from apps.results import services as results_services
from apps.results.models import ResultSheet
from apps.teachers.models import TeacherAssignment, TeacherProfile

from .fixtures import (
    make_accounts,
    make_class,
    make_fee_structure,
    make_payment_allocation,
    make_posted_invoice,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)


class TeacherHomeSummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.maths = make_subject()
        cls.assigned = make_class(cls.year, name="Grade 5", level_order=5)
        cls.other = make_class(cls.year, name="Grade 6", level_order=6)

        cls.teacher = make_user("teacher", username="dash_teacher")
        cls.profile = TeacherProfile.objects.create(
            user=cls.teacher, staff_id="T-DASH"
        )
        TeacherAssignment.objects.create(
            teacher=cls.profile,
            school_class=cls.assigned,
            subject=cls.maths,
            academic_year=cls.year,
        )

        # Two active students in the assigned class, one in a class they do not
        # teach: the dashboard must count only the first pair.
        make_student_user(cls.assigned, admission_number="DASH-1")
        make_student_user(cls.assigned, admission_number="DASH-2")
        make_student_user(cls.other, admission_number="DASH-3")

        cls.exams = results_services.ensure_exams_for_term(cls.term)
        ResultSheet.objects.create(
            school_class=cls.assigned,
            subject=cls.maths,
            term=cls.term,
            exam=cls.exams[0],
            teacher=cls.profile,
            status=ResultSheet.Status.DRAFT,
        )
        ResultSheet.objects.create(
            school_class=cls.assigned,
            subject=cls.maths,
            term=cls.term,
            exam=cls.exams[1],
            teacher=cls.profile,
            status=ResultSheet.Status.SUBMITTED,
        )

    def test_the_numbers_are_the_teachers_own(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("dashboards:teacher_home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["class_count"], 1)
        self.assertEqual(response.context["student_count"], 2)
        self.assertEqual(response.context["draft_sheet_count"], 1)
        self.assertEqual(response.context["submitted_sheet_count"], 1)
        self.assertEqual(response.context["approved_sheet_count"], 0)

        self.assertContains(response, "My Classes")
        self.assertContains(response, "Students in My Classes")
        self.assertContains(response, "Marks Sheets in Draft")
        self.assertContains(response, "Awaiting Admin")

    def test_the_page_no_longer_carries_placeholders(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("dashboards:teacher_home"))

        self.assertNotContains(response, "Phase 5")
        self.assertNotContains(response, 'stat-value">–')

    def test_a_submitted_sheet_is_announced_as_locked(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("dashboards:teacher_home"))

        self.assertContains(response, "submitted and locked")

    def test_approved_sheets_are_counted_separately(self):
        sheet = ResultSheet.objects.get(term=self.term, exam=self.exams[0])
        sheet.status = ResultSheet.Status.CONFIRMED
        sheet.save(update_fields=["status"])

        self.client.force_login(self.teacher)
        response = self.client.get(reverse("dashboards:teacher_home"))

        self.assertEqual(response.context["approved_sheet_count"], 1)
        self.assertEqual(response.context["draft_sheet_count"], 0)
        self.assertContains(response, "already approved")

    def test_the_marks_list_shows_which_sheets_are_locked(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("teachers:marks_list"))

        self.assertContains(response, "1 draft")
        self.assertContains(response, "1 submitted")

    def test_a_teacher_without_assignments_sees_zeroes(self):
        other = make_user("teacher", username="dash_unassigned")
        TeacherProfile.objects.create(user=other, staff_id="T-NONE")

        self.client.force_login(other)
        response = self.client.get(reverse("dashboards:teacher_home"))

        self.assertEqual(response.context["class_count"], 0)
        self.assertEqual(response.context["student_count"], 0)
        self.assertEqual(response.context["draft_sheet_count"], 0)


class FinanceHomeKpiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        make_accounts()
        cls.finance = make_user("finance", username="dash_finance")

    def test_the_cards_read_the_term_kpis(self):
        self.client.force_login(self.finance)
        response = self.client.get(reverse("dashboards:finance_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Collected Today")
        self.assertContains(response, "Collected This Term")
        self.assertContains(response, "Outstanding on Posted Invoices")
        self.assertContains(response, "Collection Rate")
        self.assertContains(response, "No payments recorded this term yet.")
        self.assertNotContains(response, "Phase 5")
        self.assertNotContains(response, 'stat-value">–')

    def test_the_kpis_match_the_selector(self):
        from apps.finance.selectors import dashboard_kpis

        self.client.force_login(self.finance)
        response = self.client.get(reverse("dashboards:finance_home"))
        expected = dashboard_kpis(term=self.term)

        self.assertEqual(response.context["collected_today"], expected["collected_today"])
        self.assertEqual(
            response.context["outstanding_total"], expected["outstanding_total"]
        )
        self.assertEqual(
            list(response.context["recent_payments"]),
            list(expected["recent_payments"]),
        )

    def test_a_recent_payment_is_listed_with_its_student_and_receipt(self):
        from decimal import Decimal

        school_class = make_class(self.year)
        student = make_student_user(school_class, admission_number="DASH-PAY")
        structure = make_fee_structure(school_class, self.term)
        invoice = make_posted_invoice(student, self.term, structure)
        payment = make_payment_allocation(invoice, Decimal("250.00")).payment

        self.client.force_login(self.finance)
        response = self.client.get(reverse("dashboards:finance_home"))

        self.assertContains(response, payment.receipt_number)
        self.assertContains(response, "DASH-PAY")
        self.assertContains(response, "250.00")
        self.assertNotContains(response, "No payments recorded this term yet.")
