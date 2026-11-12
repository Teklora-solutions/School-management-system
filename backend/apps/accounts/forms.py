"""
apps/accounts/forms.py
-----------------------
Login, password-change, and staff user management forms.
"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.password_validation import validate_password

from .models import User


class LoginForm(AuthenticationForm):
    """Styled login form (adds Bootstrap classes)."""

    username = forms.CharField(
        label="Email / Admission No.",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Staff email  or  Student admission no.",
                "autofocus": True,
                "id": "id_login_username",
                "autocomplete": "username",
            }
        ),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Password",
                "id": "id_login_password",
            }
        )
    )

    def clean_username(self):
        """
        Accept a student's admission number exactly as it is printed.

        The account is keyed on ``student_username_for(admission_number)``
        (lower-cased, ``_`` for separators), so typing ``ADM/2025/014`` — which
        is what the admission letter and the bulk-import file both show — has to
        reach the same account as ``adm_2025_014``.
        """
        username = self.cleaned_data.get("username", "").strip()
        if username:
            from apps.students.models import Student
            from apps.students.services import student_username_for

            if Student.objects.filter(
                admission_number__iexact=username
            ).exists():
                return student_username_for(username)
        return username


class ForcePasswordChangeForm(PasswordChangeForm):
    """Shown once when must_change_password=True."""

    old_password = forms.CharField(
        label="Current (temporary) password",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    new_password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )


class StaffUserCreateForm(forms.Form):
    """Create teacher / director / finance staff accounts."""

    ROLE_CHOICES = [
        (User.Role.TEACHER, "Teacher"),
        (User.Role.DIRECTOR, "Director"),
        (User.Role.FINANCE, "Finance Officer"),
    ]

    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    last_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    password = forms.CharField(
        label="Initial password",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "autocomplete": "new-password",
                "placeholder": "Set the password they sign in with",
            }
        ),
        help_text="You choose this one — the system no longer generates it.",
    )
    password_confirm = forms.CharField(
        label="Confirm initial password",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "autocomplete": "new-password"}
        ),
    )
    must_change_password = forms.BooleanField(
        label="Force a password change on first login",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        help_text="Ticked: they must set their own password the first time they sign in.",
    )
    phone = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    staff_id = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        help_text="Optional. Auto-generated for teachers if left blank.",
    )

    def clean_password(self):
        """
        ``create_user()`` never runs Django's password validators, and dev/test
        set AUTH_PASSWORD_VALIDATORS to an empty list, so the form is the only
        place a hand-typed password can be checked.
        """
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        confirm = cleaned.get("password_confirm")
        if password and confirm and password != confirm:
            self.add_error("password_confirm", "The two passwords did not match.")
        return cleaned


class StaffSetPasswordForm(forms.Form):
    """
    The admin types the new password for a staff member.

    The system never invents this one, so it is never shown back and never
    emailed: the admin knows it and hands it over. ``must_change_password``
    mirrors the create-staff checkbox.
    """

    password = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "autocomplete": "new-password",
                "placeholder": "Type the password they will sign in with",
            }
        ),
    )
    password_confirm = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "autocomplete": "new-password"}
        ),
    )
    must_change_password = forms.BooleanField(
        label="Force a password change on first login",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        help_text="Ticked: they must set their own password the first time they sign in.",
    )

    def clean_password(self):
        """``set_password()`` never runs the validators, so the form does."""
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        confirm = cleaned.get("password_confirm")
        if password and confirm and password != confirm:
            self.add_error("password_confirm", "The two passwords did not match.")
        return cleaned


class StaffUserUpdateForm(forms.Form):
    """
    Edit an existing staff account's identity details.

    No ``role`` field and no password field, on purpose: a role change means
    moving the profile row that holds ``staff_id`` and re-deriving what the
    account can see, and a password change belongs to
    ``staff_reset_password`` — the only path that audits it. The view passes
    ``initial={"staff_id": …}`` because that value lives on the role's profile
    row, not on the user.
    """

    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    last_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "form-control"}),
        help_text="Staff sign in with this address, so the old one stops working once you save.",
    )
    phone = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    staff_id = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        help_text="Saved on the role's profile row. Blank keeps the current ID.",
    )


class StaffUserFilterForm(forms.Form):
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Search name, email…"}
        ),
    )
    role = forms.ChoiceField(
        required=False,
        choices=[("", "All roles")]
        + [
            (User.Role.ADMIN, "Admin"),
            (User.Role.DIRECTOR, "Director"),
            (User.Role.TEACHER, "Teacher"),
            (User.Role.FINANCE, "Finance Officer"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    is_active = forms.ChoiceField(
        required=False,
        choices=[
            ("", "All statuses"),
            ("1", "Active"),
            ("0", "Inactive"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
