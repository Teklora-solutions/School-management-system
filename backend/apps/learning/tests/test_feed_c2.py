"""
apps/learning/tests/test_feed_c2.py
-----------------------------------
C2 — photos and files on the class feed.

What is covered:
  * real-content checks (magic bytes, zip package, Pillow) and the allow-list,
  * size and count caps, teacher alt text, the hard quota stop,
  * server-side re-encode (EXIF stripped, 1600 px long edge, 400 px thumb),
  * pupil uploads waiting for a teacher, approval/removal audits and audit copies,
  * every download going through the permission-checked view, never /media/,
  * the two-level media and pupil-upload switches,
  * the class settings page and its storage card.

Feed media lives under FEED_MEDIA_ROOT, so every test points that at a
temporary directory and removes it afterwards.
"""

from __future__ import annotations

import io
import shutil
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.academics.models import Term
from apps.core.models import AuditLog, SchoolSettings
from apps.dashboards.tests.fixtures import make_class, make_student_user, make_user
from apps.learning import feed_media_services
from apps.learning.models import (
    FEED_FILE_MAX_BYTES,
    FEED_MAX_FILES,
    FEED_MAX_PHOTOS,
    FEED_PHOTO_MAX_BYTES,
    FEED_QUOTA_HARD_BYTES,
    FEED_QUOTA_SOFT_BYTES,
    ClassFeedSettings,
    ClassPost,
    ClassPostAttachment,
)

from .test_feed_community import (
    CommunityTestCase,
    make_parent_for,
    set_class_community,
    set_school_switches,
)


# ---------------------------------------------------------------------------
# Byte factories — real files, because the checks read the contents
# ---------------------------------------------------------------------------


def image_bytes(size=(320, 240), color=(30, 120, 200), fmt="JPEG", exif=False):
    buf = io.BytesIO()
    image = Image.new("RGB", size, color)
    kwargs = {}
    if exif:
        tags = Image.Exif()
        tags[0x010F] = "TestPhone"  # Make
        tags[0x0110] = "Model X"  # Model
        kwargs["exif"] = tags.tobytes()
    image.save(buf, fmt, **kwargs)
    return buf.getvalue()


def photo(name="photo.jpg", **kwargs):
    return SimpleUploadedFile(name, image_bytes(**kwargs), content_type="image/jpeg")


def png_photo(name="photo.png", size=(64, 64)):
    return SimpleUploadedFile(
        name, image_bytes(size=size, fmt="PNG"), content_type="image/png"
    )


def pdf_file(name="notes.pdf"):
    return SimpleUploadedFile(
        name,
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF",
        content_type="application/pdf",
    )


def zip_file(name, entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for entry in entries:
            archive.writestr(entry, b"x")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="application/zip")


def mp3_file(name="sound.mp3"):
    return SimpleUploadedFile(
        name, b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 32, content_type="audio/mpeg"
    )


def set_media_switches(*, media=True, student_uploads=True):
    row = SchoolSettings.load()
    row.feed_media_enabled = media
    row.feed_student_uploads_enabled = student_uploads
    row.save(
        update_fields=[
            "feed_media_enabled",
            "feed_student_uploads_enabled",
            "updated_at",
        ]
    )
    return row


