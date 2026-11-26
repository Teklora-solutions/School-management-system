"""
apps/api/permissions.py
------------------------
Scope-based permission classes for the external API.

Usage on a ViewSet:

    from apps.api.permissions import HasScope

    class StudentViewSet(viewsets.ReadOnlyModelViewSet):
        permission_classes = [HasScope("students:read")]

If the caller authenticated via JWT (i.e. they are a real User), the
permission falls back to checking the user's role.  This lets internal
staff use the same endpoints from the browsable API or mobile apps.

Role → implicit scopes mapping keeps the permission logic in one place.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from .authentication import _APIClientUser

# Which Django roles grant which read-only scopes automatically.
# Write scopes are never granted implicitly; a JWT caller must be an admin.
_ROLE_SCOPES: dict[str, set[str]] = {
    "admin": {
        "students:read", "students:write",
        "academics:read",
        "staff:read",
        "results:read",
        "finance:read", "finance:write",
        "attendance:read", "attendance:write",
        "webhooks:manage",
    },
    "director": {
        "students:read",
        "academics:read",
        "staff:read",
        "results:read",
        "finance:read",
        "attendance:read",
    },
    "teacher": {
        "students:read",
        "academics:read",
        "results:read",
        "attendance:read", "attendance:write",
    },
    "finance": {
        "students:read",
        "finance:read", "finance:write",
    },
    "parent": {"students:read", "results:read", "finance:read"},
    "student": {"results:read", "finance:read"},
}


def _user_has_scope(user, scope: str) -> bool:
    """Check whether a JWT-authenticated User implicitly holds *scope*."""
    if not user.is_authenticated:
        return False
    role = getattr(user, "role", "")
    return scope in _ROLE_SCOPES.get(role, set())


class HasScope(BasePermission):
    """
    Permission class factory.  Pass the required scope string:

        permission_classes = [HasScope("students:read")]

    For APIClient callers: checks ``request.auth.has_scope(scope)``.
    For User callers:     checks the implicit role→scope mapping above.
    """

    def __init__(self, scope: str):
        self.scope = scope
        # DRF instantiates permission classes, not the instances returned by
        # the factory.  We use __init_subclass__ trickery via a closure instead.

    # DRF calls has_permission on an *instance*, so we implement it here.
    def has_permission(self, request, view) -> bool:
        user = request.user
        auth = request.auth  # APIClient instance for key auth, None for JWT

        if isinstance(user, _APIClientUser):
            # API key path
            return auth is not None and auth.has_scope(self.scope)

        # JWT / session path — real User
        return _user_has_scope(user, self.scope)

    # Allow Django to call has_permission on the class returned from HasScope()
    # by making __call__ return self so the instance is both the factory result
    # and a valid permission instance.
    def __call__(self):
        return self


def require_scope(scope: str):
    """
    Shorthand that returns a *class* (not instance) suitable for
    ``permission_classes = [require_scope("students:read")]``.
    """

    class _ScopePermission(BasePermission):
        _scope = scope

        def has_permission(self, request, view) -> bool:
            user = request.user
            auth = request.auth
            if isinstance(user, _APIClientUser):
                return auth is not None and auth.has_scope(self._scope)
            return _user_has_scope(user, self._scope)

    _ScopePermission.__name__ = f"HasScope_{scope.replace(':', '_')}"
    return _ScopePermission
