"""
apps/core/tests/test_money.py
-------------------------------
The single definition of money arithmetic, tested at the definition.

Every figure the school prints goes through these functions, so the rules that
are easy to get subtly wrong — how a half pesewa rounds, what an empty cell in
an uploaded spreadsheet becomes, whether a rate is ever a float — are pinned
here rather than re-argued at each call site.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase

from apps.core.money import (
    currency_code,
    format_money,
    money_sum,
    percent,
    to_money,
)
from apps.core.selectors import school_settings_values

D = Decimal


class ToMoneyTests(SimpleTestCase):
    def test_every_shape_of_input_becomes_two_decimal_places(self):
        for value, expected in (
            (D("10"), D("10.00")),
            (D("10.005"), D("10.01")),
            (10, D("10.00")),
            ("10", D("10.00")),
            (" 10.00 ", D("10.00")),
            ("1,234.5", D("1234.50")),
        ):
            with self.subTest(value=value):
                self.assertEqual(to_money(value), expected)

    def test_a_half_rounds_up_not_to_even(self):
        """
        Python's builtin round() would send both of these to 10.12.

        Two receipts that round the same half-pesewa to different figures look
        like a calculation error to whoever is holding them, so the school's
        rule is the one a till follows: five or more rounds up.
        """
        self.assertEqual(to_money(D("10.125")), D("10.13"))
        self.assertEqual(to_money(D("10.135")), D("10.14"))

    def test_a_float_is_read_through_str_not_directly(self):
        # Decimal(0.1) is 0.1000000000000000055511151231257827; str(0.1) is "0.1".
        self.assertEqual(to_money(0.1), D("0.10"))
        self.assertEqual(to_money(1234.5678), D("1234.57"))

    def test_nothing_and_blank_are_zero_not_an_error(self):
        self.assertEqual(to_money(None), D("0.00"))
        self.assertEqual(to_money(""), D("0.00"))

    def test_text_that_is_not_money_is_refused_loudly(self):
        for bad in ("abc", "12.3.4", "GHS 100"):
            with self.subTest(value=bad), self.assertRaises(ValidationError):
                to_money(bad)


class MoneySumTests(SimpleTestCase):
    def test_each_row_is_money_before_it_is_added(self):
        """
        The total of a printed column is the sum of the printed rows.

        0.005 shows as 0.01 on the row, so it has to count as 0.01 in the total;
        totalling the unrounded figures and rounding once at the end would give
        a column whose rows visibly do not add up.
        """
        self.assertEqual(money_sum([D("0.005"), "0.005", 1]), D("1.02"))

    def test_an_empty_column_is_zero(self):
        self.assertEqual(money_sum([]), D("0.00"))


class PercentTests(SimpleTestCase):
    def test_rate_is_one_decimal_place_half_up(self):
        self.assertEqual(percent(D("100"), D("650")), D("15.4"))
        self.assertEqual(percent(D("1"), D("8")), D("12.5"))

    def test_nothing_billed_has_no_rate(self):
        self.assertIsNone(percent(D("0"), D("0")))
        self.assertIsNone(percent(D("5"), D("0")))

    def test_inputs_are_quantized_first(self):
        self.assertEqual(percent("200", "1300"), D("15.4"))


class FormatMoneyTests(SimpleTestCase):
    def test_grouping_and_currency_prefix(self):
        self.assertEqual(format_money(D("1234.5"), currency="GHS"), "GHS 1,234.50")

    def test_a_credit_shows_its_sign(self):
        self.assertEqual(format_money(D("-1234.5"), currency="GHS"), "GHS -1,234.50")

    def test_without_currency_for_a_column_that_names_it_in_the_header(self):
        self.assertEqual(format_money(D("1234.5"), with_currency=False), "1,234.50")

    def test_a_float_is_not_allowed_to_choose_the_rounding(self):
        self.assertEqual(format_money(10.125, currency="GHS"), "GHS 10.13")


class CurrencyCodeTests(TestCase):
    def test_the_environment_setting_is_the_fallback(self):
        self.assertEqual(currency_code(), "GHS")

    def test_the_saved_school_setting_wins(self):
        from apps.core.models import SchoolSettings

        SchoolSettings.objects.update_or_create(
            pk=SchoolSettings.SINGLETON_PK, defaults={"currency": "NGN"}
        )
        school_only = SchoolSettings.objects.get(pk=SchoolSettings.SINGLETON_PK)
        with self.assertNumQueries(1):
            self.assertEqual(currency_code(), school_only.currency)


class OneReadPerRequestTests(TestCase):
    """
    A page that needs both the school name and the currency pays one query.

    The Director home runs a query budget; the currency became a database read
    when the settings page gained it, so the read is shared with the context
    processor instead of doubling.
    """

    def make_request(self):
        from django.test import RequestFactory

        return RequestFactory().get("/")

    def test_the_second_call_reuses_the_first(self):
        request = self.make_request()
        with self.assertNumQueries(1):
            values = school_settings_values(request)
            again = school_settings_values(request)
            self.assertEqual(currency_code(request), values["currency"])
        self.assertIs(again, values)

    def test_two_requests_do_not_share_a_memo(self):
        first, second = self.make_request(), self.make_request()
        with self.assertNumQueries(2):
            school_settings_values(first)
            school_settings_values(second)


class MoneyTemplateFilterTests(TestCase):
    def render(self, source, **context):
        return Template("{% load core_extras %}" + source).render(Context(context))

    def test_a_table_passes_the_currency_the_page_already_has(self):
        with self.assertNumQueries(0):
            rendered = self.render(
                "{% for row in rows %}{{ row|money:currency }}{% endfor %}",
                rows=[D("1"), D("2"), D("3")],
                currency="GHS",
            )
        self.assertEqual(rendered, "GHS 1.00GHS 2.00GHS 3.00")

    def test_a_single_figure_can_read_the_code_itself(self):
        # One query for the settings row. Right for a headline total, wrong for
        # a hundred rows, which is why the table form above passes the code in.
        with self.assertNumQueries(1):
            self.assertEqual(
                self.render("{{ total|money }}", total=D("250")), "GHS 250.00"
            )

    def test_the_digits_filter_is_for_a_column_with_a_currency_header(self):
        self.assertEqual(
            self.render("{{ total|money_digits }}", total=D("1234.5")), "1,234.50"
        )

    def test_a_credit_is_printed_with_its_sign_not_only_its_colour(self):
        self.assertEqual(
            self.render("{{ total|money:currency }}", total=D("-40.00"), currency="GHS"),
            "GHS -40.00",
        )
