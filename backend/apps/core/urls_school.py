"""
apps/core/urls_school.py
------------------------
Admin-facing URLs for school-wide configuration.
Mounted at /school/. Uses its own namespace because ``core`` is already taken
by the audit viewer in urls_audit.py — two includes sharing one namespace make
reverse() ambiguous (Django's urls.W005).
"""

from django.urls import path

from . import views

app_name = "core_school"

urlpatterns = [
    path("", views.SchoolSettingsView.as_view(), name="settings"),
]
