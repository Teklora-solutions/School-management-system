"""
apps/core/tests/test_security_matrix.py
-----------------------------------------
URL × Role permission matrix tests for Module 1 (Admin Dashboard).

Verifies that every admin URL created for Module 1 is reachable only by the
admin role and rejects every other authenticated role with 403 (or a redirect
for unauthenticated users). POST-only endpoints are included as
"allowed_roles = set()": non-admins are rejected by the mixin before the
method check, while the admin receives 405 for a GET — the test tolerates that.

The same file also keeps the role dashboards and communications entry points
covered so regressions in the existing navigation surface are caught.
"""

from __future__ import annotations

import datetime
import sys
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.academics.models import AcademicYear, ClassSubject, SchoolClass, Subject, Term
from apps.accounts.models import ParentProfile, User
from apps.public.models import ContactMessage, GalleryPhoto
from apps.results.models import ResultSheet
from apps.students.models import Student, StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile


class _FakeHTML:
    def __init__(self, string=None, base_url=None):
        self.string = string

    def write_pdf(self):
        return b"%PDF-1.4\nstub\n"


#: This matrix asks who may reach a URL, so the PDF screens must not be decided
#: by whether this host happens to have WeasyPrint's native libraries.
_engine = patch.dict(sys.modules, {"weasyprint": SimpleNamespace(HTML=_FakeHTML)})


def _make_user(role: str, n: int, *, must_change: bool = False) -> User:
    username = f"sec_matrix_{role}_{n}"
    return User.objects.create_user(
        username=username,
        email=f"{username}@test.school",
        password="Testpass1!",
        role=role,
        must_change_password=must_change,
        first_name="Test",
        last_name=role.title(),
    )


