"""
apps/finance/interface/
-----------------------
The money read interface — the only door other modules may use to ask
Finance what the school is owed, has collected, has paid out, or has left.

This package deliberately contains **no arithmetic**. Every name below is
re-exported from :mod:`apps.finance.selectors`, which is where each figure is
actually derived. A second copy of the arithmetic is what put the Director's
"outstanding" and the Finance officer's "outstanding" two hundred cedis apart
over a fee the school had already waived, and the two screens could not be
reconciled because each was internally consistent.

So the split is:

* ``interface`` — the contract. Names, argument shapes, return shapes. This is
  what a screen or another app imports, and what a test may pin.
* ``selectors`` — the queries that satisfy that contract. Only Finance's own
  module reaches into it directly.
* ``services`` — the writes. Never reachable from a screen: a figure that can
  also change the ledger while rendering it is not a figure, it is a side
  effect.

Nothing outside ``apps/finance`` should import ``selectors`` or ``models``.
``tests/test_interface.py`` reads this package's source and fails if it ever
gains a calculation, and the Director portal's own AST guard
(``apps/dashboards/tests/test_director_finance_contract.py``) fails if a
Director screen starts doing money maths instead of asking here.

To add a figure: write the reader in ``selectors``, add its name to the
``__all__`` below, and extend the test that proves nothing is calculated here.
"""

from __future__ import annotations

from apps.finance import selectors as _selectors

# One import block, sorted: every name in it is a reader defined in
# ``selectors`` and nothing else. What each one answers is grouped in
# ``__all__`` below, where a screen actually reads it.
from apps.finance.selectors import (
    account_balances,
    account_list,
    approval_queue,
    arrears_rows,
    arrears_summary,
    budget_variance,
    cash_and_bank_balances,
    cash_received,
    class_fee_rows,
    class_finance_summary,
    collected_by_month,
    dashboard_kpis,
    expense_list,
    fee_item_breakdown,
    fee_structure_list,
    fee_totals,
    get_active_fee_structure,
    get_fee_structure,
    get_fee_upload_job,
    get_invoice,
    get_payment,
    income_statement,
    invoice_list,
    late_fee_proposals,
    ledger_entries,
    monthly_income_expense,
    outstanding_aging,
    outstanding_students,
    payment_list,
    payment_plan_rows,
    receipts_register,
    report_summary,
    school_finance_summary,
    student_balance,
    student_fee_rows,
    student_statement,
    top_expense_categories,
    trial_balance,
    trial_balance_at,
    vendor_list,
    year_finance_summary,
)

#: The contract, in full. ``test_interface.py`` asserts this set equals the
#: public readers defined in ``selectors`` — so a new reader cannot be added to
#: the module without being added here, and a name can never be exported twice
#: under two spellings.
__all__ = [
    "account_balances",
    "account_list",
    "approval_queue",
    "arrears_rows",
    "arrears_summary",
    "budget_variance",
    "cash_and_bank_balances",
    "cash_received",
    "class_fee_rows",
    "class_finance_summary",
    "collected_by_month",
    "dashboard_kpis",
    "expense_list",
    "fee_item_breakdown",
    "fee_structure_list",
    "fee_totals",
    "get_active_fee_structure",
    "get_fee_structure",
    "get_fee_upload_job",
    "get_invoice",
    "get_payment",
    "income_statement",
    "invoice_list",
    "late_fee_proposals",
    "ledger_entries",
    "monthly_income_expense",
    "outstanding_aging",
    "outstanding_students",
    "payment_list",
    "payment_plan_rows",
    "receipts_register",
    "report_summary",
    "school_finance_summary",
    "student_balance",
    "student_fee_rows",
    "student_statement",
    "top_expense_categories",
    "trial_balance",
    "trial_balance_at",
    "vendor_list",
    "year_finance_summary",
]


def public_readers() -> list[str]:
    """
    The readers ``selectors`` defines itself, in declaration order.

    Used by the contract test to prove ``__all__`` is neither short nor stale.
    Imported models and helpers live in ``selectors`` too, so this filters to
    functions that were actually defined there rather than merely imported.
    """
    return sorted(
        name
        for name, value in vars(_selectors).items()
        if not name.startswith("_")
        and callable(value)
        and getattr(value, "__module__", "") == _selectors.__name__
    )
