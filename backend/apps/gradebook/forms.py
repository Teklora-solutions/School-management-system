"""
apps/gradebook/forms.py
-----------------------
The teacher's editing forms: the four weights, hand-recorded work, its marks,
and one-pupil excuses.

Every choice list is scoped to the classroom the view already resolved, so a
crafted POST cannot point at another class's item or pupil. The rules that
touch stored data (locks, bounds, roster membership) live in the service.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from apps.results.models import ResultSheet
from apps.students.models import Student

from . import selectors
from .models import ManualItem
from .services import RERELEASE_REASON_MESSAGE


def _text(widget_attrs: dict | None = None):
    return forms.TextInput(attrs={"class": "form-control", **(widget_attrs or {})})


class WeightsForm(forms.Form):
    """The four bucket weights and the end-of-term sheet."""

    homework_weight = forms.IntegerField(
        min_value=0,
        max_value=100,
        label="Homework",
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0", "max": "100"}),
    )
    quiz_weight = forms.IntegerField(
        min_value=0,
        max_value=100,
        label="Quizzes",
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0", "max": "100"}),
    )
    lab_weight = forms.IntegerField(
        min_value=0,
        max_value=100,
        label="Labs and hand-recorded work",
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0", "max": "100"}),
    )
    exam_weight = forms.IntegerField(
        min_value=0,
        max_value=100,
        label="End-of-term exam",
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "0", "max": "100"}),
    )
    exam_sheet = forms.ModelChoiceField(
        queryset=ResultSheet.objects.none(),
        required=False,
        empty_label="No end-of-term sheet yet",
        label="End-of-term sheet",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, setup, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup = setup
        if not self.is_bound:
            self.initial.setdefault("homework_weight", setup.homework_weight)
            self.initial.setdefault("quiz_weight", setup.quiz_weight)
            self.initial.setdefault("lab_weight", setup.lab_weight)
            self.initial.setdefault("exam_weight", setup.exam_weight)
            self.initial.setdefault("exam_sheet", setup.exam_sheet_id)
        self.fields["exam_sheet"].queryset = (
            ResultSheet.objects.filter(
                school_class=setup.school_class,
                subject=setup.subject,
                term=setup.term,
            )
            .select_related("exam")
            .order_by("exam__name", "pk")
        )

    def clean(self):
        cleaned = super().clean()
        weights = [
            cleaned.get("homework_weight"),
            cleaned.get("quiz_weight"),
            cleaned.get("lab_weight"),
            cleaned.get("exam_weight"),
        ]
        if all(value is not None for value in weights) and sum(weights) != 100:
            self.add_error(
                "homework_weight",
                f"The four weights must add up to 100 (they add up to {sum(weights)}).",
            )
        return cleaned


class ManualItemForm(forms.Form):
    """One piece of work recorded by hand, with the reason it exists."""

    bucket = forms.ChoiceField(
        choices=ManualItem.Bucket.choices,
        label="Bucket",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    title = forms.CharField(
        max_length=200,
        label="Title",
        widget=_text({"placeholder": "e.g. Titration practical"}),
    )
    max_score = forms.DecimalField(
        min_value=Decimal("0.1"),
        max_digits=6,
        decimal_places=1,
        label="Out of",
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.5", "min": "0.1"}
        ),
    )
    worked_on = forms.DateField(
        label="Worked on",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    reason = forms.CharField(
        label="Why is this recorded by hand?",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": (
                    "e.g. Written test done in class; the papers are kept in the "
                    "subject folder. This note is what the mark is defended with later."
                ),
            }
        ),
    )
    counts_for_gradebook = forms.BooleanField(
        required=False,
        initial=True,
        label="Count towards the average",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, setup, instance: ManualItem | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup = setup
        self.instance = instance
        if instance is not None and not self.is_bound:
            self.initial.setdefault("bucket", instance.bucket)
            self.initial.setdefault("title", instance.title)
            self.initial.setdefault("max_score", instance.max_score)
            self.initial.setdefault("worked_on", instance.worked_on)
            self.initial.setdefault("reason", instance.reason)
            self.initial.setdefault(
                "counts_for_gradebook", instance.counts_for_gradebook
            )


class ManualScoresForm(forms.Form):
    """One mark per pupil on a hand-recorded item. A blank box clears the mark."""

    def __init__(self, *args, setup, item: ManualItem, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup = setup
        self.item = item
        roster = list(
            selectors.roster(
                school_class_id=setup.school_class_id,
                stream_id=setup.stream_id,
            )
        )
        existing = {
            score.student_id: score.score
            for score in item.scores.all()
            if score.score is not None
        }
        self.score_fields = []
        for student in roster:
            name = f"score_{student.pk}"
            self.fields[name] = forms.DecimalField(
                required=False,
                min_value=Decimal("0"),
                max_value=Decimal(str(item.max_score)),
                max_digits=6,
                decimal_places=1,
                label=str(student),
                widget=forms.NumberInput(
                    attrs={
                        "class": "form-control form-control-sm",
                        "step": "0.5",
                        "min": "0",
                        "max": str(item.max_score),
                        "inputmode": "decimal",
                    }
                ),
            )
            if not self.is_bound:
                self.initial.setdefault(name, existing.get(student.pk))
            self.score_fields.append((student, self[name]))


class ReleaseForm(forms.Form):
    """
    Freeze the grid into the next release.

    The first release carries an optional note; every later one needs a reason,
    because it moves a mark a family may already have seen.
    """

    reason = forms.CharField(
        required=False,
        label="Note for the record (optional)",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "e.g. End of week 6. Weights left at the agreed 30/30/20/20.",
            }
        ),
    )

    def __init__(self, *args, setup, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup = setup
        self.has_previous = setup.releases.exists()
        if self.has_previous:
            self.fields["reason"].label = "What changed since the last release?"
            self.fields["reason"].widget.attrs["placeholder"] = (
                "e.g. Kofi's late homework was marked; the class average moved "
                "from 61.2% to 61.8%."
            )

    def clean_reason(self) -> str:
        reason = (self.cleaned_data.get("reason") or "").strip()
        if self.has_previous and not reason:
            raise ValidationError(RERELEASE_REASON_MESSAGE)
        return reason


class ExcuseForm(forms.Form):
    """Excuse one pupil from one counted item, with a reason."""

    item = forms.ChoiceField(
        label="Work",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    student = forms.ModelChoiceField(
        queryset=Student.objects.none(),
        label="Pupil",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    reason = forms.CharField(
        label="Reason",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "e.g. Hospital appointment; work will not be made up.",
            }
        ),
    )

    def __init__(self, *args, setup, items: list, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup = setup
        choices = [
            (
                f"{item['source_kind']}:{item['source_id']}",
                f"{item['bucket_label']}: {item['title']}",
            )
            for item in items
            if item["source_kind"] in ("homework", "quiz", "manual")
        ]
        self.fields["item"].choices = choices
        self.fields["student"].queryset = selectors.roster(
            school_class_id=setup.school_class_id,
            stream_id=setup.stream_id,
        )
        self._sources = {
            (item["source_kind"], item["source_id"]) for item in items
        }

    def clean(self):
        cleaned = super().clean()
        raw = cleaned.get("item")
        if raw:
            kind, _, source_id = raw.partition(":")
            if not source_id.isdigit() or (kind, int(source_id)) not in self._sources:
                self.add_error("item", "Choose a counted piece of work.")
            else:
                cleaned["source_kind"] = kind
                cleaned["source_id"] = int(source_id)
        reason = (cleaned.get("reason") or "").strip()
        if not reason:
            self.add_error("reason", "Give the reason this pupil is excused.")
        cleaned["reason"] = reason
        return cleaned