# (url_name, kwargs, allowed_roles, note)
# allowed_roles: roles that must NOT receive an error response.
# POST-only admin endpoints use an empty set — admin gets 405 (tolerated).
URL_ROLE_MATRIX = [
    # ── Role dashboards ──────────────────────────────────────────────────
    ("dashboards:admin_home", {}, {"admin"}, "admin dashboard"),
    ("dashboards:director_home", {}, {"director"}, ""),
    ("dashboards:director_performance", {}, {"director"}, ""),
    ("dashboards:director_fees", {}, {"director"}, ""),
    ("dashboards:director_staff", {}, {"director"}, ""),
    ("dashboards:director_books", {}, {"director"}, ""),
    ("dashboards:director_books_ledger", {}, {"director"}, ""),
    ("dashboards:director_books_trial_balance", {}, {"director"}, ""),
    ("dashboards:finance_home", {}, {"finance"}, ""),
    ("dashboards:student_home", {}, {"student"}, ""),
    ("dashboards:parent_home", {}, {"parent"}, ""),
    ("dashboards:teacher_home", {}, {"teacher"}, ""),
    # ── Accounts: profile + staff management ─────────────────────────────
    (
        "accounts:profile",
        {},
        {"admin", "director", "teacher", "student", "parent", "finance"},
        "",
    ),
    ("accounts:staff_list", {}, {"admin"}, "Module 1"),
    ("accounts:staff_create", {}, {"admin"}, "Module 1"),
    ("accounts:staff_detail", {"pk": "__staff__"}, {"admin"}, "Module 1"),
    # ── Academics structure ──────────────────────────────────────────────
    ("academics:year_list", {}, {"admin", "director", "finance"}, "read-only"),
    ("academics:year_create", {}, {"admin"}, "Module 1"),
    ("academics:year_detail", {"pk": "__year__"}, {"admin"}, "Module 1"),
    ("academics:class_list", {}, {"admin", "director", "finance"}, "read-only"),
    ("academics:class_create", {}, {"admin"}, "Module 1"),
    ("academics:class_detail", {"pk": "__class__"}, {"admin"}, "Module 1"),
    ("academics:subject_list", {}, {"admin", "director", "finance"}, "read-only"),
    ("academics:subject_create", {}, {"admin"}, "Module 1"),
    ("academics:class_subject_list", {}, {"admin", "director", "finance"}, "read-only"),
    ("academics:class_subject_create", {}, {"admin"}, "Module 1"),
    # ── Teachers / assignment matrix ─────────────────────────────────────
    ("teachers:list", {}, {"admin", "director", "finance"}, "read-only"),
    ("teachers:detail", {"pk": "__teacher_profile__"}, {"admin"}, "Module 1"),
    ("teachers:assignment_list", {}, {"admin"}, "Module 1"),
    ("teachers:assignment_create", {}, {"admin"}, "Module 1"),
    ("teachers:assignment_matrix", {}, {"admin"}, "Module 1"),
    ("teachers:my_classes", {}, {"teacher"}, "own classes only"),
    ("teachers:marks_list", {}, {"teacher"}, "own assignments only"),
    (
        "teachers:marks_entry",
        {"assignment_id": "__assignment__"},
        {"teacher"},
        "own assignments only",
    ),
    # ── Students: CRUD, PDF, bulk import, promotion ──────────────────────
    ("students:list", {}, {"admin", "director", "finance"}, "read-only"),
    ("students:add", {}, {"admin"}, "Module 1"),
    ("students:detail", {"pk": "__student__"}, {"admin"}, "Module 1"),
    ("students:pdf", {"pk": "__student__"}, {"admin"}, "Module 1"),
    ("students:blank_pdf", {}, {"admin"}, "Module 1"),
    ("students:bulk_import", {}, {"admin"}, "Module 1"),
    ("students:bulk_import_template", {}, {"admin"}, "Module 1"),
    ("students:promote", {}, {"admin"}, "Module 1"),
    ("students:promotion_history", {}, {"admin"}, "Module 1"),
    # ── Results governance + grading ─────────────────────────────────────
    ("results:student_results", {}, {"student", "parent"}, "student/parent only"),
    ("results:broadsheet", {}, {"admin"}, "R1 class broadsheet"),
    ("results:sheet_list", {}, {"admin"}, "Module 1"),
    ("results:sheet_detail", {"pk": "__sheet__"}, {"admin"}, "Module 1"),
    ("results:grading_list", {}, {"admin"}, "Module 1"),
    ("results:grading_config", {"year_id": "__year__"}, {"admin"}, "Module 1"),
    ("results:grade_scale", {"year_id": "__year__"}, {"admin"}, "G2 grading system"),
    # ── Audit log ────────────────────────────────────────────────────────
    ("core:audit_log", {}, {"admin"}, "Module 1"),
    ("core_activity:feed", {}, {"admin"}, "Activity feed (Module 3 S0a)"),
    ("core_activity:daily_report", {}, {"admin"}, "Daily report (Module 3 S0a)"),
    ("core_school:settings", {}, {"admin"}, "School Settings"),
    # ── Communications ───────────────────────────────────────────────────
    (
        "communications:list",
        {},
        {"admin", "director", "teacher", "student", "parent", "finance"},
        "",
    ),
    ("communications:announcement_create", {}, {"admin", "director"}, "Module 1"),
    ("communications:inbox", {}, {"admin", "director"}, ""),
    # ── POST-only admin endpoints (GET → 405 for admin, 403 for others) ──
    ("accounts:staff_deactivate", {"pk": "__staff__"}, set(), "POST only"),
    ("accounts:staff_reactivate", {"pk": "__staff__"}, set(), "POST only"),
    ("accounts:staff_reset_password", {"pk": "__staff__"}, set(), "POST only"),
    ("academics:year_set_current", {"pk": "__year__"}, set(), "POST only"),
    ("academics:year_delete", {"pk": "__year__"}, set(), "POST only"),
    ("academics:term_set_current", {"pk": "__term__"}, set(), "POST only"),
    ("academics:term_update", {"pk": "__term__"}, set(), "POST only"),
    ("academics:class_subject_remove", {"pk": "__mapping__"}, set(), "POST only"),
    ("teachers:assignment_delete", {"pk": "__assignment__"}, set(), "POST only"),
    ("teachers:set_class_teacher", {"pk": "__class__"}, set(), "POST only"),
    ("results:sheet_confirm", {"pk": "__sheet__"}, set(), "POST only"),
    ("results:sheet_publish", {"pk": "__sheet__"}, set(), "POST only"),
    ("results:sheet_reopen", {"pk": "__sheet__"}, set(), "POST only"),
    ("results:publish_class_term", {}, set(), "POST only"),
    (
        "results:grade_scale_delete",
        {"year_id": "__year__"},
        set(),
        "POST only",
    ),
    # ── Health probes (unauthenticated) ──────────────────────────────────
    ("liveness", {}, {"__anon__"}, ""),
    ("readiness", {}, {"__anon__"}, ""),
    # ── The school's own website ─────────────────────────────────────────
    # These five are open to anyone on purpose: a family looking for a school
    # has not got an account yet. Recorded here so a future "harden everything"
    # pass that puts a role mixin on a public view fails loudly.
    ("public:home", {}, {"__anon__"}, "public site"),
    ("public:about", {}, {"__anon__"}, "public site"),
    ("public:academics", {}, {"__anon__"}, "public site (/programme/)"),
    ("public:gallery", {}, {"__anon__"}, "public site"),
    ("public:contact", {}, {"__anon__"}, "public site"),
    # ── The school's own Website pages ───────────────────────────────────
    # The mirror image of the five rows above: same two tables, but publishing
    # a photograph and reading what a stranger sent the school are both acts of
    # editing what the public sees, so only admin may do them.
    ("public_admin:gallery_list", {}, {"admin"}, "website gallery"),
    ("public_admin:gallery_add", {}, {"admin"}, "website gallery"),
    ("public_admin:gallery_edit", {"pk": "__photo__"}, {"admin"}, "website gallery"),
    ("public_admin:gallery_publish", {"pk": "__photo__"}, set(), "POST only"),
    ("public_admin:gallery_delete", {"pk": "__photo__"}, set(), "POST only"),
    ("public_admin:enquiry_list", {}, {"admin"}, "enquiry inbox"),
    ("public_admin:enquiry_detail", {"pk": "__enquiry__"}, {"admin"}, "enquiry inbox"),
    ("public_admin:enquiry_handled", {"pk": "__enquiry__"}, set(), "POST only"),
    ("public_admin:enquiry_delete", {"pk": "__enquiry__"}, set(), "POST only"),
]

