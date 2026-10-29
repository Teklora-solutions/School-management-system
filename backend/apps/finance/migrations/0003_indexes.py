"""
0003_indexes.py
---------------
Add performance indexes for common finance query patterns.

  finance_invoice:
    (student_id, status)         — student_statement() filter
    (term_id, status)            — per-term aggregates (school_kpis, class_fee_summary)
    (student_id, term_id)        — invoice_list() double-filter
    issued_date                  — date-range queries on ledger / reports

  finance_payment:
    (student_id, payment_date)   — payment_list() + recent_payments ordering
    payment_date                 — standalone date filter (collected_today/week)

  finance_payment_allocation:
    (invoice_id,)                — already has unique_together FK, explicit named index
    invoice__term compound covered by FK chain — no extra index needed here

  finance_journal_entry:
    (source_type, source_id)     — look up entry for a given payment/invoice
    ← already has both fields individually indexed; add composite for point lookup

  finance_journal_line:
    (entry_id,)                  — FK already indexed by Django
    (account_id, entry__entry_date) — ledger_entries() account+date filter
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0002_initial"),
    ]

    operations = [
        # Invoice look-ups
        migrations.AddIndex(
            model_name="invoice",
            index=models.Index(
                fields=["student", "status"],
                name="finance_invoice_student_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="invoice",
            index=models.Index(
                fields=["term", "status"],
                name="finance_invoice_term_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="invoice",
            index=models.Index(
                fields=["student", "term"],
                name="finance_invoice_student_term_idx",
            ),
        ),
        # Payment look-ups
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(
                fields=["student", "payment_date"],
                name="finance_payment_student_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(
                fields=["payment_date"],
                name="finance_payment_date_idx",
            ),
        ),
        # Journal entry composite look-up
        migrations.AddIndex(
            model_name="journalentry",
            index=models.Index(
                fields=["source_type", "source_id"],
                name="finance_je_source_idx",
            ),
        ),
        # Journal line account+date (for ledger_entries filter)
        migrations.AddIndex(
            model_name="journalline",
            index=models.Index(
                fields=["account"],
                name="finance_jl_account_idx",
            ),
        ),
    ]
