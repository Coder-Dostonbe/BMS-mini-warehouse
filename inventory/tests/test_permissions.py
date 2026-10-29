from decimal import Decimal

from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from accounts.permissions import MATRIX, Perm, has_perm
from inventory import services
from inventory.models import Operation, Product

from .helpers import demo_user, key, make_product

PUBLIC_URL_NAMES = {"login", "logout", "set_language"}


def iter_url_patterns(patterns=None, namespace=""):
    for pattern in get_resolver().url_patterns if patterns is None else patterns:
        if isinstance(pattern, URLResolver):
            ns = f"{namespace}{pattern.namespace}:" if pattern.namespace else namespace
            yield from iter_url_patterns(pattern.url_patterns, ns)
        elif isinstance(pattern, URLPattern):
            yield f"{namespace}{pattern.name}", pattern


def snapshot():
    return (
        list(Product.objects.order_by("pk").values_list("pk", "name", "unit", "price", "currency", "stock", "is_archived")),
        list(Operation.objects.order_by("pk").values_list("pk", "type", "quantity", "cancelled_at")),
    )


class MatrixTests(TestCase):
    def test_matrix_is_default_deny(self):
        self.assertFalse(has_perm(None, Perm.VIEW_STOCK))
        nobody = demo_user("director")
        nobody.role = ""
        self.assertFalse(any(has_perm(nobody, perm) for perm in Perm))
        nobody.role = "admin"
        self.assertFalse(any(has_perm(nobody, perm) for perm in Perm))
        self.assertEqual(set(MATRIX), {"director", "storekeeper", "manager"})


class EveryUrlIsProtectedTests(TestCase):
    def test_every_view_is_decorated(self):
        for name, pattern in iter_url_patterns():
            if name in PUBLIC_URL_NAMES:
                continue
            with self.subTest(url=name):
                self.assertTrue(hasattr(pattern.callback, "required_perms"), f"{name} has no @role_required")

    def test_anonymous_is_sent_to_login_from_everywhere(self):
        login_url = reverse("login")
        for name, pattern in iter_url_patterns():
            if name in PUBLIC_URL_NAMES:
                continue
            url = reverse(name, kwargs={arg: 1 for arg in pattern.pattern.converters})
            for method in ("get", "post"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response["Location"].startswith(f"{login_url}?next="), response["Location"])


class RoleUrlMatrixTests(TestCase):
    """Every role x every URL x GET/POST: expected status, and no DB change when refused."""

    def setUp(self):
        self.director = demo_user("director")
        self.product = make_product(name="Кабель", unit="m", price="2.00", stock="10")
        self.spare = make_product(name="Без движений", unit="pcs", price="1.00")
        self.issue = services.issue(self.director, self.product, Decimal("1"), "Объект", key=key()).operation

    def cases(self):
        p, s, op = self.product.pk, self.spare.pk, self.issue.pk
        product_data = {"name": "Новый", "unit": "pcs", "price": "5", "currency": "UZS"}
        all_roles = {"director": 200, "storekeeper": 200, "manager": 200}
        movers = {"director": None, "storekeeper": None, "manager": 403}
        only_director = {"storekeeper": 403, "manager": 403}
        return [
            ("GET", reverse("inventory:stock"), None, all_roles),
            ("POST", reverse("inventory:stock"), {}, {"director": 405, "storekeeper": 405, "manager": 405}),
            ("GET", reverse("inventory:export"), None, all_roles),
            ("GET", reverse("inventory:receive"), None, {**movers, "director": 200, "storekeeper": 200}),
            ("POST", reverse("inventory:receive"), lambda: {"product": p, "quantity": "1", "idempotency_key": key()},
             {**movers, "director": 302, "storekeeper": 302}),
            ("GET", reverse("inventory:issue"), None, {**movers, "director": 200, "storekeeper": 200}),
            ("POST", reverse("inventory:issue"), lambda: {"product": p, "quantity": "1", "object_name": "Объект", "idempotency_key": key()},
             {**movers, "director": 302, "storekeeper": 302}),
            ("GET", reverse("inventory:products"), None, {**only_director, "director": 200}),
            ("GET", reverse("inventory:product_create"), None, {**only_director, "director": 200}),
            ("POST", reverse("inventory:product_create"), lambda: product_data, {**only_director, "director": 302}),
            ("GET", reverse("inventory:product_edit", args=[s]), None, {**only_director, "director": 200}),
            ("POST", reverse("inventory:product_edit", args=[s]),
             lambda: {"name": "Переименован", "unit": "m", "price": "3", "currency": "USD"}, {**only_director, "director": 302}),
            ("GET", reverse("inventory:product_archive", args=[s]), None, {**only_director, "director": 405}),
            ("POST", reverse("inventory:product_archive", args=[s]), {}, {**only_director, "director": 302}),
            ("POST", reverse("inventory:product_restore", args=[s]), {}, {**only_director, "director": 302}),
            ("GET", reverse("inventory:product_delete", args=[s]), None, {**only_director, "director": 405}),
            ("GET", reverse("inventory:history"), None, {"director": 200, "storekeeper": 403, "manager": 200}),
            ("POST", reverse("inventory:history"), {}, {"director": 405, "storekeeper": 403, "manager": 405}),
            ("GET", reverse("inventory:cancel", args=[op]), None, {**only_director, "director": 200}),
            ("POST", reverse("inventory:cancel", args=[op]), {}, {**only_director, "director": 302}),
            ("POST", reverse("inventory:product_delete", args=[s]), {}, {**only_director, "director": 302}),
        ]

    def test_matrix(self):
        clients = {}
        for role in ("director", "storekeeper", "manager"):
            clients[role] = self.client_class()
            clients[role].force_login(demo_user(role))

        # Refused roles first, so the director's successful writes do not change their targets.
        for method, url, data, expected in self.cases():
            for role in ("storekeeper", "manager", "director"):
                status = expected[role]
                payload = data() if callable(data) else data
                with self.subTest(role=role, method=method, url=url):
                    before = snapshot()
                    client = clients[role]
                    response = client.get(url) if method == "GET" else client.post(url, payload or {})
                    self.assertEqual(response.status_code, status, response.content[:300])
                    if status in (403, 405):
                        self.assertEqual(snapshot(), before, "refused request changed the database")
                    if status == 403:
                        self.assertContains(response, "Доступ запрещён", status_code=403)
                        self.assertContains(response, 'href="/"', status_code=403)

    def test_manager_sees_no_movement_buttons(self):
        self.client.force_login(demo_user("manager"))
        response = self.client.get(reverse("inventory:stock"))
        self.assertNotContains(response, reverse("inventory:receive"))
        self.assertNotContains(response, reverse("inventory:issue"))
        self.assertContains(response, reverse("inventory:export"))

    def test_storekeeper_has_no_history_or_products_links(self):
        self.client.force_login(demo_user("storekeeper"))
        response = self.client.get(reverse("inventory:stock"))
        self.assertNotContains(response, reverse("inventory:history"))
        self.assertNotContains(response, reverse("inventory:products"))
        self.assertContains(response, reverse("inventory:receive"))

    def test_role_less_user_is_denied_everything(self):
        user = demo_user("manager")
        user.role = ""
        user.save()
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("inventory:stock")).status_code, 403)
