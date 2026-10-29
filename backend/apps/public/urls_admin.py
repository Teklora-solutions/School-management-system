"""
apps/public/urls_admin.py
-------------------------
The Website section of the Admin Portal, mounted at ``/website/``.

A second URL module for this app, exactly as ``apps/core`` does with
``urls_school.py`` and ``urls_activity.py``: the five public pages are already
mounted at the site root under the ``public`` namespace, and management pages
belong behind their own prefix rather than competing with ``/gallery/`` for a
visitor's URL.
"""

from django.urls import path

from . import views_admin

app_name = "public_admin"

urlpatterns = [
    path("gallery/", views_admin.GalleryListView.as_view(), name="gallery_list"),
    path("gallery/add/", views_admin.GalleryPhotoCreateView.as_view(), name="gallery_add"),
    path(
        "gallery/<int:pk>/edit/",
        views_admin.GalleryPhotoEditView.as_view(),
        name="gallery_edit",
    ),
    path(
        "gallery/<int:pk>/publish/",
        views_admin.GalleryPhotoPublishView.as_view(),
        name="gallery_publish",
    ),
    path(
        "gallery/<int:pk>/delete/",
        views_admin.GalleryPhotoDeleteView.as_view(),
        name="gallery_delete",
    ),
    path("enquiries/", views_admin.EnquiryListView.as_view(), name="enquiry_list"),
    path(
        "enquiries/<int:pk>/",
        views_admin.EnquiryDetailView.as_view(),
        name="enquiry_detail",
    ),
    path(
        "enquiries/<int:pk>/handled/",
        views_admin.EnquiryHandledView.as_view(),
        name="enquiry_handled",
    ),
    path(
        "enquiries/<int:pk>/delete/",
        views_admin.EnquiryDeleteView.as_view(),
        name="enquiry_delete",
    ),
]
