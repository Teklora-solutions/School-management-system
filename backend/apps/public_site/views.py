"""
apps/public_site/views.py
--------------------------
Public-facing pages for the school website.
No authentication required — accessible to everyone.
"""

from __future__ import annotations

from django.conf import settings
from django.shortcuts import render
from django.views.generic import TemplateView


class PublicBaseView(TemplateView):
    """Adds school settings to context for every public page."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["SCHOOL_NAME"] = getattr(settings, "SCHOOL_NAME", "My School")
        ctx["SCHOOL_MOTTO"] = getattr(settings, "SCHOOL_MOTTO", "")
        ctx["SCHOOL_PHONE"] = getattr(settings, "SCHOOL_PHONE", "")
        ctx["SCHOOL_EMAIL"] = getattr(settings, "SCHOOL_EMAIL", "")
        ctx["SCHOOL_CURRENCY"] = getattr(settings, "SCHOOL_CURRENCY", "GHS")
        return ctx


class HomePageView(PublicBaseView):
    template_name = "public/home.html"


class AboutPageView(PublicBaseView):
    template_name = "public/about.html"


class AcademicsPageView(PublicBaseView):
    template_name = "public/academics.html"


class GalleryPageView(PublicBaseView):
    template_name = "public/gallery.html"


class ContactUsPageView(PublicBaseView):
    template_name = "public/contact_us.html"
