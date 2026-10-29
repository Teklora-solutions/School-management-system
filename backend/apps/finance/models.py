"""
apps/finance/models.py
Double-entry financial ledger for the school.

Design rules:
  - All monetary fields use DecimalField(max_digits=14, decimal_places=2) and are
    quantized through ``apps.core.money.to_money`` — see that module for why the
    rounding rule lives in exactly one place.
  - Ledger is APPEND-ONLY, enforced three ways: ``save()``/``delete()`` on the
    row, a queryset that refuses ``.update()``/``.delete()``, and a database
    trigger added by migration ``0007``. Corrections are new reversing entries.
  - Every Payment has a unique reference plus an optional idempotency key, so a
    double-clicked receipt form cannot post twice.
  - Invoice has unique_together=(student, term, fee_structure).
  - Posting into a CLOSED ``FinancialPeriod``, or back-dating beyond the window
    in ``FinanceSettings``, is refused by the service layer.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import BaseModel
from apps.core.money import to_money


def _money():
    return models.DecimalField(max_digits=14, decimal_places=2)


#: The three ``Adjustment.AdjustmentType`` values that mean "this bill was
#: reduced". Written out rather than reached for because ``Adjustment`` cannot be
#: declared before ``JournalEntry``, which is below this section — and a rule that
#: could pick "write_off" would let a recurring discount give up a debt silently.
#: ``tests/test_billing.py::ReductionTypesPinnedTests`` fails the build if the two
#: lists ever drift apart.
REDUCTION_TYPES = [
    ("discount", "Discount"),
    ("scholarship", "Scholarship"),
    ("waiver", "Waiver"),
]


class AppendOnlyQuerySet(models.QuerySet):
    """
    A queryset that refuses bulk mutation.

    ``JournalEntry.save()`` has always raised for an existing row, but
    ``JournalEntry.objects.filter(...).update(...)`` and ``.delete()`` never
    passed through ``save()`` — so the append-only rule was a sign on the door
    rather than a lock. Bulk paths are exactly how a well-meaning repair script
    or a future data migration would quietly rewrite history.
    """

    def update(self, *args, **kwargs):
        raise ValidationError(
            f"{self.model._meta.verbose_name_plural} are append-only: an UPDATE "
            "would rewrite the ledger. Post a reversing entry instead."
        )

    def delete(self):
        raise ValidationError(
            f"{self.model._meta.verbose_name_plural} are append-only: a DELETE "
            "would remove ledger history. Post a reversing entry instead."
        )

    def bulk_update(self, *args, **kwargs):
        raise ValidationError(
            f"{self.model._meta.verbose_name_plural} are append-only: no bulk UPDATE."
        )


AppendOnlyManager = models.Manager.from_queryset(AppendOnlyQuerySet)


# ---------------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------------


class Account(BaseModel):
    class Type(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    class NormalSide(models.TextChoices):
        DEBIT = "debit", "Debit"
        CREDIT = "credit", "Credit"

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=20, choices=Type.choices, db_index=True)
    normal_side = models.CharField(max_length=10, choices=NormalSide.choices)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        help_text="Groups this account under a broader one. Same type only.",
    )
    is_contra = models.BooleanField(
        default=False,
        help_text=(
            "Grows on the opposite side to its type — Discounts is income that "
            "the school gave up, so it nets inside the income total rather "
            "than appearing as a cost."
        ),
    )
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "finance_account"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"

    @property
    def grows_on(self) -> str:
        """Which side a positive posting to this account lands on."""
        return self.normal_side

    def clean(self):
        super().clean()
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({"parent": "An account cannot be its own parent."})
        if self.parent_id and self.parent.type != self.type:
            raise ValidationError(
                {
                    "parent": "A sub-account must be the same type as its parent "
                    f"({self.parent.name} is {self.parent.get_type_display()})."
                }
            )
        # A contra account is defined by growing on the wrong side for its type.
        # If that is not true, the flag is a lie and the reports will mis-file it.
        expected = {
            self.Type.ASSET: self.NormalSide.DEBIT,
            self.Type.EXPENSE: self.NormalSide.DEBIT,
            self.Type.LIABILITY: self.NormalSide.CREDIT,
            self.Type.EQUITY: self.NormalSide.CREDIT,
            self.Type.INCOME: self.NormalSide.CREDIT,
        }[self.type]
        if self.is_contra and self.normal_side == expected:
            raise ValidationError(
                {
                    "is_contra": "A contra account must be normal on the opposite "
                    f"side to its type — {self.type} accounts grow on {expected}."
                }
            )


# ---------------------------------------------------------------------------
# Fee Structure
# ---------------------------------------------------------------------------


class FeeStructure(BaseModel):
    school_class = models.ForeignKey(
        "academics.SchoolClass", on_delete=models.PROTECT, related_name="fee_structures"
    )
    term = models.ForeignKey(
        "academics.Term", on_delete=models.PROTECT, related_name="fee_structures"
    )
    version = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "What the family is expected to pay by. An item without its own "
            "date inherits this one; an invoice snapshots whichever applies."
        ),
    )
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="fee_structures_created",
    )
    copied_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="copies",
        help_text=(
            "Set when this structure was produced by copying last term's. The "
            "copy is a fresh set of rows, not a shared reference, so editing the "
            "new term's amounts can never move the old term's invoices."
        ),
    )

    class Meta:
        db_table = "finance_fee_structure"
        ordering = ["-version"]
        unique_together = [("school_class", "term", "version")]
        verbose_name = "fee structure"
        verbose_name_plural = "fee structures"

    def __str__(self) -> str:
        return f"{self.school_class} | {self.term} | v{self.version}"

    @property
    def total(self) -> Decimal:
        """What one invoice for this structure bills, summed from its items."""
        return to_money(self.items.aggregate(t=models.Sum("amount"))["t"])


class FeeItem(BaseModel):
    structure = models.ForeignKey(
        FeeStructure, on_delete=models.CASCADE, related_name="items"
    )
    label = models.CharField(max_length=100)
    amount = _money()
    ordering = models.PositiveSmallIntegerField(default=0)
    compulsory = models.BooleanField(
        default=True,
        help_text=(
            "An optional item is only billed to a pupil who has been entered "
            "for it (``StudentFeeElection``), which is how transport and meals "
            "differ between two children in the same class."
        ),
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Per-item deadline; blank means the structure's own due date.",
    )

    class Meta:
        db_table = "finance_fee_item"
        ordering = ["ordering", "label"]

    def __str__(self) -> str:
        return f"{self.label}: {self.amount}"


# ---------------------------------------------------------------------------
# Billing variations: who is billed for what, and who pays less
# ---------------------------------------------------------------------------


class StudentFeeElection(BaseModel):
    """
    One pupil's answer to one optional fee item.

    B2 asks for "student-specific variations: optional items per student (e.g.
    transport)". The class structure stays the single definition of the bill and
    this says who is in or out, so copying a structure to next term does not have
    to retype forty pupils.

    The row is keyed to a ``FeeItem``, which belongs to one structure for one
    class and term, so an election is naturally per term — the right behaviour
    for a service that changes between terms.
    """

    student = models.ForeignKey(
        "students.Student", on_delete=models.CASCADE, related_name="fee_elections"
    )
    fee_item = models.ForeignKey(
        FeeItem, on_delete=models.CASCADE, related_name="elections"
    )
    included = models.BooleanField(
        default=True,
        help_text="Off means this pupil is not charged this item this term.",
    )
    noted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="fee_elections_noted",
    )
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "finance_student_fee_election"
        ordering = ["student__admission_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "fee_item"],
                name="finance_election_one_row_per_item",
            )
        ]

    def __str__(self) -> str:
        return f"{self.student} {'in' if self.included else 'out'}: {self.fee_item}"


class FeeDiscountRule(BaseModel):
    """
    A recurring reduction applied automatically when invoices are posted (B5).

    A sibling discount is not a favour remembered at the counter, it is a rule
    the school has published, so it lives here and the billing run applies it.
    It writes an ordinary ``Adjustment`` and posts the ordinary
    DR Discounts and Scholarships / CR Fees Receivable entry: the money figure
    ``adjusted`` in the interface already means "waived", and a second route
    round a discount would either double-count it or hide it from that figure.
    """

    class Basis(models.TextChoices):
        PERCENT = "percent", "Percentage of the bill"
        FIXED = "fixed", "Fixed amount"

    name = models.CharField(max_length=100)
    basis = models.CharField(
        max_length=10, choices=Basis.choices, default=Basis.PERCENT
    )
    percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Of the bill, or of one fee item when one is named below.",
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    adjustment_type = models.CharField(
        max_length=20, choices=REDUCTION_TYPES, default="discount"
    )
    fee_item = models.ForeignKey(
        FeeItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discount_rules",
        help_text="Blank reduces the whole invoice.",
    )
    siblings_only = models.BooleanField(
        default=False,
        help_text=(
            "Only pupils who share a guardian with another active pupil. A "
            "sibling discount that is not tied to a family reduces the school's "
            "income for every pupil, which is not what was agreed."
        ),
    )
    students = models.ManyToManyField(
        "students.Student",
        blank=True,
        related_name="fee_discount_rules",
        help_text="Named pupils, e.g. a scholarship list. Empty means every pupil.",
    )
    active = models.BooleanField(default=True, db_index=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="fee_discount_rules_created",
    )
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "finance_fee_discount_rule"
        ordering = ["name"]
        verbose_name = "fee discount rule"
        verbose_name_plural = "fee discount rules"

    def __str__(self) -> str:
        share = (
            f"{self.percent}%" if self.basis == self.Basis.PERCENT else str(self.amount)
        )
        return f"{self.name} ({share})"

    def clean(self):
        super().clean()
        if self.basis == self.Basis.PERCENT:
            if self.percent is None or not (
                Decimal(0) < self.percent <= Decimal(100)
            ):
                raise ValidationError(
                    {"percent": "A percentage rule needs a value above 0 and up to 100."}
                )
            self.amount = None
        else:
            if self.amount is None or to_money(self.amount) <= 0:
                raise ValidationError(
                    {"amount": "A fixed rule needs an amount above 0."}
                )
            self.percent = None

    def reduces(self, bill: Decimal) -> Decimal:
        """What this rule takes off a bill of ``bill``, never more than the bill."""
        if self.basis == self.Basis.PERCENT:
            raw = to_money(bill) * to_money(self.percent) / Decimal(100)
        else:
            raw = to_money(self.amount)
        return min(to_money(raw), to_money(bill))


class PaymentPlan(BaseModel):
    """
    An agreed schedule of installments against a pupil's bill (B6).

    It is a promise, not a payment: no journal entry is made here, because the
    money has not moved. The plan says what was agreed and by when, and the
    ledger says what actually arrived, and the two are compared by
    ``selectors.payment_plan_rows`` — which is what makes an overdue flag honest
    rather than a date nobody checked against the bank.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Agreed"
        COMPLETED = "completed", "Completed"
        WITHDRAWN = "withdrawn", "Withdrawn"

    student = models.ForeignKey(
        "students.Student", on_delete=models.PROTECT, related_name="payment_plans"
    )
    term = models.ForeignKey(
        "academics.Term", on_delete=models.PROTECT, related_name="payment_plans"
    )
    total_amount = _money()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    agreed_on = models.DateField(auto_now_add=True)
    agreed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="payment_plans_agreed",
    )
    note = models.TextField(blank=True)

    class Meta:
        db_table = "finance_payment_plan"
        ordering = ["-term__start_date", "student__admission_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "term"],
                condition=models.Q(status__in=("active", "completed")),
                name="finance_plan_one_running_per_term",
            )
        ]

    def __str__(self) -> str:
        return f"Plan {self.student} {self.term} ({self.total_amount})"

    @property
    def scheduled_amount(self) -> Decimal:
        return to_money(
            self.installments.aggregate(t=models.Sum("amount"))["t"] or Decimal(0)
        )


