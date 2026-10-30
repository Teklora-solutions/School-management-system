"""
apps/teachers/forms.py
----------------------
Forms for teacher management and assignment.
"""

from django import forms
from django.contrib.auth import get_user_model

from apps.academics.models import AcademicYear, SchoolClass, Stream, Subject

from .models import TeacherAssignment, TeacherProfile

User = get_user_model()


def _as_id(value):
    """Accept a pk, a model instance, or a form string; anything else is None."""
    if value in (None, ""):
        return None
    return getattr(value, "pk", value)


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound:
            # A submitted form keeps the full lists. Narrowing them here would
            # answer a duplicate with "Select a valid choice" instead of the
            # service's "already assigned to another teacher".
            return

        year_id = _as_id(self.initial.get("academic_year"))
        class_id = _as_id(self.initial.get("school_class"))

        if year_id:
            self.fields["school_class"].queryset = self.fields[
                "school_class"
            ].queryset.filter(academic_year_id=year_id)

        if class_id:
            self.fields["stream"].queryset = self.fields["stream"].queryset.filter(
                school_class_id=class_id
            )
            # One teacher per class+subject per year regardless of stream, so a
            # subject claimed in any stream is gone for the whole class.
            taken = TeacherAssignment.objects.filter(
                academic_year_id=year_id,
                school_class_id=class_id,
            ).values_list("subject_id", flat=True)
            self.fields["subject"].queryset = self.fields["subject"].queryset.exclude(
                pk__in=taken
            )
        else:
            # Streams only mean something once a class is chosen.
            self.fields["stream"].queryset = Stream.objects.none()


class TeacherFilterForm(forms.Form):
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Search name, staff ID…"}
        ),
    )
