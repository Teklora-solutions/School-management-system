"""
apps/assistant/handoff/forms.py
---------------------------------
Forms for the handoff rules admin screen and the inbox.
"""

from __future__ import annotations

from django import forms

from ..models import (
    CannedReply,
    Handoff,
    HandoffNote,
    HandoffRule,
    HandoffTeam,
)

SWITCH = forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"})
TEXT = forms.TextInput(attrs={"class": "form-control"})
TEXTAREA = forms.Textarea(attrs={"class": "form-control", "rows": 3})
TIME = forms.TimeInput(attrs={"class": "form-control", "type": "time"})
SELECT = forms.Select(attrs={"class": "form-select"})
NUMBER = forms.NumberInput(attrs={"class": "form-control", "min": "0"})


class HandoffTeamForm(forms.ModelForm):
    class Meta:
        model = HandoffTeam
        fields = ("name", "description", "members", "is_active")
        widgets = {
            "name": TEXT,
            "description": TEXTAREA,
            "members": forms.SelectMultiple(attrs={"class": "form-select", "size": "6"}),
            "is_active": SWITCH,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.accounts.models import User
        self.fields["members"].queryset = User.objects.filter(
            role__in=["admin", "director", "teacher", "finance"],
            is_active=True,
        ).order_by("last_name", "first_name")


class HandoffRuleForm(forms.ModelForm):
    class Meta:
        model = HandoffRule
        fields = (
            "name",
            "description",
            "enabled",
            "priority",
            "topics",
            "signals",
            "keywords",
            "any_director",
            "specific_roles",
            "destination_type",
            "destination_user",
            "destination_team",
            "destination_role",
            "fallback_user",
            "fallback_team",
            "fallback_role",
            "working_hours_start",
            "working_hours_end",
            "outside_hours_message_en",
            "outside_hours_message_sw",
            "response_target_minutes",
            "channels",
            "order",
        )
        widgets = {
            "name": TEXT,
            "description": TEXTAREA,
            "enabled": SWITCH,
            "priority": SELECT,
            "keywords": forms.Textarea(attrs={"class": "form-control", "rows": 4,
                                              "placeholder": "One term per line"}),
            "any_director": SWITCH,
            "specific_roles": TEXT,
            "destination_type": SELECT,
            "destination_user": SELECT,
            "destination_team": SELECT,
            "destination_role": TEXT,
            "fallback_user": SELECT,
            "fallback_team": SELECT,
            "fallback_role": TEXT,
            "working_hours_start": TIME,
            "working_hours_end": TIME,
            "outside_hours_message_en": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
            "outside_hours_message_sw": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
            "response_target_minutes": NUMBER,
            "channels": TEXT,
            "order": NUMBER,
        }
        labels = {
            "any_director": "Fire for any Director message",
        }
        help_texts = {
            "topics": "Hold Ctrl/Cmd to select multiple. Leave blank to ignore.",
            "signals": "Hold Ctrl/Cmd to select multiple. Leave blank to ignore.",
            "channels": "Comma-separated codes: in_app (always used), email, sms, whatsapp.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.accounts.models import User
        staff_qs = User.objects.filter(
            role__in=["admin", "director", "teacher", "finance"],
            is_active=True,
        ).order_by("last_name", "first_name")
        self.fields["destination_user"].queryset = staff_qs
        self.fields["fallback_user"].queryset = staff_qs
        self.fields["destination_team"].queryset = HandoffTeam.objects.filter(
            is_active=True
        ).order_by("name")
        self.fields["fallback_team"].queryset = HandoffTeam.objects.filter(
            is_active=True
        ).order_by("name")

        # Topics and signals as multi-select checkboxes using existing choices
        self.fields["topics"] = TopicMultiField()
        self.fields["signals"] = SignalMultiField()

        # Populate initial from pipe-wrapped value
        if self.instance and self.instance.pk:
            from ..models import _unpipe
            self.fields["topics"].initial = _unpipe(self.instance.topics)
            self.fields["signals"].initial = _unpipe(self.instance.signals)

    def clean_topics(self) -> str:
        from ..models import _pipe
        values = self.cleaned_data.get("topics") or []
        return _pipe(values)

    def clean_signals(self) -> str:
        from ..models import _pipe
        values = self.cleaned_data.get("signals") or []
        return _pipe(values)

    def clean(self):
        cleaned = super().clean()
        if self.instance.is_required:
            if not cleaned.get("enabled", True):
                self.add_error("enabled", "A mandatory safeguarding rule cannot be switched off.")
        return cleaned


class TopicMultiField(forms.MultipleChoiceField):
    def __init__(self, **kwargs):
        kwargs.setdefault(
            "choices", HandoffRule.Topic.choices
        )
        kwargs.setdefault(
            "widget", forms.CheckboxSelectMultiple(
                attrs={"class": "form-check-input"}
            )
        )
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)


class SignalMultiField(forms.MultipleChoiceField):
    def __init__(self, **kwargs):
        kwargs.setdefault(
            "choices", HandoffRule.Signal.choices
        )
        kwargs.setdefault(
            "widget", forms.CheckboxSelectMultiple(
                attrs={"class": "form-check-input"}
            )
        )
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)


class HandoffNoteForm(forms.Form):
    body = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3,
                                     "placeholder": "Internal note (never sent to the visitor)"}),
        label="Note",
    )

    def clean_body(self):
        return self.cleaned_data["body"].strip()


class StaffReplyForm(forms.Form):
    body = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4,
                                     "placeholder": "Your reply to the visitor"}),
        label="Reply",
        max_length=4000,
    )

    def clean_body(self):
        return self.cleaned_data["body"].strip()


class ResolveForm(forms.Form):
    note = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3,
                                     "placeholder": "Brief resolution note (optional)"}),
        required=False,
        label="Resolution note",
        max_length=500,
    )
    suggest_kb = forms.BooleanField(
        required=False,
        label="Suggest this resolution as a knowledge base Q&A",
        widget=SWITCH,
    )


class AssignForm(forms.Form):
    assign_to = forms.ChoiceField(
        widget=SELECT,
        label="Assign to",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.accounts.models import User
        choices = [("", "— Unassigned —")]
        for user in User.objects.filter(
            role__in=["admin", "director", "teacher", "finance"],
            is_active=True,
        ).order_by("last_name", "first_name"):
            choices.append((f"u:{user.pk}", user.get_full_name() or user.username))
        for team in HandoffTeam.objects.filter(is_active=True).order_by("name"):
            choices.append((f"t:{team.pk}", f"Team: {team.name}"))
        self.fields["assign_to"].choices = choices


class CannedReplyForm(forms.ModelForm):
    class Meta:
        model = CannedReply
        fields = ("title", "body", "category", "is_active", "display_order")
        widgets = {
            "title": TEXT,
            "body": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "category": TEXT,
            "is_active": SWITCH,
            "display_order": NUMBER,
        }
