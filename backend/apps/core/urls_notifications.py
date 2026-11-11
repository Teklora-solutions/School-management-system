"""
apps/core/urls_notifications.py
-------------------------------
In-app notifications for every signed-in role. Mounted at /notifications/.
Its own namespace keeps reverse() unambiguous next to ``core``, ``core_school``
and ``core_activity`` (two includes sharing one namespace raise urls.W005).
"""

from django.urls import path

from . import views

app_name = "core_notifications"

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="list"),
    path("<int:pk>/open/", views.NotificationOpenView.as_view(), name="open"),
    path(
        "read-all/",
        views.NotificationMarkAllReadView.as_view(),
        name="mark_all_read",
    ),
]
