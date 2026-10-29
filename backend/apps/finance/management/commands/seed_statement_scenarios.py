"""
apps/finance/management/commands/seed_statement_scenarios.py
------------------------------------------------------------
Build the term-statement scenarios the statement page is judged against.

Nine pupils in a dedicated class, one academic year, three terms and a receipt
history chosen so every per-term state exists at least once: settled, part-paid
with a carry-over, untouched, paid ahead, late, awarded, reversed, draft, and
holding a credit.

Every write goes through the ordinary services (``post_invoices``,
``record_payment``, ``apply_adjustment``, ``reverse_payment``), so the ledger,
the receipt register and the audit trail are exactly what real money leaves
behind. A second run reuses what the first one made: the class, the pupils, the
fee structures, the draft, the receipts and the adjustments are all looked up by
their natural keys first.

Why a dedicated class: ``post_invoices`` bills a whole class, so the scenarios
live in one of their own and no existing pupil is ever billed by this command.

The figures this leaves behind are printed at the end, term by term, computed
from the rows themselves. That table is the acceptance sheet for the statement
work; it does not claim how the page should read.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.academics import services as academics_services
from apps.academics.models import AcademicYear, SchoolClass, Term
from apps.accounts.models import User
from apps.core.management.commands.seed_demo_data import production_settings_module
from apps.finance import selectors as finance_selectors
from apps.finance import services as finance_services
from apps.finance.models import (
    Adjustment,
    FeeStructure,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentAllocation,
)
from apps.students import services as students_services
from apps.students.models import Student

ZERO = Decimal("0.00")

SCENARIO_CLASS = "Statement Scenarios"
EMAIL_DOMAIN = "statement-scenarios.invalid"

# Level 999 keeps the class at the end of every ordering and off the promotion
# ladder: there is no class above it to promote these pupils into.
SCENARIO_LEVEL_ORDER = 999

# Fees per term: 400 + 150 + 50 = 600.
FEE_ITEMS = [
    ("Tuition", Decimal("400.00")),
    ("Meals", Decimal("150.00")),
    ("PTA levy", Decimal("50.00")),
]
FEE_TOTAL = sum((amount for _, amount in FEE_ITEMS), ZERO)

# (admission number, first name, last name, what the scenario is for)
SCENARIOS = [
    ("SCN-S1-PAID", "Ama", "Owusu", "every term settled in full"),
    (
        "SCN-S2-CARRY",
        "Kwame",
        "Asante",
        "term 1 part-paid, so later terms carry a balance forward",
    ),
    ("SCN-S3-NIL", "Akosua", "Boateng", "nothing paid in any term"),
    (
        "SCN-S4-ADVANCE",
        "Yaw",
        "Mensah",
        "terms 1 and 2 paid by one receipt, before term 2 begins",
    ),
    (
        "SCN-S5-LATE",
        "Kojo",
        "Frimpong",
        "the oldest debt is cleared first; this term is still unpaid",
    ),
    (
        "SCN-S6-AWARD",
        "Esi",
        "Appiah",
        "a scholarship in term 1 and a waiver in term 2",
    ),
    (
        "SCN-S7-REVERS",
        "Nii",
        "Tetteh",
        "the term 1 receipt was reversed, so term 1 is unpaid again",
    ),
    (
        "SCN-S8-DRAFT",
        "Abena",
        "Sarpong",
        "the term 3 invoice is still a draft and must not be billed",
    ),
    (
        "SCN-S9-CREDIT",
        "Adjoa",
        "Kusi",
        "300 more than term 1 owed was received; the surplus is held as credit",
    ),
]

# Receipts, in posting order: (admission, receipt suffix, days ago, amount,
# [(term position, amount to allocate)]). Every allocation is explicit so the
# scenario means what it says; whatever is left unallocated becomes credit.
RECEIPTS = [
    ("SCN-S1-PAID", "T1", 6, "600.00", [(1, "600.00")]),
    ("SCN-S1-PAID", "T2", 3, "600.00", [(2, "600.00")]),
    ("SCN-S1-PAID", "T3", 1, "600.00", [(3, "600.00")]),
    ("SCN-S2-CARRY", "T1", 6, "200.00", [(1, "200.00")]),
    ("SCN-S2-CARRY", "T2", 2, "600.00", [(2, "600.00")]),
    ("SCN-S4-ADVANCE", "T1T2", 4, "1200.00", [(1, "600.00"), (2, "600.00")]),
    ("SCN-S5-LATE", "OLD", 2, "600.00", [(1, "600.00")]),
    ("SCN-S6-AWARD", "T1", 5, "300.00", [(1, "300.00")]),
    ("SCN-S6-AWARD", "T2", 2, "450.00", [(2, "450.00")]),
    ("SCN-S7-REVERS", "T1", 6, "600.00", [(1, "600.00")]),
    ("SCN-S8-DRAFT", "T1", 3, "600.00", [(1, "600.00")]),
    ("SCN-S9-CREDIT", "T1", 4, "900.00", [(1, "600.00")]),
]

# (admission, term position, adjustment type, amount, reason)
ADJUSTMENTS = [
    (
        "SCN-S6-AWARD",
        1,
        Adjustment.AdjustmentType.SCHOLARSHIP,
        "300.00",
        "Statement scenario S6: merit scholarship for term 1",
    ),
    (
        "SCN-S6-AWARD",
        2,
        Adjustment.AdjustmentType.WAIVER,
        "150.00",
        "Statement scenario S6: bursary waiver for term 2",
    ),
]

REVERSAL = ("SCN-S7-REVERS", "T1", "Statement scenario S7: cheque was returned")


class _DryRunRollback(Exception):
    """Raised at the end of --dry-run so the atomic block unwinds every write."""


class Command(BaseCommand):
    help = (
        "Seed the nine term-statement scenarios (settled, carried over, unpaid, "
        "paid ahead, late, awarded, reversed, draft, credit)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run the whole seed, then roll every write back.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Run even under the production settings module. The scenario "
                "logins use their admission number as the password."
            ),
        )

    def handle(self, *args, **options):
        if production_settings_module() and not options["force"]:
            raise CommandError(
                "Scenario pupils are seeded with a known password and no forced "
                f"change. Refusing to run under {settings.SETTINGS_MODULE}; pass "
                "--force if that is really what you want."
            )

        self.dry_run = options["dry_run"]
        self.today = timezone.localdate()
        self.created: dict[str, int] = {}
        self.reused: dict[str, int] = {}

        apps_logger = logging.getLogger("apps")
        previous_level = apps_logger.level
        apps_logger.setLevel(logging.ERROR)
        try:
            with transaction.atomic():
                try:
                    self.seed()
                    self.ledger_rows = self._collect_ledger_rows()
                except ValidationError as exc:
                    # A closed period or a back-dating window is the usual
                    # reason; say which rather than printing a traceback.
                    raise CommandError(
                        "The finance services refused a posting: "
                        f"{'; '.join(str(m) for m in exc.messages)}"
                    ) from exc
                if self.dry_run:
                    raise _DryRunRollback
        except _DryRunRollback:
            pass
        finally:
            apps_logger.setLevel(previous_level)

        self.write_report()

    # ─── Steps ───────────────────────────────────────────────────────────

    def seed(self) -> None:
        self.year = self._current_year()
        self.terms = self._year_terms(self.year)
        self.admin = self._staff(User.Role.ADMIN)
        self.officer = self._staff(User.Role.FINANCE, fallback=self.admin)
        self.school_class = self._scenario_class()
        self.structures = [self._structure(term) for term in self.terms]
        self.students = {
            admission: self._student(admission, first, last)
            for admission, first, last, _label in SCENARIOS
        }

        # Created before the term 3 run so the billing plan sees it and skips
        # the pupil — a draft is an invoice the school has not issued yet.
        self._draft_term3_invoice()
        for term in self.terms:
            finance_services.post_invoices(
                term=term, school_class=self.school_class, actor=self.officer
            )

        self._receipts()
        self._adjustments()
        self._reversal()

    def _current_year(self) -> AcademicYear:
        year = (
            AcademicYear.objects.filter(is_current=True)
            .order_by("-start_date")
            .first()
            or AcademicYear.objects.order_by("-start_date").first()
        )
        if year is None:
            raise CommandError(
                "No academic year exists. Create the current year and its three "
                "terms first (Academics → Years)."
            )
        return year

    def _year_terms(self, year: AcademicYear) -> list[Term]:
        terms = list(year.terms.order_by("start_date")[:3])
        if len(terms) < 3:
            raise CommandError(
                f"Academic year {year.name} has {len(terms)} term(s); the "
                "statement scenarios need all three. Add the missing terms first."
            )
        return terms

    def _staff(self, role: str, *, fallback: User | None = None) -> User:
        user = (
            User.objects.filter(role=role, is_active=True).order_by("pk").first()
            or fallback
        )
        if user is None:
            raise CommandError(
                f"No active {role} account exists. The seed needs one to post "
                "the invoices, receipts and adjustments."
            )
        return user

    def _scenario_class(self) -> SchoolClass:
        school_class = SchoolClass.objects.filter(
            academic_year=self.year, name=SCENARIO_CLASS
        ).first()
        if school_class:
            self._count("reused", "scenario class")
            return school_class
        school_class = academics_services.create_class(
            academic_year=self.year,
            name=SCENARIO_CLASS,
            level_order=SCENARIO_LEVEL_ORDER,
            actor=self.admin,
        )
        self._count("created", "scenario class")
        return school_class

    def _structure(self, term: Term) -> FeeStructure:
        """The class's active fee structure for one term, created if absent."""
        structure = (
            FeeStructure.objects.filter(
                school_class=self.school_class, term=term, is_active=True
            )
            .order_by("-version")
            .first()
        )
        if structure:
            self._count("reused", "fee structure")
            return structure

        # The first term's deadline is in the past on a year already under way,
        # which is what makes its unpaid invoices overdue; the later terms fall
        # due after the last term starts.
        due_date = term.start_date + dt.timedelta(days=14)
        structure = finance_services.create_fee_structure(
            school_class=self.school_class,
            term=term,
            items=[
                {"label": label, "amount": amount, "ordering": index}
                for index, (label, amount) in enumerate(FEE_ITEMS, start=1)
            ],
            note="Seeded for the statement scenarios.",
            due_date=due_date,
            actor=self.officer,
        )
        self._count("created", "fee structure")
        return structure

    def _student(self, admission: str, first: str, last: str) -> Student:
        student = (
            Student.objects.select_related("user", "current_class")
            .filter(admission_number=admission)
            .first()
        )
        if student:
            if student.current_class_id != self.school_class.pk:
                raise CommandError(
                    f"{admission} already exists but sits in "
                    f"{student.current_class}; refusing to move an existing pupil."
                )
            self._count("reused", "pupil")
            return student

        student = students_services.create_student_with_guardian(
            admission_number=admission,
            first_name=first,
            last_name=last,
            email=f"{admission.lower()}@{EMAIL_DOMAIN}",
            current_class=self.school_class,
            must_change_password=False,
            send_credentials=False,
            actor=self.admin,
        )
        self._count("created", "pupil")
        return student

    def _draft_term3_invoice(self) -> None:
        """Leave one term-3 invoice in draft, without a ledger entry."""
        student = self.students["SCN-S8-DRAFT"]
        term = self.terms[2]
        if Invoice.objects.filter(student=student, term=term).exists():
            self._count("reused", "draft invoice")
            return

        structure = self.structures[2]
        invoice = Invoice.objects.create(
            student=student,
            term=term,
            fee_structure=structure,
            issued_date=self.today,
            due_date=structure.due_date,
            total_amount=structure.total,
            status=Invoice.Status.DRAFT,
        )
        InvoiceLine.objects.bulk_create(
            [
                InvoiceLine(
                    invoice=invoice,
                    fee_item=item,
                    label=item.label,
                    amount=item.amount,
                    due_date=structure.due_date,
                )
                for item in structure.items.all()
            ]
        )
        self._count("created", "draft invoice")

    def _receipts(self) -> None:
        for admission, suffix, days_ago, amount, allocations in RECEIPTS:
            reference = f"{admission}-{suffix}"
            if Payment.objects.filter(reference=reference).exists():
                self._count("reused", "receipt")
                continue

            student = self.students[admission]
            finance_services.record_payment(
                student=student,
                amount=Decimal(amount),
                method=Payment.Method.BANK,
                reference=reference,
                payment_date=self.today - dt.timedelta(days=days_ago),
                notes=f"Statement scenario receipt ({admission}).",
                allocations=[
                    {
                        "invoice": self._invoice(student, term_index),
                        "amount": Decimal(allocated),
                    }
                    for term_index, allocated in allocations
                ],
                recorded_by=self.officer,
            )
            self._count("created", "receipt")

    def _adjustments(self) -> None:
        for admission, term_index, kind, amount, reason in ADJUSTMENTS:
            student = self.students[admission]
            if Adjustment.objects.filter(student=student, reason=reason).exists():
                self._count("reused", "adjustment")
                continue
            finance_services.apply_adjustment(
                student=student,
                invoice=self._invoice(student, term_index),
                adjustment_type=kind,
                amount=Decimal(amount),
                reason=reason,
                actor=self.admin,
            )
            self._count("created", "adjustment")

    def _reversal(self) -> None:
        admission, suffix, reason = REVERSAL
        payment = Payment.objects.filter(reference=f"{admission}-{suffix}").first()
        if payment is None:
            raise CommandError(
                f"Receipt {admission}-{suffix} is missing, so it cannot be reversed."
            )
        if getattr(payment, "reversal", None) is not None:
            self._count("reused", "reversal")
            return
        finance_services.reverse_payment(
            payment=payment,
            reason=reason,
            approved_by=self.admin,
            actor=self.admin,
        )
        self._count("created", "reversal")

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _invoice(self, student: Student, term_index: int) -> Invoice:
        invoice = Invoice.objects.filter(
            student=student,
            term=self.terms[term_index - 1],
            status=Invoice.Status.POSTED,
        ).first()
        if invoice is None:
            raise CommandError(
                f"{student.admission_number} has no posted invoice for term "
                f"{term_index}; the billing run did not cover this pupil."
            )
        return invoice

    def _count(self, bucket: str, label: str) -> None:
        target = self.created if bucket == "created" else self.reused
        target[label] = target.get(label, 0) + 1

    # ─── Acceptance table ────────────────────────────────────────────────

    def _collect_ledger_rows(self) -> list[dict]:
        """
        One row per pupil per term, read from the rows themselves.

        ``paid`` counts the live allocations that sit on that term's posted
        invoices — the only per-term attribution the data actually carries.
        ``page`` is what ``student_statement(term=...)`` returns today, so the
        gap between the two is visible in the same table.
        """
        rows = []
        for admission, _first, _last, label in SCENARIOS:
            student = self.students[admission]
            credit = sum(
                (payment.unapplied_amount for payment in student.payments.all()),
                ZERO,
            )
            for position, term in enumerate(self.terms, start=1):
                posted_ids = list(
                    Invoice.objects.filter(
                        student=student,
                        term=term,
                        status=Invoice.Status.POSTED,
                    ).values_list("pk", flat=True)
                )
                billed = (
                    Invoice.objects.filter(pk__in=posted_ids).aggregate(
                        total=Sum("total_amount")
                    )["total"]
                    or ZERO
                )
                paid = PaymentAllocation.objects.filter(
                    invoice_id__in=posted_ids
                ).total()
                adjusted = (
                    Adjustment.objects.filter(
                        student=student,
                        invoice__term=term,
                        status=Adjustment.Status.POSTED,
                    ).aggregate(total=Sum("amount"))["total"]
                    or ZERO
                )
                draft_count = Invoice.objects.filter(
                    student=student, term=term, status=Invoice.Status.DRAFT
                ).count()
                page = finance_selectors.student_statement(
                    student_id=student.pk, term=term
                )["balance"]
                rows.append(
                    {
                        "admission": admission,
                        "name": student.user.full_name,
                        "label": label,
                        "term": term,
                        "billed": billed,
                        "paid": paid,
                        "adjusted": adjusted,
                        "open": billed - paid - adjusted,
                        "draft": draft_count,
                        "credit": credit,
                        "page": page,
                    }
                )
        return rows

    # ─── Output ──────────────────────────────────────────────────────────

    def write_report(self) -> None:
        write = self.stdout.write
        if self.dry_run:
            write(self.style.WARNING("Dry run - every write was rolled back."))
        write(
            f"Year {self.year.name} | class {SCENARIO_CLASS} | terms "
            + ", ".join(term.get_name_display() for term in self.terms)
        )
        write(f"Actors: finance={self.officer.username} admin={self.admin.username}")
        write("")
        write("Rows")
        for bucket, counts in (("created", self.created), ("reused", self.reused)):
            summary = ", ".join(f"{label} {count}" for label, count in counts.items())
            write(f"  {bucket:<8} {summary or 'nothing'}")
        write("")
        write("Scenario ledger as the rows stand (money in the school's currency)")
        write(
            "  open = billed - paid - adjusted for that term; page = the balance "
            "student_statement(term=...) returns today"
        )
        write("")
        current = None
        for row in self.ledger_rows:
            if row["admission"] != current:
                current = row["admission"]
                write(f"{row['admission']}  {row['name']} - {row['label']}")
            draft_note = f"   draft invoices: {row['draft']}" if row["draft"] else ""
            if row["credit"] and row["term"] == self.terms[-1]:
                draft_note += f"   credit held: {row['credit']:.2f}"
            write(
                f"  {row['term'].get_name_display():<7}"
                f" billed {row['billed']:>9.2f}"
                f"  paid {row['paid']:>9.2f}"
                f"  adjusted {row['adjusted']:>9.2f}"
                f"  open {row['open']:>9.2f}"
                f"  page {row['page']:>9.2f}"
                f"{draft_note}"
            )
        write("")
        write(
            "Scenario logins: username and password are both the admission number "
            f"(e.g. SCN-S1-PAID / SCN-S1-PAID). No password change is forced."
        )
