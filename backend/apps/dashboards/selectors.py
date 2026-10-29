"""
apps/dashboards/selectors.py
-----------------------------
Read-only aggregate selectors for the Director Dashboard (Module 5).

Design principles:
  - All heavy aggregates use Django ORM annotate/aggregate — no Python loops
    over querysets that hit the DB per row.
  - Every public function accepts only typed keyword arguments and returns
    plain Python dicts or typed QuerySets so the cache layer can wrap them
    transparently.
  - **No money is calculated here.** Every fee or books figure is asked for from
    ``apps.finance.interface``, the read contract other apps import; Finance's
    own queries live behind it in ``apps.finance.selectors``. Director screens
    that re-add the arithmetic drift from the Finance screens they are supposed
    to reconcile with — that is the bug this rule exists to prevent, and
    ``tests/test_director_finance_contract.py`` enforces it.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import (
    Avg,
    Count,
    Q,
    QuerySet,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# School-wide KPIs
# ---------------------------------------------------------------------------


def school_kpis(*, term) -> dict:
    """
    Single function that gathers every KPI card shown on the director home.
    Uses bulk aggregates — no per-row queries.

    Returns:
        student_count           int
        student_by_gender       dict[str, int]   e.g. {"M": 120, "F": 115}
        student_by_class        list[dict]       [{class_name, count, level_order}]
        teacher_count           int
        staff_count             int              all non-student, non-parent users
        fees_billed             Decimal
        fees_collected          Decimal
        fees_adjusted           Decimal
        fees_outstanding        Decimal
        collection_rate         Decimal | None   None when nothing is billed
        pass_rate               Decimal | None   only if published results exist
        subject_avg             Decimal | None
        term                    Term

    The four fee figures come straight from ``finance.fee_totals``, so
    they are the same numbers the Finance module's own pages print — including
    ``outstanding`` excluding amounts the school has waived, which a Director
    must not be chased for.
    """
    from apps.finance import interface as finance
    from apps.results.models import ResultSheet, ScoreEntry
    from apps.students.models import Student

    # ── Students ────────────────────────────────────────────────────────────
    active_statuses = [Student.Status.ACTIVE, Student.Status.REPEATING]
    students_qs = Student.objects.filter(status__in=active_statuses)

    student_count = students_qs.count()

    by_gender = students_qs.values("gender").annotate(n=Count("pk"))
    student_by_gender = {row["gender"] or "": row["n"] for row in by_gender}

    by_class = (
        students_qs.filter(current_class__isnull=False)
        .values(
            "current_class__name", "current_class__level_order", "current_class__pk"
        )
        .annotate(count=Count("pk"))
        .order_by("current_class__level_order", "current_class__name")
    )
    student_by_class = [
        {
            "class_name": r["current_class__name"],
            "class_id": r["current_class__pk"],
            "level_order": r["current_class__level_order"],
            "count": r["count"],
        }
        for r in by_class
    ]

    # ── Staff ────────────────────────────────────────────────────────────────
    teacher_count = User.objects.filter(role="teacher", is_active=True).count()
    staff_count = (
        User.objects.filter(is_active=True)
        .exclude(role__in=["student", "parent"])
        .count()
    )

    # ── Finance ──────────────────────────────────────────────────────────────
    fees = finance.fee_totals(term=term)

    # ── Results (published only) ──────────────────────────────────────────────
    published_entries = ScoreEntry.objects.filter(
        result_sheet__term=term,
        result_sheet__status=ResultSheet.Status.PUBLISHED,
        total__isnull=False,
    )

    agg = published_entries.aggregate(
        avg=Avg("total"),
        total_entries=Count("pk"),
    )
    subject_avg = round(float(agg["avg"]), 1) if agg["avg"] else None

    # Pass rate: % of entries with total >= pass_mark (default 50)
    pass_rate = None
    if agg["total_entries"]:
        try:
            from apps.results.models import GradingConfig

            config = GradingConfig.objects.filter(
                academic_year=term.academic_year
            ).first()
            pass_mark = config.pass_mark if config else Decimal(50)
        except Exception:  # noqa: BLE001
            pass_mark = Decimal(50)

        passing = published_entries.filter(total__gte=pass_mark).count()
        pass_rate = round(passing / agg["total_entries"] * 100, 1)

    return {
        "student_count": student_count,
        "student_by_gender": student_by_gender,
        "student_by_class": student_by_class,
        "teacher_count": teacher_count,
        "staff_count": staff_count,
        "fees_billed": fees["billed"],
        "fees_collected": fees["collected"],
        "fees_adjusted": fees["adjusted"],
        "fees_outstanding": fees["outstanding"],
        "collection_rate": fees["collection_rate"],
        "subject_avg": subject_avg,
        "pass_rate": pass_rate,
        "term": term,
    }


# ---------------------------------------------------------------------------
# Class performance
# ---------------------------------------------------------------------------


def class_performance(*, term_id: int) -> list[dict]:
    """
    Per-class aggregate over published ScoreEntries for a given term.
    Returns a list ordered by class level_order, each entry containing:
        class_id, class_name, level_order, student_count,
        avg_score, pass_rate, subject_rows (list)
    Uses a single DB pass per class via GROUP BY on ScoreEntry.
    """
    from apps.academics.models import SchoolClass, Term
    from apps.results.models import ResultSheet, ScoreEntry

    term = Term.objects.select_related("academic_year").get(pk=term_id)

    try:
        from apps.results.models import GradingConfig

        config = GradingConfig.objects.filter(academic_year=term.academic_year).first()
        pass_mark = config.pass_mark if config else Decimal(50)
    except Exception:  # noqa: BLE001
        pass_mark = Decimal(50)

    classes = SchoolClass.objects.filter(academic_year=term.academic_year).order_by(
        "level_order", "name"
    )

    result = []
    for cls in classes:
        # All published entries for this class/term
        entries_qs = ScoreEntry.objects.filter(
            result_sheet__school_class=cls,
            result_sheet__term_id=term_id,
            result_sheet__status=ResultSheet.Status.PUBLISHED,
            total__isnull=False,
        )
        agg = entries_qs.aggregate(
            avg=Avg("total"),
            total_entries=Count("pk"),
            passing=Count("pk", filter=Q(total__gte=pass_mark)),
        )

        if not agg["total_entries"]:
            result.append(
                {
                    "class_id": cls.pk,
                    "class_name": cls.name,
                    "level_order": cls.level_order,
                    "student_count": 0,
                    "avg_score": None,
                    "pass_rate": None,
                    "subject_rows": [],
                }
            )
            continue

        avg_score = round(float(agg["avg"]), 1)
        pass_rate = round(agg["passing"] / agg["total_entries"] * 100, 1)
        student_count = entries_qs.values("student_id").distinct().count()

        # Per-subject averages (single query with GROUP BY subject)
        subject_data = (
            entries_qs.values(
                "result_sheet__subject__pk",
                "result_sheet__subject__name",
                "result_sheet__subject__code",
            )
            .annotate(avg=Avg("total"), cnt=Count("pk"))
            .order_by("result_sheet__subject__name")
        )
        subject_rows = [
            {
                "subject_id": r["result_sheet__subject__pk"],
                "subject_name": r["result_sheet__subject__name"],
                "subject_code": r["result_sheet__subject__code"],
                "avg_score": round(float(r["avg"]), 1) if r["avg"] else None,
                "student_count": r["cnt"],
            }
            for r in subject_data
        ]

        result.append(
            {
                "class_id": cls.pk,
                "class_name": cls.name,
                "level_order": cls.level_order,
                "student_count": student_count,
                "avg_score": avg_score,
                "pass_rate": pass_rate,
                "subject_rows": subject_rows,
            }
        )
    return result


def class_performance_trend(*, class_id: int) -> list[dict]:
    """
    Per-term average for a single class across all terms that have published
    results. Returns list ordered chronologically for Chart.js trend chart.
    """
    from apps.results.models import ResultSheet, ScoreEntry

    rows = (
        ScoreEntry.objects.filter(
            result_sheet__school_class_id=class_id,
            result_sheet__status=ResultSheet.Status.PUBLISHED,
            total__isnull=False,
        )
        .values(
            "result_sheet__term__pk",
            "result_sheet__term__name",
            "result_sheet__term__academic_year__name",
            "result_sheet__term__academic_year__start_date",
        )
        .annotate(avg=Avg("total"), cnt=Count("pk"))
        .order_by(
            "result_sheet__term__academic_year__start_date",
            "result_sheet__term__name",
        )
    )
    return [
        {
            "term_id": r["result_sheet__term__pk"],
            "term_label": f"{r['result_sheet__term__name']} {r['result_sheet__term__academic_year__name']}",
            "avg_score": round(float(r["avg"]), 1) if r["avg"] else None,
            "entry_count": r["cnt"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Class fee summary
# ---------------------------------------------------------------------------


def class_fee_summary(*, term_id: int) -> list[dict]:
    """
    Per-class fee billed / collected / adjusted / outstanding.

    A thin call into ``finance.class_fee_rows`` — three GROUP BYs for
    the whole school, not a loop — kept here because the Director cache layer
    and the fee pages import it by this name. Renamed the payment column
    ``collected``, matching the Finance interface and the Director brief's own
    vocabulary.
    """
    from apps.academics.models import Term
    from apps.finance import interface as finance

    return finance.class_fee_rows(term=Term.objects.get(pk=term_id))


def class_fee_debtors(*, class_id: int, term_id: int) -> list[dict]:
    """
    Pupils in one class still owing money this term, largest first.

    Delegates to ``finance.student_fee_rows``, which is also what the
    Finance module's own class page lists, so a name on the Director's debtor
    list and the amount beside it cannot disagree with Finance's.
    """
    from apps.academics.models import Term
    from apps.finance import interface as finance

    return finance.student_fee_rows(
        term=Term.objects.get(pk=term_id), class_id=class_id, only_debtors=True
    )


# ---------------------------------------------------------------------------
# Staff directory
# ---------------------------------------------------------------------------


def staff_directory(*, search: str = "", role: str = "") -> QuerySet:
    """
    All non-student, non-parent active users with role-specific profile
    annotations. Single query via select_related.
    """
    qs = (
        User.objects.filter(is_active=True)
        .exclude(role__in=["student", "parent"])
        .select_related(
            "teacher_profile",
            "admin_profile",
            "finance_profile",
            "director_profile",
        )
        .prefetch_related(
            "teacher_profile__assignments__school_class",
            "teacher_profile__assignments__subject",
        )
        .order_by("role", "last_name", "first_name")
    )
    if role:
        qs = qs.filter(role=role)
    if search:
        qs = qs.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
        )
    return qs


# ---------------------------------------------------------------------------
# Books of account helpers
# ---------------------------------------------------------------------------


def income_vs_expenses_by_month(*, year_id: int) -> list[dict]:
    """
    Monthly income vs expense totals for a full academic year.

    Asks ``finance.monthly_income_expense`` for the ledger's own
    figures over the year's date range; the float columns are for Chart.js.
    """
    from apps.academics.models import AcademicYear
    from apps.finance import interface as finance

    year = AcademicYear.objects.get(pk=year_id)
    return finance.monthly_income_expense(
        date_from=year.start_date, date_to=year.end_date
    )
