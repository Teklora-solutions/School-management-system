"""
apps/homework/tests/test_teacher.py
------------------------------------
The teacher's homework workspace: scope, publishing rules, uploads and the
student-facing effects of each action.
"""

from __future__ import annotations

import datetime
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.teachers.models import TeacherAssignment, TeacherProfile

from ..models import Homework, HomeworkAttachment

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def pdf(name: str = "worksheet.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF_BYTES, content_type="application/pdf")


def png(name: str = "diagram.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")


class HomeworkTeacherTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        media_root = tempfile.mkdtemp(prefix="sms-homework-tests-")
        cls.media_override = override_settings(MEDIA_ROOT=media_root)
        cls.media_override.enable()
        cls.addClassCleanup(shutil.rmtree, media_root, ignore_errors=True)
        cls.addClassCleanup(cls.media_override.disable)

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.class_a = make_class(cls.year, name="Grade 7", level_order=7)
        cls.class_b = make_class(cls.year, name="Grade 8", level_order=8)
        cls.maths = make_subject("MATH", "Mathematics")
        cls.science = make_subject("SCI", "Science")

        cls.teacher_user = make_user("teacher", username="hw_teacher")
        cls.teacher = TeacherProfile.objects.create(
            user=cls.teacher_user, staff_id="STF-100"
        )
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.class_a,
            subject=cls.maths,
            academic_year=cls.year,
        )

        cls.other_teacher_user = make_user("teacher", username="hw_other_teacher")
        cls.other_teacher = TeacherProfile.objects.create(
            user=cls.other_teacher_user, staff_id="STF-200"
        )
        TeacherAssignment.objects.create(
            teacher=cls.other_teacher,
            school_class=cls.class_b,
            subject=cls.science,
            academic_year=cls.year,
        )

        cls.student = make_student_user(cls.class_a, admission_number="HW-101")
        cls.other_student = make_student_user(cls.class_b, admission_number="HW-102")

    def setUp(self):
        self.client.force_login(self.teacher_user)

    # -- helpers ---------------------------------------------------------

    def _homework(
        self,
        *,
        status=Homework.Status.PUBLISHED,
        school_class=None,
        subject=None,
        title="Fractions worksheet",
        set_by=None,
        due_date=None,
    ) -> Homework:
        return Homework.objects.create(
            title=title,
            description="Show all working.",
            subject=subject or self.maths,
            school_class=school_class or self.class_a,
            term=self.term,
            set_by=set_by or self.teacher_user,
            due_date=due_date or (datetime.date.today() + datetime.timedelta(days=3)),
            status=status,
            published_at=timezone.now()
            if status == Homework.Status.PUBLISHED
            else None,
        )

    def _form_data(self, **overrides) -> dict:
        data = {
            "school_class": self.class_a.pk,
            "subject": self.maths.pk,
            "stream": "",
            "term": self.term.pk,
            "title": "Fractions worksheet",
            "description": "Do questions 1 to 10.",
            "due_date": (
                datetime.date.today() + datetime.timedelta(days=2)
            ).isoformat(),
            "action": "draft",
        }
        data.update(overrides)
        return data

    # -- access ----------------------------------------------------------

    def test_students_cannot_open_the_teacher_workspace(self):
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("homework:teacher_list"))
        self.assertEqual(response.status_code, 403)

    def test_teacher_without_profile_is_refused(self):
        orphan = make_user("teacher", username="hw_orphan")
        self.client.force_login(orphan)
        response = self.client.get(reverse("homework:teacher_list"))
        self.assertEqual(response.status_code, 403)

    def test_list_shows_only_homework_in_my_assignments(self):
        self._homework(title="My fractions homework")
        self._homework(
            title="Other teacher science homework",
            school_class=self.class_b,
            subject=self.science,
            set_by=self.other_teacher_user,
        )
        response = self.client.get(reverse("homework:teacher_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My fractions homework")
        self.assertNotContains(response, "Other teacher science homework")

    # -- creation --------------------------------------------------------

    def test_create_draft_with_image_and_pdf(self):
        response = self.client.post(
            reverse("homework:teacher_create"),
            {**self._form_data(), "attachments": [png(), pdf()]},
        )
        self.assertEqual(response.status_code, 302)

        homework = Homework.objects.get(title="Fractions worksheet")
        self.assertEqual(homework.status, Homework.Status.DRAFT)
        self.assertIsNone(homework.published_at)
        self.assertEqual(homework.school_class, self.class_a)
        self.assertEqual(homework.subject, self.maths)
        self.assertEqual(homework.set_by, self.teacher_user)

        attachments = list(homework.attachments.all())
        self.assertEqual(len(attachments), 2)
        self.assertEqual(
            sorted(a.original_name for a in attachments),
            ["diagram.png", "worksheet.pdf"],
        )
        for attachment in attachments:
            self.assertTrue(attachment.file.name.startswith("homework/"))
            self.assertIn(
                f"class-{self.class_a.pk}/", attachment.file.name
            )

        # Drafts stay private to the teacher.
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("homework:list"))
        self.assertNotContains(response, "Fractions worksheet")

        self.assertTrue(
            AuditLog.objects.filter(
                action="homework.created", content_type="homework.Homework"
            ).exists()
        )

    def test_publish_now_reaches_the_student_portal(self):
        response = self.client.post(
            reverse("homework:teacher_create"),
            {**self._form_data(action="publish"), "attachments": [pdf()]},
        )
        self.assertEqual(response.status_code, 302)
        homework = Homework.objects.get(title="Fractions worksheet")
        self.assertEqual(homework.status, Homework.Status.PUBLISHED)
        self.assertIsNotNone(homework.published_at)

        self.client.force_login(self.student.user)
        response = self.client.get(reverse("homework:list"))
        self.assertContains(response, "Fractions worksheet")
        self.assertContains(response, "NEW")

        detail = self.client.get(reverse("homework:detail", args=[homework.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Do questions 1 to 10.")
        self.assertContains(detail, "worksheet.pdf")

    def test_subject_not_taught_in_that_class_is_refused(self):
        response = self.client.post(
            reverse("homework:teacher_create"),
            self._form_data(subject=self.science.pk),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("subject", response.context["form"].errors)
        self.assertFalse(Homework.objects.exists())

    def test_another_teachers_class_is_refused(self):
        response = self.client.post(
            reverse("homework:teacher_create"),
            self._form_data(school_class=self.class_b.pk),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("school_class", response.context["form"].errors)
        self.assertFalse(Homework.objects.exists())

    def test_subject_taught_elsewhere_is_not_valid_in_this_class(self):
        class_c = make_class(self.year, name="Grade 9", level_order=9)
        TeacherAssignment.objects.create(
            teacher=self.teacher,
            school_class=class_c,
            subject=self.science,
            academic_year=self.year,
        )
        response = self.client.post(
            reverse("homework:teacher_create"),
            self._form_data(school_class=self.class_a.pk, subject=self.science.pk),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "not assigned to teach this subject in that class"
        )
        self.assertFalse(Homework.objects.exists())

    def test_past_due_date_is_refused_when_creating(self):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        response = self.client.post(
            reverse("homework:teacher_create"),
            self._form_data(due_date=yesterday.isoformat()),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "today or a later date")
        self.assertFalse(Homework.objects.exists())

    def test_six_files_are_refused(self):
        files = [pdf(f"sheet-{i}.pdf") for i in range(6)]
        response = self.client.post(
            reverse("homework:teacher_create"),
            {**self._form_data(), "attachments": files},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at most 5 files")
        self.assertFalse(Homework.objects.exists())

    def test_unsupported_file_type_is_refused(self):
        evil = SimpleUploadedFile(
            "virus.exe", b"MZ", content_type="application/octet-stream"
        )
        response = self.client.post(
            reverse("homework:teacher_create"),
            {**self._form_data(), "attachments": [evil]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unsupported")
        self.assertFalse(Homework.objects.exists())

    def test_oversized_file_is_refused(self):
        big = SimpleUploadedFile(
            "huge.pdf", b"0" * (11 * 1024 * 1024), content_type="application/pdf"
        )
        response = self.client.post(
            reverse("homework:teacher_create"),
            {**self._form_data(), "attachments": [big]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "exceeds the 10 MB limit")
        self.assertFalse(Homework.objects.exists())

    # -- editing ---------------------------------------------------------

    def test_published_homework_stays_published_when_edited(self):
        homework = self._homework()

        response = self.client.post(
            reverse("homework:teacher_edit", args=[homework.pk]),
            self._form_data(title="Fractions worksheet (revised)", action="save"),
        )
        self.assertEqual(response.status_code, 302)
        homework.refresh_from_db()
        self.assertEqual(homework.title, "Fractions worksheet (revised)")
        self.assertEqual(homework.status, Homework.Status.PUBLISHED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="homework.updated", object_id=str(homework.pk)
            ).exists()
        )

    def test_unpublish_hides_homework_from_students(self):
        homework = self._homework()
        self.client.post(
            reverse("homework:teacher_edit", args=[homework.pk]),
            self._form_data(action="unpublish"),
        )
        homework.refresh_from_db()
        self.assertEqual(homework.status, Homework.Status.DRAFT)

        self.client.force_login(self.student.user)
        self.assertEqual(
            self.client.get(
                reverse("homework:detail", args=[homework.pk])
            ).status_code,
            404,
        )
        self.assertNotContains(
            self.client.get(reverse("homework:list")), "Fractions worksheet"
        )

    def test_removing_an_attachment_deletes_it_on_save(self):
        homework = self._homework(status=Homework.Status.DRAFT)
        first = HomeworkAttachment.objects.create(
            homework=homework,
            file=pdf("keep.pdf"),
            original_name="keep.pdf",
            file_size=len(PDF_BYTES),
            content_type="application/pdf",
        )
        second = HomeworkAttachment.objects.create(
            homework=homework,
            file=pdf("drop.pdf"),
            original_name="drop.pdf",
            file_size=len(PDF_BYTES),
            content_type="application/pdf",
        )
        storage = second.file.storage
        second_path = second.file.name

        response = self.client.post(
            reverse("homework:teacher_edit", args=[homework.pk]),
            {
                **self._form_data(action="save"),
                "remove_attachment": [second.pk],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(homework.attachments.values_list("pk", flat=True)), [first.pk]
        )
        self.assertFalse(storage.exists(second_path))

    def test_another_teachers_homework_cannot_be_edited(self):
        homework = self._homework(
            school_class=self.class_b,
            subject=self.science,
            set_by=self.other_teacher_user,
        )
        response = self.client.get(
            reverse("homework:teacher_edit", args=[homework.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_publish_toggle_from_the_list(self):
        homework = self._homework(status=Homework.Status.DRAFT)
        response = self.client.post(
            reverse("homework:teacher_publish", args=[homework.pk]),
            {"action": "publish"},
        )
        self.assertEqual(response.status_code, 302)
        homework.refresh_from_db()
        self.assertEqual(homework.status, Homework.Status.PUBLISHED)
        self.assertIsNotNone(homework.published_at)

        self.client.post(
            reverse("homework:teacher_publish", args=[homework.pk]),
            {"action": "unpublish"},
        )
        homework.refresh_from_db()
        self.assertEqual(homework.status, Homework.Status.DRAFT)

    # -- deletion --------------------------------------------------------

    def test_delete_removes_a_draft(self):
        homework = self._homework(status=Homework.Status.DRAFT)
        response = self.client.post(
            reverse("homework:teacher_delete", args=[homework.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Homework.objects.filter(pk=homework.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action="homework.deleted", object_id=str(homework.pk)
            ).exists()
        )

    def test_published_homework_cannot_be_deleted(self):
        homework = self._homework()
        self.client.post(reverse("homework:teacher_delete", args=[homework.pk]))
        self.assertTrue(Homework.objects.filter(pk=homework.pk).exists())

    # -- files and preview ----------------------------------------------

    def test_student_in_class_can_open_a_published_file(self):
        homework = self._homework()
        attachment = HomeworkAttachment.objects.create(
            homework=homework,
            file=pdf(),
            original_name="worksheet.pdf",
            file_size=len(PDF_BYTES),
            content_type="application/pdf",
        )
        self.client.force_login(self.student.user)
        response = self.client.get(
            reverse(
                "homework:attachment_file", args=[homework.pk, attachment.pk]
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("inline", response["Content-Disposition"])

    def test_draft_file_is_hidden_from_students(self):
        homework = self._homework(status=Homework.Status.DRAFT)
        attachment = HomeworkAttachment.objects.create(
            homework=homework,
            file=pdf(),
            original_name="worksheet.pdf",
            file_size=len(PDF_BYTES),
            content_type="application/pdf",
        )
        self.client.force_login(self.student.user)
        response = self.client.get(
            reverse(
                "homework:attachment_file", args=[homework.pk, attachment.pk]
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_other_class_student_cannot_open_a_file(self):
        homework = self._homework()
        attachment = HomeworkAttachment.objects.create(
            homework=homework,
            file=pdf(),
            original_name="worksheet.pdf",
            file_size=len(PDF_BYTES),
            content_type="application/pdf",
        )
        self.client.force_login(self.other_student.user)
        response = self.client.get(
            reverse(
                "homework:attachment_file", args=[homework.pk, attachment.pk]
            )
        )
        self.assertEqual(response.status_code, 403)

    def test_teacher_can_open_a_draft_file_from_the_workspace(self):
        homework = self._homework(status=Homework.Status.DRAFT)
        attachment = HomeworkAttachment.objects.create(
            homework=homework,
            file=pdf(),
            original_name="worksheet.pdf",
            file_size=len(PDF_BYTES),
            content_type="application/pdf",
        )
        response = self.client.get(
            reverse(
                "homework:attachment_file", args=[homework.pk, attachment.pk]
            )
        )
        self.assertEqual(response.status_code, 200)

    def test_preview_shows_a_draft_to_its_teacher(self):
        homework = self._homework(status=Homework.Status.DRAFT)
        response = self.client.get(
            reverse("homework:teacher_preview", args=[homework.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fractions worksheet")
        self.assertContains(response, "Draft preview")

    def test_word_document_is_served_as_a_download(self):
        homework = self._homework()
        attachment = HomeworkAttachment.objects.create(
            homework=homework,
            file=SimpleUploadedFile(
                "essay.docx",
                b"PK\x03\x04 fake docx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
            ),
            original_name="essay.docx",
            file_size=16,
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )
        self.client.force_login(self.student.user)
        response = self.client.get(
            reverse(
                "homework:attachment_file", args=[homework.pk, attachment.pk]
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
