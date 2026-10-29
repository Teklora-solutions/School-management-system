"""
apps/core/urls_audit.py
-----------------------
Admin-facing core URLs (audit log viewer).
Mounted at /audit/ in the root URLconf — kept separate from the
unauthenticated health-probe urls.py.
"""

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("log/", views.AuditLogListView.as_view(), name="audit_log"),
]
