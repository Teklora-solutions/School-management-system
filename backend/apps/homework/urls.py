"""apps/homework/urls.py"""

from django.urls import path

from . import teacher_views, views

app_name = "homework"

urlpatterns = [
    path("", views.HomeworkListView.as_view(), name="list"),
    path("marked/", views.MyMarkedWorkView.as_view(), name="marked_work"),
    path("teaching/", teacher_views.TeacherHomeworkListView.as_view(), name="teacher_list"),
    path("teaching/new/", teacher_views.TeacherHomeworkCreateView.as_view(), name="teacher_create"),
    path("teaching/<int:pk>/edit/", teacher_views.TeacherHomeworkEditView.as_view(), name="teacher_edit"),
    path("teaching/<int:pk>/delete/", teacher_views.TeacherHomeworkDeleteView.as_view(), name="teacher_delete"),
    path("teaching/<int:pk>/publish/", teacher_views.TeacherHomeworkPublishView.as_view(), name="teacher_publish"),
    path("teaching/<int:pk>/preview/", teacher_views.TeacherHomeworkPreviewView.as_view(), name="teacher_preview"),
    path("<int:pk>/", views.HomeworkDetailView.as_view(), name="detail"),
    path("<int:pk>/submit/", views.HomeworkSubmitView.as_view(), name="submit"),
    path(
        "<int:pk>/submissions/<int:submission_id>/files/<int:attachment_id>/",
        views.HomeworkSubmissionFileView.as_view(),
        name="submission_file",
    ),
    path(
        "teaching/<int:pk>/submissions/",
        teacher_views.TeacherHomeworkSubmissionsView.as_view(),
        name="teacher_submissions",
    ),
    path(
        "teaching/<int:pk>/mark/<int:student_id>/",
        teacher_views.TeacherHomeworkMarkView.as_view(),
        name="teacher_mark",
    ),
    path(
        "teaching/<int:pk>/mark/<int:student_id>/autosave/",
        teacher_views.TeacherHomeworkMarkAutosaveView.as_view(),
        name="teacher_mark_autosave",
    ),
    path(
        "teaching/<int:pk>/release/",
        teacher_views.TeacherHomeworkReleaseView.as_view(),
        name="teacher_release",
    ),
    path(
        "<int:pk>/files/<int:attachment_id>/",
        views.HomeworkAttachmentFileView.as_view(),
        name="attachment_file",
    ),
    path(
        "<int:pk>/download/",
        views.HomeworkAttachmentDownloadView.as_view(),
        name="attachment_download",
    ),
]