class PaymentPlanInstallment(BaseModel):
    """One agreed installment. Amounts are what was promised, not what came in."""

    plan = models.ForeignKey(
        PaymentPlan, on_delete=models.CASCADE, related_name="installments"
    )
    number = models.PositiveSmallIntegerField()
    amount = _money()
    due_date = models.DateField()

    class Meta:
        db_table = "finance_payment_plan_installment"
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "number"], name="finance_plan_number_unique_per_plan"
            )
        ]

    def __str__(self) -> str:
        return f"Installment {self.number} of {self.plan} - {self.amount} by {self.due_date}"


class FeeStructureUploadJob(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VALIDATING = "validating", "Validating"
        READY = "ready", "Ready for commit"
        COMMITTED = "committed", "Committed"
        FAILED = "failed", "Failed"

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="fee_upload_jobs",
    )
    file = models.FileField(upload_to="fee_structure_uploads/")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    target_class = models.ForeignKey(
        "academics.SchoolClass",
        null=True,
        on_delete=models.SET_NULL,
        related_name="fee_upload_jobs",
    )
    term = models.ForeignKey(
        "academics.Term",
        null=True,
        on_delete=models.SET_NULL,
        related_name="fee_upload_jobs",
    )
    preview_rows = models.JSONField(default=list, blank=True)
    error_rows = models.JSONField(default=list, blank=True)
    result_structure_id = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "finance_fee_structure_upload_job"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"FeeUpload#{self.pk} ({self.status})"


