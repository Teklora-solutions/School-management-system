"""
apps/core/urls_activity.py
--------------------------
Admin-facing activity pages (live feed and daily report).
Mounted at /activity/. Uses its own namespace because ``core`` is taken by the
audit viewer in urls_audit.py and ``core_school`` by urls_school.py — two
includes sharing one namespace make reverse() ambiguous (Django's urls.W005).
"""

from django.urls import path

from . import views

app_name = "core_activity"

urlpatterns = [
    path("feed/", views.ActivityFeedView.as_view(), name="feed"),
    path("daily/", views.DailyReportView.as_view(), name="daily_report"),
]
