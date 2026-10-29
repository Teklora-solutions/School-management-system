"""
apps/finance/forms.py
----------------------
All forms for the finance module.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.db.models import F

from .models import (
    Account,
    Adjustment,
    Expense,
    Invoice,
    Payment,
    Vendor,
)

# ---------------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------------


class ExpenseAccountForm(forms.Form):
    code = forms.CharField(
        max_length=20,
        label="Account Code",
        help_text="A unique code e.g. 6010",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. 6010"}
        ),
    )
    name = forms.CharField(
        max_length=100,
        label="Account Name",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. Stationery"}
        ),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )


# ---------------------------------------------------------------------------
# Fee Structures
# ---------------------------------------------------------------------------


class FeeStructureFilterForm(forms.Form):
    """Used to filter the fee structure list."""

    school_class = forms.IntegerField(required=False, widget=forms.HiddenInput())
    term = forms.IntegerField(required=False, widget=forms.HiddenInput())


class FeeItemFormSet(forms.BaseFormSet):
    pass


class FeeItemForm(forms.Form):
    label = forms.CharField(
        max_length=100,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. Tuition"}
        ),
    )
    amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    ordering = forms.IntegerField(
        required=False,
        initial=0,
        widget=forms.HiddenInput(),
    )


class FeeStructureCreateForm(forms.Form):
    school_class = forms.ModelChoiceField(
        queryset=None,
        label="Class",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    term = forms.ModelChoiceField(
        queryset=None,
        label="Term",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    due_date = forms.DateField(
        required=False,
        label="Pay by (optional)",
        help_text="An item without its own date inherits this one.",
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date", "id": "id_fs_due_date"}
        ),
    )
    note = forms.CharField(
        required=False,
        label="Note (optional)",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.academics.models import SchoolClass, Term

        self.fields["school_class"].queryset = SchoolClass.objects.select_related(
            "academic_year"
        ).order_by("academic_year__start_date", "level_order")
        self.fields["term"].queryset = Term.objects.select_related(
            "academic_year"
        ).order_by("-academic_year__start_date", "start_date")


class FeeStructureUploadForm(forms.Form):
    school_class = forms.ModelChoiceField(
        queryset=None,
        label="Class",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    term = forms.ModelChoiceField(
        queryset=None,
        label="Term",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    file = forms.FileField(
        label="Excel File (.xlsx)",
        widget=forms.FileInput(attrs={"class": "form-control", "accept": ".xlsx"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.academics.models import SchoolClass, Term

        self.fields["school_class"].queryset = SchoolClass.objects.select_related(
            "academic_year"
        ).order_by("academic_year__start_date", "level_order")
        self.fields["term"].queryset = Term.objects.select_related(
            "academic_year"
        ).order_by("-academic_year__start_date", "start_date")

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if f and not f.name.endswith(".xlsx"):
            raise forms.ValidationError("Only .xlsx files are accepted.")
        return f


# ---------------------------------------------------------------------------
# Invoice Generation
# ---------------------------------------------------------------------------


class InvoiceGenerateForm(forms.Form):
    term = forms.ModelChoiceField(
        queryset=None,
        label="Term",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_term"}),
    )
    school_class = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="Class (leave blank for all classes)",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_school_class"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.academics.models import SchoolClass, Term

        self.fields["term"].queryset = Term.objects.select_related(
            "academic_year"
        ).order_by("-academic_year__start_date", "start_date")
        self.fields["school_class"].queryset = SchoolClass.objects.select_related(
            "academic_year"
        ).order_by("academic_year__start_date", "level_order")


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


class PaymentRecordForm(forms.Form):
    student = forms.ModelChoiceField(
        queryset=None,
        label="Student",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_student"}),
    )
    amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.01", "id": "id_amount"}
        ),
    )
    method = forms.ChoiceField(
        choices=Payment.Method.choices,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_method"}),
    )
    reference = forms.CharField(
        max_length=100,
        label="Payment Reference",
        help_text="Bank reference, MoMo transaction ID, or any unique identifier.",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "id": "id_reference",
                "placeholder": "e.g. TXN123456",
            }
        ),
    )
    payment_date = forms.DateField(
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date", "id": "id_payment_date"}
        ),
    )
    payer_name = forms.CharField(
        required=False,
        max_length=120,
        label="Who handed it in",
        help_text="Leave blank when the recorder is the person who paid.",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "id": "id_payer_name",
                "placeholder": "e.g. Mother, Mr Otieno",
            }
        ),
    )
    attachment = forms.FileField(
        required=False,
        label="Slip or confirmation",
        help_text="Deposit slip, mobile-money confirmation, photo of a cheque.",
        widget=forms.FileInput(
            attrs={"class": "form-control", "id": "id_payment_attachment"}
        ),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 2, "id": "id_notes"}
        ),
    )
    #: The double-click guard. The page generates it, the service honours it: a
    #: resubmission of this form returns the first receipt instead of posting a
    #: second one for the same money.
    idempotency_key = forms.CharField(
        required=False,
        max_length=64,
        widget=forms.HiddenInput(attrs={"id": "id_idempotency_key"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.students.models import Student

        from .services import BILLABLE_STATUSES

        # Ordered by class then surname, because this list is read by scrolling:
        # a cashier handed "every pupil in the school, alphabetical" cannot see
        # where one class ends and the next begins. The Take Payment page filters
        # it by class and by name in the browser; this ordering is what a browser
        # without that script still gets.
        self.fields["student"].queryset = (
            Student.objects.filter(status__in=BILLABLE_STATUSES)
            .select_related("user", "current_class")
            .order_by(
                F("current_class__level_order").asc(nulls_last=True),
                "current_class__name",
                "user__last_name",
                "user__first_name",
            )
        )


class PaymentReversalForm(forms.Form):
    reason = forms.CharField(
        min_length=10,
        label="Reason for Reversal",
        help_text="Minimum 10 characters. This is audit-logged.",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3, "id": "id_reversal_reason"}
        ),
    )


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------


class AdjustmentForm(forms.Form):
    student = forms.ModelChoiceField(
        queryset=None,
        label="Student",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_adj_student"}),
    )
    invoice = forms.ModelChoiceField(
        queryset=Invoice.objects.none(),
        required=False,
        label="Invoice (optional)",
        help_text="Leave blank for a general account adjustment.",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_adj_invoice"}),
    )
    adjustment_type = forms.ChoiceField(
        choices=Adjustment.AdjustmentType.choices,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_adj_type"}),
    )
    amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.01", "id": "id_adj_amount"}
        ),
    )
    reason = forms.CharField(
        min_length=5,
        label="Reason",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3, "id": "id_adj_reason"}
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.students.models import Student

        self.fields["student"].queryset = (
            Student.objects.filter(status__in=["active", "repeating"])
            .select_related("user")
            .order_by("user__last_name", "user__first_name")
        )
        # The invoice select used to be bound to an empty queryset, which made
        # every adjustment a general one — a bill could not be named at all, and
        # a submitted pk failed validation with no way to pick a valid one.
        # It now offers the posted bills of whichever pupil the form is about.
        invoices = (
            Invoice.objects.filter(status=Invoice.Status.POSTED)
            .select_related("student__user", "term")
            .order_by("-issued_date")
        )
        chosen = self.initial.get("student") or (
            self.data.get("student") if self.is_bound else None
        )
        student_id = getattr(chosen, "pk", chosen)
        if student_id:
            invoices = invoices.filter(student_id=student_id)
        self.fields["invoice"].queryset = invoices


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


class ExpenseForm(forms.Form):
    account = forms.ModelChoiceField(
        queryset=Account.objects.filter(type=Account.Type.EXPENSE, is_active=True),
        label="Expense Category",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_exp_account"}),
    )
    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="Vendor",
        empty_label="— Not on the vendors list —",
        help_text="Pick a supplier the school already pays. A one-off payee is "
        "named below instead.",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_exp_vendor"}),
    )
    payee_name = forms.CharField(
        required=False,
        max_length=120,
        label="Payee (one-off)",
        help_text="For a person with no vendor record — the casual labourer paid at "
        "the gate, not the supplier the school bills every term.",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "id": "id_exp_payee",
                "placeholder": "e.g. K. Mensah (gate repairs)",
            }
        ),
    )
    amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.01", "id": "id_exp_amount"}
        ),
    )
    expense_date = forms.DateField(
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date", "id": "id_exp_date"}
        ),
    )
    paid_via = forms.ChoiceField(
        choices=Expense.PaymentVia.choices,
        label="Paid from",
        help_text="Petty cash is spent, never collected — fees do not arrive as cash.",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_exp_paid_via"}),
    )
    description = forms.CharField(
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3, "id": "id_exp_desc"}
        ),
    )
    attachment = forms.FileField(
        required=False,
        widget=forms.FileInput(
            attrs={"class": "form-control", "id": "id_exp_attachment"}
        ),
    )
    approval_note = forms.CharField(
        required=False,
        label="Approval Note",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 2, "id": "id_exp_approval"}
        ),
    )


# ---------------------------------------------------------------------------
# Student Finance Notes
# ---------------------------------------------------------------------------


class StudentFinanceNotesForm(forms.Form):
    sponsor = forms.CharField(
        required=False,
        max_length=200,
        label="Sponsor / Scholarship Body",
        widget=forms.TextInput(attrs={"class": "form-control", "id": "id_sponsor"}),
    )
    payment_plan_notes = forms.CharField(
        required=False,
        label="Payment Plan Notes",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 4, "id": "id_payment_plan_notes"}
        ),
    )


# ---------------------------------------------------------------------------
# Fee Reminders
# ---------------------------------------------------------------------------


class FeeReminderForm(forms.Form):
    title = forms.CharField(
        max_length=200,
        label="Reminder Title",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "id": "id_reminder_title",
                "placeholder": "e.g. Term 2 Fees Reminder",
            }
        ),
    )
    body = forms.CharField(
        label="Message",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 5, "id": "id_reminder_body"}
        ),
    )
    student_ids = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_reminder_student_ids"}),
        help_text="Comma-separated student PKs. Leave blank to send to all outstanding students.",
    )

    def clean_student_ids(self):
        raw = self.cleaned_data.get("student_ids", "")
        if not raw.strip():
            return []
        try:
            return [int(s.strip()) for s in raw.split(",") if s.strip()]
        except ValueError:
            raise forms.ValidationError("Invalid student IDs.")


# ---------------------------------------------------------------------------
# Ledger / Report Filters
# ---------------------------------------------------------------------------


class LedgerFilterForm(forms.Form):
    account = forms.ModelChoiceField(
        queryset=Account.objects.filter(is_active=True).order_by("code"),
        required=False,
        empty_label="All accounts",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_ledger_account"}),
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date", "id": "id_ledger_date_from"}
        ),
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date", "id": "id_ledger_date_to"}
        ),
    )


class OutstandingFilterForm(forms.Form):
    term = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_out_term"}),
    )
    school_class = forms.ModelChoiceField(
        queryset=None,
        required=False,
        empty_label="All classes",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_out_class"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.academics.models import SchoolClass, Term

        self.fields["term"].queryset = Term.objects.select_related(
            "academic_year"
        ).order_by("-academic_year__start_date", "start_date")
        self.fields["school_class"].queryset = SchoolClass.objects.select_related(
            "academic_year"
        ).order_by("academic_year__start_date", "level_order")


def default_report_period():
    """
    The (year, term) a report page opens on: the current term, otherwise the
    current or latest year with no term — which reads as "Whole year".
    """
    from apps.academics.models import AcademicYear
    from apps.academics.selectors import get_current_term

    term = get_current_term()
    if term is not None:
        return term.academic_year, term
    year = (
        AcademicYear.objects.filter(is_current=True).first()
        or AcademicYear.objects.order_by("-start_date").first()
    )
    return year, None


class ReportPeriodForm(forms.Form):
    """
    Year + term for the report pages; an empty term means "Whole year".

    ``year`` is required by ``clean()`` rather than by the field, so a link that
    carries only ``?term=`` — which every older link on these pages does — still
    resolves: the year is read off the term instead of being demanded twice.

    The term choices belong to one year, and changing the year reloads the page
    with that year alone. A stale term from another year is refused by
    ``clean()`` rather than quietly reported against the wrong window.
    """

    year = forms.ModelChoiceField(
        queryset=None,
        required=False,
        empty_label=None,
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "id_report_year",
                "onchange": "if (this.value) { window.location = '?year=' + this.value; }",
            }
        ),
    )
    term = forms.ModelChoiceField(
        queryset=None,
        required=False,
        empty_label="Whole year",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_report_term"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.academics.models import AcademicYear, Term

        self.fields["year"].queryset = AcademicYear.objects.order_by("-start_date")

        if self.is_bound:
            raw_year = self.data.get(self.add_prefix("year"))
        else:
            initial_year = self.initial.get("year")
            raw_year = getattr(initial_year, "pk", initial_year)
        try:
            year_id = int(raw_year) if raw_year not in (None, "") else None
        except (TypeError, ValueError):
            year_id = None

        terms = Term.objects.select_related("academic_year")
        if year_id is not None:
            terms = terms.filter(academic_year_id=year_id)
        self.fields["term"].queryset = terms.order_by("start_date", "pk")

    def clean(self):
        cleaned = super().clean()
        year = cleaned.get("year")
        term = cleaned.get("term")
        if term is not None:
            if year is None:
                year = term.academic_year
            elif term.academic_year_id != year.pk:
                raise forms.ValidationError(
                    "That term belongs to a different year. Pick the year first."
                )
        if year is None:
            raise forms.ValidationError("Choose a year.")
        cleaned["year"] = year
        return cleaned
