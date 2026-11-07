"""
apps/students/tests/test_students.py
--------------------------------------
Module 1 — students, bulk import, and class promotion.

Covers:
  - single add creates the student + linked parent accounts
  - editing an enrolled pupil, including the admission-number/username rename
  - disabling and re-enabling a pupil's portal login, including the session that
    a live pupil is already sitting in
  - deleting a pupil: what blocks it, what disappears with them, and the fact
    that a refusal must leave no audit trail claiming it happened
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
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from apps.academics.models import AcademicYear, SchoolClass, Stream, Subject, Term
from apps.accounts.models import ParentProfile, User
from apps.core.models import AuditLog
from apps.finance.models import FeeDiscountRule, FeeStructure, Invoice
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
    delete_student,
    preview_promotion,
    set_student_login_active,
    student_delete_forecast,
    update_student,
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


class StudentEditServiceTests(TestCase):
    """
    ``update_student`` — correcting an enrolled pupil's record.

    The invariant worth defending is the admission number / username pair: the
    login form turns the number printed on the admission letter back into
    ``student_username_for(number)``, so the two must move together or the
    account stops answering to its own paperwork.
    """

    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user(
            username="edit_service_admin",
            email="edit_service_admin@test.school",
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
        cls.class5 = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.class6 = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 6", level_order=6
        )
        cls.blue = Stream.objects.create(school_class=cls.class5, name="Blue")
        cls.gold = Stream.objects.create(school_class=cls.class6, name="Gold")

    def make_student(self, admission_number="ADM/2025/014", **overrides):
        data = {
            "admission_number": admission_number,
            "first_name": "Ama",
            "last_name": "Mensah",
            "email": f"{admission_number.lower().replace('/', '_')}@student.school",
            "gender": "F",
            "date_of_birth": datetime.date(2014, 5, 12),
            "current_class": self.class5,
            "current_stream": self.blue,
            "send_credentials": False,
        }
        data.update(overrides)
        return create_student_with_guardian(**data)

    def edit(self, student, **overrides):
        data = {
            "first_name": "Amaara",
            "last_name": "Mensah-Quaye",
            "email": "amaara@student.school",
            "admission_number": student.admission_number,
            "gender": "F",
            "date_of_birth": datetime.date(2014, 5, 12),
            "enrollment_date": datetime.date(2025, 9, 1),
            "current_class": student.current_class,
            "current_stream": student.current_stream,
            "address": "12 Hill Road",
            "status": Student.Status.ACTIVE,
            "actor": self.actor,
        }
        data.update(overrides)
        return update_student(student=student, **data)

    def test_fields_land_on_the_right_table(self):
        student = self.make_student()
        self.edit(student)
        student.refresh_from_db()

        self.assertEqual(student.user.first_name, "Amaara")
        self.assertEqual(student.user.last_name, "Mensah-Quaye")
        self.assertEqual(student.user.email, "amaara@student.school")
        self.assertEqual(student.address, "12 Hill Road")
        self.assertEqual(student.enrollment_date, datetime.date(2025, 9, 1))

    def test_changing_the_admission_number_renames_the_login_account(self):
        student = self.make_student()
        self.edit(student, admission_number="adm/2025/099")
        student.refresh_from_db()

        self.assertEqual(student.admission_number, "ADM/2025/099")
        self.assertEqual(student.user.username, "adm_2025_099")
        self.assertTrue(
            AuditLog.objects.filter(
                action="student.admission_number_changed", object_id=str(student.pk)
            ).exists()
        )

    def test_the_pupil_signs_in_with_the_new_number_and_not_the_old(self):
        student = self.make_student()
        self.edit(student, admission_number="ADM/2025/099")

        self.assertIsNotNone(
            authenticate(username="adm_2025_099", password="ADM/2025/014")
        )
        self.assertIsNone(authenticate(username="adm_2025_014", password="ADM/2025/014"))

    def test_the_password_is_never_touched_by_an_edit(self):
        """The pupil's password is not this route's business — no reset, no mail."""
        student = self.make_student()
        with patch("apps.students.services.enqueue_credentials_email") as queued:
            self.edit(student, admission_number="ADM/2025/099")
        queued.assert_not_called()
        student.user.refresh_from_db()
        self.assertTrue(student.user.check_password("ADM/2025/014"))

    def test_an_unrelated_edit_leaves_the_username_alone(self):
        """
        The number is stored upper-cased, so typing it in lower case is not a
        rename — and must not go near the username or its availability check.
        """
        student = self.make_student()
        self.edit(student, admission_number="adm/2025/014", first_name="Ama")
        student.user.refresh_from_db()

        self.assertEqual(student.user.username, "adm_2025_014")

    def test_a_rename_onto_a_login_name_someone_else_holds_is_refused(self):
        student = self.make_student()
        squatter = self.make_student(admission_number="ADM2025099")
        squatter.user.username = "adm_2025_099"
        squatter.user.save(update_fields=["username"])

        with self.assertRaises(ValidationError) as ctx:
            self.edit(student, admission_number="ADM/2025/099")
        self.assertIn("admission_number", ctx.exception.message_dict)

        student.refresh_from_db()
        self.assertEqual(student.admission_number, "ADM/2025/014")
        self.assertEqual(student.user.username, "adm_2025_014")

    def test_a_number_another_student_holds_is_refused(self):
        self.make_student(admission_number="ADM/2025/015")
        student = self.make_student()

        with self.assertRaises(ValidationError) as ctx:
            self.edit(student, admission_number="ADM/2025/015")
        self.assertIn("admission_number", ctx.exception.message_dict)

    def test_an_email_another_account_holds_is_refused(self):
        other = self.make_student(admission_number="ADM/2025/016")
        student = self.make_student()

        with self.assertRaises(ValidationError) as ctx:
            self.edit(student, email=other.user.email)
        self.assertIn("email", ctx.exception.message_dict)

    def test_a_stream_from_another_class_is_refused(self):
        """``gold`` belongs to Grade 6, so it cannot follow the pupil into Grade 5."""
        student = self.make_student()
        with self.assertRaises(ValidationError) as ctx:
            self.edit(student, current_class=self.class5, current_stream=self.gold)
        self.assertIn("current_stream", ctx.exception.message_dict)
        student.refresh_from_db()
        self.assertEqual(student.current_stream, self.blue)

    def test_moving_up_a_class_can_take_the_matching_stream(self):
        student = self.make_student()
        self.edit(student, current_class=self.class6, current_stream=self.gold)
        student.refresh_from_db()

        self.assertEqual(student.current_class, self.class6)
        self.assertEqual(student.current_stream, self.gold)

    def test_status_is_corrected_and_audited(self):
        student = self.make_student()
        Student.objects.filter(pk=student.pk).update(status=Student.Status.WITHDRAWN)
        student.refresh_from_db()

        self.edit(student, status=Student.Status.ACTIVE)

        student.refresh_from_db()
        self.assertEqual(student.status, Student.Status.ACTIVE)
        log = AuditLog.objects.get(action="student.updated", object_id=str(student.pk))
        self.assertEqual(log.change_summary["before"]["status"], "withdrawn")
        self.assertEqual(log.change_summary["after"]["status"], "active")

    def test_finance_fields_are_left_alone(self):
        """``sponsor`` and ``payment_plan_notes`` belong to Finance's own editor."""
        student = self.make_student()
        Student.objects.filter(pk=student.pk).update(
            sponsor="PTA Bursary", payment_plan_notes="Two instalments"
        )

        self.edit(student)

        student.refresh_from_db()
        self.assertEqual(student.sponsor, "PTA Bursary")
        self.assertEqual(student.payment_plan_notes, "Two instalments")

    def test_guardian_links_survive_an_edit(self):
        parent_user = User.objects.create_user(
            username="edit_parent",
            email="edit.parent@example.com",
            password="Testpass1!",
            role=User.Role.PARENT,
            must_change_password=False,
        )
        parent = ParentProfile.objects.create(user=parent_user)
        student = self.make_student()
        StudentGuardian.objects.create(
            student=student, parent=parent, relationship="father", is_primary=True
        )

        self.edit(student, admission_number="ADM/2025/099")

        self.assertEqual(
            StudentGuardian.objects.filter(student=student, parent=parent).count(), 1
        )

    def test_the_audit_row_carries_the_whole_before_and_after(self):
        student = self.make_student()
        self.edit(student, admission_number="ADM/2025/099")

        log = AuditLog.objects.get(action="student.updated", object_id=str(student.pk))
        self.assertEqual(
            log.change_summary["before"]["admission_number"], "ADM/2025/014"
        )
        self.assertEqual(log.change_summary["after"]["admission_number"], "ADM/2025/099")
        self.assertEqual(log.change_summary["before"]["username"], "adm_2025_014")
        self.assertEqual(log.change_summary["after"]["username"], "adm_2025_099")
        self.assertEqual(log.change_summary["after"]["class"], "Grade 5")


class StudentEditViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="student_edit_view_admin",
            email="student_edit_view_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.teacher = User.objects.create_user(
            username="student_edit_view_teacher",
            email="student_edit_view_teacher@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        cls.director = User.objects.create_user(
            username="student_edit_view_director",
            email="student_edit_view_director@test.school",
            password="Testpass1!",
            role=User.Role.DIRECTOR,
            must_change_password=False,
        )
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.class5 = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.blue = Stream.objects.create(school_class=cls.class5, name="Blue")
        cls.student = create_student_with_guardian(
            admission_number="ADM/2025/014",
            first_name="Ama",
            last_name="Mensah",
            email="ama.view@student.school",
            gender="F",
            current_class=cls.class5,
            current_stream=cls.blue,
            send_credentials=False,
        )

    def edit_url(self, pk=None):
        return reverse("students:edit", args=[pk or self.student.pk])

    def post_data(self, **overrides):
        data = {
            "first_name": "Ama",
            "last_name": "Mensah",
            "email": "ama.view@student.school",
            "admission_number": "ADM/2025/014",
            "gender": "F",
            "date_of_birth": "2014-05-12",
            "enrollment_date": "2025-09-01",
            "status": Student.Status.ACTIVE,
            "current_class": self.class5.pk,
            "current_stream": self.blue.pk,
            "address": "12 Hill Road",
        }
        data.update(overrides)
        return data

    def test_edit_page_prefills_and_offers_no_password_or_guardian_fields(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.edit_url())

        self.assertEqual(resp.status_code, 200)
        form = resp.context["form"]
        self.assertNotIn("password", form.fields)
        self.assertNotIn("guardian_email", form.fields)
        self.assertNotIn("sponsor", form.fields)
        self.assertEqual(form.initial["admission_number"], "ADM/2025/014")
        self.assertContains(resp, 'value="ADM/2025/014"', html=False)
        self.assertContains(resp, "is how this pupil signs in")

    def test_post_saves_and_returns_to_the_detail_page(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.edit_url(), self.post_data(first_name="Amaara"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("students:detail", args=[self.student.pk]))
        self.student.user.refresh_from_db()
        self.assertEqual(self.student.user.first_name, "Amaara")

    def test_renaming_the_admission_number_warns_about_the_login_account(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            self.edit_url(),
            self.post_data(admission_number="ADM/2025/099"),
            follow=True,
        )
        shown = [str(m) for m in resp.context["messages"]]
        self.assertTrue(
            any("login account is now 'adm_2025_099'" in m for m in shown), shown
        )

    def test_an_unchanged_record_produces_no_login_warning(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.edit_url(), self.post_data(), follow=True)
        shown = [str(m) for m in resp.context["messages"]]
        self.assertFalse(any("login account" in m for m in shown), shown)

    def test_the_pupil_can_then_sign_in_with_the_reprinted_admission_number(self):
        """
        End-to-end on the credential the admission letter prints: the student
        portal accepts the new number, and the old one is gone.
        """
        self.client.force_login(self.admin)
        self.client.post(self.edit_url(), self.post_data(admission_number="ADM/2025/099"))
        self.client.logout()

        resp = self.client.post(
            reverse("accounts:role_login", kwargs={"role": "student"}),
            {"username": "ADM/2025/099", "password": "ADM/2025/014"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.wsgi_request.user.username, "adm_2025_099")

    def test_a_taken_admission_number_redraws_the_form_with_the_field_error(self):
        other = create_student_with_guardian(
            admission_number="ADM/2025/077",
            first_name="Kofi",
            last_name="Owusu",
            email="kofi.view@student.school",
            current_class=self.class5,
            send_credentials=False,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(
            self.edit_url(), self.post_data(admission_number=other.admission_number)
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("admission_number", resp.context["form"].errors)
        self.student.refresh_from_db()
        self.assertEqual(self.student.admission_number, "ADM/2025/014")

    def test_a_malformed_email_is_rejected_by_the_form(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.edit_url(), self.post_data(email="not-an-email"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("email", resp.context["form"].errors)

    def test_only_an_admin_edits_a_student(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(self.edit_url()).status_code, 403)
        self.assertEqual(
            self.client.post(self.edit_url(), self.post_data()).status_code, 403
        )

    def test_an_unknown_student_is_404_not_500(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.edit_url(pk=999999))
        self.assertEqual(resp.status_code, 404)

    def test_a_next_from_the_filtered_list_is_honoured(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            self.edit_url(), self.post_data(next="/students/?status=active")
        )
        self.assertEqual(resp.url, "/students/?status=active")

    def test_a_next_pointing_off_site_is_ignored(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            self.edit_url(), self.post_data(next="http://evil.example/phish")
        )
        self.assertEqual(resp.url, reverse("students:detail", args=[self.student.pk]))

    def test_detail_page_offers_edit(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("students:detail", args=[self.student.pk]))
        self.assertContains(resp, self.edit_url(), html=False)

    def test_list_page_shows_edit_to_an_admin_only(self):
        """
        The negative half needs a director, not a teacher. ``SchoolReadMixin``
        admits admin, director and finance, so a teacher never reaches the list
        page at all — see ``test_a_teacher_cannot_read_the_list_page``.
        """
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("students:list"))
        self.assertContains(resp, self.edit_url(), html=False)

        self.client.force_login(self.director)
        resp = self.client.get(reverse("students:list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, self.edit_url(), html=False)

    def test_a_teacher_cannot_read_the_list_page(self):
        self.client.force_login(self.teacher)
        self.assertEqual(
            self.client.get(reverse("students:list")).status_code, 403
        )


class StudentLoginToggleTests(TestCase):
    """C4a — the lever that stops a pupil signing in without touching their record."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="login_toggle_admin",
            email="login_toggle_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.teacher = User.objects.create_user(
            username="login_toggle_teacher",
            email="login_toggle_teacher@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        cls.director = User.objects.create_user(
            username="login_toggle_director",
            email="login_toggle_director@test.school",
            password="Testpass1!",
            role=User.Role.DIRECTOR,
            must_change_password=False,
        )
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.class5 = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.pupil = create_student_with_guardian(
            admission_number="ADM/2025/050",
            first_name="Sierra",
            last_name="Boadu",
            email="sierra@login.school",
            current_class=cls.class5,
            send_credentials=False,
        )
        cls.pupil.user.must_change_password = False
        cls.pupil.user.save(update_fields=["must_change_password"])

    def disable_url(self):
        return reverse("students:disable_login", args=[self.pupil.pk])

    def enable_url(self):
        return reverse("students:enable_login", args=[self.pupil.pk])

    # ── service ───────────────────────────────────────────────────────────────

    def test_disabling_flips_the_flag_and_audits_the_state_change(self):
        set_student_login_active(
            student=self.pupil, active=False, actor=self.admin
        )
        self.pupil.user.refresh_from_db()
        self.assertFalse(self.pupil.user.is_active)

        entry = AuditLog.objects.get(action="student.login_disabled")
        self.assertEqual(entry.object_id, str(self.pupil.pk))
        self.assertEqual(entry.change_summary["username"], self.pupil.user.username)
        self.assertEqual(entry.change_summary["admission_number"], "ADM/2025/050")
        self.assertEqual(
            entry.change_summary["is_active"], {"before": True, "after": False}
        )

    def test_enabling_is_the_mirror_image(self):
        set_student_login_active(student=self.pupil, active=False, actor=self.admin)
        set_student_login_active(student=self.pupil, active=True, actor=self.admin)
        self.pupil.user.refresh_from_db()
        self.assertTrue(self.pupil.user.is_active)

        entry = AuditLog.objects.get(action="student.login_enabled")
        self.assertEqual(
            entry.change_summary["is_active"], {"before": False, "after": True}
        )

    def test_a_repeated_disable_writes_no_audit_row(self):
        set_student_login_active(student=self.pupil, active=False, actor=self.admin)
        set_student_login_active(student=self.pupil, active=False, actor=self.admin)
        self.assertEqual(AuditLog.objects.filter(action="student.login_disabled").count(), 1)

    def test_disabling_leaves_the_record_and_its_links_untouched(self):
        guardian_count = StudentGuardian.objects.filter(student=self.pupil).count()
        set_student_login_active(student=self.pupil, active=False, actor=self.admin)

        self.pupil.refresh_from_db()
        self.assertEqual(self.pupil.admission_number, "ADM/2025/050")
        self.assertEqual(self.pupil.status, Student.Status.ACTIVE)
        self.assertEqual(self.pupil.current_class, self.class5)
        self.assertEqual(
            StudentGuardian.objects.filter(student=self.pupil).count(), guardian_count
        )

    # ── views ─────────────────────────────────────────────────────────────────

    def test_admin_post_disables_and_returns_to_the_detail_page(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.disable_url())

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("students:detail", args=[self.pupil.pk]))
        self.pupil.user.refresh_from_db()
        self.assertFalse(self.pupil.user.is_active)

    def test_admin_post_enables_again(self):
        self.client.force_login(self.admin)
        self.client.post(self.disable_url())
        resp = self.client.post(self.enable_url())

        self.assertEqual(resp.status_code, 302)
        self.pupil.user.refresh_from_db()
        self.assertTrue(self.pupil.user.is_active)

    def test_the_message_says_what_the_admin_just_lost(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.disable_url(), follow=True)
        shown = " ".join(str(m) for m in resp.context["messages"])
        self.assertIn("cannot sign in", shown)
        self.assertIn("Marks, fees, attendance", shown)

    def test_a_disabled_pupil_cannot_authenticate(self):
        set_student_login_active(student=self.pupil, active=False, actor=self.admin)
        self.assertIsNone(
            authenticate(
                None, username="adm_2025_050", password="ADM/2025/050"
            )
        )

    def test_a_disabled_pupil_is_refused_at_the_student_login_door(self):
        """
        Signed through the real portal door, with a positive control first: the
        same credentials must work before the toggle, or the refusal below could
        just be a typo in the test.
        """
        credentials = {"username": "ADM/2025/050", "password": "ADM/2025/050"}
        resp = self.client.post(
            reverse("accounts:role_login", kwargs={"role": "student"}), credentials
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.wsgi_request.user.is_authenticated)
        self.client.logout()

        self.client.force_login(self.admin)
        self.client.post(self.disable_url())
        self.client.logout()

        resp = self.client.post(
            reverse("accounts:role_login", kwargs={"role": "student"}), credentials
        )
        self.assertNotEqual(resp.status_code, 302)
        self.assertFalse(getattr(resp.wsgi_request.user, "is_authenticated", False))

    def test_an_open_session_stops_working_at_its_next_request(self):
        """
        The part that is easy to assume: disabling does not merely block the next
        sign-in, it evicts a pupil already sitting in the portal.
        """
        pupil_client = self.client
        pupil_client.force_login(self.pupil.user)
        self.assertEqual(
            pupil_client.get(reverse("dashboards:student_home")).status_code, 200
        )

        admin_client = self.client_class()
        admin_client.force_login(self.admin)
        admin_client.post(self.disable_url())

        resp = pupil_client.get(reverse("dashboards:student_home"))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_only_an_admin_toggles_a_pupil_login(self):
        for role_user in (self.teacher, self.director):
            self.client.force_login(role_user)
            self.assertEqual(self.client.post(self.disable_url()).status_code, 403)
        self.pupil.user.refresh_from_db()
        self.assertTrue(self.pupil.user.is_active)

    def test_the_toggle_endpoints_are_post_only(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.disable_url()).status_code, 405)
        self.assertEqual(self.client.get(self.enable_url()).status_code, 405)

    def test_an_unknown_pupil_is_404(self):
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(
                reverse("students:disable_login", args=[999999])
            ).status_code,
            404,
        )

    # ── templates ─────────────────────────────────────────────────────────────

    def test_detail_page_offers_the_button_matching_the_current_state(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("students:detail", args=[self.pupil.pk]))
        self.assertContains(resp, self.disable_url(), html=False)
        self.assertContains(resp, "Can sign in", html=False)
        self.assertNotContains(resp, self.enable_url(), html=False)

        self.client.post(self.disable_url())
        resp = self.client.get(reverse("students:detail", args=[self.pupil.pk]))
        self.assertContains(resp, self.enable_url(), html=False)
        self.assertContains(resp, "Login disabled", html=False)

    def test_list_page_badges_only_a_disabled_pupil(self):
        other = create_student_with_guardian(
            admission_number="ADM/2025/051",
            first_name="Nia",
            last_name="Royal",
            email="nia@login.school",
            current_class=self.class5,
            send_credentials=False,
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("students:list"))
        self.assertNotContains(resp, "Login disabled")

        set_student_login_active(student=self.pupil, active=False, actor=self.admin)
        resp = self.client.get(reverse("students:list"))
        self.assertContains(resp, "Login disabled")
        self.assertIn(self.pupil.user.full_name, str(list(resp.context["students"])))
        self.assertTrue(other.user.is_active)


class StudentDeleteTests(TestCase):
    """C4b — the permanent lever, and the refusals that guard it."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="delete_admin",
            email="delete_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.teacher = User.objects.create_user(
            username="delete_teacher",
            email="delete_teacher@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        cls.director = User.objects.create_user(
            username="delete_director",
            email="delete_director@test.school",
            password="Testpass1!",
            role=User.Role.DIRECTOR,
            must_change_password=False,
        )
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.class5 = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.term = Term.objects.create(
            academic_year=cls.year,
            name=Term.TermName.TERM_1,
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 15),
            is_current=True,
        )
        cls.pupil = create_student_with_guardian(
            admission_number="ADM/2025/060",
            first_name="Kwame",
            last_name="Osei",
            email="kwame@delete.school",
            current_class=cls.class5,
            guardian_first_name="Ama",
            guardian_last_name="Osei",
            guardian_email="ama@delete.school",
            send_credentials=False,
        )
        cls.parent_user = ParentProfile.objects.get(
            user__email="ama@delete.school"
        ).user

    def delete_url(self, pk=None):
        return reverse("students:delete", args=[pk or self.pupil.pk])

    def invoice(self, *, version=1, student=None):
        structure = FeeStructure.objects.create(
            school_class=self.class5,
            term=self.term,
            version=version,
            is_active=True,
            due_date=datetime.date(2025, 12, 15),
        )
        return Invoice.objects.create(
            student=student or self.pupil,
            term=self.term,
            fee_structure=structure,
            issued_date=datetime.date(2025, 12, 1),
            due_date=datetime.date(2025, 12, 15),
            total_amount=Decimal("500.00"),
            status=Invoice.Status.POSTED,
        )

    # ── forecast ──────────────────────────────────────────────────────────────

    def test_a_pupil_with_no_history_shows_nothing_blocking(self):
        forecast = student_delete_forecast(self.pupil)
        self.assertEqual(forecast["blockers"], {})
        # The guardian link is the one row this fixture actually has, and it is a
        # CASCADE: it goes with the pupil, so it belongs in the cost, not the block.
        self.assertEqual(forecast["destroyed"], {"guardian link": 1})
        self.assertEqual(forecast["links"], {})

    def test_an_invoice_blocks_and_is_named_in_the_pages_own_words(self):
        self.invoice()
        forecast = student_delete_forecast(self.pupil)
        self.assertEqual(forecast["blockers"], {"fee invoice": 1})

    def test_a_discount_rule_is_reported_as_a_link_that_survives(self):
        rule = FeeDiscountRule.objects.create(
            name="Siblings",
            basis=FeeDiscountRule.Basis.PERCENT,
            percent=Decimal("10.00"),
        )
        rule.students.add(self.pupil)
        self.assertEqual(
            student_delete_forecast(self.pupil)["links"], {"fee discount rule": 1}
        )

    # ── service ───────────────────────────────────────────────────────────────

    def test_deleting_takes_the_login_with_the_record(self):
        """
        The row deleted is the ``User``, so an orphan login is impossible. The
        parent keeps their own account — only the link row goes.
        """
        label = delete_student(student=self.pupil, actor=self.admin)
        self.assertEqual(label, "Kwame Osei (ADM/2025/060)")
        self.assertFalse(Student.objects.filter(pk=self.pupil.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.pupil.user_id).exists())
        self.assertEqual(StudentGuardian.objects.filter(student_id=self.pupil.pk).count(), 0)
        self.parent_user.refresh_from_db()
        self.assertTrue(self.parent_user.is_active)

    def test_the_audit_row_describes_what_was_removed(self):
        """The row cannot be read back after the delete, so it is snapshotted first."""
        delete_student(student=self.pupil, actor=self.admin)
        entry = AuditLog.objects.get(action="student.deleted")
        removed = entry.change_summary["removed"]
        self.assertEqual(removed["admission_number"], "ADM/2025/060")
        self.assertEqual(removed["username"], "adm_2025_060")
        self.assertEqual(removed["email"], "kwame@delete.school")
        self.assertEqual(removed["class"], "Grade 5")
        self.assertTrue(removed["login_active"])
        self.assertEqual(entry.change_summary["destroyed"], {"guardian link": 1})
        self.assertEqual(str(entry.object_id), str(self.pupil.pk))

    def test_a_pupil_who_owes_money_cannot_be_deleted(self):
        self.invoice()
        with self.assertRaises(ValidationError) as ctx:
            delete_student(student=self.pupil, actor=self.admin)
        self.assertIn("1 fee invoice", ctx.exception.messages[0])
        self.assertIn("disable the login", ctx.exception.messages[0])
        self.assertTrue(Student.objects.filter(pk=self.pupil.pk).exists())
        self.pupil.user.refresh_from_db()
        self.assertTrue(self.pupil.user.is_active)

    def test_several_blockers_are_counted_not_merged_into_one(self):
        self.invoice(version=1)
        self.invoice(version=2)
        with self.assertRaises(ValidationError) as ctx:
            delete_student(student=self.pupil, actor=self.admin)
        self.assertIn("2 fee invoices", ctx.exception.messages[0])

    def test_a_refused_delete_leaves_no_trail_claiming_it_happened(self):
        self.invoice()
        with self.assertRaises(ValidationError):
            delete_student(student=self.pupil, actor=self.admin)
        self.assertEqual(
            AuditLog.objects.filter(action__endswith=".deleted").count(), 0
        )

    # ── view ──────────────────────────────────────────────────────────────────

    def test_the_confirm_page_offers_the_button_when_nothing_blocks(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.delete_url())
        self.assertContains(resp, "btn-confirm-student-delete")
        self.assertNotContains(resp, "id=\"delete-blocked\"")

    def test_the_confirm_page_withholds_the_button_behind_the_blockers(self):
        self.invoice()
        self.client.force_login(self.admin)
        resp = self.client.get(self.delete_url())
        self.assertContains(resp, "id=\"delete-blocked\"")
        self.assertContains(resp, "1 fee invoice")
        self.assertNotContains(resp, "btn-confirm-student-delete")

    def test_reading_the_confirm_page_deletes_nothing(self):
        self.client.force_login(self.admin)
        self.client.get(self.delete_url())
        self.assertTrue(Student.objects.filter(pk=self.pupil.pk).exists())

    def test_posting_deletes_and_lands_on_the_list(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.delete_url())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("students:list"))
        self.assertFalse(Student.objects.filter(pk=self.pupil.pk).exists())

    def test_a_refused_post_keeps_the_same_pupil_on_screen(self):
        self.invoice()
        self.client.force_login(self.admin)
        resp = self.client.post(self.delete_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "id=\"delete-blocked\"")
        shown = " ".join(str(m) for m in resp.context["messages"])
        self.assertIn("1 fee invoice", shown)
        self.assertTrue(Student.objects.filter(pk=self.pupil.pk).exists())

    def test_only_an_admin_reaches_the_delete_page(self):
        for other in (self.teacher, self.director):
            self.client.force_login(other)
            self.assertEqual(self.client.get(self.delete_url()).status_code, 403)
            self.assertEqual(self.client.post(self.delete_url()).status_code, 403)
        self.assertTrue(Student.objects.filter(pk=self.pupil.pk).exists())

    def test_an_unknown_pupil_is_404(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.delete_url(pk=999999)).status_code, 404)
        self.assertEqual(self.client.post(self.delete_url(pk=999999)).status_code, 404)

    def test_the_detail_page_links_to_the_delete_page(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("students:detail", args=[self.pupil.pk]))
        self.assertContains(resp, self.delete_url(), html=False)

