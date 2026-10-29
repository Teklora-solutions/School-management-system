"""
apps/dashboards/tests/test_parent_scope.py
-------------------------------------------
Module 4: parent child-switcher must only ever show linked children.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ParentProfile
from apps.students.models import StudentGuardian

from .fixtures import make_class, make_student_user, make_user, make_year


class ParentChildScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.school_class = make_class(cls.year)
        cls.parent_user = make_user("parent", username="scope_parent")
        cls.parent_profile = ParentProfile.objects.create(user=cls.parent_user)

        cls.child_a = make_student_user(cls.school_class, admission_number="SCOPE-A")
        cls.child_b = make_student_user(cls.school_class, admission_number="SCOPE-B")
        cls.unrelated = make_student_user(cls.school_class, admission_number="SCOPE-X")
        for student, first_name in [
            (cls.child_a, "Alpha"),
            (cls.child_b, "Beta"),
            (cls.unrelated, "Unrelated"),
        ]:
            student.user.first_name = first_name
            student.user.save(update_fields=["first_name", "updated_at"])
        StudentGuardian.objects.create(
            student=cls.child_a,
            parent=cls.parent_profile,
            is_primary=True,
        )
        StudentGuardian.objects.create(
            student=cls.child_b,
            parent=cls.parent_profile,
            is_primary=False,
        )

    def test_default_is_primary_child(self):
        self.client.force_login(self.parent_user)
        response = self.client.get(reverse("dashboards:parent_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, f"Viewing: <strong>{self.child_a.user.full_name}</strong>"
        )

    def test_switcher_can_select_own_linked_child(self):
        self.client.force_login(self.parent_user)
        session = self.client.session
        session["selected_child_id"] = self.child_b.pk
        session.save()
        response = self.client.get(reverse("dashboards:parent_home"))
        self.assertContains(
            response, f"Viewing: <strong>{self.child_b.user.full_name}</strong>"
        )

    def test_tampered_session_falls_back_to_linked_child(self):
        self.client.force_login(self.parent_user)
        session = self.client.session
        session["selected_child_id"] = self.unrelated.pk
        session.save()
        response = self.client.get(reverse("dashboards:parent_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, f"Viewing: <strong>{self.child_a.user.full_name}</strong>"
        )
        self.assertNotContains(
            response, f"Viewing: <strong>{self.unrelated.user.full_name}</strong>"
        )
