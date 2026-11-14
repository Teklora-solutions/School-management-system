"""
apps/learning/live_class_forms.py
---------------------------------
Form for scheduling and updating live class sessions.
"""

from __future__ import annotations

from django import forms
from django.utils import timezone

from apps.learning.live_class_utils import (
    normalize_meeting_url,
    validate_meeting_url,
    validate_recording_url,
)
from apps.learning.models import Lesson, LiveClassSession


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


class LiveClassRecordingForm(forms.ModelForm):
    """Recording link for a session, with the notice confirmation."""

    notice_given = forms.BooleanField(
        required=True,
        label="I told the class this lesson is being recorded.",
        error_messages={
            "required": "Tick the box to confirm you told the class the lesson is being recorded.",
        },
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = LiveClassSession
        fields = ["recording_url", "recording_lesson"]
        widgets = {
            "recording_url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "https://drive.google.com/... or https://youtu.be/...",
                }
            ),
            "recording_lesson": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "recording_url": "Recording link",
            "recording_lesson": "Also show on a lesson (optional)",
        }

    def __init__(self, *args, school_class=None, subject=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recording_url"].required = True
        field = self.fields["recording_lesson"]
        field.required = False
        field.empty_label = "Not attached to a lesson"
        if school_class is not None and subject is not None:
            field.queryset = (
                Lesson.objects.filter(
                    module__school_class=school_class,
                    module__subject=subject,
                )
                .select_related("module")
                .order_by("module__position", "module__id", "position", "id")
            )
        field.label_from_instance = lambda lesson: (
            f"{lesson.module.title} › {lesson.title}"
        )

    def clean_recording_url(self) -> str:
        url = normalize_meeting_url(self.cleaned_data.get("recording_url", ""))
        valid, err_msg = validate_recording_url(url)
        if not valid:
            raise forms.ValidationError(err_msg)
        return url

