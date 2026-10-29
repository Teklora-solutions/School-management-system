"""
Checkpoint 3b: the billing variations Part B2 asks a fee list to carry.

One migration, four new tables and five new columns. Nothing here rewrites an
existing row, and nothing here changes a number that is already on the ledger:

  ``FeeItem.compulsory`` defaults to True, so every item invoiced before this
  point keeps being billed to every pupil in the class — an optional item is a
  decision about a new fee, never a reinterpretation of an old one.

  The three ``due_date`` columns are all nullable. ``InvoiceLine.due_date`` is
  filled by the billing run from now on and stays empty on the lines already
  posted, whose lateness is still measured the way it always was (``due_date``
  falling back to ``issued_date`` in ``outstanding_aging``).

  ``StudentFeeElection``, ``FeeDiscountRule``, ``PaymentPlan`` and
  ``PaymentPlanInstallment`` are new, so a discount or an agreed schedule that
  was not given before this release cannot appear on an old invoice
  retrospectively.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0001_initial"),
        ("students", "0003_remove_student_students_student_class_status_idx_and_more"),
        ("finance", "0008_numberseries_receipt_numbering_and_verification"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="feeitem",
            name="compulsory",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "An optional item is only billed to a pupil who has been "
                    "entered for it (``StudentFeeElection``), which is how "
                    "transport and meals differ between two children in the "
                    "same class."
                ),
            ),
        ),
        migrations.AddField(
            model_name="feeitem",
            name="due_date",
            field=models.DateField(
                blank=True,
                help_text="Per-item deadline; blank means the structure's own due date.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="feestructure",
            name="copied_from",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Set when this structure was produced by copying last "
                    "term's. The copy is a fresh set of rows, not a shared "
                    "reference, so editing the new term's amounts can never "
                    "move the old term's invoices."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="copies",
                to="finance.feestructure",
            ),
        ),
        migrations.AddField(
            model_name="feestructure",
            name="due_date",
            field=models.DateField(
                blank=True,
                help_text=(
                    "What the family is expected to pay by. An item without "
                    "its own date inherits this one; an invoice snapshots "
                    "whichever applies."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="invoiceline",
            name="due_date",
            field=models.DateField(
                blank=True,
                help_text=(
                    "Copied from the fee item (or the structure behind it) "
                    "when the invoice was posted. Kept on the line because a "
                    "late fee is owed on this item from this date, and editing "
                    "the structure afterwards must not move a deadline a pupil "
                    "was already billed under."
                ),
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="FeeDiscountRule",
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
                ("name", models.CharField(max_length=100)),
                (
                    "basis",
                    models.CharField(
                        choices=[
                            ("percent", "Percentage of the bill"),
                            ("fixed", "Fixed amount"),
                        ],
                        default="percent",
                        max_length=10,
                    ),
                ),
                (
                    "percent",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text=(
                            "Of the bill, or of one fee item when one is named "
                            "below."
                        ),
                        max_digits=5,
                        null=True,
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=14, null=True
                    ),
                ),
                (
                    "adjustment_type",
                    models.CharField(
                        choices=[
                            ("discount", "Discount"),
                            ("scholarship", "Scholarship"),
                            ("waiver", "Waiver"),
                        ],
                        default="discount",
                        max_length=20,
                    ),
                ),
                (
                    "siblings_only",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Only pupils who share a guardian with another "
                            "active pupil. A sibling discount that is not tied "
                            "to a family reduces the school's income for every "
                            "pupil, which is not what was agreed."
                        ),
                    ),
                ),
                ("active", models.BooleanField(db_index=True, default=True)),
                ("valid_from", models.DateField(blank=True, null=True)),
                ("valid_to", models.DateField(blank=True, null=True)),
                ("note", models.CharField(blank=True, max_length=200)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="fee_discount_rules_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "fee_item",
                    models.ForeignKey(
                        blank=True,
                        help_text="Blank reduces the whole invoice.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="discount_rules",
                        to="finance.feeitem",
                    ),
                ),
                (
                    "students",
                    models.ManyToManyField(
                        blank=True,
                        help_text=(
                            "Named pupils, e.g. a scholarship list. Empty "
                            "means every pupil."
                        ),
                        related_name="fee_discount_rules",
                        to="students.student",
                    ),
                ),
            ],
            options={
                "verbose_name": "fee discount rule",
                "verbose_name_plural": "fee discount rules",
                "db_table": "finance_fee_discount_rule",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="PaymentPlan",
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
                    "total_amount",
                    models.DecimalField(decimal_places=2, max_digits=14),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Agreed"),
                            ("completed", "Completed"),
                            ("withdrawn", "Withdrawn"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=12,
                    ),
                ),
                ("agreed_on", models.DateField(auto_now_add=True)),
                ("note", models.TextField(blank=True)),
                (
                    "agreed_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payment_plans_agreed",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payment_plans",
                        to="students.student",
                    ),
                ),
                (
                    "term",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payment_plans",
                        to="academics.term",
                    ),
                ),
            ],
            options={
                "db_table": "finance_payment_plan",
                "ordering": ["-term__start_date", "student__admission_number"],
            },
        ),
        migrations.CreateModel(
            name="PaymentPlanInstallment",
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
                ("number", models.PositiveSmallIntegerField()),
                (
                    "amount",
                    models.DecimalField(decimal_places=2, max_digits=14),
                ),
                ("due_date", models.DateField()),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="installments",
                        to="finance.paymentplan",
                    ),
                ),
            ],
            options={
                "db_table": "finance_payment_plan_installment",
                "ordering": ["number"],
            },
        ),
        migrations.CreateModel(
            name="StudentFeeElection",
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
                    "included",
                    models.BooleanField(
                        default=True,
                        help_text=(
                            "Off means this pupil is not charged this item "
                            "this term."
                        ),
                    ),
                ),
                ("note", models.CharField(blank=True, max_length=200)),
                (
                    "fee_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="elections",
                        to="finance.feeitem",
                    ),
                ),
                (
                    "noted_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="fee_elections_noted",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fee_elections",
                        to="students.student",
                    ),
                ),
            ],
            options={
                "db_table": "finance_student_fee_election",
                "ordering": ["student__admission_number"],
            },
        ),
        migrations.AddConstraint(
            model_name="paymentplan",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ("active", "completed"))),
                fields=("student", "term"),
                name="finance_plan_one_running_per_term",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentplaninstallment",
            constraint=models.UniqueConstraint(
                fields=("plan", "number"),
                name="finance_plan_number_unique_per_plan",
            ),
        ),
        migrations.AddConstraint(
            model_name="studentfeeelection",
            constraint=models.UniqueConstraint(
                fields=("student", "fee_item"),
                name="finance_election_one_row_per_item",
            ),
        ),
    ]
