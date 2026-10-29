"""
apps/finance/tests/test_accounts_seed.py
------------------------------------------
The chart of accounts a database actually has, versus the chart the project
says it keeps.

A fresh test database reaches the accounts *through the migrations*, so
comparing it with ``chart_of_accounts.CHART_OF_ACCOUNTS`` proves the two copies
of the chart — the one services and seeders read, the one embedded in migration
0007 because a migration may not depend on code that later changes — have not
drifted apart. That drift is invisible until a report totals an account that is
filed under a different type than the posting service expected.
"""

from django.test import TestCase

from ..chart_of_accounts import CHART_OF_ACCOUNTS
from ..models import Account


class ChartOfAccountsSeedTests(TestCase):
    def test_system_accounts_exist_after_migrations(self):
        codes = set(Account.objects.values_list("code", flat=True))
        self.assertTrue({"1000", "1010", "1100", "4000", "5000", "6000"} <= codes)
        self.assertTrue(Account.objects.filter(code="1000", is_system=True).exists())

    def test_the_migrated_chart_is_the_chart_the_code_uses(self):
        for spec in CHART_OF_ACCOUNTS:
            with self.subTest(code=spec["code"]):
                account = Account.objects.get(code=spec["code"])
                self.assertEqual(account.name, spec["name"])
                self.assertEqual(account.type, spec["type"])
                self.assertEqual(account.normal_side, spec["normal_side"])
                self.assertTrue(account.is_system)
                self.assertEqual(account.is_contra, spec["is_contra"])
                self.assertEqual(
                    account.parent_id,
                    Account.objects.filter(code=spec["parent_code"]).values_list(
                        "pk", flat=True
                    ).first()
                    if spec["parent_code"]
                    else None,
                )

    def test_no_account_is_missing_and_none_is_extra(self):
        seeded = set(
            Account.objects.filter(is_system=True).values_list("code", flat=True)
        )
        self.assertEqual(seeded, {row["code"] for row in CHART_OF_ACCOUNTS})

    def test_the_two_accounts_that_are_not_cash_nor_bank_are_still_assets(self):
        """
        1020 Mobile Money and 1100 Fees Receivable are assets, not income.

        Money in the mobile-money wallet is the school's, but it is not in the
        bank yet; fees receivable is what has been billed and not collected.
        Either one filed as income would make the balance sheet claim the
        headteacher has already been paid.
        """
        for code in ("1020", "1100"):
            self.assertEqual(Account.objects.get(code=code).type, Account.Type.ASSET)

    def test_discounts_is_contra_income_under_fee_income(self):
        discounts = Account.objects.get(code="5000")
        self.assertEqual(discounts.type, Account.Type.INCOME)
        self.assertTrue(discounts.is_contra)
        self.assertEqual(discounts.normal_side, Account.NormalSide.DEBIT)
        self.assertEqual(discounts.parent.code, "4000")
        self.assertEqual(discounts.grows_on, "debit")

    def test_every_chart_row_is_a_valid_account(self):
        for account in Account.objects.filter(is_system=True):
            account.clean()
