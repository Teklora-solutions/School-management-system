"""
apps/students/tests/test_students.py
--------------------------------------
Module 1 — students, bulk import, and class promotion.

Covers:
  - single add creates the student + linked parent accounts
  - bulk import: template → validate → preview → commit
      * idempotent re-upload (admission number is the key)
      * row-level errors reported (and downloadable)
      * ALL_OR_NOTHING (default) blocks when errors exist
      * PARTIAL commits valid rows only
  - promotion: dry-run, published-results gate, audited override,
    transactional commit and idempotency
"""

from __future__ import annotations

import datetime
import io
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.accounts.models import ParentProfile, User
from apps.core.models import AuditLog
from apps.results.models import ResultSheet
from apps.students.models import (
    BulkImportJob,
    PromotionBatch,
    PromotionRecord,
    Student,
    StudentGuardian,
)
from apps.students.services import (
    BULK_TEMPLATE_HEADERS,
    build_error_report_bytes,
    commit_bulk_import,
    commit_promotion,
    create_student_with_guardian,
    preview_promotion,
)
from apps.students.tasks import validate_bulk_import


class _FakeHTML:
    def __init__(self, string=None, base_url=None):
        self.string = string

    def write_pdf(self):
        return b"%PDF-1.4\nstub\n"


#: The download tests below assert the PDF success path, so the engine is
#: stubbed rather than depending on this host's GTK/Pango libraries.
_engine = patch.dict(sys.modules, {"weasyprint": SimpleNamespace(HTML=_FakeHTML)})


def _student_row(**overrides) -> dict:
    row = {
        "admission_number": "ADM1001",
        "first_name": "Ama",
        "last_name": "Mensah",
        "email": "ama.mensah@student.school",
        "gender": "F",
        "date_of_birth": "2014-05-12",
        "stream": "",
        "guardian_first_name": "Kwame",
        "guardian_last_name": "Mensah",
        "guardian_email": "kwame.mensah@example.com",
        "guardian_phone": "0244000000",
        "guardian_relationship": "father",
    }
    row.update(overrides)
    return row