def set_class_media(school_class, *, media=True, student_uploads=True, active=True):
    row, _ = ClassFeedSettings.objects.update_or_create(
        school_class=school_class,
        defaults={
            "is_active": active,
            "media_enabled": media,
            "student_uploads_enabled": student_uploads,
        },
    )
    return row


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class C2Base(CommunityTestCase):
    def setUp(self):
        super().setUp()
        self.media_dir = tempfile.mkdtemp(prefix="feed-media-c2-")
        self.media_override = override_settings(FEED_MEDIA_ROOT=self.media_dir)
        self.media_override.enable()
        set_media_switches()
        set_class_media(self.school_class)
        self.classmate = make_student_user(
            self.school_class, admission_number="CM-002"
        )
        # The shared fixture's term is a past window; the storage budget is
        # term-scoped, so move it around today.
        today = timezone.localdate()
        Term.objects.filter(pk=self.term.pk).update(
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=60),
        )

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_dir, ignore_errors=True)
        super().tearDown()

    # -- helpers ---------------------------------------------------------

    def make_post(
        self,
        *,
        author=None,
        post_type=ClassPost.PostType.QUESTION,
        body="Our science project",
    ):
        return ClassPost.objects.create(
            school_class=self.school_class,
            author=author or self.teacher_user,
            post_type=post_type,
            body=body,
        )

    def upload(self, *, photos=(), files=(), actor=None, post=None, alt="Our project"):
        actor = actor or self.teacher_user
        post = post or self.make_post(author=actor)
        descriptors = feed_media_services.validate_uploads(
            list(photos),
            list(files),
            actor=actor,
            school_class=self.school_class,
            alt_text=alt,
        )
        saved = feed_media_services.save_uploads(post, descriptors, actor=actor)
        return post, saved

    def file_url(self, post, attachment):
        return reverse(
            "learning:feed_attachment_file",
            args=[self.school_class.pk, post.pk, attachment.pk],
        )

    def action_url(self, post, attachment):
        return reverse(
            "learning:feed_attachment_action",
            args=[self.school_class.pk, post.pk, attachment.pk],
        )

    def feed_url(self):
        return reverse("learning:class_feed", args=[self.school_class.pk])

    def settings_url(self):
        return reverse("learning:feed_settings", args=[self.school_class.pk])

    def read_image(self, attachment):
        with attachment.file.open("rb") as handle:
            return Image.open(io.BytesIO(handle.read()))

    def page(self, user, url=None):
        self.client.force_login(user)
        return self.client.get(url or self.feed_url())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ValidationTests(C2Base):
    def test_the_agreed_limits_are_in_force(self):
        self.assertEqual(FEED_MAX_PHOTOS, 3)
        self.assertEqual(FEED_MAX_FILES, 3)
        self.assertEqual(FEED_PHOTO_MAX_BYTES, 8 * 1024 * 1024)
        self.assertEqual(FEED_FILE_MAX_BYTES, 10 * 1024 * 1024)
        self.assertEqual(FEED_QUOTA_SOFT_BYTES, 250 * 1024 * 1024)
        self.assertEqual(FEED_QUOTA_HARD_BYTES, 350 * 1024 * 1024)

    def test_a_real_jpeg_is_accepted(self):
        _, saved = self.upload(photos=[photo()])
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].kind, ClassPostAttachment.Kind.IMAGE)
        self.assertEqual(saved[0].alt_text, "Our project")

    def test_a_fake_jpeg_is_refused(self):
        fake = SimpleUploadedFile(
            "photo.jpg", b"this is not a picture", content_type="image/jpeg"
        )
        with self.assertRaises(feed_media_services.UploadProblem):
            self.upload(photos=[fake])

    def test_a_pdf_named_as_a_photo_is_refused(self):
        disguised = SimpleUploadedFile(
            "photo.jpg", pdf_file().read(), content_type="image/jpeg"
        )
        with self.assertRaises(feed_media_services.UploadProblem):
            self.upload(photos=[disguised])

    def test_a_png_is_accepted_and_stored_as_jpeg(self):
        _, saved = self.upload(photos=[png_photo()])
        self.assertEqual(saved[0].content_type, "image/jpeg")
        self.assertEqual(self.read_image(saved[0]).format, "JPEG")

    def test_an_unlisted_extension_is_refused(self):
        exe = SimpleUploadedFile("virus.exe", b"MZ\x90\x00", content_type="application/octet-stream")
        with self.assertRaises(feed_media_services.UploadProblem) as caught:
            self.upload(files=[exe])
        self.assertIn("not allowed", str(caught.exception))

    def test_an_oversized_photo_is_refused(self):
        with patch.object(feed_media_services, "FEED_PHOTO_MAX_BYTES", 100):
            with self.assertRaises(feed_media_services.UploadProblem) as caught:
                self.upload(photos=[photo()])
        self.assertIn("8 MB", str(caught.exception))

    def test_more_than_three_photos_are_refused(self):
        with self.assertRaises(feed_media_services.UploadProblem) as caught:
            self.upload(photos=[photo() for _ in range(4)])
        self.assertIn("at most 3 photos", str(caught.exception))

    def test_more_than_three_files_are_refused(self):
        with self.assertRaises(feed_media_services.UploadProblem) as caught:
            self.upload(files=[pdf_file() for _ in range(4)])
        self.assertIn("at most 3 files", str(caught.exception))

    def test_a_real_pdf_is_accepted(self):
        _, saved = self.upload(files=[pdf_file()])
        self.assertEqual(saved[0].kind, ClassPostAttachment.Kind.FILE)

    def test_a_fake_pdf_is_refused(self):
        fake = SimpleUploadedFile(
            "notes.pdf", b"just text", content_type="application/pdf"
        )
        with self.assertRaises(feed_media_services.UploadProblem):
            self.upload(files=[fake])

    def test_a_docx_must_be_a_real_word_package(self):
        good = zip_file(
            "work.docx", ["[Content_Types].xml", "word/document.xml"]
        )
        _, saved = self.upload(files=[good], alt="")
        self.assertEqual(saved[0].original_name, "work.docx")

        wrong = zip_file("work.docx", ["[Content_Types].xml", "xl/workbook.xml"])
        with self.assertRaises(feed_media_services.UploadProblem):
            self.upload(files=[wrong], alt="")

    def test_a_legacy_doc_needs_the_ole_header(self):
        ole = SimpleUploadedFile(
            "old.doc",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64,
            content_type="application/msword",
        )
        _, saved = self.upload(files=[ole], alt="")
        self.assertEqual(saved[0].original_name, "old.doc")

    def test_an_mp3_needs_a_real_frame_or_id3_header(self):
        fake = SimpleUploadedFile("song.mp3", b"not music", content_type="audio/mpeg")
        with self.assertRaises(feed_media_services.UploadProblem):
            self.upload(files=[fake])
        _, saved = self.upload(files=[mp3_file()], alt="")
        self.assertEqual(saved[0].kind, ClassPostAttachment.Kind.FILE)

    def test_a_teacher_must_describe_the_photos(self):
        with self.assertRaises(feed_media_services.UploadProblem) as caught:
            self.upload(photos=[photo()], alt="   ")
        self.assertIn("screen reader", str(caught.exception))

    def test_a_pupil_is_not_forced_to_describe_a_photo(self):
        _, saved = self.upload(
            photos=[photo()], actor=self.student.user, alt=""
        )
        self.assertEqual(saved[0].alt_text, "")

    def test_the_school_media_switch_is_required(self):
        set_media_switches(media=False)
        with self.assertRaises(PermissionDenied):
            self.upload(photos=[photo()])

    def test_the_class_media_switch_is_required(self):
        set_class_media(self.school_class, media=False)
        with self.assertRaises(PermissionDenied):
            self.upload(photos=[photo()])

    def test_a_pupil_needs_both_pupil_switches(self):
        set_media_switches(media=True, student_uploads=False)
        with self.assertRaises(PermissionDenied):
            self.upload(photos=[photo()], actor=self.student.user)

        set_media_switches(media=True, student_uploads=True)
        set_class_media(self.school_class, student_uploads=False)
        with self.assertRaises(PermissionDenied):
            self.upload(photos=[photo()], actor=self.student.user)

    def test_a_teacher_may_still_upload_when_pupil_uploads_are_off(self):
        set_class_media(self.school_class, student_uploads=False)
        _, saved = self.upload(photos=[photo()])
        self.assertEqual(len(saved), 1)

    def test_a_parent_cannot_upload(self):
        with self.assertRaises(PermissionDenied):
            self.upload(photos=[photo()], actor=self.parent_user)

    def test_the_hard_quota_stop_refuses_a_upload(self):
        with patch.object(feed_media_services, "FEED_QUOTA_HARD_BYTES", 1024):
            with self.assertRaises(feed_media_services.UploadProblem) as caught:
                self.upload(photos=[photo()])
        self.assertIn("storage limit", str(caught.exception))


