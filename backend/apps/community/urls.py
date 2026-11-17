"""
apps/community/urls.py
"""

from django.urls import path

from . import views

app_name = "community"

urlpatterns = [
    # ── Public / M1 ──────────────────────────────────────────────────────
    path("", views.CommunityHomeView.as_view(), name="home"),
    path("paused/", views.CommunityPausedView.as_view(), name="paused"),
    path("events/", views.CommunityEventsView.as_view(), name="events"),
    path("members/", views.CommunityMembersView.as_view(), name="members"),
    path("about/", views.CommunityAboutView.as_view(), name="about"),
    path("search/", views.CommunitySearchView.as_view(), name="search"),
    path("c/<slug:category_slug>/", views.CategoryFeedView.as_view(), name="category_feed"),
    path("post/<int:pk>/", views.PostDetailView.as_view(), name="post_detail"),

    # ── Participation / M2 ────────────────────────────────────────────────
    path("consent/", views.ConsentView.as_view(), name="consent"),
    path("post/create/", views.PostCreateView.as_view(), name="post_create"),
    path("post/<int:pk>/edit/", views.PostEditView.as_view(), name="post_edit"),
    path("post/<int:pk>/remove/", views.PostRemoveView.as_view(), name="post_remove"),
    path("post/<int:post_pk>/reply/", views.CommentCreateView.as_view(), name="comment_create"),
    path("comment/<int:pk>/edit/", views.CommentEditView.as_view(), name="comment_edit"),
    path("comment/<int:pk>/remove/", views.CommentRemoveView.as_view(), name="comment_remove"),
    path("post/<int:post_pk>/react/", views.ReactionToggleView.as_view(), name="reaction_toggle"),
    path("draft/save/", views.DraftSaveView.as_view(), name="draft_save"),
    path("draft/clear/", views.DraftClearView.as_view(), name="draft_clear"),
    path("my-posts/", views.MyPostsView.as_view(), name="my_posts"),
    path("report/", views.ReportView.as_view(), name="report"),
    path("warning/<int:pk>/", views.WarningDetailView.as_view(), name="warning_detail"),
    path("warning/<int:warning_pk>/appeal/", views.AppealView.as_view(), name="appeal"),
    path("dashboard-card/", views.CommunityCardView.as_view(), name="dashboard_card"),

    # ── Moderation / M3 ──────────────────────────────────────────────────
    path("admin/queue/", views.ModQueueView.as_view(), name="admin_queue"),
    path("admin/post/<int:pk>/approve/", views.PostApproveView.as_view(), name="admin_post_approve"),
    path("admin/post/<int:pk>/restore/", views.PostRestoreView.as_view(), name="admin_post_restore"),
    path("admin/post/<int:pk>/pin/", views.PostPinView.as_view(), name="admin_post_pin"),
    path("admin/post/<int:pk>/unpin/", views.PostUnpinView.as_view(), name="admin_post_unpin"),
    path("admin/post/<int:pk>/lock/", views.PostLockView.as_view(), name="admin_post_lock"),
    path("admin/post/<int:pk>/move/", views.PostMoveView.as_view(), name="admin_post_move"),
    path("admin/warn/", views.IssueWarningView.as_view(), name="admin_warn"),
    path("admin/warning/<int:pk>/lift/", views.LiftWarningView.as_view(), name="admin_warning_lift"),
    path("admin/appeal/<int:pk>/", views.AdminAppealView.as_view(), name="admin_appeal"),
    path("admin/report/<int:pk>/resolve/", views.ResolveReportView.as_view(), name="admin_report_resolve"),
    path("admin/mute/", views.MuteUserView.as_view(), name="admin_mute"),
    path("admin/unmute/", views.UnmuteUserView.as_view(), name="admin_unmute"),
    path("admin/suspend/", views.SuspendUserView.as_view(), name="admin_suspend"),
    path("admin/unsuspend/", views.UnsuspendUserView.as_view(), name="admin_unsuspend"),
    path("admin/words/", views.WordFilterView.as_view(), name="admin_word_filter"),
    path("admin/delegation/", views.DelegationView.as_view(), name="admin_delegation"),
    path("admin/insights/", views.InsightsView.as_view(), name="admin_insights"),
    path("admin/categories/", views.CategoryAdminListView.as_view(), name="admin_categories"),
    path("admin/categories/new/", views.CategoryAdminEditView.as_view(), name="admin_category_create"),
    path("admin/categories/<int:pk>/edit/", views.CategoryAdminEditView.as_view(), name="admin_category_edit"),
    path("admin/user/<int:user_pk>/anonymise/", views.AnonymiseUserView.as_view(), name="admin_anonymise"),
    path("admin/user/<int:user_pk>/export/", views.ExportUserContentView.as_view(), name="admin_export"),
]
