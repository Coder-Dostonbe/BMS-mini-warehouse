"""All stock-changing business rules. Views only translate HTTP <-> these calls.

Stock is never updated with F() arithmetic: SQLite keeps DECIMAL columns as REAL, so
``stock - 0.1 - 0.2`` drifts to -2.8e-17 and trips the CHECK on a legitimate "issue all".
Instead the new value is computed exactly in Decimal inside an IMMEDIATE transaction
(write lock taken at BEGIN) and written with a guarded UPDATE ... WHERE stock = <read value>.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.permissions import Perm, require

from .models import (
    COMMENT_MAX_LENGTH,
    MAX_PRICE,
    MAX_QUANTITY,
    MAX_STOCK,
    NAME_MAX_LENGTH,
    OBJECT_MAX_LENGTH,
    Currency,
    Operation,
    OperationType,
    Product,
    Unit,
    name_key,
    normalize_name,
)
from .numbers import format_quantity, is_whole

QUANTITY_QUANTUM = Decimal("0.001")
PRICE_QUANTUM = Decimal("0.01")


class DomainError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class OperationResult:
    operation: Operation
    created: bool


def format_moment(moment) -> str:
    return timezone.localtime(moment).strftime("%d.%m.%Y %H:%M")


# --- validation helpers -------------------------------------------------------


def _clean_text(value, max_length: int, *, required: bool, required_message: str = "") -> str:
    text = " ".join(str(value or "").split()) if required else str(value or "").strip()
    if required and not text:
        raise DomainError(required_message)
    if len(text) > max_length:
        raise DomainError(_("Слишком длинный текст: не более %(max)s символов") % {"max": max_length})
    return text


def _check_quantity(product: Product, quantity) -> Decimal:
    if not isinstance(quantity, Decimal):
        raise TypeError("quantity must be Decimal")
    if not quantity.is_finite() or quantity <= 0:
        raise DomainError(_("Количество должно быть больше нуля"))
    if quantity > MAX_QUANTITY:
        raise DomainError(_("Слишком большое количество"))
    if quantity != quantity.quantize(QUANTITY_QUANTUM):
        raise DomainError(_("Не более %(places)s знаков после запятой") % {"places": 3})
    if product.unit == Unit.PCS and not is_whole(quantity):
        raise DomainError(_("Для штук количество должно быть целым"))
    return quantity.quantize(QUANTITY_QUANTUM)


def _check_key(key) -> uuid.UUID:
    try:
        return key if isinstance(key, uuid.UUID) else uuid.UUID(str(key))
    except (TypeError, ValueError, AttributeError):
        raise DomainError(_("Форма устарела. Обновите страницу и повторите.")) from None


def _write_stock(product: Product, new_stock: Decimal) -> None:
    if new_stock < 0:
        raise DomainError(_("Остаток не может стать отрицательным"))
    if new_stock > MAX_STOCK:
        raise DomainError(_("Превышен максимально допустимый остаток"))
    updated = Product.objects.filter(pk=product.pk, stock=product.stock).update(
        stock=new_stock, updated_at=timezone.now()
    )
    if updated != 1:
        raise DomainError(_("Остаток изменился во время операции. Повторите."))
    product.stock = new_stock


def _existing_operation(key: uuid.UUID, user):
    operation = Operation.objects.select_related("product").filter(idempotency_key=key).first()
    if operation is not None and operation.created_by_id != user.pk:
        raise DomainError(_("Форма устарела. Обновите страницу и повторите."))
    return operation


def _record(user, key, apply) -> OperationResult:
    key = _check_key(key)
    try:
        with transaction.atomic():
            existing = _existing_operation(key, user)
            if existing is not None:
                return OperationResult(existing, created=False)
            return OperationResult(apply(key), created=True)
    except IntegrityError:
        # A concurrent request with the same key won the race: show its result.
        existing = _existing_operation(key, user)
        if existing is not None:
            return OperationResult(existing, created=False)
        raise DomainError(_("Операция не сохранена: данные изменились. Обновите страницу и повторите.")) from None


def _fresh_active_product(product: Product) -> Product:
    try:
        fresh = Product.objects.get(pk=product.pk)
    except Product.DoesNotExist:
        raise DomainError(_("Товар не найден")) from None
    if fresh.is_archived:
        raise DomainError(_("Товар «%(name)s» в архиве") % {"name": fresh.name})
    return fresh


# --- stock movements ----------------------------------------------------------


def receive(user, product: Product, quantity: Decimal, comment: str = "", key=None) -> OperationResult:
    require(user, Perm.MOVE_STOCK)
    comment = _clean_text(comment, COMMENT_MAX_LENGTH, required=False)

    def apply(op_key):
        fresh = _fresh_active_product(product)
        qty = _check_quantity(fresh, quantity)
        _write_stock(fresh, fresh.stock + qty)
        return Operation.objects.create(
            type=OperationType.IN,
            product=fresh,
            quantity=qty,
            comment=comment,
            created_by=user,
            idempotency_key=op_key,
        )

    return _record(user, key, apply)


def issue(
    user, product: Product, quantity: Decimal, object_name: str, comment: str = "", key=None
) -> OperationResult:
    require(user, Perm.MOVE_STOCK)
    object_name = _clean_text(
        object_name, OBJECT_MAX_LENGTH, required=True, required_message=_("Укажите объект")
    )
    comment = _clean_text(comment, COMMENT_MAX_LENGTH, required=False)

    def apply(op_key):
        fresh = _fresh_active_product(product)
        qty = _check_quantity(fresh, quantity)
        if fresh.stock < qty:
            raise DomainError(
                _("На складе только %(available)s")
                % {"available": format_quantity(fresh.stock, fresh.unit)}
            )
        _write_stock(fresh, fresh.stock - qty)
        return Operation.objects.create(
            type=OperationType.OUT,
            product=fresh,
            quantity=qty,
            object_name=object_name,
            comment=comment,
            created_by=user,
            idempotency_key=op_key,
        )

    return _record(user, key, apply)


def _already_cancelled(operation: Operation) -> DomainError:
    return DomainError(
        _("Операция уже отменена (%(user)s, %(when)s)")
        % {"user": operation.cancelled_by.get_username(), "when": format_moment(operation.cancelled_at)}
    )


def cancel(user, operation: Operation) -> Operation:
    require(user, Perm.CANCEL_OPERATION)
    with transaction.atomic():
        try:
            op = Operation.objects.select_related("product", "cancelled_by").get(pk=operation.pk)
        except Operation.DoesNotExist:
            raise DomainError(_("Операция не найдена")) from None
        if op.is_cancelled:
            raise _already_cancelled(op)

        product = op.product
        if op.type == OperationType.IN:
            new_stock = product.stock - op.quantity
            if new_stock < 0:
                raise DomainError(
                    _("Нельзя отменить приход: из него уже выдано %(issued)s, на складе осталось только %(left)s")
                    % {
                        "issued": format_quantity(op.quantity - product.stock, product.unit),
                        "left": format_quantity(product.stock, product.unit),
                    }
                )
        else:
            new_stock = product.stock + op.quantity

        now = timezone.now()
        claimed = Operation.objects.filter(pk=op.pk, cancelled_at__isnull=True).update(
            cancelled_at=now, cancelled_by=user
        )
        if claimed != 1:
            op.refresh_from_db()
            raise _already_cancelled(op)
        _write_stock(product, new_stock)

    op.refresh_from_db()
    return op


# --- product catalogue --------------------------------------------------------


def _check_product_fields(name, unit, price, currency) -> tuple[str, Decimal]:
    clean_name = normalize_name(name or "")
    if not clean_name:
        raise DomainError(_("Укажите название"))
    if len(clean_name) > NAME_MAX_LENGTH:
        raise DomainError(_("Слишком длинный текст: не более %(max)s символов") % {"max": NAME_MAX_LENGTH})
    if unit not in Unit.values:
        raise DomainError(_("Неизвестная единица измерения"))
    if currency not in Currency.values:
        raise DomainError(_("Неизвестная валюта"))
    if not isinstance(price, Decimal):
        raise TypeError("price must be Decimal")
    if not price.is_finite() or price < 0 or price > MAX_PRICE or price != price.quantize(PRICE_QUANTUM):
        raise DomainError(_("Некорректная цена"))
    return clean_name, price.quantize(PRICE_QUANTUM)


def _ensure_unique_active_name(name: str, exclude_pk=None) -> None:
    clash = Product.objects.filter(name_key=name_key(name), is_archived=False)
    if exclude_pk is not None:
        clash = clash.exclude(pk=exclude_pk)
    if clash.exists():
        raise DomainError(_("Товар с таким названием уже есть"))


def _save_product(product: Product) -> Product:
    try:
        with transaction.atomic():
            product.save()
    except IntegrityError:
        raise DomainError(_("Товар с таким названием уже есть")) from None
    return product


def create_product(user, *, name, unit, price, currency) -> Product:
    require(user, Perm.MANAGE_PRODUCTS)
    clean_name, clean_price = _check_product_fields(name, unit, price, currency)
    _ensure_unique_active_name(clean_name)
    return _save_product(Product(name=clean_name, unit=unit, price=clean_price, currency=currency))


def update_product(user, product: Product, *, name, unit, price, currency) -> Product:
    require(user, Perm.MANAGE_PRODUCTS)
    clean_name, clean_price = _check_product_fields(name, unit, price, currency)
    with transaction.atomic():
        fresh = Product.objects.get(pk=product.pk)
        if unit != fresh.unit and fresh.operations.exists():
            raise DomainError(_("Нельзя изменить единицу измерения: по товару уже есть операции"))
        if not fresh.is_archived:
            _ensure_unique_active_name(clean_name, exclude_pk=fresh.pk)
        fresh.name, fresh.unit, fresh.price, fresh.currency = clean_name, unit, clean_price, currency
        # stock is deliberately not in update_fields: only movements change it.
        try:
            with transaction.atomic():
                fresh.save(update_fields=["name", "unit", "price", "currency", "updated_at"])
        except IntegrityError:
            raise DomainError(_("Товар с таким названием уже есть")) from None
    return fresh


def archive_product(user, product: Product) -> Product:
    require(user, Perm.MANAGE_PRODUCTS)
    with transaction.atomic():
        fresh = Product.objects.get(pk=product.pk)
        if fresh.is_archived:
            return fresh
        if fresh.stock != 0:
            raise DomainError(
                _("Нельзя отправить в архив товар с остатком %(qty)s. Сначала выдайте остаток.")
                % {"qty": format_quantity(fresh.stock, fresh.unit)}
            )
        fresh.is_archived = True
        fresh.save(update_fields=["is_archived", "updated_at"])
    return fresh


def restore_product(user, product: Product) -> Product:
    require(user, Perm.MANAGE_PRODUCTS)
    with transaction.atomic():
        fresh = Product.objects.get(pk=product.pk)
        if not fresh.is_archived:
            return fresh
        _ensure_unique_active_name(fresh.name, exclude_pk=fresh.pk)
        fresh.is_archived = False
        try:
            with transaction.atomic():
                fresh.save(update_fields=["is_archived", "updated_at"])
        except IntegrityError:
            raise DomainError(_("Товар с таким названием уже есть")) from None
    return fresh


def delete_product(user, product: Product) -> None:
    require(user, Perm.MANAGE_PRODUCTS)
    message = _("Нельзя удалить товар: по нему есть операции. Отправьте его в архив.")
    with transaction.atomic():
        fresh = Product.objects.get(pk=product.pk)
        if fresh.operations.exists():
            raise DomainError(message)
        try:
            fresh.delete()
        except ProtectedError:
            raise DomainError(message) from None