# ---------------------------------------------------------------------------
# Document numbering
# ---------------------------------------------------------------------------


class NumberSeries(BaseModel):
    """
    The next invoice and receipt number, one row per document type per year.

    A school's books are audited by sequence: an Auditor reading the receipts
    register expects 00041 to be followed by 00042, and a missing number is a
    question the school has to answer. A random ``RCP-<uuid>`` cannot answer it,
    so numbers come from here instead.

    A number is taken with a compare-and-set — read ``next_number``, then
    ``UPDATE ... WHERE next_number = <what I read>``. One writer wins, the loser
    re-reads and takes the following number, and nothing is ever skipped. It is
    deliberately not ``select_for_update``: this project runs on SQLite, which
    cannot lock a row, and the whole school is one process, so the CAS gives the
    same guarantee without pretending to a feature the database does not have.

    The row lives inside the caller's transaction, so a payment that fails after
    taking a number hands the number back — no gap. The prefix is copied in when
    the series is created, which means a later rename of the numbering format
    applies to future documents and never renumbers history.
    """

    class Kind(models.TextChoices):
        INVOICE = "invoice", "Invoice"
        RECEIPT = "receipt", "Receipt"
        EXPENSE = "expense", "Expense claim"

    kind = models.CharField(max_length=10, choices=Kind.choices, db_index=True)
    year = models.PositiveSmallIntegerField()
    prefix = models.CharField(max_length=8)
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "finance_number_series"
        ordering = ["kind", "year"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "year"], name="finance_series_one_row_per_year"
            )
        ]

    def __str__(self) -> str:
        return f"{self.prefix}-{self.year} next {self.next_number:05d}"

    def label(self, issued: int) -> str:
        return f"{self.prefix}-{self.year}-{issued:05d}"


# ---------------------------------------------------------------------------
# Invoicing
# ---------------------------------------------------------------------------


def _invoice_number() -> str:
    return f"INV-{uuid.uuid4().hex[:10].upper()}"


class Invoice(BaseModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    student = models.ForeignKey(
        "students.Student", on_delete=models.PROTECT, related_name="invoices"
    )
    term = models.ForeignKey(
        "academics.Term", on_delete=models.PROTECT, related_name="invoices"
    )
    fee_structure = models.ForeignKey(
        FeeStructure, on_delete=models.PROTECT, related_name="invoices"
    )
    invoice_number = models.CharField(
        max_length=30, unique=True, default=_invoice_number
    )
    issued_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    total_amount = _money()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="invoices_posted",
    )
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "finance_invoice"
        ordering = ["-issued_date", "invoice_number"]
        unique_together = [("student", "term", "fee_structure")]  # idempotency
        indexes = [
            # Every fee figure in the project filters posted invoices of one term,
            # usually for one student or one class. Migration 0004 removed the
            # indexes 0003 added; these are the ones the selectors actually query.
            models.Index(
                fields=["student", "term", "status"],
                name="finance_invoice_student_term",
            ),
            models.Index(
                fields=["term", "status", "issued_date"],
                name="finance_invoice_term_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.invoice_number} - {self.student} ({self.term})"

    @property
    def amount_paid(self) -> Decimal:
        """
        Receipts standing against this invoice.

        ``allocations.total()`` drops every row whose receipt has been reversed,
        which is what makes a reversal restore the balance exactly (A2) without
        the allocation history being deleted to achieve it.
        """
        return self.allocations.total()

    @property
    def amount_outstanding(self) -> Decimal:
        return self.total_amount - self.amount_paid


class InvoiceLine(BaseModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    fee_item = models.ForeignKey(
        FeeItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice_lines",
    )
    label = models.CharField(max_length=100)
    amount = _money()
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Copied from the fee item (or the structure behind it) when the "
            "invoice was posted. Kept on the line because a late fee is owed on "
            "this item from this date, and editing the structure afterwards must "
            "not move a deadline a pupil was already billed under."
        ),
    )

    class Meta:
        db_table = "finance_invoice_line"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.invoice.invoice_number}: {self.label}"


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def _receipt_number() -> str:
    return f"RCP-{uuid.uuid4().hex[:10].upper()}"


