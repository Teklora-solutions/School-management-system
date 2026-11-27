"""
apps/api/urls.py
----------------
URL configuration for the external integration API.

Mounted at /api/v1/ from config/urls.py.

Browsable API schema (OpenAPI / Swagger):
    /api/v1/schema/            — raw OpenAPI JSON/YAML
    /api/v1/schema/swagger-ui/ — interactive Swagger UI
    /api/v1/schema/redoc/      — ReDoc documentation

JWT auth endpoints:
    POST /api/v1/auth/token/          — obtain access + refresh tokens
    POST /api/v1/auth/token/refresh/  — refresh an access token
    POST /api/v1/auth/token/verify/   — verify a token is valid
"""

from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from .inbound import InboundWebhookView
from .views import (
    AcademicYearViewSet,
    APIClientViewSet,
    AttendanceViewSet,
    SchoolClassViewSet,
    StudentViewSet,
    SubjectViewSet,
    TeacherViewSet,
    TermViewSet,
    WebhookDeliveryViewSet,
    WebhookEndpointViewSet,
)

app_name = "api_v1"

router = DefaultRouter()
router.register("academic-years", AcademicYearViewSet, basename="academic-year")
router.register("terms", TermViewSet, basename="term")
router.register("subjects", SubjectViewSet, basename="subject")
router.register("classes", SchoolClassViewSet, basename="class")
router.register("students", StudentViewSet, basename="student")
router.register("staff", TeacherViewSet, basename="staff")
router.register("attendance", AttendanceViewSet, basename="attendance")
router.register("webhooks/endpoints", WebhookEndpointViewSet, basename="webhook-endpoint")
router.register("webhooks/deliveries", WebhookDeliveryViewSet, basename="webhook-delivery")
router.register("clients", APIClientViewSet, basename="api-client")

urlpatterns = [
    # OpenAPI schema
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path(
        "schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="api_v1:api-schema"),
        name="swagger-ui",
    ),
    path(
        "schema/redoc/",
        SpectacularRedocView.as_view(url_name="api_v1:api-schema"),
        name="redoc",
    ),
    # JWT auth
    path("auth/token/", TokenObtainPairView.as_view(), name="token-obtain"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/token/verify/", TokenVerifyView.as_view(), name="token-verify"),
    # Inbound webhooks from external systems
    path(
        "inbound/<str:source_slug>/",
        InboundWebhookView.as_view(),
        name="inbound-webhook",
    ),
    # All router-registered endpoints
    path("", include(router.urls)),
]
