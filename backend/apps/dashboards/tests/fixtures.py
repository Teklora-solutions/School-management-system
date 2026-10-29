"""
apps/dashboards/tests/fixtures.py
-----------------------------------
Shared test-fixture helpers for Director Dashboard tests.

All helpers are plain functions (not pytest fixtures) so they work with
Django's TestCase.setUp() without needing pytest-django.  Each helper
accepts keyword overrides so individual tests can customise what they create.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from apps.academics.models import AcademicYear, SchoolClass, Subject, Term
from apps.accounts.models import User
from apps.finance.chart_of_accounts import CHART_OF_ACCOUNTS
from apps.finance.models import (
    Account,
    FeeItem,
    FeeStructure,
    Invoice,
    Payment,
    PaymentAllocation,
)
from apps.results.models import (
    GradeBand,
    GradingConfig,
    ResultSheet,
    ScoreEntry,
)
from apps.students.models import Student

# ---------------------------------------------------------------------------
# Academic structure
# ---------------------------------------------------------------------------


def make_year(name: str = "2025/2026", **kwargs) -> AcademicYear:
    defaults = {
        "name": name,
        "start_date": datetime.date(2025, 9, 1),
        "end_date": datetime.date(2026, 7, 31),
        "is_current": True,
    }
    defaults.update(kwargs)
    return AcademicYear.objects.create(**defaults)


def make_term(year: AcademicYear, name: str = "term_1", **kwargs) -> Term:
    defaults = {
        "academic_year": year,
        "name": name,
        "start_date": datetime.date(2025, 9, 1),
        "end_date": datetime.date(2025, 12, 20),
        "is_current": True,
    }
    defaults.update(kwargs)
    return Term.objects.create(**defaults)


def make_class(
    year: AcademicYear, name: str = "Grade 5", level_order: int = 5, **kwargs
) -> SchoolClass:
    defaults = {
        "academic_year": year,
        "name": name,
        "level_order": level_order,
    }
    defaults.update(kwargs)
    return SchoolClass.objects.create(**defaults)


def make_subject(code: str = "MATH", name: str = "Mathematics") -> Subject:
    return Subject.objects.get_or_create(code=code, defaults={"name": name})[0]


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

_user_counter = 0


def make_user(role: str, username: str | None = None, **kwargs) -> User:
    global _user_counter
    _user_counter += 1
    uname = username or f"user_{role}_{_user_counter}"
    defaults = {
        "username": uname,
        "email": f"{uname}@test.school",
        "role": role,
        "must_change_password": False,
        "first_name": "Test",
        "last_name": role.title(),
    }
    defaults.update(kwargs)
    u = User.objects.create_user(password="testpass123", **defaults)
    return u


def make_director(**kwargs) -> User:
    return make_user("director", **kwargs)


def make_student_user(
    school_class: SchoolClass,
    admission_number: str | None = None,
    gender: str = "M",
    **kwargs,
) -> Student:
    global _user_counter
    _user_counter += 1
    adm = admission_number or f"ADM{_user_counter:04d}"
    user = make_user("student", username=f"stu_{adm}")
    student = Student.objects.create(
        user=user,
        admission_number=adm,
        current_class=school_class,
        gender=gender,
        status=Student.Status.ACTIVE,
    )
    return student


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------


def make_accounts() -> dict:
    """
    The system chart of accounts, keyed by code.

    This used to declare six accounts of its own — a second chart that drifted
    from the one the migrations seed, which is how a test came to assert that a
    fee waiver was an operating expense. It now reads
    ``finance.chart_of_accounts``, the same list the migration and
    ``services.seed_accounts()`` use.
    """
    acc = {}
    for spec in CHART_OF_ACCOUNTS:
        obj, _ = Account.objects.get_or_create(
            code=spec["code"],
            defaults={
                "name": spec["name"],
                "type": spec["type"],
                "normal_side": spec["normal_side"],
                "is_system": spec["is_system"],
                "is_contra": spec["is_contra"],
                "description": spec["description"],
            },
        )
        acc[spec["code"]] = obj
    for spec in CHART_OF_ACCOUNTS:
        if spec["parent_code"]:
            child, parent = acc[spec["code"]], acc[spec["parent_code"]]
            if child.parent_id != parent.pk:
                child.parent = parent
                child.save(update_fields=["parent", "updated_at"])
    return acc


def make_fee_structure(
    school_class: SchoolClass, term: Term, amount: Decimal = Decimal("500.00")
) -> FeeStructure:
    fs = FeeStructure.objects.create(
        school_class=school_class,
        term=term,
        version=1,
        is_active=True,
    )
    FeeItem.objects.create(structure=fs, label="Tuition", amount=amount, ordering=1)
    return fs


def make_posted_invoice(
    student: Student,
    term: Term,
    fee_structure: FeeStructure,
    amount: Decimal = Decimal("500.00"),
) -> Invoice:
    return Invoice.objects.create(
        student=student,
        term=term,
        fee_structure=fee_structure,
        issued_date=datetime.date(2025, 9, 5),
        total_amount=amount,
        status=Invoice.Status.POSTED,
    )


def make_payment_allocation(invoice: Invoice, amount: Decimal) -> PaymentAllocation:
    """Create a payment + allocation directly (bypasses services to keep tests fast)."""
    global _user_counter
    _user_counter += 1
    payment = Payment.objects.create(
        student=invoice.student,
        reference=f"REF-TEST-{_user_counter}",
        payment_date=datetime.date(2025, 9, 10),
        method=Payment.Method.BANK,
        amount=amount,
    )
    return PaymentAllocation.objects.create(
        payment=payment,
        invoice=invoice,
        allocated_amount=amount,
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def make_grading_config(
    year: AcademicYear, pass_mark: Decimal = Decimal(50)
) -> GradingConfig:
    config, _ = GradingConfig.objects.get_or_create(
        academic_year=year,
        defaults={"pass_mark": pass_mark},
    )
    # Grade bands 0-100
    for lo, hi, grade in [(0, 49, "F"), (50, 69, "C"), (70, 84, "B"), (85, 100, "A")]:
        GradeBand.objects.get_or_create(
            academic_year=year,
            grade=grade,
            defaults={"min_score": lo, "max_score": hi, "remark": grade, "points": 0},
        )
    return config


def make_published_score(
    school_class: SchoolClass,
    subject: Subject,
    term: Term,
    student: Student,
    total: Decimal = Decimal("65.00"),
) -> ScoreEntry:
    sheet, _ = ResultSheet.objects.get_or_create(
        school_class=school_class,
        subject=subject,
        term=term,
        stream=None,
        defaults={"status": ResultSheet.Status.PUBLISHED},
    )
    if sheet.status != ResultSheet.Status.PUBLISHED:
        sheet.status = ResultSheet.Status.PUBLISHED
        sheet.save(update_fields=["status"])
    entry, _ = ScoreEntry.objects.update_or_create(
        result_sheet=sheet,
        student=student,
        defaults={
            "raw_score": total,
            "total": total,
            "grade": "C" if total < 70 else "B",
            "remark": "Pass",
            "points": Decimal(2),
        },
    )
    return entry
