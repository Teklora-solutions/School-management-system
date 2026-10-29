"""
tests/test_password_reset_policy.py
-------------------------------------
Proves the password-reset access policy:

  1. Staff roles (teacher, director, finance, admin) CANNOT self-reset via
     the /accounts/password_reset/ form — no reset email is ever sent.

  2. Students and parents CAN self-reset — a reset email IS sent.

  3. Admin CAN reset any staff member's password via the staff management
     page at /accounts/staff/<pk>/reset-password/.

  4. Non-admin roles are blocked from the admin reset endpoint.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

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
# 3. Admin can reset staff passwords via the staff management page
# ---------------------------------------------------------------------------


class AdminStaffResetTest(TestCase):
    """
    The admin can reset any staff member's password through the staff detail
    page.  This is the ONLY legitimate path for staff password resets.
    """

    def setUp(self):
        self.admin = _make_user(User.Role.ADMIN, 0)
        self.teacher = _make_user(User.Role.TEACHER, 1)
        self.client.force_login(self.admin)

    def test_admin_can_reset_teacher_password(self):
        response = self.client.post(
            reverse("accounts:staff_reset_password", kwargs={"pk": self.teacher.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        # The teacher's password hash must have changed
        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.check_password("OldPass123!"))
        self.assertTrue(
            self.teacher.must_change_password,
            "Teacher must be flagged for password change on next login",
        )

    def test_admin_reset_sets_must_change_password(self):
        self.client.post(
            reverse("accounts:staff_reset_password", kwargs={"pk": self.teacher.pk}),
        )
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.must_change_password)


# ---------------------------------------------------------------------------
# 4. Non-admin roles are blocked from the admin reset endpoint
# ---------------------------------------------------------------------------


class NonAdminBlockedFromStaffResetTest(TestCase):
    """
    Only admin can reset staff passwords.  All other roles must be rejected
    (403 or redirect) when they POST to the staff reset endpoint.
    """

    def setUp(self):
        self.teacher = _make_user(User.Role.TEACHER, 1)
        self.target = _make_user(User.Role.FINANCE, 2)

    def _try_reset_as(self, role: str):
        user = _make_user(role, 99)
        self.client.force_login(user)
        return self.client.post(
            reverse("accounts:staff_reset_password", kwargs={"pk": self.target.pk}),
        )

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

    def test_unauthenticated_blocked(self):
        self.client.logout()
        response = self.client.post(
            reverse("accounts:staff_reset_password", kwargs={"pk": self.target.pk}),
        )
        self.assertIn(response.status_code, (302, 301))
