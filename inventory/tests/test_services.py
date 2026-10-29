import uuid
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import TestCase

from inventory import services
from inventory.models import ImmutableRecordError, Operation, Product
from inventory.services import DomainError

from .helpers import demo_user, key, make_product


class MovementTests(TestCase):
    def setUp(self):
        self.director = demo_user("director")
        self.storekeeper = demo_user("storekeeper")
        self.manager = demo_user("manager")
        self.product = make_product()

    def stock(self):
        self.product.refresh_from_db()
        return self.product.stock

    def test_receive_and_issue(self):
        services.receive(self.storekeeper, self.product, Decimal("100"), key=key())
        services.issue(self.storekeeper, self.product, Decimal("30.5"), "Объект А", key=key())
        self.assertEqual(self.stock(), Decimal("69.5"))

    def test_issue_exactly_the_whole_stock_is_allowed(self):
        services.receive(self.director, self.product, Decimal("5.25"), key=key())
        services.issue(self.director, self.product, Decimal("5.25"), "Объект", key=key())
        self.assertEqual(self.stock(), Decimal("0"))

    def test_issue_one_thousandth_more_than_stock_is_rejected(self):
        services.receive(self.director, self.product, Decimal("69.5"), key=key())
        with self.assertRaisesMessage(DomainError, "На складе только 69,5 м"):
            services.issue(self.director, self.product, Decimal("69.501"), "Объект", key=key())
        self.assertEqual(self.stock(), Decimal("69.5"))
        self.assertEqual(Operation.objects.filter(type="out").count(), 0)

    def test_no_binary_float_drift(self):
        # With F() arithmetic on SQLite REAL this ends at -2.8e-17 and violates the CHECK.
        services.receive(self.director, self.product, Decimal("0.3"), key=key())
        services.issue(self.director, self.product, Decimal("0.1"), "А", key=key())
        services.issue(self.director, self.product, Decimal("0.2"), "Б", key=key())
        self.assertEqual(self.stock(), Decimal("0"))
        for _ in range(10):
            services.receive(self.director, self.product, Decimal("0.1"), key=key())
        services.issue(self.director, self.product, Decimal("1"), "В", key=key())
        self.assertEqual(self.stock(), Decimal("0"))

    def test_pieces_must_be_whole(self):
        bolts = make_product(name="Болт", unit="pcs", price="1.00")
        with self.assertRaisesMessage(DomainError, "Для штук количество должно быть целым"):
            services.receive(self.director, bolts, Decimal("1.5"), key=key())
        services.receive(self.director, bolts, Decimal("3"), key=key())
        bolts.refresh_from_db()
        self.assertEqual(bolts.stock, Decimal("3"))

    def test_float_is_refused(self):
        with self.assertRaises(TypeError):
            services.receive(self.director, self.product, 0.1, key=key())

    def test_bad_quantities(self):
        for bad in [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), Decimal("0.0001")]:
            with self.subTest(qty=bad), self.assertRaises(DomainError):
                services.receive(self.director, self.product, bad, key=key())
        self.assertEqual(self.stock(), Decimal("0"))

    def test_stock_ceiling(self):
        big = Decimal("1000000000")
        for _ in range(99):
            services.receive(self.director, self.product, big, key=key())
        with self.assertRaisesMessage(DomainError, "Превышен"):
            services.receive(self.director, self.product, big, key=key())

    def test_object_is_required_for_issue(self):
        services.receive(self.director, self.product, Decimal("10"), key=key())
        for blank in ["", "   ", "\t\n"]:
            with self.subTest(blank=blank), self.assertRaisesMessage(DomainError, "Укажите объект"):
                services.issue(self.director, self.product, Decimal("1"), blank, key=key())
        with self.assertRaisesMessage(DomainError, "не более 255"):
            services.issue(self.director, self.product, Decimal("1"), "x" * 256, key=key())

    def test_archived_product_cannot_move(self):
        services.archive_product(self.director, self.product)
        with self.assertRaisesMessage(DomainError, "в архиве"):
            services.receive(self.director, self.product, Decimal("1"), key=key())

    def test_roles_are_enforced_in_services_too(self):
        with self.assertRaises(PermissionDenied):
            services.receive(self.manager, self.product, Decimal("1"), key=key())
        services.receive(self.storekeeper, self.product, Decimal("1"), key=key())
        op = Operation.objects.get()
        with self.assertRaises(PermissionDenied):
            services.cancel(self.storekeeper, op)
        with self.assertRaises(PermissionDenied):
            services.create_product(self.storekeeper, name="X", unit="m", price=Decimal("1"), currency="USD")

    def test_db_constraint_is_last_line_of_defence(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=self.product.pk).update(stock=Decimal("-1"))


