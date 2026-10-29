"""apps/results/urls.py"""

from django.urls import path

from . import views

app_name = "results"

urlpatterns = [
    # Student-facing (published results only)
    path("my-results/", views.StudentResultsView.as_view(), name="student_results"),
    path(
        "report-card/<int:student_id>/<int:term_id>/pdf/",
        views.ReportCardPdfView.as_view(),
        name="report_card_pdf",
    ),
    # ── Admin governance (Module 1) ─────────────────────────────────────────
    path(
        "broadsheet/", views.ClassBroadsheetView.as_view(), name="broadsheet"
    ),
    path("sheets/", views.ResultSheetListView.as_view(), name="sheet_list"),
    path(
        "sheets/<int:pk>/", views.ResultSheetDetailView.as_view(), name="sheet_detail"
    ),
    path(
        "sheets/<int:pk>/confirm/",
        views.ResultSheetConfirmView.as_view(),
        name="sheet_confirm",
    ),
    path(
        "sheets/<int:pk>/publish/",
        views.ResultSheetPublishView.as_view(),
        name="sheet_publish",
    ),
    path(
        "sheets/<int:pk>/reopen/",
        views.ResultSheetReopenView.as_view(),
        name="sheet_reopen",
    ),
    path(
        "publish/class-term/",
        views.PublishClassTermView.as_view(),
        name="publish_class_term",
    ),
    # ── Grading configuration (Module 1) ────────────────────────────────────
    path("grading/", views.GradingConfigListView.as_view(), name="grading_list"),
    path(
        "grading/<int:year_id>/",
        views.GradingConfigEditView.as_view(),
        name="grading_config",
    ),
    path(
        "grading/<int:year_id>/scale/",
        views.GradeScaleEditView.as_view(),
        name="grade_scale",
    ),
    path(
        "grading/<int:year_id>/scale/delete/",
        views.GradeScaleDeleteView.as_view(),
        name="grade_scale_delete",
    ),
]
