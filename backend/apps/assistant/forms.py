"""apps/assistant/forms.py — admin screens for settings, providers, rules, console."""

from __future__ import annotations

from django import forms
from django.db.models import Q

from . import crypto
from .models import AssistantSettings, CustomGuardrailRule, ProviderConfig

KEY_HELP = (
    "Paste the key your provider gave you. It is encrypted before saving and is shown "
    "afterwards only as its last four characters."
)

SWITCH = forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"})
NUMBER = forms.NumberInput(attrs={"class": "form-control", "min": "0"})
MONEY = forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"})
TEXT = forms.TextInput(attrs={"class": "form-control"})
TIME = forms.TimeInput(attrs={"class": "form-control", "type": "time"})


class AssistantSettingsForm(forms.ModelForm):
    class Meta:
        model = AssistantSettings
        fields = (
            "enabled",
            "kill_switch",
            "public_enabled",
            "signed_in_enabled",
            "student_tutor_enabled",
            "default_provider",
            "house_rules",
            "monthly_budget_ksh",
            "usd_to_ksh",
            "daily_message_limit",
            "public_visitor_daily_limit",
            "public_ip_daily_limit",
            "public_site_daily_limit",
            "console_daily_limit",
            "console_monthly_budget_ksh",
            "safeguarding_contact_name",
            "safeguarding_contact_phone",
            "safeguarding_contact_email",
            "escalation_minutes",
            "working_hours_start",
            "working_hours_end",
            "alert_retention_months",
            "handoff_email",
            "widget_enabled",
            "widget_opening_en",
            "widget_opening_sw",
            "widget_max_message_chars",
            "widget_feedback_enabled",
            "widget_pow_enabled",
            "sla_normal_minutes",
            "sla_urgent_minutes",
            "sla_critical_minutes",
            "sla_reminder_percent",
            "conversation_retention_days",
            "handoff_retention_months",
            "callback_retention_days",
            "feedback_reason_retention_days",
            "consent_approved",
            "consent_approved_by",
            "consent_approved_on",
        )
        widgets = {
            "enabled": SWITCH,
            "kill_switch": SWITCH,
            "public_enabled": SWITCH,
            "signed_in_enabled": SWITCH,
            "student_tutor_enabled": SWITCH,
            "default_provider": forms.Select(attrs={"class": "form-select"}),
            "house_rules": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
            "monthly_budget_ksh": MONEY,
            "usd_to_ksh": MONEY,
            "daily_message_limit": NUMBER,
            "public_visitor_daily_limit": NUMBER,
            "public_ip_daily_limit": NUMBER,
            "public_site_daily_limit": NUMBER,
            "console_daily_limit": NUMBER,
            "console_monthly_budget_ksh": MONEY,
            "safeguarding_contact_name": TEXT,
            "safeguarding_contact_phone": TEXT,
            "safeguarding_contact_email": forms.EmailInput(attrs={"class": "form-control"}),
            "escalation_minutes": NUMBER,
            "working_hours_start": TIME,
            "working_hours_end": TIME,
            "alert_retention_months": NUMBER,
            "handoff_email": forms.EmailInput(attrs={"class": "form-control"}),
            "widget_enabled": SWITCH,
            "widget_opening_en": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "widget_opening_sw": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "widget_max_message_chars": NUMBER,
            "widget_feedback_enabled": SWITCH,
            "widget_pow_enabled": SWITCH,
            "sla_normal_minutes": NUMBER,
            "sla_urgent_minutes": NUMBER,
            "sla_critical_minutes": NUMBER,
            "sla_reminder_percent": NUMBER,
            "conversation_retention_days": NUMBER,
            "handoff_retention_months": NUMBER,
            "callback_retention_days": NUMBER,
            "feedback_reason_retention_days": NUMBER,
            "consent_approved": SWITCH,
            "consent_approved_by": TEXT,
            "consent_approved_on": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
        }
        labels = {
            "enabled": "Assistant switched on (public and signed-in)",
            "kill_switch": "STOP EVERYTHING NOW (stop switch)",
            "public_enabled": "Public assistant on the website",
            "signed_in_enabled": "Signed-in assistant",
            "student_tutor_enabled": "Student tutor mode",
            "default_provider": "Default provider (tried first)",
            "house_rules": "House rules",
            "monthly_budget_ksh": "Monthly budget, all providers (KSh)",
            "usd_to_ksh": "USD → KSh rate",
            "daily_message_limit": "Messages per signed-in person per day",
            "public_visitor_daily_limit": "Public: questions per visitor per day",
            "public_ip_daily_limit": "Public: questions per IP per day",
            "public_site_daily_limit": "Public: questions per day, whole site",
            "console_daily_limit": "Test tools: requests per day",
            "console_monthly_budget_ksh": "Test tools: monthly ceiling (KSh)",
            "safeguarding_contact_name": "Safeguarding contact name",
            "safeguarding_contact_phone": "Safeguarding contact phone",
            "safeguarding_contact_email": "Safeguarding contact email",
            "escalation_minutes": "Escalate an unacknowledged alert after (minutes)",
            "working_hours_start": "Working hours start",
            "working_hours_end": "Working hours end",
            "alert_retention_months": "DRAFT: erase a closed alert's excerpt after (months)",
            "handoff_email": "Handoff email",
            "widget_enabled": "Public chat widget on the website",
            "widget_opening_en": "Widget opening message (English)",
            "widget_opening_sw": "Widget opening message (Kiswahili)",
            "widget_max_message_chars": "Widget: longest message accepted (characters)",
            "widget_feedback_enabled": "Widget: ask whether an answer helped",
            "widget_pow_enabled": "Widget: bot check when a conversation starts",
            "sla_normal_minutes": "Normal handoffs: reply within (minutes)",
            "sla_urgent_minutes": "Urgent handoffs: reply within (minutes)",
            "sla_critical_minutes": "Critical handoffs: reply within (minutes)",
            "sla_reminder_percent": "Remind the assignee at (% of the target)",
            "conversation_retention_days": "DRAFT: delete a chat with no handoff after (days)",
            "handoff_retention_months": "DRAFT: delete a resolved handoff after (months)",
            "callback_retention_days": "DRAFT: delete callback details after (days)",
            "feedback_reason_retention_days": "DRAFT: delete feedback reasons after (days)",
            "consent_approved": "Consent wording approved",
            "consent_approved_by": "Approved by (name)",
            "consent_approved_on": "Approved on (date)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["default_provider"].queryset = ProviderConfig.objects.filter(
            Q(is_active=True) | Q(pk=self.instance.default_provider_id)
        ).order_by("priority", "name")
        self.fields["default_provider"].empty_label = "No default provider"

    def clean(self):
        cleaned = super().clean()
        needs_contact = cleaned.get("signed_in_enabled") or cleaned.get("student_tutor_enabled")
        name = (cleaned.get("safeguarding_contact_name") or "").strip()
        phone = (cleaned.get("safeguarding_contact_phone") or "").strip()
        email = (cleaned.get("safeguarding_contact_email") or "").strip()
        if needs_contact and not name:
            self.add_error(
                "safeguarding_contact_name",
                "A named safeguarding contact is required before the signed-in assistant "
                "or student tutor can be switched on.",
            )
        if name and not (phone or email):
            self.add_error(
                "safeguarding_contact_phone",
                "Give a phone number or an email address for the safeguarding contact.",
            )
        if cleaned.get("student_tutor_enabled") and not cleaned.get("signed_in_enabled"):
            self.add_error(
                "student_tutor_enabled",
                "Switch on the signed-in assistant before the student tutor mode.",
            )
        budget = cleaned.get("monthly_budget_ksh") or 0
        rate = cleaned.get("usd_to_ksh") or 0
        if budget > 0 and rate <= 0:
            self.add_error(
                "usd_to_ksh",
                "Set the USD → KSh rate; without it a positive budget cannot be tracked.",
            )
        approved = cleaned.get("consent_approved")
        by = (cleaned.get("consent_approved_by") or "").strip()
        on = cleaned.get("consent_approved_on")
        if approved and not (by and on):
            self.add_error(
                "consent_approved_by",
                "Record who approved the consent wording and the date.",
            )
        if not approved and (by or on):
            self.add_error(
                "consent_approved",
                "Tick “Consent wording approved” as well, or clear the name and date.",
            )

        if cleaned.get("widget_enabled"):
            if not cleaned.get("public_enabled"):
                self.add_error(
                    "widget_enabled",
                    "Switch on the public assistant before the website widget.",
                )
            if not (approved and by and on):
                self.add_error(
                    "widget_enabled",
                    "The widget may not appear until the consent wording is approved "
                    "with a name and a date.",
                )

        longest = cleaned.get("widget_max_message_chars")
        if longest is not None and not 50 <= longest <= 4000:
            self.add_error(
                "widget_max_message_chars",
                "Choose between 50 and 4000 characters.",
            )
        remind = cleaned.get("sla_reminder_percent")
        if remind is not None and not 1 <= remind <= 99:
            self.add_error(
                "sla_reminder_percent",
                "Choose a percentage between 1 and 99.",
            )
        for field in ("sla_normal_minutes", "sla_urgent_minutes", "sla_critical_minutes"):
            value = cleaned.get(field)
            if value is not None and value < 1:
                self.add_error(field, "A service-level target must be at least one minute.")
        return cleaned


