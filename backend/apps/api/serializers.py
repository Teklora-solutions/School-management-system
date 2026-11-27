"""
apps/api/serializers.py
-----------------------
Read serializers for every public-facing resource.

Design rules:
- Never expose internal auto-increment PKs. Use natural keys:
  admission_number for students, code for subjects/accounts, etc.
- Published results only — raw exam marks are never sent externally.
- Monetary amounts are serialized as strings to avoid float precision loss.
- Write serializers are defined only where the API accepts inbound data
  (attendance POST, finance payment confirmation).
"""

from __future__ import annotations

from rest_framework import serializers

from apps.academics.models import AcademicYear, SchoolClass, Stream, Subject, Term
from apps.attendance.models import AttendanceEntry, AttendanceRegister
from apps.finance.models import Invoice
from apps.results.models import ResultSheet, ScoreEntry
from apps.students.models import Student
from apps.teachers.models import TeacherProfile

from .models import APIClient, WebhookDelivery, WebhookEndpoint


# ---------------------------------------------------------------------------
# Academics
# ---------------------------------------------------------------------------


class AcademicYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = ["name", "start_date", "end_date", "is_current"]


class TermSerializer(serializers.ModelSerializer):
    academic_year = serializers.StringRelatedField()

    class Meta:
        model = Term
        fields = ["academic_year", "name", "start_date", "end_date", "is_current"]


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ["code", "name", "is_active"]


class StreamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stream
        fields = ["name"]


class SchoolClassSerializer(serializers.ModelSerializer):
    academic_year = serializers.StringRelatedField()
    streams = StreamSerializer(many=True, read_only=True)

    class Meta:
        model = SchoolClass
        fields = ["name", "level_order", "academic_year", "streams"]


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------


class StudentListSerializer(serializers.ModelSerializer):
    """Compact representation for list endpoints."""

    full_name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    current_class = serializers.StringRelatedField()
    current_stream = serializers.StringRelatedField()

    class Meta:
        model = Student
        fields = [
            "admission_number",
            "full_name",
            "email",
            "gender",
            "status",
            "current_class",
            "current_stream",
            "enrollment_date",
        ]


class StudentDetailSerializer(serializers.ModelSerializer):
    """Full representation for the single-student endpoint."""

    full_name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)
    current_class = serializers.StringRelatedField()
    current_stream = serializers.StringRelatedField()

    class Meta:
        model = Student
        fields = [
            "admission_number",
            "full_name",
            "email",
            "phone",
            "date_of_birth",
            "gender",
            "status",
            "current_class",
            "current_stream",
            "enrollment_date",
            "address",
        ]


# ---------------------------------------------------------------------------
# Teachers / Staff
# ---------------------------------------------------------------------------


class TeacherSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = TeacherProfile
        fields = [
            "staff_id",
            "full_name",
            "email",
            "qualification",
            "department",
            "date_joined",
        ]


# ---------------------------------------------------------------------------
# Results  (published only)
# ---------------------------------------------------------------------------


class ScoreEntrySerializer(serializers.ModelSerializer):
    student = serializers.CharField(
        source="student.admission_number", read_only=True
    )
    student_name = serializers.CharField(
        source="student.user.full_name", read_only=True
    )

    class Meta:
        model = ScoreEntry
        fields = [
            "student",
            "student_name",
            "raw_score",
            "total",
            "grade",
            "remark",
            "points",
        ]


class ResultSheetSerializer(serializers.ModelSerializer):
    school_class = serializers.StringRelatedField()
    subject = serializers.StringRelatedField()
    term = serializers.StringRelatedField()
    stream = serializers.StringRelatedField()
    scores = ScoreEntrySerializer(many=True, read_only=True, source="entries")

    class Meta:
        model = ResultSheet
        fields = [
            "school_class",
            "subject",
            "term",
            "stream",
            "status",
            "published_at",
            "scores",
        ]


# ---------------------------------------------------------------------------
# Finance  (read-only: invoices + balance)
# ---------------------------------------------------------------------------