# ---------------------------------------------------------------------------
# Server-side photo processing
# ---------------------------------------------------------------------------


class PhotoProcessingTests(C2Base):
    def test_a_big_photo_is_capped_at_1600_px_and_keeps_its_shape(self):
        _, saved = self.upload(photos=[photo(size=(2400, 1800))])
        image = self.read_image(saved[0])
        self.assertEqual((image.width, image.height), (1600, 1200))
        self.assertEqual(saved[0].width, 1600)
        self.assertEqual(saved[0].height, 1200)

    def test_a_small_photo_is_not_enlarged(self):
        _, saved = self.upload(photos=[photo(size=(320, 240))])
        image = self.read_image(saved[0])
        self.assertEqual((image.width, image.height), (320, 240))

    def test_the_thumbnail_is_at_most_400_px(self):
        _, saved = self.upload(photos=[photo(size=(2400, 1800))])
        with saved[0].thumbnail.open("rb") as handle:
            thumb = Image.open(io.BytesIO(handle.read()))
        self.assertLessEqual(max(thumb.size), 400)
        self.assertEqual(thumb.size, (400, 300))

    def test_exif_location_data_is_stripped(self):
        _, saved = self.upload(photos=[photo(size=(800, 600), exif=True)])
        exif = dict(self.read_image(saved[0]).getexif())
        self.assertNotIn(0x010F, exif)
        self.assertNotIn(0x0110, exif)
        self.assertEqual(dict(self.read_image(saved[0]).getexif().get_ifd(0x8825)), {})

    def test_the_stored_name_is_random_but_the_original_is_kept(self):
        _, saved = self.upload(photos=[photo(name="class-trip.jpg")])
        self.assertNotIn("class-trip", saved[0].file.name)
        self.assertEqual(saved[0].original_name, "class-trip.jpg")

    def test_a_file_keeps_its_bytes_and_download_name(self):
        payload = b"%PDF-1.4\nunique payload\n%%EOF"
        upload = SimpleUploadedFile("worksheet.pdf", payload, content_type="application/pdf")
        _, saved = self.upload(files=[upload], alt="")
        with saved[0].file.open("rb") as handle:
            self.assertEqual(handle.read(), payload)
        self.assertEqual(saved[0].original_name, "worksheet.pdf")


