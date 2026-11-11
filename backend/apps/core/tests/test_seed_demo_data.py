"""
apps/core/tests/test_seed_demo_data.py
--------------------------------------
Proves the demo seeder is safe to run, complete, and repeatable.

The seed is the entry point a new developer hits first, so these tests check
the three things that make it usable: it produces data on every screen, it
never duplicates itself on a second run, and the demo logins it prints
actually authenticate.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Count, Sum
from django.test import TestCase

from apps.academics import calendar as academics_calendar
from apps.academics.models import AcademicYear, ClassSubject, SchoolClass, Term
from apps.accounts.models import ParentProfile, User
from apps.communications.models import Announcement, MessageThread
from apps.core.models import AuditLog, Notification
from apps.finance.models import (
    FeeStructure,
    Invoice,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentAllocation,
)
from apps.homework.models import Homework, HomeworkSubmission
from apps.results import services as results_services
from apps.results.models import GradeBand, GradeScaleBand, ResultSheet, ScoreEntry
from apps.students.models import Student, StudentGuardian
from apps.teachers.models import TeacherAssignment, TeacherProfile

from ..management.commands.seed_demo_data import (
    CLASSES,
    GRADE_BANDS,
    SHEET_TARGETS,
    STUDENTS_PER_CLASS,
    SUBJECTS,
    TEACHERS,
    is_production,
)

EXPECTED_STUDENTS = len(CLASSES) * STUDENTS_PER_CLASS
SEED_STATES = set(SHEET_TARGETS)
# Passed with --password so the test can log in afterwards. Deliberately
# fake: nothing outside the test database ever uses it.
SEED_TEST_PASSWORD = "seed-test-only-password"


def run_seed(**options) -> str:
    """Invoke the seeder, returning everything it wrote to stdout."""
    options.setdefault("password", SEED_TEST_PASSWORD)
    # The test runner turns DEBUG off, which is exactly the condition the
    # command refuses to run under; the tests seed a throwaway database.
    options.setdefault("force", True)
    out = io.StringIO()
    call_command("seed_demo_data", stdout=out, verbosity=1, **options)
    return out.getvalue()


def _counts() -> dict:
    return {
        "users": User.objects.count(),
        "students": Student.objects.count(),
        "classes": SchoolClass.objects.count(),
        "assignments": TeacherAssignment.objects.count(),
        "invoices": Invoice.objects.count(),
        "payments": Payment.objects.count(),
        "journal_lines": JournalLine.objects.count(),
        "sheets": ResultSheet.objects.count(),
        "entries": ScoreEntry.objects.count(),
        "homework": Homework.objects.count(),
        "announcements": Announcement.objects.count(),
        "threads": MessageThread.objects.count(),
        "notifications": Notification.objects.count(),
    }


class SeedDemoDataTests(TestCase):
    """One full seed, then assertions about what it left behind."""

    @classmethod
    def setUpTestData(cls):
        cls.output = run_seed()

    # ─── Structure ───────────────────────────────────────────────────────

    def test_academic_structure_exists(self):
        self.assertEqual(AcademicYear.objects.filter(is_current=True).count(), 1)
        self.assertEqual(Term.objects.filter(is_current=True).count(), 1)
        self.assertEqual(SchoolClass.objects.count(), len(CLASSES))
        current = Term.objects.get(is_current=True)
        self.assertEqual(
            ClassSubject.objects.filter(
                school_class__academic_year=current.academic_year
            ).count(),
            len(CLASSES) * len(SUBJECTS),
        )

    def test_every_class_has_a_class_teacher_and_teaching_assignments(self):
        for name, _level, _fee in CLASSES:
            school_class = SchoolClass.objects.get(name=name)
            self.assertIsNotNone(school_class.class_teacher_id, name)
            self.assertEqual(school_class.class_teacher.role, "teacher")
        self.assertEqual(
            TeacherAssignment.objects.count(), len(CLASSES) * len(SUBJECTS)
        )
        self.assertEqual(TeacherProfile.objects.count(), len(TEACHERS))

    def test_students_have_user_accounts_and_one_guardian_each(self):
        self.assertEqual(Student.objects.count(), EXPECTED_STUDENTS)
        self.assertFalse(
            Student.objects.filter(current_class__isnull=True).exists(),
            "every demo student must sit in a class",
        )
        self.assertEqual(StudentGuardian.objects.count(), EXPECTED_STUDENTS)
        linked = set(
            StudentGuardian.objects.values_list("student_id", flat=True)
        )
        self.assertEqual(len(linked), EXPECTED_STUDENTS)

    def test_two_families_have_children_in_two_classes(self):
        """The parent child-switcher needs a parent with more than one ward."""
        multi = (
            ParentProfile.objects.annotate(wards=Count("children_links"))
            .filter(wards__gt=1)
            .first()
        )
        self.assertIsNotNone(multi, "expected at least one parent with 2 children")

    # ─── Results ─────────────────────────────────────────────────────────

    def test_result_sheets_cover_every_state(self):
        current = Term.objects.get(is_current=True)
        sheets = ResultSheet.objects.filter(term=current)
        self.assertEqual(sheets.count(), len(CLASSES) * len(SUBJECTS))
        for target in SEED_STATES:
            self.assertTrue(
                sheets.filter(status=target).exists(),
                f"no sheet seeded in state {target}",
            )

    def test_published_sheets_have_graded_entries(self):
        published = ResultSheet.objects.filter(status=ResultSheet.Status.PUBLISHED)
        entries = ScoreEntry.objects.filter(result_sheet__in=published)
        self.assertTrue(entries.exists())
        self.assertFalse(
            entries.filter(total__isnull=True).exists(),
            "every published entry must carry a computed total",
        )
        self.assertFalse(
            entries.filter(grade="").exists(),
            "the grade scale must resolve every seeded total to a grade",
        )
        self.assertEqual(GradeScaleBand.objects.count(), len(GRADE_BANDS))
        self.assertEqual(GradeBand.objects.count(), 0)

    def test_draft_sheet_is_only_partially_entered(self):
        draft = ResultSheet.objects.filter(status=ResultSheet.Status.DRAFT).first()
        self.assertIsNotNone(draft)
        enrolled = Student.objects.filter(current_class=draft.school_class).count()
        entered = ScoreEntry.objects.filter(result_sheet=draft).count()
        self.assertLess(entered, enrolled)

    # ─── Finance ────────────────────────────────────────────────────────

    def test_invoices_are_posted_for_every_student(self):
        current = Term.objects.get(is_current=True)
        self.assertEqual(
            Invoice.objects.filter(term=current, status=Invoice.Status.POSTED).count(),
            EXPECTED_STUDENTS,
        )
        self.assertEqual(
            FeeStructure.objects.filter(term=current, is_active=True).count(),
            len(CLASSES),
        )

    def test_some_students_paid_and_some_still_owe(self):
        paid = Payment.objects.count()
        self.assertGreater(paid, 0)
        self.assertLess(paid, EXPECTED_STUDENTS)
        fully_paid = 0
        for invoice in Invoice.objects.all():
            if invoice.amount_outstanding <= 0:
                fully_paid += 1
        self.assertGreater(fully_paid, 0)
        self.assertLess(fully_paid, EXPECTED_STUDENTS)

    def test_every_payment_is_fully_allocated_against_an_invoice(self):
        for payment in Payment.objects.all():
            allocated = payment.allocations.aggregate(total=Sum("allocated_amount"))[
                "total"
            ]
            self.assertEqual(
                allocated,
                payment.amount,
                f"{payment.reference} left {payment.amount - allocated} unallocated",
            )
        self.assertTrue(PaymentAllocation.objects.exists())

    def test_double_entry_ledger_stays_balanced(self):
        totals = JournalLine.objects.aggregate(
            debit=Sum("debit_amount"), credit=Sum("credit_amount")
        )
        self.assertGreater(totals["debit"], 0)
        self.assertEqual(totals["debit"], totals["credit"])
        for entry in JournalEntry.objects.all():
            line_totals = entry.lines.aggregate(
                debit=Sum("debit_amount"), credit=Sum("credit_amount")
            )
            self.assertEqual(
                line_totals["debit"],
                line_totals["credit"],
                f"journal entry {entry.reference} is unbalanced",
            )

    # ─── Other screens ───────────────────────────────────────────────────

    def test_homework_announcements_threads_and_notifications_exist(self):
        self.assertEqual(Homework.objects.count(), len(CLASSES) * 2)
        self.assertEqual(Announcement.objects.count(), 3)
        self.assertEqual(MessageThread.objects.count(), 3)
        self.assertTrue(Notification.objects.exists())
        for thread in MessageThread.objects.all():
            self.assertTrue(thread.messages.filter(is_opening=True).exists())

    def test_homework_has_hand_ins_and_the_overdue_ones_are_late(self):
        overdue = Homework.objects.filter(due_date__lt=dt.date.today())
        upcoming = Homework.objects.filter(due_date__gte=dt.date.today())
        self.assertTrue(overdue.exists())
        self.assertTrue(upcoming.exists())

        late = HomeworkSubmission.objects.filter(homework__in=overdue)
        self.assertTrue(late.exists(), "the review page needs late hand-ins")
        self.assertFalse(late.filter(is_late=False).exists())

        on_time = HomeworkSubmission.objects.filter(homework__in=upcoming)
        self.assertTrue(on_time.exists())
        self.assertFalse(on_time.filter(is_late=True).exists())

        # Only part of each register has handed in, so the page shows both.
        for homework in Homework.objects.all():
            enrolled = Student.objects.filter(current_class=homework.school_class).count()
            handed_in = HomeworkSubmission.objects.filter(homework=homework).count()
            self.assertLess(handed_in, enrolled, homework.title)

    def test_audit_trail_records_the_seeded_activity(self):
        actions = set(AuditLog.objects.values_list("action", flat=True))
        for expected in (
            "academics.year_created",
            "student.created",
            "teacher.assigned",
            "finance.invoices_posted",
            "finance.payment_recorded",
            "results.published",
        ):
            self.assertIn(expected, actions)

    # ─── Logins ──────────────────────────────────────────────────────────

    def test_demo_logins_authenticate(self):
        logins = [
            ("admin", User.Role.ADMIN),
            ("director", User.Role.DIRECTOR),
            ("finance", User.Role.FINANCE),
            ("tch_001", User.Role.TEACHER),
            ("stu_001", User.Role.STUDENT),
            ("par_001", User.Role.PARENT),
        ]
        for username, role in logins:
            with self.subTest(username=username):
                user = User.objects.get(username=username)
                self.assertEqual(user.role, role)
                self.assertFalse(
                    user.must_change_password,
                    "demo accounts must not be stuck on the forced password change",
                )
                self.assertTrue(
                    self.client.login(
                        username=username, password=SEED_TEST_PASSWORD
                    )
                )
                self.client.logout()

    def test_summary_prints_the_login_table(self):
        self.assertIn("Demo data ready.", self.output)
        self.assertIn(SEED_TEST_PASSWORD, self.output)
        self.assertIn("stu_001", self.output)

    # ─── Safety ──────────────────────────────────────────────────────────

    def test_dry_run_writes_nothing(self):
        before = {
            "students": Student.objects.count(),
            "users": User.objects.count(),
            "sheets": ResultSheet.objects.count(),
        }
        output = run_seed(dry_run=True)
        self.assertIn("rolled back", output)
        self.assertEqual(
            {
                "students": Student.objects.count(),
                "users": User.objects.count(),
                "sheets": ResultSheet.objects.count(),
            },
            before,
        )


class SeedDemoDataIdempotencyTests(TestCase):
    """A second run must add nothing — no duplicate students, invoices or rows."""

    def test_running_twice_creates_no_duplicates(self):
        run_seed()
        snapshot = _counts()
        run_seed()
        self.assertEqual(_counts(), snapshot)


class SeedDemoDataClassSizeTests(TestCase):
    """--students-per-class is what makes the seed useful at register size."""

    def test_the_register_size_follows_the_option(self):
        output = run_seed(students_per_class=12)

        self.assertEqual(Student.objects.count(), len(CLASSES) * 12)
        self.assertEqual(StudentGuardian.objects.count(), len(CLASSES) * 12)
        self.assertEqual(
            Invoice.objects.filter(status=Invoice.Status.POSTED).count(),
            len(CLASSES) * 12,
        )
        # Numbering carries on past the first class, so the last pupil exists.
        self.assertTrue(User.objects.filter(username="stu_048").exists())
        self.assertIn("students per class", output)

    def test_a_size_below_the_sibling_minimum_is_refused(self):
        with self.assertRaises(CommandError):
            run_seed(students_per_class=1)
        self.assertEqual(Student.objects.count(), 0)


class SeedDemoDataAdoptionTests(TestCase):
    """A database that already has an admin must keep that admin's password."""

    def test_an_existing_same_role_account_is_reused_untouched(self):
        admin = User.objects.create_user(
            username="admin",
            email="admin@school.test",
            password="TheirOwnPass123!",
            role=User.Role.ADMIN,
        )

        output = run_seed()

        admin.refresh_from_db()
        self.assertTrue(admin.check_password("TheirOwnPass123!"))
        self.assertEqual(User.objects.filter(username="admin").count(), 1)
        self.assertEqual(admin.email, "admin@school.test")
        self.assertIn("password unchanged", output)

    def test_a_same_name_account_in_another_role_still_refuses(self):
        User.objects.create_user(
            username="admin",
            email="someone@school.test",
            password="Pass12345!",
            role=User.Role.TEACHER,
        )
        with self.assertRaises(CommandError):
            run_seed()

    def test_second_run_reports_zero_created(self):
        run_seed()
        output = run_seed()
        for line in output.splitlines():
            if line.startswith("  +"):
                self.fail(f"second seed still created rows: {line}")


