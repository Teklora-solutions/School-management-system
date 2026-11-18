"""
apps/assistant/tests/test_widget_ui.py
--------------------------------------
The public widget inside the public site (Module 3, CP3).

These tests do not drive a browser. They prove what the server guarantees:

  * a school with the widget off (the default) ships no widget markup, no
    stylesheet and no script to a visitor;
  * the widget appears on the five public pages, once, only when every gate
    (kill switch, enabled, public, widget, approved consent, a visitor limit)
    is open;
  * staff dashboard pages never carry it;
  * the mount decision is cached and cleared by a settings save, so the public
    query budget is not quietly spent on every page view;
  * the markup keeps the accessibility and safety promises (labelled controls,
    a live status region, two honeypots the JS and the server agree on, no
    inline styles, no HTML injection in the script);
  * every interface word exists in both English and Kiswahili.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.assistant.models import (
    WIDGET_GATE_CACHE_KEY,
    AssistantSettings,
)
from apps.dashboards.tests.fixtures import make_user

TEMPLATES = Path(settings.TEMPLATES[0]["DIRS"][0])
STATIC = Path(settings.STATICFILES_DIRS[0])
WIDGET_TEMPLATE = TEMPLATES / "features" / "assistant" / "widget.html"
WIDGET_JS = STATIC / "js" / "assistant_widget.js"
WIDGET_CSS = STATIC / "css" / "assistant_widget.css"
PUBLIC_BASE = TEMPLATES / "public" / "base.html"

PUBLIC_PAGES = ("home", "about", "academics", "gallery", "contact")


def enable_widget(**overrides):
    row = AssistantSettings.load()
    row.enabled = True
    row.public_enabled = True
    row.widget_enabled = True
    row.consent_approved = True
    row.consent_approved_by = "Test Head Teacher"
    row.consent_approved_on = datetime.date(2026, 11, 18)
    row.public_visitor_daily_limit = 10
    for field, value in overrides.items():
        setattr(row, field, value)
    row.save()
    return row


class MountTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def body(self, url_name="public:home"):
        return self.client.get(reverse(url_name)).content.decode("utf-8")

    def test_a_new_school_ships_nothing(self):
        body = self.body()
        self.assertNotIn('id="assistant-widget"', body)
        self.assertNotIn("assistant_widget.css", body)
        self.assertNotIn("assistant_widget.js", body)

    def test_every_open_gate_mounts_the_widget_once(self):
        enable_widget()
        body = self.body()
        self.assertEqual(body.count('id="assistant-widget"'), 1)
        self.assertIn("css/assistant_widget.css?v=", body)
        self.assertIn("js/assistant_widget.js?v=", body)
        self.assertIn(reverse("assistant:chat_state"), body)
        self.assertIn(reverse("assistant:chat_send"), body)
        self.assertIn("hidden", body.split('id="aw-launcher"')[1].split(">")[0])

    def test_the_widget_is_on_all_five_public_pages(self):
        enable_widget()
        for name in PUBLIC_PAGES:
            with self.subTest(page=name):
                self.assertIn('id="assistant-widget"', self.body(f"public:{name}"))

    def test_staff_dashboards_never_carry_it(self):
        enable_widget()
        staff = make_user("admin", username="widget_admin")
        self.client.force_login(staff)
        body = self.client.get(reverse("dashboards:admin_home")).content.decode("utf-8")
        self.assertNotIn("assistant-widget", body)

    def test_each_gate_alone_keeps_it_off(self):
        cases = {
            "kill_switch": True,
            "enabled": False,
            "public_enabled": False,
            "widget_enabled": False,
            "consent_approved": False,
            "public_visitor_daily_limit": 0,
        }
        for field, value in cases.items():
            with self.subTest(gate=field):
                cache.clear()
                enable_widget(**{field: value})
                self.assertNotIn('id="assistant-widget"', self.body())

    def test_saving_the_settings_clears_the_cached_decision(self):
        self.assertNotIn('id="assistant-widget"', self.body())
        enable_widget()
        self.assertIn('id="assistant-widget"', self.body())
        row = AssistantSettings.load()
        row.widget_enabled = False
        row.save()
        self.assertNotIn('id="assistant-widget"', self.body())

    def test_the_decision_costs_one_query_a_minute_not_one_a_page(self):
        enable_widget()
        with CaptureQueriesContext(connections["default"]) as first:
            self.client.get(reverse("public:home"))
        with CaptureQueriesContext(connections["default"]) as second:
            self.client.get(reverse("public:home"))
        settings_reads = [
            query for query in first.captured_queries
            if "assistant_assistantsettings" in query["sql"]
        ]
        self.assertEqual(len(settings_reads), 1)
        self.assertFalse(
            [
                query for query in second.captured_queries
                if "assistant_assistantsettings" in query["sql"]
            ]
        )
        self.assertEqual(cache.get(WIDGET_GATE_CACHE_KEY), True)


class MarkupContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = WIDGET_TEMPLATE.read_text(encoding="utf-8")
        cls.js = WIDGET_JS.read_text(encoding="utf-8")
        cls.css = WIDGET_CSS.read_text(encoding="utf-8")

    def test_the_base_template_includes_it_exactly_once(self):
        source = PUBLIC_BASE.read_text(encoding="utf-8")
        self.assertEqual(
            source.count('{% include "features/assistant/widget.html" %}'), 1
        )

    def test_no_dashboard_shell_mounts_it(self):
        for path in sorted((TEMPLATES / "dashboards").rglob("*.html")):
            with self.subTest(template=path.name):
                self.assertNotIn("features/assistant/widget.html", path.read_text(encoding="utf-8"))

    def test_the_script_never_writes_html(self):
        self.assertNotIn("innerHTML", self.js)
        self.assertNotIn("outerHTML", self.js)
        self.assertNotIn("insertAdjacentHTML", self.js)
        self.assertNotIn("document.write", self.js)
        self.assertIn("textContent", self.js)

    def test_nothing_is_loaded_from_another_site(self):
        for name, source in (("js", self.js), ("css", self.css), ("template", self.template)):
            with self.subTest(asset=name):
                self.assertNotIn("http://", source)
                self.assertNotIn("https://", source)

    def test_the_dialog_and_live_region_are_labelled(self):
        self.assertIn('role="dialog"', self.template)
        self.assertIn('aria-labelledby="aw-title"', self.template)
        self.assertNotIn('aria-modal="true"', self.template)
        self.assertIn('role="log"', self.template)
        self.assertIn('role="status"', self.template)
        self.assertIn('aria-live="polite"', self.template)
        self.assertIn('aria-expanded="false"', self.template)
        self.assertIn('aria-controls="aw-panel"', self.template)

    def test_the_launcher_keeps_a_name_when_its_label_is_hidden(self):
        self.assertIn('aria-label="Ask the school"', self.template)
        self.assertIn('data-i18n-label="askSchool"', self.template)
        self.assertIn(".aw-launcher__label {\n    display: none;", self.css)
        self.assertIn("[data-i18n-label]", self.js)

    def test_the_honeypots_match_what_the_views_read(self):
        self.assertEqual(self.template.count('name="website"'), 2)
        self.assertEqual(self.template.count('class="aw-hp"'), 2)
        self.assertGreaterEqual(self.template.count('tabindex="-1"'), 2)
        self.assertGreaterEqual(self.template.count('autocomplete="off"'), 2)

    def test_a_visitor_can_type_a_question_from_a_keyboard(self):
        self.assertIn('for="aw-input"', self.template)
        self.assertIn('name="message"', self.template)
        self.assertIn("'Escape'", self.js)
        self.assertIn("event.shiftKey", self.js)
        self.assertIn("els.input.focus()", self.js)

    def test_the_template_has_no_inline_styles(self):
        self.assertNotRegex(self.template, r"\bstyle\s*=")
        self.assertNotIn("<style", self.template)

    def test_the_feedback_chips_name_their_words(self):
        self.assertIn("chip('helpful'", self.js)
        self.assertNotIn("chip(t(", self.js)

    def test_the_first_load_draws_the_suggested_questions(self):
        start = self.js.index("function showSuggestions()")
        body = self.js[start : self.js.index("\n  }", start)]
        self.assertIn("renderSuggestions()", body)

    def test_the_script_speaks_the_server_event_names(self):
        for marker in ("'start'", "'delta'", "'done'"):
            with self.subTest(event=marker):
                self.assertIn(marker, self.js)

    def test_both_languages_define_the_same_words(self):
        english = re.search(r"en: \{(.*?)\n    \},", self.js, re.S)
        swahili = re.search(r"sw: \{(.*?)\n    \}\n  \};", self.js, re.S)
        self.assertIsNotNone(english)
        self.assertIsNotNone(swahili)
        en_keys = set(re.findall(r"^\s+([a-zA-Z]+):", english.group(1), re.M))
        sw_keys = set(re.findall(r"^\s+([a-zA-Z]+):", swahili.group(1), re.M))
        self.assertEqual(en_keys, sw_keys)
        used = set(re.findall(r'data-i18n(?:-label|-placeholder)?="([a-zA-Z]+)"', self.template))
        self.assertTrue(used)
        self.assertEqual(used - en_keys, set(), "the template uses a word the script does not define")
