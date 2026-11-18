"""apps/assistant/forms.py — admin screens for assistant settings and providers."""

from __future__ import annotations

from django import forms
from django.db.models import Q

from . import crypto
from .models import AssistantSettings, ProviderConfig

KEY_HELP = (
    "Paste the key your provider gave you. It is encrypted before saving and "
    "is shown afterwards only as its last four characters."
)


class AssistantSettingsForm(forms.ModelForm):
    class Meta:
        model = AssistantSettings
        fields = (
            "enabled",
            "default_provider",
            "house_rules",
            "monthly_budget_ksh",
            "daily_message_limit",
            "handoff_email",
        )
        widgets = {
            "enabled": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
            "default_provider": forms.Select(attrs={"class": "form-select"}),
            "house_rules": forms.Textarea(attrs={"class": "form-control", "rows": 6}),
            "monthly_budget_ksh": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.01"}
            ),
            "daily_message_limit": forms.NumberInput(
                attrs={"class": "form-control", "min": "0"}
            ),
            "handoff_email": forms.EmailInput(attrs={"class": "form-control"}),
        }
        labels = {
            "enabled": "Assistant switched on",
            "default_provider": "Default provider",
            "house_rules": "House rules",
            "monthly_budget_ksh": "Monthly budget (KSh)",
            "daily_message_limit": "Messages per person per day",
            "handoff_email": "Handoff email",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # An inactive provider is not offered, but a provider that was the
        # default before it was switched off stays selectable so saving this
        # page cannot silently change the school's choice.
        self.fields["default_provider"].queryset = ProviderConfig.objects.filter(
            Q(is_active=True) | Q(pk=self.instance.default_provider_id)
        ).order_by("name")
        self.fields["default_provider"].empty_label = "No default provider"


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
        fields = ("name", "kind", "model_name", "base_url", "timeout_seconds", "is_active")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "kind": forms.Select(attrs={"class": "form-select"}),
            "model_name": forms.TextInput(attrs={"class": "form-control"}),
            "base_url": forms.URLInput(attrs={"class": "form-control"}),
            "timeout_seconds": forms.NumberInput(
                attrs={"class": "form-control", "min": "1", "max": "120"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "model_name": "Model name",
            "base_url": "Server address (optional)",
            "timeout_seconds": "Timeout (seconds)",
            "is_active": "Available for use",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.has_key:
            self.fields["api_key"].help_text = (
                f"A key ending {self.instance.api_key_last4} is already saved. "
                "Leave this blank to keep it, or paste a new key to replace it."
            )
        elif not self.instance.pk:
            self.fields["model_name"].help_text = (
                "Optional for the fake provider; required once real providers arrive."
            )

    def clean_api_key(self):
        return (self.cleaned_data.get("api_key") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("api_key") and not crypto.is_configured():
            self.add_error(
                "api_key",
                "This server has no encryption key configured, so a provider key "
                "cannot be saved safely. Ask your developer to set "
                "ASSISTANT_ENCRYPTION_KEY first.",
            )
        return cleaned_data
