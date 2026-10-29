from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

MAX_PRICE = Decimal("100000000000")  # 10^11
MAX_QUANTITY = Decimal("1000000000")  # one operation
MAX_STOCK = Decimal("99999999999.999")  # what DecimalField(14, 3) can hold
NAME_MAX_LENGTH = 200
OBJECT_MAX_LENGTH = 255
COMMENT_MAX_LENGTH = 500


class Unit(models.TextChoices):
    PCS = "pcs", _("Штуки (шт)")
    M = "m", _("Метры (м)")


class Currency(models.TextChoices):
    UZS = "UZS", "UZS"
    USD = "USD", "USD"
    CNY = "CNY", "CNY"


class OperationType(models.TextChoices):
    IN = "in", _("Приход")
    OUT = "out", _("Выдача")


def normalize_name(value: str) -> str:
    return " ".join(str(value).split())


def name_key(value: str) -> str:
    # SQLite LIKE/icontains only folds ASCII case, so search/uniqueness use a casefolded copy.
    return normalize_name(value).casefold()


class Product(models.Model):
    name = models.CharField(max_length=NAME_MAX_LENGTH)
    name_key = models.CharField(max_length=NAME_MAX_LENGTH * 3, editable=False, db_index=True)
    unit = models.CharField(max_length=3, choices=Unit.choices)
    price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(MAX_PRICE)],
    )
    currency = models.CharField(max_length=3, choices=Currency.choices)
    stock = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"), editable=False)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name_key", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(stock__gte=0), name="product_stock_non_negative"),
            models.CheckConstraint(condition=Q(price__gte=0), name="product_price_non_negative"),
            models.UniqueConstraint(
                fields=["name_key"],
                condition=Q(is_archived=False),
                name="product_unique_active_name",
            ),
        ]

    def save(self, *args, **kwargs):
        self.name = normalize_name(self.name)
        self.name_key = name_key(self.name)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "name" in update_fields:
            kwargs["update_fields"] = {*update_fields, "name_key"}
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ImmutableRecordError(Exception):
    """Operations form an append-only journal; corrections are made by cancelling."""


class OperationQuerySet(models.QuerySet):
    def delete(self):
        raise ImmutableRecordError("Operations cannot be deleted; cancel them instead.")


class Operation(models.Model):
    type = models.CharField(max_length=3, choices=OperationType.choices)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="operations")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    object_name = models.CharField(max_length=OBJECT_MAX_LENGTH, blank=True, default="")
    comment = models.CharField(max_length=COMMENT_MAX_LENGTH, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="operations"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cancelled_operations",
        null=True,
        blank=True,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    idempotency_key = models.UUIDField(unique=True)

    objects = OperationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="operation_quantity_positive"),
            models.CheckConstraint(condition=Q(type__in=["in", "out"]), name="operation_type_valid"),
            models.CheckConstraint(
                condition=Q(type="in") | ~Q(object_name=""), name="operation_out_has_object"
            ),
            models.CheckConstraint(
                condition=Q(cancelled_at__isnull=True, cancelled_by__isnull=True)
                | Q(cancelled_at__isnull=False, cancelled_by__isnull=False),
                name="operation_cancel_fields_together",
            ),
        ]

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("Operations cannot be deleted; cancel them instead.")

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None

    def __str__(self):
        return f"{self.get_type_display()} #{self.pk}"
