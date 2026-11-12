"""
tests/test_password_reset_policy.py
-------------------------------------
Proves the password-reset access policy:

  1. Staff roles (teacher, director, finance, admin) CANNOT self-reset via
     the /accounts/password_reset/ form — no reset email is ever sent.

  2. Students and parents CAN self-reset — a reset email IS sent.

  3. Admin sets any staff member's password by typing it on the staff page at
     /accounts/staff/<pk>/reset-password/ — nothing is generated or emailed.

  4. Non-admin roles are blocked from the admin reset endpoint.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import AuditLog

User = get_user_model()

# Roles that must be blocked from self-service password reset.
STAFF_ROLES = [
    User.Role.TEACHER,
    User.Role.DIRECTOR,
    User.Role.FINANCE,
    User.Role.ADMIN,
]

# Roles that are allowed to self-service password reset.
NON_STAFF_ROLES = [
    User.Role.STUDENT,
    User.Role.PARENT,
]


def _make_user(role: str, n: int) -> User:
    """Create a user with the given role and a known password."""
    username = f"pwreset_{role}_{n}"
    return User.objects.create_user(
        username=username,
        email=f"{username}@test.school",
        password="OldPass123!",
        role=role,
        must_change_password=False,
        first_name="Test",
        last_name=role.title(),
    )


# ---------------------------------------------------------------------------
# 1. Staff roles are blocked from self-service password reset
# ---------------------------------------------------------------------------


class StaffSelfResetBlockedTest(TestCase):
    """
    For every staff role, submitting the password_reset form with their email
    must NOT send a reset email.
    """

    def setUp(self):
        self.users = {
            role: _make_user(role, i) for i, role in enumerate(STAFF_ROLES, 1)
        }

    def _post_reset(self, email: str):
        # Django's PasswordResetView requires a CSRF token; the test client
        # handles CSRF enforcement automatically.
        return self.client.post(
            reverse("password_reset"),
            data={"email": email},
            follow=True,
        )

    def test_teacher_cannot_self_reset(self):
        mail.outbox = []
        self._post_reset(self.users[User.Role.TEACHER].email)
        self.assertEqual(
            len(mail.outbox), 0, "Teacher should NOT receive a password reset email"
        )

    def test_director_cannot_self_reset(self):
        mail.outbox = []
        self._post_reset(self.users[User.Role.DIRECTOR].email)
        self.assertEqual(
            len(mail.outbox), 0, "Director should NOT receive a password reset email"
        )

    def test_finance_cannot_self_reset(self):
        mail.outbox = []
        self._post_reset(self.users[User.Role.FINANCE].email)
        self.assertEqual(
            len(mail.outbox),
            0,
            "Finance officer should NOT receive a password reset email",
        )

    def test_admin_cannot_self_reset(self):
        mail.outbox = []
        self._post_reset(self.users[User.Role.ADMIN].email)
        self.assertEqual(
            len(mail.outbox), 0, "Admin should NOT receive a self-service reset email"
        )

    def test_staff_reset_redirects_to_done_page(self):
        """
        Staff reset requests must still redirect to the 'done' page so the
        form does not leak whether the email belongs to a staff account.
        """
        response = self._post_reset(self.users[User.Role.TEACHER].email)
        self.assertEqual(response.status_code, 200)
        # The done page renders the password_reset_done template
        self.assertTemplateUsed(response, "registration/password_reset_done.html")


# ---------------------------------------------------------------------------
# 2. Non-staff roles (student, parent) CAN self-service reset
# ---------------------------------------------------------------------------


class NonStaffSelfResetAllowedTest(TestCase):
    """
    Students and parents must still be able to use the self-service password
    reset flow — a reset email IS sent.
    """

    def setUp(self):
        self.users = {
            role: _make_user(role, i) for i, role in enumerate(NON_STAFF_ROLES, 1)
        }

    def _post_reset(self, email: str):
        return self.client.post(
            reverse("password_reset"),
            data={"email": email},
            follow=True,
        )

    def test_student_can_self_reset(self):
        mail.outbox = []
        self._post_reset(self.users[User.Role.STUDENT].email)
        self.assertEqual(
            len(mail.outbox), 1, "Student should receive a password reset email"
        )
        self.assertIn("reset", mail.outbox[0].subject.lower())

    def test_parent_can_self_reset(self):
        mail.outbox = []
        self._post_reset(self.users[User.Role.PARENT].email)
        self.assertEqual(
            len(mail.outbox), 1, "Parent should receive a password reset email"
        )

    def test_nonexistent_email_sends_nothing(self):
        """A non-existent email must not reveal account existence."""
        mail.outbox = []
        self._post_reset("nobody@nowhere.test")
        self.assertEqual(len(mail.outbox), 0)


# ---------------------------------------------------------------------------
# 3. Admin sets staff passwords by typing them on the staff page
# ---------------------------------------------------------------------------


class AdminStaffResetTest(TestCase):
    """
    The admin chooses the new password and hands it over. Nothing is generated,
    shown back or emailed — staff cannot self-reset (see part 1).
    """

    def setUp(self):
        self.admin = _make_user(User.Role.ADMIN, 0)
        self.teacher = _make_user(User.Role.TEACHER, 1)
        self.client.force_login(self.admin)

    def _url(self):
        return reverse(
            "accounts:staff_reset_password", kwargs={"pk": self.teacher.pk}
        )

    def _payload(self, password="ChosenPass123!", confirm=None, force=True):
        data = {"password": password, "password_confirm": confirm or password}
        if force:
            data["must_change_password"] = "on"
        return data

    def test_the_form_opens_with_two_password_fields(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'name="password_confirm"')

    def test_admin_chosen_password_is_the_one_stored(self):
        response = self.client.post(self._url(), self._payload(), follow=True)
        self.assertEqual(response.status_code, 200)

        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.check_password("ChosenPass123!"))
        self.assertFalse(self.teacher.check_password("OldPass123!"))
        self.assertTrue(
            self.teacher.must_change_password,
            "Teacher must be flagged for password change on next login",
        )
        self.assertEqual(len(mail.outbox), 0, "a chosen password is never emailed")

    def test_admin_can_leave_the_forced_change_off(self):
        self.client.post(self._url(), self._payload(force=False))
        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.must_change_password)
        self.assertTrue(self.teacher.check_password("ChosenPass123!"))

    def test_mismatch_is_refused_and_the_password_unchanged(self):
        response = self.client.post(
            self._url(), self._payload(confirm="Different123!")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "did not match")
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.check_password("OldPass123!"))

    @override_settings(
        AUTH_PASSWORD_VALIDATORS=[
            {
                "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
                "OPTIONS": {"min_length": 12},
            }
        ]
    )
    def test_a_hand_typed_password_is_validated(self):
        response = self.client.post(self._url(), self._payload(password="short"))
        self.assertEqual(response.status_code, 200)
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.check_password("OldPass123!"))

    def test_the_edit_page_links_to_the_password_form(self):
        response = self.client.get(
            reverse("accounts:staff_update", kwargs={"pk": self.teacher.pk})
        )
        self.assertContains(response, self._url())

    def test_the_reset_is_audited_without_the_password(self):
        self.client.post(self._url(), self._payload())
        entry = AuditLog.objects.get(
            action="user.password_reset", object_id=str(self.teacher.pk)
        )
        self.assertEqual(entry.change_summary, {"email": self.teacher.email})

    def test_the_return_url_is_honoured(self):
        edit = reverse("accounts:staff_update", kwargs={"pk": self.teacher.pk})
        response = self.client.post(self._url(), dict(self._payload(), next=edit))
        self.assertRedirects(response, edit)

    def test_an_external_return_url_is_ignored(self):
        response = self.client.post(
            self._url(), dict(self._payload(), next="https://evil.example.com/")
        )
        self.assertRedirects(
            response,
            reverse("accounts:staff_detail", kwargs={"pk": self.teacher.pk}),
        )


# ---------------------------------------------------------------------------
# 4. Non-admin roles are blocked from the admin password page
# ---------------------------------------------------------------------------


class NonAdminBlockedFromStaffResetTest(TestCase):
    """
    Only admin can set staff passwords.  All other roles must be rejected
    (403 or redirect) and the target's password must be untouched.
    """

    def setUp(self):
        self.teacher = _make_user(User.Role.TEACHER, 1)
        self.target = _make_user(User.Role.FINANCE, 2)

    def _payload(self):
        return {
            "password": "Hijacked123!",
            "password_confirm": "Hijacked123!",
            "must_change_password": "on",
        }

    def _try_reset_as(self, role: str):
        user = _make_user(role, 99)
        self.client.force_login(user)
        response = self.client.post(
            reverse("accounts:staff_reset_password", kwargs={"pk": self.target.pk}),
            self._payload(),
        )
        self.target.refresh_from_db()
        self.assertTrue(
            self.target.check_password("OldPass123!"),
            f"{role} must not be able to set a staff password",
        )
        return response

    def test_teacher_blocked(self):
        response = self._try_reset_as(User.Role.TEACHER)
        self.assertIn(response.status_code, (403, 302))

    def test_finance_blocked(self):
        response = self._try_reset_as(User.Role.FINANCE)
        self.assertIn(response.status_code, (403, 302))

    def test_director_blocked(self):
        response = self._try_reset_as(User.Role.DIRECTOR)
        self.assertIn(response.status_code, (403, 302))

    def test_student_blocked(self):
        response = self._try_reset_as(User.Role.STUDENT)
        self.assertIn(response.status_code, (403, 302))

    def test_parent_blocked(self):
        response = self._try_reset_as(User.Role.PARENT)
        self.assertIn(response.status_code, (403, 302))

    def test_teacher_cannot_even_open_the_form(self):
        self.client.force_login(self.teacher)
        response = self.client.get(
            reverse("accounts:staff_reset_password", kwargs={"pk": self.target.pk})
        )
        self.assertIn(response.status_code, (403, 302))

    def test_unauthenticated_blocked(self):
        self.client.logout()
        response = self.client.post(
            reverse("accounts:staff_reset_password", kwargs={"pk": self.target.pk}),
            self._payload(),
        )
        self.assertIn(response.status_code, (302, 301))
