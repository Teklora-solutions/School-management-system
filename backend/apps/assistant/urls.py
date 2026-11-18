"""apps/assistant/urls.py — mounted at /assistant/."""

from django.urls import path

from . import chat_views, views
from .knowledge import views as knowledge_views

app_name = "assistant"

urlpatterns = [
    # Public chat widget endpoints (visitor-facing, no login, all switched OFF
    # by default). Server-side rules live in chat.py.
    path("chat/state/", chat_views.WidgetStateView.as_view(), name="chat_state"),
    path("chat/send/", chat_views.ChatSendView.as_view(), name="chat_send"),
    path("chat/new/", chat_views.ChatNewView.as_view(), name="chat_new"),
    path("chat/feedback/", chat_views.ChatFeedbackView.as_view(), name="chat_feedback"),
    path("chat/callback/", chat_views.ChatCallbackView.as_view(), name="chat_callback"),
    path("settings/", views.AssistantSettingsView.as_view(), name="settings"),
    path("providers/", views.ProviderListView.as_view(), name="providers"),
    path("providers/new/", views.ProviderCreateView.as_view(), name="provider_create"),
    path("providers/<int:pk>/edit/", views.ProviderUpdateView.as_view(), name="provider_update"),
    path("providers/<int:pk>/delete/", views.ProviderDeleteView.as_view(), name="provider_delete"),
    path("providers/<int:pk>/test/", views.ProviderTestView.as_view(), name="provider_test"),
    path("guardrails/", views.GuardrailListView.as_view(), name="guardrails"),
    path("guardrails/new/", views.GuardrailCreateView.as_view(), name="guardrail_create"),
    path("guardrails/<int:pk>/toggle/", views.GuardrailToggleView.as_view(), name="guardrail_toggle"),
    path("alerts/", views.AlertListView.as_view(), name="alerts"),
    path("alerts/<int:pk>/acknowledge/", views.AlertAcknowledgeView.as_view(), name="alert_acknowledge"),
    path("alerts/<int:pk>/close/", views.AlertCloseView.as_view(), name="alert_close"),
    path("console/", views.TestConsoleView.as_view(), name="console"),
    path("jobs/", views.JobFailureListView.as_view(), name="jobs"),
    path("jobs/<int:pk>/resolve/", views.JobFailureResolveView.as_view(), name="job_resolve"),
    # Knowledge base (Module 2)
    path(
        "knowledge/collections/",
        knowledge_views.CollectionListView.as_view(),
        name="knowledge_collections",
    ),
    path(
        "knowledge/collections/new/",
        knowledge_views.CollectionCreateView.as_view(),
        name="knowledge_collection_create",
    ),
    path(
        "knowledge/collections/<int:pk>/edit/",
        knowledge_views.CollectionUpdateView.as_view(),
        name="knowledge_collection_update",
    ),
    path(
        "knowledge/documents/",
        knowledge_views.DocumentListView.as_view(),
        name="knowledge_documents",
    ),
    path(
        "knowledge/documents/new/",
        knowledge_views.DocumentNewView.as_view(),
        name="knowledge_document_new",
    ),
    path(
        "knowledge/documents/new/upload/",
        knowledge_views.DocumentUploadView.as_view(),
        name="knowledge_document_new_upload",
    ),
    path(
        "knowledge/documents/new/url/",
        knowledge_views.DocumentUrlView.as_view(),
        name="knowledge_document_new_url",
    ),
    path(
        "knowledge/documents/new/paste/",
        knowledge_views.DocumentPasteView.as_view(),
        name="knowledge_document_new_paste",
    ),
    path(
        "knowledge/documents/new/faq/",
        knowledge_views.DocumentFAQView.as_view(),
        name="knowledge_document_new_faq",
    ),
    path(
        "knowledge/documents/<int:pk>/",
        knowledge_views.DocumentDetailView.as_view(),
        name="knowledge_document",
    ),
    path(
        "knowledge/documents/<int:pk>/edit/",
        knowledge_views.DocumentEditView.as_view(),
        name="knowledge_document_edit",
    ),
    path(
        "knowledge/documents/<int:pk>/status/",
        knowledge_views.DocumentStatusView.as_view(),
        name="knowledge_document_status",
    ),
    path(
        "knowledge/documents/<int:pk>/download/",
        knowledge_views.DocumentDownloadView.as_view(),
        name="knowledge_document_download",
    ),
    path(
        "knowledge/documents/<int:pk>/reindex/",
        knowledge_views.DocumentReindexView.as_view(),
        name="knowledge_document_reindex",
    ),
    path(
        "knowledge/documents/<int:pk>/confirm-personal-data/",
        knowledge_views.DocumentConfirmPIIView.as_view(),
        name="knowledge_document_confirm_pii",
    ),
    path(
        "knowledge/documents/<int:pk>/check/",
        knowledge_views.DocumentCheckView.as_view(),
        name="knowledge_document_check",
    ),
    path(
        "knowledge/ask/",
        knowledge_views.AnswerDeskView.as_view(),
        name="knowledge_answer_desk",
    ),
    path(
        "knowledge/gaps/",
        knowledge_views.GapListView.as_view(),
        name="knowledge_gaps",
    ),
    path(
        "knowledge/gaps/<int:pk>/resolve/",
        knowledge_views.GapResolveView.as_view(),
        name="knowledge_gap_resolve",
    ),
    path(
        "knowledge/gaps/<int:pk>/delete/",
        knowledge_views.GapDeleteView.as_view(),
        name="knowledge_gap_delete",
    ),
]
