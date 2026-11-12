"""
apps/homework/forms.py
----------------------
Teacher-facing homework form.

The class / subject / stream choices are not free lookups: they are exactly
the teacher's own ``TeacherAssignment`` rows, so the workspace can only offer
combinations the teacher is actually allocated to this academic year. The
same list is used to validate the submission, so a crafted POST cannot set
homework for someone else's class.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django import forms

from apps.academics.models import SchoolClass, Stream, Subject, Term
from apps.academics.selectors import get_current_term, get_current_year

from . import selectors
from .models import HomeworkMark


class HomeworkForm(forms.Form):
    school_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.none(),
        empty_label="Choose a class",
        label="Class",
    )
    subject = forms.ModelChoiceField(
        queryset=Subject.objects.none(),
        empty_label="Choose a subject",
        label="Subject",
    )
    stream = forms.ModelChoiceField(
        queryset=Stream.objects.none(),
        required=False,
        empty_label="All streams",
        label="Stream",
    )
    term = forms.ModelChoiceField(
        queryset=Term.objects.none(),
        empty_label=None,
        label="Term",
    )
    title = forms.CharField(
        max_length=200,
        label="Title",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. Fractions worksheet 3",
            }
        ),
    )
    description = forms.CharField(
        required=False,
        label="Instructions",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 9,
                "placeholder": (
                    "Explain what to do, how long it should take, and what to "
                    "hand in. Students see this text word for word."
                ),
            }
        ),
    )
    due_date = forms.DateField(
        label="Due date",
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"}
        ),
    )
    max_score = forms.DecimalField(
        required=False,
        min_value=Decimal("0.1"),
        max_digits=5,
        decimal_places=1,
        label="Total mark",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.5",
                "min": "0.1",
                "placeholder": "e.g. 20",
            }
        ),
        help_text=(
            "What a full mark is out of. Optional — leave it blank until you "
            "are ready to mark the work."
        ),
    )

    def __init__(
        self,
        *args,
        teacher_user_id: int,
        instance=None,
        is_create: bool = True,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.is_create = is_create
        self.assignment = None
        self.assignments = selectors.teacher_assignments(
            teacher_user_id=teacher_user_id
        )

        self.year = self._resolve_year()
        self.current_term = get_current_term() if self.year else None

        self.fields["school_class"].queryset = SchoolClass.objects.filter(
            pk__in={a.school_class_id for a in self.assignments}
        ).order_by("level_order", "name")
        self.fields["subject"].queryset = Subject.objects.filter(
            pk__in={a.subject_id for a in self.assignments}
        ).order_by("name")
        self.fields["stream"].queryset = Stream.objects.filter(
            pk__in={a.stream_id for a in self.assignments if a.stream_id}
        ).order_by("school_class__level_order", "name")
        self.fields["term"].queryset = (
            Term.objects.filter(academic_year=self.year).order_by("name")
            if self.year
            else Term.objects.none()
        )

        if instance is not None and not self.is_bound:
            self.initial.setdefault("school_class", instance.school_class_id)
            self.initial.setdefault("subject", instance.subject_id)
            self.initial.setdefault("stream", instance.stream_id)
            self.initial.setdefault("term", instance.term_id)
            self.initial.setdefault("title", instance.title)
            self.initial.setdefault("description", instance.description)
            self.initial.setdefault("due_date", instance.due_date)
            self.initial.setdefault("max_score", instance.max_score)
        elif not self.is_bound and self.current_term is not None:
            self.initial.setdefault("term", self.current_term.pk)

    def _resolve_year(self):
        if self.assignments:
            return self.assignments[0].academic_year
        return get_current_year()

    @property
    def term_options(self) -> list:
        return list(self.fields["term"].queryset)

    @property
    def class_options(self) -> list:
        seen: dict[int, SchoolClass] = {}
        for assignment in self.assignments:
            seen.setdefault(assignment.school_class_id, assignment.school_class)
        return sorted(seen.values(), key=lambda c: (c.level_order, c.name))

    @property
    def subject_options(self) -> list:
        seen: dict[tuple[int, int], dict] = {}
        for assignment in self.assignments:
            key = (assignment.school_class_id, assignment.subject_id)
            seen.setdefault(
                key,
                {
                    "id": assignment.subject_id,
                    "name": assignment.subject.name,
                    "class_id": assignment.school_class_id,
                },
            )
        return sorted(seen.values(), key=lambda s: (s["class_id"], s["name"]))

    @property
    def stream_options(self) -> list:
        seen: dict[int, dict] = {}
        for assignment in self.assignments:
            if not assignment.stream_id:
                continue
            seen.setdefault(
                assignment.stream_id,
                {
                    "id": assignment.stream_id,
                    "name": assignment.stream.name,
                    "class_id": assignment.school_class_id,
                },
            )
        return sorted(seen.values(), key=lambda s: (s["class_id"], s["name"]))

    def clean_due_date(self):
        due_date = self.cleaned_data["due_date"]
        if self.is_create and due_date < date.today():
            raise forms.ValidationError(
                "Pick today or a later date for the due date."
            )
        return due_date

    def clean(self):
        cleaned = super().clean()
        school_class = cleaned.get("school_class")
        subject = cleaned.get("subject")
        stream = cleaned.get("stream")

        if school_class and subject:
            matches = [
                a
                for a in self.assignments
                if a.school_class_id == school_class.pk
                and a.subject_id == subject.pk
            ]
            if not matches:
                self.add_error(
                    "subject",
                    "You are not assigned to teach this subject in that class.",
                )
            else:
                chosen = None
                if stream is not None:
                    chosen = next(
                        (a for a in matches if a.stream_id == stream.pk), None
                    )
                    if chosen is None:
                        self.add_error(
                            "stream", "You do not teach this subject in that stream."
                        )
                elif len({a.stream_id for a in matches}) > 1:
                    self.add_error(
                        "stream",
                        "You teach this subject in more than one stream — choose one.",
                    )
                else:
                    chosen = matches[0]
                self.assignment = chosen

        term = cleaned.get("term")
        if term and self.year and term.academic_year_id != self.year.pk:
            self.add_error(
                "term", "That term is not in the current academic year."
            )
        return cleaned


class HomeworkMarkForm(forms.Form):
    """
    The teacher's marking form for one pupil.

    Types are checked here so a mistyped mark is a field error rather than a
    stack trace; the rules that matter (bounds against the homework's total,
    lateness deductions, extra attempts) live in the service.
    """

    status = forms.ChoiceField(
        choices=[("", "Choose a status")] + list(HomeworkMark.Status.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    score = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        max_digits=5,
        decimal_places=1,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.5", "min": "0"}
        ),
    )
    feedback = forms.CharField(
        required=False,
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 5,
                "placeholder": (
                    "What went well, and one thing to try next. The pupil and "
                    "their parents read this word for word."
                ),
            }
        ),
    )
    late_deduction = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        max_digits=5,
        decimal_places=1,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.5", "min": "0"}
        ),
    )
    resubmissions_allowed = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=10,
        initial=1,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "min": "0", "max": "10", "step": "1"}
        ),
    )

    def __init__(self, *args, late_penalty: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.late_penalty = late_penalty
        if not late_penalty:
            self.fields.pop("late_deduction")
