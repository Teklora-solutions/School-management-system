"""
apps/core/tests/test_navigation_integrity.py
--------------------------------------------
Every sidebar entry in ``NAVIGATION`` must be one a user of that role can
actually open.

The context processor hides broken links instead of surfacing them: an
unresolvable ``url_name`` renders as ``href="#"`` and the page behind it is
never checked. That let three classes of defect ship silently —
  * a nav entry pointing at a URL name that does not exist,
  * a nav entry pointing at a page that rejects the role looking at it (403),
  * a nav entry pointing at a page whose template was never built (500).

These tests walk NAVIGATION per role and assert resolve + reachability.
Entries in KNOWN_BROKEN are exempted with a reason; the list may only shrink.
"""

from __future__ import annotations

import datetime
import sys
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.accounts.models import ParentProfile, User
from apps.core.navigation import NAVIGATION
from apps.students.models import Student, StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile


class _FakeHTML:
    def __init__(self, string=None, base_url=None):
        self.string = string

    def write_pdf(self):
        return b"%PDF-1.4\nstub\n"


#: Reachability is a question about roles, not about this host's PDF libraries,
#: so the engine is stubbed while nav entries are walked.
_engine = patch.dict(sys.modules, {"weasyprint": SimpleNamespace(HTML=_FakeHTML)})

ALL_ROLES = ["admin", "director", "teacher", "student", "finance", "parent"]

# Nav entries whose target is known-broken, keyed by (role, url_name).
# Exempting an entry records a real defect with a ticket to the checkpoint that
# fixes it — never add to this list to make a test pass.
#
# The seven finance exemptions that shipped with the admin-portal checkpoints
# were removed at Module 6 checkpoint F7, when the features/finance/*.html
# layer was built and every exempted page started loading.
KNOWN_BROKEN: dict[tuple[str, str], str] = {}

# url_names that only accept POST — the sidebar should never link to them.
POST_ONLY = {
    "academics:year_set_current",
    "academics:term_set_current",
}


def flatten(items: list[dict], prefix: str = ""):
    """Yield (label, entry) for every nav item that declares a url_name."""
    for item in items:
        label = f"{prefix}{item['label']}" if prefix else item["label"]
        if item.get("url_name"):
            yield label, item
        yield from flatten(item.get("children", []), prefix=f"{label} > ")


class NavigationIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.year = AcademicYear.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 7, 31),
            is_current=True,
        )
        cls.term = Term.objects.create(
            academic_year=cls.year,
            name="Term 1",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 20),
            is_current=True,
        )
        cls.school_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )

        cls.users: dict[str, User] = {}
        for n, role in enumerate(ALL_ROLES, start=1):
            cls.users[role] = User.objects.create_user(
                username=f"nav_{role}",
                email=f"nav_{role}@test.school",
                password="Testpass1!",
                role=role,
                must_change_password=False,
                first_name="Nav",
                last_name=role.title(),
            )

        # A real student profile, so student-only pages resolve a student.
        cls.student = Student.objects.create(
            user=cls.users["student"],
            admission_number="NAV-001",
            current_class=cls.school_class,
        )
        # A real guardian link, so the parent child-switcher has a child.
        parent_profile = ParentProfile.objects.create(user=cls.users["parent"])
        StudentGuardian.objects.create(
            student=cls.student, parent=parent_profile, is_primary=True
        )

        # Teacher teaching load, so "My Classes" has real content to scope by.
        cls.other_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 6", level_order=6
        )
        cls.maths = Subject.objects.create(name="Mathematics", code="MATH")
        TeacherProfile.objects.create(
            user=cls.users["teacher"], staff_id="NAV-T-001"
        )
        cls.school_class.class_teacher = cls.users["teacher"]
        cls.other_class.class_teacher = cls.users["admin"]
        cls.school_class.save(update_fields=["class_teacher"])
        cls.other_class.save(update_fields=["class_teacher"])
        TeacherAssignment.objects.create(
            teacher=cls.users["teacher"].teacher_profile,
            school_class=cls.school_class,
            subject=cls.maths,
            academic_year=cls.year,
        )

    def test_my_classes_shows_only_the_signed_in_teachers_classes(self):
        """
        Regression guard: MyClassesView used to be an admin-only stub that
        returned every staff member. It must now show only this teacher's
        homeroom class and subject assignments.
        """
        self.client.force_login(self.users["teacher"])
        response = self.client.get(reverse("teachers:my_classes"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Grade 5", content)
        self.assertIn("Mathematics", content)
        self.assertNotIn("Grade 6", content)  # another teacher's homeroom
        # Must not list staff names — the old stub rendered the staff roster.
        self.assertNotIn("Nav Finance", content)

    def _resolve(self, entry: dict) -> str | None:
        try:
            return reverse(
                entry["url_name"],
                args=entry.get("url_args", []),
                kwargs=entry.get("url_kwargs", {}),
            )
        except NoReverseMatch:
            return None

    def test_every_nav_entry_resolves(self):
        """No sidebar entry may point at a URL name that does not exist."""
        failures = []
        for role in ALL_ROLES:
            for label, entry in flatten(NAVIGATION[role]):
                if self._resolve(entry) is None:
                    failures.append(f"  {role} → {label!r}: {entry['url_name']} does not resolve")
        if failures:
            self.fail(
                "Sidebar entries point at non-existent URLs:\n" + "\n".join(failures)
            )

    def test_no_post_only_pages_are_linked_in_the_sidebar(self):
        failures = []
        for role in ALL_ROLES:
            for label, entry in flatten(NAVIGATION[role]):
                if entry["url_name"] in POST_ONLY:
                    failures.append(f"  {role} → {label!r}: {entry['url_name']} is POST-only")
        if failures:
            self.fail("Sidebar links a POST-only endpoint:\n" + "\n".join(failures))

    def test_every_nav_entry_is_reachable_by_its_role(self):
        """A nav entry must load for the role that sees it in their sidebar."""
        failures = []
        for role in ALL_ROLES:
            self.client.force_login(self.users[role])
            for label, entry in flatten(NAVIGATION[role]):
                exemption = KNOWN_BROKEN.get((role, entry["url_name"]))
                if exemption:
                    continue
                path = self._resolve(entry)
                if path is None:
                    continue  # reported by test_every_nav_entry_resolves
                with _engine:
                    status = self.client.get(path).status_code
                # 405 = the target legitimately rejects GET; the link is still
                # honest as long as we did not get 403 or a server error.
                if status >= 400:
                    failures.append(
                        f"  {role} → {label!r} ({entry['url_name']}): "
                        f"GET {path} returned {status}"
                    )
        if failures:
            self.fail(
                "Sidebar entries lead to pages their own role cannot open:\n"
                + "\n".join(failures)
            )

    def test_known_broken_exemptions_are_all_still_broken(self):
        """
        Guards the exemption list from rotting: if a page is fixed, its entry
        must be deleted from KNOWN_BROKEN so it starts being enforced again.
        """
        stale = []
        for (role, url_name), reason in KNOWN_BROKEN.items():
            self.client.force_login(self.users[role])
            try:
                status = self.client.get(reverse(url_name)).status_code
            except Exception:  # noqa: BLE001 — a 500 here means "still broken"
                status = 500
            if status < 400:
                stale.append(f"  ({role}, {url_name}) now loads — remove it ({reason})")
        if stale:
            self.fail(
                "KNOWN_BROKEN contains entries that are no longer broken:\n"
                + "\n".join(stale)
            )
