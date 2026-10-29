"""
apps/public/tests/fixtures.py
-----------------------------
Shared builders for the ``public`` app's test modules: the two views of the
same gallery table (a visitor's page and the school's own management pages)
need the same photographs, and a real ``ImageField`` needs real bytes.
"""

from __future__ import annotations

import base64
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile

from ..models import GalleryPhoto

#: A 1x1 PNG, so the gallery tests exercise a real ``ImageField`` rather than a
#: filename that would fail the moment Pillow checks the bytes.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

#: Uploads land here instead of the project's ``MEDIA_ROOT``, so a test run
#: never writes into the folder the dev server serves.
MEDIA_TMP = tempfile.mkdtemp()


def upload(name: str = "photo.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")


def make_photo(**kwargs) -> GalleryPhoto:
    defaults = {"title": "Sports day", "image": upload(), "display_order": 0}
    defaults.update(kwargs)
    return GalleryPhoto.objects.create(**defaults)
