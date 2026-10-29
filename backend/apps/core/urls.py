"""
apps/core/urls.py
-----------------
Health and readiness probe endpoints.
Mounted at /healthz/ and /readyz/ in root urls.py.
Neither endpoint requires authentication.
"""

from django.urls import path

from .health import ReadinessView

urlpatterns = [
    path("", ReadinessView.as_view(), name="readiness"),
    path("ready/", ReadinessView.as_view(), name="readiness_alias"),
]
