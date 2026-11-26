"""
The two login entry points (/accounts/login/ and /login/) both render the
unified sign-in form, and legacy per-role login URLs are removed.
"""

from django.test import TestCase


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

    def test_legacy_per_role_login_routes_are_removed(self):
        for slug in ("admin", "director", "teacher", "student", "finance", "parent"):
            with self.subTest(slug=slug):
                response = self.client.get(f"/login/{slug}/")
                self.assertEqual(response.status_code, 404)
