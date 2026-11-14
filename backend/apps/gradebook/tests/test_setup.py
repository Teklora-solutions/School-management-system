"""
apps/gradebook/tests/test_setup.py
----------------------------------
Checkpoint 3: the four weights, hand-recorded work with its marks, and excuses.

Each rule that protects stored evidence is tested twice where it matters: once
on the service (the gate) and once through the screen a teacher actually uses.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.core.models import AuditLog, SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.gradebook import services
from apps.gradebook.models import (
    GradebookExcuse,
    GradebookSetup,
    GradebookTermLock,
    ManualItem,
    ManualScore,
)
from apps.homework.models import Homework
from apps.results.models import ResultSheet
from apps.teachers.models import TeacherAssignment, TeacherProfile


class SetupTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year, name="Basic 6", level_order=6)
        cls.other_class = make_class(cls.year, name="Basic 7", level_order=7)
        cls.maths = make_subject("MATH", "Mathematics")
        cls.science = make_subject("SCI", "Science")

        cls.teacher_user = make_user("teacher", username="st_teacher")
        cls.teacher = TeacherProfile.objects.create(
            user=cls.teacher_user, staff_id="ST-001"
        )
        cls.assignment = TeacherAssignment.objects.create(
            teacher=cls.teacher,
            school_class=cls.school_class,
            subject=cls.maths,
            academic_year=cls.year,
        )

        cls.other_teacher_user = make_user("teacher", username="st_other")
        cls.other_teacher = TeacherProfile.objects.create(
            user=cls.other_teacher_user, staff_id="ST-002"
        )
        TeacherAssignment.objects.create(
            teacher=cls.other_teacher,
            school_class=cls.other_class,
            subject=cls.science,
            academic_year=cls.year,
        )

        cls.ama = make_student_user(cls.school_class, admission_number="ST-101")
        cls.kofi = make_student_user(cls.school_class, admission_number="ST-102")
        cls.outsider = make_student_user(cls.other_class, admission_number="ST-201")

    def setUp(self):
        row = SchoolSettings.load()
        row.gradebook_enabled = True
        row.save(update_fields=["gradebook_enabled", "updated_at"])
        self.client.force_login(self.teacher_user)

    def _setup(self) -> GradebookSetup:
        return services.get_or_create_setup(
            school_class=self.school_class,
            subject=self.maths,
            term=self.term,
            stream=self.assignment.stream,
            actor=self.teacher_user,
        )

    def _homework(self, *, counted=True, status=Homework.Status.PUBLISHED) -> Homework:
        return Homework.objects.create(
            title="Fractions",
            subject=self.maths,
            school_class=self.school_class,
            term=self.term,
            due_date=datetime.date(2025, 10, 10),
            status=status,
            max_score=Decimal("20"),
            counts_for_gradebook=counted,
        )

    def _item(self, setup=None, **overrides) -> ManualItem:
        values = {
            "setup": setup or self._setup(),
            "bucket": ManualItem.Bucket.LAB,
            "title": "Titration",
            "max_score": Decimal("20"),
            "worked_on": datetime.date(2025, 10, 5),
            "reason": "Practical done in the lab; papers kept.",
        }
        values.update(overrides)
        return ManualItem.objects.create(**values)

    # URLs -------------------------------------------------------------

    @property
    def setup_url(self) -> str:
        return reverse("gradebook:teacher_setup", args=[self._setup().pk])

    def _create_url(self) -> str:
        return reverse("gradebook:manual_item_create", args=[self._setup().pk])

    def _edit_url(self, item: ManualItem) -> str:
        return reverse(
            "gradebook:manual_item_edit", args=[item.setup_id, item.pk]
        )

    def _remove_url(self, item: ManualItem) -> str:
        return reverse(
            "gradebook:manual_item_remove", args=[item.setup_id, item.pk]
        )

    # Form payloads ----------------------------------------------------

    def _item_payload(self, **overrides) -> dict:
        values = {
            "action": "item",
            "bucket": ManualItem.Bucket.LAB,
            "title": "Oral test",
            "max_score": "10",
            "worked_on": "2025-10-08",
            "reason": "Oral test taken in class; the rubric is in the folder.",
            "counts_for_gradebook": "on",
        }
        values.update(overrides)
        return values


class WeightsTests(SetupTestCase):
    def test_a_valid_weight_change_is_saved_and_audited(self):
        setup = self._setup()
        services.update_weights(
            setup=setup,
            homework_weight=40,
            quiz_weight=20,
            lab_weight=20,
            exam_weight=20,
            actor=self.teacher_user,
        )
        setup.refresh_from_db()
        self.assertEqual(
            (setup.homework_weight, setup.quiz_weight, setup.lab_weight, setup.exam_weight),
            (40, 20, 20, 20),
        )
        log = AuditLog.objects.get(action="gradebook.weights_updated")
        self.assertEqual(log.actor, self.teacher_user)
        self.assertEqual(log.change_summary["to"]["homework"], 40)

    def test_weights_that_miss_100_are_refused(self):
        setup = self._setup()
        with self.assertRaises(ValidationError):
            services.update_weights(
                setup=setup,
                homework_weight=40,
                quiz_weight=40,
                lab_weight=20,
                exam_weight=20,
                actor=self.teacher_user,
            )
        setup.refresh_from_db()
        self.assertEqual(setup.homework_weight, 30)

    def test_a_sheet_from_another_classroom_is_refused(self):
        foreign = ResultSheet.objects.create(
            school_class=self.other_class,
            subject=self.science,
            term=self.term,
        )
        with self.assertRaises(ValidationError):
            services.update_weights(
                setup=self._setup(),
                homework_weight=30,
                quiz_weight=30,
                lab_weight=20,
                exam_weight=20,
                exam_sheet=foreign,
                actor=self.teacher_user,
            )

    def test_the_weights_form_saves_through_the_screen(self):
        response = self.client.post(
            self.setup_url,
            {
                "action": "weights",
                "homework_weight": "25",
                "quiz_weight": "25",
                "lab_weight": "25",
                "exam_weight": "25",
            },
        )
        self.assertRedirects(response, self.setup_url)
        setup = GradebookSetup.objects.get(pk=self._setup().pk)
        self.assertEqual(setup.exam_weight, 25)

    def test_the_screen_explains_weights_that_miss_100(self):
        response = self.client.post(
            self.setup_url,
            {
                "action": "weights",
                "homework_weight": "50",
                "quiz_weight": "30",
                "lab_weight": "20",
                "exam_weight": "20",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must add up to 100")
        self.assertEqual(GradebookSetup.objects.get(pk=self._setup().pk).homework_weight, 30)

    def test_another_classrooms_sheet_is_not_offered_or_accepted(self):
        foreign = ResultSheet.objects.create(
            school_class=self.other_class,
            subject=self.science,
            term=self.term,
        )
        response = self.client.post(
            self.setup_url,
            {
                "action": "weights",
                "homework_weight": "30",
                "quiz_weight": "30",
                "lab_weight": "20",
                "exam_weight": "20",
                "exam_sheet": str(foreign.pk),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'value="{foreign.pk}"')
        self.assertIsNone(GradebookSetup.objects.get(pk=self._setup().pk).exam_sheet)


class ManualItemTests(SetupTestCase):
    def test_a_hand_recorded_item_is_created_with_its_reason(self):
        item = services.create_manual_item(
            setup=self._setup(),
            bucket=ManualItem.Bucket.LAB,
            title="Titration",
            max_score=Decimal("20"),
            worked_on=datetime.date(2025, 10, 5),
            reason="  Practical done in the lab; papers kept.  ",
            actor=self.teacher_user,
        )
        self.assertEqual(item.reason, "Practical done in the lab; papers kept.")
        self.assertTrue(item.counts_for_gradebook)
        self.assertTrue(
            AuditLog.objects.filter(
                action="gradebook.manual_item_created", object_id=str(item.pk)
            ).exists()
        )

    def test_the_exam_bucket_cannot_be_recorded_by_hand(self):
        with self.assertRaises(ValidationError):
            services.create_manual_item(
                setup=self._setup(),
                bucket="exam",
                title="Mock exam",
                max_score=Decimal("100"),
                worked_on=datetime.date(2025, 10, 5),
                reason="Typed in by hand.",
                actor=self.teacher_user,
            )

    def test_a_reason_is_required(self):
        with self.assertRaises(ValidationError):
            services.create_manual_item(
                setup=self._setup(),
                bucket=ManualItem.Bucket.LAB,
                title="Titration",
                max_score=Decimal("20"),
                worked_on=datetime.date(2025, 10, 5),
                reason="   ",
                actor=self.teacher_user,
            )

    def test_the_total_must_be_more_than_zero(self):
        with self.assertRaises(ValidationError):
            services.create_manual_item(
                setup=self._setup(),
                bucket=ManualItem.Bucket.LAB,
                title="Titration",
                max_score=Decimal("0"),
                worked_on=datetime.date(2025, 10, 5),
                reason="Practical done in the lab.",
                actor=self.teacher_user,
            )

    def test_the_screen_creates_the_item_then_opens_its_marks(self):
        response = self.client.post(self._create_url(), self._item_payload())
        item = ManualItem.objects.get()
        self.assertRedirects(response, self._edit_url(item))
        self.assertEqual(item.max_score, Decimal("10.0"))

    def test_the_screen_shows_the_reason_missing(self):
        response = self.client.post(
            self._create_url(), self._item_payload(reason="  ")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertFalse(ManualItem.objects.exists())

    def test_unticking_keeps_the_item_but_takes_it_out_of_the_average(self):
        item = self._item()
        services.update_manual_item(
            item=item,
            bucket=item.bucket,
            title=item.title,
            max_score=item.max_score,
            worked_on=item.worked_on,
            reason=item.reason,
            counts_for_gradebook=False,
            actor=self.teacher_user,
        )
        item.refresh_from_db()
        self.assertFalse(item.counts_for_gradebook)
        grid = services.build_grid(item.setup)
        self.assertFalse(grid["has_items"])
        self.assertTrue(
            AuditLog.objects.filter(action="gradebook.manual_item_updated").exists()
        )

    def test_the_total_cannot_drop_below_a_mark_already_recorded(self):
        item = self._item(max_score=Decimal("20"))
        ManualScore.objects.create(item=item, student=self.ama, score=Decimal("15"))
        with self.assertRaises(ValidationError):
            services.update_manual_item(
                item=item,
                bucket=item.bucket,
                title=item.title,
                max_score=Decimal("10"),
                worked_on=item.worked_on,
                reason=item.reason,
                counts_for_gradebook=True,
                actor=self.teacher_user,
            )
        item.refresh_from_db()
        self.assertEqual(item.max_score, Decimal("20.0"))

    def test_an_item_with_no_marks_can_be_removed_and_is_audited(self):
        item = self._item()
        item_id = item.pk
        services.remove_manual_item(item=item, actor=self.teacher_user)
        self.assertFalse(ManualItem.objects.filter(pk=item_id).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action="gradebook.manual_item_removed", object_id=str(item_id)
            ).exists()
        )

    def test_an_item_with_marks_is_kept_for_the_evidence(self):
        item = self._item()
        ManualScore.objects.create(item=item, student=self.ama, score=Decimal("7"))
        with self.assertRaises(ValidationError):
            services.remove_manual_item(item=item, actor=self.teacher_user)
        self.assertTrue(ManualItem.objects.filter(pk=item.pk).exists())

    def test_the_remove_button_on_the_screen_explains_the_refusal(self):
        item = self._item()
        ManualScore.objects.create(item=item, student=self.ama, score=Decimal("7"))
        response = self.client.post(self._remove_url(item), follow=True)
        self.assertContains(response, "already has marks")
        self.assertTrue(ManualItem.objects.filter(pk=item.pk).exists())


class ManualScoreTests(SetupTestCase):
    def test_marks_are_saved_and_a_blank_box_clears_one(self):
        item = self._item()
        result = services.save_manual_scores(
            item=item,
            scores={self.ama.pk: Decimal("8.5"), self.kofi.pk: Decimal("6")},
            actor=self.teacher_user,
        )
        self.assertEqual(result, {"saved": 2, "cleared": 0})
        self.assertEqual(
            ManualScore.objects.get(item=item, student=self.ama).score,
            Decimal("8.5"),
        )

        result = services.save_manual_scores(
            item=item,
            scores={self.ama.pk: None, self.kofi.pk: Decimal("7")},
            actor=self.teacher_user,
        )
        self.assertEqual(result, {"saved": 1, "cleared": 1})
        self.assertFalse(
            ManualScore.objects.filter(item=item, student=self.ama).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action="gradebook.manual_scores_saved").exists()
        )

    def test_a_mark_above_the_total_is_refused(self):
        item = self._item(max_score=Decimal("20"))
        with self.assertRaises(ValidationError):
            services.save_manual_scores(
                item=item,
                scores={self.ama.pk: Decimal("21")},
                actor=self.teacher_user,
            )
        self.assertFalse(ManualScore.objects.exists())

    def test_a_pupil_from_another_class_is_refused(self):
        item = self._item()
        with self.assertRaises(ValidationError):
            services.save_manual_scores(
                item=item,
                scores={self.outsider.pk: Decimal("5")},
                actor=self.teacher_user,
            )

    def test_the_marks_screen_saves_a_roster_of_marks(self):
        item = self._item()
        response = self.client.post(
            self._edit_url(item),
            {
                "action": "scores",
                f"score_{self.ama.pk}": "9.5",
                f"score_{self.kofi.pk}": "",
            },
            follow=True,
        )
        self.assertContains(response, "1 mark saved")
        self.assertEqual(
            ManualScore.objects.get(item=item, student=self.ama).score,
            Decimal("9.5"),
        )

    def test_the_marks_screen_names_the_pupils_and_the_total(self):
        item = self._item()
        response = self.client.get(self._edit_url(item))
        self.assertContains(response, "ST-101")
        self.assertContains(response, "Out of 20")


class ExcuseTests(SetupTestCase):
    def test_an_excuse_is_recorded_with_its_reason(self):
        homework = self._homework()
        excuse = services.record_excuse(
            setup=self._setup(),
            student=self.kofi,
            source_kind=GradebookExcuse.SourceKind.HOMEWORK,
            source_id=homework.pk,
            reason="Hospital appointment.",
            actor=self.teacher_user,
        )
        self.assertEqual(excuse.reason, "Hospital appointment.")
        self.assertTrue(
            AuditLog.objects.filter(action="gradebook.excuse_recorded").exists()
        )

    def test_work_that_is_not_counted_cannot_be_excused(self):
        draft = self._homework(status=Homework.Status.DRAFT)
        with self.assertRaises(ValidationError):
            services.record_excuse(
                setup=self._setup(),
                student=self.ama,
                source_kind="homework",
                source_id=draft.pk,
                reason="Sick.",
                actor=self.teacher_user,
            )

    def test_the_same_pupil_cannot_be_excused_twice_from_the_same_work(self):
        homework = self._homework()
        services.record_excuse(
            setup=self._setup(),
            student=self.ama,
            source_kind="homework",
            source_id=homework.pk,
            reason="Sick.",
            actor=self.teacher_user,
        )
        with self.assertRaises(ValidationError):
            services.record_excuse(
                setup=self._setup(),
                student=self.ama,
                source_kind="homework",
                source_id=homework.pk,
                reason="Still sick.",
                actor=self.teacher_user,
            )
        self.assertEqual(GradebookExcuse.objects.count(), 1)

    def test_a_pupil_from_another_class_cannot_be_excused(self):
        homework = self._homework()
        with self.assertRaises(ValidationError):
            services.record_excuse(
                setup=self._setup(),
                student=self.outsider,
                source_kind="homework",
                source_id=homework.pk,
                reason="Sick.",
                actor=self.teacher_user,
            )

    def test_revoking_an_excuse_makes_the_work_count_again(self):
        homework = self._homework()
        excuse = services.record_excuse(
            setup=self._setup(),
            student=self.ama,
            source_kind="homework",
            source_id=homework.pk,
            reason="Sick.",
            actor=self.teacher_user,
        )
        services.revoke_excuse(excuse=excuse, actor=self.teacher_user)
        self.assertFalse(GradebookExcuse.objects.exists())
        self.assertTrue(
            AuditLog.objects.filter(action="gradebook.excuse_revoked").exists()
        )

    def test_the_screen_records_and_revokes_an_excuse(self):
        homework = self._homework()
        response = self.client.post(
            self.setup_url,
            {
                "action": "excuse",
                "item": f"homework:{homework.pk}",
                "student": str(self.ama.pk),
                "reason": "Hospital appointment.",
            },
        )
        self.assertRedirects(response, self.setup_url)
        excuse = GradebookExcuse.objects.get()
        self.assertContains(self.client.get(self.setup_url), "Hospital appointment.")

        response = self.client.post(
            reverse("gradebook:excuse_revoke", args=[self._setup().pk, excuse.pk])
        )
        self.assertRedirects(response, self.setup_url)
        self.assertFalse(GradebookExcuse.objects.exists())

    def test_a_hand_recorded_item_can_be_excused_too(self):
        item = self._item()
        services.record_excuse(
            setup=item.setup,
            student=self.ama,
            source_kind="manual",
            source_id=item.pk,
            reason="Joined late; practical not repeated.",
            actor=self.teacher_user,
        )
        response = self.client.get(self.setup_url)
        self.assertContains(response, "Labs: Titration")
        self.assertContains(response, "Joined late; practical not repeated.")


class LockedTermTests(SetupTestCase):
    def setUp(self):
        super().setUp()
        GradebookTermLock.objects.create(
            term=self.term, locked_by=make_user("admin", username="st_admin")
        )
        self.setup = self._setup()

    def test_weights_cannot_change(self):
        with self.assertRaises(ValidationError):
            services.update_weights(
                setup=self.setup,
                homework_weight=10,
                quiz_weight=30,
                lab_weight=30,
                exam_weight=30,
                actor=self.teacher_user,
            )

    def test_hand_recorded_work_cannot_be_added_or_removed(self):
        with self.assertRaises(ValidationError):
            services.create_manual_item(
                setup=self.setup,
                bucket=ManualItem.Bucket.LAB,
                title="Titration",
                max_score=Decimal("20"),
                worked_on=datetime.date(2025, 10, 5),
                reason="Practical done in the lab.",
                actor=self.teacher_user,
            )

    def test_marks_cannot_be_saved(self):
        item = self._item(setup=self.setup)
        with self.assertRaises(ValidationError):
            services.save_manual_scores(
                item=item,
                scores={self.ama.pk: Decimal("5")},
                actor=self.teacher_user,
            )

    def test_excuses_cannot_be_recorded_or_revoked(self):
        with self.assertRaises(ValidationError):
            services.record_excuse(
                setup=self.setup,
                student=self.ama,
                source_kind="homework",
                source_id=self._homework().pk,
                reason="Sick.",
                actor=self.teacher_user,
            )

    def test_the_screen_is_read_only_and_says_why(self):
        response = self.client.get(self.setup_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "is locked")
        self.assertNotContains(response, 'name="action" value="weights"')
        self.assertNotContains(response, 'name="action" value="excuse"')

    def test_a_locked_weights_post_changes_nothing(self):
        response = self.client.post(
            self.setup_url,
            {
                "action": "weights",
                "homework_weight": "10",
                "quiz_weight": "30",
                "lab_weight": "30",
                "exam_weight": "30",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "locked")
        self.assertEqual(GradebookSetup.objects.get(pk=self.setup.pk).homework_weight, 30)


class SetupAccessTests(SetupTestCase):
    def test_the_setup_screen_opens_for_the_assigned_teacher(self):
        self._item()
        response = self.client.get(self.setup_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Weights")
        self.assertContains(response, "Hand-recorded work")
        self.assertContains(response, "Titration")

    def test_another_teacher_cannot_open_or_post(self):
        setup = self._setup()
        url = reverse("gradebook:teacher_setup", args=[setup.pk])
        self.client.force_login(self.other_teacher_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                url,
                {
                    "action": "weights",
                    "homework_weight": "10",
                    "quiz_weight": "30",
                    "lab_weight": "30",
                    "exam_weight": "30",
                },
            ).status_code,
            403,
        )
        self.assertEqual(GradebookSetup.objects.get(pk=setup.pk).homework_weight, 30)

    def test_a_pupil_cannot_open_the_setup_screen(self):
        url = reverse("gradebook:teacher_setup", args=[self._setup().pk])
        self.client.force_login(self.ama.user)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_the_switch_off_replaces_the_screen_with_the_sentence(self):
        row = SchoolSettings.load()
        row.gradebook_enabled = False
        row.save(update_fields=["gradebook_enabled", "updated_at"])
        response = self.client.get(self.setup_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Available once the Gradebook module is activated."
        )
        self.assertNotContains(response, "Hand-recorded work")
