"""Organise/release checkpoint: read-time schedules, targeting and moderation."""

from __future__ import annotations

import datetime
from html.parser import HTMLParser
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.academics.models import ClassSubject
from apps.core.models import AuditLog, SchoolSettings
from apps.dashboards.tests.fixtures import (
    make_class,
    make_student_user,
    make_subject,
    make_term,
    make_year,
)
from apps.learning import comment_services, course_selectors, topic_services
from apps.learning.engagement_selectors import (
    class_progress_overview,
    director_engagement_overview,
    student_progress,
)
from apps.learning.models import (
    ClassEngagementSettings,
    CourseModule,
    Lesson,
    LessonAttachment,
    LessonComment,
    LessonProgress,
    Quiz,
    QuizAttempt,
    QuizAttemptAnswer,
    Topic,
)
from apps.learning.quiz_selectors import teacher_quiz_results
from apps.learning.release import is_released_at, release_status
from apps.learning.tests.test_course_builder import make_teacher
from apps.learning.tests.test_quizzes import enable_quizzes, make_full_quiz, make_parent_for
from apps.teachers.models import TeacherAssignment


NOW = datetime.datetime(2025, 10, 6, 9, tzinfo=datetime.timezone.utc)
TICK = datetime.timedelta(microseconds=1)
END = NOW + datetime.timedelta(hours=1)


class TestReleaseBoundaries(SimpleTestCase):
    """The start is inclusive; the hide time is exclusive, for all three models."""

    def objects(self, **fields):
        return [model(**fields) for model in (CourseModule, Lesson, Quiz)]

    def assert_status(self, obj, clock, expected):
        self.assertEqual(release_status(obj, now=clock), expected)
        self.assertEqual(is_released_at(obj, now=clock), expected == "published")

    def test_no_schedule_is_immediately_released(self):
        for obj in self.objects(is_published=True):
            with self.subTest(model=type(obj).__name__):
                self.assert_status(obj, NOW, "published")

    def test_publish_boundary_is_inclusive(self):
        for obj in self.objects(is_published=True, publish_at=NOW):
            for clock, expected in ((NOW - TICK, "scheduled"), (NOW, "published"),
                                    (NOW + TICK, "published")):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    self.assert_status(obj, clock, expected)

    def test_hide_boundary_is_exclusive(self):
        for obj in self.objects(is_published=True, hide_at=END):
            for clock, expected in ((END - TICK, "published"), (END, "hidden"),
                                    (END + TICK, "hidden")):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    self.assert_status(obj, clock, expected)

    def test_draft_never_releases_even_after_publish_time(self):
        for obj in self.objects(is_published=False, publish_at=NOW, hide_at=END):
            for clock in (NOW - TICK, NOW, END, END + TICK):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    self.assert_status(obj, clock, "draft")

    def test_window_with_equal_endpoints_never_releases(self):
        for obj in self.objects(is_published=True, publish_at=NOW, hide_at=NOW):
            for clock, expected in ((NOW - TICK, "scheduled"), (NOW, "hidden")):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    self.assert_status(obj, clock, expected)

    def test_nairobi_and_utc_represent_the_same_boundary(self):
        local_start = NOW.astimezone(ZoneInfo("Africa/Nairobi"))
        for obj in self.objects(is_published=True, publish_at=local_start):
            with self.subTest(model=type(obj).__name__):
                self.assert_status(obj, NOW - TICK, "scheduled")
                self.assert_status(obj, NOW, "published")

    def test_default_clock_can_be_patched_without_a_worker(self):
        for obj in self.objects(is_published=True, publish_at=NOW, hide_at=END):
            for clock, expected in ((NOW - TICK, "scheduled"), (NOW, "published"),
                                    (END, "hidden")):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    with patch("apps.learning.release.timezone.now", return_value=clock):
                        self.assertEqual(release_status(obj), expected)
                        self.assertEqual(is_released_at(obj), expected == "published")


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class OrganiseReleaseBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        enable_quizzes()
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.sc = make_class(cls.year, name="Organise class", level_order=40)
        cls.other_class = make_class(cls.year, name="Other organise class", level_order=41)
        cls.subj = make_subject("ORG", "Organise subject")
        cls.other_subject = make_subject("ORG2", "Other organise subject")
        for sc, subj in ((cls.sc, cls.subj), (cls.other_class, cls.subj),
                         (cls.sc, cls.other_subject)):
            ClassSubject.objects.create(school_class=sc, subject=subj)
        cls.teacher_user, cls.teacher = make_teacher("organise_teacher", "ORG-T1")
        TeacherAssignment.objects.create(
            teacher=cls.teacher, school_class=cls.sc, subject=cls.subj,
            academic_year=cls.year,
        )
        cls.other_teacher_user, _ = make_teacher("organise_other_teacher", "ORG-T2")
        cls.student = make_student_user(cls.sc, admission_number="ORG-A")
        cls.peer = make_student_user(cls.sc, admission_number="ORG-B")
        cls.third = make_student_user(cls.sc, admission_number="ORG-C")
        cls.foreign_student = make_student_user(cls.other_class, admission_number="ORG-X")
        cls.parent_user, _ = make_parent_for(cls.student)
        cls.module = CourseModule.objects.create(
            school_class=cls.sc, subject=cls.subj, term=cls.term,
            title="Organise module", position=0, is_published=True,
        )
        cls.lesson = Lesson.objects.create(
            module=cls.module, title="Organise lesson", notes="Unique release notes",
            position=0, is_published=True,
        )
        cls.foreign_module = CourseModule.objects.create(
            school_class=cls.other_class, subject=cls.subj, term=cls.term,
            title="Foreign module", position=0, is_published=True,
        )
        cls.foreign_lesson = Lesson.objects.create(
            module=cls.foreign_module, title="Foreign lesson", position=0,
            is_published=True,
        )
        cls.quiz, cls.questions, cls.choices = make_full_quiz(cls.lesson, cls.teacher_user)

    def classroom_url(self, *, teacher=False):
        return reverse("learning:teacher_classroom" if teacher else "learning:classroom",
                       args=[self.sc.pk, self.subj.pk])

    def lesson_url(self, lesson=None, *, preview=False):
        lesson = lesson or self.lesson
        args = [self.sc.pk, self.subj.pk]
        if preview:
            args.append(lesson.module_id)
        args.append(lesson.pk)
        return reverse("learning:lesson_preview" if preview else "learning:lesson_view",
                       args=args)

    def quiz_url(self, name):
        return reverse(f"learning:{name}", args=[self.sc.pk, self.subj.pk, self.lesson.pk])

    def course(self, student=None):
        return course_selectors.course_for_student(
            school_class=self.sc, subject=self.subj, term=self.term,
            student=student or self.student,
        )

    def lesson_ids(self, student=None):
        return [lp.lesson.pk for mwl in self.course(student) for lp in mwl.lessons]

    def topic(self, title="Revision", **fields):
        defaults = dict(school_class=self.sc, subject=self.subj, term=self.term,
                        title=title, position=0)
        defaults.update(fields)
        return Topic.objects.create(**defaults)

    def attachment(self):
        return LessonAttachment.objects.create(
            lesson=self.lesson,
            file=SimpleUploadedFile("release.pdf", b"%PDF-1.4 released", "application/pdf"),
            original_name="release.pdf", content_type="application/pdf",
        )

    def attachment_url(self, attachment):
        return reverse("learning:lesson_attachment_file",
                       args=[self.lesson.pk, attachment.pk])

    def set_lesson_targets(self, students):
        topic_services.set_lesson_targets(
            lesson=self.lesson, assign_all=False,
            student_ids=[s.pk for s in students], actor=self.teacher_user,
        )

    def set_quiz_targets(self, students):
        topic_services.set_quiz_targets(
            quiz=self.quiz, assign_all=False,
            student_ids=[s.pk for s in students], actor=self.teacher_user,
        )

    def enable_comments(self):
        settings, _ = ClassEngagementSettings.objects.get_or_create(school_class=self.sc)
        settings.lesson_comments_enabled = True
        settings.save(update_fields=["lesson_comments_enabled", "updated_at"])
        return settings

    def comment_url(self, name="lesson_comment_create", comment=None, lesson=None,
                    sc=None, subj=None):
        args = [(sc or self.sc).pk, (subj or self.subj).pk,
                (lesson or self.lesson).pk]
        if comment is not None:
            args.append(comment.pk)
        return reverse(f"learning:{name}", args=args)

    def assert_no_student_writes(self):
        self.assertFalse(LessonProgress.objects.filter(student=self.student).exists())
        self.assertFalse(QuizAttempt.objects.filter(student=self.student).exists())


