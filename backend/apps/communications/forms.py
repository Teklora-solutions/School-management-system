"""
apps/communications/forms.py
"""

from django import forms

from .models import MessageThread


class MessageThreadForm(forms.Form):
    message_type = forms.ChoiceField(
        choices=MessageThread.MessageType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Type",
    )
    subject = forms.CharField(
        max_length=200,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Brief subject"}
        ),
    )
    body = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 5,
                "placeholder": "Write your message…",
            }
        ),
        label="Message",
    )
    attachment = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )


class ThreadReplyForm(forms.Form):
    body = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Write your reply…",
            }
        ),
        label="Reply",
    )
    attachment = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )
    new_status = forms.ChoiceField(
        choices=[("", "— keep current status —")] + MessageThread.Status.choices,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Update status",
    )


class InboxFilterForm(forms.Form):
    status = forms.ChoiceField(
        choices=[("", "All statuses")] + MessageThread.Status.choices,
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    message_type = forms.ChoiceField(
        choices=[("", "All types")] + MessageThread.MessageType.choices,
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Search subject or sender…",
            }
        ),
    )


class AnnouncementForm(forms.Form):
    """Used by admin and director to create announcements."""

    from .models import Announcement as _Ann

    title = forms.CharField(
        max_length=200,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Announcement title"}
        ),
    )
    body = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 5,
                "placeholder": "Write the announcement body…",
            }
        ),
    )
    audience = forms.ChoiceField(
        choices=_Ann.Audience.choices,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_audience"}),
    )
    target_roles = forms.MultipleChoiceField(
        choices=[
            ("student", "Students"),
            ("parent", "Parents / Guardians"),
            ("teacher", "Teachers"),
            ("finance", "Finance Officers"),
            ("admin", "Admins"),
            ("director", "Directors"),
        ],
        required=False,
        widget=forms.CheckboxSelectMultiple(),
    )
    target_classes = forms.ModelMultipleChoiceField(
        queryset=None,  # set in __init__
        required=False,
        widget=forms.CheckboxSelectMultiple(),
    )
    attachment = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )
    publish_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"class": "form-control", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
        help_text="Leave blank to publish immediately.",
    )
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"class": "form-control", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
        help_text="Optional expiry date/time.",
    )
    is_published = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="Publish immediately",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.academics.models import SchoolClass
        from apps.academics.selectors import get_current_year

        year = get_current_year()
        if year:
            self.fields["target_classes"].queryset = SchoolClass.objects.filter(
                academic_year=year
            ).order_by("level_order", "name")
        else:
            self.fields["target_classes"].queryset = SchoolClass.objects.none()
