"""
apps/learning/tests/test_checkpoint_b.py
------------------------------------------
Tests for Checkpoint B: lesson page, Markdown rendering, XSS prevention,
student notes, next/prev navigation, and course search.

Coverage
--------
  A. markdown_utils.render_notes — XSS prevention, plain text, Markdown
  B. lesson_notes_services — save/load, max-length cap, parent restriction
  C. course_selectors — next/prev lesson, search
  D. Views — StudentLessonView, SaveLessonNoteView, CourseSearchView
             (access control, note privacy, search results)
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import ClassSubject
from apps.accounts.models import ParentProfile
from apps.core.models import SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_user,
    make_year,
)
from apps.learning.course_selectors import (
    next_lesson_for_student,
    prev_lesson_for_student,
    search_course,
)
from apps.learning.lesson_notes_services import get_lesson_note, save_lesson_note
from apps.learning.markdown_utils import render_notes
from apps.learning.models import (
    CourseModule,
    Lesson,
    LessonProgress,
    StudentLessonNote,
)
from apps.students.models import StudentGuardian
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


def make_parent_for(student):
    parent_user = make_user("parent", username=f"par_{student.admission_number}")
    profile = ParentProfile.objects.create(user=parent_user)
    StudentGuardian.objects.create(student=student, parent=profile, is_primary=True)
    return parent_user, profile


# ============================================================================
# A. markdown_utils.render_notes
# ============================================================================


class RenderNotesXSSTests(TestCase):
    """Script injection must never survive render_notes()."""

    def test_script_tag_stripped_from_plain(self):
        html = render_notes("<script>alert('xss')</script>", notes_format="plain")
        # Plain text is HTML-escaped — the tag becomes &lt;script&gt; (safe, not executable)
        self.assertNotIn("<script>", html)
        self.assertNotIn("</script>", html)

    def test_script_tag_stripped_from_markdown(self):
        html = render_notes(
            "Hello\n\n<script>evil()</script>\n\nWorld",
            notes_format="markdown",
        )
        self.assertNotIn("<script>", html)
        self.assertNotIn("</script>", html)

    def test_on_click_attribute_stripped(self):
        html = render_notes(
            "[click me](javascript:evil())",
            notes_format="markdown",
        )
        self.assertNotIn("javascript:", html)
        self.assertNotIn("evil()", html)

    def test_iframe_stripped(self):
        html = render_notes(
            '<iframe src="https://evil.com"></iframe>',
            notes_format="markdown",
        )
        self.assertNotIn("<iframe", html)

    def test_event_handler_attribute_stripped(self):
        html = render_notes(
            '<img src="x" onerror="evil()">',
            notes_format="markdown",
        )
        self.assertNotIn("onerror", html)

    def test_style_attribute_stripped(self):
        html = render_notes(
            '<p style="color:red">text</p>',
            notes_format="markdown",
        )
        self.assertNotIn('style=', html)


class RenderNotesMarkdownTests(TestCase):
    """Markdown content renders to the expected HTML structure."""

    def test_heading_renders(self):
        html = render_notes("# Hello World", notes_format="markdown")
        self.assertIn("<h1>", html)
        self.assertIn("Hello World", html)

    def test_bold_renders(self):
        html = render_notes("**bold text**", notes_format="markdown")
        self.assertIn("<strong>", html)

    def test_bullet_list_renders(self):
        html = render_notes("- item one\n- item two", notes_format="markdown")
        self.assertIn("<ul>", html)
        self.assertIn("<li>", html)

    def test_code_block_renders(self):
        html = render_notes("```\ncode here\n```", notes_format="markdown")
        self.assertIn("<code>", html)

    def test_table_renders(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = render_notes(md, notes_format="markdown")
        self.assertIn("<table>", html)

    def test_links_get_target_blank(self):
        html = render_notes(
            "[visit](https://example.com)", notes_format="markdown"
        )
        self.assertIn('target="_blank"', html)
        self.assertIn("noopener", html)

    def test_empty_string_returns_empty(self):
        self.assertEqual(render_notes("", notes_format="markdown"), "")

    def test_plain_format_preserves_line_breaks(self):
        html = render_notes("line one\nline two", notes_format="plain")
        self.assertIn("line one", html)
        self.assertIn("line two", html)
        # Should not apply markdown interpretation
        self.assertNotIn("<h1>", html)


# ============================================================================
# B. lesson_notes_services
# ============================================================================


class LessonNoteServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject()
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.student = make_student_user(cls.sc, admission_number="LN-001")
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Module", position=0,
        )
        cls.lesson = Lesson.objects.create(module=mod, title="Lesson", position=0)

    def test_save_creates_note(self):
        note = save_lesson_note(
            student=self.student, lesson=self.lesson, content="My thoughts"
        )
        self.assertEqual(note.content, "My thoughts")
        self.assertEqual(note.student, self.student)
        self.assertEqual(note.lesson, self.lesson)

    def test_save_updates_existing_note(self):
        save_lesson_note(student=self.student, lesson=self.lesson, content="First")
        save_lesson_note(student=self.student, lesson=self.lesson, content="Updated")
        count = StudentLessonNote.objects.filter(
            student=self.student, lesson=self.lesson
        ).count()
        self.assertEqual(count, 1)
        note = StudentLessonNote.objects.get(student=self.student, lesson=self.lesson)
        self.assertEqual(note.content, "Updated")

    def test_note_capped_at_5000_chars(self):
        long_text = "x" * 6000
        note = save_lesson_note(
            student=self.student, lesson=self.lesson, content=long_text
        )
        self.assertEqual(len(note.content), 5000)

    def test_get_lesson_note_returns_none_when_no_note(self):
        other_student = make_student_user(self.sc, admission_number="LN-002")
        result = get_lesson_note(student=other_student, lesson=self.lesson)
        self.assertIsNone(result)

    def test_get_lesson_note_returns_saved_note(self):
        save_lesson_note(
            student=self.student, lesson=self.lesson, content="Hello"
        )
        note = get_lesson_note(student=self.student, lesson=self.lesson)
        self.assertIsNotNone(note)
        self.assertEqual(note.content, "Hello")


# ============================================================================
# C. course_selectors — next/prev and search
# ============================================================================


class NextPrevLessonTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject("NPSUBJ", "NP Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.student = make_student_user(cls.sc, admission_number="NP-001")

        mod1 = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Mod 1", position=0, is_published=True,
        )
        mod2 = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Mod 2", position=1, is_published=True,
        )
        cls.l1 = Lesson.objects.create(module=mod1, title="L1", position=0, is_published=True)
        cls.l2 = Lesson.objects.create(module=mod1, title="L2", position=1, is_published=True)
        cls.l3 = Lesson.objects.create(module=mod2, title="L3", position=0, is_published=True)

    def _nav_kwargs(self, lesson_id):
        return dict(
            lesson_id=lesson_id,
            school_class=self.sc,
            subject=self.subj,
            term=self.term,
            student=self.student,
        )

    def test_next_lesson_from_first(self):
        nxt = next_lesson_for_student(**self._nav_kwargs(self.l1.pk))
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.lesson.pk, self.l2.pk)

    def test_next_lesson_crosses_module_boundary(self):
        nxt = next_lesson_for_student(**self._nav_kwargs(self.l2.pk))
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.lesson.pk, self.l3.pk)

    def test_no_next_lesson_from_last(self):
        nxt = next_lesson_for_student(**self._nav_kwargs(self.l3.pk))
        self.assertIsNone(nxt)

    def test_prev_lesson_from_second(self):
        prv = prev_lesson_for_student(**self._nav_kwargs(self.l2.pk))
        self.assertIsNotNone(prv)
        self.assertEqual(prv.lesson.pk, self.l1.pk)

    def test_no_prev_lesson_from_first(self):
        prv = prev_lesson_for_student(**self._nav_kwargs(self.l1.pk))
        self.assertIsNone(prv)

    def test_prev_crosses_module_boundary(self):
        prv = prev_lesson_for_student(**self._nav_kwargs(self.l3.pk))
        self.assertIsNotNone(prv)
        self.assertEqual(prv.lesson.pk, self.l2.pk)

    def test_draft_lesson_not_in_navigation(self):
        draft = Lesson.objects.create(
            module=self.l1.module, title="Draft", position=5, is_published=False
        )
        nxt = next_lesson_for_student(**self._nav_kwargs(self.l2.pk))
        # Should skip the draft and go to l3, not draft
        if nxt:
            self.assertNotEqual(nxt.lesson.pk, draft.pk)


class CourseSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year)
        cls.subj = make_subject("SRCHSUBJ", "Search Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.student = make_student_user(cls.sc, admission_number="SR-001")

        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Module", position=0, is_published=True,
        )
        Lesson.objects.create(
            module=mod, title="Introduction to Photosynthesis",
            notes="Plants use sunlight to make food.", position=0, is_published=True,
        )
        Lesson.objects.create(
            module=mod, title="Cell Structure",
            notes="The nucleus controls the cell.", position=1, is_published=True,
        )
        Lesson.objects.create(
            module=mod, title="Draft Topic",
            notes="Hidden content.", position=2, is_published=False,
        )

    def _search(self, query):
        return search_course(
            school_class=self.sc, subject=self.subj,
            term=self.term, student=self.student, query=query,
        )

    def test_search_by_title(self):
        results = self._search("photosynthesis")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].lesson.title, "Introduction to Photosynthesis")

    def test_search_by_notes_content(self):
        results = self._search("nucleus")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].lesson.title, "Cell Structure")

    def test_search_case_insensitive(self):
        results = self._search("SUNLIGHT")
        self.assertEqual(len(results), 1)

    def test_empty_query_returns_empty(self):
        results = self._search("")
        self.assertEqual(results, [])

    def test_draft_lesson_excluded_from_search(self):
        results = self._search("hidden")
        self.assertEqual(len(results), 0)

    def test_no_match_returns_empty(self):
        results = self._search("quantum mechanics")
        self.assertEqual(len(results), 0)


# ============================================================================
# D. Views
# ============================================================================


class StudentLessonViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Grade LV2", level_order=15)
        cls.subj = make_subject("SLVSUBJ", "SLV Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.student = make_student_user(cls.sc, admission_number="SL2-001")
        cls.parent_user, _ = make_parent_for(cls.student)
        cls.teacher_user, cls.teacher = make_teacher("slv2_teacher", "SLV2-001")
        TeacherAssignment.objects.create(
            teacher=cls.teacher, school_class=cls.sc,
            subject=cls.subj, academic_year=cls.year,
        )
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Module", position=0, is_published=True,
        )
        cls.lesson = Lesson.objects.create(
            module=mod, title="Published Lesson",
            notes="# Hello\n\n**Bold text** and a list:\n\n- item 1\n- item 2",
            notes_format="markdown",
            position=0, is_published=True,
        )
        cls.next_lesson = Lesson.objects.create(
            module=mod, title="Next Lesson", position=1, is_published=True,
        )

    def _url(self):
        return reverse(
            "learning:lesson_view",
            args=[self.sc.pk, self.subj.pk, self.lesson.pk],
        )

    def test_student_sees_lesson_page(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Published Lesson")

    def test_markdown_renders_html(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._url())
        # Should contain rendered HTML, not raw Markdown
        self.assertContains(r, "<h1>")
        self.assertContains(r, "<strong>")
        self.assertContains(r, "<ul>")
        # Raw Markdown syntax should not appear as-is
        self.assertNotContains(r, "**Bold text**")

    def test_xss_stripped_in_notes(self):
        from apps.learning.course_services import update_lesson
        # Inject a script tag — reuse the teacher already assigned to this class
        update_lesson(
            lesson=self.lesson,
            title=self.lesson.title,
            notes='<script>evil()</script>\n\n# Real heading',
            notes_format="markdown",
            video_url="",
            is_published=True,
            actor=self.teacher_user,
        )
        self.client.force_login(self.student.user)
        r = self.client.get(self._url())
        # The <script> tag must never reach the browser as executable HTML
        self.assertNotContains(r, "<script>evil")
        self.assertContains(r, "<h1>")   # but the rest of the content still renders
        # Restore
        update_lesson(
            lesson=self.lesson,
            title=self.lesson.title,
            notes="# Hello\n\n**Bold text**\n\n- item 1\n- item 2",
            notes_format="markdown",
            video_url="",
            is_published=True,
            actor=self.teacher_user,
        )

    def test_next_lesson_link_present(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._url())
        self.assertContains(r, "Next Lesson")

    def test_parent_can_view_lesson(self):
        self.client.force_login(self.parent_user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)

    def test_parent_sees_no_notes_textarea(self):
        self.client.force_login(self.parent_user)
        r = self.client.get(self._url())
        self.assertNotContains(r, 'id="my-notes-textarea"')

    def test_student_sees_notes_textarea(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._url())
        self.assertContains(r, 'id="my-notes-textarea"')

    def test_teacher_cannot_access_student_lesson_view(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 403)

    def test_anonymous_redirected(self):
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 302)


class SaveLessonNoteViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Grade NV", level_order=16)
        cls.subj = make_subject("NVSUBJ", "NV Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.student = make_student_user(cls.sc, admission_number="NV-001")
        cls.parent_user, _ = make_parent_for(cls.student)
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Mod", position=0, is_published=True,
        )
        cls.lesson = Lesson.objects.create(
            module=mod, title="Lesson", position=0, is_published=True,
        )

    def _url(self):
        return reverse(
            "learning:save_lesson_note",
            args=[self.sc.pk, self.subj.pk, self.lesson.pk],
        )

    def test_student_can_save_note(self):
        self.client.force_login(self.student.user)
        r = self.client.post(
            self._url(), {"content": "My revision notes"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertTrue(
            StudentLessonNote.objects.filter(
                student=self.student, lesson=self.lesson
            ).exists()
        )

    def test_note_is_private_to_student(self):
        """Another student cannot access the first student's note."""
        other = make_student_user(self.sc, admission_number="NV-002")
        save_lesson_note(student=self.student, lesson=self.lesson, content="Secret")
        note = get_lesson_note(student=other, lesson=self.lesson)
        self.assertIsNone(note)

    def test_parent_cannot_save_note(self):
        self.client.force_login(self.parent_user)
        r = self.client.post(self._url(), {"content": "Parent note"})
        self.assertEqual(r.status_code, 403)

    def test_get_returns_404(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 404)


class CourseSearchViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_learning()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Grade SRV", level_order=17)
        cls.subj = make_subject("SRVSUBJ", "SRV Subject")
        ClassSubject.objects.create(school_class=cls.sc, subject=cls.subj)
        cls.student = make_student_user(cls.sc, admission_number="SRV-001")
        cls.other_student = make_student_user(
            make_class(cls.year, name="Grade SRV2", level_order=18),
            admission_number="SRV-002",
        )
        mod = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Module", position=0, is_published=True,
        )
        Lesson.objects.create(
            module=mod, title="Mitosis and Cell Division",
            notes="Cells divide by mitosis.", position=0, is_published=True,
        )

    def _url(self):
        return reverse("learning:course_search", args=[self.sc.pk, self.subj.pk])

    def test_student_can_search(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._url() + "?q=mitosis")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Mitosis and Cell Division")

    def test_empty_query_shows_no_results(self):
        self.client.force_login(self.student.user)
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Mitosis")

    def test_student_from_other_class_gets_404(self):
        self.client.force_login(self.other_student.user)
        r = self.client.get(self._url() + "?q=mitosis")
        self.assertEqual(r.status_code, 404)

    def test_anonymous_redirected(self):
        r = self.client.get(self._url() + "?q=test")
        self.assertEqual(r.status_code, 302)
