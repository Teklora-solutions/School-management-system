"""
apps/core/tests/test_events.py
--------------------------------
Module 3 S0a — the activity registry and ``record_event``.

Covers the three promises the events layer makes:
  1. a registered event renders into a readable sentence,
  2. an unregistered or half-filled event still records (never raises),
  3. the JSON log sink keeps receiving every event it used to.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.db import OperationalError, transaction
from django.test import TestCase

from apps.dashboards.tests.fixtures import (
    make_accounts,
    make_class,
    make_fee_structure,
    make_posted_invoice,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.finance import services as finance_services
from apps.finance.models import Payment
from apps.results import services as results_services
from apps.results.models import ResultSheet

from ..events import CATEGORIES, EVENT_TYPES, record_event, render_message
from ..models import SchoolSettings, SystemEvent


class EventRegistryTests(TestCase):
    def test_every_registered_type_has_wording_and_a_known_category(self):
        for event_type, spec in EVENT_TYPES.items():
            self.assertTrue(spec["label"], msg=f"{event_type} has no label")
            self.assertIn(
                spec["category"],
                CATEGORIES,
                msg=f"{event_type} uses a category outside CATEGORIES",
            )

    def test_a_registered_event_renders_its_sentence(self):
        self.assertEqual(
            render_message(
                "finance.invoices_posted",
                {"count": 3, "term": "Term 1", "class": "Grade 5"},
            ),
            "3 invoice(s) posted for Term 1 (Grade 5)",
        )

    def test_a_missing_value_renders_a_dash_instead_of_raising(self):
        self.assertEqual(
            render_message("finance.payment_posted", {"amount": "50.00"}),
            "Payment —: 50.00 from —",
        )

    def test_an_unregistered_type_falls_back_to_its_own_name(self):
        event = record_event(event_type="library.book_issued", data={"isbn": "x"})
        assert event is not None
        self.assertEqual(event.category, "other")
        self.assertEqual(event.message, "library.book_issued")

    def test_an_overlong_message_is_clipped_to_the_column(self):
        event = record_event(
            event_type="finance.payment_posted",
            message="Receipt " + "0" * 400,
            data={},
        )
        assert event is not None
        self.assertEqual(len(event.message), 255)


class RecordEventTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.admin = make_user("admin", username="evt_admin")
        cls.student = make_student_user(cls.school_class, admission_number="EVT-001")

    def test_actor_and_student_are_linked(self):
        event = record_event(
            event_type="finance.payment_posted",
            actor=self.admin,
            student=self.student,
            data={"amount": "50.00", "student_admission": "EVT-001"},
        )
        assert event is not None
        self.assertEqual(event.actor_id, self.admin.pk)
        self.assertEqual(event.student_id, self.student.pk)
        self.assertIn("EVT-001", event.message)

    def test_request_id_is_taken_from_the_request(self):
        event = record_event(
            event_type="finance.payment_posted",
            data={},
            request=SimpleNamespace(request_id="req-42"),
        )
        assert event is not None
        self.assertEqual(event.request_id, "req-42")

    def test_the_json_sink_still_receives_the_event(self):
        with self.assertLogs("apps.events", level="INFO") as captured:
            record_event(
                event_type="finance.payment_posted",
                actor=self.admin,
                data={"amount": "50.00"},
                request=SimpleNamespace(request_id="req-7"),
            )
        record = captured.records[0]
        self.assertEqual(record.event, "payment_posted")
        self.assertEqual(record.actor_id, self.admin.pk)
        self.assertEqual(record.request_id, "req-7")
        self.assertEqual(record.data, {"amount": "50.00"})

    def test_the_sink_name_is_not_renamed_by_the_dotted_type(self):
        """
        ``results.published`` must still reach the sink as ``results_published``
        — the name the log has always used.
        """
        with self.assertLogs("apps.events", level="INFO") as captured:
            record_event(event_type="results.published", data={})
        self.assertEqual(captured.records[0].event, "results_published")

    def test_a_failed_event_row_never_breaks_the_business_write(self):
        """
        Every caller sits inside ``@transaction.atomic``. If the feed row cannot
        be stored, the surrounding write still commits and the caller gets None.
        """
        with transaction.atomic():
            kept = SchoolSettings.objects.create(name="Keeper")
            with (
                mock.patch.object(
                    SystemEvent,
                    "save",
                    side_effect=OperationalError("database is locked"),
                ),
                self.assertLogs("apps.core.events", level="WARNING"),
            ):
                result = record_event(event_type="finance.payment_posted", data={})
        self.assertIsNone(result)
        self.assertTrue(SchoolSettings.objects.filter(pk=kept.pk).exists())


class EventCallSiteTests(TestCase):
    """
    The four services that already emitted a JSON event now go through
    ``record_event`` — one row per business action, no double logging.
    """

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.admin = make_user("admin", username="evt_cs_admin")
        cls.student = make_student_user(cls.school_class, admission_number="EVT-CS-1")
        cls.accounts = make_accounts()
        cls.structure = make_fee_structure(
            cls.school_class, cls.term, Decimal("500.00")
        )

    def _one(self, event_type: str) -> SystemEvent:
        events = list(SystemEvent.objects.filter(type=event_type))
        self.assertEqual(len(events), 1, msg=f"expected one {event_type} row")
        return events[0]

    def test_posting_invoices_records_one_event_for_the_run(self):
        posted = finance_services.post_invoices(
            term=self.term, school_class=self.school_class, actor=self.admin
        )
        event = self._one("finance.invoices_posted")
        self.assertEqual(event.data["count"], len(posted))
        self.assertEqual(len(posted), 1)
        self.assertIn("1 invoice(s)", event.message)
        self.assertIsNone(event.student_id)

    def test_recording_a_payment_links_the_event_to_the_student(self):
        make_posted_invoice(
            self.student, self.term, self.structure, Decimal("500.00")
        )
        payment = finance_services.record_payment(
            student=self.student,
            amount=Decimal("200.00"),
            method=Payment.Method.BANK,
            reference="EVT-PAY-1",
            payment_date=datetime.date(2025, 9, 10),
            recorded_by=self.admin,
        )
        event = self._one("finance.payment_posted")
        self.assertEqual(event.student_id, self.student.pk)
        self.assertEqual(event.data["amount"], "200.00")
        self.assertIn(payment.receipt_number, event.message)

    def test_reversing_a_payment_records_a_reversal(self):
        make_posted_invoice(self.student, self.term, self.structure, Decimal("500.00"))
        payment = finance_services.record_payment(
            student=self.student,
            amount=Decimal("200.00"),
            method=Payment.Method.BANK,
            reference="EVT-PAY-2",
            payment_date=datetime.date(2025, 9, 10),
            recorded_by=self.admin,
        )
        finance_services.reverse_payment(
            payment=payment, reason="Wrong account", actor=self.admin
        )
        event = self._one("finance.payment_reversed")
        self.assertEqual(event.student_id, self.student.pk)
        self.assertIn(payment.receipt_number, event.message)

    def test_publishing_a_sheet_records_one_event_per_sheet(self):
        sheet = ResultSheet.objects.create(
            school_class=self.school_class,
            subject=make_subject(),
            term=self.term,
            status=ResultSheet.Status.CONFIRMED,
        )
        results_services.publish_sheet(sheet=sheet, actor=self.admin)
        event = self._one("results.published")
        self.assertEqual(event.category, "results")
        self.assertIn(sheet.subject.name, event.message)
