import pathlib
import textwrap

TARGET = pathlib.Path(
    r"c:\Users\JNR Developer\Desktop\school system\school-management-system\backend\apps\finance\models.py"
)

code = textwrap.dedent(
    '''\
    """
    apps/finance/models.py
    Double-entry financial ledger for the school.

    Design rules:
      - All monetary fields use DecimalField(max_digits=14, decimal_places=2).
      - Ledger is APPEND-ONLY. Corrections are new reversing entries.
      - Every Payment has a unique reference (idempotency guard).
      - Invoice has unique_together=(student, term, fee_structure).
    """

    from __future__ import annotations

    import uuid
    from decimal import Decimal

    from django.conf import settings
    from django.core.exceptions import ValidationError
    from django.db import models

    from apps.core.models import BaseModel


    def _money():
        return models.DecimalField(max_digits=14, decimal_places=2)


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
        is_system = models.BooleanField(default=False)
        is_active = models.BooleanField(default=True, db_index=True)
        description = models.TextField(blank=True)

        class Meta:
            db_table = "finance_account"
            ordering = ["code"]

        def __str__(self) -> str:
            return f"{self.code} - {self.name}"


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
        note = models.TextField(blank=True)
        created_by = models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            on_delete=models.SET_NULL,
            related_name="fee_structures_created",
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
            return self.items.aggregate(t=models.Sum("amount"))["t"] or Decimal("0.00")


    class FeeItem(BaseModel):
        structure = models.ForeignKey(FeeStructure, on_delete=models.CASCADE, related_name="items")
        label = models.CharField(max_length=100)
        amount = _money()
        ordering = models.PositiveSmallIntegerField(default=0)

        class Meta:
            db_table = "finance_fee_item"
            ordering = ["ordering", "label"]

        def __str__(self) -> str:
            return f"{self.label}: {self.amount}"


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
        invoice_number = models.CharField(max_length=30, unique=True, default=_invoice_number)
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

        def __str__(self) -> str:
            return f"{self.invoice_number} - {self.student} ({self.term})"

        @property
        def amount_paid(self) -> Decimal:
            return (
                self.allocations.aggregate(t=models.Sum("allocated_amount"))["t"]
                or Decimal("0.00")
            )

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


    class Payment(BaseModel):
        class Method(models.TextChoices):
            CASH = "cash", "Cash"
            BANK = "bank", "Bank Transfer"
            MOBILE_MONEY = "mobile_money", "Mobile Money"
            OTHER = "other", "Other"

        student = models.ForeignKey(
            "students.Student", on_delete=models.PROTECT, related_name="payments"
        )
        reference = models.CharField(max_length=100, unique=True, db_index=True)
        receipt_number = models.CharField(max_length=30, unique=True, default=_receipt_number)
        payment_date = models.DateField()
        method = models.CharField(max_length=20, choices=Method.choices)
        amount = _money()
        notes = models.TextField(blank=True)
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

        def __str__(self) -> str:
            return f"{self.receipt_number} - {self.student} ({self.amount})"

        def clean(self):
            if self.amount is not None and self.amount <= 0:
                raise ValidationError({"amount": "Payment amount must be positive."})


    class PaymentAllocation(BaseModel):
        payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
        invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="allocations")
        allocated_amount = _money()

        class Meta:
            db_table = "finance_payment_allocation"
            unique_together = [("payment", "invoice")]

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
        source_type = models.CharField(max_length=20, choices=SourceType.choices, db_index=True)
        source_id = models.CharField(max_length=40, blank=True, db_index=True)
        reverses = models.OneToOneField(
            "self",
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name="reversed_by",
        )

        class Meta:
            db_table = "finance_journal_entry"
            ordering = ["-entry_date", "-created_at"]

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

        entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
        account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="journal_lines")
        debit_amount = _money()
        credit_amount = _money()

        class Meta:
            db_table = "finance_journal_line"
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

        def __str__(self) -> str:
            side = (
                f"DR {self.debit_amount}" if self.debit_amount else f"CR {self.credit_amount}"
            )
            return f"{self.entry.reference} | {self.account.code} | {side}"


    # ---------------------------------------------------------------------------
    # Adjustments
    # ---------------------------------------------------------------------------

    class Adjustment(BaseModel):
        class AdjustmentType(models.TextChoices):
            DISCOUNT = "discount", "Discount"
            SCHOLARSHIP = "scholarship", "Scholarship"
            WAIVER = "waiver", "Waiver"
            OTHER = "other", "Other"

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
        amount = _money()
        reason = models.TextField()
        applied_by = models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            on_delete=models.SET_NULL,
            related_name="adjustments_applied",
        )
        journal_entry = models.OneToOneField(
            JournalEntry, null=True, on_delete=models.SET_NULL, related_name="adjustment"
        )

        class Meta:
            db_table = "finance_adjustment"
            ordering = ["-created_at"]

        def __str__(self) -> str:
            return f"{self.get_adjustment_type_display()} {self.amount} for {self.student}"


    # ---------------------------------------------------------------------------
    # Expenses
    # ---------------------------------------------------------------------------

    class Expense(BaseModel):
        account = models.ForeignKey(
            Account,
            on_delete=models.PROTECT,
            related_name="expenses",
            limit_choices_to={"type": Account.Type.EXPENSE, "is_active": True},
        )
        amount = _money()
        expense_date = models.DateField(db_index=True)
        description = models.TextField()
        attachment = models.FileField(upload_to="expense_attachments/", blank=True, null=True)
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
            return f"{self.account.name}: {self.amount} on {self.expense_date}"
'''
)

TARGET.write_text(code, encoding="utf-8")
print("OK", TARGET.stat().st_size)
