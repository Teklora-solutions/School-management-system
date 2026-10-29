"""
apps/homework/tests/test_submissions.py
---------------------------------------
The student submission flow: who may hand work in, what makes a submission
late, when it can still be changed, file access, and the teacher's review page.
"""

from __future__ import annotations

import datetime
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ParentProfile
from apps.core.models import AuditLog
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.students.models import StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile

from .. import services
from ..models import Homework, HomeworkSubmission

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def png(name: str = "answer.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")


def pdf(name: str = "answer.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF_BYTES, content_type="application/pdf")


class SubmissionTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        media_root = tempfile.mkdtemp(prefix="sms-submission-tests-")
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

        cls.teacher_user = make_user("teacher", username="sub_teacher")
        cls.teacher = TeacherProfile.objects.create(
            user=cls.teacher_user, staff_id="SUB-100"
        )
        TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.class_a,
            subject=cls.maths,
            academic_year=cls.year,
        )

        cls.other_teacher_user = make_user(
            "teacher", username="sub_other_teacher"
        )
        cls.other_teacher = TeacherProfile.objects.create(
            user=cls.other_teacher_user, staff_id="SUB-200"
        )
        TeacherAssignment.objects.create(
            teacher=cls.other_teacher,
            school_class=cls.class_b,
            subject=cls.science,
            academic_year=cls.year,
        )

        cls.student = make_student_user(cls.class_a, admission_number="SUB-101")
        cls.classmate = make_student_user(
            cls.class_a, admission_number="SUB-102"
        )
        cls.other_student = make_student_user(
            cls.class_b, admission_number="SUB-103"
        )

        parent_user = make_user("parent", username="sub_parent")
        cls.parent = ParentProfile.objects.create(user=parent_user)
        StudentGuardian.objects.create(
            student=cls.student, parent=cls.parent, is_primary=True
        )

    def setUp(self):
        self.client.force_login(self.student.user)

    # -- helpers ---------------------------------------------------------

    def _homework(
        self,
        *,
        status=Homework.Status.PUBLISHED,
        school_class=None,
        due_date=None,
        title="Fractions worksheet",
    ) -> Homework:
        return Homework.objects.create(
            title=title,
            description="Show all working.",
            subject=self.maths,
            school_class=school_class or self.class_a,
            term=self.term,
            set_by=self.teacher_user,
            due_date=due_date or (datetime.date.today() + datetime.timedelta(days=3)),
            status=status,
            published_at=timezone.now()
            if status == Homework.Status.PUBLISHED
            else None,
        )

    def _submit(self, homework, *, note="", files=None, remove_ids=None, follow=True):
        data = {"note": note}
        if files:
            data["files"] = files
        if remove_ids:
            data["remove_attachment"] = remove_ids
        return self.client.post(
            reverse("homework:submit", args=[homework.pk]),
            data,
            follow=follow,
        )

    # -- handing in ------------------------------------------------------

    def test_student_submits_note_and_file(self):
        homework = self._homework()

        response = self._submit(homework, note="Done, see the photo.", files=[png()])

        self.assertRedirects(
            response, reverse("homework:detail", args=[homework.pk])
        )
        self.assertContains(response, "Homework submitted.")
        submission = HomeworkSubmission.objects.get(
            homework=homework, student=self.student
        )
        self.assertFalse(submission.is_late)
        self.assertEqual(submission.note, "Done, see the photo.")
        self.assertEqual(submission.attachments.count(), 1)
        attachment = submission.attachments.first()
        self.assertEqual(attachment.original_name, "answer.png")
        self.assertTrue(attachment.file.storage.exists(attachment.file.name))

        self.assertTrue(
            AuditLog.objects.filter(
                action="homework.submitted", object_id=str(submission.pk)
            ).exists()
        )
        self.assertContains(response, "Submitted")
        self.assertContains(response, "answer.png")

    def test_empty_submission_is_refused(self):
        homework = self._homework()

        response = self._submit(homework, note="   ")

        self.assertContains(
            response, "Attach a file or write a note before submitting."
        )
        self.assertFalse(
            HomeworkSubmission.objects.filter(homework=homework).exists()
        )

    def test_note_without_a_file_is_enough(self):
        homework = self._homework()

        self._submit(homework, note="I did it in my exercise book.")

        self.assertTrue(
            HomeworkSubmission.objects.filter(
                homework=homework, student=self.student
            ).exists()
        )

    def test_draft_homework_is_not_open_for_submissions(self):
        homework = self._homework(status=Homework.Status.DRAFT)

        response = self.client.post(
            reverse("homework:submit", args=[homework.pk]), {"note": "x"}
        )

        self.assertEqual(response.status_code, 404)

    def test_another_class_cannot_submit(self):
        homework = self._homework()
        self.client.force_login(self.other_student.user)

        response = self.client.post(
            reverse("homework:submit", args=[homework.pk]), {"note": "x"}
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            HomeworkSubmission.objects.filter(homework=homework).exists()
        )

    def test_parents_and_teachers_cannot_submit(self):
        homework = self._homework()
        for user in (self.parent.user, self.teacher_user):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.post(
                    reverse("homework:submit", args=[homework.pk]),
                    {"note": "x"},
                )
                self.assertEqual(response.status_code, 403)
        self.assertFalse(
            HomeworkSubmission.objects.filter(homework=homework).exists()
        )

    def test_resubmission_updates_in_place(self):
        homework = self._homework()
        self._submit(homework, note="First try.", files=[png()])
        submission = HomeworkSubmission.objects.get(homework=homework)
        old_attachment = submission.attachments.first()
        old_name = old_attachment.file.name
        first_submitted_at = submission.submitted_at

        response = self._submit(
            homework,
            note="Second try.",
            files=[pdf()],
            remove_ids=[old_attachment.pk],
        )

        submission.refresh_from_db()
        self.assertEqual(HomeworkSubmission.objects.filter(homework=homework).count(), 1)
        self.assertEqual(submission.note, "Second try.")
        self.assertGreaterEqual(submission.submitted_at, first_submitted_at)
        self.assertEqual(submission.attachments.count(), 1)
        self.assertEqual(submission.attachments.first().original_name, "answer.pdf")
        self.assertFalse(old_attachment.file.storage.exists(old_name))
        self.assertContains(response, "Your submission was updated.")
        self.assertTrue(
            AuditLog.objects.filter(
                action="homework.resubmitted", object_id=str(submission.pk)
            ).exists()
        )

    def test_late_submission_is_accepted_and_flagged(self):
        homework = self._homework(
            due_date=datetime.date.today() - datetime.timedelta(days=2)
        )

        response = self._submit(homework, note="Sorry it is late.")

        submission = HomeworkSubmission.objects.get(homework=homework)
        self.assertTrue(submission.is_late)
        self.assertContains(
            response,
            "the due date had passed, so it is marked late",
        )
        self.assertContains(response, "Submitted late")

    def test_a_late_submission_can_no_longer_be_changed(self):
        homework = self._homework(
            due_date=datetime.date.today() - datetime.timedelta(days=2)
        )
        self._submit(homework, note="Late but done.")

        response = self._submit(homework, note="Changed my mind.")

        self.assertContains(
            response,
            "The due date has passed, so this submission can no longer be changed.",
        )
        submission = HomeworkSubmission.objects.get(homework=homework)
        self.assertEqual(submission.note, "Late but done.")

    def test_an_on_time_submission_cannot_be_changed_once_the_due_date_moves(self):
        homework = self._homework(due_date=datetime.date.today())
        self._submit(homework, note="On time.")
        self.assertFalse(HomeworkSubmission.objects.get(homework=homework).is_late)

        homework.due_date = datetime.date.today() - datetime.timedelta(days=1)
        homework.save(update_fields=["due_date"])

        response = self._submit(homework, note="Too late now.")

        self.assertContains(
            response,
            "The due date has passed, so this submission can no longer be changed.",
        )
        self.assertEqual(
            HomeworkSubmission.objects.get(homework=homework).note, "On time."
        )

    def test_six_files_are_refused(self):
        homework = self._homework()

        response = self._submit(homework, files=[png(f"a{i}.png") for i in range(6)])

        self.assertContains(response, "at most 5 files")
        self.assertFalse(
            HomeworkSubmission.objects.filter(homework=homework).exists()
        )

    def test_a_executable_is_refused(self):
        homework = self._homework()
        exe = SimpleUploadedFile(
            "virus.exe", b"MZ\x90\x00", content_type="application/x-msdownload"
        )

        response = self._submit(homework, files=[exe])

        self.assertContains(response, "Unsupported submission file")
        self.assertFalse(
            HomeworkSubmission.objects.filter(homework=homework).exists()
        )

    def test_an_oversized_file_is_refused(self):
        homework = self._homework()
        big = SimpleUploadedFile(
            "big.png",
            b"\x00" * (11 * 1024 * 1024),
            content_type="image/png",
        )

        response = self._submit(homework, files=[big])

        self.assertContains(response, "exceeds the 10 MB limit")
        self.assertFalse(
            HomeworkSubmission.objects.filter(homework=homework).exists()
        )

    def test_a_removed_file_is_deleted_from_storage(self):
        homework = self._homework()
        self._submit(homework, files=[png()])
        attachment = HomeworkSubmission.objects.get(homework=homework).attachments.first()
        name = attachment.file.name

        self._submit(homework, note="Removed the photo, wrote it instead.", remove_ids=[attachment.pk])

        submission = HomeworkSubmission.objects.get(homework=homework)
        self.assertEqual(submission.attachments.count(), 0)
        self.assertFalse(attachment.file.storage.exists(name))

    # -- file access -----------------------------------------------------

    def test_the_student_can_open_their_own_file(self):
        homework = self._homework()
        self._submit(homework, files=[png()])
        submission = HomeworkSubmission.objects.get(homework=homework)
        attachment = submission.attachments.first()
        url = reverse(
            "homework:submission_file",
            args=[homework.pk, submission.pk, attachment.pk],
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

        download = self.client.get(url + "?download=1")
        self.assertIn("attachment", download["Content-Disposition"])

    def test_a_classmate_cannot_open_someone_elses_file(self):
        homework = self._homework()
        self._submit(homework, files=[png()])
        submission = HomeworkSubmission.objects.get(homework=homework)
        attachment = submission.attachments.first()

        self.client.force_login(self.classmate.user)
        response = self.client.get(
            reverse(
                "homework:submission_file",
                args=[homework.pk, submission.pk, attachment.pk],
            )
        )

        self.assertEqual(response.status_code, 403)

    def test_a_parent_can_open_their_childs_file(self):
        homework = self._homework()
        self._submit(homework, files=[png()])
        submission = HomeworkSubmission.objects.get(homework=homework)
        attachment = submission.attachments.first()

        self.client.force_login(self.parent.user)
        response = self.client.get(
            reverse(
                "homework:submission_file",
                args=[homework.pk, submission.pk, attachment.pk],
            )
        )

        self.assertEqual(response.status_code, 200)

    def test_the_setting_teacher_can_open_a_submission_file(self):
        homework = self._homework()
        self._submit(homework, files=[png()])
        submission = HomeworkSubmission.objects.get(homework=homework)
        attachment = submission.attachments.first()
        url = reverse(
            "homework:submission_file",
            args=[homework.pk, submission.pk, attachment.pk],
        )

        self.client.force_login(self.teacher_user)
        self.assertEqual(self.client.get(url).status_code, 200)

        self.client.force_login(self.other_teacher_user)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_an_unpublished_homework_hides_the_file_from_students(self):
        homework = self._homework()
        self._submit(homework, files=[png()])
        submission = HomeworkSubmission.objects.get(homework=homework)
        attachment = submission.attachments.first()
        url = reverse(
            "homework:submission_file",
            args=[homework.pk, submission.pk, attachment.pk],
        )

        homework.status = Homework.Status.DRAFT
        homework.save(update_fields=["status"])

        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.teacher_user)
        self.assertEqual(self.client.get(url).status_code, 200)

    # -- what the pages show --------------------------------------------

    def test_the_detail_page_shows_not_submitted_before_the_due_date(self):
        homework = self._homework()

        response = self.client.get(reverse("homework:detail", args=[homework.pk]))

        self.assertContains(response, "Not submitted yet")
        self.assertContains(response, 'name="files"')

    def test_the_detail_page_marks_a_missing_submission_overdue(self):
        homework = self._homework(
            due_date=datetime.date.today() - datetime.timedelta(days=1)
        )

        response = self.client.get(reverse("homework:detail", args=[homework.pk]))

        self.assertContains(response, "Not submitted — overdue")
        self.assertContains(response, "anything you hand in now will be marked late")

    def test_a_parent_sees_the_status_but_no_form(self):
        homework = self._homework()
        self._submit(homework, note="Finished.", files=[png()])

        self.client.force_login(self.parent.user)
        response = self.client.get(reverse("homework:detail", args=[homework.pk]))

        self.assertContains(response, "Finished.")
        self.assertContains(response, "Submitted")
        self.assertContains(response, self.student.user.get_full_name())
        self.assertNotContains(response, 'name="files"')

    def test_the_teacher_page_lists_who_handed_in(self):
        homework = self._homework()
        self._submit(homework, note="Here you go.")

        self.client.force_login(self.teacher_user)
        response = self.client.get(
            reverse("homework:teacher_submissions", args=[homework.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["roster_size"], 2)
        self.assertEqual(response.context["submitted_count"], 1)
        self.assertEqual(response.context["missing_count"], 1)
        self.assertContains(response, self.student.user.get_full_name())
        self.assertContains(response, self.classmate.user.get_full_name())
        self.assertContains(response, "On time")
        self.assertContains(response, "Not submitted")
        self.assertContains(response, "Here you go.")

    def test_the_teacher_page_flags_late_work(self):
        homework = self._homework(
            due_date=datetime.date.today() - datetime.timedelta(days=1)
        )
        self._submit(homework, note="Late.")

        self.client.force_login(self.teacher_user)
        response = self.client.get(
            reverse("homework:teacher_submissions", args=[homework.pk])
        )

        self.assertEqual(response.context["late_count"], 1)
        self.assertContains(response, "Late")

    def test_only_the_setting_teacher_can_review_submissions(self):
        homework = self._homework()
        url = reverse("homework:teacher_submissions", args=[homework.pk])

        self.client.force_login(self.other_teacher_user)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.student.user)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_the_teacher_list_shows_the_submission_count(self):
        homework = self._homework()
        self._submit(homework, note="Done.")

        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse("homework:teacher_list"))

        row = next(r for r in response.context["rows"] if r.pk == homework.pk)
        self.assertEqual(row.submission_count, 1)

    # -- deletion --------------------------------------------------------

    def test_deleting_a_draft_removes_submission_files_too(self):
        homework = self._homework()
        self._submit(homework, files=[png()])
        submission = HomeworkSubmission.objects.get(homework=homework)
        attachment = submission.attachments.first()
        name = attachment.file.name
        self.assertTrue(attachment.file.storage.exists(name))

        homework.status = Homework.Status.DRAFT
        homework.save(update_fields=["status"])
        services.delete_homework(homework=homework, actor=self.teacher_user)

        self.assertFalse(attachment.file.storage.exists(name))
        self.assertFalse(HomeworkSubmission.objects.filter(pk=submission.pk).exists())
