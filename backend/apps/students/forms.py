"""
apps/students/forms.py
----------------------
Student add / bulk-import / promotion forms.
"""

from __future__ import annotations

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.validators import FileExtensionValidator

from apps.academics.models import AcademicYear, SchoolClass, Stream, Term


class StudentCreateForm(forms.Form):
    """Add a single student with guardian info."""

    # ── Student info ───────────────────────────────────────────────
    first_name = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs={"class": "form-control"})
    )
    last_name = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs={"class": "form-control"})
    )
    email = forms.EmailField(widget=forms.EmailInput(attrs={"class": "form-control"}))
    admission_number = forms.CharField(
        max_length=40, widget=forms.TextInput(attrs={"class": "form-control"})
    )
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    gender = forms.ChoiceField(
        choices=[("", "—"), ("M", "Male"), ("F", "Female"), ("O", "Other")],
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    enrollment_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    current_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.select_related("academic_year").all(),
        required=False,
        empty_label="— Select class —",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    current_stream = forms.ModelChoiceField(
        queryset=Stream.objects.select_related("school_class").all(),
        required=False,
        empty_label="— Select stream (optional) —",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    # ── Login ───────────────────────────────────────────────────────
    password = forms.CharField(
        label="Initial password",
        required=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "autocomplete": "new-password",
                "placeholder": "Blank = their admission number",
            }
        ),
        help_text="Leave blank to use the admission number, which is what bulk "
        "import does. Fill this in to override it for one student.",
    )
    password_confirm = forms.CharField(
        label="Confirm initial password",
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "autocomplete": "new-password"}
        ),
    )
    must_change_password = forms.BooleanField(
        label="Force a password change on first login",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    # ── Guardian info ───────────────────────────────────────────────
    guardian_first_name = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs={"class": "form-control"})
    )
    guardian_last_name = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs={"class": "form-control"})
    )
    guardian_email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "form-control"})
    )
    guardian_phone = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    relationship = forms.ChoiceField(
        choices=[
            ("guardian", "Guardian"),
            ("father", "Father"),
            ("mother", "Mother"),
            ("other", "Other"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def clean_password(self):
        """Only an override is validated — the admission-number default is a
        school-wide convention, not a password choice."""
        password = self.cleaned_data["password"]
        if password:
            validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        if password and password != cleaned.get("password_confirm"):
            self.add_error(
                "password_confirm", "Confirm the password, or leave both blank."
            )
        return cleaned


class BulkImportForm(forms.Form):
    """Upload Excel file for bulk student import."""

    MODE_CHOICES = [
        ("all_or_nothing", "All-or-Nothing (default): abort on any error"),
        ("partial", "Partial: commit valid rows, skip bad rows"),
    ]

    file = forms.FileField(
        label="Excel File (.xlsx)",
        validators=[FileExtensionValidator(allowed_extensions=["xlsx"])],
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": ".xlsx"}
        ),
    )
    target_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.select_related("academic_year").all(),
        empty_label="— Select the class to import into —",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    mode = forms.ChoiceField(
        choices=MODE_CHOICES,
        widget=forms.RadioSelect,
        initial="all_or_nothing",
    )


class BulkImportCommitForm(forms.Form):
    """Confirm-and-commit a validated bulk import job."""

    job_id = forms.IntegerField(widget=forms.HiddenInput)


class PromotionPreviewForm(forms.Form):
    """Choose source & destination years and term, and which students repeat."""

    from_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.all(),
        widget=forms.Select(attrs={"class": "form-select"}),
        label="From Academic Year",
    )
    to_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.all(),
        widget=forms.Select(attrs={"class": "form-select"}),
        label="To Academic Year",
    )
    term = forms.ModelChoiceField(
        queryset=Term.objects.select_related("academic_year").all(),
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Results Term (for publication check)",
    )
    repeat_students = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        label="Students to repeat (admission numbers)",
        help_text="Comma-separated admission numbers of students who must repeat their current level.",
    )

    def clean(self):
        cleaned = super().clean()
        from_y = cleaned.get("from_year")
        to_y = cleaned.get("to_year")
        if from_y and to_y and from_y.pk == to_y.pk:
            raise forms.ValidationError("From-year and To-year must be different.")
        return cleaned


class PromotionCommitForm(forms.Form):
    """Commit a promotion (carries repeat list + override flag)."""

    from_year = forms.IntegerField(widget=forms.HiddenInput)
    to_year = forms.IntegerField(widget=forms.HiddenInput)
    term = forms.IntegerField(widget=forms.HiddenInput)
    repeat_students = forms.CharField(
        required=False, widget=forms.HiddenInput
    )  # comma-separated admission numbers
    override_unpublished = forms.BooleanField(required=False)
    override_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )
