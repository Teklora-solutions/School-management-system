"""
apps/learning/live_class_forms.py
---------------------------------
Form for scheduling and updating live class sessions.
"""

from __future__ import annotations

from django import forms
from django.utils import timezone

from apps.learning.live_class_utils import normalize_meeting_url, validate_meeting_url
from apps.learning.models import LiveClassSession


class LiveClassSessionForm(forms.ModelForm):
    scheduled_start = forms.DateTimeField(
        widget=forms.DateTimeInput(
            attrs={
                "class": "form-control",
                "type": "datetime-local",
            },
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        help_text="Date and time when the live session starts.",
    )

    class Meta:
        model = LiveClassSession
        fields = [
            "title",
            "description",
            "platform",
            "meeting_url",
            "meeting_id",
            "passcode",
            "scheduled_start",
            "duration_minutes",
        ]
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. Chapter 4 Problem Solving & Review"}
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "What will be covered, any questions students should prepare, etc.",
                }
            ),
            "platform": forms.Select(attrs={"class": "form-select"}),
            "meeting_url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "https://meet.google.com/... or https://zoom.us/j/...",
                }
            ),
            "meeting_id": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Optional meeting ID"}
            ),
            "passcode": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Optional passcode / password"}
            ),
            "duration_minutes": forms.NumberInput(
                attrs={"class": "form-control", "min": 5, "max": 480}
            ),
        }

    def clean_meeting_url(self) -> str:
        url = self.cleaned_data.get("meeting_url", "")
        platform = self.cleaned_data.get("platform", "google_meet")
        norm_url = normalize_meeting_url(url)
        valid, err_msg = validate_meeting_url(norm_url, platform)
        if not valid:
            raise forms.ValidationError(err_msg)
        return norm_url

    def clean_scheduled_start(self):
        start = self.cleaned_data.get("scheduled_start")
        if start and not self.instance.pk:
            # For new sessions, warn if scheduled in the past by more than 15 minutes
            if start < timezone.now() - timezone.timedelta(minutes=15):
                raise forms.ValidationError("Scheduled start time cannot be in the past.")
        return start

