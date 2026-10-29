import re
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from inventory.models import Operation, Product

KEY_RE = re.compile(r'name="idempotency_key" value="([0-9a-f-]{36})"')
PASSWORDS = {"director": "Director2026!", "storekeeper": "Store2026!", "manager": "Manager2026!"}


def login(client, username):
    response = client.post(reverse("login"), {"username": username, "password": PASSWORDS[username]})
    assert response.status_code == 302, response.content
    return client


def form_key(client, url):
    response = client.get(url)
    match = KEY_RE.search(response.content.decode())
    assert match, "idempotency_key missing from the form"
    return match.group(1)


class EndToEndScenarioTests(TestCase):
    """The reference scenario, driven exactly like a browser: real login and real POSTs."""

    def test_full_scenario(self):
        director = login(self.client_class(), "director")
        storekeeper = login(self.client_class(), "storekeeper")
        manager = login(self.client_class(), "manager")

        # The director creates the product: 0,80 $ per metre.
        response = director.post(
            reverse("inventory:product_create"),
            {"name": "Кабель 3×1.5", "unit": "m", "price": "0,80", "currency": "USD"},
        )
        self.assertRedirects(response, reverse("inventory:products"))
        product = Product.objects.get(name="Кабель 3×1.5")

        # Storekeeper receives 100 m.
        response = storekeeper.post(
            reverse("inventory:receive"),
            {"product": product.pk, "quantity": "100", "comment": "", "idempotency_key": form_key(storekeeper, reverse("inventory:receive"))},
            follow=True,
        )
        self.assertContains(response, "Приход сохранён: Кабель 3×1.5 — 100 м")
        receipt = Operation.objects.get(type="in")

        response = director.get(reverse("inventory:stock"))
        self.assertContains(response, "100 м")
        self.assertContains(response, "80 $")

        # Storekeeper issues 30,5 m (comma input).
        response = storekeeper.post(
            reverse("inventory:issue"),
            {"product": product.pk, "quantity": "30,5", "object_name": "Объект «Чиланзар»", "comment": "",
             "idempotency_key": form_key(storekeeper, reverse("inventory:issue"))},
            follow=True,
        )
        self.assertContains(response, "Выдача сохранена: Кабель 3×1.5 — 30,5 м")
        issue = Operation.objects.get(type="out")

        for client in (director, manager):
            response = client.get(reverse("inventory:stock"))
            self.assertContains(response, "69,5 м")
            self.assertContains(response, "55,60 $")
            self.assertContains(response, "Итого: 55,60 $")
        response = storekeeper.get(reverse("inventory:stock"))
        self.assertContains(response, "69,5 м")
        self.assertNotContains(response, "55,60")
        self.assertNotContains(response, "0,80")

        # Issuing more than is left is refused, with the input kept.
        response = storekeeper.post(
            reverse("inventory:issue"),
            {"product": product.pk, "quantity": "70", "object_name": "Объект Б", "comment": "",
             "idempotency_key": form_key(storekeeper, reverse("inventory:issue"))},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "На складе только 69,5 м")
        self.assertContains(response, 'value="70"')
        self.assertContains(response, 'value="Объект Б"')

        # The receipt cannot be cancelled: part of it is already issued.
        response = director.post(reverse("inventory:cancel", args=[receipt.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Нельзя отменить приход: из него уже выдано 30,5 м, на складе осталось только 69,5 м"
        )
        product.refresh_from_db()
        self.assertEqual(product.stock, Decimal("69.5"))

        # Cancelling the issue brings the stock back to 100 m.
        response = director.post(reverse("inventory:cancel", args=[issue.pk]), follow=True)
        self.assertContains(response, "Операция отменена")
        response = director.get(reverse("inventory:stock"))
        self.assertContains(response, "100 м")
        self.assertContains(response, "80 $")
        product.refresh_from_db()
        self.assertEqual(product.stock, Decimal("100"))

        # History shows the cancellation as its own event and marks the original.
        response = manager.get(reverse("inventory:history"))
        kinds = [event["kind"] for event in response.context["events"]]
        self.assertEqual(kinds, ["cancel", "operation", "operation"])
        self.assertContains(response, "отменено: director")
        self.assertEqual(Operation.objects.count(), 2)
