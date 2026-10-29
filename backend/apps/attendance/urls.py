"""apps/attendance/urls.py — stub routes, full implementation in Phase 2+"""

from django.urls import path

from .views import ComingSoonView

app_name = "attendance"

urlpatterns = [
    path("", ComingSoonView.as_view(), name="list"),
]
