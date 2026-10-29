"""Parsing user-typed numbers and formatting quantities/money. Decimal only, never float."""

import re
from decimal import ROUND_HALF_UP, Decimal

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

UNIT_LABELS = {"pcs": gettext_lazy("шт"), "m": gettext_lazy("м")}
CURRENCY_LABELS = {"UZS": gettext_lazy("сум"), "USD": "$", "CNY": "¥"}
CURRENCY_ORDER = ("UZS", "USD", "CNY")

CENT = Decimal("0.01")
MAX_INPUT_LENGTH = 40
_NUMBER_RE = re.compile(r"([0-9]+)(?:\.([0-9]+))?")
_WHITESPACE_RE = re.compile(r"\s+")


class NumberError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def parse_decimal(raw, *, places: int, max_value: Decimal, allow_zero: bool = False) -> Decimal:
    """Accepts "12,5", "12.5", "1 000,25". Rejects signs, exponents, NaN/Infinity, too many places."""
    text = "" if raw is None else str(raw)
    if len(text) > MAX_INPUT_LENGTH:
        raise NumberError(_("Слишком длинное число"))
    text = _WHITESPACE_RE.sub("", text).replace(",", ".")
    if not text:
        raise NumberError(_("Введите число"))
    if text.startswith("-"):
        raise NumberError(_("Значение не может быть отрицательным"))
    match = _NUMBER_RE.fullmatch(text)
    if match is None:
        raise NumberError(_("Введите число, например 12,5"))
    fraction = (match.group(2) or "").rstrip("0")
    if len(fraction) > places:
        raise NumberError(_("Не более %(places)s знаков после запятой") % {"places": places})
    value = Decimal(text)
    if value == 0 and not allow_zero:
        raise NumberError(_("Значение должно быть больше нуля"))
    if value > max_value:
        raise NumberError(
            _("Слишком большое значение, максимум %(max)s") % {"max": format_number(max_value, 0)}
        )
    return value.quantize(Decimal(1).scaleb(-places))


def is_whole(value: Decimal) -> bool:
    return value == value.to_integral_value()


def format_number(value, places: int, *, strip_zeros: bool = False) -> str:
    """1234567.5 -> "1 234 567,5": space groups thousands, comma separates the fraction."""
    quantized = Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    whole, sep, fraction = f"{abs(quantized):f}".partition(".")
    if strip_zeros:
        fraction = fraction.rstrip("0")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    text = " ".join(groups)
    if fraction:
        text += "," + fraction
    if quantized < 0:
        text = "-" + text
    return text


def unit_label(unit: str) -> str:
    return str(UNIT_LABELS.get(unit, unit))


def currency_label(currency: str) -> str:
    return str(CURRENCY_LABELS.get(currency, currency))


def format_quantity(value, unit: str) -> str:
    return f"{format_number(value, 3, strip_zeros=True)} {unit_label(unit)}"


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def line_amount(quantity: Decimal, price: Decimal) -> Decimal:
    return money(quantity * price)


def format_money(value, currency: str) -> str:
    """Whole amounts drop the kopecks ("340 $"), others keep two places ("55,60 $")."""
    text = format_number(money(value), 2)
    if text.endswith(",00"):
        text = text[:-3]
    return f"{text} {currency_label(currency)}"


def currency_totals(rows) -> list[tuple[str, Decimal]]:
    """Per-currency sums of the already-rounded line amounts; never converted between currencies."""
    totals: dict[str, Decimal] = {}
    for row in rows:
        totals[row["currency"]] = totals.get(row["currency"], Decimal("0.00")) + row["amount"]
    return [(code, totals[code]) for code in CURRENCY_ORDER if code in totals]


def format_totals(totals) -> str:
    return "; ".join(format_money(amount, code) for code, amount in totals)