class ProviderForm(forms.ModelForm):
    api_key = forms.CharField(
        required=False,
        strip=True,
        label="API key",
        help_text=KEY_HELP,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"class": "form-control", "autocomplete": "new-password"},
        ),
    )

    class Meta:
        model = ProviderConfig
        fields = (
            "name",
            "kind",
            "model_name",
            "fast_model_name",
            "embedding_model_name",
            "base_url",
            "timeout_seconds",
            "is_active",
            "priority",
            "input_usd_per_1m",
            "output_usd_per_1m",
            "embedding_usd_per_1m",
            "monthly_budget_ksh",
            "region_note",
        )
        widgets = {
            "name": TEXT,
            "kind": forms.Select(attrs={"class": "form-select"}),
            "model_name": TEXT,
            "fast_model_name": TEXT,
            "embedding_model_name": TEXT,
            "base_url": forms.URLInput(attrs={"class": "form-control"}),
            "timeout_seconds": forms.NumberInput(
                attrs={"class": "form-control", "min": "1", "max": "120"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "priority": forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
            "input_usd_per_1m": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.0001"}
            ),
            "output_usd_per_1m": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.0001"}
            ),
            "embedding_usd_per_1m": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.0001"}
            ),
            "monthly_budget_ksh": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.01"}
            ),
            "region_note": TEXT,
        }
        labels = {
            "model_name": "Chat model",
            "fast_model_name": "Fast/cheap model (public assistant)",
            "embedding_model_name": "Embedding model",
            "base_url": "Server address (base URL)",
            "timeout_seconds": "Timeout (seconds)",
            "is_active": "Available for use",
            "priority": "Fallback order (lower is earlier)",
            "input_usd_per_1m": "Input price, USD per 1M tokens",
            "output_usd_per_1m": "Output price, USD per 1M tokens",
            "embedding_usd_per_1m": "Embedding price, USD per 1M tokens",
            "monthly_budget_ksh": "Provider monthly cap (KSh, optional)",
            "region_note": "Where this provider processes data",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["base_url"].help_text = (
            "OpenAI: https://api.openai.com/v1 · DeepSeek: https://api.deepseek.com · "
            "Gemini: https://generativelanguage.googleapis.com/v1beta · "
            "OpenAI-compatible: the full base URL, including /v1 if your server uses it."
        )
        self.fields["embedding_model_name"].help_text = (
            "Changing this later requires re-indexing every knowledge document."
        )
        self.fields["region_note"].help_text = (
            "For example “United States” or “European Union”, as stated by the provider. "
            "Shown to the admin as a data-processing reminder."
        )
        if self.instance.pk and self.instance.has_key:
            self.fields["api_key"].help_text = (
                f"A key ending {self.instance.api_key_last4} is already saved. Leave this "
                "blank to keep it, or paste a new key to replace it."
            )

    def clean_api_key(self):
        return (self.cleaned_data.get("api_key") or "").strip()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("api_key") and not crypto.is_configured():
            self.add_error(
                "api_key",
                "This server has no encryption key configured, so a provider key cannot be "
                "saved safely. Ask your developer to set ASSISTANT_ENCRYPTION_KEY first.",
            )
        kind = cleaned.get("kind")
        if kind != ProviderConfig.Kind.FAKE:
            if not (cleaned.get("model_name") or "").strip():
                self.add_error("model_name", "Choose the chat model for this provider.")
            if kind == ProviderConfig.Kind.OPENAI_COMPATIBLE and not (cleaned.get("base_url") or "").strip():
                self.add_error("base_url", "Enter the server address for this provider.")
        return cleaned


class GuardrailRuleForm(forms.ModelForm):
    class Meta:
        model = CustomGuardrailRule
        fields = ("name", "category", "patterns", "note", "is_active")
        widgets = {
            "name": TEXT,
            "category": forms.Select(attrs={"class": "form-select"}),
            "patterns": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "patterns": "Trigger phrases (one per line)",
            "note": "Why this rule exists",
        }

    def clean_patterns(self):
        value = self.cleaned_data.get("patterns") or ""
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if not lines:
            raise forms.ValidationError("Add at least one phrase, one per line.")
        return "\n".join(lines)


class AlertCloseForm(forms.Form):
    outcome = forms.CharField(
        label="Outcome / action taken",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Who was contacted, what was done, what happens next…",
            }
        ),
    )

    def clean_outcome(self):
        value = (self.cleaned_data.get("outcome") or "").strip()
        if len(value) < 5:
            raise forms.ValidationError("Record what was done before closing the alert.")
        return value


class TestConsoleForm(forms.Form):
    provider = forms.ModelChoiceField(
        queryset=ProviderConfig.objects.none(),
        required=False,
        label="Provider to test",
        empty_label="Use the default provider",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    question = forms.CharField(
        label="Question",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 4, "placeholder": "Type a test question…"}
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["provider"].queryset = ProviderConfig.objects.filter(
            is_active=True
        ).order_by("priority", "name")

    def clean_question(self):
        value = (self.cleaned_data.get("question") or "").strip()
        if len(value) < 3:
            raise forms.ValidationError("Type a question to test.")
        return value