def _verification_code() -> str:
    """
    The code printed on a receipt so a bursar can confirm one at the counter.

    It is not a secret and not a capability: it proves a receipt exists with
    these details, so the download link a parent receives stays the thing that
    authorises access (Part F, IDOR), and this stays the thing that catches a
    photocopied or edited slip.
    """
    return uuid.uuid4().hex[:8].upper()


class Payment(BaseModel):
    class Method(models.TextChoices):
        """
        How money ARRIVED, decided by the school's banking, not by a gateway.

        There is no ``cash`` choice: this school does not take fees in cash, so
        every receipt settles into Bank or Mobile Money, and the Cash account is
        petty cash the school spends, never collects. ``cheque`` is its own
        method because a cheque sits in the till until it clears, which is a
        different fact from money that is already in the bank.

        A gateway can be added later without a schema change: one new choice, its
        ``settled_into`` account, and a webhook that writes through
        ``services.record_payment`` carrying an idempotency key.
        """

        BANK = "bank", "Bank Transfer"
        CHEQUE = "cheque", "Cheque"
        MOBILE_MONEY = "mobile_money", "Mobile Money"
        OTHER = "other", "Other"

    student = models.ForeignKey(
        "students.Student", on_delete=models.PROTECT, related_name="payments"
    )
    reference = models.CharField(max_length=100, unique=True, db_index=True)
    idempotency_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        help_text=(
            "Client-generated key for the form submission, or a future gateway "
            "callback. Retrying with the same key returns the first payment "
            "instead of posting a second one."
        ),
    )
    receipt_number = models.CharField(
        max_length=30, unique=True, default=_receipt_number
    )
    verification_code = models.CharField(
        max_length=16,
        unique=True,
        default=_verification_code,
        help_text="Printed on the receipt; typed at the counter to confirm it is real.",
    )
    payment_date = models.DateField()
    method = models.CharField(max_length=20, choices=Method.choices)
    settled_into = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payments_settled",
        help_text=(
            "The asset account this receipt actually landed in. Stored rather "
            "than re-derived from the method at reversal time, because a later "
            "change to the method→account map must not move history."
        ),
    )
    amount = _money()
    payer_name = models.CharField(
        max_length=120,
        blank=True,
        help_text="Who handed the money in, when that is not the person recording it.",
    )
    attachment = models.FileField(
        upload_to="payment_attachments/",
        blank=True,
        null=True,
        help_text="Deposit slip, mobile-money confirmation, photo of a cheque.",
    )
    notes = models.TextField(blank=True)
    print_count = models.PositiveSmallIntegerField(
        default=0,
        help_text=(
            "How many copies of this receipt have left the building. The second "
            "and later copies are marked DUPLICATE, because a receipt printed "
            "twice and paid twice is the oldest trick in a fee office."
        ),
    )
    last_printed_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="payments_recorded",
    )
    is_reversed = models.BooleanField(default=False, db_index=True)
    reversal_reason = models.TextField(blank=True)
    reverses = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reversal",
    )

    class Meta:
        db_table = "finance_payment"
        ordering = ["-payment_date", "-created_at"]
        indexes = [
            models.Index(
                fields=["student", "payment_date"],
                name="finance_payment_student_date",
            ),
            # The receipts register and the end-of-day summary both read a date
            # window; the by-officer split reads officer + date.
            models.Index(
                fields=["payment_date", "method"],
                name="finance_payment_date_method",
            ),
            models.Index(
                fields=["recorded_by", "payment_date"],
                name="finance_payment_recorder_date",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.receipt_number} - {self.student} ({self.amount})"

    def clean(self):
        super().clean()
        if self.amount is not None and to_money(self.amount) <= 0:
            raise ValidationError({"amount": "Payment amount must be positive."})

    @property
    def applied_amount(self) -> Decimal:
        """What this receipt has been put against invoices, reversals excluded."""
        return self.allocations.total()

    @property
    def unapplied_amount(self) -> Decimal:
        """
        The part of this receipt no invoice has absorbed yet.

        That is the school holding a parent's money, so it is what a credit
        balance is made of and what the dashboard lists as an unallocated
        payment. Two rows answer zero rather than their own figure: a reversed
        receipt, because the money went back, and a reversal row, because a
        give-back is not a surplus waiting for an invoice.
        """
        if self.is_reversed or to_money(self.amount) <= 0:
            return Decimal("0.00")
        return to_money(self.amount) - self.applied_amount


class AllocationQuerySet(models.QuerySet):
    """Allocations, with the one filter every money figure in the project needs."""

    def live(self):
        """
        Drop the allocations that belong to a reversed receipt.

        ``reverse_payment`` used to DELETE these rows, which is why every SUM
        over them quietly netted reversals. Module 6 ruled that a reversal must
        not erase history (A2), so the rows stay and the netting is now this
        filter — stated once, here, instead of in each caller that might forget
        it. Anything that genuinely wants the deleted-proof history (a reversal
        finding which invoices it touched) uses ``.all()``.
        """
        return self.filter(payment__is_reversed=False)

    def total(self) -> Decimal:
        """SUM of the live rows, as money, 0.00 when nothing matches."""
        return to_money(self.live().aggregate(t=models.Sum("allocated_amount"))["t"])


#: ``from_queryset`` hands back a class, and Django only wires up a manager when
#: it is given an *instance* — the inline form would be silently replaced by the
#: stock manager and ``.live()`` / ``.total()`` would disappear from every
#: caller, including ``Invoice.allocations``.
AllocationManager = models.Manager.from_queryset(AllocationQuerySet)


class PaymentAllocation(BaseModel):
    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name="allocations"
    )
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="allocations"
    )
    allocated_amount = _money()

    objects = AllocationManager()

    class Meta:
        db_table = "finance_payment_allocation"
        unique_together = [("payment", "invoice")]
        indexes = [
            # ``collected`` for a term SUMs these joined to invoices; the join
            # and the per-payment total are the two hot paths.
            models.Index(
                fields=["invoice", "allocated_amount"],
                name="finance_alloc_invoice_amount",
            ),
            # Every one of those SUMs now also asks whether the receipt behind
            # the row is still standing, so the join column is indexed.
            models.Index(
                fields=["payment"],
                name="finance_alloc_payment",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.payment.receipt_number} -> {self.invoice.invoice_number}: "
            f"{self.allocated_amount}"
        )


