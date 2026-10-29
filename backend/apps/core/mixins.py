"""
apps/core/mixins.py
-------------------
Reusable CBV mixins for authentication and role-based access control.
All views that require login should use LoginRequiredMixin (Django built-in).
Views that need a specific role use RoleRequiredMixin.
"""

from collections.abc import Sequence

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


class RoleRequiredMixin(LoginRequiredMixin):
    """
    Restrict a view to users with one or more specific roles.

    Usage:
        class MyView(RoleRequiredMixin, View):
            allowed_roles = ["admin", "director"]

    Superuser rule: ``is_superuser`` bypasses a gate only when the account
    carries no role of its own (what ``createsuperuser`` produces, since the
    role field has no default). A superuser who does declare a role is held to
    that role, so tagging an account ``director`` cannot smuggle it into
    admin-only pages.
    """

    allowed_roles: Sequence[str] = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        role = getattr(request.user, "role", "")
        if role in self.allowed_roles:
            return super().dispatch(request, *args, **kwargs)
        if request.user.is_superuser and not role:
            return super().dispatch(request, *args, **kwargs)
        raise PermissionDenied(
            f"Your role '{role}' is not permitted to access this page."
        )


class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = ["admin"]


class DirectorRequiredMixin(RoleRequiredMixin):
    allowed_roles = ["director"]


class TeacherRequiredMixin(RoleRequiredMixin):
    allowed_roles = ["teacher"]


class StudentRequiredMixin(RoleRequiredMixin):
    allowed_roles = ["student"]


class FinanceRequiredMixin(RoleRequiredMixin):
    allowed_roles = ["finance"]


class ParentRequiredMixin(RoleRequiredMixin):
    allowed_roles = ["parent"]


class AdminOrDirectorMixin(RoleRequiredMixin):
    allowed_roles = ["admin", "director"]


class StaffMixin(RoleRequiredMixin):
    """Any staff role (admin, director, teacher, finance)."""

    allowed_roles = ["admin", "director", "teacher", "finance"]


class SchoolReadMixin(RoleRequiredMixin):
    """
    Read-only access to the school-structure pages (academics, students,
    teachers) for the three oversight roles.

    Director and finance officer both browse this data — the director's own
    analytics pages aggregate over classes, students and staff, and finance
    needs to look up students — but only admin may mutate it. Write views in
    those apps keep ``AdminRequiredMixin``; apply this mixin to list/detail
    views only.
    """

    allowed_roles = ["admin", "director", "finance"]