class IdempotencyTests(TestCase):
    def setUp(self):
        self.director = demo_user("director")
        self.product = make_product()

    def test_same_key_twice_gives_one_operation(self):
        k = key()
        first = services.receive(self.director, self.product, Decimal("10"), key=k)
        second = services.receive(self.director, self.product, Decimal("10"), key=k)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.operation.pk, second.operation.pk)
        self.assertEqual(Operation.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("10"))

    def test_key_of_another_user_is_not_reused(self):
        k = key()
        services.receive(self.director, self.product, Decimal("10"), key=k)
        with self.assertRaises(DomainError):
            services.receive(demo_user("storekeeper"), self.product, Decimal("10"), key=k)

    def test_invalid_key(self):
        for bad in [None, "", "not-a-uuid"]:
            with self.subTest(key=bad), self.assertRaises(DomainError):
                services.receive(self.director, self.product, Decimal("1"), key=bad)
        self.assertEqual(Operation.objects.count(), 0)


class CancelTests(TestCase):
    def setUp(self):
        self.director = demo_user("director")
        self.product = make_product()
        self.receipt = services.receive(self.director, self.product, Decimal("100"), key=key()).operation

    def stock(self):
        self.product.refresh_from_db()
        return self.product.stock

    def test_cancel_issue_restores_stock(self):
        out = services.issue(self.director, self.product, Decimal("30.5"), "Объект", key=key()).operation
        cancelled = services.cancel(self.director, out)
        self.assertEqual(self.stock(), Decimal("100"))
        self.assertEqual(cancelled.cancelled_by, self.director)
        self.assertIsNotNone(cancelled.cancelled_at)

    def test_cancel_twice_is_rejected(self):
        services.cancel(self.director, self.receipt)
        with self.assertRaisesMessage(DomainError, "Операция уже отменена (director,"):
            services.cancel(self.director, self.receipt)
        self.assertEqual(self.stock(), Decimal("0"))

    def test_cancel_with_stale_object_is_rejected(self):
        stale = Operation.objects.get(pk=self.receipt.pk)
        services.cancel(self.director, self.receipt)
        with self.assertRaisesMessage(DomainError, "уже отменена"):
            services.cancel(self.director, stale)

    def test_cancel_receipt_that_would_go_negative_is_rejected(self):
        services.issue(self.director, self.product, Decimal("30.5"), "Объект", key=key())
        with self.assertRaisesMessage(
            DomainError,
            "Нельзя отменить приход: из него уже выдано 30,5 м, на складе осталось только 69,5 м",
        ):
            services.cancel(self.director, self.receipt)
        self.assertEqual(self.stock(), Decimal("69.5"))
        self.receipt.refresh_from_db()
        self.assertFalse(self.receipt.is_cancelled)

    def test_cancel_works_for_archived_product(self):
        out = services.issue(self.director, self.product, Decimal("100"), "Объект", key=key()).operation
        services.archive_product(self.director, self.product)
        services.cancel(self.director, out)
        self.assertEqual(self.stock(), Decimal("100"))

    def test_operations_cannot_be_deleted(self):
        with self.assertRaises(ImmutableRecordError):
            self.receipt.delete()
        with self.assertRaises(ImmutableRecordError):
            Operation.objects.all().delete()
        self.assertEqual(Operation.objects.count(), 1)


