"""
apps/finance/views.py
----------------------
All CBVs for the finance module.

Access rules:
  - Write views (record payment, post invoices, etc.) require role=finance.
  - Read-only views (reports, statements) allow admin and director too.
  - Students / parents can access only their own statement.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView, View

from apps.academics.models import SchoolClass, Term
from apps.core.exports import XLSX_CONTENT_TYPE
from apps.core.mixins import (
    FinanceRequiredMixin,
    RoleRequiredMixin,
)
from apps.core.money import ZERO, currency_code

from . import selectors, services
from .forms import (
    AdjustmentForm,
    ExpenseAccountForm,
    ExpenseForm,
    FeeReminderForm,
    FeeStructureCreateForm,
    FeeStructureUploadForm,
    InvoiceGenerateForm,
    LedgerFilterForm,
    OutstandingFilterForm,
    PaymentRecordForm,
    PaymentReversalForm,
    ReportPeriodForm,
    StudentFinanceNotesForm,
    default_report_period,
)
from .models import (
    FeeItem,
    FeeStructure,
    FeeStructureUploadJob,
    Invoice,
    Payment,
    StudentFeeElection,
)

# Mixin for Finance write + Admin/Director read
FINANCE_READ_ROLES = ["finance", "admin", "director"]


class FinanceReadMixin(RoleRequiredMixin):
    allowed_roles = FINANCE_READ_ROLES


class FinanceWriteMixin(FinanceRequiredMixin):
    """Write operations restricted to Finance role only."""


def _int_param(request, name: str) -> int | None:
    """
    A filter id from the query string, or ``None``.

    Handing the raw string to ``filter(term_id=...)`` works right up until
    somebody edits the URL, at which point a letter in the parameter is a 500 on
    a money page. The pages keep their own selected value for the form to echo.
    """
    raw = request.GET.get(name)
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _report_period(request):
    """
    The (form, year, term) a report page is showing.

    An empty query string opens on the current term — the officer should not
    have to pick what the school is in. A query string that fails validation (a
    hand-edited year, a term from another year) renders the form's errors and no
    figure at all, never a different window than the one asked for.

    A term of ``None`` is the "Whole year" mode, not a missing filter.
    """
    if request.GET:
        form = ReportPeriodForm(request.GET)
        if form.is_valid():
            return form, form.cleaned_data["year"], form.cleaned_data.get("term")
        return form, None, None
    year, term = default_report_period()
    form = ReportPeriodForm(
        initial={
            "year": year.pk if year is not None else None,
            "term": term.pk if term is not None else None,
        }
    )
    return form, year, term


def _filter_lists() -> dict:
    """The term and class dropdowns the list pages filter with."""
    return {
        "all_terms": Term.objects.select_related("academic_year").order_by(
            "-academic_year__start_date", "start_date"
        ),
        "all_classes": SchoolClass.objects.select_related("academic_year").order_by(
            "academic_year__start_date", "level_order", "name"
        ),
    }


def _upload_rows(job: FeeStructureUploadJob) -> dict:
    """
    The parsed upload rows in a shape a template can read.

    ``validate_fee_structure_rows`` stores its bookkeeping under keys that start
    with an underscore (``_row``, ``_errors``) so they cannot collide with a
    column the officer typed. Django refuses to resolve such a name in a
    template at all — the whole page is a syntax error, not a blank cell — so
    they are re-exposed here under plain names. The same conversion
    ``apps.students.selectors.bulk_import_rows`` does for the pupil import.

    This lives in the view, not in ``selectors``, on purpose: it shapes a page's
    context, it is not a money figure, and the money boundary in
    ``apps.finance.interface`` is a contract that only grows when the checkpoint
    that owns it says so.
    """

    def display(row: dict) -> dict:
        return {
            **row,
            "row_number": row.get("_row"),
            "errors": row.get("_errors") or [],
        }

    return {
        "preview_rows": [display(row) for row in job.preview_rows],
        "error_rows": [display(row) for row in job.error_rows],
    }


def _electable_rows(item: FeeItem) -> dict:
    """
    The pupils an election screen may tick, and the ones already ticked.

    The roster is exactly the one ``_billing_plan`` bills from — pupils still
    in school whose ``current_class`` is the structure's class — because a
    screen that offers a pupil the billing run will then skip promises a fee
    nobody charges. The checkboxes are therefore a view of that rule, not a
    second opinion about who is in the class.

    Ticked means a ``StudentFeeElection`` with ``included=True``. For an
    optional item there is no row for "out" that differs from no row at all;
    both mean "not on this pupil's bill", so an explicit ``included=False`` row
    counts as unticked and nothing is gained by keeping the two apart.

    Like ``_upload_rows``, this lives in the view rather than in ``selectors``:
    it shapes a page, it is not a money figure, and the contract in
    ``apps.finance.interface`` only grows when the checkpoint that owns it says
    so.
    """
    from apps.students.models import Student

    students = list(
        Student.objects.filter(
            current_class_id=item.structure.school_class_id,
            status__in=services.BILLABLE_STATUSES,
        )
        .select_related("user")
        .order_by("admission_number")
    )
    elected_ids = set(
        StudentFeeElection.objects.filter(
            fee_item=item, included=True
        ).values_list("student_id", flat=True)
    )
    rows = [
        {
            "student": student,
            "name": student.user.full_name,
            "admission_number": student.admission_number,
            "status": student.get_status_display(),
            "elected": student.pk in elected_ids,
        }
        for student in students
    ]
    return {
        "rows": rows,
        "roster_total": len(rows),
        "elected_total": len(elected_ids & {student.pk for student in students}),
        "elected_ids": elected_ids,
        "students_by_id": {student.pk: student for student in students},
    }


# ---------------------------------------------------------------------------
# Dashboard overview
# ---------------------------------------------------------------------------


class FinanceOverviewView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/overview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.academics.selectors import get_current_term

        term = get_current_term()
        ctx["term"] = term
        if term:
            ctx.update(selectors.dashboard_kpis(term=term))
        ctx["currency"] = currency_code(self.request)
        return ctx


# ---------------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------------


class AccountListView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/account_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["accounts"] = selectors.account_list(active_only=False)
        ctx["form"] = ExpenseAccountForm()
        ctx["currency"] = currency_code(self.request)
        return ctx


class AccountAddExpenseView(FinanceWriteMixin, View):
    def post(self, request, *args, **kwargs):
        form = ExpenseAccountForm(request.POST)
        if form.is_valid():
            try:
                account = services.create_expense_account(
                    code=form.cleaned_data["code"],
                    name=form.cleaned_data["name"],
                    description=form.cleaned_data.get("description", ""),
                    actor=request.user,
                    request=request,
                )
                messages.success(request, f"Account '{account.name}' created.")
            except ValidationError as e:
                messages.error(request, str(e))
        else:
            messages.error(request, "Please fix the form errors.")
        return redirect("finance:account_list")


# ---------------------------------------------------------------------------
# Fee Structures
# ---------------------------------------------------------------------------


class FeeStructureListView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/fee_structure_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_id = _int_param(self.request, "class")
        term_id = _int_param(self.request, "term")
        ctx["structures"] = selectors.fee_structure_list(
            class_id=class_id,
            term_id=term_id,
        )
        ctx["selected_class_id"] = class_id
        ctx["selected_term_id"] = term_id
        ctx["currency"] = currency_code(self.request)
        ctx.update(_filter_lists())
        return ctx


class FeeStructureDetailView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/fee_structure_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        structure = get_object_or_404(FeeStructure, pk=kwargs["pk"])
        ctx["structure"] = structure
        # How many pupils each optional item is ticked for, counted on the list
        # rather than one query per row. Read through the template's own item
        # loop so the badge and the "who takes this" page cannot disagree.
        ctx["items"] = list(
            structure.items.annotate(
                elected_count=Count("elections", filter=Q(elections__included=True))
            )
        )
        ctx["currency"] = currency_code(self.request)
        return ctx


class FeeStructureCreateView(FinanceWriteMixin, TemplateView):
    template_name = "features/finance/fee_structure_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = FeeStructureCreateForm(self.request.POST or None)
        ctx["currency"] = currency_code(self.request)
        return ctx

    def post(self, request, *args, **kwargs):
        form = FeeStructureCreateForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data())

        # Collect fee items from POST: label_N, amount_N, compulsory_N
        items = []
        i = 0
        while f"label_{i}" in request.POST:
            label = request.POST.get(f"label_{i}", "").strip()
            amount_str = request.POST.get(f"amount_{i}", "0").strip()
            if label:
                try:
                    from decimal import Decimal

                    amount = Decimal(amount_str)
                    items.append(
                        {
                            "label": label,
                            "amount": amount,
                            "ordering": i,
                            # Transport and meals differ between two children in
                            # the same class; the model has always had the flag
                            # and the Excel path sets it, so the form must too.
                            "compulsory": request.POST.get(f"compulsory_{i}") == "on",
                        }
                    )
                except Exception:  # noqa: BLE001, S110
                    pass
            i += 1

        if not items:
            messages.error(request, "Add at least one fee item.")
            return self.render_to_response(self.get_context_data())

        try:
            structure = services.create_fee_structure(
                school_class=form.cleaned_data["school_class"],
                term=form.cleaned_data["term"],
                items=items,
                note=form.cleaned_data.get("note", ""),
                due_date=form.cleaned_data.get("due_date"),
                actor=request.user,
                request=request,
            )
            messages.success(request, f"Fee structure v{structure.version} created.")
            return redirect("finance:fee_structure_detail", pk=structure.pk)
        except ValidationError as e:
            messages.error(request, str(e))
        return self.render_to_response(self.get_context_data())


class FeeItemStudentsView(FinanceWriteMixin, TemplateView):
    """
    "Who takes this?" — choose the pupils in the class who are billed an item.

    The structure page is readable by Admin and Director, but naming which
    children a school charges is the bursar's decision, so this page is
    Finance-only, the same way Take Payment is.

    It only tags optional items. A compulsory item needs no election — it is on
    every bill in the class — and the service refuses to opt one pupil out of it,
    because that is a waiver with no ledger entry behind it.
    """

    template_name = "features/finance/fee_item_students.html"

    def get_item(self) -> FeeItem:
        return get_object_or_404(
            FeeItem.objects.select_related(
                "structure__school_class", "structure__term__academic_year"
            ),
            pk=self.kwargs["pk"],
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        item = self.get_item()
        ctx["item"] = item
        ctx["structure"] = item.structure
        ctx.update(_electable_rows(item))
        ctx["currency"] = currency_code(self.request)
        # An election changes future bills only: ``_billing_plan`` reads them
        # when a run posts, and a posted invoice is the record of what was
        # billed. Saying so on the page stops an officer "fixing" a pupil who
        # already holds a bill this way.
        ctx["invoices_posted"] = Invoice.objects.filter(
            fee_structure=item.structure, status=Invoice.Status.POSTED
        ).exists()
        return ctx

    def post(self, request, *args, **kwargs):
        item = self.get_item()
        if item.compulsory:
            messages.error(
                request,
                f"{item.label} is compulsory for {item.structure.school_class}, so "
                "every pupil is already billed it. Take it off one bill with a "
                "waiver adjustment instead.",
            )
            return redirect("finance:fee_structure_detail", pk=item.structure_id)

        election = _electable_rows(item)
        wanted: set[int] = set()
        for raw in request.POST.getlist("students"):
            try:
                wanted.add(int(raw))
            except (TypeError, ValueError):
                continue

        added = removed = 0
        with transaction.atomic():
            # Only the roster is walked, so an id pasted into the form for a
            # pupil in another class never reaches the service.
            for student_id, student in election["students_by_id"].items():
                include = student_id in wanted
                if include == (student_id in election["elected_ids"]):
                    continue
                services.set_student_fee_item(
                    student=student,
                    fee_item=item,
                    included=include,
                    actor=request.user,
                    request=request,
                )
                if include:
                    added += 1
                else:
                    removed += 1

        if added or removed:
            parts = []
            if added:
                parts.append(f"{added} pupil{'s' if added != 1 else ''} now billed")
            if removed:
                parts.append(f"{removed} taken off")
            messages.success(request, f"{item.label}: " + ", ".join(parts) + ".")
        else:
            messages.info(request, f"{item.label}: the selection is unchanged.")
        return redirect("finance:fee_item_students", pk=item.pk)


class FeeStructureTemplateDownloadView(FinanceReadMixin, View):
    def get(self, request, *args, **kwargs):
        data = services.build_fee_structure_template_bytes()
        response = HttpResponse(
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            'attachment; filename="fee_structure_template.xlsx"'
        )
        return response


class FeeStructureUploadView(FinanceWriteMixin, TemplateView):
    template_name = "features/finance/fee_structure_upload.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = FeeStructureUploadForm(
            self.request.POST or None, self.request.FILES or None
        )
        ctx["recent_jobs"] = FeeStructureUploadJob.objects.order_by("-created_at")[:10]
        ctx["currency"] = currency_code(self.request)
        return ctx

    def post(self, request, *args, **kwargs):
        form = FeeStructureUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Please fix the form errors.")
            return self.render_to_response(self.get_context_data())
        job = services.start_fee_structure_upload(
            uploaded_file=request.FILES["file"],
            target_class=form.cleaned_data["school_class"],
            term=form.cleaned_data["term"],
            uploaded_by=request.user,
        )
        messages.info(request, "File uploaded. Validation in progress...")
        return redirect("finance:fee_structure_upload_preview", pk=job.pk)


class FeeStructureUploadPreviewView(FinanceWriteMixin, TemplateView):
    template_name = "features/finance/fee_structure_upload_preview.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["job"] = get_object_or_404(FeeStructureUploadJob, pk=kwargs["pk"])
        ctx.update(_upload_rows(ctx["job"]))
        ctx["currency"] = currency_code(self.request)
        return ctx


class FeeStructureUploadCommitView(FinanceWriteMixin, View):
    def post(self, request, *args, **kwargs):
        job = get_object_or_404(FeeStructureUploadJob, pk=kwargs["pk"])
        try:
            structure = services.commit_fee_structure_upload(
                job=job, actor=request.user, request=request
            )
            messages.success(request, f"Fee structure v{structure.version} committed.")
            return redirect("finance:fee_structure_detail", pk=structure.pk)
        except ValidationError as e:
            messages.error(request, str(e))
        return redirect("finance:fee_structure_upload_preview", pk=job.pk)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


class InvoiceListView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/invoice_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        term_id = _int_param(self.request, "term")
        class_id = _int_param(self.request, "class")
        status = self.request.GET.get("status") or None
        ctx["invoices"] = selectors.invoice_list(
            term_id=term_id,
            class_id=class_id,
            status=status if status in Invoice.Status.values else None,
        )
        ctx["selected_term_id"] = term_id
        ctx["selected_class_id"] = class_id
        ctx["selected_status"] = status if status in Invoice.Status.values else ""
        ctx["status_choices"] = Invoice.Status.choices
        ctx["currency"] = currency_code(self.request)
        ctx.update(_filter_lists())
        return ctx


class InvoiceDetailView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/invoice_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["invoice"] = selectors.get_invoice(kwargs["pk"])
        ctx["currency"] = currency_code(self.request)
        return ctx


class InvoiceGenerateView(FinanceWriteMixin, TemplateView):
    template_name = "features/finance/invoice_generate.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = InvoiceGenerateForm(self.request.GET or None)
        ctx["currency"] = currency_code(self.request)
        ctx["preview"] = None
        if self.request.GET.get("term"):
            form = InvoiceGenerateForm(self.request.GET)
            if form.is_valid():
                ctx["preview"] = services.preview_invoice_generation(
                    term=form.cleaned_data["term"],
                    school_class=form.cleaned_data.get("school_class"),
                )
                ctx["form"] = form
        return ctx

    def post(self, request, *args, **kwargs):
        form = InvoiceGenerateForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please select a valid term.")
            return self.render_to_response(self.get_context_data())
        try:
            invoices = services.post_invoices(
                term=form.cleaned_data["term"],
                school_class=form.cleaned_data.get("school_class"),
                actor=request.user,
                request=request,
            )
            messages.success(
                request, f"{len(invoices)} invoice(s) posted successfully."
            )
        except Exception as e:  # noqa: BLE001
            messages.error(request, str(e))
        return redirect("finance:invoice_list")


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def _payment_picker_context(*, term) -> dict:
    """
    What each pupil owes, in the shape the Take Payment picker draws from.

    A cashier's queue should not need a second page to answer "what do I take
    from this child?", so the class filter, the search and the pupil card are all
    fed from one read taken here: the roster, the term's per-pupil position
    (``student_fee_rows``) and the live arrears across every term
    (``arrears_rows``). Three whole-set reads, however many pupils the school has.

    Two figures are kept apart on purpose. ``due`` is what this term's invoices
    still ask for and is ``None`` when no term is current — a page with no term
    cannot pretend a pupil owes nothing *this* term. ``arrears`` is what earlier
    terms still ask for, which is why it is the all-terms figure less this term's.
    ``total`` is their sum, computed here in Decimal so the browser never adds two
    money strings and invents a rounding difference.

    A pupil in credit is not in arrears: both figures floor at zero.
    """
    from apps.students.models import Student

    students = list(
        Student.objects.filter(status__in=services.BILLABLE_STATUSES)
        .select_related("user", "current_class")
        .order_by("admission_number")
    )
    this_term = (
        {row["student"].pk: row for row in selectors.student_fee_rows(term=term)}
        if term is not None
        else {}
    )
    arrears = {
        row["student_id"]: row["outstanding"] for row in selectors.arrears_rows()
    }

    classes: dict[int, dict] = {}
    pupils = []
    for student in students:
        class_id = student.current_class_id or 0
        class_name = (
            student.current_class.name if student.current_class else "No class"
        )
        bucket = classes.setdefault(
            class_id, {"id": class_id, "name": class_name, "count": 0}
        )
        bucket["count"] += 1

        row = this_term.get(student.pk)
        due = row["outstanding"] if row else ZERO
        owed_before = max(ZERO, arrears.get(student.pk, ZERO) - max(due, ZERO))
        pupils.append(
            {
                "id": student.pk,
                "name": student.user.full_name,
                "admission": student.admission_number,
                "class_id": class_id,
                "class_name": class_name,
                "billed": row["billed"] if row else ZERO,
                "paid": row["collected"] if row else ZERO,
                "due": due if term is not None else None,
                "arrears": owed_before,
                "total": owed_before + max(ZERO, due),
            }
        )
    return {"pupil_data": pupils, "class_options": list(classes.values()), "term": term}


class PaymentListView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/payment_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["payments"] = selectors.payment_list(include_reversals=False)
        ctx["currency"] = currency_code(self.request)
        return ctx


class PaymentDetailView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/payment_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["payment"] = selectors.get_payment(kwargs["pk"])
        ctx["currency"] = currency_code(self.request)
        return ctx


class PaymentRecordView(FinanceWriteMixin, TemplateView):
    template_name = "features/finance/payment_record.html"

    def _form(self, data=None, files=None):
        initial = {"payment_date": date.today()}
        if data is None:
            # Fresh page: a key for this submission, which the service honours as
            # the double-click guard. Re-rendered after a failed POST keeps the
            # posted value, so a corrected resubmission cannot post twice.
            initial["idempotency_key"] = uuid4().hex
            # A bill page links here with ?student= so the cashier is not asked to
            # find the pupil again.
            student_id = _int_param(self.request, "student")
            if student_id:
                initial["student"] = student_id
            return PaymentRecordForm(None, initial=initial)
        return PaymentRecordForm(data, files, initial=initial)

    def _selected_student_id(self) -> int | None:
        """
        The pupil the card should describe.

        On a POST that is the submitted one, not ``?student=``: a re-render after
        a validation error must describe the pupil the officer actually typed,
        and a stale query string would show somebody else's balance.
        """
        raw = (
            self.request.POST.get("student")
            if self.request.method == "POST"
            else self.request.GET.get("student")
        )
        try:
            return int(raw) if raw else None
        except (TypeError, ValueError):
            return None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = self._form(self.request.POST or None, self.request.FILES or None)
        from apps.academics.selectors import get_current_term

        picker = _payment_picker_context(term=get_current_term())
        ctx.update(picker)
        ctx["selected"] = {
            pupil["id"]: pupil for pupil in picker["pupil_data"]
        }.get(self._selected_student_id())
        ctx["currency"] = currency_code(self.request)
        return ctx

    def post(self, request, *args, **kwargs):
        form = self._form(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Please fix the form errors.")
            return self.render_to_response(self.get_context_data())
        try:
            payment = services.record_payment(
                student=form.cleaned_data["student"],
                amount=form.cleaned_data["amount"],
                method=form.cleaned_data["method"],
                reference=form.cleaned_data["reference"],
                payment_date=form.cleaned_data["payment_date"],
                payer_name=form.cleaned_data.get("payer_name", ""),
                attachment=form.cleaned_data.get("attachment"),
                notes=form.cleaned_data.get("notes", ""),
                idempotency_key=form.cleaned_data.get("idempotency_key") or None,
                recorded_by=request.user,
                request=request,
            )
            messages.success(
                request, f"Payment {payment.receipt_number} recorded successfully."
            )
            return redirect("finance:payment_detail", pk=payment.pk)
        except ValidationError as e:
            for field, errs in (
                e.message_dict.items()
                if hasattr(e, "message_dict")
                else {None: [str(e)]}.items()
            ):
                for err in errs:
                    messages.error(request, err)
        return self.render_to_response(self.get_context_data())


class PaymentReceiptPdfView(FinanceReadMixin, View):
    """
    The printed receipt records itself.

    The first copy is the receipt; every later copy is stamped DUPLICATE, so
    one receipt cannot be presented twice as proof of two payments. The count
    only moves once a PDF was actually produced — a 503 from the PDF engine is
    not a print.
    """

    def get(self, request, *args, **kwargs):
        payment = selectors.get_payment(kwargs["pk"])
        duplicate = payment.print_count >= 1
        from django.template.loader import render_to_string

        html = render_to_string(
            "features/finance/payment_receipt_pdf.html",
            {
                "payment": payment,
                "duplicate": duplicate,
                "currency": currency_code(self.request),
                "SCHOOL_NAME": __import__(
                    "django.conf", fromlist=["settings"]
                ).settings.SCHOOL_NAME,
                "request": request,
            },
        )
        from apps.core.pdf import pdf_response

        response = pdf_response(
            html=html,
            filename=f"receipt_{payment.receipt_number}.pdf",
            base_url=request.build_absolute_uri("/"),
        )
        if response.status_code == 200:
            services.mark_receipt_printed(
                payment=payment, actor=request.user, request=request
            )
        return response


class PaymentReverseView(FinanceWriteMixin, TemplateView):
    template_name = "features/finance/payment_reverse_confirm.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["payment"] = get_object_or_404(Payment, pk=kwargs["pk"])
        ctx["form"] = PaymentReversalForm()
        return ctx

    def post(self, request, *args, **kwargs):
        payment = get_object_or_404(Payment, pk=kwargs["pk"])
        form = PaymentReversalForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please provide a reversal reason.")
            return self.render_to_response({"payment": payment, "form": form})
        try:
            reversal = services.reverse_payment(
                payment=payment,
                reason=form.cleaned_data["reason"],
                actor=request.user,
                request=request,
            )
            messages.success(
                request,
                f"Payment {payment.receipt_number} reversed. "
                f"Reversal receipt: {reversal.receipt_number}",
            )
        except ValidationError as e:
            messages.error(request, str(e))
        return redirect("finance:payment_list")


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------


class AdjustmentCreateView(FinanceWriteMixin, TemplateView):
    template_name = "features/finance/adjustment_form.html"

    def _form(self, data=None):
        initial = {}
        student_id = _int_param(self.request, "student")
        if student_id:
            initial["student"] = student_id
        invoice_id = _int_param(self.request, "invoice")
        if invoice_id:
            initial["invoice"] = invoice_id
        return AdjustmentForm(data, initial=initial)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = self._form(self.request.POST or None)
        ctx["currency"] = currency_code(self.request)
        return ctx

    def post(self, request, *args, **kwargs):
        form = self._form(request.POST)
        if not form.is_valid():
            messages.error(request, "Please fix the form errors.")
            return self.render_to_response(self.get_context_data())
        try:
            adj = services.apply_adjustment(
                student=form.cleaned_data["student"],
                invoice=form.cleaned_data.get("invoice"),
                adjustment_type=form.cleaned_data["adjustment_type"],
                amount=form.cleaned_data["amount"],
                reason=form.cleaned_data["reason"],
                actor=request.user,
                request=request,
            )
            if adj.posted:
                messages.success(request, f"Adjustment of {adj.amount} applied.")
            else:
                messages.info(
                    request,
                    f"Write-off of {adj.amount} submitted for approval — "
                    "an Admin must post it before the debt is released.",
                )
            return redirect(
                "finance:student_statement", pk=form.cleaned_data["student"].pk
            )
        except (ValidationError, ValueError) as e:
            messages.error(request, str(e))
        return self.render_to_response(self.get_context_data())


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


class LedgerView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/ledger.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = LedgerFilterForm(self.request.GET or None)
        ctx["form"] = form
        account_id = date_from = date_to = None
        if form.is_valid():
            account_id = form.cleaned_data.get("account")
            account_id = account_id.pk if account_id else None
            date_from = form.cleaned_data.get("date_from")
            date_to = form.cleaned_data.get("date_to")
        ctx["lines"] = selectors.ledger_entries(
            account_id=account_id, date_from=date_from, date_to=date_to
        )[:500]
        ctx["currency"] = currency_code(self.request)
        return ctx


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


class TrialBalanceView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/trial_balance.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # The totals come from the interface, not from re-adding the rows here,
        # so this page and the Director's trial balance cannot disagree about
        # whether the ledger balances.
        books = selectors.trial_balance_at()
        ctx["rows"] = books["rows"]
        ctx["as_of"] = books["as_of"]
        ctx["total_debits"] = books["total_debits"]
        ctx["total_credits"] = books["total_credits"]
        ctx["balanced"] = books["total_debits"] == books["total_credits"]
        ctx["currency"] = currency_code(self.request)
        return ctx


class ReportSummaryView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/report_summary.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form, year, term = _report_period(self.request)
        ctx.update(form=form, year=year, term=term, summary=None)
        if year is not None:
            ctx["summary"] = selectors.report_summary(
                term_id=term.pk if term else None, year_id=year.pk
            )
        ctx["currency"] = currency_code(self.request)
        return ctx


class OutstandingFeesView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/outstanding_fees.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = OutstandingFilterForm(self.request.GET or None)
        ctx["form"] = form
        ctx["rows"] = []
        if form.is_valid():
            term = form.cleaned_data["term"]
            school_class = form.cleaned_data.get("school_class")
            class_id = school_class.pk if school_class else None
            ctx["term"] = term
            ctx["selected_class_name"] = school_class.name if school_class else None
            ctx["rows"] = selectors.outstanding_students(
                term_id=term.pk,
                class_id=class_id,
            )
            # The headline is the term's own figure from the interface, never the
            # sum of the rows beneath it: a pupil holding a credit is left off a
            # chasing list on purpose, so adding the rows would understate what
            # the school is owed and disagree with the Director's page.
            ctx["totals"] = selectors.fee_totals(term=term, class_id=class_id)
        ctx["currency"] = currency_code(self.request)
        return ctx


class OutstandingFeesExportView(FinanceReadMixin, View):
    def get(self, request, *args, **kwargs):
        form = OutstandingFilterForm(request.GET)
        if not form.is_valid():
            messages.error(request, "Please select a term first.")
            return redirect("finance:outstanding_fees")
        rows = selectors.outstanding_students(
            term_id=form.cleaned_data["term"].pk,
            class_id=(
                form.cleaned_data.get("school_class").pk
                if form.cleaned_data.get("school_class")
                else None
            ),
        )
        data = services.build_outstanding_excel(rows, actor=request.user)
        response = HttpResponse(data, content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = 'attachment; filename="outstanding_fees.xlsx"'
        return response


# ---------------------------------------------------------------------------
# Student Statement
# ---------------------------------------------------------------------------


class StudentStatementView(LoginRequiredMixin, TemplateView):
    template_name = "features/finance/student_statement.html"

    def dispatch(self, request, *args, **kwargs):
        pk = kwargs.get("pk")
        # Finance / Admin / Director can view any statement
        if request.user.role in FINANCE_READ_ROLES or request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)
        # Students can view only their own
        if (
            request.user.role == "student"
            and hasattr(request.user, "student_profile")
            and str(request.user.student_profile.pk) == str(pk)
        ):
            return super().dispatch(request, *args, **kwargs)
        # Parents can view their children's statements
        if request.user.role == "parent" and hasattr(request.user, "parent_profile"):
            child_ids = list(
                request.user.parent_profile.children_links.values_list(
                    "student_id", flat=True
                )
            )
            if int(pk) in child_ids:
                return super().dispatch(request, *args, **kwargs)
        raise PermissionDenied("You do not have permission to view this statement.")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        term_id = _int_param(self.request, "term")
        term = Term.objects.filter(pk=term_id).first() if term_id else None
        ctx.update(selectors.student_statement(student_id=kwargs["pk"], term=term))
        ctx["currency"] = currency_code(self.request)
        ctx["notes_form"] = StudentFinanceNotesForm(
            initial={
                "sponsor": ctx["student"].sponsor,
                "payment_plan_notes": ctx["student"].payment_plan_notes,
            }
        )
        # A pupil or guardian reading their own statement must not be handed an
        # empty sponsor/plan box that only Finance can actually save.
        user = self.request.user
        ctx["is_finance_reader"] = (
            user.role in FINANCE_READ_ROLES or user.is_superuser
        )
        ctx["all_terms"] = (
            Term.objects.select_related("academic_year").order_by(
                "-academic_year__start_date", "start_date"
            )
        )
        return ctx


class StudentStatementPdfView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        pk = kwargs["pk"]
        # Same access rules as StudentStatementView
        if (
            request.user.role not in FINANCE_READ_ROLES
            and not request.user.is_superuser
        ):
            if request.user.role == "student":
                if not (
                    hasattr(request.user, "student_profile")
                    and str(request.user.student_profile.pk) == str(pk)
                ):
                    raise PermissionDenied()
            elif request.user.role == "parent":
                if not hasattr(request.user, "parent_profile"):
                    raise PermissionDenied()
                child_ids = list(
                    request.user.parent_profile.children_links.values_list(
                        "student_id", flat=True
                    )
                )
                if int(pk) not in child_ids:
                    raise PermissionDenied()
            else:
                raise PermissionDenied()

        # The printed statement must be the one the officer was looking at, so the
        # same ?term= filter is honoured here too.
        term_id = _int_param(request, "term")
        term = Term.objects.filter(pk=term_id).first() if term_id else None
        stmt = selectors.student_statement(student_id=pk, term=term)
        from django.conf import settings as _settings
        from django.template.loader import render_to_string

        html = render_to_string(
            "features/finance/student_statement_pdf.html",
            {
                **stmt,
                "currency": currency_code(self.request),
                "SCHOOL_NAME": _settings.SCHOOL_NAME,
                "request": request,
            },
        )
        from apps.core.pdf import pdf_response

        return pdf_response(
            html=html,
            filename=f'statement_{stmt["student"].admission_number}.pdf',
            base_url=request.build_absolute_uri("/"),
        )


class StudentFinanceNotesView(FinanceWriteMixin, View):
    def post(self, request, *args, **kwargs):
        from apps.students.selectors import get_student

        student = get_student(kwargs["pk"])
        form = StudentFinanceNotesForm(request.POST)
        if form.is_valid():
            services.update_student_finance_notes(
                student=student,
                sponsor=form.cleaned_data.get("sponsor", ""),
                payment_plan_notes=form.cleaned_data.get("payment_plan_notes", ""),
                actor=request.user,
                request=request,
            )
            messages.success(request, "Finance notes updated.")
        else:
            messages.error(request, "Could not save notes.")
        return redirect("finance:student_statement", pk=student.pk)


# ---------------------------------------------------------------------------
# Student self-service redirect (for student / parent dashboard link)
# ---------------------------------------------------------------------------


class MyStatementRedirectView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        if request.user.role == "student" and hasattr(request.user, "student_profile"):
            return redirect(
                "finance:student_statement", pk=request.user.student_profile.pk
            )
        if request.user.role == "parent" and hasattr(request.user, "parent_profile"):
            children = list(
                request.user.parent_profile.children_links.select_related("student")
                .filter(is_primary=True)
                .values_list("student_id", flat=True)
            )
            if children:
                return redirect("finance:student_statement", pk=children[0])
        raise PermissionDenied("No student account found.")


# ---------------------------------------------------------------------------
# Class Summary
# ---------------------------------------------------------------------------


class ClassSummaryView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/class_summary.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form, year, term = _report_period(self.request)
        school_class = get_object_or_404(SchoolClass, pk=kwargs["pk"])
        ctx.update(
            form=form, year=year, term=term, school_class=school_class, summary=None
        )
        if year is not None:
            ctx["summary"] = selectors.class_finance_summary(
                school_class_id=school_class.pk,
                term_id=term.pk if term else None,
                year_id=year.pk,
            )
        ctx["currency"] = currency_code(self.request)
        return ctx


# ---------------------------------------------------------------------------
# School Summary
# ---------------------------------------------------------------------------


class SchoolSummaryView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/school_summary.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form, year, term = _report_period(self.request)
        ctx.update(form=form, year=year, term=term, summary=None)
        if year is not None:
            ctx["summary"] = selectors.year_finance_summary(
                year_id=year.pk, term_id=term.pk if term else None
            )
        ctx["currency"] = currency_code(self.request)
        return ctx


class ClassReportsView(FinanceReadMixin, TemplateView):
    """
    Every class of a year, billed or not, for one term or the whole year.

    School Summary deliberately lists only classes that have been billed; this
    page exists so the officer can also see the class that has *not* been
    invoiced — the one a term is about to start without.
    """

    template_name = "features/finance/class_reports.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form, year, term = _report_period(self.request)
        ctx.update(form=form, year=year, term=term, summary=None, rows=[])
        if year is not None:
            summary = selectors.year_finance_summary(
                year_id=year.pk, term_id=term.pk if term else None
            )
            by_class = {row["class_id"]: row for row in summary["per_class"]}
            rows = []
            for school_class in (
                SchoolClass.objects.filter(academic_year=year)
                .select_related("academic_year")
                .order_by("level_order", "name")
            ):
                row = by_class.get(school_class.pk)
                rows.append(
                    {
                        "class": school_class,
                        "billed": row["billed"] if row else ZERO,
                        "paid": row["paid"] if row else ZERO,
                        "adjusted": row["adjusted"] if row else ZERO,
                        "outstanding": row["outstanding"] if row else ZERO,
                        "rate": row["rate"] if row else None,
                        "billed_any": row is not None,
                    }
                )
            ctx.update(summary=summary, rows=rows)
        ctx["currency"] = currency_code(self.request)
        return ctx


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


class ExpenseListView(FinanceReadMixin, TemplateView):
    template_name = "features/finance/expense_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["expenses"] = selectors.expense_list()[:200]
        ctx["currency"] = currency_code(self.request)
        return ctx


class ExpenseRecordView(FinanceWriteMixin, TemplateView):
    template_name = "features/finance/expense_form.html"

    def _form(self, data=None, files=None):
        initial = {"expense_date": date.today()}
        if data is None:
            return ExpenseForm(None, initial=initial)
        return ExpenseForm(data, files, initial=initial)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = self._form(
            self.request.POST or None, self.request.FILES or None
        )
        ctx["currency"] = currency_code(self.request)
        return ctx

    def post(self, request, *args, **kwargs):
        form = self._form(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Please fix the form errors.")
            return self.render_to_response(self.get_context_data())
        try:
            expense = services.record_expense(
                account=form.cleaned_data["account"],
                amount=form.cleaned_data["amount"],
                expense_date=form.cleaned_data["expense_date"],
                description=form.cleaned_data["description"],
                paid_via=form.cleaned_data["paid_via"],
                vendor=form.cleaned_data.get("vendor"),
                payee_name=form.cleaned_data.get("payee_name", ""),
                attachment=form.cleaned_data.get("attachment"),
                approval_note=form.cleaned_data.get("approval_note", ""),
                actor=request.user,
                request=request,
            )
            if expense.posted:
                messages.success(request, f"Expense of {expense.amount} posted.")
            else:
                messages.info(
                    request,
                    f"Claim of {expense.amount} submitted for approval — "
                    "an Admin must post it before the money leaves the account.",
                )
            return redirect("finance:expense_list")
        except (ValidationError, ValueError) as e:
            messages.error(request, str(e))
        return self.render_to_response(self.get_context_data())


# ---------------------------------------------------------------------------
# Fee Reminders
# ---------------------------------------------------------------------------


class SendRemindersView(FinanceWriteMixin, TemplateView):
    template_name = "features/finance/send_reminders.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = FeeReminderForm()
        # Pre-populate outstanding students for selection
        from apps.academics.selectors import get_current_term

        term = get_current_term()
        ctx["term"] = term
        ctx["outstanding"] = []
        if term:
            ctx["outstanding"] = selectors.outstanding_students(term_id=term.pk)
        ctx["currency"] = currency_code(self.request)
        return ctx

    def post(self, request, *args, **kwargs):
        form = FeeReminderForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please fix the form errors.")
            return self.render_to_response(self.get_context_data())
        student_ids = form.cleaned_data.get("student_ids", [])
        try:
            from django_q.tasks import async_task

            async_task(
                "apps.finance.tasks.send_fee_reminder_task",
                student_ids=student_ids,
                title=form.cleaned_data["title"],
                body=form.cleaned_data["body"],
                actor_id=request.user.pk,
            )
            messages.success(request, "Fee reminder queued for sending.")
        except Exception:  # noqa: BLE001
            # Fallback: send synchronously
            from apps.finance.tasks import send_fee_reminder_task

            send_fee_reminder_task(
                student_ids=student_ids,
                title=form.cleaned_data["title"],
                body=form.cleaned_data["body"],
                actor_id=request.user.pk,
            )
            messages.success(request, "Fee reminder sent.")
        return redirect("finance:send_reminders")
