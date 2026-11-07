"""apps/students/urls.py"""

from django.urls import path

from . import views

app_name = "students"

urlpatterns = [
    path("", views.StudentListView.as_view(), name="list"),
    path("add/", views.StudentCreateView.as_view(), name="add"),
    path("<int:pk>/", views.StudentDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.StudentUpdateView.as_view(), name="edit"),
    path(
        "<int:pk>/disable-login/",
        views.StudentLoginDisableView.as_view(),
        name="disable_login",
    ),
    path(
        "<int:pk>/enable-login/",
        views.StudentLoginEnableView.as_view(),
        name="enable_login",
    ),
    path("<int:pk>/delete/", views.StudentDeleteView.as_view(), name="delete"),
    path("<int:pk>/pdf/", views.StudentPDFView.as_view(), name="pdf"),
    path("admission-form/", views.StudentBlankPDFView.as_view(), name="blank_pdf"),
    path("import/", views.BulkImportView.as_view(), name="bulk_import"),
    path(
        "import/template/",
        views.BulkImportTemplateView.as_view(),
        name="bulk_import_template",
    ),
    path(
        "import/<int:job_pk>/",
        views.BulkImportPreviewView.as_view(),
        name="bulk_import_preview",
    ),
    path(
        "import/<int:job_pk>/errors/",
        views.BulkImportErrorReportView.as_view(),
        name="bulk_import_errors",
    ),
    path("promote/", views.PromotionView.as_view(), name="promote"),
    path(
        "promote/history/",
        views.PromotionBatchListView.as_view(),
        name="promotion_history",
    ),
]
