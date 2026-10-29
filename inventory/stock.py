"""The stock report shared by the HTML page and the Excel export.

Prices are cut at the query: a role without VIEW_PRICES never has them loaded.
"""

from dataclasses import dataclass, field

from accounts.permissions import Perm, has_perm

from .models import Product, name_key
from .numbers import currency_totals, line_amount

QUERY_MAX_LENGTH = 200


@dataclass
class StockReport:
    rows: list[dict]
    with_prices: bool
    query: str
    archived: bool
    totals: list = field(default_factory=list)


def clean_query(raw) -> str:
    return " ".join(str(raw or "").split())[:QUERY_MAX_LENGTH]


def stock_report(user, *, query="", archived=False) -> StockReport:
    with_prices = has_perm(user, Perm.VIEW_PRICES)
    archived = bool(archived) and has_perm(user, Perm.MANAGE_PRODUCTS)
    query = clean_query(query)

    products = Product.objects.filter(is_archived=archived)
    if query:
        products = products.filter(name_key__contains=name_key(query))

    columns = ["id", "name", "unit", "stock"]
    if with_prices:
        columns += ["price", "currency"]
    rows = list(products.values(*columns))

    totals = []
    if with_prices:
        for row in rows:
            row["amount"] = line_amount(row["stock"], row["price"])
        totals = currency_totals(rows)
    return StockReport(rows=rows, with_prices=with_prices, query=query, archived=archived, totals=totals)
