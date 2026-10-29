"""
apps/communications/tests/test_announcements.py
-------------------------------------------------
Module 1 — announcements.

Covers:
  - audience targeting: everyone / by role / by class
  - class audience reaches students AND their linked parents
  - scheduled (future publish_at) and expired announcements are hidden
  - reusable service creates in-app notifications
  - admin compose screen creates an announcement
"""

from __future__ import annotations

import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, SchoolClass, Subject
from apps.accounts.models import ParentProfile, User
from apps.core.models import Notification
from apps.students.models import Student, StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile

from ..models import Announcement
from ..selectors import announcements_for_user
from ..services import create_announcement


class AnnouncementAudienceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.class_a = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.class_b = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 6", level_order=6
        )

        cls.admin = User.objects.create_user(
            username="ann_admin",
            email="ann_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.director = User.objects.create_user(
            username="ann_director",
            email="ann_director@test.school",
            password="Testpass1!",
            role=User.Role.DIRECTOR,
            must_change_password=False,
        )
        cls.teacher = User.objects.create_user(
            username="ann_teacher",
            email="ann_teacher@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        cls.student_a_user = User.objects.create_user(
            username="ann_student_a",
            email="ann_student_a@test.school",
            password="Testpass1!",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        cls.student_a = Student.objects.create(
            user=cls.student_a_user,
            admission_number="ANN-A-1",
            current_class=cls.class_a,
        )
        cls.student_b_user = User.objects.create_user(
            username="ann_student_b",
            email="ann_student_b@test.school",
            password="Testpass1!",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        cls.student_b = Student.objects.create(
            user=cls.student_b_user,
            admission_number="ANN-B-1",
            current_class=cls.class_b,
        )
        cls.parent_user = User.objects.create_user(
            username="ann_parent",
            email="ann_parent@test.school",
            password="Testpass1!",
            role=User.Role.PARENT,
            must_change_password=False,
        )
        cls.parent_profile = ParentProfile.objects.create(user=cls.parent_user)
        StudentGuardian.objects.create(
            student=cls.student_a,
            parent=cls.parent_profile,
            is_primary=True,
        )
        cls.inactive_user = User.objects.create_user(
            username="ann_inactive",
            email="ann_inactive@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
            is_active=False,
        )

    def _notified_usernames(self) -> set[str]:
        return set(
            Notification.objects.filter(category="announcement").values_list(
                "recipient__username", flat=True
            )
        )

    def test_by_role_notifies_only_that_role(self):
        create_announcement(
            title="Students only",
            body="Assembly tomorrow",
            audience=Announcement.Audience.BY_ROLE,
            target_roles=["student"],
            created_by=self.admin,
        )
        self.assertEqual(
            self._notified_usernames(),
            {"ann_student_a", "ann_student_b"},
        )

    def test_by_class_notifies_students_and_linked_parents(self):
        create_announcement(
            title="Grade 5 trip",
            body="Bring a packed lunch",
            audience=Announcement.Audience.BY_CLASS,
            target_class_ids=[self.class_a.pk],
            created_by=self.admin,
        )
        self.assertEqual(
            self._notified_usernames(),
            {"ann_student_a", "ann_parent"},
        )

    def test_everyone_notifies_all_active_users(self):
        create_announcement(
            title="School closed",
            body="Public holiday",
            audience=Announcement.Audience.EVERYONE,
            created_by=self.admin,
        )
        self.assertEqual(
            self._notified_usernames(),
            {
                "ann_admin",
                "ann_director",
                "ann_teacher",
                "ann_student_a",
                "ann_student_b",
                "ann_parent",
            },
        )

    def test_scheduled_and_unpublished_do_not_notify(self):
        create_announcement(
            title="Scheduled",
            body="Later",
            audience=Announcement.Audience.EVERYONE,
            publish_at=timezone.now() + datetime.timedelta(days=1),
            created_by=self.admin,
        )
        create_announcement(
            title="Draft",
            body="Not yet",
            audience=Announcement.Audience.EVERYONE,
            is_published=False,
            created_by=self.admin,
        )
        self.assertEqual(
            Notification.objects.filter(category="announcement").count(), 0
        )


class AnnouncementVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.class_a = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.class_b = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 6", level_order=6
        )
        cls.admin = User.objects.create_user(
            username="vis_admin",
            email="vis_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.student_a_user = User.objects.create_user(
            username="vis_student_a",
            email="vis_student_a@test.school",
            password="Testpass1!",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        cls.student_a = Student.objects.create(
            user=cls.student_a_user,
            admission_number="VIS-A-1",
            current_class=cls.class_a,
        )
        cls.student_b_user = User.objects.create_user(
            username="vis_student_b",
            email="vis_student_b@test.school",
            password="Testpass1!",
            role=User.Role.STUDENT,
            must_change_password=False,
        )
        cls.student_b = Student.objects.create(
            user=cls.student_b_user,
            admission_number="VIS-B-1",
            current_class=cls.class_b,
        )
        cls.parent_user = User.objects.create_user(
            username="vis_parent",
            email="vis_parent@test.school",
            password="Testpass1!",
            role=User.Role.PARENT,
            must_change_password=False,
        )
        parent_profile = ParentProfile.objects.create(user=cls.parent_user)
        StudentGuardian.objects.create(
            student=cls.student_a, parent=parent_profile, is_primary=True
        )
        cls.teacher = User.objects.create_user(
            username="vis_teacher",
            email="vis_teacher@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        teacher_profile = TeacherProfile.objects.create(
            user=cls.teacher, staff_id="VIS-T-1"
        )
        TeacherAssignment.objects.create(
            teacher=teacher_profile,
            school_class=cls.class_a,
            subject=Subject.objects.create(code="VIS-SUB", name="Vis Subject"),
            academic_year=cls.year,
        )

        def ann(title, audience, **kwargs):
            return create_announcement(
                title=title,
                body=title,
                audience=audience,
                created_by=cls.admin,
                is_published=kwargs.pop("is_published", True),
                publish_at=kwargs.pop("publish_at", None),
                expires_at=kwargs.pop("expires_at", None),
                target_roles=kwargs.pop("target_roles", None),
                target_class_ids=kwargs.pop("target_class_ids", None),
            )

        cls.everyone = ann("Everyone", Announcement.Audience.EVERYONE)
        cls.students_only = ann(
            "Students only",
            Announcement.Audience.BY_ROLE,
            target_roles=["student"],
        )
        cls.parents_only = ann(
            "Parents only",
            Announcement.Audience.BY_ROLE,
            target_roles=["parent"],
        )
        cls.class_a_ann = ann(
            "Grade 5",
            Announcement.Audience.BY_CLASS,
            target_class_ids=[cls.class_a.pk],
        )
        cls.class_b_ann = ann(
            "Grade 6",
            Announcement.Audience.BY_CLASS,
            target_class_ids=[cls.class_b.pk],
        )
        cls.future = ann(
            "Future",
            Announcement.Audience.EVERYONE,
            publish_at=timezone.now() + datetime.timedelta(days=2),
        )
        cls.expired = ann(
            "Expired",
            Announcement.Audience.EVERYONE,
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )
        cls.draft = ann(
            "Draft",
            Announcement.Audience.EVERYONE,
            is_published=False,
        )

    def _titles(self, user) -> set[str]:
        return set(announcements_for_user(user=user).values_list("title", flat=True))

    def test_student_sees_everyone_role_and_own_class_only(self):
        self.assertEqual(
            self._titles(self.student_a_user),
            {"Everyone", "Students only", "Grade 5"},
        )
        self.assertEqual(
            self._titles(self.student_b_user),
            {"Everyone", "Students only", "Grade 6"},
        )

    def test_parent_sees_everyone_parent_role_and_childs_class(self):
        self.assertEqual(
            self._titles(self.parent_user),
            {"Everyone", "Parents only", "Grade 5"},
        )

    def test_teacher_sees_only_own_class_announcements(self):
        self.assertEqual(
            self._titles(self.teacher),
            {"Everyone", "Grade 5"},
        )

    def test_compose_view_creates_announcement(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("communications:announcement_create"),
            {
                "title": "Composed",
                "body": "Created from the admin screen.",
                "audience": Announcement.Audience.EVERYONE,
                "is_published": "on",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("communications:list"))
        announcement = Announcement.objects.get(title="Composed")
        self.assertTrue(announcement.is_published)
        self.assertEqual(announcement.created_by, self.admin)
