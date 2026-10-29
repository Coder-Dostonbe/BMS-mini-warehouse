from decimal import Decimal

from django.test import SimpleTestCase

from inventory.models import MAX_PRICE, MAX_QUANTITY
from inventory.numbers import (
    NumberError,
    currency_totals,
    format_money,
    format_number,
    format_quantity,
    format_totals,
    line_amount,
    parse_decimal,
)


def qty(raw):
    return parse_decimal(raw, places=3, max_value=MAX_QUANTITY)


class ParseDecimalTests(SimpleTestCase):
    def test_accepts_comma_dot_and_spaces(self):
        self.assertEqual(qty("12,5"), Decimal("12.500"))
        self.assertEqual(qty("12.5"), Decimal("12.500"))
        self.assertEqual(qty(" 1 000,25 "), Decimal("1000.250"))
        self.assertEqual(qty("1 000"), Decimal("1000.000"))
        self.assertEqual(qty("0,001"), Decimal("0.001"))
        self.assertEqual(qty("007"), Decimal("7.000"))

    def test_trailing_zeros_do_not_count_as_places(self):
        self.assertEqual(qty("1,2340"), Decimal("1.234"))

    def test_result_is_decimal_never_float(self):
        self.assertIsInstance(qty("0,1"), Decimal)

    def test_rejects_garbage(self):
        for raw in [
            "", "   ", None, "NaN", "nan", "Infinity", "-Infinity", "inf", "1e5", "1E5", "1e-3",
            "+5", "abc", "1,2,3", "1.2.3", "5,", ",5", "0x10", "١٢", "½", "12 кг", "--1",
        ]:
            with self.subTest(raw=raw), self.assertRaises(NumberError):
                qty(raw)

    def test_rejects_negative_with_clear_message(self):
        with self.assertRaisesMessage(NumberError, "не может быть отрицательным"):
            qty("-5")

    def test_rejects_zero(self):
        for raw in ["0", "0,000", "000"]:
            with self.subTest(raw=raw), self.assertRaisesMessage(NumberError, "больше нуля"):
                qty(raw)

    def test_zero_allowed_for_price(self):
        self.assertEqual(
            parse_decimal("0", places=2, max_value=MAX_PRICE, allow_zero=True), Decimal("0.00")
        )

    def test_rejects_more_than_three_places(self):
        with self.assertRaisesMessage(NumberError, "Не более 3 знаков"):
            qty("1,2345")

    def test_rejects_too_large(self):
        self.assertEqual(qty("1000000000"), MAX_QUANTITY)
        with self.assertRaisesMessage(NumberError, "Слишком большое"):
            qty("1000000000,001")
        with self.assertRaises(NumberError):
            qty("9" * 39)
        with self.assertRaisesMessage(NumberError, "Слишком длинное"):
            qty("1" * 41)

    def test_price_limits(self):
        price = lambda raw: parse_decimal(raw, places=2, max_value=MAX_PRICE, allow_zero=True)  # noqa: E731
        self.assertEqual(price("987 654,32"), Decimal("987654.32"))
        with self.assertRaisesMessage(NumberError, "Не более 2 знаков"):
            price("1,005")
        with self.assertRaises(NumberError):
            price("100000000000,01")


class FormatTests(SimpleTestCase):
    def test_quantity_has_no_trailing_zeros(self):
        self.assertEqual(format_quantity(Decimal("69.500"), "m"), "69,5 м")
        self.assertEqual(format_quantity(Decimal("100.000"), "m"), "100 м")
        self.assertEqual(format_quantity(Decimal("3.000"), "pcs"), "3 шт")
        self.assertEqual(format_quantity(Decimal("0.001"), "m"), "0,001 м")
        self.assertEqual(format_quantity(Decimal("1234567.250"), "m"), "1 234 567,25 м")

    def test_number_grouping(self):
        self.assertEqual(format_number(Decimal("0"), 0), "0")
        self.assertEqual(format_number(Decimal("999"), 0), "999")
        self.assertEqual(format_number(Decimal("1000"), 0), "1 000")
        self.assertEqual(format_number(Decimal("12500000"), 0), "12 500 000")
        self.assertEqual(format_number(Decimal("-1234.5"), 2), "-1 234,50")

    def test_three_times_ten_cents_is_exactly_thirty_cents(self):
        amount = line_amount(Decimal("3"), Decimal("0.10"))
        self.assertEqual(amount, Decimal("0.30"))
        self.assertEqual(format_money(amount, "USD"), "0,30 $")

    def test_money_rounds_half_up(self):
        self.assertEqual(line_amount(Decimal("1.005"), Decimal("1.00")), Decimal("1.01"))
        self.assertEqual(line_amount(Decimal("0.125"), Decimal("1.00")), Decimal("0.13"))
        self.assertEqual(line_amount(Decimal("69.5"), Decimal("0.80")), Decimal("55.60"))

    def test_money_format(self):
        self.assertEqual(format_money(Decimal("55.60"), "USD"), "55,60 $")
        self.assertEqual(format_money(Decimal("340.00"), "USD"), "340 $")
        self.assertEqual(format_money(Decimal("12500000"), "UZS"), "12 500 000 сум")
        self.assertEqual(format_money(Decimal("1200"), "CNY"), "1 200 ¥")

    def test_totals_are_per_currency_without_conversion(self):
        rows = [
            {"currency": "USD", "amount": Decimal("300.00")},
            {"currency": "UZS", "amount": Decimal("12000000.00")},
            {"currency": "CNY", "amount": Decimal("1200.00")},
            {"currency": "USD", "amount": Decimal("40.00")},
            {"currency": "UZS", "amount": Decimal("500000.00")},
        ]
        totals = currency_totals(rows)
        self.assertEqual(
            totals,
            [("UZS", Decimal("12500000.00")), ("USD", Decimal("340.00")), ("CNY", Decimal("1200.00"))],
        )
        self.assertEqual(format_totals(totals), "12 500 000 сум; 340 $; 1 200 ¥")
