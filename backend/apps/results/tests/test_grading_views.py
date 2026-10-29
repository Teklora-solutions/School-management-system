"""
apps/results/tests/test_grading_views.py
----------------------------------------
Admin screens for the pass mark and the year's grading system.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.academics.models import AcademicYear, SchoolClass
from apps.accounts.models import User
from apps.results.models import GradeScale, GradeScaleBand, GradingConfig


class GradingViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="grading_admin",
            email="grading_admin@test.school",
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
        cls.grade5 = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.grade6 = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 6", level_order=6
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def _bands_payload(self):
        return {
            "bands-TOTAL_FORMS": "2",
            "bands-INITIAL_FORMS": "0",
            "bands-MIN_NUM_FORMS": "0",
            "bands-MAX_NUM_FORMS": "1000",
            "bands-0-min_score": "0",
            "bands-0-max_score": "49",
            "bands-0-grade": "F",
            "bands-0-remark": "Fail",
            "bands-0-points": "0",
            "bands-1-min_score": "50",
            "bands-1-max_score": "100",
            "bands-1-grade": "P",
            "bands-1-remark": "Pass",
            "bands-1-points": "2",
        }

    def test_pass_mark_page_links_to_the_grading_system(self):
        url = reverse("results:grading_config", args=[self.year.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Grading System")
        self.assertContains(resp, reverse("results:grade_scale", args=[self.year.pk]))

    def test_pass_mark_is_saved(self):
        url = reverse("results:grading_config", args=[self.year.pk])
        resp = self.client.post(url, {"pass_mark": "45"})
        self.assertRedirects(resp, reverse("results:grading_list"))
        self.assertEqual(
            GradingConfig.objects.get(academic_year=self.year).pass_mark,
            Decimal("45.00"),
        )

    def test_grading_system_page_lists_classes(self):
        url = reverse("results:grade_scale", args=[self.year.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Grade 5")
        self.assertContains(resp, "Grade 6")
        self.assertContains(resp, f'value="{self.grade5.pk}"')

    def test_grading_system_is_created_with_its_classes(self):
        url = reverse("results:grade_scale", args=[self.year.pk])
        data = {
            "name": "Upper Primary",
            "classes": [str(self.grade5.pk)],
            **self._bands_payload(),
        }
        resp = self.client.post(url, data)
        self.assertRedirects(resp, reverse("results:grading_list"))

        scale = GradeScale.objects.get(academic_year=self.year)
        self.assertEqual(scale.name, "Upper Primary")
        self.assertEqual(
            list(scale.bands.values_list("grade", flat=True)), ["F", "P"]
        )
        self.grade5.refresh_from_db()
        self.grade6.refresh_from_db()
        self.assertEqual(self.grade5.grade_scale_id, scale.pk)
        self.assertIsNone(self.grade6.grade_scale_id)

    def test_resaving_updates_the_same_years_system(self):
        url = reverse("results:grade_scale", args=[self.year.pk])
        self.client.post(
            url,
            {"name": "First", "classes": [str(self.grade5.pk)], **self._bands_payload()},
        )
        self.client.post(
            url,
            {"name": "Second", "classes": [str(self.grade6.pk)], **self._bands_payload()},
        )
        self.assertEqual(GradeScale.objects.filter(academic_year=self.year).count(), 1)
        scale = GradeScale.objects.get(academic_year=self.year)
        self.assertEqual(scale.name, "Second")
        self.grade5.refresh_from_db()
        self.grade6.refresh_from_db()
        self.assertIsNone(self.grade5.grade_scale_id)
        self.assertEqual(self.grade6.grade_scale_id, scale.pk)

    def test_invalid_bands_are_rejected(self):
        url = reverse("results:grade_scale", args=[self.year.pk])
        data = {
            "name": "Gappy",
            "classes": [str(self.grade5.pk)],
            "bands-TOTAL_FORMS": "1",
            "bands-INITIAL_FORMS": "0",
            "bands-MIN_NUM_FORMS": "0",
            "bands-MAX_NUM_FORMS": "1000",
            "bands-0-min_score": "0",
            "bands-0-max_score": "49",
            "bands-0-grade": "F",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(GradeScale.objects.filter(academic_year=self.year).exists())
        self.grade5.refresh_from_db()
        self.assertIsNone(self.grade5.grade_scale_id)

    def test_grading_system_can_be_removed(self):
        url = reverse("results:grade_scale", args=[self.year.pk])
        self.client.post(
            url,
            {"name": "Upper", "classes": [str(self.grade5.pk)], **self._bands_payload()},
        )
        resp = self.client.post(
            reverse("results:grade_scale_delete", args=[self.year.pk])
        )
        self.assertRedirects(resp, reverse("results:grading_list"))
        self.assertFalse(GradeScale.objects.filter(academic_year=self.year).exists())
        self.assertFalse(GradeScaleBand.objects.exists())
        self.grade5.refresh_from_db()
        self.assertIsNone(self.grade5.grade_scale_id)
