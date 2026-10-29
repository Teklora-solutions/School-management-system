"""
apps/core/tests/test_axes_lockout.py
------------------------------------
A lockout replaces whatever login page a person was on, so it has to be
readable by a person. Programmatic callers still get structured JSON.
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from ..utils import axes_lockout_response


class AxesLockoutResponseTests(TestCase):
    """``TestCase`` not ``SimpleTestCase``: the school-settings context
    processor reads the database on every render."""
    def setUp(self):
        self.factory = RequestFactory()

    def lock_out(self, **request_kwargs):
        request = self.factory.post("/login/director/", **request_kwargs)
        # RequestFactory skips the middleware that would have attached a user,
        # and the auth context processor needs one to render.
        request.user = AnonymousUser()
        return axes_lockout_response(request, credentials={})

    def test_a_browser_gets_a_page_not_a_json_error(self):
        response = self.lock_out(HTTP_ACCEPT="text/html,application/xhtml+xml,*/*;q=0.8")
        self.assertEqual(response.status_code, 403)
        self.assertIn("text/html", response["Content-Type"])
        self.assertIn(b"Too many sign-in attempts", response.content)
        self.assertIn(b"/login/", response.content)

    def test_an_ajax_caller_still_gets_json(self):
        response = self.lock_out(HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 403)
        self.assertIn("application/json", response["Content-Type"])

    def test_a_json_body_still_gets_json(self):
        response = self.lock_out(content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("application/json", response["Content-Type"])