# ---------------------------------------------------------------------------
# Double-Entry Ledger
# ---------------------------------------------------------------------------


class JournalEntry(BaseModel):
    """
    Header for each double-entry posting. APPEND-ONLY.
    """

    class SourceType(models.TextChoices):
        INVOICE = "invoice", "Invoice"
        PAYMENT = "payment", "Payment"
        REVERSAL = "reversal", "Reversal"
        ADJUSTMENT = "adjustment", "Adjustment"
        EXPENSE = "expense", "Expense"
        CREDIT = "credit", "Credit applied"
        MANUAL = "manual", "Manual"

    reference = models.CharField(max_length=60, unique=True, db_index=True)
    entry_date = models.DateField(db_index=True)
    description = models.TextField()
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="journal_entries",
    )
    source_type = models.CharField(
        max_length=20, choices=SourceType.choices, db_index=True
    )
    source_id = models.CharField(max_length=40, blank=True, db_index=True)
    reverses = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reversed_by",
    )

    objects = AppendOnlyManager()

    class Meta:
        db_table = "finance_journal_entry"
        ordering = ["-entry_date", "-created_at"]
        indexes = [
            # ``reverse_payment`` looks an entry up by what created it; the books
            # read a date window.
            models.Index(
                fields=["source_type", "source_id"],
                name="finance_je_source",
            ),
            models.Index(fields=["entry_date"], name="finance_je_entry_date"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Journal entries are append-only; post a reversal.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Journal entries are append-only; post a reversal.")

    def __str__(self) -> str:
        return f"{self.reference} ({self.entry_date})"


class JournalLine(BaseModel):
    """
    One leg of a double-entry journal entry.

    DB constraints:
      - debit_amount >= 0
      - credit_amount >= 0
      - NOT (debit_amount > 0 AND credit_amount > 0)

    Service layer asserts SUM(debit) == SUM(credit) per entry before INSERT.
    """

    entry = models.ForeignKey(
        JournalEntry, on_delete=models.CASCADE, related_name="lines"
    )
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="journal_lines"
    )
    debit_amount = _money()
    credit_amount = _money()

    objects = AppendOnlyManager()

    class Meta:
        db_table = "finance_journal_line"
        indexes = [
            # Every books figure in the project is a GROUP BY over lines by
            # account, filtered by entry date.
            models.Index(
                fields=["account", "entry"], name="finance_jl_account_entry"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(debit_amount__gte=0),
                name="finance_jl_debit_nonneg",
            ),
            models.CheckConstraint(
                check=models.Q(credit_amount__gte=0),
                name="finance_jl_credit_nonneg",
            ),
            models.CheckConstraint(
                check=~(models.Q(debit_amount__gt=0) & models.Q(credit_amount__gt=0)),
                name="finance_jl_one_sided",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Journal lines are append-only; post a reversal.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Journal lines are append-only; post a reversal.")

    def __str__(self) -> str:
        side = (
            f"DR {self.debit_amount}"
            if self.debit_amount
            else f"CR {self.credit_amount}"
        )
        return f"{self.entry.reference} | {self.account.code} | {side}"


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------


class AdjustmentQuerySet(models.QuerySet):
    """Adjustments, with the filter every money reader needs now one can be pending."""

    def live(self):
        """
        Only the reductions that are actually on the books.

        A write-off waiting for an Admin signature has given up nobody's debt yet,
        so it must not lower ``adjusted`` or a balance. Same shape as
        ``AllocationQuerySet.live()`` for reversed receipts: the rule is stated on
        the queryset so a SUM that forgets it is visible in review, not correct by
        luck.
        """
        return self.filter(status=Adjustment.Status.POSTED)


#: Instance, not class — see ``AllocationManager``.
AdjustmentManager = models.Manager.from_queryset(AdjustmentQuerySet)


class Adjustment(BaseModel):
    class AdjustmentType(models.TextChoices):
        DISCOUNT = "discount", "Discount"
        SCHOLARSHIP = "scholarship", "Scholarship"
        WAIVER = "waiver", "Waiver"
        WRITE_OFF = "write_off", "Write-off"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        """
        Whether this reduction has been decided.

        Everything in the table before A5 was somebody's unilateral decision, so
        the default is ``posted`` and no existing figure moves. ``pending`` exists
        for a write-off that needs a second signature: the row is the ask, and the
        ledger line only appears when somebody with the authority agrees to it.
        """

        PENDING = "pending", "Awaiting approval"
        POSTED = "posted", "Posted"
        REJECTED = "rejected", "Rejected"

    student = models.ForeignKey(
        "students.Student", on_delete=models.PROTECT, related_name="adjustments"
    )
    invoice = models.ForeignKey(
        Invoice,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="adjustments",
    )
    adjustment_type = models.CharField(
        max_length=20, choices=AdjustmentType.choices, db_index=True
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.POSTED, db_index=True
    )
    amount = _money()
    reason = models.TextField()
    attachment = models.FileField(
        upload_to="adjustment_attachments/",
        blank=True,
        null=True,
        help_text="The letter or minute the waiver, scholarship or write-off rests on.",
    )
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="adjustments_applied",
    )
    journal_entry = models.OneToOneField(
        JournalEntry, null=True, on_delete=models.SET_NULL, related_name="adjustment"
    )

    objects = AdjustmentManager()

    class Meta:
        db_table = "finance_adjustment"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_adjustment_type_display()} {self.amount} for {self.student}"

    @property
    def posted(self) -> bool:
        return self.status == Adjustment.Status.POSTED


# ---------------------------------------------------------------------------
# Expenses, vendors and budgets
# ---------------------------------------------------------------------------


class Vendor(BaseModel):
    """
    Who the school pays. B8 asks for a vendors list, and a payee typed fresh on
    every claim is how one supplier ends up as three, each with its own history.

    Deliberately not a ``User``: most vendors never log in, and giving a market
    electrician a portal account would be a security decision nobody meant to
    make.
    """

    name = models.CharField(max_length=120, unique=True)
    contact_person = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="vendors_created",
    )

    class Meta:
        db_table = "finance_vendor"
        ordering = ["name"]
        verbose_name_plural = "vendors"

    def __str__(self) -> str:
        return self.name


