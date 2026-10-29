"""
apps/finance/chart_of_accounts.py
---------------------------------
The system accounts the school's books are kept in, in one list.

Two things read this list and must never drift from each other:

  - ``services.seed_accounts()``, which makes the chart idempotently at runtime
    (the seeder and tests call it), and
  - the data migration that seeds a fresh database, which embeds its own copy
    because a migration may not depend on application code that later changes.

``test_chart_matches_the_seeded_migration`` in ``finance/tests/test_accounts_seed.py``
compares the database — which a test database reaches *through* the migrations —
against this list, so the two copies cannot silently disagree.

Money vocabulary the rest of the module depends on:

  - ``normal_side`` is the side the account grows on. A contra account has the
    opposite side to its own type — ``5000 Discounts and Scholarships`` is
    ``income`` typed, ``debit`` normal, therefore ``is_contra=True``: it is
    income the school gave up, and it belongs inside the income total, not in
    expenses. Counting a waiver as an operating cost made the school look both
    worse and different depending on which report you opened.
  - ``is_system=True`` accounts cannot be renamed, deactivated or deleted; they
    are what the postings and the reports are written against.
"""

from __future__ import annotations

# (code, name, type, normal_side, is_contra, parent_code, description)
CHART_OF_ACCOUNTS: list[dict] = [
    {
        "code": "1000",
        "name": "Cash",
        "type": "asset",
        "normal_side": "debit",
        "is_system": True,
        "is_contra": False,
        "parent_code": None,
        "description": "Petty cash held at the school. Fees are not received in cash.",
    },
    {
        "code": "1010",
        "name": "Bank",
        "type": "asset",
        "normal_side": "debit",
        "is_system": True,
        "is_contra": False,
        "parent_code": None,
        "description": "The school's bank account. Transfers and cheques settle here.",
    },
    {
        "code": "1020",
        "name": "Mobile Money",
        "type": "asset",
        "normal_side": "debit",
        "is_system": True,
        "is_contra": False,
        "parent_code": None,
        "description": "Fees received by mobile money, until the wallet is cashed out.",
    },
    {
        "code": "1100",
        "name": "Fees Receivable",
        "type": "asset",
        "normal_side": "debit",
        "is_system": True,
        "is_contra": False,
        "parent_code": None,
        "description": "Fee income billed and not yet collected, waived or written off.",
    },
    {
        "code": "2100",
        "name": "Student Credit Balances",
        "type": "liability",
        "normal_side": "credit",
        "is_system": True,
        "is_contra": False,
        "parent_code": None,
        "description": "Money the school holds for students — overpayments awaiting an invoice.",
    },
    {
        "code": "3000",
        "name": "Opening Balances",
        "type": "equity",
        "normal_side": "credit",
        "is_system": True,
        "is_contra": False,
        "parent_code": None,
        "description": "The other half of every opening-balance import.",
    },
    {
        "code": "4000",
        "name": "Fee Income",
        "type": "income",
        "normal_side": "credit",
        "is_system": True,
        "is_contra": False,
        "parent_code": None,
        "description": "What the school bills for tuition and the other fee items.",
    },
    {
        "code": "4010",
        "name": "Other Income",
        "type": "income",
        "normal_side": "credit",
        "is_system": True,
        "is_contra": False,
        "parent_code": None,
        "description": "Uniform sales, canteen, and anything else that is not a fee.",
    },
    {
        "code": "5000",
        "name": "Discounts and Scholarships",
        "type": "income",
        "normal_side": "debit",
        "is_system": True,
        "is_contra": True,
        "parent_code": "4000",
        "description": "Fee income given up: waivers, scholarships, sibling discounts.",
    },
    {
        "code": "5100",
        "name": "Bad Debt and Write-offs",
        "type": "expense",
        "normal_side": "debit",
        "is_system": True,
        "is_contra": False,
        "parent_code": None,
        "description": "Invoiced fees the school has given up on chasing.",
    },
    {
        "code": "6000",
        "name": "General Expenses",
        "type": "expense",
        "normal_side": "debit",
        "is_system": True,
        "is_contra": False,
        "parent_code": None,
        "description": "Spend that does not belong to a category Finance has created.",
    },
]

#: Codes the posting services reach for by name. Keeping them here means a
#: missing account is one lookup away, not a guess repeated down the module.
CASH_CODE = "1000"
BANK_CODE = "1010"
MOBILE_MONEY_CODE = "1020"
FEES_RECEIVABLE_CODE = "1100"
CREDIT_BALANCES_CODE = "2100"
OPENING_BALANCES_CODE = "3000"
FEE_INCOME_CODE = "4000"
OTHER_INCOME_CODE = "4010"
DISCOUNTS_CODE = "5000"
BAD_DEBT_CODE = "5100"
GENERAL_EXPENSES_CODE = "6000"