class TestTargetedEngagementSummaries(OrganiseReleaseBase):
    def setUp(self):
        self.engagement = ClassEngagementSettings.objects.create(
            school_class=self.sc, is_active=True,
        )

    def director_row(self):
        return next(row for row in director_engagement_overview(academic_year=self.year)
                    if row.school_class.pk == self.sc.pk)

    def test_director_averages_individual_assignment_percentages(self):
        targeted = Lesson.objects.create(
            module=self.module, title="Peer lesson", position=1,
            is_published=True, assign_all=False,
        )
        targeted.assigned_students.add(self.peer)
        for student in (self.student, self.peer, self.foreign_student):
            LessonProgress.objects.create(student=student, lesson=self.lesson, completed=True)
        LessonProgress.objects.create(student=self.third, lesson=self.lesson, completed=False)
        LessonProgress.objects.create(student=self.third, lesson=targeted, completed=True)

        row = self.director_row()
        self.assertEqual((row.student_count, row.total_lessons), (3, 2))
        # Student 100%, peer 50%, third 0%; stale/foreign completions do not count.
        self.assertEqual((row.active_student_count, row.avg_completion_pct), (2, 50))

    def test_director_excludes_zero_assignment_pupils_and_stale_completions(self):
        self.set_lesson_targets([self.peer])
        for student in (self.student, self.peer):
            LessonProgress.objects.create(student=student, lesson=self.lesson, completed=True)

        row = self.director_row()
        self.assertEqual((row.student_count, row.total_lessons), (3, 1))
        self.assertEqual((row.active_student_count, row.avg_completion_pct), (1, 100))

    def test_director_counts_released_lessons_even_when_assigned_to_nobody(self):
        self.set_lesson_targets([])
        LessonProgress.objects.create(student=self.student, lesson=self.lesson, completed=True)

        row = self.director_row()
        self.assertEqual((row.student_count, row.total_lessons), (3, 1))
        self.assertEqual((row.active_student_count, row.avg_completion_pct), (0, 0))

    def test_director_excludes_inactive_pupils_from_completions_and_average(self):
        self.set_lesson_targets([self.student, self.third])
        self.third.status = self.third.Status.WITHDRAWN
        self.third.save(update_fields=["status"])
        for student in (self.peer, self.third):
            LessonProgress.objects.create(student=student, lesson=self.lesson, completed=True)

        row = self.director_row()
        self.assertEqual((row.student_count, row.total_lessons), (2, 1))
        self.assertEqual((row.active_student_count, row.avg_completion_pct), (0, 0))

    def test_director_rechecks_module_and_lesson_release_windows(self):
        self.set_lesson_targets([self.student])
        LessonProgress.objects.create(student=self.student, lesson=self.lesson, completed=True)
        for obj in (self.module, self.lesson):
            obj.publish_at, obj.hide_at = NOW, END
            obj.save(update_fields=["publish_at", "hide_at"])
            for clock, released in ((NOW - TICK, False), (NOW, True),
                                    (END - TICK, True), (END, False)):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    with patch("apps.learning.release.timezone.now", return_value=clock):
                        row = self.director_row()
                    self.assertEqual(row.total_lessons, int(released))
                    self.assertEqual(row.active_student_count, int(released))
                    self.assertEqual(row.avg_completion_pct, 100 if released else 0)
            obj.publish_at = obj.hide_at = None
            obj.save(update_fields=["publish_at", "hide_at"])
        self.assertTrue(LessonProgress.objects.filter(
            student=self.student, lesson=self.lesson, completed=True,
        ).exists())

    def test_director_excludes_draft_modules_and_lessons(self):
        LessonProgress.objects.create(student=self.student, lesson=self.lesson, completed=True)
        for obj in (self.module, self.lesson):
            with self.subTest(model=type(obj).__name__):
                obj.is_published = False
                obj.save(update_fields=["is_published"])
                row = self.director_row()
                self.assertEqual((row.total_lessons, row.active_student_count,
                                  row.avg_completion_pct), (0, 0, 0))
                obj.is_published = True
                obj.save(update_fields=["is_published"])

    def test_director_limits_lessons_and_completions_to_current_term_and_class(self):
        other_term = make_term(self.year, name="term_2", is_current=False)
        old_module = CourseModule.objects.create(
            school_class=self.sc, subject=self.subj, term=other_term,
            title="Other term module", position=0, is_published=True,
        )
        old_lesson = Lesson.objects.create(
            module=old_module, title="Other term lesson", position=0, is_published=True,
        )
        LessonProgress.objects.create(student=self.student, lesson=old_lesson, completed=True)
        LessonProgress.objects.create(student=self.student, lesson=self.foreign_lesson,
                                      completed=True)
        row = self.director_row()
        self.assertEqual((row.total_lessons, row.active_student_count,
                          row.avg_completion_pct), (1, 0, 0))

    def test_director_keeps_engagement_and_current_term_gating(self):
        LessonProgress.objects.create(student=self.student, lesson=self.lesson, completed=True)
        for field in ("engagement", "term"):
            with self.subTest(gate=field):
                if field == "engagement":
                    self.engagement.is_active = False
                    self.engagement.save(update_fields=["is_active"])
                else:
                    self.term.is_current = False
                    self.term.save(update_fields=["is_current"])
                row = self.director_row()
                self.assertEqual(row.student_count, 3)
                self.assertEqual(row.engagement_on, field != "engagement")
                self.assertEqual((row.total_lessons, row.active_student_count,
                                  row.avg_completion_pct), (0, 0, 0))
                self.engagement.is_active = True
                self.engagement.save(update_fields=["is_active"])

    def test_teacher_average_excludes_zero_assignment_rows_but_includes_not_started(self):
        self.set_lesson_targets([self.student, self.peer])
        for student in (self.student, self.third):
            LessonProgress.objects.create(student=student, lesson=self.lesson, completed=True)
        overview = class_progress_overview(
            school_class=self.sc, subject_id=self.subj.pk, term=self.term,
        )
        rows = {row.student.pk: row for row in overview.rows}
        self.assertEqual((rows[self.third.pk].total, rows[self.third.pk].completed), (0, 0))
        self.assertTrue(rows[self.peer.pk].not_started)
        self.assertEqual(overview.avg_pct, 50)

        self.set_lesson_targets([])
        overview = class_progress_overview(
            school_class=self.sc, subject_id=self.subj.pk, term=self.term,
        )
        self.assertTrue(all(row.total == 0 for row in overview.rows))
        self.assertEqual(overview.avg_pct, 0)

    def test_director_query_count_does_not_grow_with_pupils_or_lessons(self):
        self.set_lesson_targets([self.student, self.peer])
        LessonProgress.objects.create(student=self.student, lesson=self.lesson, completed=True)
        with CaptureQueriesContext(connection) as before:
            self.assertEqual(self.director_row().avg_completion_pct, 50)

        for index in range(5):
            make_student_user(self.sc, admission_number=f"ORG-EXTRA-{index}")
        for position in range(1, 5):
            lesson = Lesson.objects.create(
                module=self.module, title=f"Extra targeted lesson {position}",
                position=position, is_published=True, assign_all=False,
            )
            lesson.assigned_students.add(self.student)
        with CaptureQueriesContext(connection) as after:
            row = self.director_row()
            self.assertEqual((row.student_count, row.total_lessons), (8, 5))
            self.assertEqual((row.active_student_count, row.avg_completion_pct), (1, 10))
        self.assertEqual(len(after), len(before))


class TestAlternateReadsAndForms(OrganiseReleaseBase):
    def setUp(self):
        settings = SchoolSettings.load()
        settings.live_classes_enabled = True
        settings.save(update_fields=["live_classes_enabled", "updated_at"])

    def test_calendar_hides_unassigned_quiz_from_student_and_parent(self):
        self.set_quiz_targets([self.peer])
        url = reverse("learning:class_calendar", args=[self.sc.pk])
        for user in (self.student.user, self.parent_user):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, f"Quiz: {self.quiz.title}")
        self.client.force_login(self.peer.user)
        self.assertContains(self.client.get(url), f"Quiz: {self.quiz.title}")

    def test_calendar_checks_quiz_lesson_and_module_release(self):
        self.client.force_login(self.student.user)
        url = reverse("learning:class_calendar", args=[self.sc.pk])
        for obj in (self.module, self.lesson, self.quiz):
            obj.publish_at = NOW
            obj.save(update_fields=["publish_at"])
            with patch("apps.learning.release.timezone.now", return_value=NOW - TICK):
                self.assertNotContains(self.client.get(url), f"Quiz: {self.quiz.title}")
            obj.publish_at = None
            obj.save(update_fields=["publish_at"])

    def test_feed_resource_cards_and_picker_hide_unassigned_items(self):
        from apps.learning import feed_selectors, views
        from types import SimpleNamespace

        self.set_lesson_targets([self.peer])
        post = SimpleNamespace(lesson=self.lesson, quiz=self.quiz, school_class_id=self.sc.pk)
        self.assertIsNone(feed_selectors._lesson_link(post, self.student.user, self.student))
        self.assertIsNone(feed_selectors._quiz_link(post, self.parent_user, self.student))
        self.assertIsNotNone(feed_selectors._lesson_link(post, self.peer.user, self.peer))
        self.assertIsNotNone(feed_selectors._quiz_link(post, self.peer.user, self.peer))
        self.assertEqual(views._feed_link_lessons(self.sc, self.student), [])
        self.assertEqual(views._feed_link_quizzes(self.sc, self.student), [])

    def test_feed_cards_recheck_each_release_level(self):
        from apps.learning import feed_selectors
        from types import SimpleNamespace

        post = SimpleNamespace(lesson=self.lesson, quiz=self.quiz, school_class_id=self.sc.pk)
        for obj in (self.module, self.lesson, self.quiz):
            obj.hide_at = NOW
            obj.save(update_fields=["hide_at"])
            with patch("apps.learning.release.timezone.now", return_value=NOW):
                self.assertIsNone(feed_selectors._quiz_link(post, self.student.user, self.student))
            obj.hide_at = None
            obj.save(update_fields=["hide_at"])

    def test_lesson_form_saves_nairobi_schedule_topic_and_targets(self):
        self.client.force_login(self.teacher_user)
        topic = self.topic()
        url = reverse("learning:lesson_edit", args=[self.sc.pk, self.subj.pk, self.module.pk, self.lesson.pk])
        response = self.client.post(url, {
            "title": self.lesson.title, "notes": "Form notes", "is_published": "1",
            "publish_at": "2026-11-20T09:00", "hide_at": "2026-11-21T09:00",
            "assign_all": "0", "assigned_students": [self.peer.pk], "topic_id": topic.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.publish_at.hour, 6)
        self.assertEqual(self.lesson.hide_at.hour, 6)
        self.assertEqual(self.lesson.topic_id, topic.pk)
        self.assertFalse(self.lesson.assign_all)
        self.assertEqual(list(self.lesson.assigned_students.all()), [self.peer])

    def test_older_lesson_posts_preserve_targeting_schedule_and_topic(self):
        self.client.force_login(self.teacher_user)
        self.set_lesson_targets([self.peer])
        self.lesson.topic = self.topic()
        self.lesson.publish_at = NOW
        self.lesson.save(update_fields=["topic", "publish_at"])
        topic_id = self.lesson.topic_id
        url = reverse("learning:lesson_edit", args=[self.sc.pk, self.subj.pk, self.module.pk, self.lesson.pk])
        self.client.post(url, {"title": self.lesson.title, "notes": "Revised", "is_published": "1"})
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.topic_id, topic_id)
        self.assertEqual(self.lesson.publish_at, NOW)
        self.assertFalse(self.lesson.assign_all)
        self.assertEqual(list(self.lesson.assigned_students.all()), [self.peer])

    def test_direct_comment_calls_cannot_bypass_membership_or_actor(self):
        from django.core.exceptions import PermissionDenied
        from types import SimpleNamespace

        self.enable_comments()
        for student, request in (
            (self.foreign_student, None),
            (self.student, SimpleNamespace(user=self.parent_user)),
        ):
            with self.assertRaises(PermissionDenied):
                comment_services.add_comment(lesson=self.lesson, student=student, body="Denied", request=request)
        self.assertFalse(LessonComment.objects.exists())

    def test_direct_topic_and_moderation_calls_require_teacher_scope(self):
        from django.core.exceptions import PermissionDenied

        self.enable_comments()
        comment = comment_services.add_comment(lesson=self.lesson, student=self.student, body="Question")
        for actor in (self.student.user, self.other_teacher_user, self.parent_user):
            with self.assertRaises(PermissionDenied):
                topic_services.create_topic(school_class=self.sc, subject=self.subj, term=self.term, title="Denied", actor=actor)
            with self.assertRaises(PermissionDenied):
                comment_services.remove_comment(comment=comment, actor=actor)
        comment.refresh_from_db()
        self.assertFalse(comment.is_removed)


