"""apps/assistant/tests/test_forms.py — validation that protects go-live."""

from __future__ import annotations

from django.test import TestCase, override_settings

from apps.assistant.forms import (
    AlertCloseForm,
    AssistantSettingsForm,
    ProviderForm,
)
from apps.assistant.models import AssistantSettings


def settings_data(**overrides):
    data = {
        "enabled": "",
        "public_enabled": "",
        "signed_in_enabled": "",
        "student_tutor_enabled": "",
        "default_provider": "",
        "house_rules": "",
        "monthly_budget_ksh": "0",
        "usd_to_ksh": "0",
        "daily_message_limit": "20",
        "public_visitor_daily_limit": "10",
        "public_ip_daily_limit": "30",
        "public_site_daily_limit": "300",
        "console_daily_limit": "50",
        "console_monthly_budget_ksh": "500",
        "safeguarding_contact_name": "",
        "safeguarding_contact_phone": "",
        "safeguarding_contact_email": "",
        "escalation_minutes": "15",
        "working_hours_start": "07:00",
        "working_hours_end": "17:00",
        "alert_retention_months": "12",
        "handoff_email": "",
        "widget_max_message_chars": "1000",
        "sla_normal_minutes": "480",
        "sla_urgent_minutes": "240",
        "sla_critical_minutes": "15",
        "sla_reminder_percent": "75",
        "conversation_retention_days": "90",
        "handoff_retention_months": "12",
        "callback_retention_days": "30",
        "feedback_reason_retention_days": "90",
        "consent_approved": "",
        "consent_approved_by": "",
        "consent_approved_on": "",
    }
    data.update(overrides)
    return data


def settings_form(**overrides):
    return AssistantSettingsForm(data=settings_data(**overrides), instance=AssistantSettings.load())


class AssistantSettingsFormTests(TestCase):
    def test_the_plain_defaults_are_valid(self):
        self.assertTrue(settings_form().is_valid(), settings_form().errors)

    def test_signed_in_mode_needs_a_named_contact(self):
        form = settings_form(signed_in_enabled="on")
        self.assertFalse(form.is_valid())
        self.assertIn("safeguarding_contact_name", form.errors)

    def test_student_tutor_needs_signed_in_mode(self):
        form = settings_form(
            student_tutor_enabled="on",
            safeguarding_contact_name="Mrs Achieng",
            safeguarding_contact_phone="0722000111",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("student_tutor_enabled", form.errors)

    def test_a_named_contact_needs_a_phone_or_email(self):
        form = settings_form(safeguarding_contact_name="Mrs Achieng")
        self.assertFalse(form.is_valid())
        self.assertIn("safeguarding_contact_phone", form.errors)

    def test_a_positive_budget_needs_the_usd_rate(self):
        form = settings_form(monthly_budget_ksh="1500", usd_to_ksh="0")
        self.assertFalse(form.is_valid())
        self.assertIn("usd_to_ksh", form.errors)

    def test_consent_approval_needs_a_name_and_date(self):
        form = settings_form(consent_approved="on")
        self.assertFalse(form.is_valid())
        self.assertIn("consent_approved_by", form.errors)

    def test_a_name_without_the_approval_flag_is_refused(self):
        form = settings_form(consent_approved_by="Director")
        self.assertFalse(form.is_valid())
        self.assertIn("consent_approved", form.errors)


class AlertCloseFormTests(TestCase):
    def test_an_outcome_is_required(self):
        form = AlertCloseForm(data={"outcome": "   "})
        self.assertFalse(form.is_valid())
        self.assertIn("outcome", form.errors)

    def test_a_real_outcome_passes(self):
        form = AlertCloseForm(
            data={"outcome": "Called the parent, referred to the counsellor."}
        )
        self.assertTrue(form.is_valid(), form.errors)


class ProviderFormTests(TestCase):
    def data(self, **overrides):
        data = {
            "name": "Main",
            "kind": "openai",
            "model_name": "gpt-test",
            "fast_model_name": "",
            "embedding_model_name": "",
            "base_url": "",
            "timeout_seconds": "30",
            "is_active": "on",
            "priority": "100",
            "input_usd_per_1m": "0",
            "output_usd_per_1m": "0",
            "embedding_usd_per_1m": "0",
            "monthly_budget_ksh": "",
            "region_note": "",
            "api_key": "sk-test-not-a-real-key-0000",
        }
        data.update(overrides)
        return data

    def test_a_real_provider_needs_a_model(self):
        form = ProviderForm(data=self.data(model_name=""))
        self.assertFalse(form.is_valid())
        self.assertIn("model_name", form.errors)

    def test_an_openai_compatible_provider_needs_a_base_url(self):
        form = ProviderForm(data=self.data(kind="openai_compatible", base_url=""))
        self.assertFalse(form.is_valid())
        self.assertIn("base_url", form.errors)

    def test_the_fake_provider_needs_neither_model_nor_key(self):
        form = ProviderForm(
            data=self.data(kind="fake", model_name="", api_key="")
        )
        self.assertTrue(form.is_valid(), form.errors)

    @override_settings(ASSISTANT_ENCRYPTION_KEY="")
    def test_a_key_without_server_encryption_is_refused(self):
        form = ProviderForm(data=self.data())
        self.assertFalse(form.is_valid())
        self.assertIn("api_key", form.errors)


class WidgetSettingsFormTests(TestCase):
    def test_the_widget_cannot_switch_on_without_the_public_switch(self):
        form = settings_form(widget_enabled="on")
        self.assertFalse(form.is_valid())
        self.assertIn("widget_enabled", form.errors)

    def test_the_widget_needs_approved_consent_wording(self):
        form = settings_form(public_enabled="on", widget_enabled="on")
        self.assertFalse(form.is_valid())
        self.assertIn("widget_enabled", form.errors)

    def test_the_widget_can_switch_on_once_public_and_consent_are_in_place(self):
        form = settings_form(
            public_enabled="on",
            widget_enabled="on",
            consent_approved="on",
            consent_approved_by="School Director",
            consent_approved_on="2026-11-18",
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_zero_service_level_target_is_refused(self):
        form = settings_form(sla_critical_minutes="0")
        self.assertFalse(form.is_valid())
        self.assertIn("sla_critical_minutes", form.errors)

    def test_a_reminder_percentage_outside_the_range_is_refused(self):
        form = settings_form(sla_reminder_percent="100")
        self.assertFalse(form.is_valid())
        self.assertIn("sla_reminder_percent", form.errors)

    def test_a_very_short_message_limit_is_refused(self):
        form = settings_form(widget_max_message_chars="10")
        self.assertFalse(form.is_valid())
        self.assertIn("widget_max_message_chars", form.errors)