ALL_ROLES = {"admin", "director", "teacher", "student", "parent", "finance"}
DISALLOWED_STATUS = {403, 302, 301}


class MatrixFixtures:
    """
    Shared fixtures + helpers for the permission tests in this module.

    Deliberately NOT a TestCase so subclasses don't inherit each other's tests.
    """
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
            name="term_1",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2025, 12, 20),
            is_current=True,
        )
        cls.school_class = SchoolClass.objects.create(
            academic_year=cls.year, name="Grade 5", level_order=5
        )
        cls.subject = Subject.objects.create(name="Mathematics", code="MATH")
        cls.mapping = ClassSubject.objects.create(
            school_class=cls.school_class, subject=cls.subject
        )

        # One user per role — all with must_change_password=False so the
        # force-password middleware does not mask permission results.
        cls.users: dict[str, User] = {}
        for n, role in enumerate(ALL_ROLES, start=1):
            cls.users[role] = _make_user(role, n)

        # Student profile for the student role
        cls.student = Student.objects.create(
            user=cls.users["student"],
            admission_number="ADM-MATRIX-1",
            current_class=cls.school_class,
            status=Student.Status.ACTIVE,
        )

        # Parent profile + guardian link for the parent role
        cls.parent_profile = ParentProfile.objects.create(user=cls.users["parent"])
        StudentGuardian.objects.create(
            student=cls.student,
            parent=cls.parent_profile,
            relationship=StudentGuardian.Relationship.GUARDIAN,
        )

        # Teacher profile + assignment for admin-only screens
        cls.teacher_profile = TeacherProfile.objects.create(
            user=cls.users["teacher"], staff_id="TCH-MATRIX-1"
        )
        cls.assignment = TeacherAssignment.objects.create(
            teacher=cls.teacher_profile,
            school_class=cls.school_class,
            subject=cls.subject,
            academic_year=cls.year,
        )

        # A draft result sheet for the governance detail screen
        cls.sheet = ResultSheet.objects.create(
            school_class=cls.school_class,
            subject=cls.subject,
            term=cls.term,
        )

        # One photograph and one enquiry for the Website management screens.
        # The image is a name rather than real bytes: this matrix asks who may
        # reach a page, and the upload behaviour is pinned with a real file in
        # ``apps/public/tests/test_admin_pages.py``.
        cls.photo = GalleryPhoto.objects.create(
            title="Assembly", image="gallery/assembly.png", display_order=1
        )
        cls.enquiry = ContactMessage.objects.create(
            name="Asiedu Family",
            email="asiedu@example.test",
            message="We would like to visit the school.",
        )

        cls.placeholders = {
            "__staff__": cls.users["finance"].pk,
            "__year__": cls.year.pk,
            "__term__": cls.term.pk,
            "__class__": cls.school_class.pk,
            "__student__": cls.student.pk,
            "__teacher_profile__": cls.teacher_profile.pk,
            "__mapping__": cls.mapping.pk,
            "__assignment__": cls.assignment.pk,
            "__sheet__": cls.sheet.pk,
            "__photo__": cls.photo.pk,
            "__enquiry__": cls.enquiry.pk,
        }

    def _resolve_kwargs(self, kwargs: dict) -> dict:
        return {
            key: (
                self.placeholders[value]
                if isinstance(value, str) and value in self.placeholders
                else value
            )
            for key, value in kwargs.items()
        }

    def _get(self, url_name: str, kwargs: dict, as_role: str | None) -> int:
        url = reverse(url_name, kwargs=self._resolve_kwargs(kwargs))
        if as_role is None:
            self.client.logout()
        else:
            self.client.force_login(self.users[as_role])
        with _engine:
            return self.client.get(url).status_code


