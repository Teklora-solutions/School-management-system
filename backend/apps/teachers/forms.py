"""
apps/teachers/forms.py
----------------------
Forms for teacher management and assignment.
"""

from django import forms
from django.contrib.auth import get_user_model

from apps.academics.models import AcademicYear, SchoolClass, Stream, Subject

from .models import TeacherProfile

User = get_user_model()


class TeacherAssignmentForm(forms.Form):
    """Assign a teacher to a class/subject/year."""

    teacher = forms.ModelChoiceField(
        queryset=TeacherProfile.objects.select_related("user").all(),
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Teacher",
    )
    school_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.select_related("academic_year").all(),
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Class",
    )
    subject = forms.ModelChoiceField(
        queryset=Subject.objects.filter(is_active=True),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    academic_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.all(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    stream = forms.ModelChoiceField(
        queryset=Stream.objects.select_related("school_class").all(),
        required=False,
        empty_label="All streams",
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class TeacherFilterForm(forms.Form):
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Search name, staff ID…"}
        ),
    )
