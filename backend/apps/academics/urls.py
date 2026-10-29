"""
apps/academics/urls.py
"""

from django.urls import path

from . import views

app_name = "academics"

urlpatterns = [
    path("", views.YearListView.as_view(), name="year_list"),
    path("years/add/", views.YearCreateView.as_view(), name="year_create"),
    path("years/<int:pk>/", views.YearDetailView.as_view(), name="year_detail"),
    path(
        "years/<int:pk>/set-current/",
        views.YearSetCurrentView.as_view(),
        name="year_set_current",
    ),
    path("years/<int:pk>/delete/", views.YearDeleteView.as_view(), name="year_delete"),
    path("terms/add/", views.TermCreateView.as_view(), name="term_create"),
    path("terms/<int:pk>/edit/", views.TermUpdateView.as_view(), name="term_update"),
    path(
        "terms/<int:pk>/set-current/",
        views.TermSetCurrentView.as_view(),
        name="term_set_current",
    ),
    path("classes/", views.ClassListView.as_view(), name="class_list"),
    path("classes/add/", views.ClassCreateView.as_view(), name="class_create"),
    path("classes/<int:pk>/", views.ClassDetailView.as_view(), name="class_detail"),
    path("streams/add/", views.StreamCreateView.as_view(), name="stream_create"),
    path("subjects/", views.SubjectListView.as_view(), name="subject_list"),
    path("subjects/add/", views.SubjectCreateView.as_view(), name="subject_create"),
    path(
        "class-subjects/",
        views.ClassSubjectListView.as_view(),
        name="class_subject_list",
    ),
    path(
        "class-subjects/add/",
        views.ClassSubjectCreateView.as_view(),
        name="class_subject_create",
    ),
    path(
        "class-subjects/<int:pk>/remove/",
        views.ClassSubjectRemoveView.as_view(),
        name="class_subject_remove",
    ),
]
