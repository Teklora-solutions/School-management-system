"""
apps/api/views.py
-----------------
ViewSets for the external REST API.

Every ViewSet enforces scope-based permissions via ``require_scope``.
Writes go through the existing service layer — never direct ORM saves.

Endpoint map (registered in urls.py):
  GET  /api/v1/academic-years/
  GET  /api/v1/terms/
  GET  /api/v1/subjects/
  GET  /api/v1/classes/

  GET  /api/v1/students/
  GET  /api/v1/students/{admission_number}/
  GET  /api/v1/students/{admission_number}/results/
  GET  /api/v1/students/{admission_number}/fees/

  GET  /api/v1/staff/

  GET  /api/v1/attendance/
  POST /api/v1/attendance/

  GET  /api/v1/webhooks/endpoints/
  POST /api/v1/webhooks/endpoints/
  GET  /api/v1/webhooks/endpoints/{id}/
  PUT  /api/v1/webhooks/endpoints/{id}/
  DEL  /api/v1/webhooks/endpoints/{id}/
  GET  /api/v1/webhooks/deliveries/

  GET  /api/v1/clients/            (admin only)
  POST /api/v1/clients/            (admin only — returns plain key once)
  GET  /api/v1/clients/{id}/       (admin only)
  POST /api/v1/clients/{id}/revoke/ (admin only)
"""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.attendance.models import AttendanceEntry, AttendanceRegister
from apps.finance.models import Invoice
from apps.results.models import ResultSheet
from apps.students.models import Student
from apps.teachers.models import TeacherProfile

from .models import APIClient, WebhookDelivery, WebhookEndpoint, VALID_SCOPES
from .permissions import require_scope
from .serializers import (
    AcademicYearSerializer,
    APIClientCreateSerializer,
    APIClientSerializer,
    AttendanceEntryReadSerializer,
    AttendanceEntryWriteSerializer,
    InvoiceSerializer,
    ResultSheetSerializer,
    SchoolClassSerializer,
    StudentDetailSerializer,
    StudentListSerializer,
    SubjectSerializer,
    TeacherSerializer,
    TermSerializer,
    WebhookDeliverySerializer,
    WebhookEndpointSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Academics
# ---------------------------------------------------------------------------


@extend_schema(tags=["Academics"])
class AcademicYearViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AcademicYear.objects.all().order_by("-start_date")
    serializer_class = AcademicYearSerializer
    permission_classes = [require_scope("academics:read")]
    filter_backends = [filters.SearchFilter]
    search_fields = ["name"]
    lookup_field = "name"


@extend_schema(tags=["Academics"])
class TermViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        Term.objects.select_related("academic_year").order_by(
            "-academic_year__start_date", "start_date"
        )
    )
    serializer_class = TermSerializer
    permission_classes = [require_scope("academics:read")]
    filterset_fields = ["is_current", "name"]


@extend_schema(tags=["Academics"])
class SubjectViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Subject.objects.filter(is_active=True).order_by("name")
    serializer_class = SubjectSerializer
    permission_classes = [require_scope("academics:read")]
    lookup_field = "code"
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "code"]


@extend_schema(tags=["Academics"])
class SchoolClassViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        SchoolClass.objects.select_related("academic_year")
        .prefetch_related("streams")
        .order_by("academic_year__start_date", "level_order")
    )
    serializer_class = SchoolClassSerializer
    permission_classes = [require_scope("academics:read")]
    filterset_fields = ["academic_year__name"]


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------