class TestReadTimeSchedules(OrganiseReleaseBase):
    def assert_lesson_reachability(self, released, attachment):
        self.assertEqual(self.lesson.pk in self.lesson_ids(), released)
        detail = course_selectors.lesson_detail_for_student(
            lesson_id=self.lesson.pk, student=self.student,
        )
        self.assertEqual(detail is not None, released)
        response = self.client.get(self.classroom_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.lesson.title in response.content.decode(), released)
        response = self.client.get(self.lesson_url())
        self.assertEqual(response.status_code, 200 if released else 404)
        response = self.client.get(reverse("learning:course_search",
                                          args=[self.sc.pk, self.subj.pk]),
                                   {"q": "Unique release notes"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([lp.lesson.pk for lp in response.context["results"]],
                         [self.lesson.pk] if released else [])
        response = self.client.get(self.attachment_url(attachment))
        self.assertEqual(response.status_code, 200 if released else 404)
        if released:
            self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 released")
        response.close()

    def check_lesson_schedule_without_worker(self, obj):
        obj.publish_at, obj.hide_at = NOW, END
        obj.save(update_fields=["publish_at", "hide_at"])
        attachment = self.attachment()
        self.client.force_login(self.student.user)
        # Only the clock changes. No task invocation or publish flag write occurs.
        for clock, released in ((NOW - TICK, False), (NOW, True),
                                (END - TICK, True), (END, False)):
            with self.subTest(clock=clock):
                with patch("apps.learning.release.timezone.now", return_value=clock):
                    self.assert_lesson_reachability(released, attachment)
                    self.assertEqual(student_progress(student=self.student, term=self.term)
                                     .total_lessons, int(released))
                    self.assertEqual(self.client.get(self.quiz_url("quiz_landing")).status_code,
                                     200 if released else 404)
        obj.refresh_from_db()
        self.assertTrue(obj.is_published)
        self.assertEqual((obj.publish_at, obj.hide_at), (NOW, END))
        self.assert_no_student_writes()

    def test_module_schedule_is_enforced_on_reads_with_worker_down(self):
        self.check_lesson_schedule_without_worker(self.module)

    def test_lesson_schedule_is_enforced_on_reads_with_worker_down(self):
        self.check_lesson_schedule_without_worker(self.lesson)

    def test_quiz_schedule_is_enforced_on_reads_with_worker_down(self):
        self.quiz.publish_at, self.quiz.hide_at = NOW, END
        self.quiz.save(update_fields=["publish_at", "hide_at"])
        self.client.force_login(self.student.user)
        for clock, released in ((NOW - TICK, False), (NOW, True),
                                (END - TICK, True), (END, False)):
            with self.subTest(clock=clock):
                with patch("apps.learning.release.timezone.now", return_value=clock):
                    lp = self.course()[0].lessons[0]
                    self.assertEqual(lp.quiz is not None, released)
                    response = self.client.get(self.classroom_url())
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(self.quiz_url("quiz_landing") in response.content.decode(),
                                     released)
                    self.assertEqual(self.client.get(self.quiz_url("quiz_landing")).status_code,
                                     200 if released else 404)
                    self.assertEqual(self.client.get(self.lesson_url()).status_code, 200)
        self.quiz.refresh_from_db()
        self.assertTrue(self.quiz.is_published)
        self.assertEqual((self.quiz.publish_at, self.quiz.hide_at), (NOW, END))
        self.assert_no_student_writes()

    def test_quiz_take_save_and_submit_recheck_each_schedule_level(self):
        self.client.force_login(self.student.user)
        attempt = QuizAttempt.objects.create(quiz=self.quiz, student=self.student)
        for obj in (self.module, self.lesson, self.quiz):
            obj.publish_at, obj.hide_at = NOW, END
            obj.save(update_fields=["publish_at", "hide_at"])
            for clock in (NOW - TICK, END):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    with patch("apps.learning.release.timezone.now", return_value=clock):
                        self.assertEqual(self.client.get(self.quiz_url("quiz_take")).status_code, 404)
                        response = self.client.post(self.quiz_url("quiz_save_answer"), {
                            "question_id": self.questions[0].pk, "choice_id": self.choices[0].pk,
                        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                        self.assertEqual(response.status_code, 404)
                        response = self.client.post(self.quiz_url("quiz_take"), {
                            f"answer_{self.questions[0].pk}": self.choices[0].pk,
                        })
                        self.assertEqual(response.status_code, 404)
                        self.assertFalse(QuizAttemptAnswer.objects.filter(attempt=attempt).exists())
                        attempt.refresh_from_db()
                        self.assertIsNone(attempt.submitted_at)
            obj.publish_at = obj.hide_at = None
            obj.save(update_fields=["publish_at", "hide_at"])
        self.assertEqual(QuizAttempt.objects.filter(student=self.student).count(), 1)

    def test_quiz_take_and_save_work_at_exact_release_time_without_worker(self):
        self.quiz.publish_at, self.quiz.hide_at = NOW, END
        self.quiz.save(update_fields=["publish_at", "hide_at"])
        self.client.force_login(self.student.user)
        with patch("apps.learning.release.timezone.now", return_value=NOW):
            self.assertEqual(self.client.get(self.quiz_url("quiz_take")).status_code, 200)
            response = self.client.post(self.quiz_url("quiz_save_answer"), {
                "question_id": self.questions[0].pk, "choice_id": self.choices[0].pk,
            }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            self.assertTrue(QuizAttemptAnswer.objects.filter(
                attempt__student=self.student, question=self.questions[0],
            ).exists())

    def test_complete_rechecks_module_and_lesson_windows(self):
        self.client.force_login(self.student.user)
        url = reverse("learning:mark_lesson_complete", args=[self.lesson.pk])
        for obj in (self.module, self.lesson):
            obj.publish_at, obj.hide_at = NOW, END
            obj.save(update_fields=["publish_at", "hide_at"])
            for clock in (NOW - TICK, END):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    with patch("apps.learning.release.timezone.now", return_value=clock):
                        self.assertEqual(self.client.post(url).status_code, 404)
                        self.assertFalse(LessonProgress.objects.filter(
                            student=self.student, lesson=self.lesson,
                        ).exists())
            obj.publish_at = obj.hide_at = None
            obj.save(update_fields=["publish_at", "hide_at"])

    def test_historical_completion_is_excluded_outside_release_windows(self):
        LessonProgress.objects.create(student=self.student, lesson=self.lesson, completed=True)
        for obj in (self.module, self.lesson):
            obj.publish_at, obj.hide_at = NOW, END
            obj.save(update_fields=["publish_at", "hide_at"])
            for clock, released in ((NOW - TICK, False), (NOW, True), (END, False)):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    with patch("apps.learning.release.timezone.now", return_value=clock):
                        progress = student_progress(student=self.student, term=self.term)
                        self.assertEqual((progress.total_lessons, progress.completed_lessons),
                                         (int(released), int(released)))
                        overview = class_progress_overview(
                            school_class=self.sc, subject_id=self.subj.pk, term=self.term,
                        )
                        row = next(r for r in overview.rows if r.student.pk == self.student.pk)
                        self.assertEqual((row.total, row.completed), (int(released), int(released)))
            obj.publish_at = obj.hide_at = None
            obj.save(update_fields=["publish_at", "hide_at"])
        self.assertTrue(LessonProgress.objects.filter(
            student=self.student, lesson=self.lesson, completed=True,
        ).exists())


class TestStudentTargeting(OrganiseReleaseBase):
    def setUp(self):
        self.set_lesson_targets([self.peer])
        self.client.force_login(self.student.user)

    def test_unassigned_lesson_is_absent_from_course_and_classroom(self):
        self.assertNotIn(self.lesson.pk, self.lesson_ids())
        response = self.client.get(self.classroom_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.lesson.title)
        self.assertEqual(response.context["total_lessons"], 0)

    def test_unassigned_lesson_detail_is_unreachable(self):
        self.assertIsNone(course_selectors.lesson_detail_for_student(
            lesson_id=self.lesson.pk, student=self.student,
        ))
        self.assertEqual(self.client.get(self.lesson_url()).status_code, 404)
        self.assert_no_student_writes()

    def test_unassigned_lesson_attachment_is_unreachable(self):
        response = self.client.get(self.attachment_url(self.attachment()))
        self.assertEqual(response.status_code, 404)

    def test_unassigned_lesson_cannot_leak_through_title_or_notes_search(self):
        for query in (self.lesson.title, "Unique release notes"):
            with self.subTest(query=query):
                results = course_selectors.search_course(
                    school_class=self.sc, subject=self.subj, term=self.term,
                    student=self.student, query=query,
                )
                self.assertEqual(results, [])
                response = self.client.get(reverse("learning:course_search",
                                                  args=[self.sc.pk, self.subj.pk]),
                                           {"q": query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["results"], [])

    def test_unassigned_lesson_cannot_be_completed_by_post(self):
        url = reverse("learning:mark_lesson_complete", args=[self.lesson.pk])
        for headers in ({}, {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.post(url, **headers).status_code, 404)
        self.assert_no_student_writes()

    def test_unassigned_lesson_cannot_offer_quiz_landing_or_take(self):
        for name in ("quiz_landing", "quiz_take"):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(self.quiz_url(name)).status_code, 404)
        self.assert_no_student_writes()

    def test_unassigned_lesson_cannot_save_or_submit_an_existing_attempt(self):
        attempt = QuizAttempt.objects.create(quiz=self.quiz, student=self.student)
        for name, payload in (
            ("quiz_save_answer", {"question_id": self.questions[0].pk,
                                  "choice_id": self.choices[0].pk}),
            ("quiz_take", {f"answer_{self.questions[0].pk}": self.choices[0].pk}),
        ):
            with self.subTest(route=name):
                self.assertEqual(self.client.post(self.quiz_url(name), payload).status_code, 404)
                attempt.refresh_from_db()
                self.assertIsNone(attempt.submitted_at)
                self.assertFalse(attempt.answers.exists())

    def test_unassigned_historical_completion_is_excluded_from_student_progress(self):
        LessonProgress.objects.create(student=self.student, lesson=self.lesson, completed=True)
        progress = student_progress(student=self.student, term=self.term)
        self.assertEqual((progress.total_lessons, progress.completed_lessons), (0, 0))
        self.assertIsNone(progress.next_lesson)
        response = self.client.get(reverse("learning:student_progress"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["progress"].total_lessons, 0)
        self.assertEqual(response.context["progress"].completed_lessons, 0)

    def test_teacher_progress_does_not_count_unassigned_lessons_as_missing(self):
        LessonProgress.objects.create(student=self.student, lesson=self.lesson, completed=True)
        overview = class_progress_overview(
            school_class=self.sc, subject_id=self.subj.pk, term=self.term,
        )
        rows = {row.student.pk: row for row in overview.rows}
        self.assertEqual((rows[self.student.pk].total, rows[self.student.pk].completed), (0, 0))
        self.assertFalse(rows[self.student.pk].not_started)
        self.assertFalse(rows[self.student.pk].is_behind)
        self.assertEqual(rows[self.peer.pk].total, 1)
        self.assertTrue(rows[self.peer.pk].not_started)
        self.assertEqual(overview.not_started_count, 1)
        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse("learning:teacher_class_progress",
                                           args=[self.sc.pk, self.subj.pk]))
        self.assertEqual(response.status_code, 200)
        row = next(r for r in response.context["overview"].rows if r.student.pk == self.student.pk)
        self.assertEqual(row.total, 0)
        self.assertFalse(row.not_started)

    def test_teacher_not_attempted_excludes_students_not_assigned_the_lesson(self):
        results = teacher_quiz_results(quiz=self.quiz, school_class=self.sc)
        self.assertEqual({s.pk for s in results.not_attempted}, {self.peer.pk})
        self.assertEqual(results.not_attempted_count, 1)

    def test_assigned_student_can_list_open_download_and_complete(self):
        self.client.force_login(self.peer.user)
        self.assertIn(self.lesson.pk, self.lesson_ids(self.peer))
        self.assertEqual(self.client.get(self.lesson_url()).status_code, 200)
        response = self.client.get(self.attachment_url(self.attachment()))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 released")
        response.close()
        self.assertEqual(self.client.get(self.quiz_url("quiz_landing")).status_code, 200)
        response = self.client.post(reverse("learning:mark_lesson_complete", args=[self.lesson.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(LessonProgress.objects.filter(
            student=self.peer, lesson=self.lesson, completed=True,
        ).exists())

    def test_parent_obeys_active_child_assignment(self):
        self.client.force_login(self.parent_user)
        self.assertEqual(self.client.get(self.lesson_url()).status_code, 404)
        self.assertEqual(self.client.get(self.quiz_url("quiz_landing")).status_code, 404)
        response = self.client.get(self.classroom_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.lesson.title)


class TestQuizTargeting(OrganiseReleaseBase):
    def setUp(self):
        self.set_quiz_targets([self.peer])
        self.client.force_login(self.student.user)

    def test_unassigned_quiz_is_not_linked_but_its_lesson_remains_visible(self):
        lp = self.course()[0].lessons[0]
        self.assertEqual(lp.lesson.pk, self.lesson.pk)
        self.assertIsNone(lp.quiz)
        response = self.client.get(self.classroom_url())
        self.assertContains(response, self.lesson.title)
        self.assertNotContains(response, self.quiz_url("quiz_landing"))
        self.assertEqual(self.client.get(self.lesson_url()).status_code, 200)

    def test_unassigned_quiz_landing_and_take_are_unreachable(self):
        for name in ("quiz_landing", "quiz_take"):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(self.quiz_url(name)).status_code, 404)
        self.assert_no_student_writes()

    def test_unassigned_quiz_cannot_save_or_submit_existing_attempt(self):
        attempt = QuizAttempt.objects.create(quiz=self.quiz, student=self.student)
        self.assertEqual(self.client.post(self.quiz_url("quiz_save_answer"), {
            "question_id": self.questions[0].pk, "choice_id": self.choices[0].pk,
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code, 404)
        self.assertEqual(self.client.post(self.quiz_url("quiz_take"), {
            f"answer_{self.questions[0].pk}": self.choices[0].pk,
        }).status_code, 404)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.submitted_at)
        self.assertFalse(attempt.answers.exists())

    def test_teacher_not_attempted_requires_both_lesson_and_quiz_assignment(self):
        self.set_lesson_targets([self.student, self.peer])
        self.set_quiz_targets([self.peer, self.third])
        results = teacher_quiz_results(quiz=self.quiz, school_class=self.sc)
        self.assertEqual({s.pk for s in results.not_attempted}, {self.peer.pk})
        self.client.force_login(self.teacher_user)
        response = self.client.get(reverse("learning:teacher_quiz_results", args=[
            self.sc.pk, self.subj.pk, self.module.pk, self.lesson.pk,
        ]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({s.pk for s in response.context["results"].not_attempted}, {self.peer.pk})

    def test_assigned_quiz_student_can_take_and_save(self):
        self.client.force_login(self.peer.user)
        self.assertEqual(self.client.get(self.quiz_url("quiz_landing")).status_code, 200)
        self.assertEqual(self.client.get(self.quiz_url("quiz_take")).status_code, 200)
        response = self.client.post(self.quiz_url("quiz_save_answer"), {
            "question_id": self.questions[0].pk, "choice_id": self.choices[0].pk,
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertTrue(QuizAttemptAnswer.objects.filter(
            attempt__student=self.peer, question=self.questions[0],
        ).exists())


class TestTargetServices(OrganiseReleaseBase):
    def test_models_default_to_whole_class_assignment(self):
        self.assertTrue(self.lesson.assign_all)
        self.assertTrue(self.quiz.assign_all)
        self.assertFalse(self.lesson.assigned_students.exists())
        self.assertFalse(self.quiz.assigned_students.exists())
        self.assertIn(self.lesson.pk, self.lesson_ids(self.student))
        self.assertIn(self.lesson.pk, self.lesson_ids(self.peer))

    def test_targets_cannot_include_students_from_another_class(self):
        self.set_lesson_targets([self.student, self.foreign_student])
        self.set_quiz_targets([self.student, self.foreign_student])
        self.assertEqual(set(self.lesson.assigned_students.values_list("pk", flat=True)),
                         {self.student.pk})
        self.assertEqual(set(self.quiz.assigned_students.values_list("pk", flat=True)),
                         {self.student.pk})
        self.assertIsNone(course_selectors.lesson_detail_for_student(
            lesson_id=self.lesson.pk, student=self.foreign_student,
        ))

    def test_assign_all_clears_explicit_target_lists(self):
        self.set_lesson_targets([self.student])
        self.set_quiz_targets([self.student])
        topic_services.set_lesson_targets(
            lesson=self.lesson, assign_all=True, student_ids=[], actor=self.teacher_user,
        )
        topic_services.set_quiz_targets(
            quiz=self.quiz, assign_all=True, student_ids=[], actor=self.teacher_user,
        )
        self.lesson.refresh_from_db()
        self.quiz.refresh_from_db()
        self.assertTrue(self.lesson.assign_all)
        self.assertTrue(self.quiz.assign_all)
        self.assertFalse(self.lesson.assigned_students.exists())
        self.assertFalse(self.quiz.assigned_students.exists())
        self.assertIn(self.lesson.pk, self.lesson_ids(self.peer))
        self.assertIsNotNone(self.course(self.peer)[0].lessons[0].quiz)

    def test_empty_chosen_target_list_assigns_to_nobody(self):
        self.set_lesson_targets([])
        self.set_quiz_targets([])
        for student in (self.student, self.peer, self.third):
            with self.subTest(student=student.pk):
                self.assertNotIn(self.lesson.pk, self.lesson_ids(student))
        self.assertEqual(teacher_quiz_results(quiz=self.quiz, school_class=self.sc).not_attempted, [])


class TestTopicServicesAndSelectors(OrganiseReleaseBase):
    def test_create_and_rename_strip_whitespace_and_preserve_scope(self):
        topic = topic_services.create_topic(
            school_class=self.sc, subject=self.subj, term=self.term,
            title="  Week one  ", actor=self.teacher_user,
        )
        self.assertEqual(topic.title, "Week one")
        self.assertEqual((topic.school_class_id, topic.subject_id, topic.term_id),
                         (self.sc.pk, self.subj.pk, self.term.pk))
        topic_services.rename_topic(topic=topic, title="  Revision  ", actor=self.teacher_user)
        topic.refresh_from_db()
        self.assertEqual(topic.title, "Revision")

    def test_blank_topic_titles_are_rejected(self):
        topic = self.topic()
        with self.assertRaises(ValidationError):
            topic_services.create_topic(
                school_class=self.sc, subject=self.subj, term=self.term,
                title="  ", actor=self.teacher_user,
            )
        with self.assertRaises(ValidationError):
            topic_services.rename_topic(topic=topic, title="  ", actor=self.teacher_user)
        topic.refresh_from_db()
        self.assertEqual(topic.title, "Revision")

    def test_delete_topic_keeps_lesson_module_progress_and_quiz(self):
        topic = self.topic()
        topic_services.set_lesson_topic(lesson=self.lesson, topic=topic, actor=self.teacher_user)
        progress = LessonProgress.objects.create(student=self.student, lesson=self.lesson, completed=True)
        topic_id = topic.pk
        topic_services.delete_topic(topic=topic, actor=self.teacher_user)
        self.assertFalse(Topic.objects.filter(pk=topic_id).exists())
        self.lesson.refresh_from_db()
        self.assertIsNone(self.lesson.topic_id)
        self.assertEqual(self.lesson.topic_position, 0)
        self.assertEqual(self.lesson.module_id, self.module.pk)
        self.assertTrue(LessonProgress.objects.filter(pk=progress.pk).exists())
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())

    def test_move_topics_swaps_neighbours_without_changing_other_courses(self):
        first = self.topic("First", position=0)
        second = self.topic("Second", position=1)
        foreign = self.topic("Foreign topic", school_class=self.other_class, position=7)
        topic_services.move_topic(topic=second, direction="up", actor=self.teacher_user)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertLess(second.position, first.position)
        topic_services.move_topic(topic=second, direction="down", actor=self.teacher_user)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertLess(first.position, second.position)
        foreign.refresh_from_db()
        self.assertEqual(foreign.position, 7)

    def test_set_lesson_topic_rejects_other_class_subject_and_term(self):
        other_term = make_term(self.year, name="term_2", is_current=False)
        for fields in ({"school_class": self.other_class}, {"subject": self.other_subject},
                       {"term": other_term}):
            foreign = self.topic("Different course", **fields)
            with self.subTest(scope=fields):
                with self.assertRaises(ValidationError):
                    topic_services.set_lesson_topic(
                        lesson=self.lesson, topic=foreign, actor=self.teacher_user,
                    )
                self.lesson.refresh_from_db()
                self.assertIsNone(self.lesson.topic_id)

    def test_lessons_move_inside_topic_without_moving_modules(self):
        topic = self.topic()
        second_module = CourseModule.objects.create(
            school_class=self.sc, subject=self.subj, term=self.term, title="Second module",
            position=1,
        )
        second = Lesson.objects.create(module=second_module, title="Second lesson", position=8)
        for lesson in (self.lesson, second):
            topic_services.set_lesson_topic(lesson=lesson, topic=topic, actor=self.teacher_user)
        topic_services.move_lesson_in_topic(lesson=second, direction="up", actor=self.teacher_user)
        self.lesson.refresh_from_db()
        second.refresh_from_db()
        self.assertLess(second.topic_position, self.lesson.topic_position)
        self.assertEqual((self.lesson.module_id, second.module_id), (self.module.pk, second_module.pk))
        self.assertEqual((self.lesson.position, second.position), (0, 8))
        topic_services.move_lesson_in_topic(lesson=second, direction="down", actor=self.teacher_user)
        self.lesson.refresh_from_db()
        second.refresh_from_db()
        self.assertLess(self.lesson.topic_position, second.topic_position)

    def test_ungrouping_does_not_remove_lesson(self):
        topic_services.set_lesson_topic(lesson=self.lesson, topic=self.topic(), actor=self.teacher_user)
        topic_services.set_lesson_topic(lesson=self.lesson, topic=None, actor=self.teacher_user)
        self.lesson.refresh_from_db()
        self.assertIsNone(self.lesson.topic_id)
        self.assertEqual(self.lesson.topic_position, 0)
        self.assertIn(self.lesson.pk, self.lesson_ids())

    def test_topic_selector_uses_topic_order_and_filters_release_and_assignment(self):
        topic = self.topic()
        self.lesson.topic, self.lesson.topic_position = topic, 2
        self.lesson.save(update_fields=["topic", "topic_position"])
        first = Lesson.objects.create(module=self.module, title="Topic first", topic=topic,
                                      topic_position=0, position=9)
        scheduled = Lesson.objects.create(module=self.module, title="Topic scheduled", topic=topic,
                                          publish_at=END, topic_position=1, position=1)
        unassigned = Lesson.objects.create(module=self.module, title="Topic unassigned", topic=topic,
                                           assign_all=False, topic_position=3, position=2)
        unassigned.assigned_students.add(self.peer)
        ungrouped = Lesson.objects.create(module=self.module, title="Ungrouped", position=3)
        self.topic("Foreign topic", school_class=self.other_class)
        with patch("apps.learning.release.timezone.now", return_value=NOW):
            groups, untopic = course_selectors.topics_for_student(
                school_class=self.sc, subject=self.subj, term=self.term, student=self.student,
            )
        self.assertEqual([g.topic.pk for g in groups], [topic.pk])
        self.assertEqual([lp.lesson.pk for lp in groups[0].lessons], [first.pk, self.lesson.pk])
        self.assertEqual([lp.lesson.pk for lp in untopic], [ungrouped.pk])
        self.assertNotIn(scheduled.pk, [lp.lesson.pk for g in groups for lp in g.lessons])
        self.assert_no_student_writes()

    def test_legacy_no_topic_classroom_keeps_module_outline(self):
        self.assertFalse(Topic.objects.filter(school_class=self.sc).exists())
        self.client.force_login(self.student.user)
        response = self.client.get(self.classroom_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.module.title)
        self.assertContains(response, self.lesson.title)
        self.assertContains(response, self.lesson_url())
        self.assertEqual(response.context["total_lessons"], 1)
        self.assert_no_student_writes()


class TestTopicViews(OrganiseReleaseBase):
    def setUp(self):
        self.first = self.topic("Topic alpha", position=0)
        self.second = self.topic("Topic beta", position=1)
        self.lesson.topic, self.lesson.topic_position = self.first, 0
        self.lesson.save(update_fields=["topic", "topic_position"])
        self.second_lesson = Lesson.objects.create(
            module=self.module, title="Topic second lesson", position=1,
            topic=self.first, topic_position=1,
        )
        self.client.force_login(self.teacher_user)

    def topic_url(self, name, topic=None, direction=None):
        args = [self.sc.pk, self.subj.pk]
        if topic is not None:
            args.append(topic.pk)
        if direction is not None:
            args.append(direction)
        return reverse(f"learning:{name}", args=args)

    def move_lesson_url(self, lesson, direction):
        return reverse("learning:lesson_topic_move",
                       args=[self.sc.pk, self.subj.pk, lesson.pk, direction])

    def test_teacher_outline_contains_real_post_forms_for_no_js_ordering(self):
        class Forms(HTMLParser):
            def __init__(self):
                super().__init__()
                self.methods = {}

            def handle_starttag(self, tag, attrs):
                if tag == "form":
                    attrs = dict(attrs)
                    self.methods[attrs.get("action")] = attrs.get("method", "get").lower()

        response = self.client.get(self.classroom_url(teacher=True))
        self.assertEqual(response.status_code, 200)
        forms = Forms()
        forms.feed(response.content.decode())
        urls = [self.topic_url("topic_create"), self.topic_url("topic_edit", self.first),
                self.topic_url("topic_delete", self.first)]
        for direction in ("up", "down"):
            urls.append(self.topic_url("topic_move", self.second, direction))
            urls.append(self.move_lesson_url(self.second_lesson, direction))
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(forms.methods.get(url), "post")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_topic_create_edit_delete_work_with_plain_posts(self):
        response = self.client.post(self.topic_url("topic_create"), {"title": "  New topic  "})
        self.assertRedirects(response, self.classroom_url(teacher=True), fetch_redirect_response=False)
        topic = Topic.objects.get(school_class=self.sc, title="New topic")
        response = self.client.post(self.topic_url("topic_edit", topic), {"title": "Renamed topic"})
        self.assertEqual(response.status_code, 302)
        topic.refresh_from_db()
        self.assertEqual(topic.title, "Renamed topic")
        response = self.client.post(self.topic_url("topic_delete", topic))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Topic.objects.filter(pk=topic.pk).exists())

    def test_topic_up_and_down_are_no_js_posts(self):
        for direction, expected in (("up", [self.second.pk, self.first.pk]),
                                    ("down", [self.first.pk, self.second.pk])):
            with self.subTest(direction=direction):
                response = self.client.post(self.topic_url("topic_move", self.second, direction))
                self.assertRedirects(response, self.classroom_url(teacher=True),
                                     fetch_redirect_response=False)
                self.assertEqual(list(Topic.objects.filter(school_class=self.sc)
                                      .order_by("position", "pk").values_list("pk", flat=True)), expected)

    def test_lesson_topic_up_and_down_are_no_js_posts(self):
        for direction, expected in (("up", [self.second_lesson.pk, self.lesson.pk]),
                                    ("down", [self.lesson.pk, self.second_lesson.pk])):
            with self.subTest(direction=direction):
                response = self.client.post(self.move_lesson_url(self.second_lesson, direction))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(list(Lesson.objects.filter(topic=self.first)
                                      .order_by("topic_position", "pk").values_list("pk", flat=True)), expected)

    def test_get_never_mutates_topics_or_lesson_order(self):
        urls = [self.topic_url("topic_create"), self.topic_url("topic_edit", self.first),
                self.topic_url("topic_delete", self.first),
                self.topic_url("topic_move", self.second, "up"),
                self.move_lesson_url(self.second_lesson, "up")]
        for url in urls:
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (404, 405))
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.second_lesson.refresh_from_db()
        self.assertEqual((self.first.position, self.second.position), (0, 1))
        self.assertEqual(self.second_lesson.topic_position, 1)

    def test_student_and_parent_cannot_organise(self):
        urls = [self.topic_url("topic_create"), self.topic_url("topic_edit", self.first),
                self.topic_url("topic_delete", self.first),
                self.topic_url("topic_move", self.second, "up"),
                self.move_lesson_url(self.second_lesson, "up")]
        for user in (self.student.user, self.parent_user):
            self.client.force_login(user)
            for url in urls:
                with self.subTest(role=user.role, url=url):
                    self.assertEqual(self.client.post(url, {"title": "Tampered"}).status_code, 403)
        self.first.refresh_from_db()
        self.assertEqual(self.first.title, "Topic alpha")

    def test_unassigned_teacher_cannot_organise(self):
        self.client.force_login(self.other_teacher_user)
        for url in (self.topic_url("topic_create"), self.topic_url("topic_edit", self.first),
                    self.topic_url("topic_delete", self.first),
                    self.topic_url("topic_move", self.first, "down"),
                    self.move_lesson_url(self.lesson, "down")):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"title": "Tampered"}).status_code, 404)

    def test_foreign_topic_and_lesson_ids_are_rejected_under_owned_class_url(self):
        foreign = self.topic("Foreign topic", school_class=self.other_class)
        for url in (self.topic_url("topic_edit", foreign), self.topic_url("topic_delete", foreign),
                    self.topic_url("topic_move", foreign, "up"),
                    self.move_lesson_url(self.foreign_lesson, "up")):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"title": "Tampered"}).status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.title, "Foreign topic")
        self.assertTrue(Lesson.objects.filter(pk=self.foreign_lesson.pk).exists())

    def test_foreign_subject_topic_is_rejected(self):
        foreign = self.topic("Foreign subject topic", subject=self.other_subject)
        self.assertEqual(self.client.post(self.topic_url("topic_edit", foreign),
                                          {"title": "Tampered"}).status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.title, "Foreign subject topic")

    def test_invalid_move_direction_does_not_change_order(self):
        for url in (self.topic_url("topic_move", self.first, "sideways"),
                    self.move_lesson_url(self.lesson, "sideways")):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 404)
        self.first.refresh_from_db()
        self.lesson.refresh_from_db()
        self.assertEqual(self.first.position, 0)
        self.assertEqual(self.lesson.topic_position, 0)


class TestLessonComments(OrganiseReleaseBase):
    def test_comments_are_off_without_settings_and_default_off_with_settings(self):
        self.assertFalse(comment_services.comments_enabled(self.sc))
        settings = ClassEngagementSettings.objects.create(school_class=self.sc)
        self.assertFalse(settings.lesson_comments_enabled)
        self.client.force_login(self.student.user)
        self.assertEqual(self.client.post(self.comment_url(), {"body": "Blocked"}).status_code, 403)
        self.assertFalse(LessonComment.objects.exists())
        with self.assertRaises(PermissionDenied):
            comment_services.add_comment(lesson=self.lesson, student=self.student, body="Blocked")

    def test_off_switch_hides_existing_comments_and_post_form(self):
        comment = LessonComment.objects.create(lesson=self.lesson, student=self.student,
                                                body="Hidden comment marker")
        self.client.force_login(self.student.user)
        response = self.client.get(self.lesson_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, comment.body)
        self.assertNotContains(response, self.comment_url())

    def test_teacher_toggle_is_class_scoped_post_only_and_audited(self):
        url = reverse("learning:lesson_comments_toggle", args=[self.sc.pk, self.subj.pk])
        self.client.force_login(self.teacher_user)
        self.assertIn(self.client.get(url).status_code, (404, 405))
        self.assertFalse(comment_services.comments_enabled(self.sc))
        for enabled, action in (("1", "lesson.comments.enabled"),
                                ("0", "lesson.comments.disabled")):
            with self.subTest(enabled=enabled):
                response = self.client.post(url, {"enabled": enabled})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(comment_services.comments_enabled(self.sc), enabled == "1")
                self.assertFalse(comment_services.comments_enabled(self.other_class))
                self.assertTrue(AuditLog.objects.filter(actor=self.teacher_user, action=action).exists())

    def test_student_parent_and_unassigned_teacher_cannot_toggle_comments(self):
        url = reverse("learning:lesson_comments_toggle", args=[self.sc.pk, self.subj.pk])
        for user, status in ((self.student.user, 403), (self.parent_user, 403),
                             (self.other_teacher_user, 404)):
            with self.subTest(role=user.role, user=user.pk):
                self.client.force_login(user)
                self.assertEqual(self.client.post(url, {"enabled": "1"}).status_code, status)
        self.assertFalse(comment_services.comments_enabled(self.sc))

    def test_student_posts_plain_text_and_addition_is_audited(self):
        self.enable_comments()
        self.client.force_login(self.student.user)
        response = self.client.post(self.comment_url(), {
            "body": '  <b>Question marker</b><img src=x onerror="evil()">  ',
            "student_id": self.peer.pk,
        })
        self.assertRedirects(response, self.lesson_url(), fetch_redirect_response=False)
        comment = LessonComment.objects.get(lesson=self.lesson)
        self.assertEqual(comment.student_id, self.student.pk)
        self.assertEqual(comment.body, "Question marker")
        self.assertFalse(comment.is_removed)
        audit = AuditLog.objects.get(action="lesson.comment.added", object_id=str(comment.pk))
        self.assertEqual(audit.actor_id, self.student.user_id)
        self.assertEqual(audit.content_type, "learning.LessonComment")
        self.assertEqual(audit.change_summary["lesson_id"], self.lesson.pk)
        self.assertEqual(audit.change_summary["excerpt"], "Question marker")
        response = self.client.get(self.lesson_url())
        self.assertContains(response, "Question marker")
        self.assertNotContains(response, 'onerror="evil()"')
        self.assertContains(response, self.comment_url())

    def test_service_strips_html_and_rejects_empty_or_overlong_body(self):
        self.enable_comments()
        comment = comment_services.add_comment(
            lesson=self.lesson, student=self.student, body="  <i>Safe text</i>  ",
        )
        self.assertEqual(comment.body, "Safe text")
        for body in ("  ", "<b></b>", "x" * 2001):
            with self.subTest(body_length=len(body)):
                with self.assertRaises(ValidationError):
                    comment_services.add_comment(lesson=self.lesson, student=self.student, body=body)
        self.assertEqual(LessonComment.objects.count(), 1)
        boundary = comment_services.add_comment(
            lesson=self.lesson, student=self.student, body="x" * 2000,
        )
        self.assertEqual(len(boundary.body), 2000)

    def test_same_class_students_can_read_each_others_questions(self):
        self.enable_comments()
        comment = comment_services.add_comment(
            lesson=self.lesson, student=self.peer, body="Peer question marker",
        )
        self.client.force_login(self.student.user)
        self.assertContains(self.client.get(self.lesson_url()), comment.body)

    def test_parent_can_read_but_cannot_post_remove_or_restore(self):
        self.enable_comments()
        comment = comment_services.add_comment(
            lesson=self.lesson, student=self.student, body="Parent readable marker",
        )
        self.client.force_login(self.parent_user)
        response = self.client.get(self.lesson_url())
        self.assertContains(response, comment.body)
        self.assertNotContains(response, self.comment_url())
        for url in (self.comment_url(), self.comment_url("lesson_comment_remove", comment),
                    self.comment_url("lesson_comment_restore", comment)):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"body": "Parent cannot write"}).status_code, 403)
        comment.refresh_from_db()
        self.assertFalse(comment.is_removed)
        self.assertEqual(LessonComment.objects.count(), 1)

    def test_comments_are_isolated_from_other_classes_and_lessons(self):
        self.enable_comments()
        comment = comment_services.add_comment(
            lesson=self.lesson, student=self.student, body="Class private marker",
        )
        self.client.force_login(self.foreign_student.user)
        self.assertEqual(self.client.get(self.lesson_url()).status_code, 404)
        self.assertEqual(self.client.post(self.comment_url(), {"body": "Intrusion"}).status_code, 404)
        self.client.force_login(self.student.user)
        response = self.client.post(self.comment_url(lesson=self.foreign_lesson), {"body": "Intrusion"})
        self.assertEqual(response.status_code, 404)
        other_lesson = Lesson.objects.create(module=self.module, title="Other local lesson", position=1)
        self.assertNotContains(self.client.get(self.lesson_url(other_lesson)), comment.body)
        self.assertEqual(LessonComment.objects.count(), 1)

    def test_comment_post_checks_class_and_subject_url_not_just_lesson_id(self):
        self.enable_comments()
        self.client.force_login(self.student.user)
        for fields in ({"sc": self.other_class}, {"subj": self.other_subject}):
            with self.subTest(fields=fields):
                self.assertEqual(self.client.post(self.comment_url(**fields),
                                                  {"body": "Wrong URL"}).status_code, 404)
        self.assertFalse(LessonComment.objects.exists())

    def test_unassigned_scheduled_and_hidden_lessons_cannot_receive_comments(self):
        self.enable_comments()
        self.client.force_login(self.student.user)
        self.set_lesson_targets([self.peer])
        self.assertEqual(self.client.post(self.comment_url(), {"body": "Not assigned"}).status_code, 404)
        topic_services.set_lesson_targets(
            lesson=self.lesson, assign_all=True, student_ids=[], actor=self.teacher_user,
        )
        for obj in (self.module, self.lesson):
            obj.publish_at, obj.hide_at = NOW, END
            obj.save(update_fields=["publish_at", "hide_at"])
            for clock in (NOW - TICK, END):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    with patch("apps.learning.release.timezone.now", return_value=clock):
                        self.assertEqual(self.client.post(self.comment_url(),
                                                          {"body": "Unavailable"}).status_code, 404)
            obj.publish_at = obj.hide_at = None
            obj.save(update_fields=["publish_at", "hide_at"])
        self.assertFalse(LessonComment.objects.exists())

    def test_student_cannot_moderate_even_their_own_comment(self):
        self.enable_comments()
        comment = comment_services.add_comment(lesson=self.lesson, student=self.student, body="Own question")
        for user in (self.student.user, self.peer.user):
            self.client.force_login(user)
            for name in ("lesson_comment_remove", "lesson_comment_restore"):
                with self.subTest(student=user.pk, action=name):
                    self.assertEqual(self.client.post(self.comment_url(name, comment)).status_code, 403)
        comment.refresh_from_db()
        self.assertFalse(comment.is_removed)

    def test_teacher_soft_remove_restore_is_audited_and_idempotent(self):
        self.enable_comments()
        comment = comment_services.add_comment(
            lesson=self.lesson, student=self.student, body="Moderation question marker",
        )
        self.client.force_login(self.teacher_user)
        url = self.comment_url("lesson_comment_remove", comment)
        self.assertIn(self.client.get(url).status_code, (404, 405))
        with patch("apps.learning.comment_services.timezone.now", return_value=NOW):
            self.assertEqual(self.client.post(url).status_code, 302)
            self.assertEqual(self.client.post(url).status_code, 302)
        comment.refresh_from_db()
        self.assertTrue(comment.is_removed)
        self.assertEqual(comment.removed_at, NOW)
        self.assertEqual(comment.removed_by_id, self.teacher_user.pk)
        self.assertEqual(comment.body, "Moderation question marker")
        self.assertTrue(LessonComment.objects.filter(pk=comment.pk).exists())
        audit = AuditLog.objects.get(action="lesson.comment.removed", object_id=str(comment.pk))
        self.assertEqual(audit.actor_id, self.teacher_user.pk)
        self.assertEqual(audit.change_summary["lesson_id"], self.lesson.pk)
        self.client.force_login(self.student.user)
        self.assertNotContains(self.client.get(self.lesson_url()), comment.body)
        self.assertEqual(comment_services.visible_comments(lesson=self.lesson), [])
        self.client.force_login(self.teacher_user)
        url = self.comment_url("lesson_comment_restore", comment)
        self.assertIn(self.client.get(url).status_code, (404, 405))
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.client.post(url).status_code, 302)
        comment.refresh_from_db()
        self.assertFalse(comment.is_removed)
        self.assertIsNone(comment.removed_at)
        self.assertIsNone(comment.removed_by_id)
        audit = AuditLog.objects.get(action="lesson.comment.restored", object_id=str(comment.pk))
        self.assertEqual(audit.actor_id, self.teacher_user.pk)
        self.client.force_login(self.student.user)
        self.assertContains(self.client.get(self.lesson_url()), comment.body)

    def test_comment_services_remove_and_restore_preserve_body_and_author(self):
        self.enable_comments()
        comment = comment_services.add_comment(lesson=self.lesson, student=self.student, body="Keep this")
        comment_services.remove_comment(comment=comment, actor=self.teacher_user)
        self.assertTrue(comment.is_removed)
        comment_services.restore_comment(comment=comment, actor=self.teacher_user)
        comment.refresh_from_db()
        self.assertFalse(comment.is_removed)
        self.assertEqual((comment.body, comment.student_id, comment.lesson_id),
                         ("Keep this", self.student.pk, self.lesson.pk))

    def test_unassigned_teacher_and_foreign_comment_ids_cannot_be_moderated(self):
        self.enable_comments()
        comment = comment_services.add_comment(lesson=self.lesson, student=self.student, body="Local marker")
        foreign = LessonComment.objects.create(
            lesson=self.foreign_lesson, student=self.foreign_student, body="Foreign marker",
        )
        self.client.force_login(self.other_teacher_user)
        for name in ("lesson_comment_remove", "lesson_comment_restore"):
            with self.subTest(action=name):
                self.assertEqual(self.client.post(self.comment_url(name, comment)).status_code, 404)
        self.client.force_login(self.teacher_user)
        for name in ("lesson_comment_remove", "lesson_comment_restore"):
            with self.subTest(action=name):
                self.assertEqual(self.client.post(self.comment_url(name, foreign)).status_code, 404)
                self.assertEqual(self.client.post(self.comment_url(name, foreign,
                                                                  lesson=self.foreign_lesson)).status_code, 404)
        foreign.refresh_from_db()
        self.assertFalse(foreign.is_removed)
        self.assertFalse(AuditLog.objects.filter(action="lesson.comment.removed").exists())


