"""apps/teachers/urls.py"""

from django.urls import path

from . import views

app_name = "teachers"

urlpatterns = [
    path("", views.TeacherListView.as_view(), name="list"),
    path("<int:pk>/", views.TeacherDetailView.as_view(), name="detail"),
    path("my-classes/", views.MyClassesView.as_view(), name="my_classes"),
    path("marks/", views.MarksEntryListView.as_view(), name="marks_list"),
    path(
        "marks/<int:assignment_id>/",
        views.MarksEntryView.as_view(),
        name="marks_entry",
    ),
    path(
        "assignments/",
        views.TeacherAssignmentListView.as_view(),
        name="assignment_list",
    ),
    path(
        "assignments/add/",
        views.TeacherAssignmentCreateView.as_view(),
        name="assignment_create",
    ),
    path(
        "assignments/<int:pk>/delete/",
        views.TeacherAssignmentDeleteView.as_view(),
        name="assignment_delete",
    ),
    path("matrix/", views.AssignmentMatrixView.as_view(), name="assignment_matrix"),
    path(
        "distribution/",
        views.SubjectDistributionView.as_view(),
        name="subject_distribution",
    ),
    path("workload/", views.TeacherWorkloadView.as_view(), name="workload"),
    path(
        "classes/<int:pk>/set-teacher/",
        views.SetClassTeacherView.as_view(),
        name="set_class_teacher",
    ),
]
