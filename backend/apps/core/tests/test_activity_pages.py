"""
apps/core/tests/test_activity_pages.py
----------------------------------------
Module 3 S0a — the admin live feed and daily report pages.

Covers rendering, filtering, day grouping, the truncation note, and the query
shape of the two selectors (no N+1 on actor/student).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_year,
)

from .. import selectors
from ..events import record_event
from ..models import SystemEvent

FEED_URL = "core_activity:feed"
DAILY_URL = "core_activity:daily_report"


def _on_day(
    event: SystemEvent, year: int, month: int, day: int, minute: int = 0
) -> SystemEvent:
    """Stamp an event onto a calendar day at 12:00 local time."""
    moment = timezone.make_aware(
        datetime.datetime(year, month, day, 12, minute),
        timezone.get_current_timezone(),
    )
    SystemEvent.objects.filter(pk=event.pk).update(created_at=moment)
    event.created_at = moment
    return event


class ActivityFeedPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="feed_admin",
            email="feed_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.teacher = User.objects.create_user(
            username="feed_teacher",
            email="feed_teacher@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        year = make_year()
        school_class = make_class(year)
        cls.student = make_student_user(school_class, admission_number="FEED-001")

        cls.payment = record_event(
            event_type="finance.payment_posted",
            actor=cls.admin,
            student=cls.student,
            data={"amount": "50.00", "student_admission": "FEED-001"},
        )
        cls.invoices = _on_day(
            record_event(
                event_type="finance.invoices_posted",
                actor=cls.admin,
                data={"count": 12, "term": "Term 1", "class": "Grade 5"},
            ),
            2025,
            3,
            3,
        )
        cls.publish = _on_day(
            record_event(
                event_type="results.published",
                actor=cls.admin,
                data={"subject": "Mathematics", "class": "Grade 5"},
            ),
            2025,
            3,
            3,
            minute=5,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def _types(self, response) -> list[str]:
        return [event.type for event in response.context["events"]]

    def test_feed_lists_events_newest_first(self):
        response = self.client.get(reverse(FEED_URL))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._types(response),
            [
                "finance.payment_posted",
                "results.published",
                "finance.invoices_posted",
            ],
        )

    def test_feed_groups_the_page_by_day(self):
        response = self.client.get(reverse(FEED_URL))
        groups = response.context["days"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["day"], timezone.localdate())
        self.assertEqual(groups[1]["day"], datetime.date(2025, 3, 3))
        self.assertEqual(len(groups[1]["events"]), 2)

    def test_rendered_row_shows_the_sentence_and_the_student(self):
        response = self.client.get(reverse(FEED_URL))
        content = response.content.decode()
        self.assertIn("Payment —: 50.00 from FEED-001", content)
        self.assertIn("12 invoice(s) posted for Term 1 (Grade 5)", content)
        self.assertIn("FEED-001", content)

    def test_filter_by_category(self):
        response = self.client.get(reverse(FEED_URL), {"category": "results"})
        self.assertEqual(self._types(response), ["results.published"])

    def test_filter_by_event_type(self):
        response = self.client.get(reverse(FEED_URL), {"event_type": self.payment.type})
        self.assertEqual(self._types(response), ["finance.payment_posted"])

    def test_filter_by_date_range(self):
        response = self.client.get(
            reverse(FEED_URL), {"date_from": "2025-03-01", "date_to": "2025-03-31"}
        )
        self.assertEqual(
            set(self._types(response)),
            {"finance.invoices_posted", "results.published"},
        )

    def test_an_unreadable_filter_shows_everything(self):
        response = self.client.get(reverse(FEED_URL), {"date_from": "not-a-date"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._types(response)), 3)

    def test_filter_choices_only_offer_recorded_activity(self):
        form = self.client.get(reverse(FEED_URL)).context["filter_form"]
        type_choices = {value for value, _ in form.fields["event_type"].choices}
        category_choices = {value for value, _ in form.fields["category"].choices}
        self.assertIn("results.published", type_choices)
        self.assertNotIn("library.book_issued", type_choices)
        self.assertEqual(category_choices, {"", "finance", "results"})

    def test_feed_paginates_at_fifty_rows(self):
        for _ in range(60):
            record_event(event_type="finance.payment_posted", data={"amount": "1.00"})
        response = self.client.get(reverse(FEED_URL))
        self.assertTrue(response.context["is_paginated"])
        self.assertEqual(len(response.context["events"]), 50)
        self.assertEqual(response.context["paginator"].count, 63)

    def test_empty_feed_explains_how_events_appear(self):
        SystemEvent.objects.all().delete()
        response = self.client.get(reverse(FEED_URL))
        self.assertContains(response, "No activity matches these filters")

    def test_a_non_admin_is_refused(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse(FEED_URL)).status_code, 403)


class DailyReportPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="daily_admin",
            email="daily_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.other_admin = User.objects.create_user(
            username="daily_admin_2",
            email="daily_admin_2@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_defaults_to_today(self):
        response = self.client.get(reverse(DAILY_URL))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["day"], timezone.localdate())
        self.assertContains(response, "Nothing was recorded on this day")

    def test_counts_events_by_area_and_totals_payments(self):
        record_event(
            event_type="finance.payment_posted",
            actor=self.admin,
            data={"amount": "50.00"},
        )
        record_event(
            event_type="finance.payment_posted",
            actor=self.other_admin,
            data={"amount": "25.50"},
        )
        record_event(
            event_type="results.published", actor=self.admin, data={"subject": "Maths"}
        )
        response = self.client.get(reverse(DAILY_URL))
        report = response.context["report"]
        self.assertEqual(report["total"], 3)
        self.assertEqual(
            {group["slug"]: group["count"] for group in report["by_category"]},
            {"finance": 2, "results": 1},
        )
        self.assertEqual(report["collected"], Decimal("75.50"))
        self.assertFalse(report["truncated"])

    def test_a_payment_event_without_a_readable_amount_is_skipped(self):
        record_event(event_type="finance.payment_posted", data={"amount": "10.00"})
        record_event(event_type="finance.payment_posted", data={"amount": "n/a"})
        record_event(event_type="finance.payment_posted", data={})
        report = self.client.get(reverse(DAILY_URL)).context["report"]
        self.assertEqual(report["total"], 3)
        self.assertEqual(report["collected"], Decimal("10.00"))

    def test_reports_a_chosen_day(self):
        _on_day(
            record_event(
                event_type="finance.invoices_posted",
                data={"count": 4, "term": "Term 2", "class": "Grade 6"},
            ),
            2025,
            5,
            5,
        )
        response = self.client.get(reverse(DAILY_URL), {"date": "2025-05-05"})
        self.assertEqual(response.context["day"], datetime.date(2025, 5, 5))
        self.assertEqual(response.context["report"]["total"], 1)
        # Today's own day must not leak into it.
        self.assertEqual(self.client.get(reverse(DAILY_URL)).context["report"]["total"], 0)

    def test_an_invalid_date_falls_back_to_today(self):
        """
        A junk date must not 500 and must not silently show another day.
        (Note "05/05/2025" is *not* junk — settings.DATE_INPUT_FORMATS accepts
        slash formats, so the form parses it.)
        """
        response = self.client.get(reverse(DAILY_URL), {"date": "not-a-date"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["day"], timezone.localdate())

    def test_marks_the_day_truncated_past_the_listed_rows(self):
        for _ in range(selectors.DAILY_REPORT_ROWS + 1):
            record_event(event_type="finance.payment_posted", data={"amount": "1.00"})
        report = self.client.get(reverse(DAILY_URL)).context["report"]
        self.assertEqual(len(report["events"]), selectors.DAILY_REPORT_ROWS)
        self.assertTrue(report["truncated"])
        self.assertContains(self.client.get(reverse(DAILY_URL)), "see all")

    def test_a_non_admin_is_refused(self):
        self.client.logout()
        finance = User.objects.create_user(
            username="daily_finance",
            email="daily_finance@test.school",
            password="Testpass1!",
            role=User.Role.FINANCE,
            must_change_password=False,
        )
        self.client.force_login(finance)
        self.assertEqual(self.client.get(reverse(DAILY_URL)).status_code, 403)


class ActivitySelectorQueryTests(TestCase):
    """The selectors must not fan out per row — feed rendering reads two FKs."""

    @classmethod
    def setUpTestData(cls):
        year = make_year()
        school_class = make_class(year)
        cls.admin = User.objects.create_user(
            username="query_admin",
            email="query_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.student = make_student_user(school_class, admission_number="QRY-001")
        for _ in range(20):
            record_event(
                event_type="finance.payment_posted",
                actor=cls.admin,
                student=cls.student,
                data={"amount": "5.00", "student_admission": "QRY-001"},
            )

    def test_the_feed_reads_actor_and_student_in_one_query(self):
        with self.assertNumQueries(1):
            for event in selectors.system_event_list():
                str(event.actor.full_name)
                str(event.student.user.full_name)

    def test_the_daily_report_costs_three_queries(self):
        with self.assertNumQueries(3):
            selectors.daily_activity(timezone.localdate())