class SeedDemoDataExistingGradeScaleTests(TestCase):
    """
    A school that already tuned the current year's scale must keep it.

    The dev database hit this: the year pre-dated the seed and already had a
    two-band scale, so the seeder skipped creating one — and, because
    ``save_grade_scale`` is what attaches classes, the demo classes were left
    unlinked and every seeded mark graded blank.
    """

    @classmethod
    def setUpTestData(cls):
        start_year = academics_calendar.current_start_year(dt.date.today())
        start, end = academics_calendar.year_dates(start_year)
        cls.year = AcademicYear.objects.create(
            name=academics_calendar.year_name(start_year),
            start_date=start,
            end_date=end,
            is_current=True,
        )
        cls.scale = results_services.save_grade_scale(
            academic_year=cls.year,
            name="School's own scale",
            bands=[
                {
                    "min_score": Decimal("0"),
                    "max_score": Decimal("49.99"),
                    "grade": "F",
                    "remark": "Fail",
                    "points": Decimal("0"),
                },
                {
                    "min_score": Decimal("50"),
                    "max_score": Decimal("100"),
                    "grade": "A",
                    "remark": "Pass",
                    "points": Decimal("1"),
                },
            ],
            classes=[],
        )["scale"]
        cls.output = run_seed()

    def test_the_existing_bands_are_not_replaced(self):
        self.assertEqual(
            list(self.scale.bands.order_by("min_score").values_list("grade", flat=True)),
            ["F", "A"],
        )

    def test_the_demo_classes_are_attached_to_the_existing_scale(self):
        self.assertEqual(
            SchoolClass.objects.filter(
                academic_year=self.year, grade_scale=self.scale
            ).count(),
            len(CLASSES),
        )

    def test_published_entries_are_graded_through_it(self):
        entries = ScoreEntry.objects.filter(total__isnull=False)
        self.assertTrue(entries.exists())
        self.assertFalse(
            entries.filter(grade="").exists(),
            "marks graded through the pre-existing scale must not be blank",
        )
        self.assertIn("A", set(entries.values_list("grade", flat=True)))


