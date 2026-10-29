"""
apps/accounts/tests/test_staff_management.py
---------------------------------------------
Module 1 — staff user management.

Covers:
  - the single ``create_staff_user`` service used for Teacher/Director/Finance
  - the admin setting the initial password (with the generate-one fallback)
  - force-change-on-first-login as a choice rather than a certainty
  - credential email queued
  - deactivate / reactivate / reset-password
  - users can never be deleted (Django admin permission)
  - ForcePasswordChangeMiddleware
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib import admin as django_admin
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import AuditLog
from apps.teachers.models import TeacherProfile

from ..admin import UserAdmin
from ..models import DirectorProfile, FinanceProfile, User
from ..services import (
    _send_credentials_email,
    create_staff_user,
    deactivate_user,
    reactivate_user,
    reset_user_password,
)


class StaffUserServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user(
            username="staff_admin",
            email="staff_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )

    def test_create_teacher_without_a_password_generates_one(self):
        """
        The seeder and any caller that never emails credentials still get a
        generated password; the admin-facing form is the path that supplies one.
        """
        with patch("apps.accounts.services.enqueue_credentials_email") as queued:
            user, temp_password = create_staff_user(
                role=User.Role.TEACHER,
                first_name="Jane",
                last_name="Doe",
                email="jane.doe@test.school",
                phone="0244000001",
                staff_id="TCH-9001",
                actor=self.actor,
            )

        self.assertEqual(user.role, User.Role.TEACHER)
        self.assertEqual(user.username, "jane_doe")
        self.assertTrue(user.is_active)
        self.assertTrue(user.must_change_password)
        self.assertTrue(temp_password)
        self.assertTrue(user.check_password(temp_password))
        self.assertTrue(
            TeacherProfile.objects.filter(user=user, staff_id="TCH-9001").exists()
        )
        queued.assert_called_once_with(user, temp_password, True)
        self.assertTrue(
            AuditLog.objects.filter(
                action="user.staff_created", object_id=str(user.pk)
            ).exists()
        )

    def test_admin_supplied_password_is_used_verbatim(self):
        with patch("apps.accounts.services.enqueue_credentials_email") as queued:
            user, returned = create_staff_user(
                role=User.Role.DIRECTOR,
                first_name="Ada",
                last_name="Direct",
                email="ada.direct@test.school",
                password="BoardRoom-2026",
                actor=self.actor,
            )

        self.assertEqual(returned, "BoardRoom-2026")
        self.assertTrue(user.check_password("BoardRoom-2026"))
        self.assertTrue(user.must_change_password)
        queued.assert_called_once_with(user, "BoardRoom-2026", True)

    def test_must_change_password_flag_follows_the_admin_choice(self):
        with patch("apps.accounts.services.enqueue_credentials_email") as queued:
            user, _ = create_staff_user(
                role=User.Role.FINANCE,
                first_name="No",
                last_name="Force",
                email="no.force@test.school",
                password="KeptForNow-9",
                must_change_password=False,
                actor=self.actor,
            )

        self.assertFalse(user.must_change_password)
        queued.assert_called_once_with(user, "KeptForNow-9", False)

    def test_create_director_and_finance_create_role_profiles(self):
        cases = [
            (User.Role.DIRECTOR, DirectorProfile),
            (User.Role.FINANCE, FinanceProfile),
        ]
        with patch("apps.accounts.services.enqueue_credentials_email"):
            for role, profile_cls in cases:
                with self.subTest(role=role):
                    user, _ = create_staff_user(
                        role=role,
                        first_name="Test",
                        last_name=role.title(),
                        email=f"{role}@test.school",
                        staff_id=f"STAFF-{role.upper()}",
                        actor=self.actor,
                    )
                    self.assertTrue(profile_cls.objects.filter(user=user).exists())

    def test_non_staff_role_is_rejected(self):
        with self.assertRaises(ValidationError):
            create_staff_user(
                role=User.Role.STUDENT,
                first_name="Not",
                last_name="Staff",
                email="not.staff@test.school",
                actor=self.actor,
                send_credentials=False,
            )

    def test_duplicate_email_is_rejected(self):
        with patch("apps.accounts.services.enqueue_credentials_email"):
            create_staff_user(
                role=User.Role.TEACHER,
                first_name="First",
                last_name="Teacher",
                email="duplicate@test.school",
                actor=self.actor,
            )
        with self.assertRaises(ValidationError) as ctx:
            create_staff_user(
                role=User.Role.FINANCE,
                first_name="Second",
                last_name="Staff",
                email="duplicate@test.school",
                actor=self.actor,
                send_credentials=False,
            )
        self.assertIn("email", ctx.exception.message_dict)

    def test_deactivate_reactivate_and_self_guard(self):
        with patch("apps.accounts.services.enqueue_credentials_email"):
            target, _ = create_staff_user(
                role=User.Role.FINANCE,
                first_name="Target",
                last_name="User",
                email="target.finance@test.school",
                actor=self.actor,
            )

        deactivate_user(user=target, actor=self.actor)
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertTrue(
            AuditLog.objects.filter(
                action="user.deactivated", object_id=str(target.pk)
            ).exists()
        )

        reactivate_user(user=target, actor=self.actor)
        target.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertTrue(
            AuditLog.objects.filter(
                action="user.reactivated", object_id=str(target.pk)
            ).exists()
        )

        with self.assertRaises(ValidationError):
            deactivate_user(user=self.actor, actor=self.actor)

    def test_reset_password_forces_change_and_audits(self):
        with patch("apps.accounts.services.enqueue_credentials_email"):
            target, old_temp = create_staff_user(
                role=User.Role.TEACHER,
                first_name="Reset",
                last_name="Me",
                email="reset.me@test.school",
                actor=self.actor,
            )

        with patch("apps.accounts.services.enqueue_credentials_email") as queued:
            new_temp = reset_user_password(user=target, actor=self.actor)

        target.refresh_from_db()
        self.assertNotEqual(new_temp, old_temp)
        self.assertTrue(target.check_password(new_temp))
        self.assertTrue(target.must_change_password)
        queued.assert_called_once_with(target, new_temp)
        self.assertTrue(
            AuditLog.objects.filter(
                action="user.password_reset", object_id=str(target.pk)
            ).exists()
        )

    def test_django_admin_cannot_delete_users(self):
        model_admin = UserAdmin(User, django_admin.site)
        self.assertFalse(model_admin.has_delete_permission(request=None))
        # No delete URL is exposed either — deactivation is the only exit.
        self.assertEqual(reverse("accounts:staff_list"), "/staff/")


class StaffCreateViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="view_admin",
            email="view_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )

    def test_admin_can_create_staff_via_view(self):
        self.client.force_login(self.admin)
        with patch("apps.accounts.services.enqueue_credentials_email") as queued:
            resp = self.client.post(
                reverse("accounts:staff_create"),
                {
                    "role": "teacher",
                    "first_name": "View",
                    "last_name": "Created",
                    "email": "view.created@test.school",
                    "password": "Chosen-By-Admin1",
                    "password_confirm": "Chosen-By-Admin1",
                    "must_change_password": "on",
                    "phone": "",
                    "staff_id": "",
                },
            )
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(email="view.created@test.school")
        self.assertEqual(user.role, User.Role.TEACHER)
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password("Chosen-By-Admin1"))
        self.assertTrue(TeacherProfile.objects.filter(user=user).exists())
        queued.assert_called_once_with(user, "Chosen-By-Admin1", True)

    def test_password_is_required_by_the_form(self):
        self.client.force_login(self.admin)
        with patch("apps.accounts.services.enqueue_credentials_email") as queued:
            resp = self.client.post(
                reverse("accounts:staff_create"),
                {
                    "role": "director",
                    "first_name": "No",
                    "last_name": "Password",
                    "email": "no.password@test.school",
                    "phone": "",
                    "staff_id": "",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="no.password@test.school").exists())
        queued.assert_not_called()
        self.assertContains(resp, "Initial password")
        self.assertContains(resp, "Force a password change on first login")
        form = resp.context["form"]
        self.assertIn("password", form.errors)

    def test_mismatched_confirmation_is_rejected(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("accounts:staff_create"),
            {
                "role": "finance",
                "first_name": "Mis",
                "last_name": "Match",
                "email": "mis.match@test.school",
                "password": "Intended-Password-1",
                "password_confirm": "Typed-Wrong-2",
                "must_change_password": "on",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("password_confirm", resp.context["form"].errors)
        self.assertFalse(User.objects.filter(email="mis.match@test.school").exists())

    def test_weak_password_is_rejected_when_validators_are_on(self):
        """
        dev/test settings empty AUTH_PASSWORD_VALIDATORS, so this override is the
        only way the suite can see the form calling validate_password() at all.
        """
        self.client.force_login(self.admin)
        with override_settings(
            AUTH_PASSWORD_VALIDATORS=[
                {
                    "NAME": "django.contrib.auth.password_validation"
                    ".MinimumLengthValidator"
                }
            ]
        ):
            resp = self.client.post(
                reverse("accounts:staff_create"),
                {
                    "role": "teacher",
                    "first_name": "Weak",
                    "last_name": "Pass",
                    "email": "weak.pass@test.school",
                    "password": "abc",
                    "password_confirm": "abc",
                    "must_change_password": "on",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("password", resp.context["form"].errors)
        self.assertFalse(User.objects.filter(email="weak.pass@test.school").exists())

    def test_unticking_force_change_creates_a_signin_ready_account(self):
        self.client.force_login(self.admin)
        with patch("apps.accounts.services.enqueue_credentials_email"):
            resp = self.client.post(
                reverse("accounts:staff_create"),
                {
                    "role": "director",
                    "first_name": "Settled",
                    "last_name": "Password",
                    "email": "settled.password@test.school",
                    "password": "Handed-Over-1",
                    "password_confirm": "Handed-Over-1",
                },
            )
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(email="settled.password@test.school")
        self.assertFalse(user.must_change_password)


class CredentialsEmailTests(TestCase):
    """
    The email is the only thing most people ever see of their credentials, so
    its two lines must not blur together now that a password can be something
    other than the admission number.
    """

    def setUp(self):
        mail.outbox = []

    def test_student_email_prints_the_username_not_a_copy_of_the_password(self):
        student = User.objects.create_user(
            username="adm2002",
            email="adm2002@test.school",
            password="Adm2002-Override",
            role=User.Role.STUDENT,
        )
        _send_credentials_email(student.pk, "Adm2002-Override")

        body = mail.outbox[0].body
        self.assertIn("Username (Admission No.): adm2002", body)
        self.assertIn("Password: Adm2002-Override", body)

    def test_staff_email_drops_the_change_notice_when_the_password_is_final(self):
        staff = User.objects.create_user(
            username="handed_over",
            email="handed.over@test.school",
            password="Handed-Over-1",
            role=User.Role.FINANCE,
        )
        _send_credentials_email(staff.pk, "Handed-Over-1", must_change=False)

        body = mail.outbox[0].body
        self.assertIn("Password: Handed-Over-1", body)
        self.assertNotIn("required to set a new password", body)

    def test_staff_email_keeps_the_change_notice_by_default(self):
        staff = User.objects.create_user(
            username="still_forced",
            email="still.forced@test.school",
            password="Generated-1",
            role=User.Role.TEACHER,
        )
        _send_credentials_email(staff.pk, "Generated-1")

        self.assertIn(
            "required to set a new password", mail.outbox[0].body
        )


class ForcePasswordChangeMiddlewareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="force_pw_admin",
            email="force_pw_admin@test.school",
            password="OldTempPass1!",
            role=User.Role.ADMIN,
            must_change_password=True,
        )

    def test_flagged_user_is_redirected_to_change_password(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("dashboards:admin_home"))
        self.assertRedirects(
            resp,
            reverse("accounts:force_password_change"),
            fetch_redirect_response=False,
        )

        # The change-password screen itself is reachable.
        resp = self.client.get(reverse("accounts:force_password_change"))
        self.assertEqual(resp.status_code, 200)

    def test_changing_password_clears_flag_and_allows_access(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse("accounts:force_password_change"),
            {
                "old_password": "OldTempPass1!",
                "new_password1": "BrandNewPass456!",
                "new_password2": "BrandNewPass456!",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)
        self.assertTrue(self.user.check_password("BrandNewPass456!"))

        resp = self.client.get(reverse("dashboards:admin_home"))
        self.assertEqual(resp.status_code, 200)
