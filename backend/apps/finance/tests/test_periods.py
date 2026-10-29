"""
apps/finance/tests/test_periods.py
------------------------------------
Period control: who may close the books, what a closed period refuses, and how
far back a correction may be dated.

The lock is worth nothing if the person who wants to post into last month can
do it quietly. These tests pin the three things that make it a control rather
than a preference: it is enforced in the one function every posting passes
through, an Admin is not exempt from it, and the only way in is a reopen, which
names the person who did it and why.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.core.models import AuditLog, SystemEvent
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

from .. import services
from ..models import (
    FinanceSettings,
    FinancialPeriod,
    JournalEntry,
    Payment,
)

D = Decimal
TODAY = timezone.localdate()


def month_of(day) -> FinancialPeriod:
    return services.month_period(day)


class PeriodRowTests(TestCase):
    def test_a_month_gets_one_row_however_often_it_is_asked_for(self):
        first = month_of(datetime.date(2026, 3, 4))
        second = month_of(datetime.date(2026, 3, 29))
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            FinancialPeriod.objects.filter(
                kind=FinancialPeriod.Kind.MONTH, year=2026, month=3
            ).count(),
            1,
        )

    def test_the_window_is_the_whole_calendar_month(self):
        period = month_of(datetime.date(2026, 2, 9))
        self.assertEqual(period.start_date, datetime.date(2026, 2, 1))
        self.assertEqual(period.end_date, datetime.date(2026, 2, 28))
        self.assertTrue(period.contains(datetime.date(2026, 2, 28)))
        self.assertFalse(period.contains(datetime.date(2026, 3, 1)))

    def test_an_open_period_is_the_default_and_nothing_is_locked_yet(self):
        period = month_of(TODAY)
        self.assertEqual(period.status, FinancialPeriod.Status.OPEN)
        self.assertIsNone(services.closed_period_covering(TODAY))

    def test_february_of_a_leap_year_ends_on_the_29th(self):
        self.assertEqual(
            month_of(datetime.date(2028, 2, 10)).end_date, datetime.date(2028, 2, 29)
        )


class CloseAndReopenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.finance = make_user("finance", username="period_finance")
        cls.admin = make_user("admin", username="period_admin")
        cls.director = make_user("director", username="period_director")
        cls.teacher = make_user("teacher", username="period_teacher")
        cls.year = make_year()
        cls.term_rows = 0

    def close_month(self, actor):
        period = month_of(TODAY)
        return services.close_period(period=period, actor=actor, note="agreed with bank")

    def test_finance_can_close_its_own_month(self):
        period = self.close_month(self.finance)
        self.assertEqual(period.status, FinancialPeriod.Status.CLOSED)
        self.assertEqual(period.closed_by, self.finance)
        self.assertTrue(period.close_note)
        self.assertTrue(
            AuditLog.objects.filter(
                action="finance.period_closed", actor=self.finance
            ).exists()
        )
        self.assertTrue(SystemEvent.objects.filter(type="finance.period_closed").exists())

    def test_closing_a_second_time_refuses_rather_than_re_stamping_the_record(self):
        self.close_month(self.finance)
        with self.assertRaises(ValidationError):
            self.close_month(self.finance)

    def test_a_month_can_be_closed_without_being_a_term_decision(self):
        """
        The role test is on the *kind*, so the bursar's monthly books are not
        held up waiting for an Admin.
        """
        self.assertEqual(month_of(TODAY).kind, FinancialPeriod.Kind.MONTH)
        self.close_month(self.finance)
        self.assertEqual(services.closed_period_covering(TODAY).pk, month_of(TODAY).pk)

    TERM_NAMES = ("term_1", "term_2", "term_3")

    def term_period(self):
        """
        A fresh term each call, because ``FinancialPeriod`` allows one row per
        term and the roles test closes three of them.
        """
        name = self.TERM_NAMES[self.term_rows % len(self.TERM_NAMES)]
        self.term_rows += 1
        term = make_term(self.year, name=name, is_current=False)
        return FinancialPeriod.objects.create(
            kind=FinancialPeriod.Kind.TERM,
            term=term,
            start_date=term.start_date,
            end_date=term.end_date,
        )

    def test_only_admin_or_director_can_close_a_term(self):
        with self.assertRaises(ValidationError):
            services.close_period(period=self.term_period(), actor=self.finance)
        for actor in (self.admin, self.director):
            period = self.term_period()
            services.close_period(period=period, actor=actor)
            self.assertEqual(period.status, FinancialPeriod.Status.CLOSED)

    def test_a_teacher_cannot_close_anything(self):
        with self.assertRaises(ValidationError):
            self.close_month(self.teacher)

    def test_reopening_is_admin_only_and_needs_a_reason(self):
        period = self.close_month(self.finance)
        with self.assertRaises(ValidationError):
            services.reopen_period(period=period, actor=self.finance, reason="oops")
        with self.assertRaises(ValidationError):
            services.reopen_period(period=period, actor=self.admin, reason="   ")
        reopened = services.reopen_period(
            period=period, actor=self.admin, reason="Bank line 4 missed"
        )
        self.assertEqual(reopened.status, FinancialPeriod.Status.OPEN)
        self.assertEqual(reopened.reopened_by, self.admin)
        self.assertEqual(reopened.reopen_reason, "Bank line 4 missed")
        self.assertIsNone(services.closed_period_covering(TODAY))
        self.assertTrue(SystemEvent.objects.filter(type="finance.period_reopened").exists())


class PostingIntoAClosedPeriodTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.admin = make_user("admin", username="lock_admin")
        cls.finance = make_user("finance", username="lock_finance")
        cls.student = make_student_user(cls.school_class, admission_number="LCK-001")
        cls.accounts = make_accounts()
        cls.structure = make_fee_structure(
            cls.school_class, cls.term, Decimal("500.00")
        )
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, Decimal("500.00")
        )
        # These tests are about the lock, so the back-dating window is widened
        # out of the way. BackdatingWindowTests covers it on its own.
        services.update_finance_settings(
            actor=cls.admin, backdating_days=5000, request=None
        )

    PAYMENT_DATE = datetime.date(2025, 9, 10)

    def close_the_payment_month(self):
        return services.close_period(
            period=month_of(self.PAYMENT_DATE),
            actor=self.finance,
            note="September agreed",
            # Nothing in the school's calendar declares September, so the
            # back-dating window stays switched off and this test is about the
            # lock alone. See BackdatingWindowTests for the window.
        )

    def record(self, reference):
        return services.record_payment(
            student=self.student,
            amount=Decimal("100.00"),
            method=Payment.Method.BANK,
            reference=reference,
            payment_date=self.PAYMENT_DATE,
            recorded_by=self.finance,
        )

    def test_a_receipt_into_a_closed_month_is_refused(self):
        self.close_the_payment_month()
        with self.assertRaises(ValidationError) as caught:
            self.record("LOCK-1")
        message = str(caught.exception)
        self.assertIn("closed", message)
        self.assertIn("2025-09-01", message)

    def test_the_refusal_leaves_no_trace_of_a_payment_behind(self):
        self.close_the_payment_month()
        entries = JournalEntry.objects.count()
        with self.assertRaises(ValidationError):
            self.record("LOCK-2")
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(JournalEntry.objects.count(), entries)

    def test_an_admin_is_not_exempt(self):
        self.close_the_payment_month()
        with self.assertRaises(ValidationError):
            services.record_payment(
                student=self.student,
                amount=Decimal("100.00"),
                method=Payment.Method.BANK,
                reference="LOCK-3",
                payment_date=self.PAYMENT_DATE,
                recorded_by=self.admin,
            )

    def test_reopening_lets_the_correction_through(self):
        period = self.close_the_payment_month()
        with self.assertRaises(ValidationError):
            self.record("LOCK-4")
        services.reopen_period(
            period=period, actor=self.admin, reason="A cheque was missed"
        )
        self.assertTrue(self.record("LOCK-4").pk)

    def test_the_lock_can_be_switched_off_for_a_school_that_does_not_use_it(self):
        self.close_the_payment_month()
        rules = FinanceSettings.load()
        rules.period_lock_enabled = False
        rules.save()
        self.assertTrue(self.record("LOCK-5").pk)

    def test_a_closed_month_inside_an_open_term_still_refuses_the_posting(self):
        """
        The tighter lock wins, and it is not invisible.

        Checking only term periods would let an October receipt into a month
        Finance had already agreed with the bank, because the term around it is
        still open.
        """
        FinancialPeriod.objects.create(
            kind=FinancialPeriod.Kind.TERM,
            term=self.term,
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 20),
        )
        services.close_period(
            period=month_of(datetime.date(2025, 10, 15)),
            actor=self.finance,
            note="agreed",
        )
        with self.assertRaises(ValidationError):
            services.ensure_postable(datetime.date(2025, 10, 20))
        services.ensure_postable(datetime.date(2025, 11, 20))


class BackdatingWindowTests(TestCase):
    """
    How far back a posting may be dated — and only once the school has a calendar.

    A school that has never declared a period has made no promise about which
    windows its books cover. Refusing every older date at that point would fail
    a bursar's ordinary first entry for reasons no screen explains, so the
    window switches on with the calendar.
    """

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        cls.accounts = make_accounts()
        cls.admin = make_user("admin", username="back_admin")
        cls.finance = make_user("finance", username="back_finance")
        cls.student = make_student_user(cls.school_class, admission_number="BCK-001")
        cls.structure = make_fee_structure(
            cls.school_class, cls.term, Decimal("500.00")
        )
        cls.invoice = make_posted_invoice(
            cls.student, cls.term, cls.structure, Decimal("500.00")
        )

    def record(self, reference, on):
        return services.record_payment(
            student=self.student,
            amount=Decimal("40.00"),
            method=Payment.Method.BANK,
            reference=reference,
            payment_date=on,
            recorded_by=self.finance,
        )

    def test_without_a_calendar_an_old_date_is_still_a_normal_receipt(self):
        self.assertTrue(self.record("BACK-1", TODAY - datetime.timedelta(days=400)).pk)

    def test_once_a_period_exists_the_window_applies(self):
        month_of(TODAY)
        with self.assertRaises(ValidationError) as caught:
            self.record("BACK-2", TODAY - datetime.timedelta(days=400))
        self.assertIn("back-dating window", str(caught.exception))

    def test_inside_the_window_is_allowed(self):
        month_of(TODAY)
        self.assertTrue(self.record("BACK-3", TODAY - datetime.timedelta(days=3)).pk)

    def test_an_admin_can_widen_the_window_and_the_change_is_on_record(self):
        month_of(TODAY)
        services.update_finance_settings(
            actor=self.admin, backdating_days=45, request=None
        )
        self.assertTrue(self.record("BACK-4", TODAY - datetime.timedelta(days=40)).pk)
        self.assertTrue(
            AuditLog.objects.filter(action="finance.settings_updated").exists()
        )

    def test_finance_cannot_widen_it(self):
        with self.assertRaises(ValidationError):
            services.update_finance_settings(actor=self.finance, backdating_days=90)
        self.assertEqual(FinanceSettings.load().backdating_days, 7)
