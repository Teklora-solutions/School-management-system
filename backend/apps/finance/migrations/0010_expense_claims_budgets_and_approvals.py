"""
Checkpoint 4a: claims a person can ask about, and a decision trail for the asks.

Part F of the Finance spec wants an expense to be a claim with a payee, a number
and a second signature rather than a line in a notes file. Three new tables and
nine new columns do that, and none of them rewrites a figure that is already on
the ledger:

  ``Expense.status`` and ``Adjustment.status`` both default to ``posted``. Every
  claim and every write-off recorded before this release was recorded by somebody
  with the authority to record it, so it stays counted. A *new* gated action comes
  in as ``pending``, which is the case the services create.

  ``Expense.reference`` is nullable and unique. A null reference reads as "this
  claim predates the sequence, or never got past approval" — both true, and both
  better than inventing a number for a document that was never posted. SQLite
  allows any number of nulls in a unique column, so neither group collides.

  ``ApprovalRequest.amount`` is nullable because the one ask that always needs a
  decision — reopening a closed period — cannot name a figure. Its risk is the
  term's whole ledger. ``clean()`` still requires an amount on the three kinds
  that do carry one.

  ``Vendor`` is a separate table rather than a ``User``: a supplier gets paid but
  does not log in, and an ex-supplier must stay resolvable by old claims, which
  ``SET_NULL`` on ``Expense.vendor`` plus ``payee_name`` covers.

  ``Budget`` stores only the sanctioned figure. Variance is never saved; it is
  read from the journal against this number, so a late posting cannot leave a
  stale variance sitting next to a correct total.
"""

from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0001_initial"),
        (
            "finance",
            "0009_fee_structure_variations_discount_rules_and_payment_plans",
        ),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="adjustment",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Awaiting approval"),
                    ("posted", "Posted"),
                    ("rejected", "Rejected"),
                ],
                db_index=True,
                default="posted",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="expense",
            name="payee_name",
            field=models.CharField(
                blank=True,
                help_text="For a one-off payee with no vendor record — the casual "
                "labourer paid at the gate, not the supplier the school bills "
                "every term.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="expense",
            name="reference",
            field=models.CharField(
                blank=True,
                help_text="Taken from the expense number series when the claim is "
                "posted. Null on claims recorded before the sequence existed, "
                "and on a claim that never got past approval.",
                max_length=30,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="expense",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Awaiting approval"),
                    ("posted", "Posted"),
                    ("rejected", "Rejected"),
                ],
                db_index=True,
                default="posted",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="financesettings",
            name="budget_alert_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("80.00"),
                help_text="Warn when a category has spent this share of its term "
                "budget. A school that only finds out at 100% has no chance left "
                "to stop.",
                max_digits=5,
            ),
        ),
        migrations.AddField(
            model_name="financesettings",
            name="expense_prefix",
            field=models.CharField(
                default="EXP",
                help_text="Prefix for the expense claim sequence. A claim a bursar "
                "has to find again in two years is found by its number, not its "
                "description.",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="numberseries",
            name="kind",
            field=models.CharField(
                choices=[
                    ("invoice", "Invoice"),
                    ("receipt", "Receipt"),
                    ("expense", "Expense claim"),
                ],
                db_index=True,
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name="Vendor",
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
                ("name", models.CharField(max_length=120, unique=True)),
                ("contact_person", models.CharField(blank=True, max_length=100)),
                ("phone", models.CharField(blank=True, max_length=20)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("address", models.TextField(blank=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("note", models.CharField(blank=True, max_length=200)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="vendors_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "vendors",
                "db_table": "finance_vendor",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="expense",
            name="vendor",
            field=models.ForeignKey(
                blank=True,
                help_text="Pick from the vendors list, or name the payee below.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="expenses",
                to="finance.vendor",
            ),
        ),
        migrations.CreateModel(
            name="ApprovalRequest",
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
                        choices=[
                            ("expense", "Expense claim"),
                            ("write_off", "Write-off"),
                            ("reversal", "Receipt reversal"),
                            ("period_reopen", "Reopen a closed period"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=12,
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="How much this ask puts at risk. Empty for a "
                        "period reopen, whose risk is the whole term rather than "
                        "a single number.",
                        max_digits=14,
                        null=True,
                    ),
                ),
                (
                    "reason",
                    models.TextField(
                        help_text="Why this is being asked for. It is what the "
                        "approver reads, and it is also what gets performed when "
                        "the ask is approved."
                    ),
                ),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decision_note", models.TextField(blank=True)),
                (
                    "adjustment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approval_requests",
                        to="finance.adjustment",
                    ),
                ),
                (
                    "decided_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approvals_decided",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "expense",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approval_requests",
                        to="finance.expense",
                    ),
                ),
                (
                    "payment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approval_requests",
                        to="finance.payment",
                    ),
                ),
                (
                    "period",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approval_requests",
                        to="finance.financialperiod",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approvals_requested",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "finance_approval_request",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["status", "kind"], name="approval_queue_open"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("expense__isnull", False), ("status", "pending")
                        ),
                        fields=("expense",),
                        name="approval_one_open_ask_per_expense",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("adjustment__isnull", False), ("status", "pending")
                        ),
                        fields=("adjustment",),
                        name="approval_one_open_ask_per_adjustment",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("payment__isnull", False), ("status", "pending")
                        ),
                        fields=("payment",),
                        name="approval_one_open_ask_per_payment",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("period__isnull", False), ("status", "pending")
                        ),
                        fields=("period",),
                        name="approval_one_open_ask_per_period",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Budget",
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
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("note", models.CharField(blank=True, max_length=200)),
                (
                    "account",
                    models.ForeignKey(
                        limit_choices_to={"type": "expense"},
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="budgets",
                        to="finance.account",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="budgets_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "term",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="budgets",
                        to="academics.term",
                    ),
                ),
            ],
            options={
                "db_table": "finance_budget",
                "ordering": ["-term__start_date", "account__code"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("term", "account"),
                        name="finance_budget_one_number_per_category_per_term",
                    )
                ],
            },
        ),
    ]
