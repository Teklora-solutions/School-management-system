"""
apps/assistant/urls.py
----------------------
Mounted at /assistant/. Admin-only in this build.
"""

from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("settings/", views.AssistantSettingsView.as_view(), name="settings"),
    path("providers/", views.ProviderListView.as_view(), name="providers"),
    path("providers/new/", views.ProviderCreateView.as_view(), name="provider_create"),
    path(
        "providers/<int:pk>/edit/",
        views.ProviderUpdateView.as_view(),
        name="provider_update",
    ),
    path(
        "providers/<int:pk>/test/",
        views.ProviderTestView.as_view(),
        name="provider_test",
    ),
]