class ProductTests(TestCase):
    def setUp(self):
        self.director = demo_user("director")

    def create(self, name, unit="m", price="1.00", currency="USD"):
        return services.create_product(
            self.director, name=name, unit=unit, price=Decimal(price), currency=currency
        )

    def test_name_is_trimmed_and_unique_case_insensitive_including_cyrillic(self):
        product = self.create("  Кабель   ВВГ  ")
        self.assertEqual(product.name, "Кабель ВВГ")
        for clash in ["кабель ввг", "КАБЕЛЬ ВВГ", "Кабель  ввг"]:
            with self.subTest(name=clash), self.assertRaisesMessage(DomainError, "уже есть"):
                self.create(clash)

    def test_blank_name_rejected(self):
        with self.assertRaisesMessage(DomainError, "Укажите название"):
            self.create("   ")

    def test_archived_name_can_be_reused_but_not_restored_into_a_clash(self):
        old = self.create("Труба")
        services.archive_product(self.director, old)
        self.create("ТРУБА")
        with self.assertRaisesMessage(DomainError, "уже есть"):
            services.restore_product(self.director, old)

    def test_db_unique_constraint_backs_the_check(self):
        self.create("Провод")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(name="провод", unit="m", price=Decimal("1"), currency="USD")

    def test_unit_is_frozen_after_first_movement(self):
        product = self.create("Шуруп", unit="pcs")
        services.update_product(self.director, product, name="Шуруп", unit="m", price=Decimal("2"), currency="USD")
        services.receive(self.director, product, Decimal("5"), key=key())
        with self.assertRaisesMessage(DomainError, "Нельзя изменить единицу измерения"):
            services.update_product(self.director, product, name="Шуруп", unit="pcs", price=Decimal("2"), currency="USD")
        product.refresh_from_db()
        self.assertEqual(product.unit, "m")
        updated = services.update_product(
            self.director, product, name="Шуруп 4×40", unit="m", price=Decimal("3.50"), currency="UZS"
        )
        self.assertEqual((updated.name, updated.price, updated.currency, updated.stock), ("Шуруп 4×40", Decimal("3.50"), "UZS", Decimal("5")))

    def test_product_with_movements_cannot_be_deleted(self):
        product = self.create("Гайка", unit="pcs")
        services.receive(self.director, product, Decimal("5"), key=key())
        with self.assertRaisesMessage(DomainError, "Нельзя удалить"):
            services.delete_product(self.director, product)
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())

    def test_product_without_movements_can_be_deleted(self):
        product = self.create("Опечатка")
        services.delete_product(self.director, product)
        self.assertFalse(Product.objects.filter(pk=product.pk).exists())

    def test_archive_requires_zero_stock(self):
        product = self.create("Лента")
        services.receive(self.director, product, Decimal("2"), key=key())
        with self.assertRaisesMessage(DomainError, "Нельзя отправить в архив"):
            services.archive_product(self.director, product)

    def test_bad_price(self):
        for bad in [Decimal("-1"), Decimal("100000000000.01"), Decimal("1.001"), Decimal("NaN")]:
            with self.subTest(price=bad), self.assertRaises(DomainError):
                services.create_product(self.director, name="X", unit="m", price=bad, currency="USD")
        with self.assertRaises(DomainError):
            services.create_product(self.director, name="X", unit="kg", price=Decimal("1"), currency="USD")
        with self.assertRaises(DomainError):
            services.create_product(self.director, name="X", unit="m", price=Decimal("1"), currency="EUR")

    def test_uuid_key_objects_are_accepted(self):
        product = self.create("Ключ")
        result = services.receive(self.director, product, Decimal("1"), key=uuid.uuid4())
        self.assertTrue(result.created)
