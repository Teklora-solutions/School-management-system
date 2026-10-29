"""
The two login entry points must both offer the portal chooser, and every portal
must answer at its own URL with the project's own form.
"""

from django.test import TestCase
from django.urls import reverse

from ..portals import PORTALS


class LoginRouteTests(TestCase):
    def test_accounts_login_route_renders_the_chooser(self):
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/portal_chooser.html")

    def test_custom_login_route_renders_the_chooser(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/portal_chooser.html")

    def test_each_portal_renders_the_custom_form(self):
        for slug in PORTALS:
            with self.subTest(slug=slug):
                response = self.client.get(
                    reverse("accounts:role_login", kwargs={"role": slug})
                )
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "auth/login.html")
                self.assertContains(response, 'name="username"')
