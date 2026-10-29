"""
apps/dashboards/tests/test_director_finance_contract.py
---------------------------------------------------------
A static guard for the rule the Director portal is built on:

    Director screens never calculate money. They display what
    ``apps.finance.interface`` returns.

This is not a style preference. Before this checkpoint the same arithmetic —
billed, collected, outstanding, collection rate — existed in
``dashboards/selectors.py`` *and* in ``finance/selectors.py``, and the two
copies disagreed about whether a waiver was still a debt. A screen that
re-adds the arithmetic cannot be reconciled with the Finance page it is
supposed to match, and the difference only shows up when a headteacher is
chasing a fee the school already forgave.

Tests of behaviour can drift; a test of structure cannot be quietly worked
around, which is why this one reads the source.
"""

from __future__ import annotations

import ast
from pathlib import Path

from django.test import SimpleTestCase

DASHBOARDS_DIR = Path(__file__).resolve().parent.parent

#: Anything a Director may not import from Finance: the ledger tables
#: (``models``), the writes (``services``), and the raw queries (``selectors``)
#: — everything except ``apps.finance.interface``, which is the contract those
#: three are hidden behind. Importing ``selectors`` directly is legal Python and
#: still lets a screen pick a reader that was never meant to leave the app; the
#: façade is where a name becomes part of the Director's promise.
FORBIDDEN_MODULES = (
    "apps.finance.models",
    "apps.finance.services",
    "apps.finance.selectors",
)

#: Words that mark an expression as money, for the arithmetic check.
MONEY_WORDS = (
    "billed",
    "collected",
    "paid",
    "outstanding",
    "amount",
    "fee",
    "income",
    "expense",
    "cash",
    "bank",
    "balance",
    "receipt",
    "adjustment",
)

#: Building an .xlsx or a PDF in a screen is how the export helpers get bypassed.
FORBIDDEN_LIBRARY_IMPORTS = ("openpyxl", "weasyprint", "reportlab")

MONEY_OPS = (ast.Sub, ast.Add, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)


def production_sources() -> list[Path]:
    """Every Dashboard source file that ships, tests excluded."""
    return sorted(
        path
        for path in DASHBOARDS_DIR.rglob("*.py")
        if "tests" not in path.relative_to(DASHBOARDS_DIR).parts
    )


def find_violations(source: str) -> list[str]:
    """Structural rule breaches in one module's source, as readable strings."""
    violations: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(FORBIDDEN_MODULES) or alias.name.startswith(
                    FORBIDDEN_LIBRARY_IMPORTS
                ):
                    violations.append(
                        f"line {node.lineno}: imports {alias.name!r}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # ``from apps.finance import selectors as finance`` names a whole
            # forbidden submodule without putting it in ``module``, so every
            # alias is qualified before it is checked. Without this, the import
            # style this project actually uses slipped past the guard.
            banned = [
                alias.name
                for alias in node.names
                if f"{module}.{alias.name}".startswith(FORBIDDEN_MODULES)
            ]
            if banned:
                violations.append(
                    f"line {node.lineno}: imports {', '.join(banned)} from "
                    f"{module!r} — ask apps.finance.interface for the figure "
                    "instead"
                )
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, MONEY_OPS):
                continue
            text = ast.get_source_segment(source, node) or ""
            lowered = text.lower()
            if any(word in lowered for word in MONEY_WORDS):
                violations.append(
                    f"line {node.lineno}: calculates money ({text.strip()[:70]}) "
                    "instead of reading it from apps.finance.interface"
                )
    return violations


class DirectorFinanceContractTests(SimpleTestCase):
    def test_there_is_something_to_guard(self):
        """A guard that scans nothing proves nothing."""
        names = {path.name for path in production_sources()}
        self.assertIn("selectors.py", names)
        self.assertIn("views_director.py", names)

    def test_no_dashboard_module_breaches_the_contract(self):
        report = []
        for path in production_sources():
            found = find_violations(path.read_text(encoding="utf-8"))
            if found:
                report.append(
                    f"{path.relative_to(DASHBOARDS_DIR)}:\n  " + "\n  ".join(found)
                )
        self.assertEqual(report, [], "\n".join(report))

    # ── The guard itself, so a passing suite above means something ──────────

    def test_catches_importing_the_ledger_models(self):
        violations = find_violations(
            "from apps.finance.models import Invoice\nx = 1\n"
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("apps.finance.models", violations[0])

    def test_catches_importing_a_finance_service_and_writing(self):
        violations = find_violations("from apps.finance.services import record_payment\n")
        self.assertEqual(len(violations), 1)

    def test_catches_re_adding_the_fee_arithmetic(self):
        violations = find_violations("outstanding = billed - collected\n")
        self.assertEqual(len(violations), 1)
        self.assertIn("calculates money", violations[0])

    def test_catches_a_division_that_produces_a_rate(self):
        violations = find_violations("rate = collected / billed * 100\n")
        self.assertTrue(violations)

    def test_lets_the_read_interface_through(self):
        allowed = (
            "from apps.finance import interface as finance\n"
            "def kpis(term):\n"
            "    fees = finance.fee_totals(term=term)\n"
            "    return {'fees_billed': fees['billed'], "
            "'fees_outstanding': fees['outstanding']}\n"
        )
        self.assertEqual(find_violations(allowed), [])

    def test_catches_a_reader_bought_from_the_module_behind_the_facade(self):
        """
        ``from apps.finance import selectors`` never names a forbidden module in
        the ``module`` attribute, so a guard that only checks ``module`` waves
        through the exact import style every Director screen already uses.
        """
        for source in (
            "from apps.finance import selectors as finance\n",
            "from apps.finance.selectors import fee_totals\n",
            "from apps.finance import models\n",
        ):
            with self.subTest(source=source):
                self.assertEqual(len(find_violations(source)), 1)

    def test_lets_non_financial_arithmetic_through(self):
        # Percentages of scores and day counts are the Director's own business.
        allowed = "pass_rate = round(passing / total_entries * 100, 1)\n"
        self.assertEqual(find_violations(allowed), [])

    def test_catches_a_screen_building_its_own_spreadsheet(self):
        violations = find_violations("import openpyxl\n")
        self.assertEqual(len(violations), 1)
        self.assertIn("openpyxl", violations[0])