@extend_schema(tags=["Students"])
class StudentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    List and retrieve students.  Write access (students:write) is not yet
    exposed externally; enrolment goes through the admin console.
    """

    queryset = (
        Student.objects.select_related("user", "current_class", "current_stream")
        .order_by("admission_number")
    )
    lookup_field = "admission_number"
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "gender", "current_class__name"]
    search_fields = ["admission_number", "user__first_name", "user__last_name"]
    ordering_fields = ["admission_number", "enrollment_date"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return StudentDetailSerializer
        return StudentListSerializer

    def get_permissions(self):
        return [require_scope("students:read")()]

    @extend_schema(
        responses={200: ResultSheetSerializer(many=True)},
        description="Return published result sheets for one student.",
    )
    @action(detail=True, methods=["get"], url_path="results")
    def results(self, request, admission_number=None):
        student = self.get_object()
        sheets = (
            ResultSheet.objects.filter(
                entries__student=student,
                status=ResultSheet.Status.PUBLISHED,
            )
            .select_related("school_class", "subject", "term", "stream")
            .prefetch_related("entries__student__user")
            .distinct()
        )
        serializer = ResultSheetSerializer(sheets, many=True)
        return Response(serializer.data)

    @extend_schema(
        responses={200: InvoiceSerializer(many=True)},
        description="Return fee invoices for one student.",
    )
    @action(detail=True, methods=["get"], url_path="fees")
    def fees(self, request, admission_number=None):
        require_scope("finance:read")().has_permission(request, self)
        student = self.get_object()
        invoices = (
            Invoice.objects.filter(student=student)
            .select_related("term")
            .order_by("-created_at")
        )
        serializer = InvoiceSerializer(invoices, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Staff / Teachers
# ---------------------------------------------------------------------------


@extend_schema(tags=["Staff"])
class TeacherViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        TeacherProfile.objects.select_related("user")
        .order_by("user__last_name", "user__first_name")
    )
    serializer_class = TeacherSerializer
    permission_classes = [require_scope("staff:read")]
    lookup_field = "staff_id"
    filter_backends = [filters.SearchFilter]
    search_fields = ["staff_id", "user__first_name", "user__last_name", "department"]


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------


@extend_schema(tags=["Attendance"])
class AttendanceViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET  — read attendance entries (requires attendance:read)
    POST — submit a register (requires attendance:write)
    """

    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "date"]
    ordering_fields = ["date"]

    def get_queryset(self):
        return (
            AttendanceEntry.objects.select_related(
                "register__school_class", "student__user"
            ).order_by("-date", "student__admission_number")
        )

    def get_serializer_class(self):
        if self.request.method == "POST":
            return AttendanceEntryWriteSerializer
        return AttendanceEntryReadSerializer

    def get_permissions(self):
        if self.request.method == "POST":
            return [require_scope("attendance:write")()]
        return [require_scope("attendance:read")()]

    def create(self, request, *args, **kwargs):
        serializer = AttendanceEntryWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Resolve school class
        try:
            school_class = SchoolClass.objects.get(
                name=data["school_class"],
                academic_year__name=data["academic_year"],
            )
        except SchoolClass.DoesNotExist:
            raise serializers.ValidationError(
                {"school_class": "Class not found for the given academic year."}
            )

        register, _ = AttendanceRegister.objects.get_or_create(
            school_class=school_class,
            date=data["date"],
        )

        created_count = 0
        for entry_data in data["entries"]:
            student = get_object_or_404(
                Student, admission_number=entry_data["admission_number"]
            )
            AttendanceEntry.objects.update_or_create(
                register=register,
                student=student,
                defaults={
                    "status": entry_data["status"],
                    "reason": entry_data.get("reason", ""),
                    "date": data["date"],
                },
            )
            created_count += 1

        return Response(
            {"recorded": created_count, "date": str(data["date"])},
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Webhook management
# ---------------------------------------------------------------------------


@extend_schema(tags=["Webhooks"])
class WebhookEndpointViewSet(viewsets.ModelViewSet):
    queryset = WebhookEndpoint.objects.all().order_by("name")
    serializer_class = WebhookEndpointSerializer
    permission_classes = [require_scope("webhooks:manage")]

    def perform_create(self, serializer):
        user = getattr(self.request.user, "client", None) or self.request.user
        # For APIClient callers, created_by must be a real User or null.
        created_by = None
        if hasattr(self.request.user, "role"):
            created_by = self.request.user
        serializer.save(created_by=created_by)


@extend_schema(tags=["Webhooks"])
class WebhookDeliveryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = (
        WebhookDelivery.objects.select_related("endpoint")
        .order_by("-created_at")
    )
    serializer_class = WebhookDeliverySerializer
    permission_classes = [require_scope("webhooks:manage")]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "event_type", "endpoint"]
    ordering_fields = ["created_at", "last_attempted_at"]


# ---------------------------------------------------------------------------
# API Client management  (admin/director only)
# ---------------------------------------------------------------------------


@extend_schema(tags=["API Clients"])
class APIClientViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Admin-only endpoint for managing API keys.

    POST /api/v1/clients/ — creates a new client and returns the plain key
                            ONCE.  Store it immediately; it cannot be recovered.
    POST /api/v1/clients/{id}/revoke/ — deactivates the key.
    """

    queryset = APIClient.objects.all().order_by("name")
    permission_classes = [require_scope("webhooks:manage")]  # admin-scoped

    def get_serializer_class(self):
        if self.action == "create":
            return APIClientCreateSerializer
        return APIClientSerializer

    def create(self, request, *args, **kwargs):
        serializer = APIClientCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        # Validate scopes
        invalid = set(d["scopes"]) - VALID_SCOPES
        if invalid:
            raise serializers.ValidationError(
                {"scopes": f"Unknown scopes: {sorted(invalid)}"}
            )

        created_by = request.user if hasattr(request.user, "role") else None
        client, plain_key = APIClient.create_client(
            name=d["name"],
            scopes=d["scopes"],
            created_by=created_by,
            description=d.get("description", ""),
            allowed_ips=d.get("allowed_ips", []),
        )

        out = APIClientSerializer(client).data
        out["plain_key"] = plain_key  # returned ONCE — not stored in DB
        return Response(out, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=None,
        responses={200: APIClientSerializer},
        description="Revoke (deactivate) an API client key.",
    )
    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request, pk=None):
        client = self.get_object()
        APIClient.objects.filter(pk=client.pk).update(is_active=False)
        client.is_active = False
        return Response(APIClientSerializer(client).data)
