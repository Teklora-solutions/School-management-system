"""
apps/core/tests/test_health.py
------------------------------
Liveness/readiness probe behaviour, including DB-unavailable handling.

The DB-down cases call the view directly with a RequestFactory so the test
harness's surrounding transaction does not itself require the patched cursor.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.db import OperationalError
from django.test import RequestFactory, TestCase
from django.urls import resolve
from django.utils import timezone
from django_q.models import Schedule

from apps.core.health import LivenessView, ReadinessView
from apps.core.models import WorkerHeartbeat
from apps.core.tasks import worker_heartbeat


class HealthProbeTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_urls_map_to_the_expected_probes(self):
        self.assertIs(resolve("/healthz/").func.view_class, LivenessView)
        self.assertIs(resolve("/readyz/").func.view_class, ReadinessView)

    def test_healthz_returns_200(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_readyz_returns_readiness_checks(self):
        response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("db", body["checks"])
        self.assertIn("cache", body["checks"])

    def test_readyz_returns_503_when_db_is_unavailable(self):
        request = self.factory.get("/readyz/")
        with patch(
            "apps.core.health.connection.cursor",
            side_effect=OperationalError("database is down"),
        ):
            response = ReadinessView.as_view()(request)
        self.assertEqual(response.status_code, 503)
        body = json.loads(response.content)
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["checks"]["db"], "error")

    def test_healthz_does_not_touch_the_database(self):
        request = self.factory.get("/healthz/")
        with patch(
            "apps.core.health.connection.cursor",
            side_effect=AssertionError("liveness must not query the DB"),
        ):
            response = LivenessView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'{"status": "ok"}')


class WorkerHeartbeatTests(TestCase):
    """
    A stopped background worker is reported by /readyz/ but never makes the
    site look down: the pages do not depend on the queue to be served.
    """

    def test_a_worker_that_has_never_run_is_reported_but_not_fatal(self):
        response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"]["worker"], "never")

    def test_a_recent_heartbeat_is_ok(self):
        WorkerHeartbeat.record()
        body = self.client.get("/readyz/").json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["checks"]["worker"].startswith("ok"))

    def test_a_stopped_worker_is_stale_and_the_site_stays_up(self):
        WorkerHeartbeat.objects.create(
            pk=WorkerHeartbeat.SINGLETON_PK,
            last_run_at=timezone.now() - timedelta(hours=2),
        )
        response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["checks"]["worker"].startswith("stale"))

    def test_the_heartbeat_task_writes_the_row(self):
        ran_at = worker_heartbeat()
        self.assertTrue(ran_at)
        heartbeat = WorkerHeartbeat.objects.get(pk=WorkerHeartbeat.SINGLETON_PK)
        self.assertIsNotNone(heartbeat.last_run_at)

    def test_the_heartbeat_is_scheduled_every_five_minutes(self):
        schedule = Schedule.objects.get(name="Worker heartbeat")
        self.assertEqual(schedule.func, "apps.core.tasks.worker_heartbeat")
        self.assertEqual(schedule.schedule_type, Schedule.MINUTES)
        self.assertEqual(schedule.minutes, 5)
        self.assertEqual(schedule.repeats, -1)
