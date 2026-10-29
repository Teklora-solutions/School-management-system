"""
apps/finance/services.py
------------------------
All write operations for the finance module.

Architecture:
  - All public functions are @transaction.atomic.
  - _post_journal_entry() is the ONLY function that creates JournalEntry/JournalLine
    rows. It asserts sum(debit) == sum(credit) BEFORE any INSERT, quantizes every
    amount through ``apps.core.money.to_money``, and refuses the posting if the
    date falls in a closed period or outside the back-dating window.
  - Idempotency is enforced at the DB level (unique constraints) and re-checked
    in service code for a clean error message before the DB raises.
  - Amounts never reach here as ``float``; a form gives a string, an import gives
    a string, and both go through ``to_money``.
"""

from __future__ import annotations

import io
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, Sum
from django.utils import timezone

from apps.core.audit_log import log_action
from apps.core.events import record_event
from apps.core.money import ZERO, money_sum, to_money

from .chart_of_accounts import (
    BAD_DEBT_CODE,
    BANK_CODE,
    CASH_CODE,
    CHART_OF_ACCOUNTS,
    CREDIT_BALANCES_CODE,
    DISCOUNTS_CODE,
    FEE_INCOME_CODE,
    FEES_RECEIVABLE_CODE,
    MOBILE_MONEY_CODE,
)
from .models import (
    Account,
    Adjustment,
    ApprovalRequest,
    Budget,
    Expense,
    FeeDiscountRule,
    FeeItem,
    FeeStructure,
    FeeStructureUploadJob,
    FinanceSettings,
    FinancialPeriod,
    Invoice,
    InvoiceLine,
    JournalEntry,
    JournalLine,
    NumberSeries,
    Payment,
    PaymentAllocation,
    PaymentPlan,
    PaymentPlanInstallment,
    StudentFeeElection,
    Vendor,
)

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.academics.models import SchoolClass, Term
    from apps.accounts.models import User
    from apps.students.models import Student

# ``chart_of_accounts`` is the only place a code is written down. These names are
# imported because this module's own functions use them, not to re-export them —
# a reader that wants a code imports it from ``chart_of_accounts`` directly, so
# an unused-import cleanup here cannot break a caller three apps away.
SEED_ACCOUNTS = CHART_OF_ACCOUNTS

#: The money column shape, so an aggregate answers 0.00 instead of ``None`` and
#: never comes back with more decimals than a receipt can print.
_MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_account(code: str) -> Account:
    try:
        return Account.objects.get(code=code)
    except Account.DoesNotExist:
        raise RuntimeError(
            f"System account with code '{code}' not found. "
            "Run the seed data migration first."
        )


def _is_admin(actor) -> bool:
    """
    Whether ``actor`` can sign off their own gated action.

    Both the role tag and superuser count, because the school runs on role tags
    and a developer testing on a superuser account must not be locked out by the
    gate. Used for the approval question only — period reopening has its own
    check because it also needs a reason, which is not this function's job.
    """
    if actor is None:
        return False
    return getattr(actor, "role", "") == "admin" or getattr(actor, "is_superuser", False)


@transaction.atomic
def _post_journal_entry(
    *,
    reference: str,
    entry_date: date,
    description: str,
    posted_by: User | None,
    lines: list[
        dict
    ],  # [{"account": Account, "debit": Decimal, "credit": Decimal}, ...]
    source_type: str,
    source_id: str = "",
    reverses: JournalEntry | None = None,
    settings_row: FinanceSettings | None = None,
) -> JournalEntry:
    """
    Create one balanced journal entry.

    `lines` is a list of dicts:
        {"account": Account, "debit": Decimal (or 0), "credit": Decimal (or 0)}

    Raises ``ValidationError`` if the entry is unbalanced, empty, or dated
    somewhere the school is not allowed to post — before anything is INSERTed,
    so a rejected entry leaves no trace and no half-entry.

    This is the only writer of ledger rows in the module, which is what makes the
    period lock and the rounding rule real rather than advisory: every path —
    invoice, receipt, reversal, adjustment, expense, import — comes through here.
    """
    ensure_postable(entry_date, settings_row=settings_row)

    totals = [
        {
            "account": line["account"],
            "debit": to_money(line.get("debit", 0)),
            "credit": to_money(line.get("credit", 0)),
        }
        for line in lines
    ]
    total_debit = sum((row["debit"] for row in totals), ZERO)
    total_credit = sum((row["credit"] for row in totals), ZERO)

    if total_debit != total_credit:
        raise ValidationError(
            f"Journal entry '{reference}' is unbalanced: "
            f"debit={total_debit}, credit={total_credit}."
        )

    if total_debit == 0:
        raise ValidationError(f"Journal entry '{reference}' has no amounts.")

    entry = JournalEntry.objects.create(
        reference=reference,
        entry_date=entry_date,
        description=description,
        posted_by=posted_by,
        source_type=source_type,
        source_id=str(source_id),
        reverses=reverses,
    )
    JournalLine.objects.bulk_create(
        [
            JournalLine(
                entry=entry,
                account=row["account"],
                debit_amount=row["debit"],
                credit_amount=row["credit"],
            )
            for row in totals
        ]
    )
    return entry


# ---------------------------------------------------------------------------
# Chart of accounts
# ---------------------------------------------------------------------------


def _chart_row(row: dict) -> tuple[dict, str | None]:
    """Split one ``CHART_OF_ACCOUNTS`` entry into (field dict, parent code)."""
    data = {k: v for k, v in row.items() if k != "parent_code"}
    return data, row["parent_code"]


@transaction.atomic
def seed_accounts() -> None:
    """Idempotent: create the system chart if rows are missing.

    Parents are linked in a second pass because a row's parent may not exist yet
    when the row itself is created. Existing system accounts are re-synced to the
    chart, which is what let ``5000 Discounts`` move from expense to contra
    income without hand-editing a database.
    """
    by_code: dict[str, tuple[Account, str | None]] = {}
    for row in CHART_OF_ACCOUNTS:
        data, parent_code = _chart_row(row)
        account, _ = Account.objects.get_or_create(code=data["code"], defaults=data)
        changed = [
            key
            for key, value in data.items()
            if key != "code" and getattr(account, key) != value
        ]
        if changed:
            for key in changed:
                setattr(account, key, data[key])
            account.save(update_fields=[*changed, "updated_at"])
        by_code[account.code] = (account, parent_code)

    for account, parent_code in by_code.values():
        parent_entry = by_code.get(parent_code) if parent_code else None
        wanted = parent_entry[0] if parent_entry else None
        if account.parent_id != (wanted.pk if wanted else None):
            account.parent = wanted
            account.save(update_fields=["parent", "updated_at"])


# ---------------------------------------------------------------------------
# Where a payment settles
# ---------------------------------------------------------------------------

#: Method → the asset account the money lands in. Kept as data, not as an
#: ``if method == cash else bank`` chain, because that chain was how a
#: mobile-money receipt silently ended up in the Bank account, and because the
#: school may add a method without editing arithmetic.
SETTLEMENT_ACCOUNT_CODES = {
    Payment.Method.BANK: BANK_CODE,
    Payment.Method.CHEQUE: BANK_CODE,
    Payment.Method.MOBILE_MONEY: MOBILE_MONEY_CODE,
    Payment.Method.OTHER: BANK_CODE,
}


def _settlement_account(method: str) -> Account:
    try:
        code = SETTLEMENT_ACCOUNT_CODES[method]
    except KeyError:
        raise ValidationError(
            {"method": f"'{method}' has no account to settle into. Add it to "
                       "the settlement map before recording receipts this way."}
        ) from None
    return _get_account(code)


# ---------------------------------------------------------------------------
# Document numbering
# ---------------------------------------------------------------------------

#: How many times a number may be contested before this is treated as a bug
#: rather than a race. One school, one process: hitting this means something is
#: looping, and a loop that mints nothing is safer than one that mints twice.
SERIES_RETRIES = 25


def _series_prefix(kind: str, settings_row: FinanceSettings) -> str:
    """The configured prefix, with the shipped default if the field was cleared."""
    if kind == NumberSeries.Kind.RECEIPT:
        configured, fallback = settings_row.receipt_prefix, "RCP"
    elif kind == NumberSeries.Kind.EXPENSE:
        configured, fallback = settings_row.expense_prefix, "EXP"
    else:
        configured, fallback = settings_row.invoice_prefix, "INV"
    return (configured or "").strip().upper()[:8] or fallback


def next_document_number(
    kind: str,
    *,
    day: date | None = None,
    settings_row: FinanceSettings | None = None,
) -> str:
    """
    Take the next invoice, receipt or expense-claim number, gap-free and unique.

    An auditor reads a receipts register by its sequence: after 00041 comes
    00042, and a hole is a question about money. A random ``RCP-<uuid>`` cannot
    answer that question, so documents are numbered from ``NumberSeries``.

    The number is taken with a compare-and-set (read, then
    ``UPDATE ... WHERE next_number = <what was read>``). A second writer that
    loses re-reads and takes the following number, so nothing is skipped and
    nothing is issued twice. ``select_for_update`` is deliberately not used: this
    project runs on SQLite, which cannot lock a row, and silently asking for a
    lock the database does not take is how a race comes back after a move.

    Call it inside the posting's transaction and pass the document's own date: a
    receipt rolled back after taking its number hands the number straight back
    (no gap), and a back-dated receipt lands in the year it belongs to.
    """
    if kind not in NumberSeries.Kind.values:
        raise ValidationError(f"'{kind}' is not a document series.")

    day = day or timezone.localdate()
    settings_row = settings_row or FinanceSettings.load()

    for _attempt in range(SERIES_RETRIES):
        series = NumberSeries.objects.filter(kind=kind, year=day.year).first()
        if series is None:
            series, created = NumberSeries.objects.get_or_create(
                kind=kind,
                year=day.year,
                defaults={"prefix": _series_prefix(kind, settings_row)},
            )
            if not created:
                continue
        taken = NumberSeries.objects.filter(
            pk=series.pk, next_number=series.next_number
        ).update(next_number=series.next_number + 1)
        if taken:
            return series.label(series.next_number)

    raise ValidationError(
        f"Could not take a {kind} number for {day.year}. Record the document "
        "again — nothing was posted while it was contested."
    )


# ---------------------------------------------------------------------------
# Period control
# ---------------------------------------------------------------------------


def month_period(day) -> FinancialPeriod:
    """
    The month period for ``day``, created open if it does not exist yet.

    A period row is only created when something needs to say something about it
    — a close, or a report that names it. An open period and no period at all
    mean the same thing to the posting guard, so a school with three years of
    history does not need 36 rows to prove its months are open.
    """
    period, _ = FinancialPeriod.objects.get_or_create(
        kind=FinancialPeriod.Kind.MONTH,
        year=day.year,
        month=day.month,
        defaults={"start_date": day.replace(day=1), "end_date": _month_end(day)},
    )
    return period


def _month_end(day):
    from calendar import monthrange

    return day.replace(day=monthrange(day.year, day.month)[1])


def closed_period_covering(day) -> FinancialPeriod | None:
    """
    The closed period that contains ``day``, if any — one indexed query.

    Term periods are checked before month periods because the message a bursar
    needs is "this term is closed", not the row that happens to sort first.
    """
    return (
        FinancialPeriod.objects.filter(
            status=FinancialPeriod.Status.CLOSED,
            start_date__lte=day,
            end_date__gte=day,
        )
        .order_by("-kind")  # term before month, alphabetically
        .first()
    )


def _period_calendar_row(settings_row):
    """
    Whether this school has started declaring accounting periods — once per settings row.

    Cached on the ``FinanceSettings`` instance rather than the process, so a bulk
    job that hoists one settings row pays one query for the whole run, and a
    period closed in another session is seen by the next request's next row.
    """
    cached = getattr(settings_row, "_period_calendar_exists", None)
    if cached is None:
        cached = FinancialPeriod.objects.exists()
        settings_row._period_calendar_exists = cached
    return cached