class SeedDemoDataGuardTests(TestCase):
    """
    The seeder hands out working passwords, so it must refuse to run somewhere
    that looks like a real deployment or against an account it does not own.
    """

    def test_debug_on_is_not_blocked(self):
        with self.settings(DEBUG=True):
            self.assertFalse(is_production())

    def test_refuses_to_run_with_debug_off(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo_data", stdout=io.StringIO(), verbosity=0)

    def test_blocked_run_writes_nothing(self):
        before = _counts()
        with self.assertRaises(CommandError):
            call_command("seed_demo_data", stdout=io.StringIO(), verbosity=0)
        self.assertEqual(_counts(), before)

    def test_force_flag_overrides_the_debug_guard(self):
        with self.settings(DEBUG=False):
            output = run_seed(force=True, dry_run=True)
        self.assertIn("Dry run complete", output)

    def test_refuses_to_hijack_an_existing_username(self):
        User.objects.create_user(
            username="admin",
            email="real.person@example.com",
            password="SomethingElse1!",
            role=User.Role.DIRECTOR,
        )
        with self.assertRaises(CommandError):
            run_seed()



class GeneratedPasswordTests(TestCase):
    """Without --password the seeder invents one, prints it, and it works."""

    def test_the_printed_password_authenticates(self):
        out = io.StringIO()
        call_command(
            "seed_demo_data", password="", stdout=out, verbosity=1, force=True
        )
        output = out.getvalue()

        admin_line = next(
            line for line in output.splitlines() if line.strip().startswith("admin ")
        )
        generated = re.search(r"\b([A-Za-z0-9]{16})\b", admin_line)
        self.assertIsNotNone(generated, admin_line)
        self.assertTrue(
            self.client.login(username="admin", password=generated.group(1))
        )

    def test_a_second_run_keeps_the_generated_password(self):
        run_seed(password="")
        second = run_seed(password="")
        admin_line = next(
            line for line in second.splitlines() if line.strip().startswith("admin ")
        )
        self.assertIn("(kept)", admin_line)