class PermissionMatrixTest(MatrixFixtures, TestCase):

    def test_allowed_roles_can_access(self):
        failures = []
        for url_name, kwargs, allowed_roles, note in URL_ROLE_MATRIX:
            if allowed_roles == {"__anon__"}:
                status = self._get(url_name, kwargs, None)
                if status >= 400:
                    failures.append(
                        f"  anon → {url_name!r}: expected 2xx, got {status}"
                    )
                continue
            for role in allowed_roles:
                status = self._get(url_name, kwargs, role)
                if status >= 400:
                    failures.append(
                        f"  {role!r} → {url_name!r} ({note or 'allowed'}): "
                        f"expected 2xx/3xx, got {status}"
                    )
        if failures:
            self.fail(
                "Allowed roles received unexpected errors:\n" + "\n".join(failures)
            )

    def test_disallowed_roles_are_rejected(self):
        failures = []
        for url_name, kwargs, allowed_roles, note in URL_ROLE_MATRIX:
            if "__anon__" in allowed_roles:
                continue
            for role in ALL_ROLES - allowed_roles:
                status = self._get(url_name, kwargs, role)
                # 403 = PermissionDenied, 301/302 = redirect to login/dashboard,
                # 405 = admin reached a POST-only view via GET (not an access).
                if status not in DISALLOWED_STATUS and status < 400:
                    failures.append(
                        f"  {role!r} → {url_name!r} ({note}): expected 403/302, got {status}"
                    )
        if failures:
            self.fail(
                "Disallowed roles accessed protected URLs:\n" + "\n".join(failures)
            )

    def test_unauthenticated_redirected_to_login(self):
        failures = []
        for url_name, kwargs, allowed_roles, _ in URL_ROLE_MATRIX:
            if "__anon__" in allowed_roles:
                continue
            status = self._get(url_name, kwargs, None)
            if status not in (301, 302):
                failures.append(
                    f"  anon → {url_name!r}: expected redirect, got {status}"
                )
        if failures:
            self.fail(
                "Unauthenticated requests were not redirected:\n" + "\n".join(failures)
            )


