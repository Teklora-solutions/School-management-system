"""
apps/core/validators.py
------------------------
Reusable file upload validators.

Usage in a model or form field::

    avatar = models.ImageField(
        validators=[validate_image_upload],
    )

    attachment = models.FileField(
        validators=[validate_document_upload],
    )
"""

from __future__ import annotations

import os

from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum upload sizes
MAX_AVATAR_SIZE_MB = 2
MAX_DOCUMENT_SIZE_MB = 10
MAX_IMPORT_SIZE_MB = 5
MAX_HOMEWORK_SIZE_MB = 10

# Allowed MIME types per category
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_DOCUMENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",  # .xls
    "text/csv",
    "application/csv",
}
ALLOWED_IMPORT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "text/csv",
    "application/csv",
}
ALLOWED_HOMEWORK_TYPES = ALLOWED_IMAGE_TYPES | {
    "application/pdf",
    "application/msword",  # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
}

# Safe extensions (second-line defence after MIME check)
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv"}
ALLOWED_IMPORT_EXTENSIONS = {".xlsx", ".csv"}
ALLOWED_HOMEWORK_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | {".pdf", ".doc", ".docx"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_mime_type(file) -> str:
    """
    Return the MIME type of an uploaded file.
    Uses `python-magic` if available, falls back to content_type attribute.
    Never raises — returns empty string on failure.
    """
    try:
        import magic

        header = file.read(2048)
        file.seek(0)
        return magic.from_buffer(header, mime=True)
    except ImportError:
        pass
    return getattr(file, "content_type", "") or ""


def _size_mb(file) -> float:
    file.seek(0, 2)  # seek to end
    size = file.tell()
    file.seek(0)  # rewind
    return size / (1024 * 1024)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


@deconstructible
class FileSizeValidator:
    """Reject files larger than `max_mb` megabytes."""

    def __init__(self, max_mb: float):
        self.max_mb = max_mb

    def __call__(self, value):
        size_mb = _size_mb(value)
        if size_mb > self.max_mb:
            raise ValidationError(
                f"File size {size_mb:.1f} MB exceeds the {self.max_mb} MB limit."
            )

    def __eq__(self, other):
        return isinstance(other, FileSizeValidator) and self.max_mb == other.max_mb


@deconstructible
class MimeTypeValidator:
    """Reject files whose detected MIME type is not in `allowed_types`."""

    def __init__(self, allowed_types: set[str], label: str = "file"):
        self.allowed_types = frozenset(allowed_types)
        self.label = label

    def __call__(self, value):
        detected = _get_mime_type(value)
        if detected and detected not in self.allowed_types:
            raise ValidationError(
                f"Unsupported {self.label} type '{detected}'. "
                f"Allowed: {', '.join(sorted(self.allowed_types))}."
            )

    def __eq__(self, other):
        return (
            isinstance(other, MimeTypeValidator)
            and self.allowed_types == other.allowed_types
        )


@deconstructible
class FileExtensionValidator:
    """Reject files whose extension is not in `allowed_extensions`."""

    def __init__(self, allowed_extensions: set[str], label: str = "file"):
        self.allowed_extensions = frozenset(e.lower() for e in allowed_extensions)
        self.label = label

    def __call__(self, value):
        name = getattr(value, "name", "") or ""
        ext = os.path.splitext(name)[1].lower()
        if ext and ext not in self.allowed_extensions:
            raise ValidationError(
                f"Unsupported {self.label} extension '{ext}'. "
                f"Allowed: {', '.join(sorted(self.allowed_extensions))}."
            )

    def __eq__(self, other):
        return (
            isinstance(other, FileExtensionValidator)
            and self.allowed_extensions == other.allowed_extensions
        )


# ---------------------------------------------------------------------------
# Convenience validator sets
# ---------------------------------------------------------------------------

validate_image_upload = [
    FileSizeValidator(MAX_AVATAR_SIZE_MB),
    MimeTypeValidator(ALLOWED_IMAGE_TYPES, label="image"),
    FileExtensionValidator(ALLOWED_IMAGE_EXTENSIONS, label="image"),
]

validate_document_upload = [
    FileSizeValidator(MAX_DOCUMENT_SIZE_MB),
    MimeTypeValidator(ALLOWED_DOCUMENT_TYPES, label="document"),
    FileExtensionValidator(ALLOWED_DOCUMENT_EXTENSIONS, label="document"),
]

validate_import_upload = [
    FileSizeValidator(MAX_IMPORT_SIZE_MB),
    MimeTypeValidator(ALLOWED_IMPORT_TYPES, label="import file"),
    FileExtensionValidator(ALLOWED_IMPORT_EXTENSIONS, label="import file"),
]

validate_homework_upload = [
    FileSizeValidator(MAX_HOMEWORK_SIZE_MB),
    MimeTypeValidator(ALLOWED_HOMEWORK_TYPES, label="homework file"),
    FileExtensionValidator(ALLOWED_HOMEWORK_EXTENSIONS, label="homework file"),
]

# A student's hand-in uses the same limits and types, under its own name.
validate_submission_upload = [
    FileSizeValidator(MAX_HOMEWORK_SIZE_MB),
    MimeTypeValidator(ALLOWED_HOMEWORK_TYPES, label="submission file"),
    FileExtensionValidator(ALLOWED_HOMEWORK_EXTENSIONS, label="submission file"),
]
