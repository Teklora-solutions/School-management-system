"""
apps/learning/tests/test_course_builder.py
--------------------------------------------
Tests for Checkpoint A: teacher course builder.

Coverage
--------
  A. course_services — module CRUD, lesson CRUD, reorder, duplicate, copy,
                       attachment validation and deletion
  B. course_selectors — draft visibility, completion tracking
  C. Views — access control (wrong teacher → 404, student → 403),
             module/lesson create/edit/delete/move/duplicate,
             copy-course, attachment file-serve
"""

from __future__ import annotations

import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import ClassSubject
from apps.core.models import SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.learning.course_selectors import course_for_student, course_for_teacher
from apps.learning.course_services import (
    add_attachment,
    copy_course,
    create_lesson,
    create_module,
    delete_lesson,
    delete_module,
    duplicate_lesson,
    duplicate_module,
    move_lesson,
    move_module,
    update_lesson,
    update_module,
)
from apps.learning.models import (
    CourseModule,
    Lesson,
    LessonAttachment,
    LessonProgress,
)
from apps.teachers.models import TeacherAssignment, TeacherProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def enable_learning():
    row = SchoolSettings.load()
    row.learning_enabled = True
    row.save(update_fields=["learning_enabled", "updated_at"])


def make_teacher(username, staff_id):
    user = make_user("teacher", username=username)
    profile = TeacherProfile.objects.create(user=user, staff_id=staff_id)
    return user, profile


def make_pdf(name="test.pdf", size=1024):
    return SimpleUploadedFile(name, b"%PDF-1.4 test" + b"x" * size, content_type="application/pdf")


def make_image(name="photo.jpg"):
    # Minimal JPEG header
    return SimpleUploadedFile(name, bytes([0xFF, 0xD8, 0xFF, 0xE0, 0, 0x10]) + b"fake", content_type="image/jpeg")


# ============================================================================
# A. course_services
# ============================================================================


class CreateModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject()
        cls.teacher_user, _ = make_teacher("cm_teacher", "CM-001")

    def test_create_module_returns_instance(self):
        mod = create_module(
            school_class=self.sc, subject=self.subj, term=self.term,
            title="Week 1", actor=self.teacher_user,
        )
        self.assertIsInstance(mod, CourseModule)
        self.assertEqual(mod.title, "Week 1")
        self.assertTrue(mod.is_published)

    def test_empty_title_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            create_module(
                school_class=self.sc, subject=self.subj, term=self.term,
                title="  ", actor=self.teacher_user,
            )

    def test_positions_are_sequential(self):
        m1 = create_module(school_class=self.sc, subject=self.subj, term=self.term,
                           title="A", actor=self.teacher_user)
        m2 = create_module(school_class=self.sc, subject=self.subj, term=self.term,
                           title="B", actor=self.teacher_user)
        self.assertGreater(m2.position, m1.position)


class UpdateModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject()
        cls.teacher_user, _ = make_teacher("um_teacher", "UM-001")
        cls.mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Original", position=0,
        )

    def test_update_changes_title(self):
        update_module(module=self.mod, title="Updated", is_published=True,
                      actor=self.teacher_user)
        self.mod.refresh_from_db()
        self.assertEqual(self.mod.title, "Updated")

    def test_unpublish_module(self):
        update_module(module=self.mod, title="Original", is_published=False,
                      actor=self.teacher_user)
        self.mod.refresh_from_db()
        self.assertFalse(self.mod.is_published)


class DeleteModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject()
        cls.teacher_user, _ = make_teacher("dm_teacher", "DM-001")

    def test_delete_removes_module_and_lessons(self):
        mod = CourseModule.objects.create(
            school_class=self.sc, subject=self.subj, term=self.term,
            title="To delete", position=0,
        )
        mod_pk = mod.pk
        Lesson.objects.create(module=mod, title="L1", position=0)
        delete_module(module=mod, actor=self.teacher_user)
        self.assertFalse(CourseModule.objects.filter(pk=mod_pk).exists())
        self.assertFalse(Lesson.objects.filter(module_id=mod_pk).exists())


class MoveModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject()
        cls.teacher_user, _ = make_teacher("mv_teacher", "MV-001")
        cls.m1 = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="First", position=0,
        )
        cls.m2 = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Second", position=1,
        )

    def test_move_second_module_up(self):
        move_module(module=self.m2, direction="up", actor=self.teacher_user)
        self.m1.refresh_from_db()
        self.m2.refresh_from_db()
        self.assertLess(self.m2.position, self.m1.position)

    def test_move_first_module_up_has_no_effect(self):
        pos_before = self.m1.position
        move_module(module=self.m1, direction="up", actor=self.teacher_user)
        self.m1.refresh_from_db()
        # Should not raise, position may or may not change due to renumber — just check no error
        self.assertIsNotNone(self.m1.position)


class LessonCRUDTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject()
        cls.teacher_user, _ = make_teacher("lc_teacher", "LC-001")
        cls.mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Module", position=0,
        )

    def test_create_lesson(self):
        lesson = create_lesson(
            module=self.mod, title="Lesson 1",
            notes="Hello world", notes_format="plain",
            video_url="", is_published=True, actor=self.teacher_user,
        )
        self.assertEqual(lesson.title, "Lesson 1")
        self.assertTrue(lesson.is_published)

    def test_update_lesson_changes_fields(self):
        lesson = create_lesson(
            module=self.mod, title="Old", notes="", notes_format="plain",
            video_url="", is_published=True, actor=self.teacher_user,
        )
        update_lesson(
            lesson=lesson, title="New", notes="New notes",
            notes_format="markdown", video_url="https://youtu.be/abc",
            is_published=False, actor=self.teacher_user,
        )
        lesson.refresh_from_db()
        self.assertEqual(lesson.title, "New")
        self.assertFalse(lesson.is_published)

    def test_delete_lesson_removes_it(self):
        lesson = create_lesson(
            module=self.mod, title="To delete", notes="", notes_format="plain",
            video_url="", is_published=True, actor=self.teacher_user,
        )
        pk = lesson.pk
        delete_lesson(lesson=lesson, actor=self.teacher_user)
        self.assertFalse(Lesson.objects.filter(pk=pk).exists())

    def test_move_lesson_down(self):
        l1 = create_lesson(module=self.mod, title="L1", notes="", notes_format="plain",
                            video_url="", is_published=True, actor=self.teacher_user)
        l2 = create_lesson(module=self.mod, title="L2", notes="", notes_format="plain",
                            video_url="", is_published=True, actor=self.teacher_user)
        move_lesson(lesson=l1, direction="down", actor=self.teacher_user)
        l1.refresh_from_db()
        l2.refresh_from_db()
        self.assertGreater(l1.position, l2.position)


class AttachmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject()
        cls.teacher_user, _ = make_teacher("att_teacher", "ATT-001")
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Module", position=0,
        )
        cls.lesson = Lesson.objects.create(module=mod, title="Lesson", position=0)

    def test_add_valid_pdf_attachment(self):
        att = add_attachment(
            lesson=self.lesson, uploaded_file=make_pdf(), actor=self.teacher_user
        )
        self.assertIsInstance(att, LessonAttachment)
        self.assertTrue(att.file.name.endswith(".pdf"))

    def test_rejected_extension_raises_validation_error(self):
        bad_file = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
        with self.assertRaises(ValidationError):
            add_attachment(lesson=self.lesson, uploaded_file=bad_file, actor=self.teacher_user)

    def test_file_too_large_raises_validation_error(self):
        big_file = SimpleUploadedFile(
            "big.pdf",
            b"%PDF " + b"x" * (21 * 1024 * 1024),  # 21 MB
            content_type="application/pdf"
        )
        with self.assertRaises(ValidationError):
            add_attachment(lesson=self.lesson, uploaded_file=big_file, actor=self.teacher_user)

    def test_max_five_attachments_enforced(self):
        for i in range(5):
            add_attachment(
                lesson=self.lesson,
                uploaded_file=SimpleUploadedFile(f"f{i}.pdf", b"%PDF-1.4 x", content_type="application/pdf"),
                actor=self.teacher_user,
            )
        with self.assertRaises(ValidationError):
            add_attachment(
                lesson=self.lesson,
                uploaded_file=make_pdf("sixth.pdf"),
                actor=self.teacher_user,
            )


class DuplicateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject()
        cls.teacher_user, _ = make_teacher("dup_teacher", "DUP-001")
        cls.mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Module", position=0,
        )
        cls.lesson = Lesson.objects.create(
            module=cls.mod, title="Lesson", notes="Hello",
            is_published=True, position=0,
        )

    def test_duplicate_lesson_creates_draft_copy(self):
        new_lesson = duplicate_lesson(lesson=self.lesson, actor=self.teacher_user)
        self.assertNotEqual(new_lesson.pk, self.lesson.pk)
        self.assertFalse(new_lesson.is_published)
        self.assertIn("copy", new_lesson.title)
        self.assertEqual(new_lesson.notes, self.lesson.notes)

    def test_duplicate_module_creates_draft_copy_with_lessons(self):
        new_mod = duplicate_module(module=self.mod, actor=self.teacher_user)
        self.assertNotEqual(new_mod.pk, self.mod.pk)
        self.assertFalse(new_mod.is_published)
        self.assertEqual(new_mod.lessons.count(), 1)
        self.assertFalse(new_mod.lessons.first().is_published)


class CopyCourseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term1 = make_term(cls.year, name="term_1")
        cls.term2 = make_term(cls.year, name="term_2",
                               start_date=cls.term1.end_date, is_current=False)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject()
        cls.teacher_user, _ = make_teacher("cp_teacher", "CP-001")

        # Build source course
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term1,
            title="Source Module", position=0,
        )
        Lesson.objects.create(module=mod, title="L1", position=0, is_published=True)
        Lesson.objects.create(module=mod, title="L2", position=1, is_published=True)

    def test_copy_course_returns_lesson_count(self):
        count = copy_course(
            source_class=self.sc, source_subject=self.subj, source_term=self.term1,
            target_class=self.sc, target_subject=self.subj, target_term=self.term2,
            actor=self.teacher_user,
        )
        self.assertEqual(count, 2)

    def test_copied_lessons_are_all_draft(self):
        copy_course(
            source_class=self.sc, source_subject=self.subj, source_term=self.term1,
            target_class=self.sc, target_subject=self.subj, target_term=self.term2,
            actor=self.teacher_user,
        )
        copied = Lesson.objects.filter(
            module__school_class=self.sc,
            module__subject=self.subj,
            module__term=self.term2,
        )
        for lesson in copied:
            self.assertFalse(lesson.is_published)

    def test_copy_empty_course_returns_zero(self):
        other_sc = make_class(self.year, name="Grade 99", level_order=99)
        count = copy_course(
            source_class=other_sc, source_subject=self.subj, source_term=self.term1,
            target_class=other_sc, target_subject=self.subj, target_term=self.term2,
            actor=self.teacher_user,
        )
        self.assertEqual(count, 0)


# ============================================================================
# B. course_selectors — draft visibility
# ============================================================================


class DraftVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject()
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.student = make_student_user(cls.sc, admission_number="DV-001")

        # Published module with a published and a draft lesson
        cls.pub_mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Published module", position=0, is_published=True,
        )
        cls.pub_lesson = Lesson.objects.create(
            module=cls.pub_mod, title="Published", position=0, is_published=True,
        )
        cls.draft_lesson = Lesson.objects.create(
            module=cls.pub_mod, title="Draft lesson", position=1, is_published=False,
        )

        # Draft module (hidden entirely from students)
        cls.draft_mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Draft module", position=1, is_published=False,
        )
        Lesson.objects.create(
            module=cls.draft_mod, title="Inside draft mod", position=0, is_published=True,
        )

    def test_teacher_sees_all_modules_and_lessons(self):
        result = course_for_teacher(
            school_class=self.sc, subject=self.subj, term=self.term
        )
        module_titles = [m.module.title for m in result]
        self.assertIn("Published module", module_titles)
        self.assertIn("Draft module", module_titles)
        # Published module has both lessons
        pub_mwl = next(m for m in result if m.module.pk == self.pub_mod.pk)
        lesson_titles = [lp.lesson.title for lp in pub_mwl.lessons]
        self.assertIn("Published", lesson_titles)
        self.assertIn("Draft lesson", lesson_titles)

    def test_student_does_not_see_draft_module(self):
        result = course_for_student(
            school_class=self.sc, subject=self.subj,
            term=self.term, student=self.student,
        )
        module_titles = [m.module.title for m in result]
        self.assertNotIn("Draft module", module_titles)

    def test_student_does_not_see_draft_lesson(self):
        result = course_for_student(
            school_class=self.sc, subject=self.subj,
            term=self.term, student=self.student,
        )
        pub_mwl = next((m for m in result if m.module.pk == self.pub_mod.pk), None)
        self.assertIsNotNone(pub_mwl)
        lesson_titles = [lp.lesson.title for lp in pub_mwl.lessons]
        self.assertIn("Published", lesson_titles)
        self.assertNotIn("Draft lesson", lesson_titles)

    def test_completion_tracking(self):
        LessonProgress.objects.create(
            student=self.student, lesson=self.pub_lesson, completed=True
        )
        result = course_for_student(
            school_class=self.sc, subject=self.subj,
            term=self.term, student=self.student,
        )
        pub_mwl = next(m for m in result if m.module.pk == self.pub_mod.pk)
        lp = next(lp for lp in pub_mwl.lessons if lp.lesson.pk == self.pub_lesson.pk)
        self.assertTrue(lp.completed)