class SuperuserBypassTest(MatrixFixtures, TestCase):
    """
    A superuser flag must not silently grant a role it does not have.

    Historically ``RoleRequiredMixin`` let ANY ``is_superuser`` through every
    role gate, so an account tagged ``director`` could open the audit log,
    staff management and results governance.  These tests pin the narrowed
    rule: a superuser passes a gate only when their own role is allowed by it
    (or when they carry no role at all — see below).
    """

    # Genuinely admin-ONLY pages, none needing URL kwargs. The school-structure
    # list pages are deliberately readable by director and finance now, so they
    # live in STRUCTURE_READ_URLS instead.
    ADMIN_ONLY_URLS = [
        "core:audit_log",
        "core_activity:feed",
        "core_activity:daily_report",
        "core_school:settings",
        "accounts:staff_list",
        "accounts:staff_create",
        "results:sheet_list",
        "results:grading_list",
        "students:add",
        "academics:year_create",
        "teachers:assignment_matrix",
    ]
    DIRECTOR_ONLY_URLS = ["dashboards:director_books", "dashboards:director_home"]
    STRUCTURE_READ_URLS = [
        "students:list",
        "teachers:list",
        "academics:year_list",
        "academics:class_list",
        "academics:subject_list",
        "academics:class_subject_list",
    ]

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A superuser whose role is NOT admin — must not reach admin-only pages.
        cls.director_superuser = User.objects.create_superuser(
            username="sup_but_director",
            email="sup_but_director@test.school",
            password="Testpass1!",
            role="director",
            must_change_password=False,
        )
        # A superuser with no role of its own (what `createsuperuser` produces,
        # since the role field has no default). Keep the legacy bypass for it so
        # such an account is never left unable to administer the system.
        cls.roleless_superuser = User.objects.create_superuser(
            username="sup_no_role",
            email="sup_no_role@test.school",
            password="Testpass1!",
            role="",
            must_change_password=False,
        )

    def _status(self, user, url_name: str) -> int:
        self.client.force_login(user)
        return self.client.get(
            reverse(url_name, kwargs=self._resolve_kwargs({}))
        ).status_code

    def test_superuser_with_other_role_cannot_reach_admin_pages(self):
        failures = []
        for url_name in self.ADMIN_ONLY_URLS:
            status = self._status(self.director_superuser, url_name)
            if status != 403:
                failures.append(
                    f"  role='director' superuser → {url_name!r}: "
                    f"expected 403, got {status}"
                )
        if failures:
            self.fail(
                "Superuser bypass leaked admin-only pages:\n" + "\n".join(failures)
            )

    def test_superuser_with_other_role_cannot_post_admin_actions(self):
        # POST-only admin endpoints must reject too. A real admin gets 405
        # here (method not allowed); a foreign-role superuser must get 403
        # because the mixin denies before the view is reached.
        self.client.force_login(self.director_superuser)
        url = reverse(
            "accounts:staff_deactivate",
            kwargs={"pk": self.users["finance"].pk},
        )
        status = self.client.post(url).status_code
        self.assertEqual(
            status,
            403,
            "a role='director' superuser must not be able to deactivate staff",
        )

    def test_superuser_still_reaches_pages_for_its_own_role(self):
        failures = []
        for url_name in self.DIRECTOR_ONLY_URLS:
            status = self._status(self.director_superuser, url_name)
            if status >= 400:
                failures.append(
                    f"  role='director' superuser → {url_name!r}: "
                    f"expected 2xx, got {status}"
                )
        if failures:
            self.fail(
                "Narrowed bypass broke a superuser's own role access:\n"
                + "\n".join(failures)
            )

    def test_roleless_superuser_keeps_legacy_bypass(self):
        failures = []
        for url_name in self.ADMIN_ONLY_URLS:
            status = self._status(self.roleless_superuser, url_name)
            if status >= 400:
                failures.append(
                    f"  role='' superuser → {url_name!r}: expected 2xx, got {status}"
                )
        if failures:
            self.fail(
                "A superuser with no role was locked out (legacy bypass broken):\n"
                + "\n".join(failures)
            )

    def test_plain_admin_role_user_still_reaches_admin_pages(self):
        # Guards against tightening the mixin accidentally breaking real admins
        failures = []
        for url_name in self.ADMIN_ONLY_URLS:
            status = self._status(self.users["admin"], url_name)
            if status >= 400:
                failures.append(
                    f"  role='admin' user → {url_name!r}: expected 2xx, got {status}"
                )
        if failures:
            self.fail("Admin role users lost access:\n" + "\n".join(failures))

    def test_school_structure_pages_are_readable_by_oversight_roles(self):
        """
        Director and finance browse classes, students and staff; teacher and
        parent must still be refused. Write screens stay admin-only (covered by
        PermissionMatrixTest).
        """
        failures = []
        for url_name in self.STRUCTURE_READ_URLS:
            for role in ("director", "finance"):
                status = self._status(self.users[role], url_name)
                if status >= 400:
                    failures.append(f"  {role!r} → {url_name!r}: expected 2xx, got {status}")
            for role in ("teacher", "parent", "student"):
                status = self._status(self.users[role], url_name)
                if status != 403:
                    failures.append(f"  {role!r} → {url_name!r}: expected 403, got {status}")
        if failures:
            self.fail("School-structure read access is wrong:\n" + "\n".join(failures))
