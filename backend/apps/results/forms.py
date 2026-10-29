"""
apps/results/forms.py
---------------------
Forms for the results governance admin screens (Module 1).
"""

from __future__ import annotations

from decimal import Decimal

from django import forms

from apps.academics.models import AcademicYear, SchoolClass, Term

from .models import ResultSheet


class ResultSheetFilterForm(forms.Form):
    """Filter the admin result-sheet list by status / year / term / class."""

    status = forms.ChoiceField(
        choices=[("", "All statuses")] + ResultSheet.Status.choices,
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.all(),
        required=False,
        empty_label="All years",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    term = forms.ModelChoiceField(
        queryset=Term.objects.select_related("academic_year").all(),
        required=False,
        empty_label="All terms",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    school_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.select_related("academic_year").all(),
        required=False,
        empty_label="All classes",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )


class ReopenSheetForm(forms.Form):
    """Reason is mandatory when reopening a confirmed/published sheet."""

    reason = forms.CharField(
        required=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Why is this sheet being reopened?",
            }
        ),
    )


class PublishClassTermForm(forms.Form):
    """Publish every CONFIRMED sheet for one class + term."""

    school_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.select_related("academic_year").all(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    term = forms.ModelChoiceField(
        queryset=Term.objects.select_related("academic_year").all(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )


# ── Grading configuration ─────────────────────────────────────────────────────


class GradingConfigForm(forms.Form):
    pass_mark = forms.DecimalField(
        min_value=0,
        max_value=100,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        help_text="Minimum total score required to pass (0–100).",
    )


class GradeScaleForm(forms.Form):
    """The name of a year's grading system."""

    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. Upper Primary Scale",
            }
        ),
        help_text="A label for this year's grading system.",
    )


class GradeBandForm(forms.Form):
    """One grade-scale band (min, max, grade, remark, points)."""

    min_score = forms.DecimalField(
        min_value=0,
        max_value=100,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "step": "0.01"}
        ),
    )
    max_score = forms.DecimalField(
        min_value=0,
        max_value=100,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "step": "0.01"}
        ),
    )
    grade = forms.CharField(
        max_length=8,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "placeholder": "A"}
        ),
    )
    remark = forms.CharField(
        required=False,
        max_length=64,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "placeholder": "Excellent"}
        ),
    )
    points = forms.DecimalField(
        required=False,
        decimal_places=2,
        initial=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control form-control-sm", "step": "0.01"}
        ),
    )
    DELETE = forms.BooleanField(required=False, widget=forms.HiddenInput)


GradeBandFormSet = forms.formset_factory(GradeBandForm, extra=1, can_delete=True)


# ── Teacher marks entry ──────────────────────────────────────────────────────


class MarksEntryForm(forms.Form):
    """One mark out of 100 per student on the roster (blank = no mark)."""

    def __init__(self, *args, students=(), **kwargs):
        super().__init__(*args, **kwargs)
        for student in students:
            self.fields[f"score_{student.pk}"] = forms.DecimalField(
                required=False,
                min_value=0,
                max_value=100,
                decimal_places=2,
                widget=forms.NumberInput(
                    attrs={
                        "class": "form-control form-control-sm text-end",
                        "step": "0.01",
                        "placeholder": "—",
                        "aria-label": f"Mark for {student.user.full_name}",
                    }
                ),
            )

    def scores(self) -> dict[int, Decimal | None]:
        """Return {student_id: mark or None} for every student in the form."""
        result: dict[int, Decimal | None] = {}
        for name, value in self.cleaned_data.items():
            if name.startswith("score_"):
                result[int(name.removeprefix("score_"))] = value
        return result
