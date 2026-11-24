"""
apps/accounts/tests/test_role_login.py
--------------------------------------
Unified sign-in page tests: one login page at /login/ authenticates any role
(staff email, parent username, student admission number, or roleless superuser)
and routes server-side to the user's dashboard without role enumeration.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import resolve, reverse

from ..models import User
from ..portals import PORTALS

PASSWORD = "Portal-Pass-1"

PORTAL_HOME = {
    "admin": "dashboards:admin_home",
    "director": "dashboards:director_home",
    "teacher": "dashboards:teacher_home",
    "student": "dashboards:student_home",
    "finance": "dashboards:finance_home",
    "parent": "dashboards:parent_home",
}


def login_url(next_url: str = "") -> str:
    url = reverse("accounts:login")
    return f"{url}?next={next_url}" if next_url else url


def role_login(slug: str, next_url: str = "") -> str:
    url = reverse("accounts:role_login", kwargs={"role": slug})
    return f"{url}?next={next_url}" if next_url else url


class UnifiedSignInTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {
            portal.slug: User.objects.create_user(
                username=f"user_{portal.slug}",
                email=f"user_{portal.slug}@test.school",
                password=PASSWORD,
                role=portal.role,
                must_change_password=False,
            )
            for portal in PORTALS.values()
        }

    def sign_in(self, username: str, password: str = PASSWORD, next_url: str = ""):
        return self.client.post(
            login_url(next_url), {"username": username, "password": password}
        )

    def test_each_role_signs_into_its_own_dashboard(self):
        for portal in PORTALS.values():
            with self.subTest(portal=portal.slug):
                self.client.logout()
                response = self.sign_in(self.users[portal.slug].email)
                self.assertRedirects(
                    response,
                    reverse(PORTAL_HOME[portal.slug]),
                    fetch_redirect_response=False,
                )

    def test_bad_password_never_opens_a_session_or_leaks_role(self):
        response = self.sign_in(self.users["director"].email, password="wrong-once")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotContains(response, "director account")

    def test_next_wins_over_the_role_home(self):
        response = self.sign_in(self.users["teacher"].email, next_url="/students/")
        self.assertRedirects(response, "/students/", fetch_redirect_response=False)

    def test_offsite_next_is_ignored(self):
        response = self.sign_in(
            self.users["teacher"].email, next_url="//evil.example/steal"
        )
        self.assertRedirects(
            response,
            reverse("dashboards:teacher_home"),
            fetch_redirect_response=False,
        )

    def test_unknown_portal_is_a_404(self):
        response = self.client.get(role_login("bursar"))
        self.assertEqual(response.status_code, 404)

    def test_legacy_portal_route_forwards_next_to_unified_login(self):
        response = self.client.get(role_login("teacher", next_url="/finance/invoices/"))
        self.assertRedirects(
            response,
            "/login/?next=%2Ffinance%2Finvoices%2F",
            fetch_redirect_response=False,
        )


class StudentCredentialTests(TestCase):
    USERNAME = "adm_2025_014"
    ADMISSION = "ADM/2025/014"

    def setUp(self):
        User.objects.create_user(
            username=self.USERNAME,
            email="adm014@student.school",
            password=self.ADMISSION,
            role=User.Role.STUDENT,
            must_change_password=False,
        )

    def test_admission_number_credentials_work_on_unified_login(self):
        response = self.client.post(
            login_url(), {"username": self.USERNAME, "password": self.ADMISSION}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)


class PrintedAdmissionNumberTests(TestCase):
    ADMISSION = "ADM/2026/014"

    def setUp(self):
        from apps.academics.models import AcademicYear, SchoolClass
        from apps.students.services import create_student_with_guardian

        year = AcademicYear.objects.create(
            name="2026/2027",
            start_date="2026-09-01",
            end_date="2027-07-31",
            is_current=True,
        )
        school_class = SchoolClass.objects.create(
            academic_year=year, name="Grade 10", level_order=10
        )
        with patch("apps.students.services.enqueue_credentials_email"):
            self.student = create_student_with_guardian(
                admission_number=self.ADMISSION,
                first_name="Kofi",
                last_name="Mensah",
                email="printed.adm014@student.school",
                current_class=school_class,
            )
        self.user = self.student.user

    def sign_in(self, username: str, password: str):
        return self.client.post(
            login_url(), {"username": username, "password": password}
        )

    def test_the_printed_admission_number_signs_in(self):
        response = self.sign_in(self.ADMISSION, self.ADMISSION)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(str(self.user.pk), self.client.session["_auth_user_id"])

    def test_the_stored_username_still_signs_in(self):
        response = self.sign_in("adm_2026_014", self.ADMISSION)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(str(self.user.pk), self.client.session["_auth_user_id"])

    def test_first_sign_in_forces_a_password_change(self):
        self.assertTrue(self.user.must_change_password)
        response = self.sign_in(self.ADMISSION, self.ADMISSION)
        self.assertRedirects(
            response, reverse("accounts:force_password_change"), fetch_redirect_response=False
        )

    def test_a_bad_password_with_the_printed_number_opens_no_session(self):
        response = self.sign_in(self.ADMISSION, "not-the-password")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)


class UnifiedLoginPageTests(TestCase):
    def test_login_page_offers_unified_form(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')

    def test_authenticated_user_is_redirected_to_dashboard(self):
        user = User.objects.create_user(
            username="already_in",
            email="already.in@test.school",
            password=PASSWORD,
            role=User.Role.DIRECTOR,
            must_change_password=False,
        )
        self.client.force_login(user)
        self.assertRedirects(
            self.client.get("/login/"),
            reverse("accounts:dashboard_redirect"),
            fetch_redirect_response=False,
        )


class PortalEdgeCaseTests(TestCase):
    def test_pending_password_change_still_lands_on_the_change_screen(self):
        user = User.objects.create_user(
            username="pending",
            email="pending@test.school",
            password=PASSWORD,
            role=User.Role.FINANCE,
            must_change_password=True,
        )
        self.assertRedirects(
            self.client.post(
                login_url(),
                {"username": user.email, "password": PASSWORD},
            ),
            reverse("accounts:force_password_change"),
            fetch_redirect_response=False,
        )
        # Active session with must_change_password=True is also redirected
        self.assertRedirects(
            self.client.get(reverse("dashboards:finance_home")),
            reverse("accounts:force_password_change"),
            fetch_redirect_response=False,
        )

    def test_roleless_superuser_signs_in_through_unified_login(self):
        root = User.objects.create_superuser(
            username="root", email="root@test.school", password=PASSWORD
        )
        response = self.client.post(
            login_url(), {"username": "root", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(root.pk))


class DashboardDestinationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {
            portal.slug: User.objects.create_user(
                username=f"dash_{portal.slug}",
                email=f"dash_{portal.slug}@test.school",
                password=PASSWORD,
                role=portal.role,
                must_change_password=False,
            )
            for portal in PORTALS.values()
        }

    def test_dashboard_route_is_its_own_path(self):
        self.assertEqual(reverse("accounts:dashboard_redirect"), "/dashboard/")
        self.assertEqual(resolve("/dashboard/").url_name, "dashboard_redirect")
        self.assertNotEqual(
            reverse("accounts:dashboard_redirect"), reverse("public:home")
        )

    def test_a_signed_in_visitor_lands_on_their_own_dashboard(self):
        for slug, home in PORTAL_HOME.items():
            with self.subTest(portal=slug):
                self.client.force_login(self.users[slug])
                first = self.client.get(reverse("accounts:login"))
                self.assertRedirects(
                    first,
                    reverse("accounts:dashboard_redirect"),
                    fetch_redirect_response=False,
                )
                landing = self.client.get(first["Location"], follow=True)
                self.assertEqual(landing.redirect_chain, [(reverse(home), 302)])
                self.assertEqual(landing.status_code, 200)

    def test_anonymous_visitor_gets_the_public_home(self):
        response = self.client.get(reverse("accounts:dashboard_redirect"))
        self.assertRedirects(
            response, reverse("public:home"), fetch_redirect_response=False
        )