class TestPreviewAsStudent(OrganiseReleaseBase):
    def setUp(self):
        self.client.force_login(self.teacher_user)

    def test_selected_student_classroom_preview_respects_assignment_and_topics(self):
        topic = self.topic("Visible topic marker")
        self.lesson.topic = topic
        self.lesson.save(update_fields=["topic"])
        hidden_topic = self.topic("Unassigned topic marker", position=1)
        hidden = Lesson.objects.create(module=self.module, title="Unassigned preview marker",
                                        topic=hidden_topic, position=1, assign_all=False)
        hidden.assigned_students.add(self.peer)
        response = self.client.get(self.classroom_url(teacher=True), {"student_id": self.student.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.lesson.title)
        self.assertContains(response, topic.title)
        self.assertNotContains(response, hidden.title)
        self.assertNotContains(response, hidden_topic.title)
        self.assert_no_student_writes()

    def test_selected_student_preview_respects_quiz_assignment_independently(self):
        self.set_quiz_targets([self.peer])
        response = self.client.get(self.classroom_url(teacher=True), {"student_id": self.student.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.lesson.title)
        self.assertNotContains(response, self.quiz_url("quiz_landing"))
        response = self.client.get(self.classroom_url(teacher=True), {"student_id": self.peer.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("learning:teacher_quiz_preview", args=[
            self.sc.pk, self.subj.pk, self.module.pk, self.lesson.pk,
        ]))
        self.assertFalse(LessonProgress.objects.exists())
        self.assertFalse(QuizAttempt.objects.exists())

    def test_selected_student_preview_respects_module_lesson_and_quiz_schedules(self):
        for obj in (self.module, self.lesson, self.quiz):
            obj.publish_at, obj.hide_at = NOW, END
            obj.save(update_fields=["publish_at", "hide_at"])
            for clock, released in ((NOW - TICK, False), (NOW, True), (END, False)):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    with patch("apps.learning.release.timezone.now", return_value=clock):
                        response = self.client.get(self.classroom_url(teacher=True),
                                                   {"student_id": self.student.pk})
                        self.assertEqual(response.status_code, 200)
                        if isinstance(obj, Quiz):
                            quiz_preview_url = reverse("learning:teacher_quiz_preview", args=[
                                self.sc.pk, self.subj.pk, self.module.pk, self.lesson.pk,
                            ])
                            self.assertEqual(quiz_preview_url in response.content.decode(), released)
                            self.assertContains(response, self.lesson.title)
                        else:
                            self.assertEqual(self.lesson.title in response.content.decode(), released)
            obj.publish_at = obj.hide_at = None
            obj.save(update_fields=["publish_at", "hide_at"])
        self.assert_no_student_writes()

    def test_selected_student_lesson_preview_denies_unassigned_lesson(self):
        self.set_lesson_targets([self.peer])
        response = self.client.get(self.lesson_url(preview=True), {"student_id": self.student.pk})
        self.assertEqual(response.status_code, 404)
        self.assert_no_student_writes()

    def test_selected_student_lesson_preview_denies_unreleased_lesson_and_module(self):
        for obj in (self.module, self.lesson):
            obj.publish_at, obj.hide_at = NOW, END
            obj.save(update_fields=["publish_at", "hide_at"])
            for clock in (NOW - TICK, END):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    with patch("apps.learning.release.timezone.now", return_value=clock):
                        response = self.client.get(self.lesson_url(preview=True),
                                                   {"student_id": self.student.pk})
                        self.assertEqual(response.status_code, 404)
            obj.publish_at = obj.hide_at = None
            obj.save(update_fields=["publish_at", "hide_at"])
        self.assert_no_student_writes()

    def test_selected_student_preview_get_does_not_create_progress_or_attempts(self):
        for url in (self.classroom_url(teacher=True), self.lesson_url(preview=True)):
            with self.subTest(url=url):
                response = self.client.get(url, {"student_id": self.student.pk})
                self.assertEqual(response.status_code, 200)
                self.assert_no_student_writes()
                self.assertFalse(LessonProgress.objects.exists())
                self.assertFalse(QuizAttempt.objects.exists())

    def test_selected_student_preview_rejects_foreign_and_invalid_student_ids(self):
        for student_id in (self.foreign_student.pk, "not-an-id", 99999999):
            for url in (self.classroom_url(teacher=True), self.lesson_url(preview=True)):
                with self.subTest(student_id=student_id, url=url):
                    self.assertEqual(self.client.get(url, {"student_id": student_id}).status_code, 404)
        self.assert_no_student_writes()

    def test_unassigned_teacher_cannot_preview_a_selected_student(self):
        self.client.force_login(self.other_teacher_user)
        for url in (self.classroom_url(teacher=True), self.lesson_url(preview=True)):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, {"student_id": self.student.pk}).status_code, 404)
        self.assert_no_student_writes()

    def test_normal_teacher_preview_still_shows_drafts_without_student_writes(self):
        self.lesson.is_published = False
        self.lesson.save(update_fields=["is_published"])
        response = self.client.get(self.lesson_url(preview=True))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.lesson.title)
        self.assert_no_student_writes()


