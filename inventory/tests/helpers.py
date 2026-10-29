import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model

from inventory import services
from inventory.models import Product


def demo_user(username: str):
    """The three users come from the data migration, not from fixtures."""
    return get_user_model().objects.get(username=username)


def make_product(name="Кабель 3×1.5", unit="m", price="0.80", currency="USD", stock=None):
    product = Product.objects.create(name=name, unit=unit, price=Decimal(price), currency=currency)
    if stock is not None:
        services.receive(demo_user("director"), product, Decimal(stock), key=uuid.uuid4())
        product.refresh_from_db()
    return product


def key() -> str:
    return str(uuid.uuid4())
