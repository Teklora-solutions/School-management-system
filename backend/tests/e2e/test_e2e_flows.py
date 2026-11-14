"""
backend/tests/e2e/test_e2e_flows.py
------------------------------------
Cross-module end-to-end scenarios E2E-1 .. E2E-4 and E2E-6.

Where a role UI is missing (teacher result entry, finance screens), the
scenario drives the corresponding service layer directly and the final report
records the missing UI. Everything else goes through the Django test client.
"""

from __future__ import annotations

import datetime
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from apps.academics.models import AcademicYear
from apps.communications.models import Announcement, MessageThread
from apps.communications.services import create_announcement
from apps.core.models import Notification
from apps.dashboards.tests.fixtures import (
    make_accounts,
    make_class,
    make_fee_structure,
    make_grading_config,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.finance import selectors as finance_selectors
from apps.finance import services as finance_services
from apps.finance.models import JournalEntry, JournalLine, Payment
from apps.homework.models import Homework
from apps.results import services as result_services
from apps.results.models import ResultSheet
from apps.students.services import (
    commit_promotion,
    create_student_with_guardian,
    preview_promotion,
)
from apps.teachers.models import TeacherAssignment, TeacherProfile
from django.core.cache import cache
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse


def _make_student(admission: str, school_class, **kwargs):
    return create_student_with_guardian(
        admission_number=admission,
        first_name="E2E",
        last_name=admission,
        email=f"{admission.lower()}@e2e.school",
        current_class=school_class,
        send_credentials=False,
        **kwargs,
    )


class _FakeHTML:
    def __init__(self, string=None, base_url=None):
        self.string = string

    def write_pdf(self):
        return b"%PDF-1.4\nstub\n"


#: The flows below must not depend on this host having WeasyPrint's native
#: libraries, so the engine is stubbed wherever a PDF is expected to succeed.
_engine = patch.dict(sys.modules, {"weasyprint": SimpleNamespace(HTML=_FakeHTML)})


class E2E1AdmissionToResultTests(TestCase):
    """Admin setup -> student -> teacher scores -> publish -> student/parent."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="e2e1_admin")
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Grade 7", level_order=7)
        cls.subject = make_subject("E2E1", "E2E Mathematics")
        make_grading_config(cls.year)

        cls.teacher_user = make_user("teacher", username="e2e1_teacher")
        cls.teacher = TeacherProfile.objects.create(
            user=cls.teacher_user, staff_id="E2E-T1"
        )
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.subject,
            academic_year=cls.year,
        )

        cls.student = _make_student(
            "E2E1-001",
            cls.school_class,
            guardian_first_name="E2E",
            guardian_last_name="Parent",
            guardian_email="e2e1.parent@e2e.school",
        )
        cls.student.user.must_change_password = False
        cls.student.user.save(update_fields=["must_change_password"])
        cls.parent_user = cls.student.guardian_links.first().parent.user
        cls.parent_user.must_change_password = False
        cls.parent_user.save(update_fields=["must_change_password"])

        cls.other_student = _make_student("E2E1-002", cls.school_class)

        cls.sheet = result_services.create_result_sheet(
            school_class=cls.school_class,
            subject=cls.subject,
            term=cls.term,
            teacher=cls.teacher,
            actor=cls.admin,
        )
        result_services.upsert_score_entry(
            sheet=cls.sheet,
            student=cls.student,
            raw_score="80",
            actor=cls.teacher_user,
        )
        result_services.submit_sheet(sheet=cls.sheet, actor=cls.teacher_user)
        result_services.confirm_sheet(sheet=cls.sheet, actor=cls.admin)
        result_services.publish_sheet(sheet=cls.sheet, actor=cls.admin)
        cls.sheet.refresh_from_db()

    def test_full_chain(self):
        self.assertEqual(self.sheet.status, ResultSheet.Status.PUBLISHED)

        self.client.force_login(self.student.user)
        response = self.client.get(reverse("results:student_results"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "E2E Mathematics")
        self.assertContains(response, "80")

        with _engine:
            card = self.client.get(
                reverse("results:report_card_pdf", args=[self.student.pk, self.term.pk])
            )
        self.assertEqual(card.status_code, 200)

        self.client.force_login(self.parent_user)
        with _engine:
            parent_card = self.client.get(
                reverse(
                    "results:report_card_pdf", args=[self.student.pk, self.term.pk]
                )
            )
        self.assertEqual(parent_card.status_code, 200)
        blocked = self.client.get(
            reverse(
                "results:report_card_pdf", args=[self.other_student.pk, self.term.pk]
            )
        )
        self.assertEqual(blocked.status_code, 403)


class E2E2FeeLifecycleTests(TestCase):
    """Fee structure -> invoices -> payment -> statement -> reverse -> balances."""

    @classmethod
    def setUpTestData(cls):
        cls.finance = make_user("finance", username="e2e2_finance")
        cls.director = make_user("director", username="e2e2_director")
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.student = make_student_user(cls.school_class, admission_number="E2E2-001")
        make_accounts()
        cls.structure = make_fee_structure(
            cls.school_class, cls.term, Decimal("500.00")
        )

    def test_fee_lifecycle(self):
        preview = finance_services.preview_invoice_generation(
            term=self.term, school_class=self.school_class
        )
        self.assertEqual(preview["counts"]["create"], 1)

        posted = finance_services.post_invoices(
            term=self.term, school_class=self.school_class, actor=self.finance
        )
        self.assertEqual(len(posted), 1)
        invoice = posted[0]
        journal_count = JournalEntry.objects.count()

        again = finance_services.post_invoices(
            term=self.term, school_class=self.school_class, actor=self.finance
        )
        self.assertEqual(len(again), 1)
        self.assertEqual(invoice.pk, again[0].pk)
        self.assertEqual(JournalEntry.objects.count(), journal_count)

        payment = finance_services.record_payment(
            student=self.student,
            amount=Decimal("200.00"),
            method=Payment.Method.BANK,
            reference="E2E2-PAY-1",
            payment_date=datetime.date.today(),
            recorded_by=self.finance,
        )

        statement = finance_selectors.student_statement(student_id=self.student.pk)
        self.assertEqual(statement["total_billed"], Decimal("500.00"))
        self.assertEqual(statement["total_paid"], Decimal("200.00"))
        self.assertEqual(statement["balance"], Decimal("300.00"))

        self.client.force_login(self.finance)
        with _engine:
            receipt = self.client.get(
                reverse("finance:payment_receipt_pdf", args=[payment.pk])
            )
        self.assertEqual(receipt.status_code, 200)

        # Director cache is warmed, then invalidated by the reversal.
        cache.set(f"director_kpis_term_{self.term.pk}", {"fees_paid": 200})
        finance_services.reverse_payment(
            payment=payment, reason="duplicate entry", actor=self.finance
        )
        self.assertIsNone(cache.get(f"director_kpis_term_{self.term.pk}"))

        self.assertEqual(invoice.amount_outstanding, Decimal("500.00"))
        rows = finance_selectors.trial_balance()
        self.assertEqual(
            sum(r["debit_total"] for r in rows),
            sum(r["credit_total"] for r in rows),
        )


class E2E3PromotionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="e2e3_admin")
        cls.from_year = make_year("2025/2026", is_current=True)
        cls.to_year = AcademicYear.objects.create(
            name="2026/2027",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 7, 31),
        )
        cls.term = make_term(cls.from_year)
        cls.subject = make_subject("E2E3", "E2E Subject")
        cls.from_grade = make_class(cls.from_year, name="Grade 5", level_order=5)
        cls.from_top = make_class(cls.from_year, name="Grade 6", level_order=6)
        cls.to_grade = make_class(cls.to_year, name="Grade 6", level_order=6)
        cls.to_top = make_class(cls.to_year, name="Grade 7", level_order=7)
        cls.student = make_student_user(cls.from_grade, admission_number="E2E3-001")
        for school_class in (cls.from_grade, cls.from_top):
            ResultSheet.objects.create(
                school_class=school_class,
                subject=cls.subject,
                term=cls.term,
                status=ResultSheet.Status.PUBLISHED,
            )

    def test_promotion_is_previewed_committed_and_idempotent(self):
        preview = preview_promotion(
            from_year=self.from_year, to_year=self.to_year, term=self.term
        )
        self.assertEqual(preview["counts"], {"promote": 1, "repeat": 0, "graduate": 0})
        self.assertTrue(preview["results_published"])

        batch = commit_promotion(
            from_year=self.from_year,
            to_year=self.to_year,
            term=self.term,
            actor=self.admin,
        )
        self.student.refresh_from_db()
        self.assertEqual(self.student.current_class, self.to_grade)

        second = commit_promotion(
            from_year=self.from_year,
            to_year=self.to_year,
            term=self.term,
            actor=self.admin,
        )
        self.assertEqual(batch.pk, second.pk)

        self.client.force_login(self.admin)
        history = self.client.get(reverse("students:promotion_history"))
        self.assertEqual(history.status_code, 200)
        self.assertContains(history, "2025/2026")


class E2E4CommunicationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="e2e4_admin")
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.other_class = make_class(cls.year, name="Grade 10", level_order=10)
        cls.subject = make_subject("E2E4", "E2E Subject")
        cls.teacher = make_user("teacher", username="e2e4_teacher")
        cls.student = make_student_user(cls.school_class, admission_number="E2E4-001")
        cls.other_student = make_student_user(
            cls.other_class, admission_number="E2E4-002"
        )
        cls.announcement = create_announcement(
            title="E2E4 class announcement",
            body="Visible only to Grade 9.",
            audience=Announcement.Audience.BY_CLASS,
            target_class_ids=[cls.school_class.pk],
            created_by=cls.admin,
        )
        cls.homework = Homework.objects.create(
            title="E2E4 homework",
            subject=cls.subject,
            school_class=cls.school_class,
            term=cls.term,
            set_by=cls.teacher,
            due_date=datetime.date.today() + datetime.timedelta(days=3),
            status=Homework.Status.PUBLISHED,
        )

    def test_communication_loop(self):
        self.client.force_login(self.student.user)
        announcements = self.client.get(reverse("communications:list"))
        self.assertContains(announcements, "E2E4 class announcement")
        homework = self.client.get(reverse("homework:list"))
        self.assertContains(homework, "E2E4 homework")

        self.client.force_login(self.other_student.user)
        other_announcements = self.client.get(reverse("communications:list"))
        self.assertNotContains(other_announcements, "E2E4 class announcement")
        other_homework = self.client.get(reverse("homework:list"))
        self.assertNotContains(other_homework, "E2E4 homework")

        # Student opens a complaint through the UI.
        self.client.force_login(self.student.user)
        response = self.client.post(
            reverse("communications:complaint_create"),
            {
                "message_type": MessageThread.MessageType.COMPLAINT,
                "subject": "E2E4 complaint",
                "body": "Please fix the lab computers.",
            },
        )
        self.assertEqual(response.status_code, 302)
        thread = MessageThread.objects.get(subject="E2E4 complaint")
        self.assertEqual(thread.status, MessageThread.Status.OPEN)

        # Admin replies and resolves; sender gets a notification.
        self.client.force_login(self.admin)
        reply = self.client.post(
            reverse("communications:thread_detail", args=[thread.pk]),
            {"body": "We are on it.", "new_status": MessageThread.Status.RESOLVED},
        )
        self.assertEqual(reply.status_code, 302)
        thread.refresh_from_db()
        self.assertEqual(thread.status, MessageThread.Status.RESOLVED)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.student.user, category="message"
            ).exists()
        )

        self.client.force_login(self.student.user)
        detail = self.client.get(
            reverse("communications:thread_detail", args=[thread.pk])
        )
        self.assertContains(detail, "We are on it.")
        self.assertContains(detail, "Resolved")

        # Another student cannot open the thread.
        self.client.force_login(self.other_student.user)
        denied = self.client.get(
            reverse("communications:thread_detail", args=[thread.pk])
        )
        self.assertEqual(denied.status_code, 403)


class E2E6FailureBehaviourTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("admin", username="e2e6_admin")
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.subject = make_subject("E2E6", "E2E Subject")
        make_grading_config(cls.year)
        make_accounts()
        cls.student = make_student_user(cls.school_class, admission_number="E2E6-001")
        cls.sheet = result_services.create_result_sheet(
            school_class=cls.school_class,
            subject=cls.subject,
            term=cls.term,
            actor=cls.admin,
        )
        cls.structure = make_fee_structure(
            cls.school_class, cls.term, Decimal("100.00")
        )
        finance_services.post_invoices(
            term=cls.term, school_class=cls.school_class, actor=cls.admin
        )

    def test_core_flows_survive_notification_and_pdf_failures(self):
        with patch(
            "apps.core.models.Notification.objects.bulk_create",
            side_effect=RuntimeError("notification backend down"),
        ), self.assertLogs("apps.communications.services", "ERROR"):
            ann = create_announcement(
                title="E2E6 announcement",
                body="Still created.",
                audience=Announcement.Audience.EVERYONE,
                created_by=self.admin,
            )
        self.assertTrue(Announcement.objects.filter(pk=ann.pk).exists())

        # Result entry/submission still succeeds.
        result_services.upsert_score_entry(
            sheet=self.sheet,
            student=self.student,
            raw_score="75",
            actor=self.admin,
        )
        result_services.submit_sheet(sheet=self.sheet, actor=self.admin)
        result_services.confirm_sheet(sheet=self.sheet, actor=self.admin)
        result_services.publish_sheet(sheet=self.sheet, actor=self.admin)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, ResultSheet.Status.PUBLISHED)

        # Payment posting still succeeds.
        payment = finance_services.record_payment(
            student=self.student,
            amount=Decimal("25.00"),
            method=Payment.Method.BANK,
            reference="E2E6-PAY-1",
            payment_date=datetime.date.today(),
            recorded_by=self.admin,
        )
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())

        # When neither PDF engine can make a document, the failure is logged
        # and reported as a 503 — never disguised as printable HTML. Inject a
        # fake module so this never imports the real WeasyPrint native
        # libraries, and disable the Chromium fallback.
        def _broken_html(**_kwargs):
            raise RuntimeError("PDF engine down")

        self.client.force_login(self.student.user)
        with patch.dict(
            sys.modules, {"weasyprint": SimpleNamespace(HTML=_broken_html)}
        ), patch("apps.core.pdf._find_browser", return_value=None), self.assertLogs(
            "apps.core.pdf", "ERROR"
        ):
            response = self.client.get(
                reverse(
                    "results:report_card_pdf",
                    args=[self.student.pk, self.term.pk],
                )
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("text/plain", response["Content-Type"])
        self.assertNotIn("text/html", response["Content-Type"])

        # Ledger still balances after all of the above.
        self.assertEqual(
            JournalLine.objects.aggregate(total=Sum("debit_amount"))["total"],
            JournalLine.objects.aggregate(total=Sum("credit_amount"))["total"],
        )
