"""
apps/public/admin.py
--------------------
Django admin registration for the two website tables.

This is the existing staff surface at ``/django-admin/``, not a second admin:
a school's own photography and inbound enquiries need somewhere to be managed
from day one, and an in-app gallery screen is a separate decision for a later
checkpoint. ``ContactMessage`` is read-only because the row has no workflow
fields to edit — deleting one is the only action, and it is a privacy request
or a spam cleanup, both of which are admin-only anyway.
"""

from __future__ import annotations

from django.contrib import admin

from .models import ContactMessage, GalleryPhoto


@admin.register(GalleryPhoto)
class GalleryPhotoAdmin(admin.ModelAdmin):
    list_display = ("title", "display_order", "is_published", "created_at")
    list_editable = ("display_order", "is_published")
    search_fields = ("title", "caption")
    ordering = ("display_order", "-created_at")
    list_per_page = 50


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "created_at")
    search_fields = ("name", "email", "message")
    readonly_fields = (
        "name",
        "email",
        "phone",
        "message",
        "created_at",
        "updated_at",
    )
    list_per_page = 50
