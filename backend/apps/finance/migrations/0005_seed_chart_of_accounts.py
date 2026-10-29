"""Seed the system chart of accounts on a fresh database."""

from django.db import migrations

SEED_ACCOUNTS = [
    ("1000", "Cash", "asset", "debit"),
    ("1010", "Bank", "asset", "debit"),
    ("1100", "Fees Receivable", "asset", "debit"),
    ("4000", "Fee Income", "income", "credit"),
    ("4010", "Other Income", "income", "credit"),
    ("5000", "Discounts and Scholarships", "expense", "debit"),
    ("6000", "General Expenses", "expense", "debit"),
]


def seed_accounts(apps, schema_editor):
    Account = apps.get_model("finance", "Account")
    for code, name, account_type, normal_side in SEED_ACCOUNTS:
        Account.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "type": account_type,
                "normal_side": normal_side,
                "is_system": True,
                "is_active": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0004_remove_invoice_finance_invoice_student_status_idx_and_more"),
    ]

    operations = [migrations.RunPython(seed_accounts, migrations.RunPython.noop)]