class Expense(BaseModel):
    class PaymentVia(models.TextChoices):
        """
        How the school paid for something — the mirror image of ``Payment.Method``.

        Fees do not arrive as cash, but petty cash is still spent, so an expense
        needs to say which side of the ledger it drew on. Defaulting to petty cash
        keeps the behaviour a bursar expects for a receipt bought at the market.
        """

        CASH = "cash", "Petty cash"
        BANK = "bank", "Bank"

    class Status(models.TextChoices):
        """
        Whether the claim is a promise or a posting.

        ``pending`` holds no ledger line at all: A5 says an expense above the
        threshold is not one person's decision, and money the school has not
        agreed to spend cannot sit in the books while somebody waits for a
        signature. ``rejected`` keeps the row for the same reason — a bursar who
        was refused and left no trace is a bursar who tries again next month.
        """

        PENDING = "pending", "Awaiting approval"
        POSTED = "posted", "Posted"
        REJECTED = "rejected", "Rejected"

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="expenses",
        limit_choices_to={"type": Account.Type.EXPENSE, "is_active": True},
    )
    vendor = models.ForeignKey(
        Vendor,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expenses",
        help_text="Pick from the vendors list, or name the payee below.",
    )
    payee_name = models.CharField(
        max_length=120,
        blank=True,
        help_text="For a one-off payee with no vendor record — the casual labourer "
        "paid at the gate, not the supplier the school bills every term.",
    )
    reference = models.CharField(
        max_length=30,
        null=True,
        blank=True,
        unique=True,
        help_text="Taken from the expense number series when the claim is posted. "
        "Null on claims recorded before the sequence existed, and on a claim "
        "that never got past approval.",
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.POSTED, db_index=True
    )
    amount = _money()
    expense_date = models.DateField(db_index=True)
    paid_via = models.CharField(
        max_length=10, choices=PaymentVia.choices, default=PaymentVia.CASH
    )
    description = models.TextField()
    attachment = models.FileField(
        upload_to="expense_attachments/", blank=True, null=True
    )
    approval_note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="expenses_recorded",
    )
    journal_entry = models.OneToOneField(
        JournalEntry, null=True, on_delete=models.SET_NULL, related_name="expense"
    )

    class Meta:
        db_table = "finance_expense"
        ordering = ["-expense_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.payee or self.account.name}: {self.amount} on {self.expense_date}"

    @property
    def payee(self) -> str:
        """However this claim names the person who got the money."""
        return self.vendor.name if self.vendor_id else (self.payee_name or "—")

    @property
    def posted(self) -> bool:
        return self.status == Expense.Status.POSTED


class Budget(BaseModel):
    """
    What a school decided to spend on one category in one term, before spending.

    B8 asks for "simple budgets per term/category". Simple means one number per
    (term, expense account) and nothing else: no line-item budget, no rolling
    forecast, no carry-forward between terms — each of those is a policy the
    governing body has not written, and a budget model that pretends a rule
    exists invites someone to budget by it.

    The variance is never stored. It is ``journal debits minus this number``, and
    a stored copy would be a second answer to a question the ledger can already
    ask.
    """

    term = models.ForeignKey(
        "academics.Term", on_delete=models.PROTECT, related_name="budgets"
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="budgets",
        limit_choices_to={"type": Account.Type.EXPENSE},
    )
    amount = _money()
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="budgets_created",
    )

    class Meta:
        db_table = "finance_budget"
        ordering = ["-term__start_date", "account__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["term", "account"],
                name="finance_budget_one_number_per_category_per_term",
            )
        ]

    def __str__(self) -> str:
        return f"{self.account.name} — {self.term} — {self.amount}"

    def clean(self):
        super().clean()
        if self.amount is not None and to_money(self.amount) <= 0:
            raise ValidationError({"amount": "A budget must be a positive amount."})


