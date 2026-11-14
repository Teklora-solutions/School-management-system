"""
apps/gradebook/tests/test_checkpoint5.py
----------------------------------------
Checkpoint 5: what families read, what the director aggregates, the CSV/Excel
downloads, and the copy of released marks onto a Draft report-card sheet.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse
from openpyxl import load_workbook

from apps.academics.models import Stream
from apps.accounts.models import ParentProfile
from apps.core.models import AuditLog, SchoolSettings
from apps.dashboards.tests.fixtures import make_grading_config, make_user
from apps.gradebook import services
from apps.gradebook.tests.test_release import ReleaseTestCase
from apps.results.models import Exam, ResultSheet, ScoreEntry
from apps.students.models import StudentGuardian


class GradebookFixture(ReleaseTestCase):
    """The checkpoint-4 fixture, with a couple of helpers for checkpoint 5."""

    def _exam_sheet(
        self,
        *,
        status: str = ResultSheet.Status.DRAFT,
        stream=None,
        order: int = 1,
    ) -> ResultSheet:
        exam = Exam.objects.create(
            term=self.term, name=f"End of term {order}", order=order
        )
        return ResultSheet.objects.create(
            school_class=self.school_class,
            subject=self.maths,
            term=self.term,
            stream=stream,
            exam=exam,
            status=status,
        )

    def _update_mark(self, homework, student, score):
        from apps.homework.models import HomeworkMark

        HomeworkMark.objects.filter(homework=homework, student=student).update(
            score=Decimal(str(score))
        )

    def _attach_sheet(self, sheet, setup=None):
        setup = setup or self._setup()
        setup.exam_sheet = sheet
        setup.save(update_fields=["exam_sheet", "updated_at"])
        return setup

    def _release(self, *, reason: str = "", setup=None):
        return services.release_gradebook(
            setup=setup or self._setup(),
            reason=reason,
            actor=self.teacher_user,
        )

    def _two_marks(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        self._mark(homework, self.kofi, 10)
        return homework

    def _send(self, setup=None):
        return services.send_to_report_card(
            setup=setup or self._setup(), actor=self.teacher_user
        )


# ---------------------------------------------------------------------------
# Families
# ---------------------------------------------------------------------------


class FamilyViewTests(GradebookFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.ama.user)

    def test_a_pupil_sees_the_latest_released_numbers_only(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        self._release()
        # v1 says 75%; the teacher corrects the mark and releases v2.
        self._update_mark(homework, self.ama, 20)
        self._release(reason="Mark corrected after remarking")

        response = self.client.get(reverse("gradebook:my_gradebook"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mathematics")
        self.assertContains(response, "100.0%")
        self.assertNotContains(response, "75.0%")
        self.assertContains(response, "v2")

    def test_bucket_percentages_are_shown(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        self._release()

        response = self.client.get(reverse("gradebook:my_gradebook"))
        self.assertContains(response, "Homework")
        self.assertContains(response, "75.0%")

    def test_nothing_released_yet_is_said_plainly(self):
        response = self.client.get(reverse("gradebook:my_gradebook"))
        self.assertContains(
            response, "No gradebook marks have been shared with you yet."
        )

    def test_a_parent_sees_their_primary_child(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        self._release()
        parent_user = make_user("parent", username="fam_parent")
        profile = ParentProfile.objects.create(user=parent_user)
        StudentGuardian.objects.create(
            student=self.ama, parent=profile, is_primary=True
        )
        self.client.force_login(parent_user)

        response = self.client.get(reverse("gradebook:my_gradebook"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "REL-101")
        self.assertContains(response, "75.0%")

    def test_the_child_switcher_decides_which_child_is_read(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        self._mark(homework, self.kofi, 5)
        self._release()
        parent_user = make_user("parent", username="fam_switcher")
        profile = ParentProfile.objects.create(user=parent_user)
        StudentGuardian.objects.create(
            student=self.ama, parent=profile, is_primary=True
        )
        StudentGuardian.objects.create(
            student=self.kofi, parent=profile, is_primary=False
        )
        self.client.force_login(parent_user)
        session = self.client.session
        session["selected_child_id"] = self.kofi.pk
        session.save()

        response = self.client.get(reverse("gradebook:my_gradebook"))
        self.assertContains(response, "REL-102")
        self.assertNotContains(response, "REL-101")

    def test_another_class_release_never_appears(self):
        self._two_marks()
        self._release()
        other_setup = services.get_or_create_setup(
            school_class=self.other_class,
            subject=self.science,
            term=self.term,
            actor=self.teacher_user,
        )
        self._homework_for(self.other_class, self.science, self.outsider)
        self._release(setup=other_setup)

        response = self.client.get(reverse("gradebook:my_gradebook"))
        self.assertContains(response, "Mathematics")
        self.assertNotContains(response, "Science")

    def _homework_for(self, school_class, subject, student):
        from decimal import Decimal

        from apps.homework.models import Homework, HomeworkMark

        homework = Homework.objects.create(
            title="Cells",
            subject=subject,
            school_class=school_class,
            term=self.term,
            due_date=self.term.start_date,
            status=Homework.Status.PUBLISHED,
            max_score=Decimal("10"),
            counts_for_gradebook=True,
        )
        HomeworkMark.objects.create(
            homework=homework,
            student=student,
            score=Decimal("8"),
            status=HomeworkMark.Status.GOOD,
            marked_by=self.teacher_user,
        )

    def test_teachers_and_directors_cannot_open_the_family_page(self):
        self.client.force_login(self.teacher_user)
        self.assertEqual(
            self.client.get(reverse("gradebook:my_gradebook")).status_code, 403
        )
        self.client.force_login(self.director_user)
        self.assertEqual(
            self.client.get(reverse("gradebook:my_gradebook")).status_code, 403
        )

    def test_switch_off_shows_the_sentence(self):
        row = SchoolSettings.load()
        row.gradebook_enabled = False
        row.save(update_fields=["gradebook_enabled", "updated_at"])

        response = self.client.get(reverse("gradebook:my_gradebook"))
        self.assertContains(
            response, "Available once the Gradebook module is activated."
        )

    def test_the_sidebar_links_to_the_page_when_switched_on(self):
        response = self.client.get(reverse("gradebook:my_gradebook"))
        self.assertContains(
            response, f'href="{reverse("gradebook:my_gradebook")}"'
        )


# ---------------------------------------------------------------------------
# Director
# ---------------------------------------------------------------------------


class DirectorOverviewTests(GradebookFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.director_user)

    @property
    def overview_url(self) -> str:
        return reverse("gradebook:director_overview") + f"?term={self.term.pk}"

    def test_class_figures_come_from_the_release_and_no_pupil_is_named(self):
        self._two_marks()
        self._release()

        response = self.client.get(self.overview_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Basic 6")
        self.assertContains(response, "Mathematics")
        self.assertContains(response, "62.5%")
        self.assertNotContains(response, "REL-101")
        self.assertNotContains(response, "REL-102")
        self.assertNotContains(response, "Test Student")

    def test_only_the_latest_release_is_aggregated(self):
        homework = self._two_marks()
        self._release()
        self._update_mark(homework, self.ama, 20)
        self._release(reason="Remarked")

        response = self.client.get(self.overview_url)
        self.assertContains(response, "75.0%")
        self.assertNotContains(response, "62.5%")

    def test_a_classroom_that_has_not_released_is_counted_but_not_in_the_table(self):
        self._two_marks()
        self._release()
        services.get_or_create_setup(
            school_class=self.other_class,
            subject=self.science,
            term=self.term,
            actor=self.teacher_user,
        )

        response = self.client.get(self.overview_url)
        self.assertContains(response, "1 classroom in this term")
        self.assertContains(response, "not released a gradebook yet")

    def test_csv_export_holds_the_same_rows(self):
        self._two_marks()
        self._release()

        response = self.client.get(
            reverse("gradebook:director_export", args=["csv"])
            + f"?term={self.term.pk}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[0][0], "Class")
        self.assertEqual(rows[0][2], "Subject")
        self.assertEqual(rows[1][0], "Basic 6")
        self.assertEqual(rows[1][2], "Mathematics")
        self.assertEqual(rows[1][3], "1")

    def test_excel_export_holds_the_same_rows(self):
        self._two_marks()
        self._release()

        response = self.client.get(
            reverse("gradebook:director_export", args=["xlsx"])
            + f"?term={self.term.pk}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        workbook = load_workbook(io.BytesIO(response.content))
        sheet = workbook.active
        self.assertEqual(sheet["A1"].value, "Class")
        self.assertEqual(sheet["A2"].value, "Basic 6")
        self.assertEqual(sheet["C2"].value, "Mathematics")
        self.assertEqual(workbook.properties.title.split(" — ")[0], "Gradebook releases")

    def test_teacher_and_pupil_cannot_open_the_overview_or_its_exports(self):
        self.client.force_login(self.teacher_user)
        self.assertEqual(self.client.get(self.overview_url).status_code, 403)
        self.client.force_login(self.ama.user)
        self.assertEqual(self.client.get(self.overview_url).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("gradebook:director_export", args=["csv"]))
            .status_code,
            403,
        )

    def test_switch_off_shows_the_sentence(self):
        row = SchoolSettings.load()
        row.gradebook_enabled = False
        row.save(update_fields=["gradebook_enabled", "updated_at"])

        response = self.client.get(reverse("gradebook:director_overview"))
        self.assertContains(
            response, "Available once the Gradebook module is activated."
        )

    def test_the_sidebar_links_to_the_overview_when_switched_on(self):
        response = self.client.get(reverse("gradebook:director_overview"))
        self.assertContains(
            response, f'href="{reverse("gradebook:director_overview")}"'
        )


# ---------------------------------------------------------------------------
# Teacher export
# ---------------------------------------------------------------------------


class TeacherExportTests(GradebookFixture):
    def test_csv_download_holds_the_grid(self):
        self._two_marks()

        response = self.client.get(
            reverse("gradebook:teacher_export", args=[self._setup().pk, "csv"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(
            rows[0],
            [
                "Pupil",
                "Admission",
                "Fractions (Homework, out of 20)",
                "Homework (%)",
                "Overall (%)",
            ],
        )
        self.assertEqual(rows[1], ["Test Student", "REL-101", "15/20", "75.0", "75.0"])
        self.assertEqual(rows[2], ["Test Student", "REL-102", "10/20", "50.0", "50.0"])

    def test_excel_download_holds_the_grid(self):
        self._two_marks()

        response = self.client.get(
            reverse("gradebook:teacher_export", args=[self._setup().pk, "xlsx"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        workbook = load_workbook(io.BytesIO(response.content))
        sheet = workbook.active
        self.assertEqual(sheet["A1"].value, "Pupil")
        self.assertEqual(sheet["A2"].value, "Test Student")
        self.assertEqual(sheet["B2"].value, "REL-101")
        self.assertEqual(sheet["C2"].value, "15/20")
        self.assertEqual(sheet["D2"].value, 75)
        self.assertEqual(sheet["E2"].value, 75)

    def test_an_excused_pupil_is_marked_in_the_file(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        services.record_excuse(
            setup=self._setup(),
            student=self.kofi,
            source_kind="homework",
            source_id=homework.pk,
            reason="Away with the school team",
            actor=self.teacher_user,
        )

        response = self.client.get(
            reverse("gradebook:teacher_export", args=[self._setup().pk, "csv"])
        )
        self.assertIn("Excused", response.content.decode())

    def test_the_export_is_scoped_to_the_teachers_own_classroom(self):
        setup = self._setup()
        self.client.force_login(self.other_teacher_user)
        self.assertEqual(
            self.client.get(
                reverse("gradebook:teacher_export", args=[setup.pk, "csv"])
            ).status_code,
            403,
        )
        self.client.force_login(self.ama.user)
        self.assertEqual(
            self.client.get(
                reverse("gradebook:teacher_export", args=[setup.pk, "csv"])
            ).status_code,
            403,
        )

    def test_an_unknown_format_is_refused(self):
        self.assertEqual(
            self.client.get(
                reverse("gradebook:teacher_export", args=[self._setup().pk, "pdf"])
            ).status_code,
            404,
        )

    def test_the_grid_offers_both_downloads(self):
        setup = self._setup()
        response = self.client.get(
            reverse(
                "gradebook:teacher_grid",
                args=[self.school_class.pk, self.maths.pk],
            )
            + f"?term={self.term.pk}"
        )
        self.assertContains(
            response, f'href="{reverse("gradebook:teacher_export", args=[setup.pk, "csv"])}"'
        )
        self.assertContains(
            response,
            f'href="{reverse("gradebook:teacher_export", args=[setup.pk, "xlsx"])}"',
        )


# ---------------------------------------------------------------------------
# Report card
# ---------------------------------------------------------------------------


class ReportCardServiceTests(GradebookFixture):
    def test_released_marks_are_written_onto_the_draft_sheet(self):
        self._two_marks()
        release = self._release()
        sheet = self._exam_sheet()
        setup = self._attach_sheet(sheet)

        outcome = self._send(setup)
        self.assertEqual(outcome["written"], 2)
        self.assertEqual(outcome["skipped"], 0)
        self.assertEqual(outcome["sheet"].pk, sheet.pk)

        ama_entry = ScoreEntry.objects.get(result_sheet=sheet, student=self.ama)
        expected = release.results.get(student=self.ama).overall_pct
        self.assertEqual(ama_entry.raw_score, expected)
        self.assertEqual(ama_entry.total, expected)

    def test_the_grade_scale_grades_the_copied_mark(self):
        make_grading_config(self.year)
        self._two_marks()
        self._release()
        setup = self._attach_sheet(self._exam_sheet())

        self._send(setup)
        entry = ScoreEntry.objects.get(result_sheet=setup.exam_sheet, student=self.ama)
        self.assertEqual(entry.grade, "B")
        self.assertEqual(entry.remark, "B")

    def test_the_copy_is_audit_logged(self):
        self._two_marks()
        self._release()
        setup = self._attach_sheet(self._exam_sheet())

        self._send(setup)
        self.assertTrue(
            AuditLog.objects.filter(action="gradebook.sent_to_report_card").exists()
        )

    def test_a_release_is_required_first(self):
        setup = self._attach_sheet(self._exam_sheet())
        with self.assertRaisesMessage(
            ValidationError, services.RELEASE_REQUIRED_MESSAGE
        ):
            self._send(setup)

    def test_an_end_of_term_sheet_must_be_chosen(self):
        self._two_marks()
        self._release()
        with self.assertRaisesMessage(ValidationError, services.SHEET_REQUIRED_MESSAGE):
            self._send()

    def test_only_a_draft_sheet_receives_marks(self):
        statuses = (
            ResultSheet.Status.SUBMITTED,
            ResultSheet.Status.CONFIRMED,
            ResultSheet.Status.PUBLISHED,
        )
        for order, status in enumerate(statuses, start=1):
            with self.subTest(status=status):
                self._two_marks()
                self._release(reason=f"Remark {order}")
                setup = self._attach_sheet(
                    self._exam_sheet(status=status, order=order)
                )
                with self.assertRaises(ValidationError) as caught:
                    self._send(setup)
                self.assertIn("only receive marks while it is a draft", str(caught.exception))
                self.assertFalse(
                    ScoreEntry.objects.filter(result_sheet=setup.exam_sheet).exists()
                )

    def test_a_sheet_for_another_stream_is_refused(self):
        stream = Stream.objects.create(school_class=self.school_class, name="Blue")
        self._two_marks()
        self._release()
        setup = self._attach_sheet(self._exam_sheet(stream=stream))
        with self.assertRaisesMessage(ValidationError, services.STREAM_MISMATCH_MESSAGE):
            self._send(setup)

    def test_a_locked_term_refuses_the_copy(self):
        self._two_marks()
        self._release()
        setup = self._attach_sheet(self._exam_sheet())
        services.set_term_lock(
            term=self.term, locked=True, actor=self.admin_user, reason="Results day"
        )
        with self.assertRaisesMessage(ValidationError, services.LOCKED_MESSAGE):
            self._send(setup)

    def test_a_pupil_with_no_weighted_mark_is_skipped(self):
        homework = self._homework()
        self._mark(homework, self.ama, 15)
        services.record_excuse(
            setup=self._setup(),
            student=self.kofi,
            source_kind="homework",
            source_id=homework.pk,
            reason="Long illness",
            actor=self.teacher_user,
        )
        self._release()
        setup = self._attach_sheet(self._exam_sheet())

        outcome = self._send(setup)
        self.assertEqual(outcome["written"], 1)
        self.assertEqual(outcome["skipped"], 1)
        self.assertFalse(
            ScoreEntry.objects.filter(result_sheet=setup.exam_sheet, student=self.kofi)
            .exists()
        )

    def test_nothing_to_send_is_refused(self):
        homework = self._homework()
        for student in (self.ama, self.kofi):
            services.record_excuse(
                setup=self._setup(),
                student=student,
                source_kind="homework",
                source_id=homework.pk,
                reason="Class trip",
                actor=self.teacher_user,
            )
        self._release()
        setup = self._attach_sheet(self._exam_sheet())
        with self.assertRaisesMessage(ValidationError, services.NOTHING_TO_SEND_MESSAGE):
            self._send(setup)

    def test_a_pupil_no_longer_in_the_class_is_not_written(self):
        self._two_marks()
        self._release()
        setup = self._attach_sheet(self._exam_sheet())
        self.kofi.current_class = self.other_class
        self.kofi.save(update_fields=["current_class", "updated_at"])

        outcome = self._send(setup)
        self.assertEqual(outcome["written"], 1)
        self.assertFalse(
            ScoreEntry.objects.filter(result_sheet=setup.exam_sheet, student=self.kofi)
            .exists()
        )


class ReportCardScreenTests(GradebookFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.teacher_user)

    def test_the_button_sends_and_returns_to_the_release_page(self):
        self._two_marks()
        self._release()
        setup = self._attach_sheet(self._exam_sheet())

        response = self.client.post(
            reverse("gradebook:send_to_report_card", args=[setup.pk]), follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 weighted marks sent")
        self.assertEqual(
            ScoreEntry.objects.filter(result_sheet=setup.exam_sheet).count(), 2
        )

    def test_the_page_explains_when_the_copy_must_wait(self):
        self._two_marks()
        setup = self._setup()
        response = self.client.get(reverse("gradebook:teacher_release", args=[setup.pk]))
        self.assertContains(response, services.RELEASE_REQUIRED_MESSAGE)

    def test_a_refused_post_keeps_the_sheet_untouched(self):
        self._two_marks()
        self._release()
        setup = self._attach_sheet(
            self._exam_sheet(status=ResultSheet.Status.CONFIRMED)
        )

        response = self.client.post(
            reverse("gradebook:send_to_report_card", args=[setup.pk]), follow=True
        )
        self.assertContains(response, "only receive marks while it is a draft")
        self.assertFalse(
            ScoreEntry.objects.filter(result_sheet=setup.exam_sheet).exists()
        )

    def test_another_teacher_cannot_send_this_classrooms_marks(self):
        setup = self._setup()
        self.client.force_login(self.other_teacher_user)
        response = self.client.post(
            reverse("gradebook:send_to_report_card", args=[setup.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_switch_off_blocks_the_post(self):
        self._two_marks()
        self._release()
        setup = self._attach_sheet(self._exam_sheet())
        row = SchoolSettings.load()
        row.gradebook_enabled = False
        row.save(update_fields=["gradebook_enabled", "updated_at"])

        response = self.client.post(
            reverse("gradebook:send_to_report_card", args=[setup.pk])
        )
        self.assertEqual(response.status_code, 200)  # placeholder page, not a write
        self.assertFalse(
            ScoreEntry.objects.filter(result_sheet=setup.exam_sheet).exists()
        )
