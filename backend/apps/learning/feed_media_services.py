"""
apps/learning/feed_media_services.py
------------------------------------
C2 — photos and files on the class feed.

Rules (approved in the C0 brief):
  * allow-list only, checked by real content (magic bytes), never the filename;
  * photos are re-encoded: EXIF/GPS stripped, long edge capped at 1600 px,
    a 400 px thumbnail kept;
  * up to 3 photos and 3 files per post, 8 MB and 10 MB respectively;
  * stored outside MEDIA_ROOT; every read goes through a permission-checked
    view, never a /media/ URL;
  * a pupil's upload waits for a teacher's approval before classmates see it;
  * removing keeps the blob and metadata as an audit copy;
  * per-class, per-term storage budget: 250 MB soft, 350 MB hard.

No antivirus engine is installed here; the checks above plus private storage
and attachment downloads stand in for it (approved decision 14).
"""

from __future__ import annotations

import io
import logging
import mimetypes
import os
import uuid
import zipfile

from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import (
    FEED_ALLOWED_EXTENSIONS,
    FEED_FILE_MAX_BYTES,
    FEED_IMAGE_EXTENSIONS,
    FEED_MAX_FILES,
    FEED_MAX_PHOTOS,
    FEED_PHOTO_MAX_BYTES,
    FEED_PHOTO_MAX_EDGE,
    FEED_PHOTO_THUMB_EDGE,
    FEED_QUOTA_HARD_BYTES,
    FEED_QUOTA_SOFT_BYTES,
    ClassFeedSettings,
    ClassPostAttachment,
)

logger = logging.getLogger(__name__)

MODERATOR_ROLES = {"admin", "director", "teacher"}
UPLOAD_ROLES = {"admin", "director", "teacher", "student"}

_IMAGE_FORMATS = {
    ".jpg": {"JPEG"},
    ".jpeg": {"JPEG"},
    ".png": {"PNG"},
    ".webp": {"WEBP"},
    ".gif": {"GIF"},
}

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OOXML_PREFIX = {".docx": "word/", ".pptx": "ppt/", ".xlsx": "xl/"}


class UploadProblem(ValueError):
    """A friendly, user-facing reason the upload was refused."""


# ---------------------------------------------------------------------------
# Switches
# ---------------------------------------------------------------------------


def _class_row(school_class):
    return (
        ClassFeedSettings.objects.filter(school_class=school_class)
        .values_list("media_enabled", "student_uploads_enabled")
        .first()
    )


def media_enabled_for(school_class, *, request=None) -> bool:
    """Photos/files need the community feed, the school switch and the class switch."""
    from .feed_selectors import community_enabled_for, school_media_switch_on

    if not community_enabled_for(school_class, request=request):
        return False
    if not school_media_switch_on(request):
        return False
    row = _class_row(school_class)
    return bool(row and row[0])


def student_uploads_enabled_for(school_class, *, request=None) -> bool:
    """Pupil uploads additionally need their own school and class switches."""
    if not media_enabled_for(school_class, request=request):
        return False
    from .feed_selectors import school_student_uploads_switch_on

    if not school_student_uploads_switch_on(request):
        return False
    row = _class_row(school_class)
    return bool(row and row[1])


