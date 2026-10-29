"""
apps/accounts/models.py
-----------------------
Custom User model + role-specific profile models.

Design decisions:
- Single User table with a `role` field keeps authentication simple.
- Profile models (one per role) store role-specific attributes and avoid
  bloating the User table.
- ParentProfile has a M2M to Student via a through table so one parent
  can be linked to many children.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.core.models import BaseModel


class User(AbstractUser):
    """
    Custom user model.  The built-in username field is kept (used for login),
    but email is also unique so either can be used for password reset.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        DIRECTOR = "director", "Director"
        TEACHER = "teacher", "Teacher"
        STUDENT = "student", "Student"
        FINANCE = "finance", "Finance Officer"
        PARENT = "parent", "Parent / Guardian"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        db_index=True,
    )
    email = models.EmailField(unique=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True)
    must_change_password = models.BooleanField(
        default=True,
        help_text="Force password change on next login (set when admin creates the account).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    REQUIRED_FIELDS = ["email", "role"]

    class Meta:
        db_table = "accounts_user"
        ordering = ["last_name", "first_name"]

    def __str__(self) -> str:
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    @property
    def full_name(self) -> str:
        return self.get_full_name() or self.username

    def get_dashboard_url(self) -> str:
        """Return the role's home URL name."""
        from django.urls import reverse

        mapping = {
            "admin": "dashboards:admin_home",
            "director": "dashboards:director_home",
            "teacher": "dashboards:teacher_home",
            "student": "dashboards:student_home",
            "finance": "dashboards:finance_home",
            "parent": "dashboards:parent_home",
        }
        # A role-less superuser has no dashboard of its own. The public site is
        # the fallback rather than ``accounts:dashboard_redirect``: that stub
        # redirects here, so using it would bounce the account in a loop.
        return reverse(mapping.get(self.role, "public:home"))


# ─── Profile models ───────────────────────────────────────────────────────────


class AdminProfile(BaseModel):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="admin_profile"
    )
    staff_id = models.CharField(max_length=30, unique=True, blank=True)

    class Meta:
        db_table = "accounts_admin_profile"

    def __str__(self) -> str:
        return f"Admin: {self.user.full_name}"


class DirectorProfile(BaseModel):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="director_profile"
    )
    staff_id = models.CharField(max_length=30, unique=True, blank=True)

    class Meta:
        db_table = "accounts_director_profile"

    def __str__(self) -> str:
        return f"Director: {self.user.full_name}"


class FinanceProfile(BaseModel):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="finance_profile"
    )
    staff_id = models.CharField(max_length=30, unique=True, blank=True)

    class Meta:
        db_table = "accounts_finance_profile"

    def __str__(self) -> str:
        return f"Finance Officer: {self.user.full_name}"


class ParentProfile(BaseModel):
    """
    Parent/Guardian profile.  The link to students is managed via
    apps.students.models.StudentGuardian (Phase 2).
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="parent_profile"
    )
    occupation = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    national_id = models.CharField(max_length=50, blank=True)

    class Meta:
        db_table = "accounts_parent_profile"

    def __str__(self) -> str:
        return f"Parent: {self.user.full_name}"
