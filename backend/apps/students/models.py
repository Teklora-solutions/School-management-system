"""
apps/students/models.py
-----------------------
Student records, guardian links, bulk import jobs, and promotion history.
"""

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class Student(BaseModel):
    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        OTHER = "O", "Other"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REPEATING = "repeating", "Repeating"
        GRADUATED = "graduated", "Graduated"
        WITHDRAWN = "withdrawn", "Withdrawn"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_profile",
    )
    admission_number = models.CharField(max_length=40, unique=True, db_index=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    current_class = models.ForeignKey(
        "academics.SchoolClass",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="students",
    )
    current_stream = models.ForeignKey(
        "academics.Stream",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="students",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    enrollment_date = models.DateField(null=True, blank=True)
    address = models.TextField(blank=True)
    # Finance-related fields (updated by Finance officers; audited via core.AuditLog)
    sponsor = models.CharField(
        max_length=200,
        blank=True,
        help_text="Name of sponsor or scholarship body, if any.",
    )
    payment_plan_notes = models.TextField(
        blank=True,
        help_text="Payment plan arrangements, instalment schedule, or other finance notes.",
    )

    class Meta:
        db_table = "students_student"
        ordering = ["admission_number"]

    def __str__(self) -> str:
        return f"{self.admission_number} � {self.user.full_name}"


class StudentGuardian(BaseModel):
    class Relationship(models.TextChoices):
        FATHER = "father", "Father"
        MOTHER = "mother", "Mother"
        GUARDIAN = "guardian", "Guardian"
        OTHER = "other", "Other"

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="guardian_links"
    )
    parent = models.ForeignKey(
        "accounts.ParentProfile",
        on_delete=models.CASCADE,
        related_name="children_links",
    )
    relationship = models.CharField(
        max_length=20, choices=Relationship.choices, default=Relationship.GUARDIAN
    )
    is_primary = models.BooleanField(default=True)

    class Meta:
        db_table = "students_student_guardian"
        unique_together = [("student", "parent")]

    def __str__(self) -> str:
        return f"{self.parent} ? {self.student}"


class BulkImportJob(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VALIDATING = "validating", "Validating"
        READY = "ready", "Ready for commit"
        COMMITTED = "committed", "Committed"
        FAILED = "failed", "Failed"

    class Mode(models.TextChoices):
        ALL_OR_NOTHING = "all_or_nothing", "All or nothing"
        PARTIAL = "partial", "Commit valid rows only"

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="bulk_import_jobs",
    )
    file = models.FileField(upload_to="bulk_imports/")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    mode = models.CharField(
        max_length=20, choices=Mode.choices, default=Mode.ALL_OR_NOTHING
    )
    target_class = models.ForeignKey(
        "academics.SchoolClass",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bulk_imports",
    )
    # Validated rows + errors stored as JSON for preview
    preview_rows = models.JSONField(default=list, blank=True)
    error_rows = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    committed_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "students_bulk_import_job"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"BulkImport#{self.pk} ({self.status})"


class PromotionBatch(BaseModel):
    """History record for a class-promotion commit."""

    from_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.PROTECT,
        related_name="promotions_from",
    )
    to_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.PROTECT,
        related_name="promotions_to",
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.PROTECT,
        related_name="promotion_batches",
        help_text="Term whose published results gate the promotion.",
    )
    committed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="promotion_batches",
    )
    override_unpublished = models.BooleanField(default=False)
    override_reason = models.TextField(blank=True)
    preview_snapshot = models.JSONField(default=dict, blank=True)
    is_committed = models.BooleanField(default=False)
    committed_at = models.DateTimeField(null=True, blank=True)
    # Idempotency key: one committed batch per (from_year, to_year)
    idempotency_key = models.CharField(max_length=64, unique=True)

    class Meta:
        db_table = "students_promotion_batch"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        state = "committed" if self.is_committed else "draft"
        return f"Promotion {self.from_year} ? {self.to_year} ({state})"


class PromotionRecord(BaseModel):
    class Action(models.TextChoices):
        PROMOTE = "promote", "Promote"
        REPEAT = "repeat", "Repeat"
        GRADUATE = "graduate", "Graduate"

    batch = models.ForeignKey(
        PromotionBatch, on_delete=models.CASCADE, related_name="records"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="promotion_records"
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    from_class = models.ForeignKey(
        "academics.SchoolClass",
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    to_class = models.ForeignKey(
        "academics.SchoolClass",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "students_promotion_record"
        unique_together = [("batch", "student")]

    def __str__(self) -> str:
        return f"{self.student} ? {self.action}"