def _xlsx_bytes(rows: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(BULK_TEMPLATE_HEADERS)
    for row in rows:
        ws.append([row.get(header, "") for header in BULK_TEMPLATE_HEADERS])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_import_job(
    *, rows, target_class, admin, mode=BulkImportJob.Mode.ALL_OR_NOTHING
):
    upload = SimpleUploadedFile(
        "students.xlsx",
        _xlsx_bytes(rows),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    job = BulkImportJob.objects.create(
        uploaded_by=admin,
        file=upload,
        target_class=target_class,
        mode=mode,
    )
    validate_bulk_import(job.pk)
    job.refresh_from_db()
    return job


class StudentCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.school_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )

    def test_creates_student_and_linked_guardian_accounts(self):
        with patch("apps.students.services.enqueue_credentials_email") as queued:
            student = create_student_with_guardian(
                admission_number="adm1001",
                first_name="Ama",
                last_name="Mensah",
                email="ama@student.school",
                gender="F",
                date_of_birth=datetime.date(2014, 5, 12),
                current_class=self.school_class,
                guardian_first_name="Kwame",
                guardian_last_name="Mensah",
                guardian_email="kwame@example.com",
                guardian_phone="0244000000",
                guardian_relationship=StudentGuardian.Relationship.FATHER,
            )

        self.assertEqual(student.admission_number, "ADM1001")
        self.assertEqual(student.current_class, self.school_class)
        self.assertEqual(student.status, Student.Status.ACTIVE)

        student_user = student.user
        self.assertEqual(student_user.role, User.Role.STUDENT)
        self.assertEqual(student_user.username, "adm1001")
        self.assertTrue(student_user.check_password("ADM1001"))
        self.assertTrue(student_user.must_change_password)

        parent_user = ParentProfile.objects.get().user
        self.assertEqual(parent_user.role, User.Role.PARENT)
        self.assertTrue(parent_user.must_change_password)
        self.assertEqual(parent_user.email, "kwame@example.com")

        guardian = StudentGuardian.objects.get(student=student)
        self.assertEqual(guardian.parent.user, parent_user)
        self.assertEqual(guardian.relationship, StudentGuardian.Relationship.FATHER)
        self.assertTrue(guardian.is_primary)

        # Student + parent credential mails queued.
        self.assertEqual(queued.call_count, 2)
        self.assertTrue(
            AuditLog.objects.filter(
                action="student.created", object_id=str(student.pk)
            ).exists()
        )

    def test_existing_parent_account_is_reused(self):
        parent_user = User.objects.create_user(
            username="existing_parent",
            email="existing@example.com",
            password="Testpass1!",
            role=User.Role.PARENT,
            must_change_password=False,
        )
        parent_profile = ParentProfile.objects.create(user=parent_user)

        with patch("apps.students.services.enqueue_credentials_email") as queued:
            student = create_student_with_guardian(
                admission_number="ADM1002",
                first_name="Kofi",
                last_name="Owusu",
                email="kofi@student.school",
                current_class=self.school_class,
                guardian_first_name="Existing",
                guardian_last_name="Parent",
                guardian_email="existing@example.com",
            )

        self.assertEqual(ParentProfile.objects.count(), 1)
        self.assertEqual(
            StudentGuardian.objects.get(student=student).parent, parent_profile
        )
        # Only the student's credentials are emailed.
        self.assertEqual(queued.call_count, 1)

    def test_duplicate_admission_number_rejected(self):
        create_student_with_guardian(
            admission_number="ADM1003",
            first_name="A",
            last_name="B",
            email="a@student.school",
            current_class=self.school_class,
            send_credentials=False,
        )
        with self.assertRaises(ValidationError) as ctx:
            create_student_with_guardian(
                admission_number="ADM1003",
                first_name="C",
                last_name="D",
                email="c@student.school",
                current_class=self.school_class,
                send_credentials=False,
            )
        self.assertIn("admission_number", ctx.exception.message_dict)


class BulkImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.school_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.admin = User.objects.create_user(
            username="import_admin",
            email="import_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )

    def setUp(self):
        self._media = tempfile.TemporaryDirectory()
        self.addCleanup(self._media.cleanup)

    def _job(self, rows, mode=BulkImportJob.Mode.ALL_OR_NOTHING):
        with override_settings(MEDIA_ROOT=self._media.name):
            return _make_import_job(
                rows=rows,
                target_class=self.school_class,
                admin=self.admin,
                mode=mode,
            )

    def test_valid_rows_validate_and_commit(self):
        rows = [
            _student_row(),
            _student_row(
                admission_number="ADM1002",
                email="kofi@student.school",
                first_name="Kofi",
                guardian_email="guardian2@example.com",
            ),
        ]
        with override_settings(MEDIA_ROOT=self._media.name):
            job = self._job(rows)
            self.assertEqual(job.status, BulkImportJob.Status.READY)
            self.assertEqual(job.summary["valid"], 2)
            self.assertEqual(job.summary["errors"], 0)

            with patch("apps.students.services.enqueue_credentials_email"):
                commit_bulk_import(job=job, actor=self.admin)
            job.refresh_from_db()

        self.assertEqual(job.status, BulkImportJob.Status.COMMITTED)
        self.assertEqual(job.committed_count, 2)
        self.assertEqual(Student.objects.count(), 2)

    def test_bad_rows_are_reported_and_all_or_nothing_blocks(self):
        rows = [
            _student_row(),
            _student_row(
                admission_number="ADM-BAD",
                email="bad@student.school",
                last_name="",
                gender="X",
            ),
        ]
        job = self._job(rows)

        self.assertEqual(job.status, BulkImportJob.Status.READY)
        self.assertEqual(job.summary["errors"], 1)
        self.assertEqual(job.summary["valid"], 1)

        bad_row = job.error_rows[0]
        self.assertEqual(bad_row["_row"], 3)
        self.assertTrue(any("last_name" in e for e in bad_row["_errors"]))
        self.assertTrue(any("gender" in e for e in bad_row["_errors"]))

        # Downloadable error report contains the row + messages.
        report = load_workbook(io.BytesIO(build_error_report_bytes(job)))
        sheet = report.active
        values = list(sheet.values)
        self.assertIn("errors", values[0])
        self.assertTrue(any("ADM-BAD" in str(cell) for cell in values[1]))

        with patch(
            "apps.students.services.enqueue_credentials_email"
        ), self.assertRaises(ValidationError):
            commit_bulk_import(job=job, actor=self.admin)
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.Status.READY)
        self.assertEqual(Student.objects.count(), 0)

    def test_partial_mode_commits_good_rows_only(self):
        rows = [
            _student_row(),
            _student_row(
                admission_number="ADM-BAD",
                email="bad@student.school",
                first_name="",
            ),
        ]
        job = self._job(rows, mode=BulkImportJob.Mode.PARTIAL)

        with patch("apps.students.services.enqueue_credentials_email"):
            commit_bulk_import(job=job, actor=self.admin)
        job.refresh_from_db()

        self.assertEqual(job.status, BulkImportJob.Status.COMMITTED)
        self.assertEqual(job.committed_count, 1)
        self.assertEqual(job.summary["created"], 1)
        self.assertEqual(job.summary["errors"], 1)
        self.assertEqual(
            list(Student.objects.values_list("admission_number", flat=True)),
            ["ADM1001"],
        )

    def test_reupload_is_idempotent_on_admission_number(self):
        rows = [_student_row()]
        job = self._job(rows)
        with patch("apps.students.services.enqueue_credentials_email"):
            commit_bulk_import(job=job, actor=self.admin)
        self.assertEqual(Student.objects.count(), 1)

        second_job = self._job(rows)
        self.assertEqual(second_job.status, BulkImportJob.Status.READY)
        self.assertEqual(second_job.summary["skipped_existing"], 1)
        self.assertEqual(second_job.summary["valid"], 0)
        self.assertEqual(second_job.error_rows, [])

        with patch("apps.students.services.enqueue_credentials_email"):
            commit_bulk_import(job=second_job, actor=self.admin)
        second_job.refresh_from_db()

        self.assertEqual(second_job.committed_count, 0)
        self.assertEqual(second_job.summary["skipped_existing"], 1)
        self.assertEqual(Student.objects.count(), 1)

    def test_duplicate_admission_number_inside_file_is_an_error(self):
        rows = [
            _student_row(),
            _student_row(email="other@student.school"),
        ]
        job = self._job(rows)
        self.assertEqual(job.summary["errors"], 1)
        self.assertIn("Duplicate admission_number", job.error_rows[0]["_errors"][0])

    def test_download_endpoints(self):
        self.client.force_login(self.admin)
        template_resp = self.client.get(reverse("students:bulk_import_template"))
        self.assertEqual(template_resp.status_code, 200)
        self.assertIn(
            "spreadsheetml",
            template_resp["Content-Type"],
        )

        job = self._job([_student_row(admission_number="ADM-BAD", first_name="")])
        error_resp = self.client.get(
            reverse("students:bulk_import_errors", args=[job.pk])
        )
        self.assertEqual(error_resp.status_code, 200)
        self.assertIn("spreadsheetml", error_resp["Content-Type"])

    def _upload(self):
        return SimpleUploadedFile(
            "students.xlsx",
            _xlsx_bytes([_student_row()]),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_upload_view_starts_the_validation_task(self):
        """The view, not just the service — a keyword mismatch here used to
        raise TypeError and no student could ever be imported."""
        self.client.force_login(self.admin)
        with (
            patch("django_q.tasks.async_task") as queue_task,
            override_settings(MEDIA_ROOT=self._media.name),
        ):
            response = self.client.post(
                reverse("students:bulk_import"),
                {
                    "file": self._upload(),
                    "target_class": self.school_class.pk,
                    "mode": BulkImportJob.Mode.ALL_OR_NOTHING,
                },
            )

        job = BulkImportJob.objects.get()
        self.assertRedirects(
            response, reverse("students:bulk_import_preview", args=[job.pk])
        )
        self.assertEqual(job.target_class, self.school_class)
        self.assertEqual(job.uploaded_by, self.admin)
        self.assertEqual(job.status, BulkImportJob.Status.PENDING)
        queue_task.assert_called_once_with(
            "apps.students.tasks.validate_bulk_import", job.pk
        )

        # The queued task itself still turns the upload into a preview.
        with override_settings(MEDIA_ROOT=self._media.name):
            validate_bulk_import(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.Status.READY)
        self.assertEqual(job.summary["valid"], 1)
        self.assertEqual(job.summary["errors"], 0)

    def test_upload_view_requires_a_target_class(self):
        self.client.force_login(self.admin)
        with (
            patch("django_q.tasks.async_task"),
            override_settings(MEDIA_ROOT=self._media.name),
        ):
            response = self.client.post(
                reverse("students:bulk_import"),
                {
                    "file": self._upload(),
                    "mode": BulkImportJob.Mode.ALL_OR_NOTHING,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "target_class", "This field is required."
        )
        self.assertEqual(BulkImportJob.objects.count(), 0)

    def test_preview_page_renders_error_rows_and_blocked_commit(self):
        """The preview template reads the validator's ``_``-prefixed keys, which
        Django templates reject outright — so this page used to 500."""
        job = self._job(
            [
                _student_row(),
                _student_row(
                    admission_number="ADM-BAD",
                    email="bad@student.school",
                    last_name="",
                    gender="X",
                ),
            ]
        )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("students:bulk_import_preview", args=[job.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ADM-BAD")
        self.assertContains(response, "Rows with Errors (1)")
        self.assertContains(response, "Commit is blocked")
        self.assertNotContains(response, "btn-commit-import")


class PromotionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="promo_admin",
            email="promo_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.from_year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.to_year = AcademicYear.objects.create(
            name="2026/2027",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 7, 31),
        )
        cls.term = Term.objects.create(
            academic_year=cls.from_year,
            name="term_3",
            start_date=datetime.date(2026, 5, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.subject = Subject.objects.create(name="Mathematics", code="MATH")

        cls.from_grade5 = SchoolClass.objects.create(
            academic_year=cls.from_year, name="Grade 5", level_order=5
        )
        cls.from_grade6 = SchoolClass.objects.create(
            academic_year=cls.from_year, name="Grade 6", level_order=6
        )
        cls.to_grade5 = SchoolClass.objects.create(
            academic_year=cls.to_year, name="Grade 5", level_order=5
        )
        cls.to_grade6 = SchoolClass.objects.create(
            academic_year=cls.to_year, name="Grade 6", level_order=6
        )

    def _student(self, admission_number: str, school_class: SchoolClass) -> Student:
        user = User.objects.create_user(
            username=f"stu_{admission_number.lower()}",
            email=f"{admission_number.lower()}@student.school",
            password="Testpass1!",
            role=User.Role.STUDENT,
            first_name="Student",
            last_name=admission_number,
            must_change_password=False,
        )
        return Student.objects.create(
            user=user,
            admission_number=admission_number,
            current_class=school_class,
            status=Student.Status.ACTIVE,
        )

    def _publish_results(self):
        ResultSheet.objects.create(
            school_class=self.from_grade5,
            subject=self.subject,
            term=self.term,
            status=ResultSheet.Status.PUBLISHED,
        )

    def test_preview_reports_promote_repeat_graduate(self):
        self._student("P5-001", self.from_grade5)
        self._student("R5-001", self.from_grade5)
        self._student("G6-001", self.from_grade6)

        preview = preview_promotion(
            from_year=self.from_year,
            to_year=self.to_year,
            term=self.term,
            repeat_admission_numbers={"R5-001"},
        )
        self.assertEqual(preview["counts"], {"promote": 1, "repeat": 1, "graduate": 1})
        self.assertFalse(preview["results_published"])

        actions = {m["admission_number"]: m for m in preview["moves"]}
        self.assertEqual(actions["P5-001"]["action"], PromotionRecord.Action.PROMOTE)
        self.assertEqual(actions["P5-001"]["to_class_id"], self.to_grade6.pk)
        self.assertEqual(actions["R5-001"]["action"], PromotionRecord.Action.REPEAT)
        self.assertEqual(actions["R5-001"]["to_class_id"], self.to_grade5.pk)
        self.assertEqual(actions["G6-001"]["action"], PromotionRecord.Action.GRADUATE)
        self.assertIsNone(actions["G6-001"]["to_class_id"])

    def test_commit_is_blocked_when_results_unpublished(self):
        self._student("P5-001", self.from_grade5)
        preview = preview_promotion(
            from_year=self.from_year, to_year=self.to_year, term=self.term
        )
        self.assertFalse(preview["results_published"])

        with self.assertRaises(ValidationError) as ctx:
            commit_promotion(
                from_year=self.from_year, to_year=self.to_year, term=self.term
            )
        self.assertIn("results are not fully published", str(ctx.exception))
        self.assertEqual(PromotionBatch.objects.count(), 0)

        # Override still requires a reason.
        with self.assertRaises(ValidationError):
            commit_promotion(
                from_year=self.from_year,
                to_year=self.to_year,
                term=self.term,
                override_unpublished=True,
                override_reason="  ",
            )
        self.assertEqual(PromotionBatch.objects.count(), 0)

    def test_override_is_audited_and_commit_is_idempotent(self):
        student = self._student("P5-001", self.from_grade5)

        batch = commit_promotion(
            from_year=self.from_year,
            to_year=self.to_year,
            term=self.term,
            override_unpublished=True,
            override_reason="Term results delayed by exam board",
            actor=self.admin,
        )
        self.assertTrue(batch.is_committed)
        self.assertTrue(batch.override_unpublished)
        student.refresh_from_db()
        self.assertEqual(student.current_class, self.to_grade6)
        self.assertEqual(student.status, Student.Status.ACTIVE)
        self.assertEqual(batch.records.count(), 1)

        self.assertTrue(
            AuditLog.objects.filter(action="student.promotion_committed").exists()
        )
        override_log = AuditLog.objects.get(
            action="student.promotion_override_unpublished"
        )
        self.assertEqual(
            override_log.change_summary["reason"],
            "Term results delayed by exam board",
        )

        # Re-running returns the same committed batch — no duplicate records.
        again = commit_promotion(
            from_year=self.from_year,
            to_year=self.to_year,
            term=self.term,
            override_unpublished=True,
            override_reason="Term results delayed by exam board",
            actor=self.admin,
        )
        self.assertEqual(again.pk, batch.pk)
        self.assertEqual(PromotionBatch.objects.count(), 1)
        self.assertEqual(PromotionRecord.objects.count(), 1)
        student.refresh_from_db()
        self.assertEqual(student.current_class, self.to_grade6)

    def test_commit_without_override_when_results_published(self):
        self._publish_results()
        grade5 = self._student("P5-001", self.from_grade5)
        repeat_student = self._student("R5-001", self.from_grade5)
        graduate = self._student("G6-001", self.from_grade6)

        batch = commit_promotion(
            from_year=self.from_year,
            to_year=self.to_year,
            term=self.term,
            repeat_admission_numbers={"R5-001"},
            actor=self.admin,
        )
        self.assertTrue(batch.is_committed)
        self.assertFalse(batch.override_unpublished)

        grade5.refresh_from_db()
        repeat_student.refresh_from_db()
        graduate.refresh_from_db()
        self.assertEqual(grade5.current_class, self.to_grade6)
        self.assertEqual(grade5.status, Student.Status.ACTIVE)
        self.assertEqual(repeat_student.current_class, self.to_grade5)
        self.assertEqual(repeat_student.status, Student.Status.REPEATING)
        self.assertIsNone(graduate.current_class)
        self.assertEqual(graduate.status, Student.Status.GRADUATED)


class StudentViewSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="student_view_admin",
            email="student_view_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.school_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )

    def test_admin_creates_student_with_guardian_via_view(self):
        self.client.force_login(self.admin)
        with patch("apps.students.services.enqueue_credentials_email"):
            resp = self.client.post(
                reverse("students:add"),
                {
                    "first_name": "Ama",
                    "last_name": "Mensah",
                    "email": "ama.view@student.school",
                    "admission_number": "ADM-VIEW-1",
                    "gender": "F",
                    "date_of_birth": "2014-05-12",
                    "current_class": self.school_class.pk,
                    "guardian_first_name": "Kwame",
                    "guardian_last_name": "Mensah",
                    "guardian_email": "kwame.view@example.com",
                    "relationship": "father",
                },
            )
        self.assertEqual(resp.status_code, 302)
        student = Student.objects.get(admission_number="ADM-VIEW-1")
        self.assertTrue(StudentGuardian.objects.filter(student=student).exists())
        self.assertTrue(
            ParentProfile.objects.filter(user__email="kwame.view@example.com").exists()
        )

    def _payload(self, admission_number, **overrides):
        base = {
            "first_name": "Ash",
            "last_name": "Over",
            "email": f"{admission_number.lower()}@student.school",
            "admission_number": admission_number,
            "gender": "F",
            "current_class": self.school_class.pk,
            "guardian_first_name": "Kojo",
            "guardian_last_name": "Over",
            "guardian_email": f"{admission_number.lower()}.guardian@example.com",
            "relationship": "guardian",
            "must_change_password": "on",
        }
        base.update(overrides)
        return base

    def test_blank_password_still_means_the_admission_number(self):
        """The bulk-import convention has to survive the new optional field."""
        self.client.force_login(self.admin)
        with patch("apps.students.services.enqueue_credentials_email"):
            resp = self.client.post(
                reverse("students:add"), self._payload("ADM-BLANK-1", password="")
            )
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username="adm-blank-1")
        self.assertTrue(user.check_password("ADM-BLANK-1"))
        self.assertTrue(user.must_change_password)

    def test_admin_can_override_one_students_password(self):
        self.client.force_login(self.admin)
        with patch("apps.students.services.enqueue_credentials_email"):
            resp = self.client.post(
                reverse("students:add"),
                self._payload(
                    "ADM-OVR-1",
                    password="Laptop-2026",
                    password_confirm="Laptop-2026",
                ),
            )
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username="adm-ovr-1")
        self.assertTrue(user.check_password("Laptop-2026"))
        self.assertFalse(user.check_password("ADM-OVR-1"))

    def test_override_without_confirmation_is_rejected(self):
        self.client.force_login(self.admin)
        with patch("apps.students.services.enqueue_credentials_email") as queued:
            resp = self.client.post(
                reverse("students:add"),
                self._payload(
                    "ADM-OVR-2",
                    password="Laptop-2026",
                    password_confirm="Laptop-2027",
                ),
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("password_confirm", resp.context["form"].errors)
        self.assertContains(resp, "Confirm password")
        self.assertFalse(Student.objects.filter(admission_number="ADM-OVR-2").exists())
        queued.assert_not_called()

    def test_unticking_force_change_lets_the_student_sign_straight_in(self):
        self.client.force_login(self.admin)
        payload = self._payload(
            "ADM-OVR-3", password="Laptop-2026", password_confirm="Laptop-2026"
        )
        payload.pop("must_change_password")
        with patch("apps.students.services.enqueue_credentials_email"):
            resp = self.client.post(reverse("students:add"), payload)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(User.objects.get(username="adm-ovr-3").must_change_password)

    def test_blank_admission_form_downloads(self):
        self.client.force_login(self.admin)
        with _engine:
            resp = self.client.get(reverse("students:blank_pdf"))
        self.assertEqual(resp.status_code, 200)
        # No HTML is ever served in place of the PDF; the engine is stubbed so
        # this asserts the success path rather than this host's native libraries.
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn("filename=", resp["Content-Disposition"])
