"""
Private storage for knowledge documents.

Files live under ``settings.KNOWLEDGE_MEDIA_ROOT`` — deliberately outside
``MEDIA_ROOT`` — so the development ``/media/`` route can never serve them.
Every read goes through the permission-checked download view in
``apps.assistant.knowledge.views``; ``url()`` raises on purpose so a template
can never print a direct storage link.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


def knowledge_upload_path(instance, filename: str) -> str:
    """Random file name under ``knowledge/`` so the original name is never a path."""
    suffix = Path(filename).suffix.lower()
    return f"knowledge/{uuid.uuid4().hex}{suffix}"


@deconstructible
class KnowledgeStorage(FileSystemStorage):
    """Filesystem storage whose location follows ``KNOWLEDGE_MEDIA_ROOT``."""

    def __init__(self):
        super().__init__(location=None, base_url=None)

    @property
    def base_location(self):
        return settings.KNOWLEDGE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        raise ValueError(
            "Knowledge documents are private: serve them through the "
            "permission-checked download view."
        )

    def deconstruct(self):
        # No absolute path is baked into migrations; the location is read from
        # settings every time it is needed.
        return ("apps.assistant.knowledge.storage.KnowledgeStorage", [], {})


knowledge_storage = KnowledgeStorage()
