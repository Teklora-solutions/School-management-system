"""
backend/tests/e2e/test_role_matrix.py
--------------------------------------
E2E-5: generate the URL x role matrix from the URLConf and assert that no
authenticated role can reach a role-gated view outside its declared roles.

Views that do object-level authorization (threads, statements, report cards)
are covered by dedicated IDOR tests instead.
"""

from __future__ import annotations

import uuid

from apps.dashboards.tests.fixtures import make_user
from django.test import TestCase
from django.urls import get_resolver, reverse

ROLES = ["admin", "director", "teacher", "student", "parent", "finance"]


def _walk(patterns, namespace=""):
    for pattern in patterns:
        if hasattr(pattern, "url_patterns"):
            ns = pattern.namespace
            child_ns = f"{namespace}:{ns}" if namespace and ns else (ns or namespace)
            yield from _walk(pattern.url_patterns, child_ns)
        elif pattern.name:
            yield namespace, pattern


def _sample_value(converter) -> object:
    name = type(converter).__name__.lower()
    if "int" in name:
        return 1
    if "uuid" in name:
        return uuid.uuid4()
    return "test"


def _reverse_args(pattern) -> list:
    converters = getattr(pattern.pattern, "converters", {}) or {}
    return [_sample_value(converter) for converter in converters.values()]


class RoleUrlMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {role: make_user(role, username=f"matrix_{role}") for role in ROLES}

    def test_role_gated_urls_reject_every_other_role(self):
        # Dummy primary keys may 404/500 inside allowed views; the security
        # assertion only concerns whether disallowed roles are rejected.
        self.client.raise_request_exception = False
        checked = 0
        violations = []
        under_permission = []

        for namespace, pattern in _walk(get_resolver().url_patterns):
            view_class = getattr(getattr(pattern, "callback", None), "view_class", None)
            allowed = list(getattr(view_class, "allowed_roles", []) or [])
            if not allowed:
                continue

            url_name = f"{namespace}:{pattern.name}" if namespace else pattern.name
            args = _reverse_args(pattern)
            try:
                url = reverse(url_name, args=args)
            except Exception:  # noqa: BLE001 — unsupported URL signatures just skip
                url = None
            if url is None:
                continue

            for role in ROLES:
                self.client.force_login(self.users[role])
                response = self.client.get(url)
                checked += 1
                if role in allowed:
                    if response.status_code in (401, 403):
                        under_permission.append(
                            (url_name, role, response.status_code, allowed)
                        )
                else:
                    if response.status_code != 403:
                        violations.append(
                            (url_name, role, response.status_code, allowed)
                        )

        self.assertEqual(
            violations,
            [],
            "Roles reached URLs they are not permitted to use:\n"
            + "\n".join(
                f"  {name}: {role} -> {status} (allowed: {allowed})"
                for name, role, status, allowed in violations
            ),
        )
        self.assertGreater(checked, 300)
        # Under-permission is not a security hole; report it for the audit.
        if under_permission:
            print(
                "\nUnder-permission findings (declared role was blocked):\n"
                + "\n".join(
                    f"  {name}: {role} -> {status} (allowed: {allowed})"
                    for name, role, status, allowed in under_permission
                )
            )