def can_upload_media(user, school_class, *, request=None) -> bool:
    role = getattr(user, "role", "")
    if role not in UPLOAD_ROLES:
        return False
    if not media_enabled_for(school_class, request=request):
        return False
    if role == "student" and not student_uploads_enabled_for(
        school_class, request=request
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# Byte-level checks
# ---------------------------------------------------------------------------


def _head(upload, size: int = 64) -> bytes:
    try:
        upload.seek(0)
        head = upload.read(size)
    finally:
        try:
            upload.seek(0)
        except (AttributeError, OSError):
            pass
    return head or b""


def _ooxml_ok(upload, ext: str) -> bool:
    try:
        upload.seek(0)
        with zipfile.ZipFile(upload) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return False
    finally:
        try:
            upload.seek(0)
        except (AttributeError, OSError):
            pass
    return "[Content_Types].xml" in names and any(
        name.startswith(_OOXML_PREFIX[ext]) for name in names
    )


def _magic_ok(upload, ext: str) -> bool:
    head = _head(upload)
    if ext == ".pdf":
        return head.startswith(b"%PDF-")
    if ext in {".jpg", ".jpeg"}:
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == ".gif":
        return head[:6] in {b"GIF87a", b"GIF89a"}
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in _OOXML_PREFIX:
        return _ooxml_ok(upload, ext)
    if ext in {".doc", ".ppt", ".xls"}:
        return head.startswith(_OLE_MAGIC)
    if ext == ".mp3":
        if head.startswith(b"ID3"):
            return True
        return len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    return False


def _extension(upload) -> str:
    return os.path.splitext(upload.name or "")[1].lower()


def _check_extension(upload, *, allowed: set[str], human: str) -> str:
    ext = _extension(upload)
    if ext not in allowed:
        raise UploadProblem(
            f"{human} — that file type is not allowed. Allowed: PDF, Word, "
            "PowerPoint, Excel, pictures and MP3."
        )
    return ext


def _check_photo(upload) -> None:
    if upload.size > FEED_PHOTO_MAX_BYTES:
        raise UploadProblem(
            "Each photo must be 8 MB or smaller. Try a smaller picture."
        )
    ext = _check_extension(upload, allowed=FEED_IMAGE_EXTENSIONS, human="Photo")
    if not _magic_ok(upload, ext):
        raise UploadProblem(
            "That picture is not a real image file. Please choose the photo again."
        )
    try:
        upload.seek(0)
        probe = Image.open(upload)
        probe.verify()
        upload.seek(0)
        reopened = Image.open(upload)
        image_format = (reopened.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError):
        raise UploadProblem(
            "That picture could not be read. Please choose another one."
        )
    finally:
        try:
            upload.seek(0)
        except (AttributeError, OSError):
            pass
    if image_format not in _IMAGE_FORMATS[ext]:
        raise UploadProblem(
            "That picture's contents do not match its file name. "
            "Please choose the photo again."
        )


def _check_file(upload) -> None:
    if upload.size > FEED_FILE_MAX_BYTES:
        raise UploadProblem("Each file must be 10 MB or smaller.")
    ext = _check_extension(upload, allowed=FEED_ALLOWED_EXTENSIONS, human="File")
    if not _magic_ok(upload, ext):
        raise UploadProblem(
            "That file's contents do not match its type. "
            "Please choose the real file again."
        )


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------


def class_storage_usage_bytes(school_class) -> int:
    """
    Blobs stored for this class this term (any approval state: removed files
    keep their audit copy on disk). With no current term, counts everything.
    """
    from apps.academics.selectors import get_current_term

    qs = ClassPostAttachment.objects.filter(post__school_class=school_class)
    term = get_current_term()
    if term is not None:
        qs = qs.filter(
            created_at__date__gte=term.start_date,
            created_at__date__lte=term.end_date,
        )
    totals = qs.aggregate(files=Sum("file_size"), thumbs=Sum("thumb_size"))
    return int(totals["files"] or 0) + int(totals["thumbs"] or 0)


def quota_state(school_class) -> dict:
    used = class_storage_usage_bytes(school_class)
    return {
        "used": used,
        "soft": FEED_QUOTA_SOFT_BYTES,
        "hard": FEED_QUOTA_HARD_BYTES,
        "used_mb": round(used / (1024 * 1024), 1),
        "soft_mb": round(FEED_QUOTA_SOFT_BYTES / (1024 * 1024)),
        "hard_mb": round(FEED_QUOTA_HARD_BYTES / (1024 * 1024)),
        "over_soft": used >= FEED_QUOTA_SOFT_BYTES,
        "over_hard": used >= FEED_QUOTA_HARD_BYTES,
        "percent": min(100, round(used * 100 / FEED_QUOTA_SOFT_BYTES)),
    }


# ---------------------------------------------------------------------------
# Validation and storage
# ---------------------------------------------------------------------------


def validate_uploads(
    photos,
    files,
    *,
    actor,
    school_class,
    alt_text: str = "",
    request=None,
) -> list[dict]:
    """
    Check every upload before any row is written. Returns descriptors for
    ``save_uploads``; raises UploadProblem / PermissionDenied with a sentence
    the view can show as-is.
    """
    photos = [upload for upload in photos if upload]
    files = [upload for upload in files if upload]
    if not photos and not files:
        return []

    role = getattr(actor, "role", "")
    if role not in UPLOAD_ROLES:
        raise PermissionDenied("Your role cannot add photos or files to the feed.")
    if not media_enabled_for(school_class, request=request):
        raise PermissionDenied(
            "Photos and files are switched off for this class."
        )
    if role == "student" and not student_uploads_enabled_for(
        school_class, request=request
    ):
        raise PermissionDenied(
            "Your class does not allow pupils to add photos or files yet."
        )
    if len(photos) > FEED_MAX_PHOTOS:
        raise UploadProblem(
            f"Please add at most {FEED_MAX_PHOTOS} photos to one post."
        )
    if len(files) > FEED_MAX_FILES:
        raise UploadProblem(
            f"Please add at most {FEED_MAX_FILES} files to one post."
        )

    alt_text = (alt_text or "").strip()[:200]
    if photos and role in MODERATOR_ROLES and not alt_text:
        raise UploadProblem(
            "Please describe the photos for pupils who use a screen reader."
        )

    for upload in photos:
        _check_photo(upload)
    for upload in files:
        _check_file(upload)

    estimate = sum(upload.size for upload in photos) + sum(
        upload.size for upload in files
    )
    if class_storage_usage_bytes(school_class) + estimate > FEED_QUOTA_HARD_BYTES:
        raise UploadProblem(
            "This class has reached its storage limit for the term. "
            "Ask a teacher to remove some photos or files first."
        )

    return (
        [{"kind": "image", "upload": upload, "alt": alt_text} for upload in photos]
        + [{"kind": "file", "upload": upload, "alt": ""} for upload in files]
    )


def _process_photo(upload) -> dict:
    upload.seek(0)
    image = Image.open(upload)
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")
    image.thumbnail((FEED_PHOTO_MAX_EDGE, FEED_PHOTO_MAX_EDGE), Image.LANCZOS)

    full = io.BytesIO()
    image.save(full, "JPEG", quality=82, optimize=True, progressive=True)
    thumb_image = image.copy()
    thumb_image.thumbnail(
        (FEED_PHOTO_THUMB_EDGE, FEED_PHOTO_THUMB_EDGE), Image.LANCZOS
    )
    thumb = io.BytesIO()
    thumb_image.save(thumb, "JPEG", quality=80, optimize=True)
    return {
        "bytes": full.getvalue(),
        "thumb_bytes": thumb.getvalue(),
        "width": image.width,
        "height": image.height,
    }


@transaction.atomic
def save_uploads(
    post,
    descriptors: list[dict],
    *,
    actor,
    request=None,
) -> list[ClassPostAttachment]:
    """Store already-validated uploads. Pupils' media waits for approval."""
    if not descriptors:
        return []

    role = getattr(actor, "role", "")
    pending = role == "student"
    approval = (
        ClassPostAttachment.Approval.PENDING
        if pending
        else ClassPostAttachment.Approval.APPROVED
    )
    saved: list[ClassPostAttachment] = []
    now = timezone.now()

    for index, descriptor in enumerate(descriptors):
        upload = descriptor["upload"]
        original_name = os.path.basename(upload.name or "upload")[:255]
        if descriptor["kind"] == "image":
            processed = _process_photo(upload)
            token = uuid.uuid4().hex
            attachment = ClassPostAttachment(
                post=post,
                uploaded_by=actor,
                kind=ClassPostAttachment.Kind.IMAGE,
                original_name=original_name,
                file_size=len(processed["bytes"]),
                thumb_size=len(processed["thumb_bytes"]),
                content_type="image/jpeg",
                alt_text=descriptor.get("alt") or "",
                width=processed["width"],
                height=processed["height"],
                approval_status=approval,
                approved_by=None if pending else actor,
                approved_at=None if pending else now,
                ordering=index,
            )
            attachment.file.save(
                f"{token}.jpg", ContentFile(processed["bytes"]), save=False
            )
            attachment.thumbnail.save(
                f"{token}_thumb.jpg",
                ContentFile(processed["thumb_bytes"]),
                save=False,
            )
        else:
            upload.seek(0)
            data = upload.read()
            ext = _extension(upload)
            content_type = (
                mimetypes.guess_type(original_name)[0] or "application/octet-stream"
            )
            attachment = ClassPostAttachment(
                post=post,
                uploaded_by=actor,
                kind=ClassPostAttachment.Kind.FILE,
                original_name=original_name,
                file_size=len(data),
                content_type=content_type,
                approval_status=approval,
                approved_by=None if pending else actor,
                approved_at=None if pending else now,
                ordering=index,
            )
            attachment.file.save(
                f"{uuid.uuid4().hex}{ext}", ContentFile(data), save=False
            )
        attachment.save()
        saved.append(attachment)

    return saved


# ---------------------------------------------------------------------------
# Moderation
# ---------------------------------------------------------------------------


def _audit(*, actor, action: str, attachment, summary: dict, request=None) -> None:
    try:
        from apps.core.audit_log import log_action

        log_action(
            actor=actor,
            action=action,
            content_type=attachment._meta.label,
            object_id=attachment.pk,
            change_summary=summary,
            request=request,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Audit log write failed for feed attachment %s", attachment.pk
        )


def _assert_moderator(actor, attachment) -> None:
    if getattr(actor, "role", "") not in MODERATOR_ROLES:
        raise PermissionDenied("Only a teacher can manage feed media.")
    from .feed_selectors import can_view_feed

    if not can_view_feed(actor, attachment.post.school_class):
        raise PermissionDenied("You do not have access to this class feed.")


@transaction.atomic
def approve_attachment(*, attachment, actor, request=None):
    _assert_moderator(actor, attachment)
    if attachment.is_removed:
        raise PermissionDenied("That item was removed; it cannot be approved.")
    attachment.approval_status = ClassPostAttachment.Approval.APPROVED
    attachment.approved_by = actor
    attachment.approved_at = timezone.now()
    attachment.save(
        update_fields=["approval_status", "approved_by", "approved_at", "updated_at"]
    )
    _audit(
        actor=actor,
        action="feed.attachment.approved",
        attachment=attachment,
        summary={
            "post_id": attachment.post_id,
            "name": attachment.original_name,
        },
        request=request,
    )
    return attachment


@transaction.atomic
def remove_attachment(*, attachment, actor, request=None):
    """Hide an item from pupils and parents; the blob stays as the audit copy."""
    _assert_moderator(actor, attachment)
    if attachment.is_removed:
        return attachment
    attachment.approval_status = ClassPostAttachment.Approval.REMOVED
    attachment.removed_by = actor
    attachment.removed_at = timezone.now()
    attachment.save(
        update_fields=[
            "approval_status",
            "removed_by",
            "removed_at",
            "updated_at",
        ]
    )
    _audit(
        actor=actor,
        action="feed.attachment.removed",
        attachment=attachment,
        summary={
            "post_id": attachment.post_id,
            "name": attachment.original_name,
            "kept_audit_copy": True,
        },
        request=request,
    )
    return attachment


def attachment_visible_to(attachment, viewer) -> bool:
    """Who may see this attachment's card (not who may download it)."""
    role = getattr(viewer, "role", "")
    if attachment.is_removed:
        return role in MODERATOR_ROLES
    if attachment.is_pending:
        return role in MODERATOR_ROLES or attachment.uploaded_by_id == getattr(
            viewer, "pk", None
        )
    return True


__all__ = [
    "UploadProblem",
    "attachment_visible_to",
    "approve_attachment",
    "can_upload_media",
    "class_storage_usage_bytes",
    "media_enabled_for",
    "quota_state",
    "remove_attachment",
    "save_uploads",
    "student_uploads_enabled_for",
    "validate_uploads",
]
