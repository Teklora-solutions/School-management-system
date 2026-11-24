"""
The two login entry points (/accounts/login/ and /login/) both render the
unified sign-in form, and legacy /login/<role>/ URLs redirect to /login/.
"""

from django.test import TestCase
from django.urls import reverse

from ..portals import PORTALS


class LoginRouteTests(TestCase):
    def test_accounts_login_route_renders_the_unified_form(self):
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/login.html")
        self.assertContains(response, 'name="username"')

    def test_custom_login_route_renders_the_unified_form(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/login.html")
        self.assertContains(response, 'name="username"')

    def test_each_legacy_portal_route_redirects_to_unified_login(self):
        for slug in PORTALS:
            with self.subTest(slug=slug):
                response = self.client.get(
                    reverse("accounts:role_login", kwargs={"role": slug})
                )
                self.assertRedirects(
                    response,
                    reverse("accounts:login"),
                    fetch_redirect_response=False,
                )