class TestFinalReleaseGuards(OrganiseReleaseBase):
    def teacher_quiz_preview_url(self):
        return reverse("learning:teacher_quiz_preview", args=[
            self.sc.pk, self.subj.pk, self.module.pk, self.lesson.pk,
        ])

    def test_classroom_preview_links_open_read_only_quiz_preview(self):
        self.client.force_login(self.teacher_user)
        response = self.client.get(self.classroom_url(teacher=True), {"student_id": self.student.pk})
        self.assertContains(response, self.teacher_quiz_preview_url())
        self.assertNotContains(response, self.quiz_url("quiz_landing"))
        self.assertNotContains(response, "Classroom menu")
        self.assertEqual(self.client.get(self.teacher_quiz_preview_url(), {
            "student_id": self.student.pk,
        }).status_code, 200)
        self.assert_no_student_writes()

    def test_selected_quiz_preview_checks_targets_and_student_membership(self):
        self.client.force_login(self.teacher_user)
        self.set_quiz_targets([self.peer])
        for student_id in (self.student.pk, self.foreign_student.pk, "invalid"):
            with self.subTest(student_id=student_id):
                self.assertEqual(self.client.get(self.teacher_quiz_preview_url(), {
                    "student_id": student_id,
                }).status_code, 404)
        self.assertEqual(self.client.get(self.teacher_quiz_preview_url(), {
            "student_id": self.peer.pk,
        }).status_code, 200)
        self.assertFalse(QuizAttempt.objects.exists())

    def test_selected_quiz_preview_rechecks_all_release_windows(self):
        self.client.force_login(self.teacher_user)
        for obj in (self.module, self.lesson, self.quiz):
            obj.publish_at, obj.hide_at = NOW, END
            obj.save(update_fields=["publish_at", "hide_at"])
            for clock, status in ((NOW - TICK, 404), (NOW, 200), (END, 404)):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    with patch("apps.learning.release.timezone.now", return_value=clock):
                        self.assertEqual(self.client.get(self.teacher_quiz_preview_url(), {
                            "student_id": self.student.pk,
                        }).status_code, status)
            obj.publish_at = obj.hide_at = None
            obj.save(update_fields=["publish_at", "hide_at"])
        self.assertFalse(QuizAttempt.objects.exists())

    def test_result_reads_recheck_release_without_deleting_attempts(self):
        attempt = QuizAttempt.objects.create(quiz=self.quiz, student=self.student, submitted_at=NOW)
        url = reverse("learning:quiz_results", args=[
            self.sc.pk, self.subj.pk, self.lesson.pk, attempt.pk,
        ])
        for user in (self.student.user, self.parent_user):
            self.client.force_login(user)
            for obj in (self.module, self.lesson, self.quiz):
                obj.publish_at, obj.hide_at = NOW, END
                obj.save(update_fields=["publish_at", "hide_at"])
                for clock, status in ((NOW - TICK, 404), (NOW, 200), (END, 404)):
                    with self.subTest(role=user.role, model=type(obj).__name__, clock=clock):
                        with patch("apps.learning.release.timezone.now", return_value=clock):
                            self.assertEqual(self.client.get(url).status_code, status)
                obj.publish_at = obj.hide_at = None
                obj.save(update_fields=["publish_at", "hide_at"])
        self.assertTrue(QuizAttempt.objects.filter(pk=attempt.pk, submitted_at=NOW).exists())

    def test_unreleased_quizzes_have_no_missing_attempts(self):
        for obj in (self.module, self.lesson, self.quiz):
            obj.publish_at, obj.hide_at = NOW, END
            obj.save(update_fields=["publish_at", "hide_at"])
            for clock, count in ((NOW - TICK, 0), (NOW, 3), (END, 0)):
                with self.subTest(model=type(obj).__name__, clock=clock):
                    with patch("apps.learning.release.timezone.now", return_value=clock):
                        data = teacher_quiz_results(quiz=self.quiz, school_class=self.sc)
                        self.assertEqual(len(data.not_attempted), count)
            obj.publish_at = obj.hide_at = None
            obj.save(update_fields=["publish_at", "hide_at"])

    def test_invalid_schedule_edit_keeps_existing_content_and_release(self):
        self.client.force_login(self.teacher_user)
        items = [
            (self.module, reverse("learning:module_edit", args=[self.sc.pk, self.subj.pk, self.module.pk])),
            (self.lesson, reverse("learning:lesson_edit", args=[self.sc.pk, self.subj.pk, self.module.pk, self.lesson.pk])),
            (self.quiz, reverse("learning:teacher_quiz_settings", args=[self.sc.pk, self.subj.pk, self.module.pk, self.lesson.pk])),
        ]
        for obj, url in items:
            original_title = obj.title
            obj.publish_at, obj.hide_at = NOW, END
            obj.save(update_fields=["publish_at", "hide_at"])
            for publish, hide in (("not-a-date", ""), ("2026-11-20T10:00", "2026-11-20T09:00")):
                with self.subTest(model=type(obj).__name__, publish=publish):
                    response = self.client.post(url, {
                        "title": "Must not save", "is_published": "1", "notes_format": "plain",
                        "pass_mark": "50", "attempts_allowed": "1",
                        "publish_at": publish, "hide_at": hide,
                    })
                    self.assertEqual(response.status_code, 302)
                    obj.refresh_from_db()
                    self.assertEqual(obj.title, original_title)
                    self.assertEqual((obj.publish_at, obj.hide_at), (NOW, END))

    def test_invalid_schedule_create_does_not_leave_partial_items(self):
        self.client.force_login(self.teacher_user)
        no_quiz_lesson = Lesson.objects.create(module=self.module, title="No quiz yet")
        items = [
            (CourseModule, reverse("learning:module_create", args=[self.sc.pk, self.subj.pk])),
            (Lesson, reverse("learning:lesson_create", args=[self.sc.pk, self.subj.pk, self.module.pk])),
            (Quiz, reverse("learning:teacher_quiz_create", args=[self.sc.pk, self.subj.pk, self.module.pk, no_quiz_lesson.pk])),
        ]
        for model, url in items:
            before = model.objects.count()
            response = self.client.post(url, {
                "title": "Must not create", "term_id": self.term.pk,
                "publish_at": "2026-11-20T10:00", "hide_at": "2026-11-20T09:00",
            })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(model.objects.count(), before)


