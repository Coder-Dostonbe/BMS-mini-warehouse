import threading
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase

from inventory import services
from inventory.models import Operation, Product
from inventory.services import DomainError


def run_in_threads(count, target):
    """Starts `count` threads at the same moment; returns each one's result or exception."""
    barrier = threading.Barrier(count)
    results = [None] * count

    def worker(index):
        try:
            barrier.wait()
            results[index] = target(index)
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            results[index] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


class ConcurrencyTests(TransactionTestCase):
    """Real parallel writers against a file SQLite DB (see DATABASES TEST NAME)."""

    def setUp(self):
        self.user = get_user_model().objects.create(username="boss", role="director")
        self.product = Product.objects.create(name="Кабель", unit="m", price=Decimal("1"), currency="USD")
        services.receive(self.user, self.product, Decimal("10"), key=uuid.uuid4())

    def test_parallel_issues_cannot_overdraw(self):
        results = run_in_threads(
            4, lambda i: services.issue(self.user, self.product, Decimal("10"), f"Объект {i}", key=uuid.uuid4())
        )
        successes = [r for r in results if isinstance(r, services.OperationResult)]
        failures = [r for r in results if isinstance(r, DomainError)]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(len(failures), 3, results)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("0"))

    def test_parallel_double_submit_creates_one_operation(self):
        shared_key = uuid.uuid4()
        results = run_in_threads(
            4, lambda i: services.receive(self.user, self.product, Decimal("5"), key=shared_key)
        )
        self.assertTrue(all(isinstance(r, services.OperationResult) for r in results), results)
        self.assertEqual(sum(r.created for r in results), 1)
        self.assertEqual(Operation.objects.filter(idempotency_key=shared_key).count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("15"))

    def test_parallel_cancel_happens_once(self):
        out = services.issue(self.user, self.product, Decimal("4"), "Объект", key=uuid.uuid4()).operation
        results = run_in_threads(4, lambda i: services.cancel(self.user, out))
        self.assertEqual(sum(isinstance(r, Operation) for r in results), 1, results)
        self.assertEqual(sum(isinstance(r, DomainError) for r in results), 3, results)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("10"))
