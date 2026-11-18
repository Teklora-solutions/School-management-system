"""
apps/assistant/tests/test_alerts.py
-----------------------------------
Creating, acknowledging and escalating safeguarding alerts, the dashboard
check that does not depend on the worker, and the visible record of a failed
email.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from apps.assistant import alerts, tasks
from apps.assistant.middleware import CHECK_KEY, EscalationCheckMiddleware
from apps.assistant.models import (
    AssistantJobFailure,
    AssistantSettings,
    SafeguardingAlert,
)
from apps.core.models import AuditLog, Notification
from apps.dashboards.tests.fixtures import make_user


class AlertCreationTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="alert_admin")
        self.director = make_user("director", username="alert_director")
        self.parent = make_user("parent", username="alert_parent")
        self.row = AssistantSettings.load()
        self.row.safeguarding_contact_name = "Mrs Achieng"
        self.row.safeguarding_contact_phone = "0722000111"
        self.row.safeguarding_contact_email = "safeguarding@school.example"
        self.row.handoff_email = "office@school.example"
        self.row.save()

    def create(self, **overrides):
        data = {
            "profile": "signed_in",
            "category": "abuse",
            "rule_id": "M-ABUSE-1",
            "raw_text": "He touches me. Call 0712 345 678.",
            "user": self.parent,
        }
        data.update(overrides)
        return alerts.create_alert(**data)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_a_masked_alert_notifies_admin_and_director(self):
        alert = self.create()
        self.assertEqual(alert.excerpt.count("0712"), 0)
        self.assertIn("[PHONE]", alert.excerpt)

        recipients = set(
            Notification.objects.filter(category="safety").values_list(
                "recipient__role", flat=True
            )
        )
        self.assertEqual(recipients, {"admin", "director"})
        self.assertFalse(
            Notification.objects.filter(category="safety", deliver_after__isnull=False).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("masked", mail.outbox[0].body.lower())

    def test_the_alert_is_audited(self):
        alert = self.create()
        self.assertTrue(
            AuditLog.objects.filter(
                action="assistant.safeguarding_alert_created",
                object_id=str(alert.pk),
            ).exists()
        )

    def test_the_full_message_is_not_stored(self):
        alert = self.create(raw_text="He touches me. " + "x" * 500)
        self.assertLessEqual(len(alert.excerpt), 300)
        self.assertNotIn("x" * 400, alert.excerpt)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_acknowledging_keeps_the_row_and_records_who(self):
        alert = self.create()
        alerts.acknowledge(alert, actor=self.director)
        alert.refresh_from_db()
        self.assertEqual(alert.status, SafeguardingAlert.Status.ACKNOWLEDGED)
        self.assertEqual(alert.acknowledged_by, self.director)
        self.assertIsNotNone(alert.acknowledged_at)
        self.assertEqual(SafeguardingAlert.objects.count(), 1)


class AlertClosureTests(TestCase):
    """Closing records an outcome; the proposed retention erases only the excerpt."""

    def setUp(self):
        self.admin = make_user("admin", username="close_admin")
        self.alert = SafeguardingAlert.objects.create(
            profile="public",
            category="abuse",
            rule_id="M-ABUSE-1",
            excerpt="He touches me. [PHONE]",
        )

    def close(self, outcome="Called the family; referred to the safeguarding lead."):
        return alerts.close_alert(self.alert, outcome=outcome, actor=self.admin)

    def test_closing_records_outcome_and_who(self):
        self.close()
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, SafeguardingAlert.Status.CLOSED)
        self.assertEqual(self.alert.outcome, "Called the family; referred to the safeguarding lead.")
        self.assertEqual(self.alert.closed_by, self.admin)
        self.assertIsNotNone(self.alert.closed_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="assistant.safeguarding_alert_closed",
                object_id=str(self.alert.pk),
            ).exists()
        )
        # The outcome is staff notes, not copied into the audit summary.
        entry = AuditLog.objects.filter(
            action="assistant.safeguarding_alert_closed"
        ).latest("created_at")
        self.assertNotIn("Called the family", str(entry.change_summary))

    def test_closing_without_an_outcome_is_refused(self):
        with self.assertRaises(ValueError):
            alerts.close_alert(self.alert, outcome="   ", actor=self.admin)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, SafeguardingAlert.Status.OPEN)

    def test_acknowledging_a_closed_alert_does_not_reopen_it(self):
        self.close()
        alerts.acknowledge(self.alert, actor=self.admin)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, SafeguardingAlert.Status.CLOSED)

    def set_retention(self, months):
        row = AssistantSettings.load()
        row.alert_retention_months = months
        row.save()

    def test_retention_erases_the_excerpt_and_keeps_the_record(self):
        self.close()
        self.set_retention(12)
        SafeguardingAlert.objects.filter(pk=self.alert.pk).update(
            closed_at=timezone.now() - datetime.timedelta(days=400)
        )
        erased = alerts.erase_excerpts()
        self.assertEqual(len(erased), 1)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.excerpt, "")
        self.assertIsNotNone(self.alert.excerpt_erased_at)
        self.assertEqual(self.alert.category, "abuse")
        self.assertIn("safeguarding lead", self.alert.outcome)
        self.assertTrue(
            AuditLog.objects.filter(
                action="assistant.safeguarding_excerpt_erased",
                object_id=str(self.alert.pk),
            ).exists()
        )
        # Running again does nothing.
        self.assertEqual(alerts.erase_excerpts(), [])

    def test_retention_off_keeps_the_excerpt(self):
        self.close()
        self.set_retention(0)
        SafeguardingAlert.objects.filter(pk=self.alert.pk).update(
            closed_at=timezone.now() - datetime.timedelta(days=4000)
        )
        self.assertEqual(alerts.erase_excerpts(), [])
        self.alert.refresh_from_db()
        self.assertNotEqual(self.alert.excerpt, "")

    def test_an_open_alert_is_never_erasable(self):
        self.set_retention(12)
        self.assertEqual(alerts.erasable_excerpts(alert_id=self.alert.pk), [])
        self.assertEqual(alerts.erase_excerpts(alert_id=self.alert.pk), [])

    def test_a_single_closed_alert_can_be_erased_for_a_request(self):
        self.close()
        self.set_retention(12)
        alerts.erase_excerpts(alert_id=self.alert.pk, reason="request")
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.excerpt, "")
        self.assertIsNotNone(self.alert.excerpt_erased_at)


class EscalationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user("admin", username="esc_admin")
        self.row = AssistantSettings.load()
        self.row.safeguarding_contact_name = "Mrs Achieng"
        self.row.safeguarding_contact_phone = "0722000111"
        # Equal start/end bypasses working-hours deferral, so the test is
        # independent of the clock.
        self.row.working_hours_start = datetime.time(7, 0)
        self.row.working_hours_end = datetime.time(7, 0)
        self.row.escalation_minutes = 5
        self.row.save()

    def make_alert(self):
        return alerts.create_alert(
            profile="public", category="self_harm", rule_id="M-SELF-HARM-1"
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_overdue_alert_escalates_once(self):
        alert = self.make_alert()
        before = Notification.objects.filter(category="safety").count()
        due = alert.created_at + datetime.timedelta(minutes=6)
        self.assertEqual(alerts.escalate_due(now=due), 1)
        alert.refresh_from_db()
        self.assertIsNotNone(alert.escalated_at)
        self.assertEqual(
            Notification.objects.filter(category="safety").count(), before + 1
        )
        self.assertEqual(alerts.escalate_due(now=due + datetime.timedelta(hours=1)), 0)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_not_yet_due_nothing_happens(self):
        alert = self.make_alert()
        self.assertEqual(
            alerts.escalate_due(now=alert.created_at + datetime.timedelta(minutes=1)), 0
        )
        alert.refresh_from_db()
        self.assertIsNone(alert.escalated_at)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_acknowledged_alert_never_escalates(self):
        alert = self.make_alert()
        alerts.acknowledge(alert, actor=self.admin)
        self.assertEqual(
            alerts.escalate_due(now=alert.created_at + datetime.timedelta(hours=2)), 0
        )

    def test_the_dashboard_check_escalates_without_a_worker(self):
        alert = self.make_alert()
        SafeguardingAlert.objects.filter(pk=alert.pk).update(
            created_at=timezone.now() - datetime.timedelta(minutes=6)
        )
        alert.refresh_from_db()
        cache.clear()
        request = RequestFactory().get("/dashboard/")
        request.user = self.admin
        middleware = EscalationCheckMiddleware(lambda r: HttpResponse("ok"))
        response = middleware(request)
        self.assertEqual(response.status_code, 200)
        alert.refresh_from_db()
        self.assertIsNotNone(alert.escalated_at)

    def test_the_check_is_throttled(self):
        cache.clear()
        cache.set(CHECK_KEY, 1)
        request = RequestFactory().get("/dashboard/")
        request.user = self.admin
        middleware = EscalationCheckMiddleware(lambda r: HttpResponse("ok"))
        with patch("apps.assistant.alerts.refresh_page_state") as mocked:
            middleware(request)
        mocked.assert_not_called()

    def test_a_failure_in_the_check_never_breaks_the_page(self):
        cache.clear()
        request = RequestFactory().get("/dashboard/")
        request.user = self.admin
        middleware = EscalationCheckMiddleware(lambda r: HttpResponse("ok"))
        with patch(
            "apps.assistant.alerts.refresh_page_state", side_effect=RuntimeError
        ):
            response = middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_requests_do_not_run_the_check(self):
        cache.clear()
        teacher = make_user("teacher", username="esc_teacher")
        request = RequestFactory().get("/dashboard/")
        request.user = teacher
        middleware = EscalationCheckMiddleware(lambda r: HttpResponse("ok"))
        with patch("apps.assistant.alerts.refresh_page_state") as mocked:
            middleware(request)
        mocked.assert_not_called()


class EmailFailureTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", username="mail_admin")
        self.alert = SafeguardingAlert.objects.create(
            profile="public", category="abuse", excerpt="[PHONE]"
        )

    @patch("apps.assistant.tasks.send_mail", side_effect=RuntimeError("SMTP is down"))
    def test_a_failed_email_is_recorded_and_visible(self, _send_mail):
        sent = tasks.send_alert_email(self.alert, attempt=1)
        self.assertFalse(sent)
        failure = AssistantJobFailure.objects.filter(
            task_name="assistant.safeguarding_email"
        ).first()
        self.assertIsNotNone(failure)
        self.assertIn("SMTP", failure.reason)

    def test_no_recipients_is_recorded(self):
        from apps.accounts.models import User

        User.objects.exclude(pk=self.admin.pk).delete()
        User.objects.update(email="")
        sent = tasks.send_alert_email(self.alert, attempt=1)
        self.assertFalse(sent)
        self.assertTrue(
            AssistantJobFailure.objects.filter(
                reason__icontains="No email recipients"
            ).exists()
        )