class InvoiceSerializer(serializers.ModelSerializer):
    student = serializers.CharField(
        source="student.admission_number", read_only=True
    )
    term = serializers.StringRelatedField()
    total_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, coerce_to_string=True
    )
    amount_paid = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        coerce_to_string=True,
        read_only=True,
    )
    amount_outstanding = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        coerce_to_string=True,
        read_only=True,
    )

    class Meta:
        model = Invoice
        fields = [
            "invoice_number",
            "student",
            "term",
            "total_amount",
            "amount_paid",
            "amount_outstanding",
            "status",
            "issued_date",
            "due_date",
            "created_at",
        ]


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------


class AttendanceEntryReadSerializer(serializers.ModelSerializer):
    student = serializers.CharField(
        source="student.admission_number", read_only=True
    )

    class Meta:
        model = AttendanceEntry
        fields = ["student", "status", "date", "reason"]


class AttendanceEntryWriteSerializer(serializers.Serializer):
    """
    Accepts a list of attendance marks for one class on one date.

    Expected shape:
        {
          "school_class": "Grade 5",
          "academic_year": "2025/2026",
          "date": "2026-11-26",
          "entries": [
            {"admission_number": "ADM001", "status": "present"},
            {"admission_number": "ADM002", "status": "absent", "note": "sick"}
          ]
        }
    """

    class _EntryItem(serializers.Serializer):
        admission_number = serializers.CharField(max_length=40)
        status = serializers.ChoiceField(choices=AttendanceEntry.Status.choices)
        reason = serializers.CharField(max_length=200, required=False, allow_blank=True)

    school_class = serializers.CharField(max_length=64)
    academic_year = serializers.CharField(max_length=32)
    date = serializers.DateField()
    entries = _EntryItem(many=True, min_length=1)


# ---------------------------------------------------------------------------
# Webhook management
# ---------------------------------------------------------------------------


class WebhookEndpointSerializer(serializers.ModelSerializer):
    # Never expose the secret in list/detail responses; only on creation.
    secret = serializers.CharField(read_only=True)
    created_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = WebhookEndpoint
        fields = [
            "id",
            "name",
            "url",
            "secret",
            "events",
            "is_active",
            "consecutive_failures",
            "disabled_at",
            "created_by",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "secret",
            "consecutive_failures",
            "disabled_at",
            "created_by",
            "created_at",
        ]


class WebhookDeliverySerializer(serializers.ModelSerializer):
    endpoint_name = serializers.CharField(source="endpoint.name", read_only=True)

    class Meta:
        model = WebhookDelivery
        fields = [
            "id",
            "endpoint_name",
            "event_type",
            "idempotency_key",
            "status",
            "attempts",
            "response_status_code",
            "last_attempted_at",
            "delivered_at",
            "next_retry_at",
            "error_detail",
            "created_at",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# APIClient management  (admin-only)
# ---------------------------------------------------------------------------


class APIClientSerializer(serializers.ModelSerializer):
    created_by = serializers.StringRelatedField(read_only=True)
    # plain_key is write-only: set on creation, never readable again.
    plain_key = serializers.CharField(read_only=True)

    class Meta:
        model = APIClient
        fields = [
            "id",
            "name",
            "key_prefix",
            "scopes",
            "is_active",
            "allowed_ips",
            "description",
            "last_used_at",
            "created_by",
            "created_at",
            "plain_key",
        ]
        read_only_fields = [
            "id",
            "key_prefix",
            "last_used_at",
            "created_by",
            "created_at",
            "plain_key",
        ]


class APIClientCreateSerializer(serializers.Serializer):
    """Used only for the POST that creates a new APIClient."""

    name = serializers.CharField(max_length=120)
    scopes = serializers.ListField(
        child=serializers.CharField(), min_length=1
    )
    description = serializers.CharField(required=False, allow_blank=True, default="")
    allowed_ips = serializers.ListField(
        child=serializers.IPAddressField(), required=False, default=list
    )
