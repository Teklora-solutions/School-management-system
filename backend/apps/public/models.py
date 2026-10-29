"""
apps/public/models.py
---------------------
The two tables a school website needs and nothing else.

Everything the pages show otherwise comes from tables the school already
fills (``Student``, ``SchoolClass``, ``Term``, ``Subject``, ``SchoolSettings``).
A third table for "news" or "events" would be a second place to maintain what
``communications`` and ``academics`` already own, so it is not here.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import BaseModel


class GalleryPhoto(BaseModel):
    """One photograph on the public gallery, published or held back."""

    title = models.CharField(max_length=200)
    caption = models.CharField(
        max_length=300,
        blank=True,
        help_text="One line shown under the photo. Leave blank for no caption.",
    )
    image = models.ImageField(upload_to="gallery/")
    display_order = models.PositiveSmallIntegerField(
        default=0,
        db_index=True,
        help_text="Lowest first. Photos sharing an order fall back to newest first.",
    )
    is_published = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "public_gallery_photo"
        ordering = ["display_order", "-created_at"]
        verbose_name = "gallery photo"
        verbose_name_plural = "gallery photos"

    def __str__(self) -> str:
        return self.title


class ContactMessage(BaseModel):
    """
    An enquiry left on the public Contact page.

    ``handled`` is the office's own mark, not a claim about the sender: nothing
    here records *what* was done or who did it, and the row is still the whole
    record of the enquiry. The flag exists so the enquiry list can be filtered
    down to the ones nobody has answered yet; who set it is in the audit log.
    """

    name = models.CharField(max_length=200)
    email = models.EmailField(max_length=254)
    phone = models.CharField(max_length=32, blank=True)
    message = models.TextField(max_length=2000)
    handled = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Tick once the office has replied. Unticked rows are the open ones.",
    )
    handled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set automatically when the mark is ticked, cleared when it is unticked.",
    )

    class Meta:
        db_table = "public_contact_message"
        ordering = ["-created_at"]
        verbose_name = "website enquiry"
        verbose_name_plural = "website enquiries"

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"
