"""
Bring an existing database up to the current chart, and make the ledger
append-only at the database level too.

Three jobs, in order:

  1. ``sync_chart`` — migration 0005 seeded seven accounts. The chart has
     eleven now, and ``5000 Discounts and Scholarships`` has been retyped from
     an expense to a contra-income account. 0005 is applied history and may not
     be edited, so the change lands here instead: this step both inserts what is
     missing and updates what changed, which is what makes it safe on a school
     database that already has journals against these accounts.
  2. ``settle_legacy_payments`` — cash is no longer a way a fee is received, so
     an old cash receipt is re-settled into the bank account, and every payment
     gets the settlement account the ledger will debit or credit from now on.
  3. ``make_ledger_append_only`` — a trigger, because a manager that refuses an
     UPDATE is only a rule for people who go through Python. The person with a
     database browser editing a posted amount is exactly who the ledger is
     protected from.

The chart is copied into this file rather than imported: a migration must keep
running the same way after the application changes. ``test_chart_matches_the_seeded_migration``
in ``finance/tests/test_accounts_seed.py`` compares the migrated database with
``apps/finance/chart_of_accounts.py`` so the two cannot drift apart.
"""

from django.db import migrations

# (code, name, type, normal_side, is_system, is_contra, parent_code, description)
CHART = [
    (
        "1000",
        "Cash",
        "asset",
        "debit",
        True,
        False,
        None,
        "Petty cash held at the school. Fees are not received in cash.",
    ),
    (
        "1010",
        "Bank",
        "asset",
        "debit",
        True,
        False,
        None,
        "The school's bank account. Transfers and cheques settle here.",
    ),
    (
        "1020",
        "Mobile Money",
        "asset",
        "debit",
        True,
        False,
        None,
        "Fees received by mobile money, until the wallet is cashed out.",
    ),
    (
        "1100",
        "Fees Receivable",
        "asset",
        "debit",
        True,
        False,
        None,
        "Fee income billed and not yet collected, waived or written off.",
    ),
    (
        "2100",
        "Student Credit Balances",
        "liability",
        "credit",
        True,
        False,
        None,
        "Money the school holds for students — overpayments awaiting an invoice.",
    ),
    (
        "3000",
        "Opening Balances",
        "equity",
        "credit",
        True,
        False,
        None,
        "The other half of every opening-balance import.",
    ),
    (
        "4000",
        "Fee Income",
        "income",
        "credit",
        True,
        False,
        None,
        "What the school bills for tuition and the other fee items.",
    ),
    (
        "4010",
        "Other Income",
        "income",
        "credit",
        True,
        False,
        None,
        "Uniform sales, canteen, and anything else that is not a fee.",
    ),
    (
        "5000",
        "Discounts and Scholarships",
        "income",
        "debit",
        True,
        True,
        "4000",
        "Fee income given up: waivers, scholarships, sibling discounts.",
    ),
    (
        "5100",
        "Bad Debt and Write-offs",
        "expense",
        "debit",
        True,
        False,
        None,
        "Invoiced fees the school has given up on chasing.",
    ),
    (
        "6000",
        "General Expenses",
        "expense",
        "debit",
        True,
        False,
        None,
        "Spend that does not belong to a category Finance has created.",
    ),
]

LEDGER_TABLES = ("finance_journal_entry", "finance_journal_line")


def sync_chart(apps, schema_editor):
    Account = apps.get_model("finance", "Account")
    rows = {}
    for code, name, kind, side, is_system, is_contra, _parent, description in CHART:
        account, _created = Account.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "type": kind,
                "normal_side": side,
                "is_system": is_system,
                "is_contra": is_contra,
                "description": description,
                "is_active": True,
            },
        )
        # 0005 seeded 5000 as an expense. Someone may also have renamed it on
        # the accounts page, so the system rows are pulled back to the chart.
        changed = []
        for field, value in (
            ("name", name),
            ("type", kind),
            ("normal_side", side),
            ("is_system", is_system),
            ("is_contra", is_contra),
            ("description", description),
        ):
            if getattr(account, field) != value:
                setattr(account, field, value)
                changed.append(field)
        if changed:
            changed.append("updated_at")
            account.save(update_fields=changed)
        rows[code] = account

    # Parents second: 5000 hangs under 4000, which may not have existed yet.
    for code, _name, _kind, _side, _system, _contra, parent_code, _desc in CHART:
        if not parent_code:
            continue
        parent, child = rows.get(parent_code), rows.get(code)
        if parent and child and child.parent_id != parent.pk:
            child.parent = parent
            child.save(update_fields=["parent", "updated_at"])


def settle_legacy_payments(apps, schema_editor):
    Payment = apps.get_model("finance", "Payment")
    Account = apps.get_model("finance", "Account")
    bank = Account.objects.filter(code="1010").first()
    if bank is None:
        return
    # A receipt recorded as cash was settled into the same ledger account the
    # bank receipts were; keep the books honest rather than delete the row.
    Payment.objects.filter(method="cash").update(method="bank", settled_into=bank)
    Payment.objects.filter(settled_into__isnull=True).update(settled_into=bank)


def ensure_settings_row(apps, schema_editor):
    FinanceSettings = apps.get_model("finance", "FinanceSettings")
    FinanceSettings.objects.get_or_create(pk=1)


_APPEND_ONLY_TEXT = (
    "Is an append-only ledger table. Corrections are posted as a reversal, "
    "never as an edit or a delete."
)


def _trigger_sql(table: str, action: str) -> str:
    name = f"{table}_no_{action.lower()}"
    return (
        f"CREATE TRIGGER {name} BEFORE {action} ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} {_APPEND_ONLY_TEXT}'); END"
    )


def make_ledger_append_only(apps, schema_editor):
    """
    Refuse UPDATE and DELETE on the journal tables at the database level.

    SQLite-only for now: this school runs on one local SQLite file, and the
    trigger syntax differs per backend. The Python guards in
    ``finance.models`` are what protect a Postgres install.
    """
    if schema_editor.connection.vendor != "sqlite":
        return
    for table in LEDGER_TABLES:
        for action in ("UPDATE", "DELETE"):
            schema_editor.execute(_trigger_sql(table, action))


def remove_ledger_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != "sqlite":
        return
    for table in LEDGER_TABLES:
        for action in ("UPDATE", "DELETE"):
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {table}_no_{action.lower()}")


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0006_financesettings_financialperiod_account_is_contra_and_more"),
    ]

    operations = [
        migrations.RunPython(sync_chart, migrations.RunPython.noop),
        migrations.RunPython(settle_legacy_payments, migrations.RunPython.noop),
        migrations.RunPython(ensure_settings_row, migrations.RunPython.noop),
        migrations.RunPython(make_ledger_append_only, remove_ledger_triggers),
    ]
