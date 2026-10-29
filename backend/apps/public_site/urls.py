"""
apps/public_site/urls.py
-------------------------
Public-facing pages — no authentication required.
"""

from django.urls import path

from .views import (
    AboutPageView,
    AcademicsPageView,
    ContactUsPageView,
    GalleryPageView,
    HomePageView,
)

urlpatterns = [
    path("", HomePageView.as_view(), name="home"),
    path("about/", AboutPageView.as_view(), name="about"),
    path("page/academics/", AcademicsPageView.as_view(), name="academics"),
    path("gallery/", GalleryPageView.as_view(), name="gallery"),
    path("contact-us/", ContactUsPageView.as_view(), name="contact_us"),
]