# ---------------------------------------------------------------------------
# Approval, removal and audits
# ---------------------------------------------------------------------------


class ApprovalTests(C2Base):
    def test_a_teacher_upload_is_approved_at_once(self):
        _, saved = self.upload(photos=[photo()])
        attachment = saved[0]
        self.assertEqual(
            attachment.approval_status, ClassPostAttachment.Approval.APPROVED
        )
        self.assertEqual(attachment.approved_by_id, self.teacher_user.pk)
        self.assertIsNotNone(attachment.approved_at)

    def test_a_pupil_upload_waits_for_a_teacher(self):
        _, saved = self.upload(photos=[photo()], actor=self.student.user)
        attachment = saved[0]
        self.assertTrue(attachment.is_pending)
        self.assertIsNone(attachment.approved_at)
        self.assertIsNone(attachment.approved_by_id)

    def test_a_pending_photo_is_hidden_from_classmates_and_parents(self):
        post, _ = self.upload(
            photos=[photo(name="private-diagram.jpg")],
            actor=self.student.user,
        )
        self.assertNotContains(self.page(self.classmate.user), "private-diagram")
        self.assertNotContains(self.page(self.parent_user), "private-diagram")

    def test_the_uploader_sees_the_item_waiting_for_the_teacher(self):
        post, _ = self.upload(
            photos=[photo(name="my-drawing.jpg")], actor=self.student.user
        )
        response = self.page(self.student.user)
        self.assertContains(response, "my-drawing")
        self.assertContains(response, "Waiting for your teacher")

    def test_a_teacher_sees_the_pending_item_and_its_controls(self):
        self.upload(photos=[photo(name="draft-poster.jpg")], actor=self.student.user)
        response = self.page(self.teacher_user)
        self.assertContains(response, "draft-poster")
        self.assertContains(response, "Waiting for your approval")
        self.assertContains(response, "Manage media")

    def test_approval_makes_the_item_visible_to_classmates(self):
        post, saved = self.upload(
            photos=[photo(name="shared-poster.jpg")], actor=self.student.user
        )
        feed_media_services.approve_attachment(
            attachment=saved[0], actor=self.teacher_user
        )
        self.assertContains(
            self.page(self.classmate.user),
            f"/attachment/{saved[0].pk}/?thumb=1",
        )

    def test_approval_is_audited(self):
        _, saved = self.upload(photos=[photo()], actor=self.student.user)
        feed_media_services.approve_attachment(
            attachment=saved[0], actor=self.teacher_user
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="feed.attachment.approved",
                content_type="learning.ClassPostAttachment",
                object_id=str(saved[0].pk),
            ).exists()
        )

    def test_a_student_cannot_approve(self):
        _, saved = self.upload(photos=[photo()], actor=self.student.user)
        with self.assertRaises(PermissionDenied):
            feed_media_services.approve_attachment(
                attachment=saved[0], actor=self.student.user
            )

    def test_a_student_cannot_remove(self):
        _, saved = self.upload(photos=[photo()])
        with self.assertRaises(PermissionDenied):
            feed_media_services.remove_attachment(
                attachment=saved[0], actor=self.student.user
            )

    def test_removal_hides_the_item_but_keeps_the_audit_copy(self):
        post, saved = self.upload(photos=[photo(name="removed-photo.jpg")])
        feed_media_services.remove_attachment(
            attachment=saved[0], actor=self.teacher_user
        )
        saved[0].refresh_from_db()
        self.assertTrue(saved[0].is_removed)
        self.assertTrue(Path(saved[0].file.path).exists())
        self.assertContains(self.page(self.teacher_user), "kept for the school")
        self.assertNotContains(self.page(self.student.user), "removed-photo")
        self.assertTrue(
            AuditLog.objects.filter(
                action="feed.attachment.removed",
                content_type="learning.ClassPostAttachment",
                object_id=str(saved[0].pk),
            ).exists()
        )

    def test_a_removed_item_cannot_be_approved(self):
        _, saved = self.upload(photos=[photo()])
        feed_media_services.remove_attachment(
            attachment=saved[0], actor=self.teacher_user
        )
        with self.assertRaises(PermissionDenied):
            feed_media_services.approve_attachment(
                attachment=saved[0], actor=self.teacher_user
            )

    def test_removing_twice_is_harmless(self):
        _, saved = self.upload(photos=[photo()])
        feed_media_services.remove_attachment(
            attachment=saved[0], actor=self.teacher_user
        )
        feed_media_services.remove_attachment(
            attachment=saved[0], actor=self.teacher_user
        )
        saved[0].refresh_from_db()
        self.assertTrue(saved[0].is_removed)


