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


#: Which tab of the settings page holds each field. The view uses the first
#: errored field to reopen the right tab after an invalid save.
FIELD_TABS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "identity",
        "Identity",
        (
            "name", "motto", "phone", "email", "currency", "logo",
            "address", "established_on",
        ),
    ),
    (
        "hero",
        "Home Hero",
        (
            "hero_eyebrow", "hero_title", "hero_title_highlight",
            "hero_text", "cta_title", "cta_text",
        ),
    ),
    ("about", "About Page", ("about", "mission", "vision", "values")),
    ("appearance", "Appearance", ("theme", "footer_about")),
    (
        "social",
        "Social & Contact",
        (
            "facebook_url", "instagram_url", "youtube_url", "linkedin_url",
            "admissions_email", "office_hours", "map_embed_url",
        ),
    ),
    (
        "modules",
        "Modules",
        (
            "show_positions", "fees_enabled", "learning_enabled",
            "engagement_enabled", "quizzes_enabled", "live_classes_enabled",
            "feed_community_enabled", "notification_quiet_hours_enabled",
            "quiet_hours_start", "quiet_hours_end", "feed_teacher_starters",
            "feed_student_starters", "homework_grading_enabled",
            "homework_late_penalty_enabled",
        ),
    ),
)

FIELD_TAB_BY_NAME: dict[str, tuple[str, str]] = {
    field: (slug, label)
    for slug, label, fields in FIELD_TABS
    for field in fields
}


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
            "notification_quiet_hours_enabled",
            "quiet_hours_start",
            "quiet_hours_end",
            "feed_teacher_starters",
            "feed_student_starters",
            "homework_grading_enabled",
            "homework_late_penalty_enabled",
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
            "quiet_hours_start": forms.NumberInput(
                attrs={"class": "form-control", "min": 0, "max": 23, "step": 1}
            ),
            "quiet_hours_end": forms.NumberInput(
                attrs={"class": "form-control", "min": 0, "max": 23, "step": 1}
            ),
            "feed_teacher_starters": forms.Textarea(
                attrs={"class": "form-control", "rows": 6}
            ),
            "feed_student_starters": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
            # ClearableFileInput keeps the current file when no new file is chosen.
            "logo": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for checkbox in ("show_positions", "fees_enabled", "learning_enabled", "engagement_enabled", "quizzes_enabled", "live_classes_enabled", "feed_community_enabled", "notification_quiet_hours_enabled"):
            self.fields[checkbox].required = False
        # Integer fields: a POST that omits them (older tests, partial forms)
        # must keep the model default rather than saving NULL.
        for int_field in ("quiet_hours_start", "quiet_hours_end"):
            self.fields[int_field].required = False
            self.fields[int_field].initial = self._meta.model._meta.get_field(
                int_field
            ).default

    def _hour_or_default(self, field_name: str) -> int:
        value = self.cleaned_data.get(field_name)
        if value is None:
            return self._meta.model._meta.get_field(field_name).default
        return value

    def clean_quiet_hours_start(self) -> int:
        return self._hour_or_default("quiet_hours_start")

    def clean_quiet_hours_end(self) -> int:
        return self._hour_or_default("quiet_hours_end")

    def clean_currency(self) -> str:
        return self.cleaned_data["currency"].strip().upper()

    @staticmethod
    def _clean_starters(raw: str, label: str) -> str:
        """
        Starters are inserted into a child's reply as plain text, so a
        bracketed placeholder like ``[topic]`` would be typed into the reply
        box and sent. Refuse it at the settings page with a clear message.
        """
        if "[" in raw or "]" in raw:
            raise forms.ValidationError(
                f"Remove the [placeholder] from the {label} starters. "
                "Starters are typed into a pupil's reply exactly as written."
            )
        return raw

    def clean_feed_teacher_starters(self) -> str:
        return self._clean_starters(
            self.cleaned_data.get("feed_teacher_starters", ""),
            "teacher",
        )

    def clean_feed_student_starters(self) -> str:
        return self._clean_starters(
            self.cleaned_data.get("feed_student_starters", ""),
            "pupil",
        )
