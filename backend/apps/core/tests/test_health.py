"""
apps/core/tests/test_health.py
------------------------------
Liveness/readiness probe behaviour, including DB-unavailable handling.

The DB-down cases call the view directly with a RequestFactory so the test
harness's surrounding transaction does not itself require the patched cursor.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.db import OperationalError
from django.test import RequestFactory, TestCase
from django.urls import resolve

from apps.core.health import LivenessView, ReadinessView


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