def ensure_postable(
    entry_date, *, settings_row: FinanceSettings | None = None
) -> None:
    """
    Refuse a posting that a closed period forbids, or that is back-dated too far.

    Called by ``_post_journal_entry``, which is the only writer of ledger rows,
    so no service can skip it — not invoicing, not a payment, not an import, not
    a reversal. An Admin is not exempt: the way into a closed period is to reopen
    it, which is logged, named and evented. That distinction is the whole value
    of the lock — an exemption that leaves no trace is not a control.

    The back-dating window switches on with the calendar. A school that has not
    closed a single period has made no promise about which windows its books
    cover, and refusing every older date would mean a first receipt for last
    month fails for a reason the bursar cannot see on any screen. The day the
    school declares its first period, the window starts to mean something.

    ``settings_row`` lets a loop (a whole school's invoices, one posting date)
    read the rules once instead of once per row.
    """
    settings_row = settings_row or FinanceSettings.load()

    if settings_row.period_lock_enabled:
        closed = closed_period_covering(entry_date)
        if closed:
            raise ValidationError(
                {
                    "entry_date": (
                        f"That date falls in a closed "
                        f"{closed.get_kind_display().lower()} period "
                        f"({closed.start_date} to {closed.end_date}). Reopen it "
                        "with a reason before posting into it."
                    )
                }
            )

    if not _period_calendar_row(settings_row):
        return

    window = settings_row.backdating_days
    if window is not None:
        earliest = timezone.localdate() - timedelta(days=window)
        if entry_date < earliest:
            raise ValidationError(
                {
                    "entry_date": f"Postings may not be dated before {earliest} "
                    f"(the back-dating window is {window} days). Ask an Admin to "
                    "widen it if this correction is genuine."
                }
            )


@transaction.atomic
def close_period(
    *,
    period: FinancialPeriod,
    actor: User | None = None,
    note: str = "",
    request: HttpRequest | None = None,
) -> FinancialPeriod:
    """
    Lock a period so nothing more can be posted into it.

    Finance closes its own months; only Admin or Director may close a term, since
    closing a term says "this term's billing is finished", which is not a
    cashier's call to make.
    """
    if period.status == FinancialPeriod.Status.CLOSED:
        raise ValidationError("This period is already closed.")
    role = getattr(actor, "role", "")
    if role not in ("", "finance", "admin", "director"):
        raise ValidationError("Only Finance, an Admin or a Director can close a period.")
    if period.kind == FinancialPeriod.Kind.TERM and role not in ("admin", "director"):
        raise ValidationError("Only an Admin or Director can close a term period.")

    period.status = FinancialPeriod.Status.CLOSED
    period.closed_by = actor
    period.closed_at = timezone.now()
    period.close_note = note.strip()
    period.save(
        update_fields=["status", "closed_by", "closed_at", "close_note", "updated_at"]
    )

    log_action(
        actor=actor,
        action="finance.period_closed",
        content_type="finance.FinancialPeriod",
        object_id=period.pk,
        change_summary={
            "kind": period.kind,
            "window": f"{period.start_date}→{period.end_date}",
            "note": note.strip(),
        },
        request=request,
    )
    record_event(
        event_type="finance.period_closed",
        actor=actor,
        data={
            "kind": period.get_kind_display(),
            "start": str(period.start_date),
            "end": str(period.end_date),
        },
        request=request,
    )
    return period


@transaction.atomic
def reopen_period(
    *,
    period: FinancialPeriod,
    actor: User | None = None,
    reason: str,
    request: HttpRequest | None = None,
) -> FinancialPeriod:
    """
    Reopen a closed period. Admin only, with a reason, always on the record.

    Reopening makes a report that was already printed and handed to someone
    wrong, which is exactly why it cannot be done quietly or by the person who
    wants to post the entry.
    """
    if not reason.strip():
        raise ValidationError({"reason": "A reopening needs a reason."})
    if getattr(actor, "role", "") != "admin" and not getattr(actor, "is_superuser", False):
        raise ValidationError("Only an Admin can reopen a closed period.")
    if period.status != FinancialPeriod.Status.CLOSED:
        raise ValidationError("This period is not closed.")

    period.status = FinancialPeriod.Status.OPEN
    period.reopened_by = actor
    period.reopened_at = timezone.now()
    period.reopen_reason = reason.strip()
    period.save(
        update_fields=[
            "status",
            "reopened_by",
            "reopened_at",
            "reopen_reason",
            "updated_at",
        ]
    )

    log_action(
        actor=actor,
        action="finance.period_reopened",
        content_type="finance.FinancialPeriod",
        object_id=period.pk,
        change_summary={
            "kind": period.kind,
            "window": f"{period.start_date}→{period.end_date}",
            "reason": reason.strip(),
        },
        request=request,
    )
    record_event(
        event_type="finance.period_reopened",
        actor=actor,
        data={
            "kind": period.get_kind_display(),
            "start": str(period.start_date),
            "end": str(period.end_date),
            "reason": reason.strip(),
        },
        request=request,
    )
    return period


# ---------------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------------