# ---------------------------------------------------------------------------
# Downloads through the permission-checked view
# ---------------------------------------------------------------------------


class AttachmentViewTests(C2Base):
    def test_a_photo_is_served_inline_with_nosniff(self):
        post, saved = self.upload(photos=[photo()])
        self.client.force_login(self.student.user)
        response = self.client.get(self.file_url(post, saved[0]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("private", response["Cache-Control"])
        response.close()

    def test_download_one_sends_it_as_a_file(self):
        post, saved = self.upload(files=[pdf_file(name="worksheet.pdf")])
        self.client.force_login(self.student.user)
        response = self.client.get(self.file_url(post, saved[0]) + "?download=1")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        self.assertIn("worksheet.pdf", response["Content-Disposition"])
        response.close()

    def test_the_thumbnail_is_returned_when_asked(self):
        post, saved = self.upload(photos=[photo(size=(2400, 1800))])
        self.client.force_login(self.student.user)
        response = self.client.get(self.file_url(post, saved[0]) + "?thumb=1")
        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content)
        thumb = Image.open(io.BytesIO(body))
        self.assertLessEqual(max(thumb.size), 400)

    def test_a_parent_may_open_a_class_photo(self):
        post, saved = self.upload(photos=[photo()])
        self.client.force_login(self.parent_user)
        response = self.client.get(self.file_url(post, saved[0]))
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_a_pupil_from_another_class_gets_404(self):
        other_class = make_class(self.year, name="Grade 9", level_order=9)
        outsider = make_student_user(other_class, admission_number="CM-900")
        post, saved = self.upload(photos=[photo()])
        self.client.force_login(outsider.user)
        response = self.client.get(self.file_url(post, saved[0]))
        self.assertEqual(response.status_code, 404)

    def test_an_attachment_id_from_another_post_is_404(self):
        post, saved = self.upload(photos=[photo()])
        other_post = self.make_post(body="Another post")
        self.client.force_login(self.teacher_user)
        response = self.client.get(
            reverse(
                "learning:feed_attachment_file",
                args=[self.school_class.pk, other_post.pk, saved[0].pk],
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_a_pupil_upload_stays_404_until_approved(self):
        post, saved = self.upload(photos=[photo()], actor=self.student.user)
        self.client.force_login(self.classmate.user)
        self.assertEqual(
            self.client.get(self.file_url(post, saved[0])).status_code, 404
        )

        self.client.force_login(self.student.user)
        response = self.client.get(self.file_url(post, saved[0]))
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_the_uploader_and_teacher_can_open_a_pending_photo(self):
        post, saved = self.upload(photos=[photo()], actor=self.student.user)
        for user in (self.student.user, self.teacher_user):
            self.client.force_login(user)
            response = self.client.get(self.file_url(post, saved[0]))
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_a_removed_item_is_404_for_pupils_but_available_to_teachers(self):
        post, saved = self.upload(photos=[photo()])
        feed_media_services.remove_attachment(
            attachment=saved[0], actor=self.teacher_user
        )
        self.client.force_login(self.student.user)
        self.assertEqual(
            self.client.get(self.file_url(post, saved[0])).status_code, 404
        )
        self.client.force_login(self.teacher_user)
        response = self.client.get(self.file_url(post, saved[0]))
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_the_media_switch_closes_the_view_but_not_the_audit_copy(self):
        admin = make_user("admin", username="c2_admin")
        post, saved = self.upload(photos=[photo()])
        set_class_media(self.school_class, media=False)

        self.client.force_login(self.student.user)
        self.assertEqual(
            self.client.get(self.file_url(post, saved[0])).status_code, 404
        )
        self.client.force_login(admin)
        response = self.client.get(self.file_url(post, saved[0]))
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_a_scheduled_post_hides_its_media_from_pupils(self):
        post = ClassPost.objects.create(
            school_class=self.school_class,
            author=self.teacher_user,
            post_type=ClassPost.PostType.ANNOUNCEMENT,
            body="Coming soon",
            scheduled_at=timezone.now() + timedelta(days=1),
        )
        _, saved = self.upload(post=post, photos=[photo()])

        self.client.force_login(self.student.user)
        self.assertEqual(
            self.client.get(self.file_url(post, saved[0])).status_code, 404
        )
        self.client.force_login(self.teacher_user)
        response = self.client.get(self.file_url(post, saved[0]))
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_the_feed_uses_the_safe_url_and_never_media(self):
        post, saved = self.upload(photos=[photo()], files=[pdf_file()])
        page = self.page(self.student.user)
        self.assertContains(page, f"/attachment/{saved[0].pk}/?thumb=1")
        self.assertContains(page, f"/attachment/{saved[1].pk}/?download=1")
        self.assertContains(page, "data-feed-photo")
        self.assertNotContains(page, "/media/")

    def test_the_image_carries_its_description_for_screen_readers(self):
        self.upload(photos=[photo()], alt="Our bean seedlings")
        page = self.page(self.student.user)
        self.assertContains(page, 'alt="Our bean seedlings"')

    def test_storage_refuses_to_build_a_public_url(self):
        _, saved = self.upload(photos=[photo()])
        with self.assertRaises(ValueError):
            saved[0].file.url
        self.assertTrue(
            str(saved[0].file.path).startswith(str(Path(settings.FEED_MEDIA_ROOT)))
        )


# ---------------------------------------------------------------------------
# Composer, action view and settings page
# ---------------------------------------------------------------------------


class ComposerTests(C2Base):
    def test_a_teacher_posts_a_photo_through_the_feed_form(self):
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            self.feed_url(),
            {
                "body": "Photos from our trip",
                "post_type": ClassPost.PostType.ANNOUNCEMENT,
                "photo_alt": "The school bus",
                "photos": [photo()],
            },
        )
        self.assertEqual(response.status_code, 302)
        post = ClassPost.objects.get(body="Photos from our trip")
        attachment = post.attachments.get()
        self.assertEqual(attachment.approval_status, ClassPostAttachment.Approval.APPROVED)
        self.assertEqual(attachment.alt_text, "The school bus")

    def test_a_refused_upload_does_not_leave_an_empty_post(self):
        before = ClassPost.objects.count()
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            self.feed_url(),
            {
                "body": "Bad picture",
                "post_type": ClassPost.PostType.ANNOUNCEMENT,
                "photo_alt": "A picture",
                "photos": [
                    SimpleUploadedFile("bad.jpg", b"not a picture", content_type="image/jpeg")
                ],
            },
            follow=True,
        )
        self.assertContains(response, "not a real image file")
        self.assertEqual(ClassPost.objects.count(), before)

    def test_a_teacher_photo_without_a_description_is_refused(self):
        before = ClassPost.objects.count()
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            self.feed_url(),
            {
                "body": "No description",
                "post_type": ClassPost.PostType.ANNOUNCEMENT,
                "photos": [photo()],
            },
            follow=True,
        )
        self.assertContains(response, "screen reader")
        self.assertEqual(ClassPost.objects.count(), before)

    def test_a_pupil_cannot_attach_when_the_pupil_switch_is_off(self):
        set_class_media(self.school_class, student_uploads=False)
        before = ClassPost.objects.count()
        self.client.force_login(self.student.user)
        response = self.client.post(
            self.feed_url(),
            {
                "body": "My photo",
                "post_type": ClassPost.PostType.QUESTION,
                "photos": [photo()],
            },
            follow=True,
        )
        self.assertContains(response, "does not allow pupils")
        self.assertEqual(ClassPost.objects.count(), before)

    def test_a_pupil_photo_is_pending_after_posting(self):
        self.client.force_login(self.student.user)
        self.client.post(
            self.feed_url(),
            {
                "body": "My drawing",
                "post_type": ClassPost.PostType.QUESTION,
                "photos": [photo()],
            },
        )
        attachment = ClassPostAttachment.objects.get()
        self.assertTrue(attachment.is_pending)


