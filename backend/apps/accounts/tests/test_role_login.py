"""
apps/accounts/tests/test_role_login.py
--------------------------------------
Module 5 P2 — every audience gets its own sign-in page.

The load-bearing rule is that a portal page only ever opens a session for its
own role. A form that accepts anyone is the shared login we replaced, just with
a smarter URL, so most of what is pinned here is the rejection path.
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


def role_login(slug: str, next_url: str = "") -> str:
    url = reverse("accounts:role_login", kwargs={"role": slug})
    return f"{url}?next={next_url}" if next_url else url


class PortalSignInTests(TestCase):
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

    def sign_in(self, at: str, username: str, password: str = PASSWORD):
        return self.client.post(
            role_login(at), {"username": username, "password": password}
        )

    def test_each_portal_signs_its_own_role_into_its_own_dashboard(self):
        for portal in PORTALS.values():
            with self.subTest(portal=portal.slug):
                self.client.logout()
                response = self.sign_in(portal.slug, self.users[portal.slug].email)
                self.assertRedirects(
                    response,
                    reverse(PORTAL_HOME[portal.slug]),
                    fetch_redirect_response=False,
                )

    def test_right_password_on_the_wrong_portal_opens_no_session(self):
        director = self.users["director"]
        response = self.sign_in("teacher", director.email)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "That is a director account.")
        self.assertTemplateUsed(response, "auth/login.html")

    def test_no_portal_accepts_a_bad_password(self):
        response = self.sign_in(
            "director", self.users["director"].email, password="wrong-once"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_next_wins_over_the_role_home(self):
        response = self.client.post(
            role_login("teacher", next_url="/students/"),
            {"username": self.users["teacher"].email, "password": PASSWORD},
        )
        self.assertRedirects(response, "/students/", fetch_redirect_response=False)

    def test_offsite_next_is_ignored(self):
        response = self.client.post(
            role_login("teacher", next_url="//evil.example/steal"),
            {"username": self.users["teacher"].email, "password": PASSWORD},
        )
        self.assertRedirects(
            response,
            reverse("dashboards:teacher_home"),
            fetch_redirect_response=False,
        )

    def test_unknown_portal_is_a_404_not_a_form(self):
        response = self.client.get(role_login("bursar"))
        self.assertEqual(response.status_code, 404)


class StudentCredentialTests(TestCase):
    """
    A student's username is the admission number with ``/`` turned into ``_``,
    and their initial password is the admission number as written. Both have to
    be typed on the student page and nowhere else.
    """

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

    def post_as_student(self, at: str):
        return self.client.post(
            role_login(at),
            {"username": self.USERNAME, "password": self.ADMISSION},
        )

    def test_admission_number_credentials_work_on_the_student_portal(self):
        self.assertEqual(self.post_as_student("student").status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_the_same_credentials_do_not_work_on_a_staff_portal(self):
        self.assertEqual(self.post_as_student("teacher").status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)


class PrintedAdmissionNumberTests(TestCase):
    """
    An account is keyed on ``ADM/2026/014`` → ``adm_2026_014``, but the admission
    letter and the bulk-import file both print ``ADM/2026/014``. A pupil should
    not have to know the derivation to sign in.
    """

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
            role_login("student"), {"username": username, "password": password}
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


class PortalChooserTests(TestCase):
    def test_chooser_links_every_portal_and_forwards_next(self):
        response = self.client.get("/login/?next=/finance/invoices/")
        self.assertEqual(response.status_code, 200)
        for portal in PORTALS.values():
            self.assertContains(response, portal.label)
            # urlencode leaves slashes alone, so the next target stays readable.
            self.assertContains(
                response,
                f"{role_login(portal.slug)}?next=/finance/invoices/",
            )

    def test_chooser_offers_no_form_of_its_own(self):
        self.assertNotContains(self.client.get("/login/"), 'name="username"')

    def test_authenticated_user_is_not_shown_the_chooser(self):
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
        """The middleware exempts the /login/ prefix, so this must not loop."""
        user = User.objects.create_user(
            username="pending",
            email="pending@test.school",
            password=PASSWORD,
            role=User.Role.FINANCE,
            must_change_password=True,
        )
        self.assertRedirects(
            self.client.post(
                role_login("finance"),
                {"username": user.email, "password": PASSWORD},
            ),
            reverse("accounts:force_password_change"),
            fetch_redirect_response=False,
        )
        # A page reload while still pending must not bounce the user onward.
        self.client.get(role_login("finance"))

    def test_roleless_superuser_may_use_any_portal(self):
        """
        ``createsuperuser`` leaves role blank and RoleRequiredMixin lets that
        account through every gate; the login pages have to agree with it or the
        account can exist but never sign in.
        """
        root = User.objects.create_superuser(
            username="root", email="root@test.school", password=PASSWORD
        )
        response = self.client.post(
            role_login("director"), {"username": "root", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(root.pk))


class DashboardDestinationTests(TestCase):
    """
    ``/dashboard/`` has to be a real path.

    ``config/urls.py`` includes the public site before the accounts app and the
    public site owns ``/``, so while ``dashboard_redirect`` was mounted at the
    root every "go to my dashboard" silently served the school website — which
    looked like a page refresh. These tests pin the destination, not the stub.
    """

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
        # Reversing the stub must not hand back the public home page's path.
        self.assertNotEqual(
            reverse("accounts:dashboard_redirect"), reverse("public:home")
        )

    def test_a_signed_in_visitor_lands_on_their_own_dashboard(self):
        for slug, home in PORTAL_HOME.items():
            with self.subTest(portal=slug):
                self.client.force_login(self.users[slug])
                first = self.client.get(role_login(slug))
                self.assertRedirects(
                    first,
                    reverse("accounts:dashboard_redirect"),
                    fetch_redirect_response=False,
                )
                landing = self.client.get(first["Location"], follow=True)
                self.assertEqual(landing.redirect_chain, [(reverse(home), 302)])
                self.assertEqual(landing.status_code, 200)

    def test_a_signed_in_visitor_is_not_shown_the_portal_chooser(self):
        self.client.force_login(self.users["teacher"])
        first = self.client.get(reverse("accounts:login"))
        self.assertRedirects(
            first,
            reverse("accounts:dashboard_redirect"),
            fetch_redirect_response=False,
        )
        landing = self.client.get(first["Location"], follow=True)
        self.assertEqual(
            landing.redirect_chain, [(reverse("dashboards:teacher_home"), 302)]
        )

    def test_anonymous_visitor_gets_the_public_home(self):
        response = self.client.get(reverse("accounts:dashboard_redirect"))
        self.assertRedirects(
            response, reverse("public:home"), fetch_redirect_response=False
        )

    def test_roleless_superuser_does_not_bounce_in_a_loop(self):
        root = User.objects.create_superuser(
            username="root_dash",
            email="root.dash@test.school",
            password=PASSWORD,
            must_change_password=False,
        )
        self.client.force_login(root)
        landing = self.client.get(
            reverse("accounts:dashboard_redirect"), follow=True
        )
        self.assertEqual(landing.redirect_chain, [(reverse("public:home"), 302)])
        self.assertEqual(landing.status_code, 200)
