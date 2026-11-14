"""Cache-busting version on unhashed static asset URLs."""

import os

from django.contrib.staticfiles import finders
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


class StaticVersionTagTests(SimpleTestCase):
    def test_existing_asset_carries_its_modification_time(self):
        rendered = Template(
            "{% load core_extras %}{% static_v 'css/base.css' %}"
        ).render(Context())
        absolute = finders.find("css/base.css")
        self.assertIsNotNone(absolute)
        self.assertEqual(
            rendered,
            f"/static/css/base.css?v={int(os.path.getmtime(absolute))}",
        )

    def test_missing_asset_falls_back_to_the_plain_url(self):
        rendered = Template(
            "{% load core_extras %}{% static_v 'css/no-such-file.css' %}"
        ).render(Context())
        self.assertEqual(rendered, "/static/css/no-such-file.css")


class PortalAssetVersionTests(TestCase):
    def test_login_page_requests_a_fresh_copy_of_the_stylesheet(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "css/base.css?v=")