class ModerationViewTests(C2Base):
    def test_a_teacher_approves_through_the_action_view(self):
        post, saved = self.upload(photos=[photo()], actor=self.student.user)
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            self.action_url(post, saved[0]),
            {"action": "approve", "next": self.feed_url()},
        )
        self.assertEqual(response.status_code, 302)
        saved[0].refresh_from_db()
        self.assertTrue(saved[0].approval_status == ClassPostAttachment.Approval.APPROVED)

    def test_an_off_site_next_is_ignored(self):
        post, saved = self.upload(photos=[photo()], actor=self.student.user)
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            self.action_url(post, saved[0]),
            {"action": "approve", "next": "https://evil.example/"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.feed_url())

    def test_a_student_cannot_use_the_action_view(self):
        post, saved = self.upload(photos=[photo()], actor=self.student.user)
        self.client.force_login(self.student.user)
        response = self.client.post(
            self.action_url(post, saved[0]), {"action": "approve"}
        )
        self.assertEqual(response.status_code, 403)

    def test_the_action_view_only_accepts_post(self):
        post, saved = self.upload(photos=[photo()])
        self.client.force_login(self.teacher_user)
        response = self.client.get(self.action_url(post, saved[0]))
        self.assertEqual(response.status_code, 404)


class SettingsPageTests(C2Base):
    def test_the_page_shows_both_switches_and_the_storage_card(self):
        response = self.page(self.teacher_user, self.settings_url())
        self.assertContains(response, "Allow photos and files")
        self.assertContains(response, "Let pupils attach photos and files")
        self.assertContains(response, "Storage this term")
        self.assertContains(response, "0.0 MB")

    def test_saving_the_switches_updates_the_class_row(self):
        self.client.force_login(self.teacher_user)
        response = self.client.post(
            self.settings_url(),
            {
                "is_active": "1",
                "media_enabled": "1",
                "student_uploads_enabled": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        row = ClassFeedSettings.objects.get(school_class=self.school_class)
        self.assertTrue(row.media_enabled)
        self.assertTrue(row.student_uploads_enabled)

    def test_an_off_switch_saves_as_off(self):
        self.client.force_login(self.teacher_user)
        self.client.post(self.settings_url(), {"is_active": "1"})
        row = ClassFeedSettings.objects.get(school_class=self.school_class)
        self.assertFalse(row.media_enabled)
        self.assertFalse(row.student_uploads_enabled)

    def test_a_pupil_cannot_open_the_settings_page(self):
        response = self.page(self.student.user, self.settings_url())
        self.assertEqual(response.status_code, 403)

    def test_the_soft_limit_is_flagged(self):
        self.upload(photos=[photo()])
        with patch.object(feed_media_services, "FEED_QUOTA_SOFT_BYTES", 10):
            response = self.page(self.teacher_user, self.settings_url())
        self.assertContains(response, "over its soft limit")

    def test_the_quota_counts_files_and_their_thumbnails(self):
        _, saved = self.upload(photos=[photo(size=(1200, 900))])
        used = feed_media_services.class_storage_usage_bytes(self.school_class)
        self.assertEqual(used, saved[0].file_size + saved[0].thumb_size)
        self.assertGreater(used, 0)

    def test_a_removed_item_still_counts_towards_storage(self):
        _, saved = self.upload(photos=[photo()])
        before = feed_media_services.class_storage_usage_bytes(self.school_class)
        feed_media_services.remove_attachment(
            attachment=saved[0], actor=self.teacher_user
        )
        after = feed_media_services.class_storage_usage_bytes(self.school_class)
        self.assertEqual(before, after)

    def test_the_quota_state_reports_percent(self):
        state = feed_media_services.quota_state(self.school_class)
        self.assertEqual(state["used"], 0)
        self.assertEqual(state["percent"], 0)
        self.assertFalse(state["over_soft"])
        self.assertFalse(state["over_hard"])


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class DefaultSwitchTests(TestCase):
    """Media and pupil uploads are off until a person turns them on."""

    def test_school_defaults_are_off(self):
        row = SchoolSettings.load()
        self.assertFalse(row.feed_media_enabled)
        self.assertFalse(row.feed_student_uploads_enabled)

    def test_class_defaults_are_off(self):
        from apps.dashboards.tests.fixtures import make_term, make_year

        year = make_year()
        make_term(year)
        school_class = make_class(year, name="Grade 4", level_order=4)
        row = ClassFeedSettings.objects.create(school_class=school_class)
        self.assertFalse(row.media_enabled)
        self.assertFalse(row.student_uploads_enabled)
