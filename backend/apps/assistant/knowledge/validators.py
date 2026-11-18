"""
Upload checks for knowledge documents.

Three checks run in order: size, extension, and the bytes themselves. The byte
check is what stops an executable renamed ``fees.pdf`` from being stored — the
declared content type is never trusted on its own.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible

MAX_KNOWLEDGE_FILE_MB = 20

KIND_PDF = "pdf"
KIND_DOCX = "docx"
KIND_TEXT = "text"

# Extension → the kind its bytes must actually be.
EXTENSION_KINDS = {
    ".pdf": KIND_PDF,
    ".docx": KIND_DOCX,
    ".txt": KIND_TEXT,
    ".md": KIND_TEXT,
}
ALLOWED_KNOWLEDGE_EXTENSIONS = frozenset(EXTENSION_KINDS)

KIND_LABELS = {
    KIND_PDF: "PDF",
    KIND_DOCX: "Word document (.docx)",
    KIND_TEXT: "plain text",
}


def _sniff(file) -> str:
    """Return the kind the bytes look like, or ``"unknown"``."""
    file.seek(0)
    header = file.read(8)
    file.seek(0)
    if header.startswith(b"%PDF-"):
        return KIND_PDF
    if header.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(file) as archive:
                if "word/document.xml" in archive.namelist():
                    return KIND_DOCX
        except (zipfile.BadZipFile, OSError):
            return "unknown"
        finally:
            file.seek(0)
        return "unknown"
    sample = file.read(4096)
    file.seek(0)
    if b"\x00" in sample:
        return "unknown"
    try:
        sample.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "unknown"
    return KIND_TEXT


@deconstructible
class KnowledgeFileValidator:
    """Reject oversized files, unknown extensions and bytes that disagree."""

    def __init__(self, max_mb: float = MAX_KNOWLEDGE_FILE_MB):
        self.max_mb = max_mb

    def __call__(self, value):
        name = getattr(value, "name", "") or ""
        extension = Path(name).suffix.lower()
        if extension not in EXTENSION_KINDS:
            raise ValidationError(
                "Unsupported file type. Upload a PDF, Word document (.docx), "
                "plain text or Markdown file."
            )
        size = getattr(value, "size", None)
        if size is None:
            try:
                value.seek(0, 2)
                size = value.tell()
            finally:
                value.seek(0)
        if size > self.max_mb * 1024 * 1024:
            raise ValidationError(
                f"That file is larger than {self.max_mb:g} MB. Split it and upload "
                "the parts, or paste the text instead."
            )
        expected = EXTENSION_KINDS[extension]
        detected = _sniff(value)
        if detected != expected:
            raise ValidationError(
                f"The file is named {extension} but its contents are not a "
                f"{KIND_LABELS[expected]}. Rename it correctly or export it again."
            )

    def __eq__(self, other):
        return isinstance(other, KnowledgeFileValidator) and self.max_mb == other.max_mb


validate_knowledge_file = [KnowledgeFileValidator()]