@transaction.atomic
def create_expense_account(
    *,
    code: str,
    name: str,
    description: str = "",
    parent: Account | None = None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Account:
    """Finance / Admin only: add a new expense category account."""
    code = code.strip().upper()
    if Account.objects.filter(code=code).exists():
        raise ValidationError({"code": f"Account code '{code}' already exists."})
    account = Account.objects.create(
        code=code,
        name=name.strip(),
        type=Account.Type.EXPENSE,
        normal_side=Account.NormalSide.DEBIT,
        is_system=False,
        parent=parent,
        description=description.strip(),
    )
    log_action(
        actor=actor,
        action="finance.account_created",
        content_type="finance.Account",
        object_id=account.pk,
        change_summary={"code": code, "name": name, "parent": parent.code if parent else None},
        request=request,
    )
    return account


@transaction.atomic
def update_account(
    *,
    account: Account,
    name: str | None = None,
    description: str | None = None,
    parent: Account | None = None,
    is_active: bool | None = None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Account:
    """
    Rename, regroup or switch an account on/off. System accounts are protected.

    A system account's *name* is part of the contract the reports are written
    against — "Fees Receivable" renamed to something else does not break the
    posting (that is by code) but it does break the sentence a bursar reads, so
    the protection is real rather than cosmetic. Type and normal side never
    change through this door at all: retyping an account with history would
    re-file every past posting under a different heading.
    """
    before = {"name": account.name, "is_active": account.is_active}
    if account.is_system and (name is not None or is_active is not None):
        raise ValidationError(
            f"'{account.name}' is a system account. Its name and availability are "
            "fixed; change the description if the wording needs clarifying."
        )
    if name is not None:
        account.name = name.strip()
    if description is not None:
        account.description = description.strip()
    if parent is not None:
        account.parent = parent
    if is_active is not None:
        account.is_active = bool(is_active)
    account.full_clean(exclude=["type", "normal_side", "is_system", "is_contra"])
    account.save(update_fields=["name", "description", "parent", "is_active", "updated_at"])

    log_action(
        actor=actor,
        action="finance.account_updated",
        content_type="finance.Account",
        object_id=account.pk,
        change_summary={
            "before": before,
            "after": {
                "name": account.name,
                "is_active": account.is_active,
                "parent": account.parent.code if account.parent_id else None,
            },
        },
        request=request,
    )
    return account


@transaction.atomic
def retire_account(*, account: Account, actor: User | None = None, request=None) -> Account:
    """
    Take an account out of use. Never deletes.

    Ledger history points at accounts, so deletion would either orphan rows or
    be blocked by the PROTECT on ``JournalLine.account`` — deactivating is the
    operation that actually exists.
    """
    if account.is_system:
        raise ValidationError("System accounts cannot be retired.")
    if account.journal_lines.exists():
        raise ValidationError(
            f"'{account.name}' has ledger history. Retire it only after the "
            "postings have been moved, or leave it active and unused."
        )
    return update_account(
        account=account, is_active=False, actor=actor, request=request
    )


# ---------------------------------------------------------------------------
# Finance settings
# ---------------------------------------------------------------------------

#: Fields an Admin alone may change, because each one decides what the Finance
#: Officer is allowed to post without a second signature.
ADMIN_ONLY_SETTINGS = {
    "backdating_days",
    "period_lock_enabled",
    "approval_reversal_threshold",
    "approval_expense_threshold",
    "write_off_always_needs_approval",
}


@transaction.atomic
def update_finance_settings(
    *,
    actor: User | None = None,
    request: HttpRequest | None = None,
    **changes,
) -> FinanceSettings:
    """
    Change the finance rules, with the before-values on the record.

    Every one of these fields decides what may be posted, or how much someone may
    post alone, so a change without a trail is a control that quietly stopped
    existing. Money-shaped fields are quantized; the thresholds are compared
    against amounts the same way afterwards.
    """
    row = FinanceSettings.load()
    role = getattr(actor, "role", "")
    if role not in ("admin", "") and any(k in ADMIN_ONLY_SETTINGS for k in changes):
        raise ValidationError(
            "Only an Admin can change the posting window, the period lock or the "
            "approval thresholds."
        )
    unknown = set(changes) - {f.name for f in FinanceSettings._meta.get_fields()}
    if unknown:
        raise ValidationError(f"Unknown finance setting(s): {', '.join(sorted(unknown))}")

    before, after = {}, {}
    for field, value in changes.items():
        before[field] = str(getattr(row, field))
        if isinstance(getattr(row, field), Decimal) or field.endswith("threshold"):
            value = to_money(value)
        setattr(row, field, value)
        after[field] = str(value)
    row.save(update_fields=[*changes.keys(), "updated_at"])

    log_action(
        actor=actor,
        action="finance.settings_updated",
        content_type="finance.FinanceSettings",
        object_id=row.pk,
        change_summary={"before": before, "after": after},
        request=request,
    )
    return row


# ---------------------------------------------------------------------------
# Fee Structures
# ---------------------------------------------------------------------------


@transaction.atomic
def create_fee_structure(
    *,
    school_class: SchoolClass,
    term: Term,
    items: list[dict],  # [{"label": str, "amount": Decimal, "ordering": int}, ...]
    note: str = "",
    due_date: date | None = None,
    copied_from: FeeStructure | None = None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> FeeStructure:
    """
    Create a new versioned fee structure for a class+term.
    If one already exists, bumps the version and deactivates the old one.

    An item dict may also carry ``compulsory`` and ``due_date``; both are read
    with ``.get`` so the callers written before F3b — the form and the Excel
    commit — keep working unchanged.
    """
    latest = (
        FeeStructure.objects.filter(school_class=school_class, term=term)
        .order_by("-version")
        .first()
    )
    version = (latest.version + 1) if latest else 1
    if latest:
        FeeStructure.objects.filter(school_class=school_class, term=term).update(
            is_active=False
        )

    structure = FeeStructure.objects.create(
        school_class=school_class,
        term=term,
        version=version,
        is_active=True,
        due_date=due_date,
        note=note.strip(),
        copied_from=copied_from,
        created_by=actor,
    )
    FeeItem.objects.bulk_create(
        [
            FeeItem(
                structure=structure,
                label=item["label"].strip(),
                amount=to_money(item["amount"]),
                ordering=item.get("ordering", idx),
                compulsory=item.get("compulsory", True),
                due_date=item.get("due_date"),
            )
            for idx, item in enumerate(items)
        ]
    )
    log_action(
        actor=actor,
        action="finance.fee_structure_created",
        content_type="finance.FeeStructure",
        object_id=structure.pk,
        change_summary={
            "class": school_class.name,
            "term": str(term),
            "version": version,
            "total": str(structure.total),
            "copied_from": str(copied_from) if copied_from else None,
        },
        request=request,
    )
    return structure


@transaction.atomic
def copy_fee_structure(
    *,
    source: FeeStructure,
    term: Term,
    school_class: SchoolClass | None = None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> FeeStructure:
    """
    Reuse one class's fee list for another term (or another class).

    B2 asks for "copy a structure to the next term/year" because typing the same
    eleven items again is how a transposed amount reaches a bill. The copy is a
    fresh set of rows — never a shared reference — so editing the new term cannot
    move an invoice that was already posted under the old one.

    The source's ``due_date`` is deliberately not copied: a deadline from last
    term is a wrong deadline, and a blank one is a question the officer answers.
    """
    target_class = school_class or source.school_class
    items = [
        {
            "label": item.label,
            "amount": item.amount,
            "ordering": item.ordering,
            "compulsory": item.compulsory,
        }
        for item in source.items.all()
    ]
    if not items:
        raise ValidationError(
            {"source": f"{source} has no fee items to copy — bill it first."}
        )
    note = source.note.strip()
    structure = create_fee_structure(
        school_class=target_class,
        term=term,
        items=items,
        note=(f"Copied from {source}. {note}".strip() if note else f"Copied from {source}."),
        copied_from=source,
        actor=actor,
        request=request,
    )
    record_event(
        event_type="finance.fee_structure_copied",
        actor=actor,
        data={
            "from": str(source),
            "to": str(structure),
            "class": target_class.name,
            "total": str(structure.total),
        },
        request=request,
    )
    return structure


# ---------------------------------------------------------------------------
# Student-specific billing variations
# ---------------------------------------------------------------------------


@transaction.atomic
def set_student_fee_item(
    *,
    student: Student,
    fee_item: FeeItem,
    included: bool,
    note: str = "",
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> StudentFeeElection:
    """
    Say whether this pupil is charged one fee item.

    A compulsory item needs no row: it is on every bill in the class unless
    someone says otherwise, and an election that opts a pupil *out* of a
    compulsory item would be a waiver wearing a costume — that belongs in
    ``apply_adjustment``, where it is audited and posted to the ledger.

    Changing an election after the invoice has been posted does not restate the
    invoice, for the same reason a structure edit does not: the version that was
    billed is the record of what was billed.
    """
    if fee_item.compulsory and not included:
        raise ValidationError(
            {
                "included": f"{fee_item.label} is compulsory for {fee_item.structure}. "
                "Take it off this pupil's bill with a waiver adjustment instead."
            }
        )
    election, _ = StudentFeeElection.objects.update_or_create(
        student=student,
        fee_item=fee_item,
        defaults={
            "included": included,
            "noted_by": actor,
            "note": note.strip(),
        },
    )
    log_action(
        actor=actor,
        action="finance.fee_election_set",
        content_type="finance.StudentFeeElection",
        object_id=election.pk,
        change_summary={
            "student": student.admission_number,
            "item": fee_item.label,
            "included": included,
            "term": str(fee_item.structure.term),
        },
        request=request,
    )
    record_event(
        event_type="finance.fee_election_set",
        actor=actor,
        student=student,
        data={
            "item": fee_item.label,
            "state": "billed" if included else "not billed",
            "student_admission": student.admission_number,
            "term": str(fee_item.structure.term),
        },
        request=request,
    )
    return election


@transaction.atomic
def create_discount_rule(
    *,
    name: str,
    basis: str,
    percent: Decimal | None = None,
    amount: Decimal | None = None,
    adjustment_type: str = "discount",
    fee_item: FeeItem | None = None,
    siblings_only: bool = False,
    students: list[Student] | None = None,
    note: str = "",
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> FeeDiscountRule:
    """
    Publish a recurring reduction the billing run applies by itself (B5).

    The named list and ``siblings_only`` narrow a rule; a rule with neither
    reduces every bill in the school, so it is checked against the whole class
    list before it is saved rather than discovered by the first invoice that
    comes out short.
    """
    rule = FeeDiscountRule(
        name=name.strip(),
        basis=basis,
        percent=to_money(percent) if percent is not None else None,
        amount=to_money(amount) if amount is not None else None,
        adjustment_type=adjustment_type,
        fee_item=fee_item,
        siblings_only=siblings_only,
        note=note.strip(),
        created_by=actor,
    )
    rule.full_clean(exclude=["students", "created_by"])
    rule.save()
    if students:
        rule.students.set(students)
    log_action(
        actor=actor,
        action="finance.discount_rule_created",
        content_type="finance.FeeDiscountRule",
        object_id=rule.pk,
        change_summary={
            "name": rule.name,
            "basis": rule.basis,
            "percent": str(rule.percent) if rule.percent else None,
            "amount": str(rule.amount) if rule.amount else None,
            "siblings_only": siblings_only,
            "named_students": len(students or ()),
        },
        request=request,
    )
    record_event(
        event_type="finance.discount_rule_created",
        actor=actor,
        data={"name": rule.name, "basis": rule.basis},
        request=request,
    )
    return rule


@transaction.atomic
def set_discount_rule_active(
    *,
    rule: FeeDiscountRule,
    active: bool,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> FeeDiscountRule:
    """
    Turn a recurring rule off without deleting it.

    Invoices already posted under it keep their adjustments, so deleting the rule
    would leave those ledger lines pointing at nothing; ``active`` is the switch
    that only stops future runs.
    """
    rule.active = active
    rule.save(update_fields=["active", "updated_at"])
    log_action(
        actor=actor,
        action="finance.discount_rule_changed",
        content_type="finance.FeeDiscountRule",
        object_id=rule.pk,
        change_summary={"name": rule.name, "active": active},
        request=request,
    )
    return rule


# ---------------------------------------------------------------------------
# Fee structure Excel upload
# ---------------------------------------------------------------------------


FEE_TEMPLATE_HEADERS = ["label", "amount"]


def build_fee_structure_template_bytes() -> bytes:
    """Return an .xlsx template for fee structure upload."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Fee Structure"
    ws.append(FEE_TEMPLATE_HEADERS)
    ws.append(["Tuition", "500.00"])
    ws.append(["Transport", "50.00"])
    ws.append(["Meals", "80.00"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def start_fee_structure_upload(
    *,
    uploaded_file,
    target_class: SchoolClass,
    term: Term,
    uploaded_by: User | None = None,
) -> FeeStructureUploadJob:
    job = FeeStructureUploadJob.objects.create(
        uploaded_by=uploaded_by,
        file=uploaded_file,
        target_class=target_class,
        term=term,
        status=FeeStructureUploadJob.Status.PENDING,
    )
    try:
        from django_q.tasks import async_task

        async_task("apps.finance.tasks.validate_fee_structure_upload", job.pk)
    except Exception:  # noqa: BLE001
        from apps.finance.tasks import validate_fee_structure_upload

        validate_fee_structure_upload(job.pk)
    return job


def validate_fee_structure_rows(
    rows: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Parse and validate fee structure rows. Returns (valid_rows, error_rows)."""
    valid: list[dict] = []
    errors: list[dict] = []
    for idx, raw in enumerate(rows, start=2):
        row = {k: str(v).strip() if v is not None else "" for k, v in raw.items()}
        row_errors: list[str] = []
        label = row.get("label", "").strip()
        amount_str = row.get("amount", "").strip()
        if not label:
            row_errors.append("label is required")
        if not amount_str:
            row_errors.append("amount is required")
        else:
            try:
                amount = Decimal(amount_str)
                if amount <= 0:
                    row_errors.append("amount must be greater than 0")
            except Exception:  # noqa: BLE001
                row_errors.append(f"amount '{amount_str}' is not a valid number")
        payload = {**row, "_row": idx}
        if row_errors:
            errors.append({**payload, "_errors": row_errors})
        else:
            valid.append(payload)
    return valid, errors


@transaction.atomic
def commit_fee_structure_upload(
    *,
    job: FeeStructureUploadJob,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> FeeStructure:
    if job.status != FeeStructureUploadJob.Status.READY:
        raise ValidationError("Upload job is not ready to commit.")
    items = [
        {"label": r["label"], "amount": to_money(r["amount"]), "ordering": i}
        for i, r in enumerate(job.preview_rows)
    ]
    structure = create_fee_structure(
        school_class=job.target_class,
        term=job.term,
        items=items,
        actor=actor,
        request=request,
    )
    job.status = FeeStructureUploadJob.Status.COMMITTED
    job.result_structure_id = structure.pk
    job.save(update_fields=["status", "result_structure_id"])
    return structure


# ---------------------------------------------------------------------------
# Invoicing
# ---------------------------------------------------------------------------

#: Which pupils a billing run bills. ``Student.Status`` has no single value that
#: means "in school this term", so the two that do are written out. A graduated
#: pupil is absent from this list on purpose — they are not billed a *new* term,
#: but the debt from the last one still follows them, which is why
#: ``selectors.arrears_rows`` does not filter on this list.
BILLABLE_STATUSES = ["active", "repeating"]

#: Plan actions that report a pupil without billing them: no fee list for the
#: class, or a list with nothing on it that applies to this pupil.
UNBILLABLE_ACTIONS = ("skip_no_structure", "skip_no_fee")


def _sibling_student_ids() -> set:
    """
    Pupils who share a guardian with at least one other pupil still in school.

    Read from the guardian links rather than from a family field someone has to
    remember to fill in: two children of the same mother in different classes
    are siblings, and a sibling discount that misses them because no form asked
    is a discount the school promised and did not give.

    Counted over the whole school, not the class being billed, so a run that
    works class by class reaches the same answer for a family split between two
    classes as a single whole-school run would.
    """
    from apps.students.models import StudentGuardian

    family_ids = [
        row["parent_id"]
        for row in StudentGuardian.objects.filter(
            student__status__in=BILLABLE_STATUSES
        )
        .values("parent_id")
        .annotate(pupils=Count("student", distinct=True))
        .filter(pupils__gte=2)
        if row["parent_id"]
    ]
    if not family_ids:
        return set()
    return set(
        StudentGuardian.objects.filter(
            parent_id__in=family_ids, student__status__in=BILLABLE_STATUSES
        ).values_list("student_id", flat=True)
    )


def _discount_rules_in_force(*, on: date) -> list[dict]:
    """
    The recurring reductions that apply on ``on``, each with the pupils covered.

    ``member_ids`` is ``None`` for a rule that covers everyone. A rule narrowed
    both ways — sibling-only *and* a named list — covers the pupils who satisfy
    both: the AND reading is the only one that cannot hand a discount to someone
    who did not earn it.

    Assembled once per billing run, not once per pupil. Three rules over nine
    hundred children should be three reads, not twenty-seven hundred.
    """
    in_force = []
    siblings = None
    for rule in FeeDiscountRule.objects.filter(active=True).prefetch_related(
        "students"
    ):
        if rule.valid_from and rule.valid_from > on:
            continue
        if rule.valid_to and rule.valid_to < on:
            continue
        member_ids = None
        if rule.siblings_only:
            if siblings is None:
                siblings = _sibling_student_ids()
            member_ids = siblings
        named = set(rule.students.values_list("pk", flat=True))
        if named:
            member_ids = named if member_ids is None else member_ids & named
        if member_ids is not None and not member_ids:
            continue
        in_force.append({"rule": rule, "member_ids": member_ids})
    return in_force


def _billing_plan(*, term: Term, school_class: SchoolClass | None = None) -> list[dict]:
    """
    Decide, for every pupil the run could bill, exactly what its invoice would say.

    ``preview_invoice_generation`` and ``post_invoices`` both read through here, so
    the preview a Finance Officer approves and the documents the run then posts
    cannot disagree — the old code had the same rule written twice, once for the
    preview and once for the commit, and the two had already started to differ.

    Everything is read in whole-set queries: structures, elections, discount
    rules, sibling families and existing invoices are five reads for the run
    however many pupils it covers, rather than one per child.

    Each row carries:

      ``student``, ``student_id``, ``admission_number``, ``name``, ``class``,
      ``structure`` / ``structure_id``, ``lines`` (each ``fee_item``, ``label``,
      ``amount``, ``due_date``), ``billed``, ``reductions`` (each ``rule_id``,
      ``name``, ``adjustment_type``, ``amount``, ``reason``), ``waived``,
      ``net``, ``due_date``, ``action`` — ``create``, ``skip_existing``,
      ``skip_no_structure`` or ``skip_no_fee`` (a pupil with nothing billable
      on the class list) — and ``existing_invoice``.
    """
    from apps.students.models import Student

    students_qs = (
        Student.objects.filter(status__in=BILLABLE_STATUSES)
        .select_related("user", "current_class")
        .order_by("admission_number")
    )
    if school_class:
        students_qs = students_qs.filter(current_class=school_class)
    else:
        students_qs = students_qs.filter(
            current_class__academic_year=term.academic_year
        )
    students = [student for student in students_qs if student.current_class_id]
    if not students:
        return []

    student_ids = [student.pk for student in students]
    class_ids = {student.current_class_id for student in students}

    # Ascending version into a dict, so the last row a class owns is its current
    # one — the same "highest active version wins" rule the old per-student
    # ``order_by("-version").first()`` reached, in one query instead of N.
    structures: dict = {}
    for structure in (
        FeeStructure.objects.filter(
            term=term, is_active=True, school_class_id__in=class_ids
        )
        .prefetch_related("items")
        .order_by("version")
    ):
        structures[structure.school_class_id] = structure

    elections = {
        (row["student_id"], row["fee_item_id"]): row["included"]
        for row in StudentFeeElection.objects.filter(
            student_id__in=student_ids, fee_item__structure__term_id=term.pk
        ).values("student_id", "fee_item_id", "included")
    }
    existing = {
        (row["student_id"], row["fee_structure_id"]): row
        for row in Invoice.objects.filter(
            term=term, student_id__in=student_ids
        ).values("pk", "student_id", "fee_structure_id", "invoice_number")
    }
    rules = _discount_rules_in_force(on=date.today())

    rows = []
    for student in students:
        structure = structures.get(student.current_class_id)
        if not structure:
            rows.append(
                {
                    "student_id": student.pk,
                    "student": student,
                    "admission_number": student.admission_number,
                    "name": student.user.full_name,
                    "class": student.current_class.name,
                    "structure": None,
                    "structure_id": None,
                    "lines": [],
                    "billed": ZERO,
                    "reductions": [],
                    "waived": ZERO,
                    "net": ZERO,
                    "due_date": None,
                    "action": "skip_no_structure",
                    "existing_invoice": None,
                    "existing_invoice_id": None,
                }
            )
            continue

        item_rows = []
        for item in structure.items.all():
            if not item.compulsory and not elections.get(
                (student.pk, item.pk), False
            ):
                # An optional item nobody entered this pupil for. The default is
                # out: transport is not a fee the school bills a child who walks.
                continue
            item_rows.append(
                {
                    "fee_item": item,
                    "fee_item_id": item.pk,
                    "label": item.label,
                    "amount": to_money(item.amount),
                    "due_date": item.due_date or structure.due_date,
                }
            )

        billed = money_sum(row["amount"] for row in item_rows)
        if billed <= ZERO:
            # A structure exists but nothing on it is billable for this pupil —
            # every item is optional and nobody was entered for one. This is
            # reported rather than posted: an invoice for 0.00 is a document
            # with no money in it, it burns a number from the gap-free
            # sequence, and ``_post_journal_entry`` refuses an entry that
            # balances at zero, so posting one would abort the run for the
            # whole class.
            rows.append(
                {
                    "student_id": student.pk,
                    "student": student,
                    "admission_number": student.admission_number,
                    "name": student.user.full_name,
                    "class": student.current_class.name,
                    "structure": str(structure),
                    "structure_id": structure.pk,
                    "lines": [],
                    "billed": ZERO,
                    "reductions": [],
                    "waived": ZERO,
                    "net": ZERO,
                    "due_date": None,
                    "action": "skip_no_fee",
                    "existing_invoice": None,
                    "existing_invoice_id": None,
                }
            )
            continue

        reductions = []
        waived = ZERO
        for entry in rules:
            rule = entry["rule"]
            if entry["member_ids"] is not None and student.pk not in entry["member_ids"]:
                continue
            if rule.fee_item_id is None:
                base = billed
            else:
                base = money_sum(
                    row["amount"]
                    for row in item_rows
                    if row["fee_item_id"] == rule.fee_item_id
                )
            room = billed - waived
            amount = min(rule.reduces(base), room)
            if amount <= ZERO:
                continue
            waived += amount
            reductions.append(
                {
                    "rule_id": rule.pk,
                    "name": rule.name,
                    "adjustment_type": rule.adjustment_type,
                    "amount": to_money(amount),
                    "reason": f"{rule.name} applied automatically at invoicing.",
                }
            )

        issued = existing.get((student.pk, structure.pk), {})
        item_dates = [row["due_date"] for row in item_rows if row["due_date"]]
        rows.append(
            {
                "student_id": student.pk,
                "student": student,
                "admission_number": student.admission_number,
                "name": student.user.full_name,
                "class": student.current_class.name,
                "structure": str(structure),
                "structure_id": structure.pk,
                "lines": item_rows,
                "billed": billed,
                "reductions": reductions,
                "waived": waived,
                "net": billed - waived,
                "due_date": structure.due_date
                or (min(item_dates) if item_dates else None),
                "action": "skip_existing" if issued else "create",
                "existing_invoice": issued.get("invoice_number"),
                "existing_invoice_id": issued.get("pk"),
            }
        )
    return rows


def invoice_generation_totals(*, term, school_class=None) -> dict:
    """
    What a run would cost the school, before it runs: rows, money, and why any
    pupil is skipped. Money comes from the same rows ``post_invoices`` writes.
    """
    return _generation_report(_billing_plan(term=term, school_class=school_class))


def _generation_report(rows: list[dict]) -> dict:
    counts = {
        "create": sum(1 for row in rows if row["action"] == "create"),
        "skip_existing": sum(1 for row in rows if row["action"] == "skip_existing"),
        "skip_no_structure": sum(
            1 for row in rows if row["action"] == "skip_no_structure"
        ),
        "skip_no_fee": sum(1 for row in rows if row["action"] == "skip_no_fee"),
    }
    made = [row for row in rows if row["action"] == "create"]
    return {
        "counts": counts,
        "billed": money_sum(row["billed"] for row in made),
        "waived": money_sum(row["waived"] for row in made),
        "net": money_sum(row["net"] for row in made),
    }


def preview_invoice_generation(
    *,
    term: Term,
    school_class: SchoolClass | None = None,
) -> dict:
    """
    Dry-run: show how many invoices would be generated, and what they would bill.

    Writes nothing. The rows come from ``_billing_plan`` — the same function the
    commit uses — so "preview said 240" and "the run posted 240" are the same
    arithmetic, not two descriptions of it.
    """
    rows = _billing_plan(term=term, school_class=school_class)
    return {
        "term": str(term),
        "class": school_class.name if school_class else "All classes",
        "rows": [
            {
                "student_id": row["student_id"],
                "admission_number": row["admission_number"],
                "name": row["name"],
                "class": row["class"],
                "structure": row["structure"],
                "total_amount": str(row["billed"]),
                "waived": str(row["waived"]),
                "net": str(row["net"]),
                "due_date": row["due_date"],
                "reductions": row["reductions"],
                "action": row["action"],
                "existing_invoice": row["existing_invoice"],
            }
            for row in rows
        ],
        **_generation_report(rows),
    }


@transaction.atomic
def post_invoices(
    *,
    term: Term,
    school_class: SchoolClass | None = None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> list[Invoice]:
    """
    Generate and post term invoices. Idempotent: skips students who already
    have an invoice for this (term, fee_structure) combination.

    Each posted invoice:
      DR Fees Receivable  / CR Fee Income

    The invoice bills the *structure* in full and a recurring reduction is then
    written as an ordinary ``Adjustment`` (DR Discounts / CR Fees Receivable)
    against it. That is why ``total_amount`` is the gross and not the net: the
    ledger already says what was charged, what was given up and what is left, and
    a second number invented here would be a figure no journal line supports.
    """
    fees_receivable = _get_account(FEES_RECEIVABLE_CODE)
    fee_income = _get_account(FEE_INCOME_CODE)
    today = date.today()
    now = timezone.now()
    # Read the posting rules once for the whole run, not once per invoice.
    finance_rules = FinanceSettings.load()
    # Which pupils are holding a credit, asked once: only those students need
    # the auto-apply check after their invoice appears.
    credit_students = _student_ids_with_credit()

    plan = _billing_plan(term=term, school_class=school_class)
    # The invoices a second run will find already there, read in one query for
    # the whole run rather than one per pupil it skips.
    already_posted = {}
    for row in plan:
        if row["existing_invoice_id"]:
            already_posted[row["existing_invoice_id"]] = None
    for invoice in Invoice.objects.filter(pk__in=list(already_posted)).select_related(
        "student", "term"
    ):
        already_posted[invoice.pk] = invoice

    posted: list[Invoice] = []
    reductions_posted = 0
    for row in plan:
        student = row["student"]
        if row["action"] in UNBILLABLE_ACTIONS:
            continue

        # Idempotency: the (student, term, structure) triple is the key, so a
        # second run adds nothing. Checked as an existing row rather than
        # ``get_or_create`` because the number must only be taken when a document
        # is really being made — a run that skips every student must not burn a
        # block of invoice numbers and leave the sequence full of holes.
        if row["existing_invoice_id"]:
            posted.append(already_posted[row["existing_invoice_id"]])
            continue

        invoice = Invoice.objects.create(
            student=student,
            term=term,
            fee_structure_id=row["structure_id"],
            invoice_number=next_document_number(
                NumberSeries.Kind.INVOICE, day=today, settings_row=finance_rules
            ),
            issued_date=today,
            due_date=row["due_date"],
            total_amount=row["billed"],
            status=Invoice.Status.POSTED,
            posted_by=actor,
            posted_at=now,
        )

        # Snapshot the items this pupil is billed for, each with the deadline it
        # was billed under. The line keeps its own date because a late fee is
        # owed on one item from one day, and the structure can be edited later.
        InvoiceLine.objects.bulk_create(
            [
                InvoiceLine(
                    invoice=invoice,
                    fee_item_id=line["fee_item_id"],
                    label=line["label"],
                    amount=line["amount"],
                    due_date=line["due_date"],
                )
                for line in row["lines"]
            ]
        )

        # Post journal entry: DR Fees Receivable / CR Fee Income
        _post_journal_entry(
            reference=f"INV-{invoice.invoice_number}",
            entry_date=today,
            description=(
                f"Invoice {invoice.invoice_number} for "
                f"{student.user.full_name} ({term})"
            ),
            posted_by=actor,
            lines=[
                {
                    "account": fees_receivable,
                    "debit": invoice.total_amount,
                    "credit": Decimal(0),
                },
                {
                    "account": fee_income,
                    "debit": Decimal(0),
                    "credit": invoice.total_amount,
                },
            ],
            source_type=JournalEntry.SourceType.INVOICE,
            source_id=invoice.pk,
            settings_row=finance_rules,
        )

        for reduction in row["reductions"]:
            apply_adjustment(
                student=student,
                invoice=invoice,
                adjustment_type=reduction["adjustment_type"],
                amount=reduction["amount"],
                reason=reduction["reason"],
                actor=actor,
                request=request,
            )
            reductions_posted += 1

        if student.pk in credit_students:
            # B4: money the school was already holding is put against the
            # invoice that has just appeared, so an overpayment does not sit on
            # a statement as a number a parent has to remember to claim.
            apply_credit_balances(
                invoice=invoice,
                actor=actor,
                settings_row=finance_rules,
                request=request,
            )
        posted.append(invoice)

    log_action(
        actor=actor,
        action="finance.invoices_posted",
        content_type="finance.Invoice",
        object_id="",
        change_summary={
            "term": str(term),
            "class": school_class.name if school_class else "all",
            "count": len(posted),
            "reductions": reductions_posted,
        },
        request=request,
    )
    record_event(
        event_type="finance.invoices_posted",
        actor=actor,
        data={
            "term": str(term),
            "class": school_class.name if school_class else "all",
            "count": len(posted),
        },
        request=request,
    )
    return posted


# ---------------------------------------------------------------------------
# Payment plans
# ---------------------------------------------------------------------------


@transaction.atomic
def create_payment_plan(
    *,
    student: Student,
    term: Term,
    installments: list[dict],  # [{"amount": Decimal, "due_date": date}, ...]
    note: str = "",
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> PaymentPlan:
    """
    Agree with a family how one term's fees will arrive (B6).

    No journal entry is made, because no money has moved: a plan is a promise,
    and the books record what happened. ``selectors.payment_plan_rows`` is what
    compares the promise against the receipts, which is what makes an overdue
    flag a fact about the bank rather than a date nobody checked.

    There is no interest, and no late fee either. A school charging a family for
    keeping to an arrangement it agreed to is a governing-body decision made in
    writing, not something a form can default to.
    """
    rows = [
        {"amount": to_money(row.get("amount")), "due_date": row.get("due_date")}
        for row in installments
    ]
    if not rows:
        raise ValidationError(
            {"installments": "An agreed plan needs at least one installment."}
        )
    for index, row in enumerate(rows, start=1):
        if row["amount"] <= ZERO:
            raise ValidationError(
                {"installments": f"Installment {index} must be above zero."}
            )
        if not row["due_date"]:
            raise ValidationError(
                {"installments": f"Installment {index} needs a due date."}
            )

    total = money_sum(row["amount"] for row in rows)
    running = (
        PaymentPlan.objects.filter(student=student, term=term)
        .exclude(status=PaymentPlan.Status.WITHDRAWN)
        .first()
    )
    if running:
        raise ValidationError(
            {
                "term": f"{student} already has a plan for {term} worth "
                f"{running.total_amount}. Withdraw it before agreeing another."
            }
        )

    plan = PaymentPlan.objects.create(
        student=student,
        term=term,
        total_amount=total,
        note=note.strip(),
        agreed_by=actor,
    )
    PaymentPlanInstallment.objects.bulk_create(
        [
            PaymentPlanInstallment(
                plan=plan,
                number=index,
                amount=row["amount"],
                due_date=row["due_date"],
            )
            for index, row in enumerate(rows, start=1)
        ]
    )
    log_action(
        actor=actor,
        action="finance.payment_plan_created",
        content_type="finance.PaymentPlan",
        object_id=plan.pk,
        change_summary={
            "student": student.admission_number,
            "term": str(term),
            "total": str(total),
            "installments": len(rows),
        },
        request=request,
    )
    record_event(
        event_type="finance.payment_plan_created",
        actor=actor,
        student=student,
        data={
            "term": str(term),
            "total": str(total),
            "installments": len(rows),
        },
        request=request,
    )
    return plan


@transaction.atomic
def withdraw_payment_plan(
    *,
    plan: PaymentPlan,
    reason: str,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> PaymentPlan:
    """
    Call off an agreed plan without erasing it.

    The installments stay: a family that paid two of four and then re-arranged
    has to be able to show when, and a withdrawn plan is the only evidence that
    the first arrangement existed. Money already received is untouched — it was
    banked, and it stays allocated to the invoices it was allocated to.
    """
    if not reason.strip():
        raise ValidationError({"reason": "Say why the plan is being called off."})
    if plan.status == PaymentPlan.Status.WITHDRAWN:
        return plan
    plan.status = PaymentPlan.Status.WITHDRAWN
    plan.note = f"{plan.note}\nWithdrawn: {reason.strip()}".strip()
    plan.save(update_fields=["status", "note", "updated_at"])
    log_action(
        actor=actor,
        action="finance.payment_plan_withdrawn",
        content_type="finance.PaymentPlan",
        object_id=plan.pk,
        change_summary={"student": plan.student.admission_number, "reason": reason},
        request=request,
    )
    record_event(
        event_type="finance.payment_plan_withdrawn",
        actor=actor,
        student=plan.student,
        data={"term": str(plan.term), "reason": reason.strip()},
        request=request,
    )
    return plan


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def _normalise_allocations(*, student, payment_amount, allocations):
    """
    Check a hand-built allocation before anything is written.

    The front desk sometimes knows better than "oldest invoice first" — a parent
    hands over money and says it is for the bus fee — and B4 allows that
    override. It is still held to the same invariant as the automatic path:
    every cent belongs to this student, sits on a posted invoice, does not
    exceed what that invoice still owes, and the rows together do not add up to
    more than the money handed in. Validating first means a rejected override
    cannot leave a receipt whose ledger disagrees with its own allocations.
    """
    if not allocations:
        return []

    invoices = {
        invoice.pk: invoice for invoice in Invoice.objects.filter(student=student)
    }
    rows = []
    seen = set()
    total = ZERO
    for entry in allocations:
        invoice_id = entry.get("invoice_id")
        if invoice_id is None:
            invoice_id = getattr(entry.get("invoice"), "pk", None)
        invoice = invoices.get(invoice_id)
        if invoice is None:
            raise ValidationError(
                {"allocations": f"Invoice {invoice_id} does not belong to this student."}
            )
        if invoice.pk in seen:
            raise ValidationError(
                {"allocations": f"{invoice.invoice_number} was listed twice."}
            )
        if invoice.status != Invoice.Status.POSTED:
            raise ValidationError(
                {"allocations": f"{invoice.invoice_number} is not a posted invoice."}
            )
        amount = to_money(entry.get("amount"))
        if amount <= 0:
            raise ValidationError(
                {"allocations": f"The amount against {invoice.invoice_number} must be positive."}
            )
        if amount > invoice.amount_outstanding:
            raise ValidationError(
                {
                    "allocations": (
                        f"{invoice.invoice_number} owes {invoice.amount_outstanding}; "
                        f"{amount} is more than it can take."
                    )
                }
            )
        seen.add(invoice.pk)
        total += amount
        rows.append((invoice, amount))

    if total > payment_amount:
        raise ValidationError(
            {
                "allocations": (
                    f"The allocation adds up to {total}, but only {payment_amount} "
                    "was received."
                )
            }
        )
    return rows


def _surplus_receipts(*, student_id) -> list[tuple[Payment, Decimal]]:
    """
    This student's receipts with money no invoice has absorbed yet.

    Oldest first, because that is the order the money arrived and therefore the
    order it should be spent. Reversed receipts and reversal rows are excluded:
    the first has gone back to the payer and the second never was money in.
    """
    rows = (
        Payment.objects.filter(
            student_id=student_id, is_reversed=False, amount__gt=ZERO
        )
        .annotate(
            applied=Sum(
                "allocations__allocated_amount",
                default=ZERO,
                output_field=_MONEY_FIELD,
            )
        )
        .order_by("payment_date", "pk")
    )
    out = []
    for payment in rows:
        left = to_money(payment.amount) - to_money(payment.applied)
        if left > 0:
            out.append((payment, left))
    return out


def _student_ids_with_credit() -> set[int]:
    """
    Every student currently holding an unapplied receipt, in one query.

    ``post_invoices`` needs exactly this yes/no for the whole school; asking it
    per pupil would add a query to every invoice generated for a credit almost
    nobody has.
    """
    rows = (
        Payment.objects.filter(is_reversed=False, amount__gt=ZERO)
        .annotate(
            applied=Sum(
                "allocations__allocated_amount",
                default=ZERO,
                output_field=_MONEY_FIELD,
            )
        )
        .values_list("student_id", "amount", "applied")
    )
    return {
        student_id
        for student_id, amount, applied in rows
        if to_money(amount) - to_money(applied) > 0
    }


@transaction.atomic
def apply_credit_balances(
    *,
    invoice: Invoice,
    actor: User | None = None,
    settings_row: FinanceSettings | None = None,
    request: HttpRequest | None = None,
) -> Decimal:
    """
    Spend a student's held credit on an invoice — the "next invoice" of B4.

    An overpayment is not a rounding matter, it is money the school is holding,
    and the parent expects it to be used rather than listed. So when an invoice
    is posted, any unallocated receipt of that student is put against it: the
    allocation row moves the debt, and one entry

        DR Student Credit Balances  /  CR Fees Receivable

    moves the ledger, which is exactly the mirror of the entry that created the
    surplus. Nothing here touches income: the fee was income when the invoice
    was posted, and the credit only changes who is paying for it.

    Returns what was applied, which is 0.00 for the ordinary student — the
    common case must not cost a journal entry.
    """
    outstanding = invoice.amount_outstanding
    if outstanding <= 0:
        return ZERO

    applied_total = ZERO
    for payment, surplus in _surplus_receipts(student_id=invoice.student_id):
        if outstanding <= 0:
            break
        take = min(surplus, outstanding)
        PaymentAllocation.objects.create(
            payment=payment, invoice=invoice, allocated_amount=take
        )
        outstanding -= take
        applied_total += take

    if applied_total <= 0:
        return ZERO

    entry = _post_journal_entry(
        reference=f"CRA-{uuid.uuid4().hex[:10].upper()}",
        entry_date=timezone.localdate(),
        description=(
            f"Credit balance applied to {invoice.invoice_number} "
            f"for {invoice.student.user.full_name}"
        ),
        posted_by=actor,
        lines=[
            {
                "account": _get_account(CREDIT_BALANCES_CODE),
                "debit": applied_total,
                "credit": Decimal(0),
            },
            {
                "account": _get_account(FEES_RECEIVABLE_CODE),
                "debit": Decimal(0),
                "credit": applied_total,
            },
        ],
        source_type=JournalEntry.SourceType.CREDIT,
        source_id=str(invoice.pk),
        settings_row=settings_row,
    )

    log_action(
        actor=actor,
        action="finance.credit_applied",
        content_type="finance.Invoice",
        object_id=invoice.pk,
        change_summary={
            "invoice": invoice.invoice_number,
            "amount": str(applied_total),
            "student": invoice.student.admission_number,
        },
        request=request,
    )
    record_event(
        event_type="finance.credit_applied",
        actor=actor,
        student=invoice.student,
        data={
            "invoice": invoice.invoice_number,
            "amount": str(applied_total),
            "entry": entry.reference,
        },
        request=request,
    )
    return applied_total


# ---------------------------------------------------------------------------
# Receipt printing
# ---------------------------------------------------------------------------


@transaction.atomic
def mark_receipt_printed(
    *, payment: Payment, copies: int = 1, actor: User | None = None, request=None
) -> Payment:
    """
    Record that copies of this receipt left the building.

    The count is the control, not the paper: B4 wants a reprint marked DUPLICATE
    precisely so a receipt cannot be presented twice as proof of a second
    payment. The first copy is the receipt itself, so only the second and later
    are marked, and the PDF asks this function how many have already gone.
    """
    copies = max(1, int(copies))
    payment.print_count = payment.print_count + copies
    payment.last_printed_at = timezone.now()
    payment.save(update_fields=["print_count", "last_printed_at", "updated_at"])

    log_action(
        actor=actor,
        action="finance.receipt_printed",
        content_type="finance.Payment",
        object_id=payment.pk,
        change_summary={
            "receipt": payment.receipt_number,
            "copies": copies,
            "total_printed": payment.print_count,
        },
        request=request,
    )
    return payment


@transaction.atomic
def record_payment(
    *,
    student: Student,
    amount: Decimal,
    method: str,
    reference: str,
    payment_date: date,
    notes: str = "",
    payer_name: str = "",
    attachment=None,
    recorded_by: User | None = None,
    idempotency_key: str | None = None,
    allocations=None,
    request: HttpRequest | None = None,
) -> Payment:
    """
    Record a payment. Allocates to oldest unpaid invoices first.

    DR settlement account (Bank / Mobile Money)  /  CR Fees Receivable

    An overpayment is no longer a note in a text field: the surplus posts to
    Student Credit Balances, so the ledger and the student's statement tell the
    same story about money the school is holding. ``apply_credit_balances``
    spends that surplus on the next invoice, which is what "credit to Student
    Credit Balances, auto-applied to the next invoice" means in the books.

    ``idempotency_key`` is the double-click guard. A front-desk form that is
    submitted twice — or retried after a timeout — returns the first payment
    instead of posting a second one, and a future gateway callback uses the same
    door with the notification's own id as the key.

    ``allocations`` is B4's manual override:
    ``[{"invoice": Invoice, "amount": Decimal}, ...]``. It replaces the
    oldest-first sweep for the part it names, and only after the whole list has
    been checked against this student's posted invoices and their balances.
    """
    reference = reference.strip()
    if not reference:
        raise ValidationError({"reference": "Payment reference is required."})

    amount = to_money(amount)
    if amount <= 0:
        raise ValidationError({"amount": "Payment amount must be positive."})

    if idempotency_key:
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    # Idempotency: DB unique will also enforce, but give a friendly error first
    if Payment.objects.filter(reference=reference).exists():
        raise ValidationError(
            {"reference": f"A payment with reference '{reference}' already exists."}
        )

    rules = FinanceSettings.load()
    # Checked before the receipt row or a number exists, so a mistyped override
    # costs nothing and cannot burn a number from the sequence.
    chosen = _normalise_allocations(
        student=student, payment_amount=amount, allocations=allocations
    )

    settlement_account = _settlement_account(method)
    fees_receivable = _get_account(FEES_RECEIVABLE_CODE)

    payment = Payment.objects.create(
        student=student,
        reference=reference,
        idempotency_key=idempotency_key or None,
        receipt_number=next_document_number(
            NumberSeries.Kind.RECEIPT, day=payment_date, settings_row=rules
        ),
        payment_date=payment_date,
        method=method,
        settled_into=settlement_account,
        amount=amount,
        payer_name=payer_name.strip(),
        attachment=attachment,
        notes=notes.strip(),
        recorded_by=recorded_by,
    )

    remaining = amount
    if chosen:
        for invoice, to_allocate in chosen:
            PaymentAllocation.objects.create(
                payment=payment,
                invoice=invoice,
                allocated_amount=to_allocate,
            )
            remaining -= to_allocate
    else:
        # Allocate to oldest unpaid invoices first
        unpaid_invoices = (
            Invoice.objects.filter(student=student, status=Invoice.Status.POSTED)
            .order_by("issued_date", "pk")
            .prefetch_related("allocations")
        )
        for invoice in unpaid_invoices:
            if remaining <= 0:
                break
            outstanding = invoice.amount_outstanding
            if outstanding <= 0:
                continue
            to_allocate = min(remaining, outstanding)
            PaymentAllocation.objects.create(
                payment=payment,
                invoice=invoice,
                allocated_amount=to_allocate,
            )
            remaining -= to_allocate

    lines = [
        {"account": settlement_account, "debit": amount, "credit": Decimal(0)},
        {"account": fees_receivable, "debit": Decimal(0), "credit": amount},
    ]
    if remaining > 0:
        # The surplus is money held for the student, not a discount and not
        # income: it is a liability until an invoice absorbs it.
        credit_balances = _get_account(CREDIT_BALANCES_CODE)
        lines.append(
            {"account": fees_receivable, "debit": remaining, "credit": Decimal(0)}
        )
        lines.append(
            {"account": credit_balances, "debit": Decimal(0), "credit": remaining}
        )

    # Journal entry: DR settlement / CR Fees Receivable (+ CR Credit Balances on the surplus)
    _post_journal_entry(
        reference=f"PMT-{payment.receipt_number}",
        entry_date=payment_date,
        description=(
            f"Payment {payment.receipt_number} from {student.user.full_name} "
            f"via {payment.get_method_display()}"
        ),
        posted_by=recorded_by,
        lines=lines,
        source_type=JournalEntry.SourceType.PAYMENT,
        source_id=payment.pk,
        settings_row=rules,
    )

    log_action(
        actor=recorded_by,
        action="finance.payment_recorded",
        content_type="finance.Payment",
        object_id=payment.pk,
        change_summary={
            "reference": reference,
            "amount": str(amount),
            "method": method,
            "student": student.admission_number,
        },
        request=request,
    )
    record_event(
        event_type="finance.payment_posted",
        actor=recorded_by,
        student=student,
        data={
            "receipt_number": payment.receipt_number,
            "reference": reference,
            "amount": str(amount),
            "method": method,
            "student_id": student.pk,
            "student_admission": student.admission_number,
        },
        request=request,
    )
    # Invalidate director KPI cache: fees_paid / collection_rate changed
    try:
        from apps.dashboards.cache import invalidate_school_kpis

        term_id = payment.allocations.values_list("invoice__term_id", flat=True).first()
        if term_id:
            invalidate_school_kpis(term_id=term_id)
        else:
            # Fallback: find term from any posted invoice for this student
            inv = (
                student.invoices.filter(status="posted", term__isnull=False)
                .values("term_id")
                .first()
            )
            if inv:
                invalidate_school_kpis(term_id=inv["term_id"])
    except Exception:  # noqa: BLE001, S110
        pass
    return payment


@transaction.atomic
def reverse_payment(
    *,
    payment: Payment,
    reason: str,
    approved_by: User | None = None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Payment:
    """
    Reverse a payment: a new row, an opposite entry, and nothing deleted.

    Three things happen and every one of them is visible afterwards.

    1. A reversing journal entry, line by line against the original (so an
       overpayment gives the surplus back to Student Credit Balances rather than
       to Fees Receivable).
    2. The original receipt is flagged ``is_reversed`` and a reversal Payment row
       is created, both linked, both still in the register.
    3. The invoice balances are restored — by the readers of allocations applying
       the ``live()`` filter, NOT by deleting the allocation rows.

    Step 3 is the change Module 6 ruled in (A2: entries are never removed).
    Deleting those rows used to be how a reversal "restored" a balance, and it
    destroyed the only record of which invoice the money had been put against,
    which is exactly what an auditor asks after a reversal. The rows stay, and
    ``payment.is_reversed`` is what makes every fee figure net them out.
    """
    if not reason.strip():
        raise ValidationError({"reason": "A reason is required to reverse a payment."})

    if payment.is_reversed:
        raise ValidationError("This payment has already been reversed.")

    if hasattr(payment, "reversal") and payment.reversal is not None:
        raise ValidationError("This payment already has a reversal record.")

    rules = FinanceSettings.load()
    # A5: a big give-back is not a one-person job. Until the approvals queue
    # lands (Checkpoint 4), the second signature is an Admin doing the reversal
    # themselves or naming themselves on it — refused outright, never silently
    # allowed through.
    if approved_by is not None and getattr(approved_by, "role", "") != "admin":
        raise ValidationError({"approved_by": "Only an Admin can approve a reversal."})
    second_signature = approved_by if approved_by is not None else actor
    if rules.amount_needs_approval("reversal", payment.amount) and not (
        second_signature is not None and getattr(second_signature, "role", "") == "admin"
    ):
        raise ValidationError(
            {
                "approved_by": (
                    f"Reversing {payment.amount} is above the approval threshold "
                    f"({rules.approval_reversal_threshold}). An Admin must perform "
                    "it or be named as the approver."
                )
            }
        )

    # Reverse journal entry: swap DR/CR
    original_entry = JournalEntry.objects.filter(
        source_type=JournalEntry.SourceType.PAYMENT, source_id=str(payment.pk)
    ).first()

    # The account the receipt actually settled into, read off the payment.
    # Re-deriving it from the method would book this reversal against whatever
    # map is configured today, not the one that was true when the money arrived.
    cash_account = payment.settled_into or _settlement_account(payment.method)
    fees_receivable = _get_account(FEES_RECEIVABLE_CODE)

    reversal_reference = f"REV-{payment.receipt_number}"
    # Mirror the original entry line by line with the sides swapped. Rebuilding
    # the two canonical lines instead would be wrong for any receipt that was not
    # a plain bank-in — a reversal of an overpayment has to give the surplus back
    # to Student Credit Balances, not to Fees Receivable, or the balances the
    # school reported yesterday stop adding up.
    original_lines = list(
        original_entry.lines.select_related("account") if original_entry else []
    )
    if original_lines:
        lines = [
            {
                "account": line.account,
                "debit": line.credit_amount,
                "credit": line.debit_amount,
            }
            for line in original_lines
            if line.debit_amount or line.credit_amount
        ]
    else:
        lines = [
            {"account": cash_account, "debit": Decimal(0), "credit": payment.amount},
            {
                "account": fees_receivable,
                "debit": payment.amount,
                "credit": Decimal(0),
            },
        ]
    _post_journal_entry(
        reference=reversal_reference,
        entry_date=date.today(),
        description=f"Reversal of {payment.receipt_number}: {reason}",
        posted_by=actor,
        lines=lines,
        source_type=JournalEntry.SourceType.REVERSAL,
        source_id=payment.pk,
        reverses=original_entry,
        settings_row=rules,
    )

    # Which invoices this receipt had been put against. Read BEFORE the flag is
    # set, because the rows are the record of that and they are not going away.
    affected_term_ids = set(
        payment.allocations.values_list("invoice__term_id", flat=True)
    )

    # Mark original as reversed
    payment.is_reversed = True
    payment.reversal_reason = reason.strip()
    payment.save(update_fields=["is_reversed", "reversal_reason", "updated_at"])

    # Create reversal payment record. It takes its own number from the receipt
    # sequence: a give-back is a line in the register an auditor reads, and a
    # register with holes in it is the thing the sequence exists to prevent.
    reversal = Payment.objects.create(
        student=payment.student,
        reference=f"REV-{payment.reference}",
        receipt_number=next_document_number(
            NumberSeries.Kind.RECEIPT, day=date.today(), settings_row=rules
        ),
        payment_date=date.today(),
        method=payment.method,
        settled_into=payment.settled_into,
        amount=-payment.amount,
        notes=f"Reversal of {payment.receipt_number}: {reason}",
        recorded_by=actor,
        reverses=payment,
        is_reversed=False,
    )

    log_action(
        actor=actor,
        action="finance.payment_reversed",
        content_type="finance.Payment",
        object_id=payment.pk,
        change_summary={
            "original_receipt": payment.receipt_number,
            "reversal_receipt": reversal.receipt_number,
            "amount": str(payment.amount),
            "reason": reason,
            "approved_by": getattr(approved_by, "username", "") if approved_by else "",
        },
        request=request,
    )
    record_event(
        event_type="finance.payment_reversed",
        actor=actor,
        student=payment.student,
        data={
            "original_receipt": payment.receipt_number,
            "amount": str(payment.amount),
            "reason": reason,
            "student_id": payment.student_id,
        },
        request=request,
    )
    try:
        from apps.dashboards.cache import invalidate_school_kpis

        for term_id in affected_term_ids:
            invalidate_school_kpis(term_id=term_id)
    except Exception:  # noqa: BLE001, S110
        pass
    return reversal


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------


def _post_adjustment_entry(
    adjustment: Adjustment, *, actor: User | None, reason_for_entry: str | None = None
) -> JournalEntry:
    """
    Put a pending-or-new adjustment through the ordinary posting path.

    Split out of ``apply_adjustment`` so that approving a write-off later and an
    admin applying one now run exactly the same two lines — a decision taken in
    the queue must not post money through a second, quieter route.

    DR Discounts and Scholarships / CR Fees Receivable, except a write-off which
    is a debt given up rather than a fee never charged: DR Bad Debt.
    """
    if adjustment.adjustment_type == Adjustment.AdjustmentType.WRITE_OFF:
        charges_code = BAD_DEBT_CODE
    else:
        charges_code = DISCOUNTS_CODE
    charges_account = _get_account(charges_code)
    fees_receivable = _get_account(FEES_RECEIVABLE_CODE)
    entry = _post_journal_entry(
        reference=f"ADJ-{uuid.uuid4().hex[:10].upper()}",
        entry_date=timezone.localdate(),
        description=reason_for_entry
        or (
            f"{adjustment.get_adjustment_type_display()} adjustment for "
            f"{adjustment.student.user.full_name}: {adjustment.reason}"
        ),
        posted_by=actor,
        lines=[
            {
                "account": charges_account,
                "debit": adjustment.amount,
                "credit": Decimal(0),
            },
            {
                "account": fees_receivable,
                "debit": Decimal(0),
                "credit": adjustment.amount,
            },
        ],
        source_type=JournalEntry.SourceType.ADJUSTMENT,
        source_id=str(adjustment.pk),
    )
    adjustment.journal_entry = entry
    adjustment.save(update_fields=["journal_entry", "updated_at"])
    return entry


@transaction.atomic
def apply_adjustment(
    *,
    student: Student,
    invoice: Invoice | None = None,
    adjustment_type: str,
    amount: Decimal,
    reason: str,
    attachment=None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Adjustment:
    """
    Apply a discount, scholarship, waiver or write-off.

    A write-off above the school's threshold, asked for by someone who is not an
    Admin, is recorded as a pending ask instead of posted — no ledger line until
    a second signature (A5). Everything else, and every gated action performed by
    an Admin, posts straight away as it always has.

    Amounts are quantized here because this figure both reduces a student's debt
    and lands in the ledger; a value rounded differently in each place is exactly
    the pesewa that makes a statement disagree with a trial balance.
    """
    if not reason.strip():
        raise ValidationError({"reason": "A reason is required for adjustments."})

    amount = to_money(amount)
    if amount <= 0:
        raise ValidationError({"amount": "An adjustment must be positive."})

    rules = FinanceSettings.load()
    gated = (
        adjustment_type == Adjustment.AdjustmentType.WRITE_OFF
        and rules.amount_needs_approval("write_off", amount)
        and not _is_admin(actor)
    )

    adjustment = Adjustment.objects.create(
        student=student,
        invoice=invoice,
        adjustment_type=adjustment_type,
        amount=amount,
        reason=reason.strip(),
        attachment=attachment,
        applied_by=actor,
        status=Adjustment.Status.PENDING if gated else Adjustment.Status.POSTED,
    )

    if gated:
        _open_approval(
            kind=ApprovalRequest.Kind.WRITE_OFF,
            amount=amount,
            reason=reason.strip(),
            target=adjustment,
            actor=actor,
            request=request,
        )
        log_action(
            actor=actor,
            action="finance.write_off_requested",
            content_type="finance.Adjustment",
            object_id=adjustment.pk,
            change_summary={
                "type": adjustment_type,
                "amount": str(amount),
                "student": student.admission_number,
                "reason": reason.strip(),
                "outcome": "awaiting approval — nothing posted",
            },
            request=request,
        )
        record_event(
            event_type="finance.write_off_requested",
            actor=actor,
            student=student,
            data={
                "amount": str(amount),
                "student_admission": student.admission_number,
            },
            request=request,
        )
        return adjustment

    _post_adjustment_entry(adjustment, actor=actor)

    log_action(
        actor=actor,
        action="finance.adjustment_applied",
        content_type="finance.Adjustment",
        object_id=adjustment.pk,
        change_summary={
            "type": adjustment_type,
            "amount": str(amount),
            "student": student.admission_number,
            "reason": reason,
        },
        request=request,
    )
    return adjustment


@transaction.atomic
def post_adjustment(
    *,
    adjustment: Adjustment,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Adjustment:
    """
    Post a pending write-off. Admin only — this is the second signature.

    The row was created by ``apply_adjustment`` holding no ledger line; posting
    it creates that line through the same path an admin's immediate write-off
    uses, so the two can never disagree about the accounts touched.
    """
    if not _is_admin(actor):
        raise ValidationError({"actor": "Only an Admin can post a write-off."})
    if adjustment.status != Adjustment.Status.PENDING:
        raise ValidationError("Only a pending adjustment can be posted.")

    adjustment.status = Adjustment.Status.POSTED
    _post_adjustment_entry(adjustment, actor=actor)
    adjustment.save(update_fields=["status", "updated_at"])

    log_action(
        actor=actor,
        action="finance.write_off_posted",
        content_type="finance.Adjustment",
        object_id=adjustment.pk,
        change_summary={
            "amount": str(adjustment.amount),
            "student_id": adjustment.student_id,
            "reason": adjustment.reason,
        },
        request=request,
    )
    return adjustment


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


def _post_expense_entry(
    expense: Expense, *, actor: User | None, settings_row: FinanceSettings | None = None
) -> JournalEntry:
    """
    Give a claim its number and its two ledger lines, or nothing at all.

    The number is taken inside this transaction so a claim that rolls back hands
    it straight back — a receipts register the bursar reads cannot have a hole in
    it. The credit side follows how the school paid: this is a school that spends
    petty cash but does not collect fees in it, so CASH and BANK are chosen by
    the claim, never defaulted.
    """
    cash_account = _get_account(
        BANK_CODE if expense.paid_via == Expense.PaymentVia.BANK else CASH_CODE
    )
    entry = _post_journal_entry(
        reference=next_document_number(
            NumberSeries.Kind.EXPENSE,
            day=expense.expense_date,
            settings_row=settings_row,
        ),
        entry_date=expense.expense_date,
        description=f"Expense: {expense.description}",
        posted_by=actor,
        lines=[
            {"account": expense.account, "debit": expense.amount, "credit": Decimal(0)},
            {"account": cash_account, "debit": Decimal(0), "credit": expense.amount},
        ],
        source_type=JournalEntry.SourceType.EXPENSE,
        source_id=str(expense.pk),
        settings_row=settings_row,
    )
    expense.reference = entry.reference
    expense.journal_entry = entry
    expense.save(update_fields=["reference", "journal_entry", "updated_at"])
    return entry


@transaction.atomic
def record_expense(
    *,
    account: Account,
    amount: Decimal,
    expense_date: date,
    description: str,
    paid_via: str = Expense.PaymentVia.CASH,
    vendor: Vendor | None = None,
    payee_name: str = "",
    attachment=None,
    approval_note: str = "",
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Expense:
    """
    Record a school expense claim.

    Above the school's threshold, a claim from someone who is not an Admin is
    held as a pending ask with no ledger line (A5: the officer who spends is not
    the officer who authorises). The money leaves the bank when an Admin approves
    it, not when the paper is written. An Admin's own claim, and any claim under
    the threshold, posts immediately as it always has.
    """
    amount = to_money(amount)
    if amount <= 0:
        raise ValidationError({"amount": "An expense must be a positive amount."})

    rules = FinanceSettings.load()
    gated = rules.amount_needs_approval("expense", amount) and not _is_admin(actor)

    expense = Expense.objects.create(
        account=account,
        amount=amount,
        expense_date=expense_date,
        description=description.strip(),
        paid_via=paid_via,
        vendor=vendor,
        payee_name=(payee_name or "").strip(),
        attachment=attachment,
        approval_note=approval_note.strip(),
        recorded_by=actor,
        status=Expense.Status.PENDING if gated else Expense.Status.POSTED,
    )

    if gated:
        _open_approval(
            kind=ApprovalRequest.Kind.EXPENSE,
            amount=amount,
            reason=description.strip(),
            target=expense,
            actor=actor,
            request=request,
        )
        log_action(
            actor=actor,
            action="finance.expense_requested",
            content_type="finance.Expense",
            object_id=expense.pk,
            change_summary={
                "account": account.name,
                "amount": str(amount),
                "payee": expense.payee,
                "date": str(expense_date),
                "outcome": "awaiting approval — nothing posted",
            },
            request=request,
        )
        record_event(
            event_type="finance.expense_requested",
            actor=actor,
            data={
                "amount": str(amount),
                "description": expense.description,
                "payee": expense.payee,
            },
            request=request,
        )
        return expense

    _post_expense_entry(expense, actor=actor, settings_row=rules)

    log_action(
        actor=actor,
        action="finance.expense_recorded",
        content_type="finance.Expense",
        object_id=expense.pk,
        change_summary={
            "account": account.name,
            "amount": str(amount),
            "payee": expense.payee,
            "date": str(expense_date),
            "reference": expense.reference,
        },
        request=request,
    )
    return expense


@transaction.atomic
def post_expense(
    *,
    expense: Expense,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Expense:
    """
    Post a pending claim. Admin only — this is the second signature.

    Posting runs the same two ledger lines an immediate claim runs. The queue
    approves a claim, it does not create a quieter way to spend.
    """
    if not _is_admin(actor):
        raise ValidationError({"actor": "Only an Admin can post an expense claim."})
    if expense.status != Expense.Status.PENDING:
        raise ValidationError("Only a pending claim can be posted.")

    expense.status = Expense.Status.POSTED
    _post_expense_entry(expense, actor=actor)
    expense.save(update_fields=["status", "updated_at"])

    log_action(
        actor=actor,
        action="finance.expense_posted",
        content_type="finance.Expense",
        object_id=expense.pk,
        change_summary={
            "amount": str(expense.amount),
            "reference": expense.reference,
            "payee": expense.payee,
        },
        request=request,
    )
    record_event(
        event_type="finance.expense_posted",
        actor=actor,
        data={
            "reference": expense.reference,
            "amount": str(expense.amount),
            "payee": expense.payee,
        },
        request=request,
    )
    return expense


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------


@transaction.atomic
def create_vendor(
    *,
    name: str,
    contact_person: str = "",
    phone: str = "",
    email: str = "",
    address: str = "",
    note: str = "",
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Vendor:
    """Add a supplier the school pays repeatedly."""
    name = name.strip()
    if not name:
        raise ValidationError({"name": "A vendor needs a name."})
    if Vendor.objects.filter(name__iexact=name).exists():
        raise ValidationError({"name": f"‘{name}’ is already on the vendors list."})
    vendor = Vendor.objects.create(
        name=name,
        contact_person=contact_person.strip(),
        phone=phone.strip(),
        email=email.strip(),
        address=address.strip(),
        note=note.strip(),
        created_by=actor,
    )
    log_action(
        actor=actor,
        action="finance.vendor_created",
        content_type="finance.Vendor",
        object_id=vendor.pk,
        change_summary={"name": vendor.name},
        request=request,
    )
    return vendor


@transaction.atomic
def update_vendor(
    *,
    vendor: Vendor,
    name: str | None = None,
    contact_person: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    address: str | None = None,
    note: str | None = None,
    is_active: bool | None = None,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Vendor:
    """Change a vendor's details. Only the fields named are touched."""
    fields = []
    for column, value in (
        ("name", name),
        ("contact_person", contact_person),
        ("phone", phone),
        ("email", email),
        ("address", address),
        ("note", note),
        ("is_active", is_active),
    ):
        if value is None:
            continue
        setattr(vendor, column, value.strip() if isinstance(value, str) else value)
        fields.append(column)
    if not fields:
        return vendor
    vendor.save(update_fields=fields + ["updated_at"])
    log_action(
        actor=actor,
        action="finance.vendor_updated",
        content_type="finance.Vendor",
        object_id=vendor.pk,
        change_summary={"changed": fields, "name": vendor.name},
        request=request,
    )
    return vendor


@transaction.atomic
def retire_vendor(
    *, vendor: Vendor, actor: User | None = None, request: HttpRequest | None = None
) -> Vendor:
    """
    Put a vendor out of the picker without deleting it.

    Old claims still name the supplier they were paid to; a retired vendor stays
    readable, it only stops being offered on a new one.
    """
    return update_vendor(
        vendor=vendor, is_active=False, actor=actor, request=request
    )


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


@transaction.atomic
def set_budget(
    *,
    term,
    account: Account,
    amount: Decimal,
    note: str = "",
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Budget:
    """
    Sanction what one expense category may spend in one term.

    Re-setting a category replaces the sanctioned figure — a budget is the
    number the school is working to now, not a history of every number it
    considered. The history of the change is in the audit log.
    """
    if account.type != Account.Type.EXPENSE:
        raise ValidationError({"account": "Budgets are set on expense categories."})
    amount = to_money(amount)
    if amount <= 0:
        raise ValidationError({"amount": "A budget must be a positive amount."})

    budget, created = Budget.objects.get_or_create(
        term=term, account=account, defaults={"amount": amount, "note": note.strip(), "created_by": actor}
    )
    if not created:
        budget.amount = amount
        budget.note = note.strip()
        budget.save(update_fields=["amount", "note", "updated_at"])

    log_action(
        actor=actor,
        action="finance.budget_set",
        content_type="finance.Budget",
        object_id=budget.pk,
        change_summary={
            "term": str(term),
            "account": account.name,
            "amount": str(amount),
            "created": created,
        },
        request=request,
    )
    record_event(
        event_type="finance.budget_set",
        actor=actor,
        data={
            "term": str(term),
            "account": account.name,
            "amount": str(amount),
        },
        request=request,
    )
    return budget


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


def _open_approval(
    *,
    kind: str,
    target,
    reason: str,
    amount: Decimal | None,
    actor: User | None,
    request: HttpRequest | None = None,
) -> ApprovalRequest:
    """
    Queue one ask, naming the document it is about.

    Internal because every gated path already holds the row being asked about;
    a caller that could name any target it liked could queue a decision on some-
    body else's claim. The partial unique constraint on the table is what makes a
    second open ask for the same document impossible even under two clerks.
    """
    target_field = ApprovalRequest.TARGET_BY_KIND[kind]
    approval = ApprovalRequest.objects.create(
        kind=kind,
        amount=to_money(amount) if amount is not None else None,
        reason=reason,
        requested_by=actor,
        **{target_field: target},
    )
    log_action(
        actor=actor,
        action="finance.approval_requested",
        content_type="finance.ApprovalRequest",
        object_id=approval.pk,
        change_summary={
            "kind": kind,
            "amount": str(amount) if amount is not None else "",
            "reason": reason,
        },
        request=request,
    )
    record_event(
        event_type="finance.approval_requested",
        actor=actor,
        data={
            "kind": approval.get_kind_display(),
            "amount": str(amount) if amount is not None else "—",
        },
        request=request,
    )
    return approval


@transaction.atomic
def request_reversal(
    *,
    payment: Payment,
    reason: str,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> ApprovalRequest:
    """
    Ask an Admin to reverse a receipt instead of reversing it unilaterally.

    ``reverse_payment`` refuses a large reversal by someone who is not an Admin —
    this is the route that refusal was always pointing at. The receipt keeps
    taking money off the pupil's balance until the ask is decided: a queued
    reversal changes nothing.
    """
    if not reason.strip():
        raise ValidationError({"reason": "An ask to reverse a receipt needs a reason."})
    if payment.is_reversed:
        raise ValidationError("This payment has already been reversed.")
    approval = _open_approval(
        kind=ApprovalRequest.Kind.REVERSAL,
        target=payment,
        reason=reason.strip(),
        amount=payment.amount,
        actor=actor,
        request=request,
    )
    return approval


@transaction.atomic
def request_period_reopen(
    *,
    period: FinancialPeriod,
    reason: str,
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> ApprovalRequest:
    """
    Ask for a closed period to be reopened.

    Reopening is what makes a printed report wrong, so the ask carries no amount:
    the risk is the whole window, and the approver reads the period, not a
    figure. The period stays closed until the ask is approved.
    """
    if not reason.strip():
        raise ValidationError({"reason": "An ask to reopen needs a reason."})
    if period.status != FinancialPeriod.Status.CLOSED:
        raise ValidationError("This period is not closed.")
    return _open_approval(
        kind=ApprovalRequest.Kind.PERIOD_REOPEN,
        target=period,
        reason=reason.strip(),
        amount=None,
        actor=actor,
        request=request,
    )


@transaction.atomic
def decide_approval(
    *,
    approval: ApprovalRequest,
    decision: str,
    note: str = "",
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> ApprovalRequest:
    """
    Say yes or no to one queued ask — and, on yes, do the thing.

    Approval performs the action through the ordinary service (``post_expense``,
    ``post_adjustment``, ``reverse_payment``, ``reopen_period``), in this same
    transaction. That is the whole point of the queue: an ask that the posting
    path would refuse is an ask that cannot be approved, so the ledger can never
    gain a line the checker did not pass.

    Refusing is also a decision with a name on it. A rejected claim keeps its
    row and its reason — the officer comes back the next week to ask whether the
    answer has changed, and "was there ever a claim?" should have an answer.
    """
    if not _is_admin(actor):
        raise ValidationError({"actor": "Only an Admin can decide an approval."})
    if approval.status != ApprovalRequest.Status.PENDING:
        raise ValidationError("This ask has already been decided.")
    if decision not in (
        ApprovalRequest.Status.APPROVED,
        ApprovalRequest.Status.REJECTED,
    ):
        raise ValidationError({"decision": "An ask is decided approved or rejected."})
    if approval.requested_by_id is not None and approval.requested_by_id == actor.pk:
        raise ValidationError(
            {
                "actor": "The person who asked cannot be the person who decides. "
                "That is what the second signature is."
            }
        )

    target = approval.target
    if target is None and decision == ApprovalRequest.Status.APPROVED:
        raise ValidationError("The document this ask was made about is gone.")

    if decision == ApprovalRequest.Status.APPROVED:
        if approval.kind == ApprovalRequest.Kind.EXPENSE:
            post_expense(expense=target, actor=actor)
        elif approval.kind == ApprovalRequest.Kind.WRITE_OFF:
            post_adjustment(adjustment=target, actor=actor)
        elif approval.kind == ApprovalRequest.Kind.REVERSAL:
            reverse_payment(
                payment=target,
                reason=approval.reason,
                approved_by=actor,
                actor=actor,
                request=request,
            )
        else:
            reopen_period(
                period=target, reason=approval.reason, actor=actor, request=request
            )
    elif approval.kind == ApprovalRequest.Kind.EXPENSE:
        target.status = Expense.Status.REJECTED
        target.save(update_fields=["status", "updated_at"])
    elif approval.kind == ApprovalRequest.Kind.WRITE_OFF:
        # The other two kinds have no state to refuse into: the receipt was never
        # reversed and the period was never closed-and-reopened, so leaving them
        # alone *is* the refusal. A FinancialPeriod does have a ``status`` and it
        # means open/closed — writing "rejected" into it would corrupt the books.
        target.status = Adjustment.Status.REJECTED
        target.save(update_fields=["status", "updated_at"])

    approval.status = decision
    approval.decided_by = actor
    approval.decided_at = timezone.now()
    approval.decision_note = note.strip()
    approval.save(
        update_fields=[
            "status",
            "decided_by",
            "decided_at",
            "decision_note",
            "updated_at",
        ]
    )

    log_action(
        actor=actor,
        action="finance.approval_decided",
        content_type="finance.ApprovalRequest",
        object_id=approval.pk,
        change_summary={
            "kind": approval.kind,
            "decision": decision,
            "amount": str(approval.amount) if approval.amount is not None else "",
            "note": note.strip(),
        },
        request=request,
    )
    record_event(
        event_type="finance.approval_decided",
        actor=actor,
        data={
            "kind": approval.get_kind_display(),
            "decision": approval.get_status_display(),
            "amount": str(approval.amount) if approval.amount is not None else "—",
        },
        request=request,
    )
    return approval


# ---------------------------------------------------------------------------
# Student finance notes
# ---------------------------------------------------------------------------


@transaction.atomic
def update_student_finance_notes(
    *,
    student: Student,
    sponsor: str = "",
    payment_plan_notes: str = "",
    actor: User | None = None,
    request: HttpRequest | None = None,
) -> Student:
    """
    Update the sponsor and free-text plan notes, with audit logging.

    ``payment_plan_notes`` is what the desk used to type an agreed schedule into.
    ``create_payment_plan`` now holds a real schedule it can check receipts
    against, so this field is for the colour around it — who promised, on what
    terms — and is deliberately left in place rather than deleted: twenty months
    of families' arrangements are written in it.
    """
    old = {"sponsor": student.sponsor, "payment_plan_notes": student.payment_plan_notes}
    student.sponsor = sponsor.strip()
    student.payment_plan_notes = payment_plan_notes.strip()
    student.save(update_fields=["sponsor", "payment_plan_notes", "updated_at"])
    log_action(
        actor=actor,
        action="finance.student_notes_updated",
        content_type="students.Student",
        object_id=student.pk,
        change_summary={
            "before": old,
            "after": {
                "sponsor": student.sponsor,
                "payment_plan_notes": student.payment_plan_notes,
            },
        },
        request=request,
    )
    return student


# ---------------------------------------------------------------------------
# Arrears and promotion
# ---------------------------------------------------------------------------


@transaction.atomic
def carry_forward_arrears(
    *,
    student_ids: list[int],
    actor: User | None = None,
    context: dict | None = None,
    request: HttpRequest | None = None,
) -> dict:
    """
    The hook class promotion calls so a balance cannot be lost in the move (B3).

    Nothing is copied. There is no "arrears" column to fill in, because a copied
    balance is a second truth about the money and the first one — the invoices,
    the receipts and the waivers — never went anywhere: ``Invoice.term`` and
    ``Invoice.student`` are not fields promotion touches. That is precisely why
    this is safe to hook rather than to migrate, and why the old arrears of a
    child who moves up stays a debt of the same child.

    What promotion *can* lose is sight of the debt. A class fee page selects the
    pupils whose *current* class is that class, so the moment a child moves, last
    term's bill stops appearing on the old class's list. This function reads the
    same figure back through ``selectors.arrears_rows`` — keyed by pupil, over
    every term, graduated pupils included — and writes it into the audit log, so
    the amount each child walked in with is on the record and the next person to
    touch the money readers has something to check themselves against.

    It never raises. A finance read that cannot be answered must not block
    children into their new class; the audit row then says the check did not run.
    """
    from .selectors import arrears_rows

    context = context or {}
    try:
        rows = arrears_rows(student_ids=student_ids)
    except Exception as exc:  # noqa: BLE001 - a promotion is not a finance outage
        log_action(
            actor=actor,
            action="finance.arrears_check_failed",
            content_type="students.Student",
            object_id="",
            change_summary={**context, "error": str(exc)},
            request=request,
        )
        return {"checked": False, "error": str(exc), "rows": [], "total": ZERO}

    total = money_sum(row["outstanding"] for row in rows)
    log_action(
        actor=actor,
        action="finance.arrears_carried_forward",
        content_type="students.Student",
        object_id="",
        change_summary={
            **context,
            "pupils_owing": len(rows),
            "total": str(total),
        },
        request=request,
    )
    record_event(
        event_type="finance.arrears_carried_forward",
        actor=actor,
        data={
            "pupils_owing": len(rows),
            "total": str(total),
            "moved": len(student_ids),
            **{key: str(value) for key, value in context.items()},
        },
        request=request,
    )
    return {"checked": True, "rows": rows, "total": total}


# ---------------------------------------------------------------------------
# Fee Reminders
# ---------------------------------------------------------------------------


def send_fee_reminder(
    *,
    student_ids: list[int],
    title: str,
    body: str,
    actor: User | None = None,
    request: HttpRequest | None = None,
):
    """
    Send a fee reminder announcement to selected students/parents.
    Reuses communications.services.compose_announcement.
    Dispatched as a background task from the view.
    """
    from apps.communications.services import compose_announcement

    return compose_announcement(
        title=title,
        body=body,
        audience="by_role",
        target_roles=["student", "parent"],
        created_by=actor,
        request=request,
        notify_recipients=True,
    )


# ---------------------------------------------------------------------------
# Excel report helpers
# ---------------------------------------------------------------------------


def build_outstanding_excel(rows: list[dict], *, actor: User | None = None) -> bytes:
    """
    Lay the outstanding-fees list out as .xlsx.

    The numbers arrive already derived by ``selectors.outstanding_students``;
    this function adds no arithmetic of its own. The waived amount gets a column
    of its own because billed = collected + waived + outstanding, and a reader
    who cannot see the waiver chases a debt the school already forgave.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    from apps.core.exports import append_rows, format_column_widths, stamp_document

    wb = Workbook()
    ws = wb.active
    ws.title = "Outstanding Fees"

    headers = [
        "Admission No.",
        "Student Name",
        "Class",
        "Term",
        "Total Billed",
        "Collected",
        "Waived / Adjusted",
        "Outstanding",
        "Last Payment",
    ]
    header_fill = PatternFill(
        start_color="1E3A5F", end_color="1E3A5F", fill_type="solid"
    )
    header_font = Font(bold=True, color="FFFFFF")

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    append_rows(
        ws,
        (
            [
                row.get("admission_number", ""),
                row.get("name", ""),
                row.get("class", ""),
                row.get("term", ""),
                row.get("total_amount", ""),
                row.get("amount_paid", ""),
                row.get("amount_adjusted", ""),
                row.get("amount_outstanding", ""),
                row.get("last_payment_date") or "",
            ]
            for row in rows
        ),
    )
    format_column_widths(ws)
    stamp_document(
        wb,
        title="Outstanding fees",
        source="Finance > Outstanding fees",
        actor=actor,
    )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
