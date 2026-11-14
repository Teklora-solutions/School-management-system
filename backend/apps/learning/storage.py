"""
Private storage for class-feed photos and files.

Files live under ``settings.FEED_MEDIA_ROOT`` — deliberately outside
``MEDIA_ROOT`` — so the development ``/media/`` route can never serve them.
Every read goes through a permission-checked view; ``url()`` raises on
purpose so a template can never print a direct storage link.
"""

from __future__ import annotations

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class FeedMediaStorage(FileSystemStorage):
    """Filesystem storage whose location follows ``FEED_MEDIA_ROOT`` at runtime."""

    def __init__(self):
        super().__init__(location=None, base_url=None)

    @property
    def base_location(self):
        return settings.FEED_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        raise ValueError(
            "Feed media is private: serve it through the permission-checked view."
        )

    def deconstruct(self):
        # No absolute path is baked into migrations; the location is read from
        # settings every time it is needed.
        return ("apps.learning.storage.FeedMediaStorage", [], {})


feed_media_storage = FeedMediaStorage()
