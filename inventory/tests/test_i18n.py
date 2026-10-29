import gettext
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from inventory import services
from inventory.i18n_catalog import extract_project, read_po
from inventory.numbers import format_money, format_quantity

from .helpers import demo_user, key, make_product

LOCALE_DIR = Path(settings.BASE_DIR) / "locale" / "uz" / "LC_MESSAGES"


class CatalogTests(TestCase):
    def test_every_msgid_has_an_uzbek_translation(self):
        catalog = read_po(LOCALE_DIR / "django.po")
        missing = sorted(m for m in extract_project(Path(settings.BASE_DIR)) if not catalog.get(m))
        self.assertEqual(missing, [], "run: python manage.py compile_translations")

    def test_committed_mo_matches_the_po(self):
        catalog = {k: v for k, v in read_po(LOCALE_DIR / "django.po").items() if k}
        with open(LOCALE_DIR / "django.mo", "rb") as handle:
            compiled = gettext.GNUTranslations(handle)
        for msgid, msgstr in catalog.items():
            with self.subTest(msgid=msgid):
                self.assertEqual(compiled.gettext(msgid), msgstr, "stale .mo: run compile_translations")

    def test_placeholders_survive_translation(self):
        catalog = read_po(LOCALE_DIR / "django.po")
        import re

        for msgid, msgstr in catalog.items():
            with self.subTest(msgid=msgid):
                self.assertEqual(
                    sorted(re.findall(r"%\(\w+\)s", msgid)), sorted(re.findall(r"%\(\w+\)s", msgstr))
                )

    def test_units_and_currency_in_uzbek(self):
        with translation.override("uz"):
            self.assertEqual(format_quantity(Decimal("5"), "pcs"), "5 dona")
            self.assertEqual(format_quantity(Decimal("69.5"), "m"), "69,5 m")
            self.assertEqual(format_money(Decimal("12500000"), "UZS"), "12 500 000 soʻm")


class SwitcherTests(TestCase):
    def setUp(self):
        self.director = demo_user("director")
        self.client.force_login(self.director)
        self.product = make_product(name="Болт", unit="pcs", price="1000", currency="UZS", stock="5")

    def switch(self, language, next_url="/"):
        response = self.client.post(reverse("set_language"), {"language": language, "next": next_url})
        self.assertEqual(response.status_code, 302)
        return response

    def test_switcher_is_on_every_page(self):
        op = services.receive(self.director, self.product, Decimal("1"), key=key()).operation
        urls = [
            reverse("inventory:stock"), reverse("inventory:receive"), reverse("inventory:issue"),
            reverse("inventory:products"), reverse("inventory:product_create"),
            reverse("inventory:product_edit", args=[self.product.pk]), reverse("inventory:history"),
            reverse("inventory:cancel", args=[op.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), 'name="language" value="uz"')
        self.assertContains(self.client.get("/missing/"), 'name="language" value="uz"', status_code=404)
        self.client.logout()
        self.assertContains(self.client.get(reverse("login")), 'name="language" value="uz"')

    def test_pages_and_errors_switch_to_uzbek_and_back(self):
        self.switch("uz")
        response = self.client.get(reverse("inventory:stock"))
        self.assertContains(response, '<html lang="uz">')
        self.assertContains(response, "Qoldiqlar")
        self.assertContains(response, "5 dona")
        self.assertContains(response, "5 000 soʻm")
        self.assertContains(response, "Jami: 5 000 soʻm")
        self.assertContains(response, "Rahbar")

        response = self.client.post(
            reverse("inventory:issue"),
            {"product": self.product.pk, "quantity": "6", "object_name": "Obyekt", "idempotency_key": key()},
        )
        self.assertContains(response, "Omborda faqat 5 dona bor")

        response = self.client.post(
            reverse("inventory:issue"),
            {"product": self.product.pk, "quantity": "1,5", "object_name": "", "idempotency_key": key()},
        )
        self.assertContains(response, "Dona uchun miqdor butun son boʻlishi kerak")
        self.assertNotContains(response, "Обязательное поле")

        response = self.client.post(
            reverse("inventory:issue"),
            {"product": self.product.pk, "quantity": "1", "object_name": "Obyekt", "idempotency_key": key()},
            follow=True,
        )
        self.assertContains(response, "Chiqim saqlandi: Болт — 1 dona → Obyekt")

        self.switch("ru")
        response = self.client.get(reverse("inventory:stock"))
        self.assertContains(response, "Остатки")
        self.assertContains(response, "4 шт")

    def test_forbidden_page_is_translated(self):
        self.client.force_login(demo_user("manager"))
        self.switch("uz")
        response = self.client.get(reverse("inventory:receive"))
        self.assertContains(response, "Ruxsat yoʻq", status_code=403)
        self.assertContains(response, "Bosh sahifaga", status_code=403)

    def test_switch_keeps_the_page_and_rejects_external_next(self):
        response = self.switch("uz", next_url=reverse("inventory:history"))
        self.assertEqual(response["Location"], reverse("inventory:history"))
        response = self.switch("uz", next_url="https://evil.example/")
        self.assertEqual(response["Location"], "/")
