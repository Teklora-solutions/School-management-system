"""
apps/core/tests/test_features.py
--------------------------------
The feature registry the Director portal asks before it renders a finance
screen. The rule worth pinning is the one the Director spec is strict about: a
switched-off feature reports as off, and nothing in this layer is allowed to
answer "yes" because the data merely happens to exist.
"""

from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from apps.core.features import (
    FEATURES,
    UnknownFeature,
    feature_disabled_message,
    feature_enabled,
)
from apps.core.models import SchoolSettings


class FeatureEnabledTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.accounts.models import User

        cls.admin = User.objects.create_user(
            username="feature_admin",
            email="feature_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )

    def test_a_feature_is_on_by_default(self):
        """A fresh install has no SchoolSettings row; the switches read on."""
        self.assertEqual(SchoolSettings.objects.count(), 0)
        self.assertTrue(feature_enabled("fees"))

    def test_the_saved_switch_wins(self):
        row = SchoolSettings.load()
        row.fees_enabled = False
        row.save()

        self.assertFalse(feature_enabled("fees"))

    def test_unchecking_the_box_is_what_turns_it_off(self):
        """The Admin settings page writes the field; the registry only reads it."""
        self.post_settings(False)
        self.assertFalse(feature_enabled("fees"))
        self.post_settings(True)
        self.assertTrue(feature_enabled("fees"))

    def post_settings(self, fees_enabled: bool) -> None:
        from django.urls import reverse

        self.client.force_login(self.admin)
        payload = {
            "name": "Feature School",
            "motto": "",
            "phone": "",
            "email": "",
            "currency": "GHS",
        }
        if fees_enabled:
            payload["fees_enabled"] = "on"
        self.client.post(reverse("core_school:settings"), payload)

    def test_an_unregistered_slug_is_a_coding_mistake_not_a_disabled_feature(self):
        """Silently returning False would render a permanent placeholder nobody debugs."""
        with self.assertRaises(UnknownFeature):
            feature_enabled("transport")

    def test_every_registered_field_exists_on_the_model(self):
        """A typo in FEATURES would raise AttributeError only on that screen."""
        for slug, spec in FEATURES.items():
            with self.subTest(slug=slug):
                self.assertTrue(
                    hasattr(SchoolSettings, spec["field"]),
                    f"{slug} points at a missing SchoolSettings field",
                )

    def test_every_registered_field_is_exposed_by_the_settings_form(self):
        """A switch the Admin cannot tick is a switch that stays stuck on."""
        from apps.core.forms import SchoolSettingsForm

        for slug, spec in FEATURES.items():
            with self.subTest(slug=slug):
                self.assertIn(spec["field"], SchoolSettingsForm.Meta.fields)

    def test_a_disabled_feature_never_reads_the_database_for_its_data(self):
        """
        The registry answers from the settings row alone. Callers get a boolean,
        so a placeholder screen costs one query rather than the aggregate it was
        going to skip.
        """
        SchoolSettings.load()
        with self.assertNumQueries(1):
            feature_enabled("fees")

    def test_asking_twice_on_one_request_costs_one_read(self):
        """
        The sidebar checks every entry that carries a feature slug, so the
        switch has to ride the same per-request memo the currency does —
        otherwise a menu costs one query per line item.
        """
        from django.test import RequestFactory

        SchoolSettings.load()
        request = RequestFactory().get("/")
        with self.assertNumQueries(1):
            feature_enabled("fees", request=request)
            feature_enabled("fees", request=request)


class FeatureDisabledMessageTests(SimpleTestCase):
    """
    The other half of a switch: what a screen says when the answer is no.

    A placeholder is only honest if it is a sentence. An empty string renders
    the same blank card as a feature that failed to load, and the difference
    between "not enabled" and "broken" is the difference between calling the
    Bursar and calling IT.
    """

    def test_an_off_feature_has_a_sentence_to_print(self):
        for slug in FEATURES:
            with self.subTest(slug=slug):
                message = feature_disabled_message(slug)
                self.assertTrue(message.strip())
                self.assertTrue(message.endswith("."))

    def test_the_message_comes_from_the_registry(self):
        self.assertEqual(
            feature_disabled_message("fees"), FEATURES["fees"]["disabled_message"]
        )

    def test_reading_the_message_for_an_unregistered_slug_is_a_mistake(self):
        """A silent default would let a typo pass as a switched-off module."""
        with self.assertRaises(UnknownFeature):
            feature_disabled_message("transport")
