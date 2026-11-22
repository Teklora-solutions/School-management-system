"""apps/assistant/urls.py — mounted at /assistant/."""

from django.urls import path

from . import chat_views, views
from .knowledge import views as knowledge_views
from .handoff import views as handoff_views
from . import signed_in_views

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
    path("guide/",    views.AssistantGuideView.as_view(),    name="guide"),
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

    # ── CP4 — Handoff rules and teams ────────────────────────────────────────
    path(
        "handoff/rules/",
        handoff_views.HandoffRuleListView.as_view(),
        name="handoff_rules",
    ),
    path(
        "handoff/rules/new/",
        handoff_views.HandoffRuleCreateView.as_view(),
        name="handoff_rule_create",
    ),
    path(
        "handoff/rules/<int:pk>/edit/",
        handoff_views.HandoffRuleUpdateView.as_view(),
        name="handoff_rule_update",
    ),
    path(
        "handoff/rules/<int:pk>/delete/",
        handoff_views.HandoffRuleDeleteView.as_view(),
        name="handoff_rule_delete",
    ),
    path(
        "handoff/rules/<int:pk>/toggle/",
        handoff_views.HandoffRuleToggleView.as_view(),
        name="handoff_rule_toggle",
    ),
    path(
        "handoff/rules/seed/",
        handoff_views.HandoffSeedView.as_view(),
        name="handoff_seed",
    ),
    path(
        "handoff/teams/",
        handoff_views.HandoffTeamListView.as_view(),
        name="handoff_teams",
    ),
    path(
        "handoff/teams/new/",
        handoff_views.HandoffTeamCreateView.as_view(),
        name="handoff_team_create",
    ),
    path(
        "handoff/teams/<int:pk>/edit/",
        handoff_views.HandoffTeamUpdateView.as_view(),
        name="handoff_team_update",
    ),
    path(
        "handoff/canned/",
        handoff_views.CannedReplyListView.as_view(),
        name="canned_replies",
    ),
    path(
        "handoff/canned/new/",
        handoff_views.CannedReplyCreateView.as_view(),
        name="canned_reply_create",
    ),
    path(
        "handoff/canned/<int:pk>/edit/",
        handoff_views.CannedReplyUpdateView.as_view(),
        name="canned_reply_update",
    ),
    path(
        "handoff/canned/<int:pk>/delete/",
        handoff_views.CannedReplyDeleteView.as_view(),
        name="canned_reply_delete",
    ),

    # ── CP5 — Human inbox ────────────────────────────────────────────────────
    path(
        "inbox/",
        handoff_views.InboxListView.as_view(),
        name="inbox",
    ),
    path(
        "inbox/<int:pk>/",
        handoff_views.InboxDetailView.as_view(),
        name="inbox_detail",
    ),

    # ── CP6 — Conversation management ────────────────────────────────────────
    path(
        "conversations/<int:pk>/export/",
        handoff_views.ConversationExportView.as_view(),
        name="conversation_export",
    ),
    path(
        "conversations/<int:pk>/delete/",
        handoff_views.ConversationDeleteView.as_view(),
        name="conversation_delete",
    ),

    # ── Module 4 — Signed-in assistant ───────────────────────────────────────
    path("me/state/",   signed_in_views.SignedInStateView.as_view(),              name="me_state"),
    path("me/page/",    signed_in_views.SignedInChatPageView.as_view(),           name="me_page"),
    path("me/send/",    signed_in_views.SignedInSendView.as_view(),               name="me_send"),
    path("me/new/",     signed_in_views.SignedInNewView.as_view(),                name="me_new"),
    path("me/history/", signed_in_views.SignedInHistoryView.as_view(),            name="me_history"),
    path("me/child/",   signed_in_views.SignedInChildSwitchView.as_view(),        name="me_child"),
    path("me/memory/",  signed_in_views.SignedInMemoryView.as_view(),             name="me_memory"),
    path("me/delete/",  signed_in_views.SignedInDeleteConversationView.as_view(), name="me_delete"),
    path("me/draft-announcement/", signed_in_views.SignedInDraftAnnouncementView.as_view(), name="me_draft_announcement"),
]