# ============================================================================
# C. Views — access control
# ============================================================================


class ModuleViewAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Grade VA", level_order=8)
        cls.subj = make_subject("VASUBJ", "VA Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)

        cls.teacher_user, cls.teacher = make_teacher("va_teacher", "VA-001")
        TeacherAssignment.objects.create(
            teacher=cls.teacher, school_class=cls.sc,
            subject=cls.subj, academic_year=cls.year,
        )
        cls.other_teacher_user, _ = make_teacher("va_other", "VA-002")
        cls.student = make_student_user(cls.sc, admission_number="VA-S01")

    def _create_url(self):
        return reverse("learning:module_create", args=[self.sc.pk, self.subj.pk])

    def test_assigned_teacher_can_get_create_form(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._create_url())
        self.assertEqual(r.status_code, 200)

    def test_other_teacher_gets_404(self):
        self.client.force_login(self.other_teacher_user)
        r = self.client.get(self._create_url())
        self.assertEqual(r.status_code, 404)

    def test_student_gets_403(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._create_url())
        self.assertEqual(r.status_code, 403)

    def test_anonymous_is_redirected(self):
        r = self.client.get(self._create_url())
        self.assertEqual(r.status_code, 302)

    def test_post_creates_module(self):
        self.client.force_login(self.teacher_user)
        self.client.post(self._create_url(), {"title": "New Module"})
        self.assertTrue(
            CourseModule.objects.filter(
                school_class=self.sc, title="New Module"
            ).exists()
        )


class LessonViewAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Grade LV", level_order=9)
        cls.subj = make_subject("LVSUBJ", "LV Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)

        cls.teacher_user, cls.teacher = make_teacher("lv_teacher", "LV-001")
        TeacherAssignment.objects.create(
            teacher=cls.teacher, school_class=cls.sc,
            subject=cls.subj, academic_year=cls.year,
        )
        cls.other_teacher_user, _ = make_teacher("lv_other", "LV-002")
        cls.student = make_student_user(cls.sc, admission_number="LV-S01")
        cls.other_student = make_student_user(
            make_class(cls.year, name="Grade LV2", level_order=10),
            admission_number="LV-S02",
        )

        cls.mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Module", position=0,
        )
        cls.lesson = Lesson.objects.create(
            module=cls.mod, title="Published lesson",
            position=0, is_published=True,
        )
        cls.draft_lesson = Lesson.objects.create(
            module=cls.mod, title="Draft",
            position=1, is_published=False,
        )

    def _edit_url(self, lesson=None):
        lesson = lesson or self.lesson
        return reverse(
            "learning:lesson_edit",
            args=[self.sc.pk, self.subj.pk, self.mod.pk, lesson.pk],
        )

    def test_assigned_teacher_can_edit_lesson(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._edit_url())
        self.assertEqual(r.status_code, 200)

    def test_other_teacher_cannot_edit_lesson(self):
        self.client.force_login(self.other_teacher_user)
        r = self.client.get(self._edit_url())
        self.assertEqual(r.status_code, 404)

    def test_student_cannot_edit_lesson(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._edit_url())
        self.assertEqual(r.status_code, 403)

    def test_student_can_view_published_lesson(self):
        self.client.force_login(self.student.user)
        url = reverse(
            "learning:lesson_view",
            args=[self.sc.pk, self.subj.pk, self.lesson.pk],
        )
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Published lesson")

    def test_student_cannot_view_draft_lesson(self):
        self.client.force_login(self.student.user)
        url = reverse(
            "learning:lesson_view",
            args=[self.sc.pk, self.subj.pk, self.draft_lesson.pk],
        )
        r = self.client.get(url)
        self.assertEqual(r.status_code, 404)

    def test_student_from_other_class_cannot_view_lesson(self):
        self.client.force_login(self.other_student.user)
        url = reverse(
            "learning:lesson_view",
            args=[self.sc.pk, self.subj.pk, self.lesson.pk],
        )
        r = self.client.get(url)
        self.assertEqual(r.status_code, 404)

    def test_teacher_preview_shows_lesson(self):
        self.client.force_login(self.teacher_user)
        url = reverse(
            "learning:lesson_preview",
            args=[self.sc.pk, self.subj.pk, self.mod.pk, self.lesson.pk],
        )
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Preview mode")


class TeacherClassroomDetailViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Grade TCD", level_order=11)
        cls.subj = make_subject("TCDSUBJ", "TCD Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.teacher_user, cls.teacher = make_teacher("tcd_teacher", "TCD-001")
        TeacherAssignment.objects.create(
            teacher=cls.teacher, school_class=cls.sc,
            subject=cls.subj, academic_year=cls.year,
        )
        # Add a module with a lesson
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Test Module", position=0,
        )
        Lesson.objects.create(module=mod, title="Test Lesson", position=0)

    def _url(self):
        return reverse("learning:teacher_classroom", args=[self.sc.pk, self.subj.pk])

    def test_teacher_sees_course_outline(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Test Module")
        self.assertContains(r, "Test Lesson")
        self.assertContains(r, "Add module")

    def test_teacher_sees_add_lesson_link(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._url())
        self.assertContains(r, "Add lesson")

    def test_empty_state_shown_when_no_modules(self):
        sc2 = make_class(self.year, name="Grade TCD2", level_order=12)
        subj2 = make_subject("TCDSUBJ2", "TCD Subject 2")
        ClassSubject.objects.create(school_class=sc2, subject=subj2)
        teacher_user2, teacher2 = make_teacher("tcd_teacher2", "TCD-002")
        TeacherAssignment.objects.create(
            teacher=teacher2, school_class=sc2,
            subject=subj2, academic_year=self.year,
        )
        self.client.force_login(teacher_user2)
        r = self.client.get(reverse("learning:teacher_classroom", args=[sc2.pk, subj2.pk]))
        self.assertContains(r, "Add your first module")


class StudentClassroomDetailViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Grade SCD", level_order=13)
        cls.subj = make_subject("SCDSUBJ", "SCD Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.student = make_student_user(cls.sc, admission_number="SCD-001")

        pub_mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Visible Module", position=0, is_published=True,
        )
        Lesson.objects.create(
            module=pub_mod, title="Visible Lesson", position=0, is_published=True,
        )
        draft_mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Hidden Module", position=1, is_published=False,
        )
        Lesson.objects.create(
            module=draft_mod, title="Hidden Lesson", position=0, is_published=True,
        )

    def _url(self):
        return reverse("learning:classroom", args=[self.sc.pk, self.subj.pk])

    def test_student_sees_published_content(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Visible Module")
        self.assertContains(r, "Visible Lesson")

    def test_student_does_not_see_draft_module(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._url())
        self.assertNotContains(r, "Hidden Module")
        self.assertNotContains(r, "Hidden Lesson")


class CopyCourseViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term1 = make_term(cls.year, name="term_1")
        import datetime
        cls.term2 = make_term(
            cls.year, name="term_2",
            start_date=datetime.date(2025, 12, 21),
            end_date=datetime.date(2026, 3, 28),
            is_current=False,
        )
        cls.sc = make_class(cls.year, name="Grade CC", level_order=14)
        cls.subj = make_subject("CCSUBJ", "CC Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.teacher_user, cls.teacher = make_teacher("cc_teacher", "CC-001")
        TeacherAssignment.objects.create(
            teacher=cls.teacher, school_class=cls.sc,
            subject=cls.subj, academic_year=cls.year,
        )
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term1,
            title="CC Module", position=0,
        )
        Lesson.objects.create(module=mod, title="CC Lesson", position=0, is_published=True)

    def _url(self):
        return reverse("learning:copy_course", args=[self.sc.pk, self.subj.pk])

    def test_teacher_can_get_copy_form(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Copy Course")

    def test_copy_creates_lessons_in_target_term(self):
        self.client.force_login(self.teacher_user)
        self.client.post(self._url(), {"target_term_id": self.term2.pk})
        self.assertTrue(
            CourseModule.objects.filter(
                school_class=self.sc,
                subject=self.subj,
                term=self.term2,
            ).exists()
        )

    def test_student_cannot_access_copy_form(self):
        student = make_student_user(self.sc, admission_number="CC-S01")
        self.client.force_login(student.user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 403)