# ---------------------------------------------------------------------------
# Period control
# ---------------------------------------------------------------------------


class FinancialPeriod(BaseModel):
    """
    A window of dates the books can be posted into, and whether it is still open.

    Two kinds, because a school closes money in two different rhythms: a **term**
    closes when billing for that term is finished, and a **month** closes when the
    books for that month are agreed. A posting date is checked against every
    period that contains it, so a closed month inside an open term still refuses
    the entry — that is the whole point, otherwise the tighter lock is invisible.

    Rows are created the first time they are needed (a posting, or a close), so
    an un-closed period costs nothing and an empty month never appears. Closing
    and reopening are logged through ``services``: reopenings additionally need
    an Admin, a reason, and they emit an event, because a reopened period makes
    yesterday's printed report wrong.
    """

    class Kind(models.TextChoices):
        TERM = "term", "Term"
        MONTH = "month", "Month"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    kind = models.CharField(max_length=10, choices=Kind.choices, db_index=True)
    term = models.ForeignKey(
        "academics.Term",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="financial_periods",
    )
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    month = models.PositiveSmallIntegerField(null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="periods_closed",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    close_note = models.TextField(blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="periods_reopened",
    )
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopen_reason = models.TextField(blank=True)

    class Meta:
        db_table = "finance_financial_period"
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["term"],
                condition=models.Q(kind="term"),
                name="finance_period_one_row_per_term",
            ),
            models.CheckConstraint(
                check=models.Q(end_date__gte=models.F("start_date")),
                name="finance_period_dates_ordered",
            ),
        ]
        indexes = [
            models.Index(
                fields=["kind", "year", "month"], name="finance_period_month_key"
            ),
            models.Index(
                fields=["start_date", "end_date"], name="finance_period_window"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.start_date}→{self.end_date} ({self.status})"

    def contains(self, day) -> bool:
        return self.start_date <= day <= self.end_date


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


class ApprovalRequest(BaseModel):
    """
    One ask for a second signature, and what was done with it.

    A5 segregates duties: an officer creates, an Admin decides. The decision is
    not a boolean stamped on the money row, because "who asked, why, who said no,
    when" is the part an auditor asks for and the part a ``needs_approval`` flag
    forgets. The row links to whatever is being asked about — a claim, a pending
    write-off, a receipt to reverse, a closed period to reopen — and approving it
    *performs* the action through the ordinary service, so the queue can never
    post money the posting path would have refused.
    """

    class Kind(models.TextChoices):
        EXPENSE = "expense", "Expense claim"
        WRITE_OFF = "write_off", "Write-off"
        REVERSAL = "reversal", "Receipt reversal"
        PERIOD_REOPEN = "period_reopen", "Reopen a closed period"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    #: Which table each kind belongs to. Read by the service to refuse a request
    #: that asks for one thing and points at another.
    TARGET_BY_KIND = {
        Kind.EXPENSE: "expense",
        Kind.WRITE_OFF: "adjustment",
        Kind.REVERSAL: "payment",
        Kind.PERIOD_REOPEN: "period",
    }

    kind = models.CharField(max_length=20, choices=Kind.choices, db_index=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    #: Money the ask puts at risk. Nullable because the one kind that always needs
    #: a decision — reopening a closed period — cannot name an amount: it exposes
    #: every entry in the term, not one figure a queue could be sorted by.
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="How much this ask puts at risk. Empty for a period reopen, whose "
        "risk is the whole term rather than a single number.",
    )
    reason = models.TextField(
        help_text="Why this is being asked for. It is what the approver reads, "
        "and it is also what gets performed when the ask is approved."
    )
    expense = models.ForeignKey(
        Expense,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approval_requests",
    )
    adjustment = models.ForeignKey(
        Adjustment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approval_requests",
    )
    payment = models.ForeignKey(
        Payment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approval_requests",
    )
    period = models.ForeignKey(
        FinancialPeriod,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approval_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="approvals_requested",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="approvals_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        db_table = "finance_approval_request"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "kind"], name="approval_queue_open")]
        constraints = [
            # One live ask per thing. A second officer who cannot see the first
            # should not be able to queue the same decision twice — two approvals
            # on one claim would mean two postings, and the second would fail.
            models.UniqueConstraint(
                fields=["expense"],
                condition=models.Q(status="pending", expense__isnull=False),
                name="approval_one_open_ask_per_expense",
            ),
            models.UniqueConstraint(
                fields=["adjustment"],
                condition=models.Q(status="pending", adjustment__isnull=False),
                name="approval_one_open_ask_per_adjustment",
            ),
            models.UniqueConstraint(
                fields=["payment"],
                condition=models.Q(status="pending", payment__isnull=False),
                name="approval_one_open_ask_per_payment",
            ),
            models.UniqueConstraint(
                fields=["period"],
                condition=models.Q(status="pending", period__isnull=False),
                name="approval_one_open_ask_per_period",
            ),
        ]

    def __str__(self) -> str:
        amount = f" {self.amount}" if self.amount is not None else ""
        return f"{self.get_kind_display()}{amount} — {self.get_status_display()}"

    @property
    def target_field(self) -> str:
        return self.TARGET_BY_KIND[self.kind]

    @property
    def target(self):
        """The row being asked about, whatever kind this is."""
        return getattr(self, self.target_field)

    def clean(self):
        super().clean()
        if self.kind not in self.Kind.values:
            raise ValidationError({"kind": "Unknown approval kind."})
        if not self.reason.strip():
            raise ValidationError({"reason": "An approval ask needs a reason."})
        if self.kind != self.Kind.PERIOD_REOPEN and self.amount is None:
            raise ValidationError(
                {"amount": "Only a period reopen can ask without naming an amount."}
            )
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "The amount at risk must be positive."})
        filled = [
            name
            for name in self.TARGET_BY_KIND.values()
            if getattr(self, f"{name}_id") is not None
        ]
        if len(filled) != 1:
            raise ValidationError(
                {"reason": "An approval ask points at exactly one document."}
            )
        if filled[0] != self.target_field:
            raise ValidationError(
                {
                    "reason": f"A {self.get_kind_display()} ask points at "
                    f"{'a ' + filled[0]}, not {self.target_field}."
                }
            )
        if self.status != self.Status.PENDING and self.decided_by_id is None:
            raise ValidationError({"decided_by": "A decided ask names who decided it."})


