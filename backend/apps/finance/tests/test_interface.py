"""
apps/finance/tests/test_interface.py
------------------------------------
The façade in ``apps.finance.interface`` is a boundary, not a second
implementation. Two failure modes are worth pinning, because neither shows up
as a wrong number on the day it is introduced:

* a name that is *copied* into the façade instead of re-exported, so the
  Director and the Finance officer are reading two implementations that agree
  until the first waiver;
* a name that exists in ``selectors`` but was never added to the contract, so
  the next screen imports ``selectors`` directly and the boundary quietly stops
  being the boundary.

Both are structural, so both are checked by reading the source, which cannot be
worked around the way a behaviour test can.
"""

from __future__ import annotations

import ast
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from apps.dashboards.tests.fixtures import (
    make_accounts,
    make_class,
    make_fee_structure,
    make_posted_invoice,
    make_student_user,
    make_term,
    make_year,
)
from apps.finance import interface, selectors

SOURCE = Path(interface.__file__).read_text(encoding="utf-8")


class FacadeIsNotAnImplementationTests(SimpleTestCase):
    def test_every_exported_name_is_the_selectors_function_itself(self):
        """
        Identity, not equality. A wrapper that calls through would pass an
        equality test and still be a second place to keep in step.
        """
        for name in interface.__all__:
            with self.subTest(name=name):
                self.assertIs(getattr(interface, name), getattr(selectors, name))

    def test_the_facade_contains_no_arithmetic(self):
        ops = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
        maths = [
            f"line {node.lineno}"
            for node in ast.walk(ast.parse(SOURCE))
            if isinstance(node, ast.BinOp) and isinstance(node.op, ops)
        ]
        self.assertEqual(
            maths,
            [],
            "apps.finance.interface must re-export figures, never derive them",
        )

    def test_the_facade_imports_only_selectors(self):
        """
        Every name the boundary touches must resolve to the module behind it —
        including ``from apps.finance import selectors``, which names
        ``apps.finance`` as the module and only puts ``selectors`` in the alias.
        """
        offenders = []
        for node in ast.walk(ast.parse(SOURCE)):
            if not isinstance(node, ast.ImportFrom) or not (
                (node.module or "").startswith("apps.")
            ):
                continue
            for alias in node.names:
                path = f"{node.module}.{alias.name}"
                if not path.startswith("apps.finance.selectors"):
                    offenders.append(path)
        self.assertEqual(
            offenders,
            [],
            "the boundary may reach only the queries it is a contract for",
        )

    def test_the_facade_exports_functions_and_nothing_else(self):
        """A model class or a service smuggled in would widen the contract."""
        for name in interface.__all__:
            with self.subTest(name=name):
                value = getattr(interface, name)
                self.assertTrue(callable(value))
                self.assertEqual(value.__module__, selectors.__name__)


class FacadeCoversTheWholeContractTests(SimpleTestCase):
    def test_every_public_reader_in_selectors_is_exported(self):
        """A reader nobody can reach through the boundary invites a bypass."""
        self.assertEqual(
            sorted(interface.public_readers()),
            sorted(interface.__all__),
            "add the new reader to __all__ or hide it behind an underscore",
        )

    def test_the_contract_has_no_stale_or_duplicated_name(self):
        self.assertEqual(len(interface.__all__), len(set(interface.__all__)))
        for name in interface.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(selectors, name))

    def test_the_contract_is_a_sane_size(self):
        """
        Not a number anyone should defend — a tripwire for an accidental
        ``from .selectors import *``, which would export every imported model
        and turn the boundary back into the module behind it.
        """
        self.assertGreater(len(interface.__all__), 20)
        self.assertEqual(len(interface.__all__), len(interface.public_readers()))


class FacadeAnswersTheMoneyQuestionsTests(TestCase):
    """The boundary is useful: a figure asked through it is the real figure."""

    @classmethod
    def setUpTestData(cls):
        cls.year = make_year()
        cls.term = make_term(cls.year)
        cls.school_class = make_class(cls.year)
        make_accounts()
        cls.structure = make_fee_structure(cls.school_class, cls.term)
        cls.student = make_student_user(cls.school_class, admission_number="FACE-001")
        make_posted_invoice(cls.student, cls.term, cls.structure)

    def test_fee_totals_reach_the_screen_unchanged(self):
        fees = interface.fee_totals(term=self.term)
        self.assertEqual(str(fees["billed"]), "500.00")
        self.assertEqual(str(fees["outstanding"]), "500.00")

    def test_a_pupil_owes_the_same_through_either_door(self):
        self.assertEqual(
            interface.student_balance(student_id=self.student.pk),
            selectors.student_balance(student_id=self.student.pk),
        )

    def test_the_books_balance_the_same_through_either_door(self):
        self.assertEqual(interface.trial_balance_at(), selectors.trial_balance_at())
