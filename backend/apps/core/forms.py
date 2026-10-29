"""
apps/core/forms.py
------------------
Forms for the admin audit log viewer, activity pages (Module 1) and School
Settings.
"""

from __future__ import annotations

from django import forms

from apps.accounts.models import User

from .events import CATEGORIES, event_spec
from .models import AuditLog, SchoolSettings, SystemEvent


class AuditLogFilterForm(forms.Form):
    """Filter audit events by user, model, and date range."""

    actor = forms.ModelChoiceField(
        queryset=User.objects.filter(audit_logs__isnull=False)
        .distinct()
        .order_by("last_name", "first_name"),
        required=False,
        empty_label="All users",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    content_type = forms.ChoiceField(
        choices=[],
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control form-control-sm", "type": "date"}
        ),
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control form-control-sm", "type": "date"}
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        models = (
            AuditLog.objects.order_by("content_type")
            .values_list("content_type", flat=True)
            .distinct()
        )
        self.fields["content_type"].choices = [("", "All models")] + [
            (m, m) for m in models
        ]


class SystemEventFilterForm(forms.Form):
    """Filter the activity feed by category, event type and date range."""

    category = forms.ChoiceField(
        choices=[],
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    event_type = forms.ChoiceField(
        choices=[],
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control form-control-sm", "type": "date"}
        ),
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control form-control-sm", "type": "date"}
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only offer what has actually been recorded, labelled from the registry.
        categories = (
            SystemEvent.objects.exclude(category="")
            .values_list("category", flat=True)
            .distinct()
        )
        self.fields["category"].choices = [("", "All activity")] + [
            (slug, CATEGORIES.get(slug, slug)) for slug in sorted(categories)
        ]
        types = SystemEvent.objects.values_list("type", flat=True).distinct()
        self.fields["event_type"].choices = [("", "All types")] + [
            (value, event_spec(value)["label"]) for value in sorted(types)
        ]


class DailyReportForm(forms.Form):
    """Choose which day the admin daily report summarises."""

    date = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control form-control-sm", "type": "date"}
        ),
    )


class SchoolSettingsForm(forms.ModelForm):
    """
    Admin edit of the SchoolSettings singleton.

    Identity fields are optional: a blank value means "use the SCHOOL_* setting
    from the environment", which the context processor resolves for templates.
    """

    class Meta:
        model = SchoolSettings
        fields = [
            "name",
            "motto",
            "phone",
            "email",
            "currency",
            "show_positions",
            "fees_enabled",
            "logo",
            "address",
            "established_on",
            "about",
            "mission",
            "vision",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "motto": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "currency": forms.TextInput(
                attrs={"class": "form-control", "maxlength": "8", "placeholder": "GHS"}
            ),
            "address": forms.Textarea(
                attrs={"class": "form-control", "rows": 2, "placeholder": "P.O. Box, Town"}
            ),
            "established_on": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"
            ),
            "about": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
            "mission": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "vision": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            # ClearableFileInput is Django's default for an ImageField and it is
            # what makes "submit the form again without touching the logo box"
            # keep the current file instead of wiping it.
            "logo": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A ModelForm marks these required because the model fields have no
        # blank=True, which would make an unchecked box a validation error
        # instead of the value False.
        for checkbox in ("show_positions", "fees_enabled"):
            self.fields[checkbox].required = False

    def clean_currency(self) -> str:
        return self.cleaned_data["currency"].strip().upper()
