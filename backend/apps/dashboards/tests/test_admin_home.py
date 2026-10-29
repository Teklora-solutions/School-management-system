"""
apps/dashboards/tests/test_admin_home.py
------------------------------------------
Module 1 — admin dashboard home cards.

Covers:
  - totals for students, teachers, and current-year classes
  - results awaiting confirmation
  - recent announcements and recent audit events
  - the fee KPI cards rendered from ``finance.selectors.dashboard_kpis``
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.accounts.models import User
from apps.communications.models import Announcement
from apps.communications.services import create_announcement
from apps.core.audit_log import log_action
from apps.dashboards.tests.fixtures import (
    make_fee_structure,
    make_payment_allocation,
    make_posted_invoice,
)
from apps.results.models import ResultSheet
from apps.students.models import Student


class AdminHomeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="home_admin",
            email="home_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.other_year = AcademicYear.objects.create(
            name="2024/2025",
            start_date=datetime.date(2024, 9, 1),
            end_date=datetime.date(2025, 7, 31),
        )
        cls.term = Term.objects.create(
            academic_year=cls.year,
            name="term_1",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 20),
            is_current=True,
        )
        cls.school_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        SchoolClass.objects.create(
            academic_year=cls.other_year, name="Grade 4", level_order=4
        )

        User.objects.create_user(
            username="home_teacher",
            email="home_teacher@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )

        cls.students = []
        for index in range(3):
            user = User.objects.create_user(
                username=f"home_student_{index}",
                email=f"home_student_{index}@test.school",
                password="Testpass1!",
                role=User.Role.STUDENT,
                must_change_password=False,
            )
            cls.students.append(
                Student.objects.create(
                    user=user,
                    admission_number=f"HOME-{index}",
                    current_class=cls.school_class,
                    status=Student.Status.ACTIVE,
                )
            )

        cls.sheet = ResultSheet.objects.create(
            school_class=cls.school_class,
            subject=Subject.objects.create(name="Mathematics", code="MATH"),
            term=cls.term,
            status=ResultSheet.Status.SUBMITTED,
        )
        cls.announcement = create_announcement(
            title="Home announcement",
            body="Visible to all.",
            audience=Announcement.Audience.EVERYONE,
            created_by=cls.admin,
        )
        log_action(
            actor=cls.admin,
            action="home.test_event",
            content_type="core.AuditLog",
            object_id="1",
        )

        # Fee position for the current term: 500.00 billed, 200.00 paid.
        cls.structure = make_fee_structure(
            cls.school_class, cls.term, Decimal("500.00")
        )
        cls.invoice = make_posted_invoice(
            cls.students[0], cls.term, cls.structure, Decimal("500.00")
        )
        make_payment_allocation(cls.invoice, Decimal("200.00"))

    def test_dashboard_cards_are_populated(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboards:admin_home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["student_count"], 3)
        self.assertEqual(response.context["teacher_count"], 1)
        # Only classes in the current academic year are counted.
        self.assertEqual(response.context["class_count"], 1)
        self.assertEqual(response.context["results_awaiting_confirmation"], 1)
        self.assertIn(self.announcement, response.context["recent_announcements"])
        actions = [entry.action for entry in response.context["recent_audit_events"]]
        self.assertIn("home.test_event", actions)

    def test_fee_kpis_are_in_context(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboards:admin_home"))

        self.assertEqual(response.context["fees_collected"], Decimal("200.00"))
        self.assertEqual(response.context["fees_outstanding"], Decimal("300.00"))
        self.assertEqual(response.context["currency"], settings.SCHOOL_CURRENCY)

    def test_fee_kpi_cards_render(self):
        """The KPIs are only useful if the home page actually shows them."""
        self.client.force_login(self.admin)
        html = self.client.get(reverse("dashboards:admin_home")).content.decode()

        currency = settings.SCHOOL_CURRENCY
        self.assertIn(f"{currency} 200.00", html)
        self.assertIn(f"{currency} 300.00", html)
        self.assertIn("Collected this term", html)
        self.assertIn("Outstanding on posted invoices", html)

    def test_no_current_term_shows_guidance_instead_of_fee_cards(self):
        Term.objects.filter(pk=self.term.pk).update(is_current=False)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboards:admin_home"))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["current_term"])
        self.assertNotIn("Collected this term", html)
        self.assertIn("No current term is set", html)
