"""apps/gradebook/urls.py"""

from django.urls import path

from . import (
    admin_views,
    director_views,
    export_views,
    family_views,
    release_views,
    setup_views,
    views,
)

app_name = "gradebook"

urlpatterns = [
    path("", views.TeacherGradebookListView.as_view(), name="teacher_list"),
    path("locks/", admin_views.AdminGradebookLockView.as_view(), name="admin_locks"),
    path(
        "my/",
        family_views.MyGradebookView.as_view(),
        name="my_gradebook",
    ),
    path(
        "director/",
        director_views.DirectorGradebookView.as_view(),
        name="director_overview",
    ),
    path(
        "director/export/<str:fmt>/",
        director_views.DirectorGradebookExportView.as_view(),
        name="director_export",
    ),
    path(
        "class/<int:class_id>/subject/<int:subject_id>/",
        views.TeacherGradebookView.as_view(),
        name="teacher_grid",
    ),
    path(
        "setup/<int:setup_id>/",
        setup_views.TeacherGradebookSetupView.as_view(),
        name="teacher_setup",
    ),
    path(
        "setup/<int:setup_id>/release/",
        release_views.TeacherGradebookReleaseView.as_view(),
        name="teacher_release",
    ),
    path(
        "setup/<int:setup_id>/report-card/",
        release_views.TeacherGradebookReportCardView.as_view(),
        name="send_to_report_card",
    ),
    path(
        "setup/<int:setup_id>/export/<str:fmt>/",
        export_views.TeacherGradebookExportView.as_view(),
        name="teacher_export",
    ),
    path(
        "setup/<int:setup_id>/manual/new/",
        setup_views.TeacherManualItemView.as_view(),
        name="manual_item_create",
    ),
    path(
        "setup/<int:setup_id>/manual/<int:item_id>/",
        setup_views.TeacherManualItemView.as_view(),
        name="manual_item_edit",
    ),
    path(
        "setup/<int:setup_id>/manual/<int:item_id>/remove/",
        setup_views.TeacherManualItemRemoveView.as_view(),
        name="manual_item_remove",
    ),
    path(
        "setup/<int:setup_id>/excuse/<int:excuse_id>/revoke/",
        setup_views.TeacherExcuseRevokeView.as_view(),
        name="excuse_revoke",
    ),
]
