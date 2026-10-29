"""All six role dashboards render inside their own shell with shared chrome."""

from django.test import TestCase
from django.urls import reverse

from apps.students.services import create_student_with_guardian
from apps.teachers.models import TeacherProfile

from .fixtures import (
    make_accounts,
    make_class,
    make_student_user,
    make_term,
    make_user,
    make_year,
)


class RoleHomeShellTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        make_accounts()
        make_student_user(cls.school_class, admission_number="SHELL-001")

        parent_student = create_student_with_guardian(
            admission_number="SHELL-002",
            first_name="Shell",
            last_name="Child",
            email="shell.child@test.school",
            current_class=cls.school_class,
            guardian_first_name="Shell",
            guardian_last_name="Parent",
            guardian_email="shell.parent@test.school",
            send_credentials=False,
        )
        parent_user = parent_student.guardian_links.first().parent.user
        parent_user.must_change_password = False
        parent_user.save(update_fields=["must_change_password"])

        teacher = make_user("teacher", username="shell_teacher")
        TeacherProfile.objects.create(user=teacher, staff_id="SHELL-T")

        cls.users = {
            "admin": make_user("admin", username="shell_admin"),
            "director": make_user("director", username="shell_director"),
            "teacher": teacher,
            "student": make_student_user(
                cls.school_class, admission_number="SHELL-003"
            ).user,
            "parent": parent_user,
            "finance": make_user("finance", username="shell_finance"),
        }

    def test_each_role_home_renders_with_own_shell(self):
        for role, user in self.users.items():
            with self.subTest(role=role):
                self.client.force_login(user)
                response = self.client.get(reverse(f"dashboards:{role}_home"))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, f"dashboards/{role}/base.html")
                self.assertContains(response, 'id="app-sidebar"')
                self.assertContains(response, 'class="app-navbar"')
                self.assertContains(response, "width=device-width")
                self.assertContains(response, "sidebar-toggle")
