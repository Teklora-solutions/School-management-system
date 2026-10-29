"""
apps/finance/selectors.py
--------------------------
Read-only queries for the finance module.
All functions return QuerySets or plain dicts. No writes here.

Everything below the "money read interface" banner is the single place a money
figure is derived in this project. Finance's own pages, the Director portal,
exports and reports all consume those functions, so a number on one screen
cannot quietly disagree with the same number on another. Add a figure by
extending that section — never by re-adding the arithmetic at a call site.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import (
    Count,
    DateField,
    DecimalField,
    F,
    Max,
    OuterRef,
    QuerySet,
    Subquery,
    Sum,
)
from django.db.models.functions import Coalesce, TruncMonth

from apps.core.money import percent as _money_percent
from apps.core.money import to_money

from .models import (
    Account,
    Adjustment,
    ApprovalRequest,
    Budget,
    Expense,
    FeeStructure,
    FeeStructureUploadJob,
    FinanceSettings,
    Invoice,
    InvoiceLine,
    JournalLine,
    Payment,
    PaymentAllocation,
    PaymentPlan,
    Vendor,
)

# ---------------------------------------------------------------------------
# Dashboard KPIs
# ---------------------------------------------------------------------------


def dashboard_kpis(*, term) -> dict:
    """
    Returns:
      collected_today, collected_this_week, collected_this_term,
      outstanding_total, recent_payments (last 10), class_collection_rates

    The per-class rows come from ``class_fee_rows`` — three GROUP BYs, not two
    queries per class — and are the same rows the Director's fee page renders.

    The three "collected" figures are on a receipt-date basis, the same window
    question ``cash_received`` answers, so they net a reversal the same way:
    every payment row dated in the window is summed, the flagged original and
    its negative reversal together.
    """
    today = date.today()
    week_start = today - timedelta(days=today.weekday())

    def _received(**date_filter):
        return _money_sum(Payment.objects.filter(**date_filter), "amount")

    collected_today = _received(payment_date=today)
    collected_this_week = _received(payment_date__gte=week_start)
    collected_this_term = _received(
        payment_date__gte=term.start_date, payment_date__lte=term.end_date
    )

    totals = fee_totals(term=term)

    recent_payments = (
        Payment.objects.filter(is_reversed=False)
        .select_related("student__user", "recorded_by")
        .order_by("-payment_date", "-created_at")[:10]
    )

    return {
        "collected_today": collected_today,
        "collected_this_week": collected_this_week,
        "collected_this_term": collected_this_term,
        "outstanding_total": totals["outstanding"],
        "total_billed": totals["billed"],
        "collection_rate": (
            totals["collection_rate"]
            if totals["collection_rate"] is not None
            else ZERO
        ),
        "recent_payments": recent_payments,
        "class_collection_rates": class_fee_rows(term=term),
        "term": term,
    }


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def account_list(*, type: str | None = None, active_only: bool = True) -> QuerySet:
    qs = Account.objects.all()
    if type:
        qs = qs.filter(type=type)
    if active_only:
        qs = qs.filter(is_active=True)
    return qs


# ---------------------------------------------------------------------------
# Fee Structures
# ---------------------------------------------------------------------------


def fee_structure_list(
    *, class_id: int | None = None, term_id: int | None = None
) -> QuerySet:
    qs = FeeStructure.objects.select_related(
        "school_class", "term", "created_by"
    ).prefetch_related("items")
    if class_id:
        qs = qs.filter(school_class_id=class_id)
    if term_id:
        qs = qs.filter(term_id=term_id)
    return qs


def get_active_fee_structure(*, class_id: int, term_id: int) -> FeeStructure | None:
    return (
        FeeStructure.objects.filter(
            school_class_id=class_id, term_id=term_id, is_active=True
        )
        .prefetch_related("items")
        .order_by("-version")
        .first()
    )


def get_fee_structure(pk: int) -> FeeStructure:
    return FeeStructure.objects.prefetch_related("items").get(pk=pk)


def get_fee_upload_job(pk: int) -> FeeStructureUploadJob:
    return FeeStructureUploadJob.objects.select_related(
        "uploaded_by", "target_class", "term"
    ).get(pk=pk)


def _paid_subquery():
    """
    Correlated SUM of the receipts still standing against the outer invoice.

    ``.live()`` is the whole point: a reversed receipt keeps its allocation rows
    (Module 6 A2), so a per-invoice balance that summed them plainly would still
    count money that was given back. Stated once here so no screen can forget it.
    """
    return (
        PaymentAllocation.objects.filter(invoice=OuterRef("pk"))
        .live()
        .values("invoice")
        .annotate(t=Sum("allocated_amount"))
        .values("t")
    )


def _adjusted_subquery():
    """Correlated SUM of the posted reductions against the outer invoice."""
    return (
        Adjustment.objects.filter(invoice=OuterRef("pk"))
        .live()
        .values("invoice")
        .annotate(t=Sum("amount"))
        .values("t")
    )


def _invoice_balance_annotations():
    """
    ``paid_here`` / ``adjusted_here`` / ``outstanding_here`` for one invoice.

    A page that lists bills needs each bill's own balance. This is the interface's
    answer — billed − collected − adjusted, the same definition ``fee_totals``
    uses for a whole term — so the row on a list cannot disagree with the total
    above it, and no template has to add anything up.
    """
    return {
        "paid_here": Coalesce(
            Subquery(_paid_subquery(), output_field=_MONEY), ZERO, output_field=_MONEY
        ),
        "adjusted_here": Coalesce(
            Subquery(_adjusted_subquery(), output_field=_MONEY),
            ZERO,
            output_field=_MONEY,
        ),
    }


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


def invoice_list(
    *,
    student_id: int | None = None,
    term_id: int | None = None,
    status: str | None = None,
    class_id: int | None = None,
) -> QuerySet:
    qs = (
        Invoice.objects.select_related(
            "student__user", "term", "fee_structure", "posted_by"
        )
        .prefetch_related("lines", "allocations")
        .annotate(**_invoice_balance_annotations())
        .annotate(
            outstanding_here=F("total_amount")
            - F("paid_here")
            - F("adjusted_here")
        )
    )
    if student_id:
        qs = qs.filter(student_id=student_id)
    if term_id:
        qs = qs.filter(term_id=term_id)
    if status:
        qs = qs.filter(status=status)
    if class_id:
        qs = qs.filter(student__current_class_id=class_id)
    return qs


def get_invoice(pk: int) -> Invoice:
    return (
        Invoice.objects.select_related(
            "student__user", "term", "fee_structure", "posted_by"
        )
        .prefetch_related("lines", "allocations__payment")
        .annotate(**_invoice_balance_annotations())
        .annotate(
            outstanding_here=F("total_amount")
            - F("paid_here")
            - F("adjusted_here")
        )
        .get(pk=pk)
    )


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def payment_list(
    *,
    student_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    include_reversals: bool = False,
) -> QuerySet:
    qs = Payment.objects.select_related(
        "student__user", "recorded_by"
    ).prefetch_related("allocations__invoice")
    if student_id:
        qs = qs.filter(student_id=student_id)
    if date_from:
        qs = qs.filter(payment_date__gte=date_from)
    if date_to:
        qs = qs.filter(payment_date__lte=date_to)
    if not include_reversals:
        qs = qs.filter(is_reversed=False)
    return qs


def get_payment(pk: int) -> Payment:
    return (
        Payment.objects.select_related("student__user", "recorded_by", "reverses")
        .prefetch_related("allocations__invoice")
        .get(pk=pk)
    )


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def ledger_entries(
    *,
    account_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> QuerySet:
    qs = JournalLine.objects.select_related(
        "entry", "account", "entry__posted_by"
    ).order_by("-entry__entry_date", "entry__reference")
    if account_id:
        qs = qs.filter(account_id=account_id)
    if date_from:
        qs = qs.filter(entry__entry_date__gte=date_from)
    if date_to:
        qs = qs.filter(entry__entry_date__lte=date_to)
    return qs


# ---------------------------------------------------------------------------
# Trial Balance
# ---------------------------------------------------------------------------


def trial_balance() -> list[dict]:
    """
    Returns a list of accounts with their debit and credit totals.
    sum(debit_total) == sum(credit_total) is the invariant that must hold.

    Rows are ``{"account", "debit_total", "credit_total", "net"}``; the totals
    and the balance check live in ``trial_balance_at``, which this delegates to,
    so the Finance page and the Director page cannot disagree about whether the
    ledger balances.
    """
    return trial_balance_at()["rows"]


# ---------------------------------------------------------------------------
# Student Statement
# ---------------------------------------------------------------------------


def student_statement(*, student_id: int, term=None) -> dict:
    """Full finance record for one student."""
    from apps.students.models import Student

    student = (
        Student.objects.select_related("user", "current_class")
        .prefetch_related("guardian_links__parent__user")
        .get(pk=student_id)
    )

    invoices_qs = (
        Invoice.objects.filter(student=student)
        .select_related("term", "fee_structure")
        .prefetch_related("lines", "allocations")
        .annotate(**_invoice_balance_annotations())
        .annotate(
            outstanding_here=F("total_amount")
            - F("paid_here")
            - F("adjusted_here")
        )
        .order_by("issued_date")
    )
    if term:
        invoices_qs = invoices_qs.filter(term=term)

    payments_qs = (
        Payment.objects.filter(student=student)
        .prefetch_related("allocations__invoice")
        .order_by("payment_date")
    )

    adjustments_qs = (
        Adjustment.objects.filter(student=student)
        .select_related("applied_by", "invoice")
        .order_by("created_at")
    )

    total_billed = invoices_qs.filter(status=Invoice.Status.POSTED).aggregate(
        t=Coalesce(Sum("total_amount"), Decimal(0), output_field=DecimalField())
    )["t"]
    total_paid = payments_qs.filter(is_reversed=False).aggregate(
        t=Coalesce(Sum("amount"), Decimal(0), output_field=DecimalField())
    )["t"]
    total_adjusted = adjustments_qs.live().aggregate(
        t=Coalesce(Sum("amount"), Decimal(0), output_field=DecimalField())
    )["t"]
    balance = total_billed - total_paid - total_adjusted

    return {
        "student": student,
        "invoices": list(invoices_qs),
        "payments": list(payments_qs),
        "adjustments": list(adjustments_qs),
        "total_billed": total_billed,
        "total_paid": total_paid,
        "total_adjusted": total_adjusted,
        "balance": balance,
        "term": term,
    }


def student_balance(*, student_id: int) -> Decimal:
    stmt = student_statement(student_id=student_id)
    return stmt["balance"]


# ---------------------------------------------------------------------------
# Class Finance Summary
# ---------------------------------------------------------------------------


def class_finance_summary(
    *,
    school_class_id: int,
    term_id: int | None = None,
    year_id: int | None = None,
) -> dict:
    """
    One class's fee position for one term, or for a whole year's terms.

    Built from the money interface, so the headline and the pupil rows are the
    same figures the Director portal renders — this page used to run two
    queries per student, and could show a total the list below it did not add up
    to once a waiver existed.

    ``term_id=None`` means the whole year given by ``year_id``: the headline and
    each pupil's row cover every term, which is what a class report for a year
    has to show before a family is told what they still owe.
    """
    from apps.academics.models import AcademicYear, SchoolClass, Term

    cls = SchoolClass.objects.get(pk=school_class_id)
    if term_id is not None:
        term = Term.objects.select_related("academic_year").get(pk=term_id)
        if year_id is not None and year_id != term.academic_year_id:
            raise ValueError("That term does not belong to the selected year.")
        year = term.academic_year
    else:
        if year_id is None:
            raise ValueError("Pass a term_id or a year_id.")
        term = None
        year = AcademicYear.objects.get(pk=year_id)

    totals = _fee_totals_for_period(
        term=term, year_id=None if term is not None else year.pk, class_id=cls.pk
    )
    rows = _student_fee_rows(
        term=term,
        year_id=None if term is not None else year.pk,
        class_id=cls.pk,
    )

    return {
        "class": cls,
        "term": term,
        "year": year,
        "total_billed": totals["billed"],
        "total_paid": totals["collected"],
        "total_adjusted": totals["adjusted"],
        "outstanding": totals["outstanding"],
        "collection_rate": totals["collection_rate"],
        "per_student": [
            {
                "student": row["student"],
                "billed": row["billed"],
                "paid": row["collected"],
                "adjusted": row["adjusted"],
                "outstanding": row["outstanding"],
            }
            for row in rows
        ],
        "student_count": len(rows),
    }


# ---------------------------------------------------------------------------
# School Finance Summary
# ---------------------------------------------------------------------------


def school_finance_summary(*, term_id: int) -> dict:
    """
    School totals for a term plus the per-class rows.

    Kept as a term-shaped name for callers that ask one term's question; the
    figures come from :func:`year_finance_summary`, so a term report and a
    whole-year report cannot disagree about the term they share.
    """
    from apps.academics.models import Term

    term = Term.objects.select_related("academic_year").get(pk=term_id)
    return year_finance_summary(year_id=term.academic_year_id, term_id=term_id)


# ---------------------------------------------------------------------------
# Outstanding Fees
# ---------------------------------------------------------------------------


def outstanding_students(
    *,
    term_id: int,
    class_id: int | None = None,
) -> list[dict]:
    """
    Every pupil owing money in a term, largest debt first.

    The per-invoice loop this replaced ran two queries per invoice — at a
    thousand invoices that is a page that never finishes. ``student_fee_rows``
    answers in one.
    """
    from apps.academics.models import Term

    term = Term.objects.get(pk=term_id)
    return [
        {
            "admission_number": row["admission_number"],
            "name": row["name"],
            "class": row["class_name"],
            "term": str(term),
            "total_amount": row["billed"],
            "amount_paid": row["collected"],
            "amount_adjusted": row["adjusted"],
            "amount_outstanding": row["outstanding"],
            "last_payment_date": row["last_payment_date"],
            "student": row["student"],
        }
        for row in student_fee_rows(term=term, class_id=class_id, only_debtors=True)
    ]


# ---------------------------------------------------------------------------
# Expenses, vendors, budgets and approvals
# ---------------------------------------------------------------------------


def expense_list(
    *,
    account_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    vendor_id: int | None = None,
) -> QuerySet:
    qs = Expense.objects.select_related("account", "recorded_by", "vendor")
    if account_id:
        qs = qs.filter(account_id=account_id)
    if status:
        qs = qs.filter(status=status)
    if vendor_id:
        qs = qs.filter(vendor_id=vendor_id)
    if date_from:
        qs = qs.filter(expense_date__gte=date_from)
    if date_to:
        qs = qs.filter(expense_date__lte=date_to)
    return qs


def vendor_list(*, active_only: bool = True) -> QuerySet:
    qs = Vendor.objects.all()
    if active_only:
        qs = qs.filter(is_active=True)
    return qs


def approval_queue(
    *,
    kind: str | None = None,
    status: str | None = None,
) -> QuerySet:
    """
    Asks awaiting a decision, oldest first — the order a queue works in.

    The model default ordering is newest-first (for a history page); the queue
    reverses it because an ask that has sat for a week is the one the approver
    should see before one raised this morning.
    """
    qs = ApprovalRequest.objects.select_related(
        "requested_by", "expense", "adjustment", "payment", "period"
    )
    if kind:
        qs = qs.filter(kind=kind)
    if status:
        qs = qs.filter(status=status)
    return qs.order_by("created_at")


def budget_variance(*, term_id: int) -> list[dict]:
    """
    Budget vs actual spend per category, for the approver's warning table.

    Variance is never stored — it is journal debits minus the sanctioned number,
    read here, so a late posting cannot leave a stale variance beside a correct
    total. ``alert`` fires at the configured percentage threshold.
    """
    from apps.academics.models import Term

    term = Term.objects.get(pk=term_id)
    settings_row = FinanceSettings.load()
    alert_pct = to_money(settings_row.budget_alert_percent)

    budgets = Budget.objects.filter(term=term).select_related("account")

    spent_rows = (
        JournalLine.objects.filter(
            account__type="expense",
            entry__entry_date__gte=term.start_date,
            entry__entry_date__lte=term.end_date,
        )
        .values("account_id")
        .annotate(
            spent=Coalesce(
                Sum("debit_amount") - Sum("credit_amount"),
                ZERO,
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )
    )
    spent_map = {row["account_id"]: row["spent"] for row in spent_rows}

    rows = []
    for b in budgets:
        spent = spent_map.get(b.account_id, ZERO)
        pct = _percent(spent, b.amount)
        rows.append(
            {
                "budget_id": b.pk,
                "account_id": b.account_id,
                "account_name": b.account.name,
                "account_code": b.account.code,
                "budget": b.amount,
                "spent": spent,
                "variance": b.amount - spent,
                "percent_used": pct,
                "alert": pct is not None and pct >= alert_pct,
            }
        )
    rows.sort(key=lambda r: (not r["alert"], -(r["percent_used"] or 0)))
    return rows


# ---------------------------------------------------------------------------
# Report Summary
# ---------------------------------------------------------------------------


def report_summary(*, term_id: int | None = None, year_id: int | None = None) -> dict:
    """
    Income vs expenses; fees collected vs outstanding, for a term or a year.

    The fee half is the interface's period/allocation basis, the income half is
    the ledger's date basis. They are shown side by side on purpose and labelled
    with their basis, because "billed for this term" and "journalised in this
    window" are two true answers to two different questions.

    ``term_id=None`` with a ``year_id`` reports the whole year: fees across all
    its terms, the ledger over the year's own start and end dates.
    """
    from apps.academics.models import AcademicYear, Term

    if term_id is not None:
        term = Term.objects.select_related("academic_year").get(pk=term_id)
        year = term.academic_year
        date_from, date_to = term.start_date, term.end_date
    elif year_id is not None:
        term = None
        year = AcademicYear.objects.get(pk=year_id)
        date_from, date_to = year.start_date, year.end_date
    else:
        raise ValueError("Pass a term_id or a year_id.")

    period = year_finance_summary(
        year_id=year.pk, term_id=term.pk if term is not None else None
    )
    statement = income_statement(date_from=date_from, date_to=date_to)

    return {
        "year": year,
        "term": term,
        "date_from": date_from,
        "date_to": date_to,
        "total_billed": period["total_billed"],
        "total_collected": period["total_paid"],
        "total_adjustments": period["total_adjusted"],
        "outstanding": period["outstanding"],
        "total_income": statement["total_income"],
        "total_expenses": statement["total_expense"],
        "net": statement["net"],
        "income_lines": statement["income"],
        "expense_lines": statement["expenses"],
    }


# ===========================================================================
# THE MONEY READ INTERFACE
# ===========================================================================
#
# Every fee and books figure in this project — Finance's own pages, the
# Director portal, exports, reports — is derived here and consumed everywhere
# else. That is the point of the section, not a stylistic preference: the same
# arithmetic previously existed in four places and the copies disagreed. One
# subtracted waivers, one did not; one attributed a balance to the class the
# student is in now, one to the class on the invoice. A Director screen built
# on these functions cannot drift from the Finance screen it is meant to
# reconcile against, because they run the same SQL.
#
# Definitions, fixed here rather than per screen:
#
#   billed        Sum of ``total_amount`` on POSTED invoices in the period.
#   collected     Sum of ``PaymentAllocation.allocated_amount`` against those
#                 invoices, over the rows whose receipt is still standing — i.e.
#                 ``.live()``, which excludes the allocations of a reversed
#                 receipt. A reversal no longer deletes those rows (A2: the
#                 ledger keeps what happened), so the filter is what nets it.
#                 Do not filter a second time on top of ``.live()``.
#   adjusted      Sum of ``Adjustment.amount`` whose invoice is in the period.
#                 Waivers, scholarships and discounts: money the school gave up
#                 collecting, which is therefore no longer a debt.
#   outstanding   billed − collected − adjusted. Negative means credit on
#                 account, not debt; the debtor lists filter it out.
#   collection %  collected ÷ billed, as a Decimal. ``None`` when nothing is
#                 billed, so a screen can say "nothing billed yet" instead of
#                 showing a misleading 0%.
#   "class"       The class the student is in *now* (``student__current_class``).
#                 A transferred pupil's balances follow the pupil rather than
#                 stranding in the old class — which is what a fees report is
#                 for, but it does mean a class's figures are not a snapshot of
#                the day the invoice was raised.
#
# Two bases, deliberately kept apart (see ``cash_received`` vs
# ``fee_totals``): "what did we receive in this window" is counted on
# ``Payment.payment_date``, "what has this term's billing covered" is counted on
# allocations against that term's invoices. They differ when a payment is
# unallocated (an overpayment) or settles an older term's invoice, and
# ``cash_received()["unallocated"]`` is the bridge between them.
# Reconciliation tests assert both the equality and the documented gap — an
# unexplained difference is a bug, an explained one is two different questions.

ZERO = Decimal("0.00")

_MONEY = DecimalField(max_digits=14, decimal_places=2)

#: Buckets for ``outstanding_aging``, in days past the due date.
AGING_BUCKETS = (
    ("not_due", "Not yet due", lambda days: days <= 0),
    ("due_1_30", "1 to 30 days overdue", lambda days: 1 <= days <= 30),
    ("due_31_60", "31 to 60 days overdue", lambda days: 31 <= days <= 60),
    ("due_61_90", "61 to 90 days overdue", lambda days: 61 <= days <= 90),
    ("over_90", "Over 90 days", lambda days: days > 90),
)


def _money_sum(queryset, field: str) -> Decimal:
    """A SUM that answers 0.00 rather than ``None`` when nothing matches."""
    total = queryset.aggregate(
        t=Coalesce(Sum(field), ZERO, output_field=_MONEY)
    )["t"]
    return to_money(total)


def _percent(part: Decimal, whole: Decimal):
    """
    Collection rate as a Decimal, or ``None`` when there is nothing to rate.

    The arithmetic lives in :func:`apps.core.money.percent` so a rate on a
    receipt rounds identically to the rate on a Director KPI — this function is
    the interface's alias for it, not a second implementation.
    """
    return _money_percent(part, whole)


def _invoice_totals_qs(*, term=None, class_id=None, student_id=None):
    """
    Posted invoices, each annotated with what is paid and waived on it.
    One query; the two annotations are correlated subqueries on the same row.

    ``term`` is optional: a reader that wants every term (a late-fee sweep, a
    day-close) asks for none. Passing nothing must not mean "term IS NULL",
    which is what a plain ``filter(term=term)`` would have done.
    """
    qs = Invoice.objects.filter(status=Invoice.Status.POSTED)
    if term is not None:
        qs = qs.filter(term=term)
    qs = qs.annotate(**_invoice_balance_annotations())
    if class_id is not None:
        qs = qs.filter(student__current_class_id=class_id)
    if student_id is not None:
        qs = qs.filter(student_id=student_id)
    return qs


def _invoice_scope(*, term=None, year_id: int | None = None) -> dict:
    """
    The filter every fee reader applies to invoices for "this period".

    Exactly one of ``term``/``year_id`` is given: a term is one billing window,
    a year is all of its terms. Written once because the four readers below
    (totals, per-class, per-pupil, per-term) must agree on what "the period"
    means, and a report page shows two of them side by side.
    """
    if (term is None) == (year_id is None):
        raise ValueError("Pass exactly one of term or year_id.")
    return {"term": term} if term is not None else {"term__academic_year_id": year_id}


def _fee_totals_for_period(*, term=None, year_id: int | None = None, class_id=None) -> dict:
    """``fee_totals`` without the term wrapper — the arithmetic itself."""
    scope = _invoice_scope(term=term, year_id=year_id)
    invoices = Invoice.objects.filter(status=Invoice.Status.POSTED, **scope)
    if class_id is not None:
        invoices = invoices.filter(student__current_class_id=class_id)

    billed = _money_sum(invoices, "total_amount")
    allocations = PaymentAllocation.objects.filter(invoice__in=invoices).live()
    collected = _money_sum(allocations, "allocated_amount")
    adjusted = _money_sum(
        Adjustment.objects.filter(invoice__in=invoices).live(), "amount"
    )
    return {
        "billed": billed,
        "collected": collected,
        "adjusted": adjusted,
        "outstanding": billed - collected - adjusted,
        "collection_rate": _percent(collected, billed),
    }


def fee_totals(*, term, class_id: int | None = None) -> dict:
    """
    The headline fee figures for one term, optionally for one class.

    Exactly three SUM queries whatever the size of the school: billed,
    collected, adjusted. Money received on a *date* basis is a different
    question and lives in ``cash_received()``; keeping the two apart is what
    lets a screen say which one of them it is answering.
    """
    totals = _fee_totals_for_period(term=term, class_id=class_id)
    return {"term": term, "class_id": class_id, **totals}


def _class_fee_rows(*, term=None, year_id: int | None = None) -> list[dict]:
    """``class_fee_rows`` for a term or a whole year — three GROUP BYs."""
    scope = _invoice_scope(term=term, year_id=year_id)
    prefixed = {f"invoice__{key}": value for key, value in scope.items()}
    billed_rows = (
        Invoice.objects.filter(status=Invoice.Status.POSTED, **scope)
        .values(
            "student__current_class__pk",
            "student__current_class__name",
            "student__current_class__level_order",
        )
        .annotate(billed=Coalesce(Sum("total_amount"), ZERO, output_field=_MONEY))
        .order_by("student__current_class__level_order", "student__current_class__name")
    )
    collected_map = {
        row["invoice__student__current_class__pk"]: row["t"]
        for row in (
            PaymentAllocation.objects.filter(
                invoice__status=Invoice.Status.POSTED, **prefixed
            )
            .live()
            .values("invoice__student__current_class__pk")
            .annotate(
                t=Coalesce(Sum("allocated_amount"), ZERO, output_field=_MONEY)
            )
        )
    }
    adjusted_map = {
        row["invoice__student__current_class__pk"]: row["t"]
        for row in (
            Adjustment.objects.filter(
                invoice__status=Invoice.Status.POSTED, **prefixed
            )
            .live()
            .values("invoice__student__current_class__pk")
            .annotate(t=Coalesce(Sum("amount"), ZERO, output_field=_MONEY))
        )
    }

    rows = []
    for row in billed_rows:
        class_id = row["student__current_class__pk"]
        billed = row["billed"]
        collected = collected_map.get(class_id, ZERO)
        adjusted = adjusted_map.get(class_id, ZERO)
        rows.append(
            {
                "class_id": class_id,
                "class_name": row["student__current_class__name"] or "—",
                "level_order": row["student__current_class__level_order"] or 0,
                "billed": billed,
                "collected": collected,
                "adjusted": adjusted,
                "outstanding": billed - collected - adjusted,
                "collection_rate": _percent(collected, billed),
            }
        )
    return rows


def class_fee_rows(*, term) -> list[dict]:
    """
    Billed / collected / adjusted / outstanding per class, in three GROUP BYs.

    Replaces the per-class loop that used to sit in three different places.
    Classes with no posted invoices are absent rather than shown as zero rows —
    a class that has not been billed is a different fact from one that has been
    billed and not paid, and the caller that wants the empty ones adds them from
    its own class list.
    """
    return _class_fee_rows(term=term)


def _per_term_totals(*, year) -> list[dict]:
    """
    Every term of a year with its fee position, in three GROUP BYs.

    Terms that were never billed keep their row with zeros: a report that hides
    them cannot be read as "Term 2 has not been invoiced yet", only as "Term 2
    does not exist".
    """
    billed = {
        row["term_id"]: row["t"]
        for row in Invoice.objects.filter(
            term__academic_year=year, status=Invoice.Status.POSTED
        )
        .values("term_id")
        .annotate(t=Coalesce(Sum("total_amount"), ZERO, output_field=_MONEY))
    }
    collected = {
        row["invoice__term_id"]: row["t"]
        for row in PaymentAllocation.objects.filter(
            invoice__term__academic_year=year,
            invoice__status=Invoice.Status.POSTED,
        )
        .live()
        .values("invoice__term_id")
        .annotate(t=Coalesce(Sum("allocated_amount"), ZERO, output_field=_MONEY))
    }
    adjusted = {
        row["invoice__term_id"]: row["t"]
        for row in Adjustment.objects.filter(
            invoice__term__academic_year=year,
            invoice__status=Invoice.Status.POSTED,
        )
        .live()
        .values("invoice__term_id")
        .annotate(t=Coalesce(Sum("amount"), ZERO, output_field=_MONEY))
    }

    rows = []
    for term in year.terms.order_by("start_date", "pk"):
        billed_here = billed.get(term.pk, ZERO)
        collected_here = collected.get(term.pk, ZERO)
        adjusted_here = adjusted.get(term.pk, ZERO)
        rows.append(
            {
                "term": term,
                "billed": billed_here,
                "collected": collected_here,
                "adjusted": adjusted_here,
                "outstanding": billed_here - collected_here - adjusted_here,
                "collection_rate": _percent(collected_here, billed_here),
            }
        )
    return rows


def year_finance_summary(*, year_id: int, term_id: int | None = None) -> dict:
    """
    A year's fee position, for one of its terms or for all of them together.

    ``term_id=None`` is the "Whole year" mode: the totals cover every term, and
    ``per_term`` still breaks them down so the officer can see which term the
    arrears came from. The per-class table and the expense figure follow the
    same period, so the header and the two tables underneath it describe one
    window rather than three.

    Expenses are journal lines dated inside the window, not invoices: this is
    the figure the school actually spent, matched to the receipt basis the
    totals use (see the two-bases note above ``fee_totals``).
    """
    from apps.academics.models import AcademicYear, Term

    year = AcademicYear.objects.get(pk=year_id)
    term = Term.objects.get(pk=term_id) if term_id is not None else None
    if term is not None and term.academic_year_id != year.pk:
        raise ValueError("That term does not belong to the selected year.")

    totals = _fee_totals_for_period(
        term=term, year_id=year.pk if term is None else None
    )
    date_from, date_to = (
        (term.start_date, term.end_date)
        if term is not None
        else (year.start_date, year.end_date)
    )
    expenses = _money_sum(
        JournalLine.objects.filter(
            account__type=Account.Type.EXPENSE,
            entry__entry_date__gte=date_from,
            entry__entry_date__lte=date_to,
        ),
        "debit_amount",
    )
    class_rows = _class_fee_rows(
        term=term, year_id=year.pk if term is None else None
    )

    return {
        "year": year,
        "term": term,
        "date_from": date_from,
        "date_to": date_to,
        "total_billed": totals["billed"],
        "total_paid": totals["collected"],
        "total_adjusted": totals["adjusted"],
        "outstanding": totals["outstanding"],
        "collection_rate": totals["collection_rate"],
        "total_expenses": expenses,
        "net_income": totals["collected"] - expenses,
        "per_term": _per_term_totals(year=year),
        "per_class": [
            {
                "class_id": row["class_id"],
                "class_name": row["class_name"],
                "level_order": row["level_order"],
                "billed": row["billed"],
                "paid": row["collected"],
                "adjusted": row["adjusted"],
                "outstanding": row["outstanding"],
                "rate": row["collection_rate"],
            }
            for row in class_rows
        ],
    }


def _student_fee_rows(
    *,
    term=None,
    year_id: int | None = None,
    class_id: int | None = None,
    only_debtors: bool = False,
) -> list[dict]:
    """
    ``student_fee_rows`` for one term or a whole year — the arithmetic itself.

    The four correlated subqueries are the same for both windows; only the
    invoice filter changes, so a class report for a year and one for a term
    cannot compute a pupil's balance differently.
    """
    from apps.students.models import Student

    scope = _invoice_scope(term=term, year_id=year_id)
    prefixed = {f"invoice__{key}": value for key, value in scope.items()}

    billed_sub = (
        Invoice.objects.filter(
            status=Invoice.Status.POSTED, student=OuterRef("pk"), **scope
        )
        .values("student")
        .annotate(t=Sum("total_amount"))
        .values("t")
    )
    paid_sub = (
        PaymentAllocation.objects.filter(
            invoice__status=Invoice.Status.POSTED,
            invoice__student=OuterRef("pk"),
            **prefixed,
        )
        .live()
        .values("invoice__student")
        .annotate(t=Sum("allocated_amount"))
        .values("t")
    )
    adjusted_sub = (
        Adjustment.objects.filter(
            invoice__status=Invoice.Status.POSTED,
            student=OuterRef("pk"),
            **prefixed,
        )
        .live()
        .values("student")
        .annotate(t=Sum("amount"))
        .values("t")
    )
    last_paid_sub = (
        PaymentAllocation.objects.filter(
            invoice__status=Invoice.Status.POSTED,
            invoice__student=OuterRef("pk"),
            **prefixed,
        )
        .live()
        .values("invoice__student")
        .annotate(m=Max("payment__payment_date"))
        .values("m")
    )

    students = Student.objects.filter(
        status__in=[Student.Status.ACTIVE, Student.Status.REPEATING]
    )
    if class_id is not None:
        students = students.filter(current_class_id=class_id)

    students = students.annotate(
        billed_here=Coalesce(Subquery(billed_sub, output_field=_MONEY), ZERO, output_field=_MONEY),
        collected_here=Coalesce(Subquery(paid_sub, output_field=_MONEY), ZERO, output_field=_MONEY),
        adjusted_here=Coalesce(
            Subquery(adjusted_sub, output_field=_MONEY), ZERO, output_field=_MONEY
        ),
        last_payment_date=Subquery(last_paid_sub, output_field=DateField()),
    ).select_related("user", "current_class")

    rows = []
    for student in students:
        outstanding = (
            student.billed_here - student.collected_here - student.adjusted_here
        )
        if only_debtors and outstanding <= ZERO:
            continue
        if not student.billed_here and not student.collected_here:
            # Never billed in this term: not a debtor, and not a row worth
            # showing in a fee list.
            continue
        rows.append(
            {
                "student": student,
                "admission_number": student.admission_number,
                "name": _person_name(student.user),
                "class_name": student.current_class.name if student.current_class else "—",
                "billed": student.billed_here,
                "collected": student.collected_here,
                "adjusted": student.adjusted_here,
                "outstanding": outstanding,
                "last_payment_date": _as_date(student.last_payment_date),
            }
        )
    rows.sort(key=lambda r: r["outstanding"], reverse=True)
    return rows


def student_fee_rows(
    *,
    term,
    class_id: int | None = None,
    only_debtors: bool = False,
) -> list[dict]:
    """
    Per-student fee position for one term: billed, collected, adjusted,
    outstanding, last payment date.

    One query over the invoice rows plus one over the students, however many
    pupils are in the class. The amounts are per *invoice*, so a student billed
    twice in a term appears once with the two added together.

    ``only_debtors`` drops students with no balance owing — including those with
    a credit, which is why the filter is ``> 0`` and not ``!= 0``.
    """
    return _student_fee_rows(
        term=term, class_id=class_id, only_debtors=only_debtors
    )


def _person_name(user) -> str:
    """
    ``User.full_name`` is a property, so a name that has to travel inside
    ``values()`` is assembled from the two stored columns instead.
    """
    return " ".join(p for p in (user.first_name, user.last_name) if p).strip()


def _as_date(value):
    """SQLite hands a subqueried MAX() back as text; screens want a date."""
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def fee_item_breakdown(*, term, class_id: int | None = None) -> list[dict]:
    """
    What the term's billing is made of, by invoice line label.

    ``B4`` of the Director brief asks for the breakdown behind a class total, so
    the number a head sees and the item it came from are one click apart.
    """
    qs = InvoiceLine.objects.filter(
        invoice__term=term, invoice__status=Invoice.Status.POSTED
    )
    if class_id is not None:
        qs = qs.filter(invoice__student__current_class_id=class_id)
    rows = (
        qs.values("label")
        .annotate(
            amount=Coalesce(Sum("amount"), ZERO, output_field=_MONEY),
            line_count=Count("pk"),
        )
        .order_by("-amount")
    )
    return [
        {
            "label": row["label"],
            "amount": row["amount"],
            "line_count": row["line_count"],
        }
        for row in rows
    ]


def outstanding_aging(*, term, class_id: int | None = None, as_of=None) -> dict:
    """
    How far behind the outstanding money is, in days past each invoice's due
    date — falling back to the issue date when no due date was set.

    Bucketed per invoice, so this is the one fee function whose cost grows with
    the number of invoices rather than the number of classes. It reads a single
    query of at most one row per posted invoice in the term.
    """
    as_of = as_of or date.today()
    buckets = {key: {"label": label, "count": 0, "amount": ZERO} for key, label, _ in AGING_BUCKETS}
    rules = [(key, test) for key, _, test in AGING_BUCKETS]

    total = ZERO
    for row in _invoice_totals_qs(term=term, class_id=class_id).values(
        "due_date", "issued_date", "total_amount", "paid_here", "adjusted_here"
    ):
        owing = row["total_amount"] - row["paid_here"] - row["adjusted_here"]
        if owing <= ZERO:
            continue
        total += owing
        reference = row["due_date"] or row["issued_date"]
        days_over = (as_of - reference).days
        for key, test in rules:
            if test(days_over):
                bucket = buckets[key]
                bucket["count"] += 1
                bucket["amount"] += owing
                break

    return {
        "as_of": as_of,
        "total": total,
        "buckets": [
            {"key": key, **buckets[key]} for key, _, _ in AGING_BUCKETS
        ],
    }


def collected_by_month(*, term) -> list[dict]:
    """
    Collections inside one term, month by month — the cumulative curve's source.

    On an allocation basis (money counted against this term's invoices), so the
    total equals ``fee_totals(term=term)["collected"]``.
    """
    rows = (
        PaymentAllocation.objects.filter(
            invoice__term=term, invoice__status=Invoice.Status.POSTED
        )
        .live()
        .annotate(month=TruncMonth("payment__payment_date"))
        .values("month")
        .annotate(amount=Coalesce(Sum("allocated_amount"), ZERO, output_field=_MONEY))
        .order_by("month")
    )
    out = []
    running = ZERO
    for row in rows:
        running += row["amount"]
        out.append(
            {
                "month": row["month"],
                "month_label": row["month"].strftime("%b %Y") if row["month"] else "—",
                "amount": row["amount"],
                "cumulative": running,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Books of account
# ---------------------------------------------------------------------------


def cash_received(*, date_from, date_to) -> dict:
    """
    Money actually received in a window, and where it went.

    A reversal is a row of its own: ``reverse_payment`` flags the original
    ``is_reversed=True`` and inserts a negative-dated-today row, so both rows
    have to be summed here — excluding the flagged original would report cash
    that was banked as never having arrived. ``count`` is real receipts only;
    the reversal row is the give-back, not a second payment.

    The original's allocation rows survive the reversal (the ledger keeps what
    happened), so ``applied_to_posted_invoices`` reads them through ``.live()``,
    which is what nets them out here too.
    """
    payments = Payment.objects.filter(
        payment_date__gte=date_from, payment_date__lte=date_to
    )
    received = payments.filter(amount__gt=ZERO)
    gross = _money_sum(received, "amount")
    reversed_out = -_money_sum(payments.filter(amount__lt=ZERO), "amount")
    allocations = PaymentAllocation.objects.filter(
        payment__in=payments,
        invoice__status=Invoice.Status.POSTED,
    ).live()
    applied = _money_sum(allocations, "allocated_amount")
    return {
        "date_from": date_from,
        "date_to": date_to,
        "gross": gross,
        "reversed": reversed_out,
        "net": gross - reversed_out,
        "count": received.count(),
        "applied_to_posted_invoices": applied,
        "unallocated": gross - reversed_out - applied,
    }


def receipts_register(*, date_from, date_to) -> dict:
    """
    The receipts list behind ``cash_received`` — one row per receipt.

    Reversal rows appear here too (a cashier auditing the window has to see
    them), which is why ``total`` sums every row and therefore equals
    ``cash_received()["net"]``.
    """
    payments = (
        Payment.objects.filter(
            payment_date__gte=date_from, payment_date__lte=date_to
        )
        .select_related("student__user", "recorded_by")
        .order_by("payment_date", "receipt_number")
    )
    return {
        "rows": payments,
        "total": _money_sum(payments, "amount"),
        "count": payments.count(),
    }


def account_balances(*, date_to=None, types: tuple[str, ...] | None = None) -> list[dict]:
    """
    Closing balance per account, on the ledger's own side (debit-normal
    accounts net debit-minus-credit).

    One GROUP BY over journal lines. ``date_to`` makes it an "as of" balance;
    every summary on the books page names the date it was struck.
    """
    qs = JournalLine.objects.all()
    if date_to is not None:
        qs = qs.filter(entry__entry_date__lte=date_to)
    if types:
        qs = qs.filter(account__type__in=types)
    rows = qs.values(
        "account__pk",
        "account__code",
        "account__name",
        "account__type",
        "account__normal_side",
    ).annotate(
        debit=Coalesce(Sum("debit_amount"), ZERO, output_field=_MONEY),
        credit=Coalesce(Sum("credit_amount"), ZERO, output_field=_MONEY),
    )
    out = []
    for row in rows:
        net = row["debit"] - row["credit"]
        if row["account__normal_side"] == Account.NormalSide.CREDIT:
            net = -net
        if not row["debit"] and not row["credit"]:
            continue
        out.append(
            {
                "account_id": row["account__pk"],
                "code": row["account__code"],
                "name": row["account__name"],
                "type": row["account__type"],
                "debit": row["debit"],
                "credit": row["credit"],
                "balance": net,
            }
        )
    out.sort(key=lambda r: r["code"])
    return out


def cash_and_bank_balances(*, as_of=None) -> dict:
    """
    What the school is holding, per the ledger — not per the bank statement.

    Codes 1000/1010/1020 are the seeded chart's cash, bank and mobile-money
    accounts; the assets are debit-normal, so a balance is debits minus credits
    up to ``as_of``.

    ``cash`` is petty cash only. Fees are never received into it (see
    ``Payment.Method``), so it is not part of "what parents paid us" — it is
    change float, and the day-close sheet lists it separately from collections.
    """
    from .chart_of_accounts import (
        BANK_CODE,
        CASH_CODE,
        MOBILE_MONEY_CODE,
    )

    as_of = as_of or date.today()
    qs = JournalLine.objects.filter(
        account__code__in=(CASH_CODE, BANK_CODE, MOBILE_MONEY_CODE),
        entry__entry_date__lte=as_of,
    ).values("account__code").annotate(
        balance=Coalesce(Sum("debit_amount"), ZERO, output_field=_MONEY)
        - Coalesce(Sum("credit_amount"), ZERO, output_field=_MONEY)
    )
    found = {row["account__code"]: row["balance"] for row in qs}
    cash = found.get(CASH_CODE, ZERO)
    bank = found.get(BANK_CODE, ZERO)
    mobile = found.get(MOBILE_MONEY_CODE, ZERO)
    return {
        "as_of": as_of,
        "cash": cash,
        "bank": bank,
        "mobile_money": mobile,
        "total": cash + bank + mobile,
    }


def income_statement(*, date_from, date_to) -> dict:
    """
    Income and expense by account over a window, from journal lines only.

    This is the ledger's answer, so it will not equal the fee figures, which are
    counted per *term* on allocations — an invoice posted late is billed to one
    term and journals in another. ``books_overview`` shows both and names the
    basis of each.
    """
    def _side(account_type: str, normal_field: str) -> dict:
        rows = (
            JournalLine.objects.filter(
                account__type=account_type,
                entry__entry_date__gte=date_from,
                entry__entry_date__lte=date_to,
            )
            .values("account__pk", "account__code", "account__name")
            .annotate(
                amount=Coalesce(Sum(normal_field), ZERO, output_field=_MONEY),
                other=Coalesce(
                    Sum("credit_amount" if normal_field == "debit_amount" else "debit_amount"),
                    ZERO,
                    output_field=_MONEY,
                ),
            )
            .order_by("account__code")
        )
        lines = [
            {
                "account_id": row["account__pk"],
                "code": row["account__code"],
                "name": row["account__name"],
                # The account's own side minus the opposite side: a refund on an
                # expense account reduces the expense rather than adding income.
                "amount": row["amount"] - row["other"],
            }
            for row in rows
        ]
        return {
            "lines": lines,
            "total": sum((line["amount"] for line in lines), ZERO),
        }

    income = _side(Account.Type.INCOME, "credit_amount")
    expense = _side(Account.Type.EXPENSE, "debit_amount")
    return {
        "date_from": date_from,
        "date_to": date_to,
        "income": income["lines"],
        "expenses": expense["lines"],
        "total_income": income["total"],
        "total_expense": expense["total"],
        "net": income["total"] - expense["total"],
    }


def top_expense_categories(*, date_from, date_to, limit: int = 5) -> list[dict]:
    """The expense accounts that dominate the window, largest first."""
    statement = income_statement(date_from=date_from, date_to=date_to)
    lines = [line for line in statement["expenses"] if line["amount"] > ZERO]
    return lines[:limit]


def monthly_income_expense(*, date_from, date_to) -> list[dict]:
    """
    Income against expense, month by month, for the combo chart.

    One query per side. Months with no activity are omitted, not zero-filled:
    the chart's x-axis is built from the rows it is given.

    Each side is net of the opposite side, exactly as ``income_statement``
    totals it, so the bars add up to the statement the same window produces —
    a waiver journals a *debit* to the contra-income account 5000, and a series
    that only summed credits would chart income that was never kept. Amounts
    stay Decimals; a view building chart JSON casts at that boundary.
    """
    def _by_month(account_type: str, own_field: str, other_field: str) -> dict:
        rows = (
            JournalLine.objects.filter(
                account__type=account_type,
                entry__entry_date__gte=date_from,
                entry__entry_date__lte=date_to,
            )
            .annotate(month=TruncMonth("entry__entry_date"))
            .values("month")
            .annotate(
                own=Coalesce(Sum(own_field), ZERO, output_field=_MONEY),
                opposite=Coalesce(Sum(other_field), ZERO, output_field=_MONEY),
            )
        )
        return {row["month"]: to_money(row["own"] - row["opposite"]) for row in rows}

    income_map = _by_month(
        Account.Type.INCOME, "credit_amount", "debit_amount"
    )
    expense_map = _by_month(
        Account.Type.EXPENSE, "debit_amount", "credit_amount"
    )
    return [
        {
            "month": month,
            "month_label": month.strftime("%b %Y"),
            "income": income_map.get(month, ZERO),
            "expense": expense_map.get(month, ZERO),
        }
        for month in sorted(set(income_map) | set(expense_map))
    ]


def trial_balance_at(*, date_to=None) -> dict:
    """
    The balanced trial balance, in two queries.

    One GROUP BY over journal lines for every account's debit and credit — it
    used to be two aggregate queries *per* account, which on the seeded chart of
    accounts cost more than the ledger rows it read — and one read of the
    account rows themselves. The totals are returned beside the rows so a page's
    "does it balance" tick mark is Finance's own answer, not a re-addition.
    """
    balances = account_balances(date_to=date_to)
    accounts = {
        row.pk: row
        for row in Account.objects.filter(
            pk__in=[row["account_id"] for row in balances]
        )
    }
    rows = [
        {
            "account": accounts[row["account_id"]],
            "debit_total": row["debit"],
            "credit_total": row["credit"],
            "net": row["debit"] - row["credit"],
        }
        for row in balances
    ]
    return {
        "as_of": date_to or date.today(),
        "rows": rows,
        "total_debits": sum((row["debit_total"] for row in rows), ZERO),
        "total_credits": sum((row["credit_total"] for row in rows), ZERO),
    }


# ---------------------------------------------------------------------------
# Arrears, lateness and agreed plans
# ---------------------------------------------------------------------------


def arrears_rows(*, student_ids=None, term=None) -> list[dict]:
    """
    What each pupil owes, over every term — including the ones they left.

    This is the view class promotion and a fee chase have to be able to answer:
    ``student_fee_rows`` reads one term and one current class, so a child who has
    moved up, or who left school with a balance, disappears from it while the
    debt does not. Nothing is filtered on ``current_class`` here and a pupil with
    no class at all still gets a row, because "graduated" is a status, not a
    write-off.

    Three GROUP BYs whatever the number of pupils: billed, collected, adjusted.
    Collected is the same allocation figure ``fee_totals`` calls *collected* —
    money the ledger has put against an invoice. Cash a family has handed over
    and the school is holding as credit is not netted here, because it is not yet
    a payment against this debt; ``apply_credit_balances`` is what turns it into
    one, and then it appears here as collected.
    """
    from apps.students.models import Student

    invoices = Invoice.objects.filter(status=Invoice.Status.POSTED)
    if student_ids is not None:
        invoices = invoices.filter(student_id__in=list(student_ids))
    if term is not None:
        invoices = invoices.filter(term=term)

    billed = {
        row["student_id"]: row["t"]
        for row in invoices.values("student_id").annotate(
            t=Coalesce(Sum("total_amount"), ZERO, output_field=_MONEY)
        )
    }
    collected = {
        row["invoice__student_id"]: row["t"]
        for row in PaymentAllocation.objects.filter(invoice__in=invoices)
        .live()
        .values("invoice__student_id")
        .annotate(t=Coalesce(Sum("allocated_amount"), ZERO, output_field=_MONEY))
    }
    adjusted = {
        row["student_id"]: row["t"]
        for row in Adjustment.objects.filter(invoice__in=invoices)
        .live()
        .values("student_id")
        .annotate(t=Coalesce(Sum("amount"), ZERO, output_field=_MONEY))
    }

    rows = []
    for student in (
        Student.objects.filter(pk__in=billed)
        .select_related("user", "current_class")
        .order_by("admission_number")
    ):
        owing = (
            billed[student.pk]
            - collected.get(student.pk, ZERO)
            - adjusted.get(student.pk, ZERO)
        )
        if owing <= ZERO:
            # Owes nothing. A pupil holding a credit is not in arrears, and a
            # list that mixes the two is the one an officer cannot chase from.
            continue
        rows.append(
            {
                "student": student,
                "student_id": student.pk,
                "admission_number": student.admission_number,
                "name": _person_name(student.user),
                "class_name": student.current_class.name if student.current_class else "—",
                "billed": billed[student.pk],
                "collected": collected.get(student.pk, ZERO),
                "adjusted": adjusted.get(student.pk, ZERO),
                "outstanding": owing,
            }
        )
    rows.sort(key=lambda row: row["outstanding"], reverse=True)
    return rows


def arrears_summary(*, student_ids=None, term=None) -> dict:
    """The arrears list totalled — one call, so a headline and a table agree."""
    rows = arrears_rows(student_ids=student_ids, term=term)
    return {
        "rows": rows,
        "count": len(rows),
        "total": sum((row["outstanding"] for row in rows), ZERO),
        "as_of": date.today(),
    }


def late_fee_proposals(*, term=None, class_id: int | None = None, as_of=None) -> dict:
    """
    The bills already past their grace window, and what a late fee would be.

    Nothing is posted, here or anywhere. B2 allows late-fee rules "if enabled by
    settings", and a rule the school has not switched on produces no figure at
    all — which is why ``enabled`` is returned rather than assumed, so a screen
    can say "late fees are off" instead of showing an empty table that looks like
    a clean school.

    Even switched on, this stays a proposal for a person to apply as an
    ``Adjustment``. An automatic charge would put money on a family's debt that
    nobody at the school agreed to, and there is no ledger rule that lets a date
    arithmetic create a receivable.
    """
    as_of = as_of or date.today()
    rules = FinanceSettings.load()
    if not rules.late_fees_enabled or not rules.late_fee_percent:
        return {
            "enabled": False,
            "as_of": as_of,
            "grace_days": rules.late_fee_grace_days,
            "percent": rules.late_fee_percent,
            "rows": [],
            "total": ZERO,
        }

    rows = []
    for invoice in (
        _invoice_totals_qs(term=term, class_id=class_id)
        .select_related("student__user", "term")
        .order_by("-total_amount")
    ):
        owing = invoice.total_amount - invoice.paid_here - invoice.adjusted_here
        if owing <= ZERO:
            continue
        reference = invoice.due_date or invoice.issued_date
        days_over = (as_of - reference).days
        if days_over <= rules.late_fee_grace_days:
            continue
        rows.append(
            {
                "invoice": invoice,
                "student": invoice.student,
                "admission_number": invoice.student.admission_number,
                "name": _person_name(invoice.student.user),
                "term": invoice.term,
                "due_date": reference,
                "days_over": days_over,
                "outstanding": owing,
                "amount": to_money(owing * rules.late_fee_percent / Decimal(100)),
            }
        )
    rows.sort(key=lambda row: row["days_over"], reverse=True)
    return {
        "enabled": True,
        "as_of": as_of,
        "grace_days": rules.late_fee_grace_days,
        "percent": rules.late_fee_percent,
        "rows": rows,
        "total": sum((row["amount"] for row in rows), ZERO),
    }


def payment_plan_rows(*, term=None, student_id: int | None = None, as_of=None) -> dict:
    """
    Agreed installments beside what actually arrived — the adherence view (B6).

    Receipts are matched to a plan oldest-installment-first, because that is how
    a bursar applies money at the counter and it is the only reading the bank
    reference supports: when the reference says nothing, "which of the four has
    she paid" is answered by order, not guessed at a screen. So an installment is
    met once the money received against this pupil's invoices in the plan's term
    reaches its cumulative total.

    ``paid`` uses the same live-allocation figure every other fee number here
    uses, so a reversed receipt does not keep an installment looking met.
    """
    as_of = as_of or date.today()

    plans = PaymentPlan.objects.select_related(
        "student__user", "term", "agreed_by"
    ).prefetch_related("installments")
    if term is not None:
        plans = plans.filter(term=term)
    if student_id is not None:
        plans = plans.filter(student_id=student_id)

    paid_map = {
        (row["invoice__student_id"], row["invoice__term_id"]): row["t"]
        for row in PaymentAllocation.objects.filter(
            invoice__status=Invoice.Status.POSTED
        )
        .live()
        .values("invoice__student_id", "invoice__term_id")
        .annotate(t=Coalesce(Sum("allocated_amount"), ZERO, output_field=_MONEY))
    }

    rows = []
    for plan in plans.order_by("student__admission_number", "-term__start_date"):
        received = paid_map.get((plan.student_id, plan.term_id), ZERO)
        cumulative = ZERO
        installments = []
        overdue_count = 0
        for installment in plan.installments.all():
            cumulative += installment.amount
            met = received >= cumulative
            late = installment.due_date < as_of and not met
            if late:
                overdue_count += 1
            installments.append(
                {
                    "installment": installment,
                    "number": installment.number,
                    "amount": installment.amount,
                    "due_date": installment.due_date,
                    "met": met,
                    "overdue": late,
                }
            )
        owing = plan.total_amount - received
        if plan.status == PaymentPlan.Status.WITHDRAWN:
            state = "withdrawn"
        elif owing <= ZERO:
            state = "settled"
        elif overdue_count:
            state = "overdue"
        else:
            state = "agreed"
        rows.append(
            {
                "plan": plan,
                "student": plan.student,
                "admission_number": plan.student.admission_number,
                "name": _person_name(plan.student.user),
                "term": plan.term,
                "total_amount": plan.total_amount,
                "scheduled": plan.scheduled_amount,
                "received": received,
                "outstanding": owing,
                "installments": installments,
                "overdue_count": overdue_count,
                "next_due_date": next(
                    (
                        row["due_date"]
                        for row in sorted(
                            installments, key=lambda item: item["due_date"]
                        )
                        if not row["met"]
                    ),
                    None,
                ),
                "state": state,
            }
        )
    return {
        "as_of": as_of,
        "rows": rows,
        "count": len(rows),
        "overdue_count": sum(1 for row in rows if row["state"] == "overdue"),
        "scheduled": sum((row["scheduled"] for row in rows), ZERO),
        "received": sum((row["received"] for row in rows), ZERO),
    }