class TestTopicNavigation(OrganiseReleaseBase):
    def navigation(self, name, lesson):
        return getattr(course_selectors, name)(
            lesson_id=lesson.pk, school_class=self.sc, subject=self.subj,
            term=self.term, student=self.student,
        )

    def test_item_move_changes_start_continue_and_previous_next(self):
        topic = self.topic()
        topic_services.set_lesson_topic(lesson=self.lesson, topic=topic, actor=self.teacher_user)
        second = Lesson.objects.create(module=self.module, title="Second lesson", position=1)
        topic_services.set_lesson_topic(lesson=second, topic=topic, actor=self.teacher_user)
        topic_services.move_lesson_in_topic(lesson=self.lesson, direction="down", actor=self.teacher_user)
        self.assertEqual(self.navigation("next_lesson_for_student", second).lesson.pk, self.lesson.pk)
        self.assertEqual(self.navigation("prev_lesson_for_student", self.lesson).lesson.pk, second.pk)
        self.assertIsNone(self.navigation("prev_lesson_for_student", second))
        self.assertIsNone(self.navigation("next_lesson_for_student", self.lesson))
        self.client.force_login(self.student.user)
        response = self.client.get(self.classroom_url())
        self.assertEqual(response.context["next_lesson"].lesson.pk, second.pk)
        LessonProgress.objects.create(student=self.student, lesson=second, completed=True)
        response = self.client.get(self.classroom_url())
        self.assertEqual(response.context["next_lesson"].lesson.pk, self.lesson.pk)

    def test_topic_move_orders_navigation_across_modules_and_other_lessons(self):
        first_topic = self.topic("First topic")
        second_topic = self.topic("Second topic", position=1)
        topic_services.set_lesson_topic(lesson=self.lesson, topic=first_topic, actor=self.teacher_user)
        later_module = CourseModule.objects.create(
            school_class=self.sc, subject=self.subj, term=self.term,
            title="Later module", position=1,
        )
        second = Lesson.objects.create(module=later_module, title="Second topic lesson")
        topic_services.set_lesson_topic(lesson=second, topic=second_topic, actor=self.teacher_user)
        other = Lesson.objects.create(module=self.module, title="Other lesson", position=1)
        topic_services.move_topic(topic=second_topic, direction="up", actor=self.teacher_user)
        self.assertEqual(self.navigation("next_lesson_for_student", second).lesson.pk, self.lesson.pk)
        self.assertEqual(self.navigation("next_lesson_for_student", self.lesson).lesson.pk, other.pk)
        self.assertEqual(self.navigation("prev_lesson_for_student", other).lesson.pk, self.lesson.pk)
        self.client.force_login(self.student.user)
        self.assertEqual(self.client.get(self.classroom_url()).context["next_lesson"].lesson.pk, second.pk)

    def test_navigation_skips_hidden_unassigned_topics_and_keeps_legacy_order(self):
        second = Lesson.objects.create(module=self.module, title="Legacy second lesson", position=1)
        self.assertEqual(self.navigation("next_lesson_for_student", self.lesson).lesson.pk, second.pk)
        hidden_topic = self.topic("Hidden topic")
        hidden = Lesson.objects.create(module=self.module, title="Future lesson", topic=hidden_topic,
                                       publish_at=END, position=2)
        chosen = Lesson.objects.create(module=self.module, title="Peer only", topic=hidden_topic,
                                       assign_all=False, position=3)
        chosen.assigned_students.add(self.peer)
        with patch("apps.learning.release.timezone.now", return_value=NOW):
            ordered = course_selectors.ordered_course_lessons(self.course())
            self.assertEqual([lp.lesson.pk for lp in ordered], [self.lesson.pk, second.pk])
            self.assertIsNone(self.navigation("next_lesson_for_student", second))
            self.assertNotIn(hidden.pk, self.lesson_ids())
