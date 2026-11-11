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

    Split into tab-groups in the template: Identity, Website Content, Appearance,
    Social & Contact. Each group surfaces the relevant subset of fields so the
    form doesn't become an intimidating wall of inputs.

    Identity fields are optional: a blank value means "use the SCHOOL_* setting
    from the environment", which the context processor resolves for templates.
    """

    class Meta:
        model = SchoolSettings
        fields = [
            # ── Identity ──────────────────────────────────────────────────
            "name",
            "motto",
            "phone",
            "email",
            "currency",
            "show_positions",
            "fees_enabled",
            "learning_enabled",
            "engagement_enabled",
            "quizzes_enabled",
            "live_classes_enabled",
            "feed_community_enabled",
            "logo",
            "address",
            "established_on",
            # ── About page content ────────────────────────────────────────
            "about",
            "mission",
            "vision",
            "values",
            # ── Home hero ─────────────────────────────────────────────────
            "hero_eyebrow",
            "hero_title",
            "hero_title_highlight",
            "hero_text",
            # ── Home CTA band ─────────────────────────────────────────────
            "cta_title",
            "cta_text",
            # ── Appearance ────────────────────────────────────────────────
            "theme",
            # ── Footer ────────────────────────────────────────────────────
            "footer_about",
            # ── Social links ──────────────────────────────────────────────
            "facebook_url",
            "instagram_url",
            "youtube_url",
            "linkedin_url",
            # ── Contact / Admissions extras ───────────────────────────────
            "admissions_email",
            "office_hours",
            "map_embed_url",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "motto": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "admissions_email": forms.EmailInput(attrs={"class": "form-control"}),
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
            "values": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "hero_eyebrow": forms.TextInput(attrs={"class": "form-control"}),
            "hero_title": forms.TextInput(attrs={"class": "form-control"}),
            "hero_title_highlight": forms.TextInput(attrs={"class": "form-control"}),
            "hero_text": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "cta_title": forms.TextInput(attrs={"class": "form-control"}),
            "cta_text": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "theme": forms.Select(
                choices=[("dark", "Dark"), ("light", "Light")],
                attrs={"class": "form-select"},
            ),
            "footer_about": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "facebook_url": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://facebook.com/yourschool"}),
            "instagram_url": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://instagram.com/yourschool"}),
            "youtube_url": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://youtube.com/@yourschool"}),
            "linkedin_url": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://linkedin.com/school/yourschool"}),
            "office_hours": forms.TextInput(attrs={"class": "form-control", "placeholder": "Monday – Friday, 8:00 AM – 4:00 PM"}),
            "map_embed_url": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://maps.google.com/..."}),
            # ClearableFileInput keeps the current file when no new file is chosen.
            "logo": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for checkbox in ("show_positions", "fees_enabled", "learning_enabled", "engagement_enabled", "quizzes_enabled", "live_classes_enabled"):
            self.fields[checkbox].required = False

    def clean_currency(self) -> str:
        return self.cleaned_data["currency"].strip().upper()
