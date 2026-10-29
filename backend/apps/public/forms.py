"""apps/public/forms.py"""

from __future__ import annotations

from django import forms

from apps.core.validators import validate_image_upload

from .models import GalleryPhoto


class ContactUsForm(forms.Form):
    """
    The enquiry form on the public Contact page.

    ``website`` is a honeypot: no browser renders it and it is empty on every
    real submission, while a bulk poster fills every field it can find. It is
    checked in the view, which then answers as if the enquiry had gone through
    — telling a spammer that the form rejected them teaches them what to fix.
    """

    name = forms.CharField(
        max_length=200,
        label="Your name",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ama Mensah"}),
    )
    email = forms.EmailField(
        max_length=254,
        label="Email address",
        widget=forms.EmailInput(
            attrs={"class": "form-control", "placeholder": "you@example.com"}
        ),
    )
    phone = forms.CharField(
        max_length=32,
        required=False,
        label="Phone number (optional)",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "+233 …"}),
    )
    message = forms.CharField(
        max_length=2000,
        label="How can we help?",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 5, "maxlength": "2000"}
        ),
        help_text="Admissions, fees, a transfer, an event — anything about the school.",
    )
    website = forms.CharField(
        required=False,
        label="Leave this field empty",
        widget=forms.HiddenInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
    )

    def clean_name(self) -> str:
        return self.cleaned_data["name"].strip()

    def clean_email(self) -> str:
        return self.cleaned_data["email"].strip().lower()

    def clean_phone(self) -> str:
        return self.cleaned_data["phone"].strip()

    def clean_message(self) -> str:
        return self.cleaned_data["message"].strip()


class GalleryPhotoForm(forms.ModelForm):
    """
    The photograph form on the Website pages.

    ``image`` uses a plain ``FileInput`` rather than the ``ClearableFileInput``
    School Settings uses, because a row without its photograph is not a thing
    this gallery has: the choices are replace it or delete the photograph, and a
    "clear" box would only produce a half-empty row that the public page then
    has to make excuses for.
    """

    class Meta:
        model = GalleryPhoto
        fields = ["title", "caption", "image", "display_order", "is_published"]
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Sports day, 2026"}
            ),
            "caption": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "One line under the photo"}
            ),
            "image": forms.FileInput(attrs={"class": "form-control"}),
            "display_order": forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # An unchecked box is False, not a validation error.
        self.fields["is_published"].required = False
        # Same size and content rules the rest of the project's uploads keep.
        self.fields["image"].validators = list(validate_image_upload)

    def clean_title(self) -> str:
        return self.cleaned_data["title"].strip()

    def clean_caption(self) -> str:
        return self.cleaned_data["caption"].strip()


class EnquiryFilterForm(forms.Form):
    """
    The two filters the enquiry list offers: open or handled, and a text search.

    Deliberately bound to nothing — it reads ``request.GET`` and hands back a
    dict the selector turns into a queryset, so a junk value in the address bar
    falls back to the default instead of erroring.
    """

    STATUS_OPEN = "open"
    STATUS_HANDLED = "handled"
    STATUS_ALL = "all"
    STATUS_CHOICES = [
        (STATUS_ALL, "All enquiries"),
        (STATUS_OPEN, "Still open"),
        (STATUS_HANDLED, "Marked handled"),
    ]

    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-control form-control-sm"}),
    )
    q = forms.CharField(
        required=False,
        label="Search",
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Name, email or a word in the message",
            }
        ),
    )

    def clean_status(self) -> str:
        value = self.cleaned_data["status"] or self.STATUS_ALL
        return value if value in dict(self.STATUS_CHOICES) else self.STATUS_ALL

    def clean_q(self) -> str:
        return self.cleaned_data["q"].strip()