class FinanceSettings(BaseModel):
    """
    The knobs the Finance Officer runs the money from. A singleton: pk is always 1.

    Kept apart from ``core.SchoolSettings`` on purpose: that row is the school's
    *identity* and everyone reads it on every page, while these are the rules for
    posting money and only finance screens and the ledger services need them. The
    currency is deliberately NOT here — one school keeps its books in one
    currency, and ``SchoolSettings.currency`` is already that answer
    (``core.money.currency_code()`` reads it).

    Anything that changes what may be posted, or how much someone may post
    without a second signature, is an Admin control: ``backdating_days``,
    ``period_lock_enabled`` and the three approval thresholds. A Finance Officer
    may set numbering, reminder throttling and the bank details printed on an
    invoice. Every change is audit-logged by the service, not by a ``save()`` —
    which is also why the fields are plain: the trail lives in ``AuditLog``.
    """

    SINGLETON_PK = 1

    # Numbering — the prefix of a gap-free sequence. The counter itself is
    # ``NumberSeries``, one row per document type per year, taken with a
    # compare-and-set rather than a lock SQLite cannot give.
    invoice_prefix = models.CharField(max_length=8, default="INV")
    receipt_prefix = models.CharField(max_length=8, default="RCP")
    expense_prefix = models.CharField(
        max_length=8,
        default="EXP",
        help_text="Prefix for the expense claim sequence. A claim a bursar has to "
        "find again in two years is found by its number, not its description.",
    )

    period_lock_enabled = models.BooleanField(
        default=True,
        help_text="Refuse postings into a closed period.",
    )
    backdating_days = models.PositiveSmallIntegerField(
        default=7,
        help_text=(
            "How far back from today a posting may be dated. Zero means today "
            "only. Widening it is an Admin decision, not a form field."
        ),
    )

    approval_reversal_threshold = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("500.00"),
        help_text="Reversing a receipt above this amount needs an Admin approval.",
    )
    approval_expense_threshold = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("1000.00"),
        help_text="Recording an expense above this amount needs an Admin approval.",
    )
    write_off_always_needs_approval = models.BooleanField(
        default=True,
        help_text="A write-off is giving up a debt, so it never goes unseconded.",
    )

    reminder_throttle_days = models.PositiveSmallIntegerField(
        default=7,
        help_text="Minimum days between two fee reminders to the same family.",
    )
    late_fees_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Off until the school decides it wants to charge for lateness. "
            "When on, an overdue invoice is flagged; nothing is posted "
            "automatically."
        ),
    )
    late_fee_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    late_fee_grace_days = models.PositiveSmallIntegerField(default=0)

    budget_alert_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("80.00"),
        help_text=(
            "Warn when a category has spent this share of its term budget. A "
            "school that only finds out at 100% has no chance left to stop."
        ),
    )

    bank_details = models.TextField(
        blank=True,
        help_text="Printed on invoices so a payer knows where to send the money.",
    )

    class Meta:
        db_table = "finance_settings"
        verbose_name = "finance settings"
        verbose_name_plural = "finance settings"

    def __str__(self) -> str:
        return "Finance settings"

    @classmethod
    def load(cls) -> FinanceSettings:
        """Return the singleton row, creating it with its defaults if absent."""
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj

    def amount_needs_approval(self, kind: str, amount) -> bool:
        """
        Whether a posting of this amount must be approved before it takes effect.

        ``to_money`` is applied first: comparing a raw form string to a threshold
        is how a 999.999 slips under a 1,000 rule.
        """
        amount = to_money(amount)
        if kind == "reversal":
            return amount > to_money(self.approval_reversal_threshold)
        if kind == "expense":
            return amount > to_money(self.approval_expense_threshold)
        if kind == "write_off":
            return bool(self.write_off_always_needs_approval) or amount > to_money(
                self.approval_expense_threshold
            )
        return False
