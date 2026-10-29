"""
apps/academics/forms.py
-----------------------
Forms for academic year, term, class, stream, subject, and class–subject mapping.
"""

from django import forms
from django.utils import timezone

from . import calendar
from .models import AcademicYear, ClassSubject, SchoolClass, Stream, Subject, Term


class AcademicYearCreateForm(forms.Form):
    """The admin chooses a year from a dropdown; everything else is derived.

    Replaced the free-text name and the two date boxes, which is how years
    called "1000" and "3958" ended up in the school's data.
    """

    start_year = forms.ChoiceField(
        label="Academic year",
        choices=[],  # filled in __init__
        widget=forms.Select(attrs={"class": "form-select", "id": "id_start_year"}),
        help_text="Runs 1 September to 31 July and comes with Term 1, Term 2 "
        "and Term 3 already created.",
    )
    is_current = forms.BooleanField(
        label="Set as the current academic year",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        help_text="Only one year can be current at a time.",
    )

    def __init__(self, *args, today=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.today = today or timezone.localdate()
        existing = set(AcademicYear.objects.values_list("name", flat=True))
        self.fields["start_year"].choices = [
            (str(year), calendar.year_name(year))
            for year in calendar.selectable_start_years(self.today)
            if calendar.year_name(year) not in existing
        ]
        # Nothing is current yet, so the obvious answer is pre-ticked.
        if not self.is_bound and not existing:
            self.fields["is_current"].initial = True

    @property
    def has_choices(self) -> bool:
        return bool(self.fields["start_year"].choices)

    def clean_start_year(self) -> int:
        return int(self.cleaned_data["start_year"])


class TermForm(forms.ModelForm):
    class Meta:
        model = Term
        fields = ["academic_year", "name", "start_date", "end_date", "is_current"]
        widgets = {
            "academic_year": forms.Select(attrs={"class": "form-select"}),
            "name": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "end_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "is_current": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, year=None, **kwargs):
        super().__init__(*args, **kwargs)
        # A new year already arrives with all three slots, so only the missing
        # ones are offered here — this form is for repairing a legacy year.
        if year is not None and self.instance.pk is None:
            used = set(
                Term.objects.filter(academic_year=year).values_list("name", flat=True)
            )
            self.fields["name"].choices = [
                (value, label) for value, label in Term.TermName.choices if value not in used
            ]

    def clean(self):
        cleaned = super().clean()
        year = cleaned.get("academic_year")
        name = cleaned.get("name")
        if year and name and Term.objects.filter(academic_year=year, name=name).exists():
            self.add_error(
                "name",
                f"{year} already has {Term.TermName(name).label}. A year holds "
                "exactly three terms.",
            )
        return cleaned


class TermDatesForm(forms.ModelForm):
    """Correct the default window a term was created with."""

    class Meta:
        model = Term
        fields = ["start_date", "end_date"]
        widgets = {
            "start_date": forms.DateInput(
                attrs={"class": "form-control form-control-sm", "type": "date"}
            ),
            "end_date": forms.DateInput(
                attrs={"class": "form-control form-control-sm", "type": "date"}
            ),
        }


class SchoolClassForm(forms.ModelForm):
    class Meta:
        model = SchoolClass
        fields = ["academic_year", "name", "level_order"]
        widgets = {
            "academic_year": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Grade 5"}
            ),
            "level_order": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        }


class StreamForm(forms.ModelForm):
    class Meta:
        model = Stream
        fields = ["school_class", "name"]
        widgets = {
            "school_class": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Blue"}
            ),
        }


class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ["name", "code", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "code": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "MATH"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class ClassSubjectForm(forms.ModelForm):
    class Meta:
        model = ClassSubject
        fields = ["school_class", "subject", "stream"]
        widgets = {
            "school_class": forms.Select(attrs={"class": "form-select"}),
            "subject": forms.Select(attrs={"class": "form-select"}),
            "stream": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stream"].required = False
        self.fields["stream"].queryset = Stream.objects.select_related("school_class")
