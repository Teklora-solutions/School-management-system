"""
apps/core/tests/test_audit_log.py
-----------------------------------
Module 1 — admin audit log viewer.

Covers:
  - admin can browse the audit trail
  - filtering by actor, model (content_type), and date range
"""

from __future__ import annotations

import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User

from ..audit_log import log_action
from ..models import AuditLog


class AuditLogViewerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="audit_admin",
            email="audit_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.teacher = User.objects.create_user(
            username="audit_teacher",
            email="audit_teacher@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )

        log_action(
            actor=cls.admin,
            action="student.created",
            content_type="students.Student",
            object_id="1",
            change_summary={"admission_number": "ADM-AUDIT-1"},
        )
        log_action(
            actor=cls.teacher,
            action="results.submitted",
            content_type="results.ResultSheet",
            object_id="1",
            change_summary={"to": "submitted"},
        )
        cls.old_log = AuditLog.objects.create(
            actor=cls.admin,
            action="user.staff_created",
            content_type="accounts.User",
            object_id="99",
            change_summary={"email": "old@test.school"},
        )
        AuditLog.objects.filter(pk=cls.old_log.pk).update(
            created_at=timezone.make_aware(datetime.datetime(2025, 1, 15, 9, 0))
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def _actions(self, response) -> set[str]:
        return {entry.action for entry in response.context["audit_logs"]}

    def test_admin_sees_all_events(self):
        response = self.client.get(reverse("core:audit_log"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._actions(response),
            {"student.created", "results.submitted", "user.staff_created"},
        )

    def test_filter_by_actor(self):
        response = self.client.get(
            reverse("core:audit_log"), {"actor": self.teacher.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._actions(response), {"results.submitted"})

    def test_filter_by_model(self):
        response = self.client.get(
            reverse("core:audit_log"), {"content_type": "students.Student"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._actions(response), {"student.created"})

    def test_filter_by_date_range(self):
        response = self.client.get(
            reverse("core:audit_log"),
            {"date_from": "2025-01-01", "date_to": "2025-01-31"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._actions(response), {"user.staff_created"})

    def test_filters_combine(self):
        response = self.client.get(
            reverse("core:audit_log"),
            {"actor": self.admin.pk, "content_type": "accounts.User"},
        )
        self.assertEqual(self._actions(response), {"user.staff_created"})

    def test_filter_form_lists_choices(self):
        response = self.client.get(reverse("core:audit_log"))
        form = response.context["filter_form"]
        model_choices = {value for value, _ in form.fields["content_type"].choices}
        self.assertIn("students.Student", model_choices)
        self.assertIn("results.ResultSheet", model_choices)
        self.assertIn(self.teacher, form.fields["actor"].queryset)
