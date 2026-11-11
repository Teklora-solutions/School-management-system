"""
apps/finance/tests/test_statement_scenarios_seed.py
---------------------------------------------------
The statement scenarios are the fixture the per-term statement work is judged
against, so these tests check the three things that make a fixture usable: it
leaves the states it claims, a second run changes nothing, and it never bills a
pupil outside its own class.
"""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal

from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from apps.academics import services as academics_services
from apps.academics.models import SchoolClass, Term
from apps.accounts.models import User
from apps.finance.models import (
    Adjustment,
    FeeStructure,
    Invoice,
    JournalLine,
    Payment,
    PaymentAllocation,
)
from apps.students import services as students_services
from apps.students.models import Student

from ..management.commands.seed_statement_scenarios import (
    SCENARIO_CLASS,
    SCENARIOS,
)
from .. import services as finance_services

ZERO = Decimal("0.00")


def run_seed(**options) -> str:
    out = io.StringIO()
    # The test runner turns DEBUG off, which is the condition the command
    # refuses to run under; this seeds a throwaway database.
    options.setdefault("force", True)
    call_command("seed_statement_scenarios", stdout=out, **options)
    return out.getvalue()


def figures(student: Student, term: Term) -> dict:
    posted = Invoice.objects.filter(
        student=student, term=term, status=Invoice.Status.POSTED
    )
    posted_ids = list(posted.values_list("pk", flat=True))
    billed = posted.aggregate(t=Sum("total_amount"))["t"] or ZERO
    paid = PaymentAllocation.objects.filter(invoice_id__in=posted_ids).total()
    adjusted = (
        Adjustment.objects.filter(
            student=student, invoice__term=term, status=Adjustment.Status.POSTED
        ).aggregate(t=Sum("amount"))["t"]
        or ZERO
    )
    return {
        "billed": billed,
        "paid": paid,
        "adjusted": adjusted,
        "open": billed - paid - adjusted,
    }


class SeedStatementScenariosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = academics_services.create_academic_year(
            name="2026/2027",
            start_date=today - dt.timedelta(days=60),
            end_date=today + dt.timedelta(days=300),
            is_current=True,
        )
        cls.terms = [
            academics_services.create_term(
                academic_year=cls.year,
                name=name,
                start_date=today + dt.timedelta(days=offset),
                end_date=today + dt.timedelta(days=offset + 80),
            )
            for name, offset in (
                (Term.TermName.TERM_1, -60),
                (Term.TermName.TERM_2, 40),
                (Term.TermName.TERM_3, 140),
            )
        ]
        User.objects.create_user(
            username="seed_admin",
            email="seed_admin@example.test",
            password="test-only",
            role=User.Role.ADMIN,
        )
        User.objects.create_user(
            username="seed_finance",
            email="seed_finance@example.test",
            password="test-only",
            role=User.Role.FINANCE,
        )

    # ─── helpers ─────────────────────────────────────────────────────────

    def _student(self, admission: str) -> Student:
        return Student.objects.get(admission_number=admission)

    def _scenario_class(self) -> SchoolClass:
        return SchoolClass.objects.get(academic_year=self.year, name=SCENARIO_CLASS)

    # ─── what it leaves behind ───────────────────────────────────────────

    def test_it_creates_the_nine_pupils_in_one_class(self):
        run_seed()
        school_class = self._scenario_class()

        self.assertEqual(school_class.students.count(), len(SCENARIOS))
        for admission, _first, _last, _label in SCENARIOS:
            self.assertTrue(
                Student.objects.filter(
                    admission_number=admission, current_class=school_class
                ).exists(),
                admission,
            )

    def test_it_bills_three_terms_and_leaves_one_invoice_in_draft(self):
        run_seed()

        self.assertEqual(
            FeeStructure.objects.filter(school_class=self._scenario_class()).count(), 3
        )
        self.assertEqual(
            Invoice.objects.filter(student__current_class=self._scenario_class()).count(),
            len(SCENARIOS) * 3,
        )
        drafts = Invoice.objects.filter(
            student=self._student("SCN-S8-DRAFT"), status=Invoice.Status.DRAFT
        )
        self.assertEqual(drafts.count(), 1)
        self.assertEqual(drafts.get().term, self.terms[2])
        # A draft belongs to no billing total and no ledger entry.
        self.assertFalse(
            JournalLine.objects.filter(
                entry__source_type="invoice", entry__source_id=str(drafts.get().pk)
            ).exists()
        )

    def test_per_term_sets_are_what_the_scenarios_claim(self):
        run_seed()
        t1, t2, t3 = self.terms

        settled = figures(self._student("SCN-S1-PAID"), t1)
        self.assertEqual((settled["billed"], settled["paid"], settled["open"]),
                         (Decimal("600.00"), Decimal("600.00"), ZERO))

        carry = figures(self._student("SCN-S2-CARRY"), t1)
        self.assertEqual((carry["paid"], carry["open"]), (Decimal("200.00"), Decimal("400.00")))
        carry_t2 = figures(self._student("SCN-S2-CARRY"), t2)
        self.assertEqual(
            (carry_t2["billed"], carry_t2["paid"], carry_t2["open"]),
            (Decimal("600.00"), Decimal("600.00"), ZERO),
        )
        # What later terms carry is 400, not 0.
        self.assertEqual(
            sum(
                figures(self._student("SCN-S2-CARRY"), term)["open"]
                for term in (t1, t2)
            ),
            Decimal("400.00"),
        )

        untouched = figures(self._student("SCN-S3-NIL"), t3)
        self.assertEqual(untouched["open"], Decimal("600.00"))
        self.assertEqual(untouched["paid"], ZERO)

        advance_t2 = figures(self._student("SCN-S4-ADVANCE"), t2)
        self.assertEqual(advance_t2["open"], ZERO)

        late = figures(self._student("SCN-S5-LATE"), t2)
        self.assertEqual(late["open"], Decimal("600.00"))
        self.assertEqual(figures(self._student("SCN-S5-LATE"), t1)["open"], ZERO)

        awarded = figures(self._student("SCN-S6-AWARD"), t1)
        self.assertEqual(
            (awarded["adjusted"], awarded["paid"], awarded["open"]),
            (Decimal("300.00"), Decimal("300.00"), ZERO),
        )
        bursary = figures(self._student("SCN-S6-AWARD"), t2)
        self.assertEqual(bursary["adjusted"], Decimal("150.00"))

        reversed_t1 = figures(self._student("SCN-S7-REVERS"), t1)
        self.assertEqual(reversed_t1["paid"], ZERO)
        self.assertEqual(reversed_t1["open"], Decimal("600.00"))

        draft = figures(self._student("SCN-S8-DRAFT"), t3)
        self.assertEqual((draft["billed"], draft["open"]), (ZERO, ZERO))

        credit_student = self._student("SCN-S9-CREDIT")
        self.assertEqual(
            sum(
                (payment.unapplied_amount for payment in credit_student.payments.all()),
                ZERO,
            ),
            Decimal("300.00"),
        )

    def test_the_reversal_is_a_second_row_not_a_deletion(self):
        run_seed()
        student = self._student("SCN-S7-REVERS")
        payments = Payment.objects.filter(student=student).order_by("created_at")
        self.assertEqual(payments.count(), 2)
        original = payments.get(reference="SCN-S7-REVERS-T1")
        self.assertTrue(original.is_reversed)
        self.assertEqual(original.reversal.amount, Decimal("-600.00"))
        self.assertEqual(original.allocations.count(), 1)

    def test_the_ledger_still_balances(self):
        run_seed()
        totals = JournalLine.objects.aggregate(
            debit=Sum("debit_amount"), credit=Sum("credit_amount")
        )
        self.assertEqual(totals["debit"], totals["credit"])

    # ─── safety and repeatability ────────────────────────────────────────

    def test_a_second_run_changes_nothing(self):
        run_seed()
        counts = {
            "classes": SchoolClass.objects.count(),
            "students": Student.objects.count(),
            "structures": FeeStructure.objects.count(),
            "invoices": Invoice.objects.count(),
            "payments": Payment.objects.count(),
            "adjustments": Adjustment.objects.count(),
            "lines": JournalLine.objects.count(),
        }

        output = run_seed()

        self.assertEqual(
            counts,
            {
                "classes": SchoolClass.objects.count(),
                "students": Student.objects.count(),
                "structures": FeeStructure.objects.count(),
                "invoices": Invoice.objects.count(),
                "payments": Payment.objects.count(),
                "adjustments": Adjustment.objects.count(),
                "lines": JournalLine.objects.count(),
            },
        )
        self.assertIn("reused", output)

    def test_a_pupil_outside_the_scenario_class_is_never_billed(self):
        other_class = academics_services.create_class(
            academic_year=self.year, name="Basic 5", level_order=5
        )
        finance_services.create_fee_structure(
            school_class=other_class,
            term=self.terms[0],
            items=[{"label": "Tuition", "amount": Decimal("300.00"), "ordering": 1}],
        )
        outsider = students_services.create_student_with_guardian(
            admission_number="OUT-001",
            first_name="Outside",
            last_name="Pupil",
            email="outside@example.test",
            current_class=other_class,
            send_credentials=False,
        )

        run_seed()

        self.assertFalse(Invoice.objects.filter(student=outsider).exists())
        self.assertFalse(Payment.objects.filter(student=outsider).exists())

    def test_dry_run_writes_nothing(self):
        output = run_seed(dry_run=True)

        self.assertIn("rolled back", output)
        self.assertFalse(SchoolClass.objects.filter(name=SCENARIO_CLASS).exists())
        self.assertFalse(Student.objects.filter(admission_number__startswith="SCN-").exists())
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def test_the_report_names_every_pupil_and_term(self):
        output = run_seed()

        for admission, _first, _last, _label in SCENARIOS:
            self.assertIn(admission, output)
        for term in self.terms:
            self.assertIn(term.get_name_display(), output)
