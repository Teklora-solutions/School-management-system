"""apps/communications/urls.py"""

from django.urls import path

from . import views

app_name = "communications"

urlpatterns = [
    # Announcements
    path("", views.AnnouncementListView.as_view(), name="list"),
    path(
        "announcements/create/",
        views.AnnouncementCreateView.as_view(),
        name="announcement_create",
    ),
    path(
        "announcements/<int:pk>/read/",
        views.AnnouncementMarkReadView.as_view(),
        name="announcement_read",
    ),
    path(
        "announcements/read-all/",
        views.AnnouncementMarkAllReadView.as_view(),
        name="announcement_read_all",
    ),
    # Student/parent messages
    path("messages/new/", views.MessageCreateView.as_view(), name="complaint_create"),
    path("messages/", views.MyMessagesView.as_view(), name="my_messages"),
    path("messages/<int:pk>/", views.ThreadDetailView.as_view(), name="thread_detail"),
    # Admin inbox
    path("inbox/", views.InboxView.as_view(), name="inbox"),
    path("inbox/<int:pk>/", views.InboxThreadView.as_view(), name="inbox_thread"),
]
