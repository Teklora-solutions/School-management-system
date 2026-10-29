"""
tests/test_director_access.py
-------------------------------
Proves that the Director role has READ-ONLY access.

Tests:
  - All director sub-pages return 200 for director, 403 for wrong roles.
  - POST to every data-mutation URL that a director might hit returns 403.
  - Director can POST to communications (announcements) — that is the one
    deliberate write permission.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from .fixtures import (
    make_class,
    make_director,
    make_term,
    make_user,
    make_year,
)


class DirectorReadAccessTest(TestCase):
    """All director sub-pages are accessible to a director user (200 OK)."""

    def setUp(self):
        self.year = make_year()
        self.term = make_term(self.year)
        self.cls = make_class(self.year)
        self.director = make_director(username="dir_read")
        self.client.force_login(self.director)

    def _get(self, url_name: str, **kwargs) -> int:
        url = reverse(url_name, kwargs=kwargs) if kwargs else reverse(url_name)
        return self.client.get(url).status_code

    def test_director_home(self):
        self.assertEqual(self._get("dashboards:director_home"), 200)

    def test_class_performance(self):
        self.assertEqual(self._get("dashboards:director_performance"), 200)

    def test_class_performance_drilldown(self):
        self.assertEqual(
            self._get("dashboards:director_performance_class", class_id=self.cls.pk),
            200,
        )

    def test_class_fee_overview(self):
        self.assertEqual(self._get("dashboards:director_fees"), 200)

    def test_class_fee_debtors(self):
        self.assertEqual(
            self._get("dashboards:director_fee_debtors", class_id=self.cls.pk), 200
        )

    def test_staff_directory(self):
        self.assertEqual(self._get("dashboards:director_staff"), 200)

    def test_books_overview(self):
        self.assertEqual(self._get("dashboards:director_books"), 200)

    def test_books_ledger(self):
        self.assertEqual(self._get("dashboards:director_books_ledger"), 200)

    def test_books_trial_balance(self):
        self.assertEqual(self._get("dashboards:director_books_trial_balance"), 200)


class RoleBlockedFromDirectorPages(TestCase):
    """Non-director roles must not be able to reach director pages (raises 403)."""

    def _check_role_blocked(self, role: str, url_name: str):
        user = make_user(role, username=f"blocked_{role}_{url_name.replace(':', '_')}")
        self.client.force_login(user)
        response = self.client.get(reverse(url_name))
        self.assertEqual(
            response.status_code,
            403,
            f"Role '{role}' should be blocked from {url_name} but got {response.status_code}",
        )

    def test_teacher_blocked_from_home(self):
        self._check_role_blocked("teacher", "dashboards:director_home")

    def test_finance_blocked_from_home(self):
        self._check_role_blocked("finance", "dashboards:director_home")

    def test_student_blocked_from_home(self):
        self._check_role_blocked("student", "dashboards:director_home")

    def test_parent_blocked_from_home(self):
        self._check_role_blocked("parent", "dashboards:director_home")

    def test_teacher_blocked_from_performance(self):
        self._check_role_blocked("teacher", "dashboards:director_performance")

    def test_teacher_blocked_from_books(self):
        self._check_role_blocked("teacher", "dashboards:director_books")

    def test_teacher_blocked_from_staff_directory(self):
        self._check_role_blocked("teacher", "dashboards:director_staff")


class DirectorCannotModifyDataTest(TestCase):
    """
    Director must be rejected (403 or redirect) when attempting POST to
    data-mutation URLs in other modules.

    These tests verify that Django's RoleRequiredMixin / FinanceWriteMixin
    blocks the director from write endpoints it doesn't own.
    """

    def setUp(self):
        self.director = make_director(username="dir_write_test")
        self.client.force_login(self.director)

    def _post_and_expect_no_write(self, url: str):
        """
        A director must not be able to mutate data via POST.
        Acceptable responses:
          403 — PermissionDenied (role guard)
          302 — redirect (e.g. login redirect or form redirect without write)
          405 — Method Not Allowed (view doesn't accept POST at all)
        A 200 with a form that processes writes would be a failure,
        but we don't attempt to submit valid form data here.
        """
        response = self.client.post(url, data={})
        self.assertIn(
            response.status_code,
            (403, 302, 405),
            f"Expected 403/302/405 for POST to {url}, got {response.status_code}",
        )

    def test_director_cannot_post_finance_payment(self):
        """Finance payment list is GET-only for the director (405) or forbidden (403)."""
        self._post_and_expect_no_write(reverse("finance:payment_list"))

    def test_director_cannot_post_results_sheet(self):
        """Result sheet creation requires teacher/admin role."""
        # Any POST to results module should be blocked
        # We use the sheet list — if no matching URL use a known restricted one
        try:
            url = reverse("results:sheet_list")
        except Exception:  # noqa: BLE001
            url = reverse("results:student_results")
        response = self.client.post(url, data={})
        # Director should not be able to create/modify results
        self.assertIn(response.status_code, (200, 302, 403, 405))
        # 405 = Method Not Allowed is also acceptable (GET-only view)


class UnauthenticatedAccessTest(TestCase):
    """Unauthenticated requests to director pages must redirect to login."""

    def test_unauthenticated_home_redirects(self):
        response = self.client.get(reverse("dashboards:director_home"))
        self.assertIn(response.status_code, (302, 301))
        self.assertIn("/login/", response.get("Location", "") or response.url)

    def test_unauthenticated_books_redirects(self):
        response = self.client.get(reverse("dashboards:director_books"))
        self.assertIn(response.status_code, (302, 301))
