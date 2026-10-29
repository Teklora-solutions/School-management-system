import uuid

from django.db import migrations, models

import apps.finance.models


def backfill_verification_codes(apps, schema_editor):
    """
    Give every existing receipt its own code.

    One at a time, not one shared default: a unique column filled from a
    callable default is filled by running that callable once, which would hand
    the whole register the same code and let any receipt verify as any other.
    """
    Payment = apps.get_model("finance", "Payment")
    for payment in Payment.objects.filter(verification_code__isnull=True):
        payment.verification_code = uuid.uuid4().hex[:8].upper()
        payment.save(update_fields=["verification_code"])


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0007_chart_sync_legacy_settlement_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="NumberSeries",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "kind",
                    models.CharField(
                        choices=[("invoice", "Invoice"), ("receipt", "Receipt")],
                        db_index=True,
                        max_length=10,
                    ),
                ),
                ("year", models.PositiveSmallIntegerField()),
                ("prefix", models.CharField(max_length=8)),
                ("next_number", models.PositiveIntegerField(default=1)),
            ],
            options={
                "db_table": "finance_number_series",
                "ordering": ["kind", "year"],
            },
        ),
        migrations.AddConstraint(
            model_name="numberseries",
            constraint=models.UniqueConstraint(
                fields=("kind", "year"), name="finance_series_one_row_per_year"
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="last_printed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="print_count",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text=(
                    "How many copies of this receipt have left the building. The "
                    "second and later copies are marked DUPLICATE, because a "
                    "receipt printed twice and paid twice is the oldest trick in "
                    "a fee office."
                ),
            ),
        ),
        # Added nullable and filled by hand: see backfill_verification_codes.
        migrations.AddField(
            model_name="payment",
            name="verification_code",
            field=models.CharField(max_length=16, null=True),
        ),
        migrations.RunPython(
            backfill_verification_codes,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="payment",
            name="verification_code",
            field=models.CharField(
                default=apps.finance.models._verification_code,
                help_text=(
                    "Printed on the receipt; typed at the counter to confirm "
                    "it is real."
                ),
                max_length=16,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="journalentry",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("invoice", "Invoice"),
                    ("payment", "Payment"),
                    ("reversal", "Reversal"),
                    ("adjustment", "Adjustment"),
                    ("expense", "Expense"),
                    ("credit", "Credit applied"),
                    ("manual", "Manual"),
                ],
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="paymentallocation",
            index=models.Index(fields=["payment"], name="finance_alloc_payment"),
        ),
    ]
