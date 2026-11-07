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
  - editing an existing staff account (name, email, phone, staff ID)
  - deleting a staff account: the three guards, what disappears with the
    account, and the ``SET_NULL`` rows that keep their figures but lose their
    author
  - the Django admin site still refuses deletes (a second gate, not the only one)
  - ForcePasswordChangeMiddleware
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin as django_admin
from django.contrib.auth import authenticate
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.core.models import AuditLog
from apps.finance.models import FeeStructure, Invoice
from apps.students.services import create_student_with_guardian
from apps.teachers.models import TeacherAssignment, TeacherProfile

from ..admin import UserAdmin
from ..models import DirectorProfile, FinanceProfile, User
from ..services import (
    _send_credentials_email,
    create_staff_user,
    deactivate_user,
    delete_staff_user,
    reactivate_user,
    reset_user_password,
    staff_delete_forecast,
    staff_delete_refusals,
    update_staff_user,
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
        # The admin site exposes no delete of its own, so the guarded
        # ``accounts:staff_delete`` page is the only door — and it refuses first.
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


class StaffEditServiceTests(TestCase):
    """
    ``update_staff_user`` — the four fields an admin is allowed to correct.

    The two things worth pinning down are where ``staff_id`` is written (the
    role's profile row, never ``User``) and what an email change costs (the old
    address stops being a credential, because staff sign in with their email).
    """

    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user(
            username="edit_actor",
            email="edit.actor@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )

    def make_teacher(self, email="teacher.edit@test.school", staff_id="TCH-7001"):
        user, _ = create_staff_user(
            role=User.Role.TEACHER,
            first_name="Teach",
            last_name="Er",
            email=email,
            phone="0244000001",
            staff_id=staff_id,
            password="Chosen-By-Admin1",
            actor=self.actor,
            send_credentials=False,
        )
        return user

    def edit(self, user, **overrides):
        data = {
            "first_name": "Ann",
            "last_name": "Lee",
            "email": "ann.lee@test.school",
            "phone": "0244000009",
            "staff_id": "TCH-0101",
            "actor": self.actor,
        }
        data.update(overrides)
        return update_staff_user(user=user, **data)

    def test_identity_fields_and_staff_id_are_written(self):
        teacher = self.make_teacher()
        self.edit(teacher)
        teacher.refresh_from_db()

        self.assertEqual(teacher.full_name, "Ann Lee")
        self.assertEqual(teacher.email, "ann.lee@test.school")
        self.assertEqual(teacher.phone, "0244000009")
        teacher.teacher_profile.refresh_from_db()
        self.assertEqual(teacher.teacher_profile.staff_id, "TCH-0101")

        log = AuditLog.objects.get(
            action="user.staff_updated", object_id=str(teacher.pk)
        )
        self.assertEqual(log.change_summary["before"]["email"], "teacher.edit@test.school")
        self.assertEqual(log.change_summary["after"]["staff_id"], "TCH-0101")

    def test_the_username_survives_an_email_change_and_case_is_normalised(self):
        teacher = self.make_teacher()
        self.edit(teacher, email="Ann.Lee@TEST.school")
        teacher.refresh_from_db()

        self.assertEqual(teacher.username, "teacher_edit")
        self.assertEqual(teacher.email, "ann.lee@test.school")

    def test_the_new_email_signs_in_and_the_old_one_does_not(self):
        teacher = self.make_teacher()
        self.assertIsNotNone(
            authenticate(
                username="teacher.edit@test.school", password="Chosen-By-Admin1"
            )
        )
        self.edit(teacher)

        self.assertIsNotNone(
            authenticate(username="ann.lee@test.school", password="Chosen-By-Admin1")
        )
        self.assertIsNone(
            authenticate(
                username="teacher.edit@test.school", password="Chosen-By-Admin1"
            )
        )

    def test_editing_sends_no_credentials_email(self):
        """A typo fix is not a re-issue; only the reset path mails a password."""
        teacher = self.make_teacher()
        with patch("apps.accounts.services.enqueue_credentials_email") as queued:
            self.edit(teacher)
        queued.assert_not_called()
        self.assertEqual(mail.outbox, [])

    def test_an_email_another_account_holds_is_refused(self):
        self.make_teacher(email="taken@test.school", staff_id="TCH-7002")
        teacher = self.make_teacher()

        with self.assertRaises(ValidationError) as ctx:
            self.edit(teacher, email="taken@test.school")
        self.assertIn("email", ctx.exception.message_dict)

    def test_a_staff_id_another_teacher_holds_is_refused(self):
        other = self.make_teacher(email="other.teacher@test.school", staff_id="TCH-7002")
        teacher = self.make_teacher()

        with self.assertRaises(ValidationError) as ctx:
            self.edit(teacher, staff_id="TCH-7002")
        self.assertIn("staff_id", ctx.exception.message_dict)
        other.teacher_profile.refresh_from_db()
        teacher.teacher_profile.refresh_from_db()
        self.assertEqual(teacher.teacher_profile.staff_id, "TCH-7001")

    def test_a_blank_staff_id_keeps_the_existing_one(self):
        user, _ = create_staff_user(
            role=User.Role.DIRECTOR,
            first_name="Dana",
            last_name="Director",
            email="dana.director@test.school",
            staff_id="DIR-9",
            password="Boardroom-2026",
            actor=self.actor,
            send_credentials=False,
        )
        self.edit(user, staff_id="")
        user.director_profile.refresh_from_db()
        self.assertEqual(user.director_profile.staff_id, "DIR-9")

    def test_editing_a_teacher_does_not_wipe_qualification_or_department(self):
        """
        ``ensure_teacher_profile`` is an update_or_create and would reset these
        two columns to blank, which is why the profile row is edited directly.
        """
        teacher = self.make_teacher()
        TeacherProfile.objects.filter(user=teacher).update(
            qualification="MSc Mathematics", department="Sciences"
        )

        self.edit(teacher)

        profile = TeacherProfile.objects.get(user=teacher)
        self.assertEqual(profile.qualification, "MSc Mathematics")
        self.assertEqual(profile.department, "Sciences")

    def test_a_teacher_missing_their_profile_row_gets_one_generated(self):
        teacher = self.make_teacher()
        TeacherProfile.objects.filter(user=teacher).delete()
        # Re-read so no cached profile instance travels into the service.
        teacher = User.objects.get(pk=teacher.pk)

        self.edit(teacher, staff_id="")

        profile = TeacherProfile.objects.get(user=teacher)
        self.assertEqual(profile.staff_id, f"TCH-{teacher.pk:05d}")

    def test_a_non_staff_role_is_refused(self):
        student = User.objects.create_user(
            username="adm2001",
            email="adm2001@test.school",
            password="Adm2001-1",
            role=User.Role.STUDENT,
        )
        with self.assertRaises(ValidationError) as ctx:
            update_staff_user(
                user=student,
                first_name="Not",
                last_name="Staff",
                email="not.staff@test.school",
            )
        self.assertIn("role", ctx.exception.message_dict)


class StaffEditViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="edit_view_admin",
            email="edit_view_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.teacher = User.objects.create_user(
            username="edit_view_teacher",
            email="edit.view.teacher@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        TeacherProfile.objects.create(
            user=cls.teacher,
            staff_id="TCH-4242",
            qualification="MSc Mathematics",
            department="Sciences",
        )
        cls.finance = User.objects.create_user(
            username="edit_view_finance",
            email="edit.view.finance@test.school",
            password="Testpass1!",
            role=User.Role.FINANCE,
            must_change_password=False,
        )
        FinanceProfile.objects.create(user=cls.finance, staff_id="FIN-4242")

    def edit_url(self, user):
        return reverse("accounts:staff_update", args=[user.pk])

    def post_data(self, **overrides):
        data = {
            "first_name": "Ann",
            "last_name": "Lee",
            "email": "ann.lee@test.school",
            "phone": "0244000009",
            "staff_id": "TCH-0101",
        }
        data.update(overrides)
        return data

    def test_edit_page_prefills_and_offers_no_role_or_password(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.edit_url(self.teacher))

        self.assertEqual(resp.status_code, 200)
        form = resp.context["form"]
        self.assertNotIn("role", form.fields)
        self.assertNotIn("password", form.fields)
        self.assertEqual(form.initial["email"], "edit.view.teacher@test.school")
        self.assertContains(resp, 'value="TCH-4242"', html=False)

    def test_post_updates_and_returns_to_the_detail_page(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.edit_url(self.teacher), self.post_data())

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("accounts:staff_detail", args=[self.teacher.pk]))
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.email, "ann.lee@test.school")
        self.teacher.teacher_profile.refresh_from_db()
        self.assertEqual(self.teacher.teacher_profile.staff_id, "TCH-0101")

    def test_changing_the_email_warns_that_the_old_one_stops_working(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            self.edit_url(self.teacher), self.post_data(), follow=True
        )
        shown = [str(m) for m in resp.context["messages"]]
        self.assertTrue(
            any("now signs in with ann.lee@test.school" in m for m in shown), shown
        )

    def test_saving_the_same_email_does_not_claim_a_login_change(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            self.edit_url(self.teacher),
            self.post_data(email="edit.view.teacher@test.school"),
            follow=True,
        )
        shown = [str(m) for m in resp.context["messages"]]
        self.assertFalse(any("signs in with" in m for m in shown), shown)

    def test_a_duplicate_email_redraws_the_form_with_the_field_error(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            self.edit_url(self.teacher),
            self.post_data(email="edit.view.finance@test.school"),
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("email", resp.context["form"].errors)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.email, "edit.view.teacher@test.school")

    def test_a_staff_id_used_by_another_teacher_redraws_the_form(self):
        """
        ``staff_id`` is unique inside each role's profile table, not across all
        staff — so the refusal has to come from a same-role clash. See
        ``test_a_staff_id_that_clashes_in_another_role_still_passes`` for the
        consequence.
        """
        User.objects.create_user(
            username="edit_view_teacher2",
            email="edit.view.teacher2@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )
        TeacherProfile.objects.create(
            user=User.objects.get(username="edit_view_teacher2"), staff_id="TCH-9999"
        )
        self.client.force_login(self.admin)
        resp = self.client.post(
            self.edit_url(self.teacher), self.post_data(staff_id="TCH-9999")
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("staff_id", resp.context["form"].errors)
        self.teacher.teacher_profile.refresh_from_db()
        self.assertEqual(self.teacher.teacher_profile.staff_id, "TCH-4242")

    def test_a_staff_id_that_clashes_in_another_role_still_passes(self):
        """
        Records the current (pre-existing) scope of staff-ID uniqueness rather
        than asserting it is desirable: AdminProfile, DirectorProfile,
        FinanceProfile and TeacherProfile each hold their own unique column, so
        an ID already used by a finance officer is accepted for a teacher.
        """
        self.client.force_login(self.admin)
        resp = self.client.post(
            self.edit_url(self.teacher), self.post_data(staff_id="FIN-4242")
        )

        self.assertEqual(resp.status_code, 302)
        self.teacher.teacher_profile.refresh_from_db()
        self.assertEqual(self.teacher.teacher_profile.staff_id, "FIN-4242")

    def test_a_missing_required_name_is_rejected_by_the_form(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.edit_url(self.teacher), self.post_data(first_name=""))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("first_name", resp.context["form"].errors)

    def test_posting_a_role_or_password_changes_neither(self):
        self.client.force_login(self.admin)
        self.client.post(
            self.edit_url(self.teacher),
            self.post_data(role=User.Role.FINANCE, password="Something-Else-9"),
        )
        self.teacher.refresh_from_db()

        self.assertEqual(self.teacher.role, User.Role.TEACHER)
        self.assertTrue(self.teacher.check_password("Testpass1!"))

    def test_a_next_from_the_staff_list_is_honoured(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            self.edit_url(self.teacher),
            self.post_data(next="/staff/?role=teacher"),
        )
        self.assertEqual(resp.url, "/staff/?role=teacher")

    def test_a_next_pointing_off_site_is_ignored(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            self.edit_url(self.teacher),
            self.post_data(next="http://evil.example/phish"),
        )
        self.assertEqual(resp.url, reverse("accounts:staff_detail", args=[self.teacher.pk]))

    def test_only_an_admin_reaches_the_edit_page(self):
        for role_user in (self.teacher, self.finance):
            with self.subTest(role=role_user.role):
                self.client.force_login(role_user)
                resp = self.client.get(self.edit_url(self.teacher))
                self.assertEqual(resp.status_code, 403)

    def test_a_student_cannot_be_edited_through_the_staff_route(self):
        student = User.objects.create_user(
            username="edit_view_student",
            email="edit.view.student@test.school",
            password="Testpass1!",
            role=User.Role.STUDENT,
        )
        self.client.force_login(self.admin)
        resp = self.client.get(self.edit_url(student))
        self.assertEqual(resp.status_code, 404)

    def test_detail_page_offers_edit_and_prints_the_staff_id(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("accounts:staff_detail", args=[self.teacher.pk]))

        self.assertContains(resp, self.edit_url(self.teacher), html=False)
        self.assertContains(resp, "TCH-4242")

    def test_staff_list_links_edit_back_to_the_filtered_page(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/staff/?role=teacher")

        self.assertContains(resp, self.edit_url(self.teacher), html=False)
        self.assertContains(resp, "next=/staff/%3Frole%3Dteacher", html=False)


class StaffDeleteServiceTests(TestCase):
    """C4b — the permanent lever on the staff side, and the three guards."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="staff_delete_admin",
            email="staff_delete_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.second_admin, _ = create_staff_user(
            role=User.Role.ADMIN,
            first_name="Ama",
            last_name="Seeds",
            email="second.admin@test.school",
            staff_id="ADM-2",
            send_credentials=False,
        )
        cls.teacher, _ = create_staff_user(
            role=User.Role.TEACHER,
            first_name="Kojo",
            last_name="Mensah",
            email="kojo.delete@test.school",
            staff_id="TCH-9001",
            send_credentials=False,
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
        cls.subject = Subject.objects.create(name="Integrated Science", code="SCI5")
        cls.term = Term.objects.create(
            academic_year=cls.year,
            name=Term.TermName.TERM_1,
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 15),
            is_current=True,
        )
        cls.pupil = create_student_with_guardian(
            admission_number="ADM/2025/070",
            first_name="Efi",
            last_name="Quaye",
            email="efi@staffdelete.school",
            current_class=cls.class5,
            send_credentials=False,
        )

    def invoice_posted_by(self, staff_member):
        structure = FeeStructure.objects.create(
            school_class=self.class5,
            term=self.term,
            version=1,
            is_active=True,
            due_date=datetime.date(2025, 12, 15),
        )
        return Invoice.objects.create(
            student=self.pupil,
            term=self.term,
            fee_structure=structure,
            issued_date=datetime.date(2025, 12, 1),
            due_date=datetime.date(2025, 12, 15),
            total_amount=Decimal("500.00"),
            status=Invoice.Status.POSTED,
            posted_by=staff_member,
        )

    # ── guards ────────────────────────────────────────────────────────────────

    def test_an_account_in_use_cannot_be_deleted(self):
        reasons = staff_delete_refusals(self.teacher, self.admin)
        self.assertEqual(len(reasons), 1)
        self.assertIn("still an active account", reasons[0])
        # The alternative is spelled out, because the refusal is not a dead end.
        self.assertIn("Deactivate it first", reasons[0])

    def test_deactivating_is_what_clears_the_refusal(self):
        deactivate_user(user=self.teacher, actor=self.admin)
        self.assertEqual(staff_delete_refusals(self.teacher, self.admin), [])

    def test_nobody_deletes_their_own_account(self):
        reasons = staff_delete_refusals(self.teacher, self.teacher)
        self.assertIn("You cannot delete your own account.", reasons)

    def test_the_last_active_administrator_survives(self):
        """
        Deleting the only admin would leave no account able to manage staff or
        pupils, so this refusal is about the school, not the row. The account is
        deactivated first so this sentence is the only one standing.
        """
        deactivate_user(user=self.second_admin, actor=self.admin)
        deactivate_user(user=self.teacher, actor=self.admin)
        deactivate_user(user=self.admin, actor=self.teacher)
        reasons = staff_delete_refusals(self.admin, self.teacher)
        self.assertEqual(len(reasons), 1)
        self.assertIn("only active administrator", reasons[0])

        # With a second active admin standing, the same account is deletable.
        reactivate_user(user=self.second_admin, actor=self.second_admin)
        self.assertEqual(
            staff_delete_refusals(self.admin, self.second_admin), []
        )

    def test_a_refusal_raises_and_leaves_no_audit_row(self):
        with self.assertRaises(ValidationError) as ctx:
            delete_staff_user(user=self.teacher, actor=self.admin)
        self.assertIn("still an active account", ctx.exception.messages[0])
        self.assertTrue(User.objects.filter(pk=self.teacher.pk).exists())
        self.assertEqual(
            AuditLog.objects.filter(action__endswith=".deleted").count(), 0
        )

    # ── forecast ──────────────────────────────────────────────────────────────

    def test_the_forecast_names_what_disappears_with_the_account(self):
        TeacherAssignment.objects.create(
            teacher=self.teacher.teacher_profile,
            school_class=self.class5,
            subject=self.subject,
            academic_year=self.year,
        )
        forecast = staff_delete_forecast(self.teacher)
        self.assertEqual(
            forecast["destroyed"],
            {
                "staff profile (staff ID, qualification, department)": 1,
                "class-subject assignment": 1,
            },
        )

    def test_the_forecast_shows_the_books_lose_the_author_not_the_row(self):
        self.invoice_posted_by(self.teacher)
        forecast = staff_delete_forecast(self.teacher)
        self.assertEqual(forecast["blanked"], {"invoice": 1})
        self.assertNotIn("invoice", forecast["destroyed"])

    def test_the_confirm_page_warns_before_the_name_is_lost(self):
        """
        The page has to say in writing what the ``SET_NULL`` columns do, because
        it is the last moment an admin can read it before clicking.
        """
        self.invoice_posted_by(self.teacher)
        deactivate_user(user=self.teacher, actor=self.admin)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("accounts:staff_delete", args=[self.teacher.pk]))
        self.assertContains(resp, "1 invoice")
        self.assertContains(resp, "Kept, but unsigned")
        self.assertContains(resp, "cannot be put back")

    # ── delete ────────────────────────────────────────────────────────────────

    def test_deleting_takes_the_profile_and_leaves_the_ledger_unsigned(self):
        """
        The claim the confirm page makes in writing: ``SET_NULL`` columns keep
        their rows and lose their author, permanently.
        """
        invoice = self.invoice_posted_by(self.teacher)
        deactivate_user(user=self.teacher, actor=self.admin)

        label = delete_staff_user(user=self.teacher, actor=self.admin)
        self.assertEqual(label, "Kojo Mensah (Teacher)")

        self.assertFalse(User.objects.filter(pk=self.teacher.pk).exists())
        self.assertFalse(TeacherProfile.objects.filter(user_id=self.teacher.pk).exists())

        invoice.refresh_from_db()
        self.assertIsNone(invoice.posted_by_id)
        self.assertEqual(invoice.total_amount, Decimal("500.00"))

    def test_the_audit_row_describes_an_account_that_no_longer_exists(self):
        deactivate_user(user=self.teacher, actor=self.admin)
        delete_staff_user(user=self.teacher, actor=self.admin)

        entry = AuditLog.objects.get(action="user.deleted")
        removed = entry.change_summary["removed"]
        self.assertEqual(removed["username"], self.teacher.username)
        self.assertEqual(removed["email"], "kojo.delete@test.school")
        self.assertEqual(removed["role"], User.Role.TEACHER)
        self.assertEqual(removed["staff_id"], "TCH-9001")
        self.assertEqual(
            entry.change_summary["destroyed"],
            {"staff profile (staff ID, qualification, department)": 1},
        )
        self.assertEqual(str(entry.object_id), str(self.teacher.pk))

    def test_the_pupil_a_deleted_teacher_taught_is_untouched(self):
        TeacherAssignment.objects.create(
            teacher=self.teacher.teacher_profile,
            school_class=self.class5,
            subject=self.subject,
            academic_year=self.year,
        )
        deactivate_user(user=self.teacher, actor=self.admin)
        delete_staff_user(user=self.teacher, actor=self.admin)
        self.pupil.refresh_from_db()
        self.assertEqual(self.pupil.admission_number, "ADM/2025/070")
        self.assertTrue(self.pupil.user.is_active)


class StaffDeleteViewTests(TestCase):
    """C4b — the confirm page, and the fact that only an admin ever sees it."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="staff_delete_view_admin",
            email="staff_delete_view_admin@test.school",
            password="Testpass1!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        cls.teacher, _ = create_staff_user(
            role=User.Role.TEACHER,
            first_name="Yaa",
            last_name="Biney",
            email="yaa.delete@test.school",
            staff_id="TCH-9002",
            send_credentials=False,
        )
        cls.director = User.objects.create_user(
            username="staff_delete_view_director",
            email="staff_delete_view_director@test.school",
            password="Testpass1!",
            role=User.Role.DIRECTOR,
            must_change_password=False,
        )
        cls.teacher_client_user = User.objects.create_user(
            username="staff_delete_view_teacher",
            email="staff_delete_view_teacher@test.school",
            password="Testpass1!",
            role=User.Role.TEACHER,
            must_change_password=False,
        )

    def delete_url(self, pk=None):
        return reverse("accounts:staff_delete", args=[pk or self.teacher.pk])

    # ── guards ────────────────────────────────────────────────────────────────

    def test_the_last_active_administrator_survives(self):
        self.client.force_login(self.admin)
        self.assertContains(
            self.client.get(self.delete_url(self.admin.pk)),
            "only active administrator",
        )
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_the_confirm_page_withholds_the_button_until_deactivation(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.delete_url())
        self.assertContains(resp, 'id="delete-blocked"')
        self.assertContains(resp, "Deactivate it first")
        self.assertNotContains(resp, "btn-confirm-staff-delete")

    def test_the_confirm_page_offers_the_button_once_deactivated(self):
        deactivate_user(user=self.teacher, actor=self.admin)
        self.client.force_login(self.admin)
        resp = self.client.get(self.delete_url())
        self.assertContains(resp, "btn-confirm-staff-delete")
        self.assertNotContains(resp, 'id="delete-blocked"')
        # With nothing on record, the page says so rather than showing an empty list.
        self.assertContains(resp, "Nothing they posted is still on record.")

    def test_reading_the_confirm_page_deletes_nothing(self):
        self.client.force_login(self.admin)
        self.client.get(self.delete_url())
        self.assertTrue(User.objects.filter(pk=self.teacher.pk).exists())

    def test_posting_deletes_and_lands_on_the_staff_list(self):
        self.teacher.is_active = False
        self.teacher.save(update_fields=["is_active"])
        self.client.force_login(self.admin)
        resp = self.client.post(self.delete_url())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("accounts:staff_list"))
        self.assertFalse(User.objects.filter(pk=self.teacher.pk).exists())

    def test_a_refused_post_keeps_the_same_account_on_screen(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.delete_url())
        self.assertEqual(resp.status_code, 200)
        shown = " ".join(str(m) for m in resp.context["messages"])
        self.assertIn("still an active account", shown)
        self.assertTrue(User.objects.filter(pk=self.teacher.pk).exists())

    def test_only_an_admin_reaches_the_delete_page(self):
        for other in (self.director, self.teacher_client_user):
            self.client.force_login(other)
            self.assertEqual(self.client.get(self.delete_url()).status_code, 403)
            self.assertEqual(self.client.post(self.delete_url()).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.teacher.pk).exists())

    def test_an_unknown_account_is_404(self):
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(self.delete_url(pk=999999)).status_code, 404
        )
        self.assertEqual(
            self.client.post(self.delete_url(pk=999999)).status_code, 404
        )

    def test_the_detail_page_offers_delete_only_once_deactivated(self):
        self.client.force_login(self.admin)
        detail = reverse("accounts:staff_detail", args=[self.teacher.pk])
        self.assertNotContains(
            self.client.get(detail), f'btn-delete-staff-{self.teacher.pk}'
        )

        self.teacher.is_active = False
        self.teacher.save(update_fields=["is_active"])
        self.assertContains(
            self.client.get(detail), self.delete_url(), html=False
        )
