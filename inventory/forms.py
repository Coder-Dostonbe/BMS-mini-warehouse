from django import forms
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .models import (
    COMMENT_MAX_LENGTH,
    MAX_PRICE,
    MAX_QUANTITY,
    NAME_MAX_LENGTH,
    OBJECT_MAX_LENGTH,
    Currency,
    OperationType,
    Product,
    Unit,
)
from .numbers import MAX_INPUT_LENGTH, NumberError, format_quantity, is_whole, parse_decimal, unit_label

STALE_FORM = gettext_lazy("Форма устарела. Обновите страницу и повторите.")


class DecimalTextField(forms.Field):
    """A text input (not type=number) so "12,5" works; parsed strictly into Decimal."""

    widget = forms.TextInput

    def __init__(self, *, places, max_value, allow_zero=False, inputmode="decimal", **kwargs):
        self.places = places
        self.max_value = max_value
        self.allow_zero = allow_zero
        super().__init__(**kwargs)
        self.widget.attrs.update(
            {"inputmode": inputmode, "autocomplete": "off", "maxlength": str(MAX_INPUT_LENGTH)}
        )

    def to_python(self, value):
        if value is None or not str(value).strip():
            return None
        try:
            return parse_decimal(
                value, places=self.places, max_value=self.max_value, allow_zero=self.allow_zero
            )
        except NumberError as exc:
            raise forms.ValidationError(exc.message, code="invalid") from None


class ProductSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        product = getattr(value, "instance", None)
        if product is not None:
            option["attrs"]["data-unit"] = product.unit
            option["attrs"]["data-hint"] = _("Единица: %(unit)s, на складе: %(stock)s") % {
                "unit": unit_label(product.unit),
                "stock": format_quantity(product.stock, product.unit),
            }
        return option


class ProductChoiceField(forms.ModelChoiceField):
    widget = ProductSelect

    def label_from_instance(self, obj):
        return f"{obj.name} — {format_quantity(obj.stock, obj.unit)}"


def active_products_for_choice():
    # Only what the select shows: no price ever reaches the form, whoever the user is.
    return Product.objects.filter(is_archived=False).only("id", "name", "unit", "stock")


class MovementForm(forms.Form):
    product = ProductChoiceField(
        label=gettext_lazy("Товар"),
        queryset=Product.objects.none(),
        empty_label=gettext_lazy("— выберите товар —"),
    )
    quantity = DecimalTextField(label=gettext_lazy("Количество"), places=3, max_value=MAX_QUANTITY)
    comment = forms.CharField(
        label=gettext_lazy("Комментарий"),
        required=False,
        max_length=COMMENT_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    idempotency_key = forms.UUIDField(
        widget=forms.HiddenInput,
        error_messages={"required": STALE_FORM, "invalid": STALE_FORM},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = active_products_for_choice()
        self.fields["product"].widget.attrs.update(
            {"data-quantity-input": "id_quantity", "data-quantity-hint": "quantity-hint"}
        )

    def clean(self):
        cleaned = super().clean()
        if "idempotency_key" in self._errors:
            del self._errors["idempotency_key"]
            self.add_error(None, STALE_FORM)
        product, quantity = cleaned.get("product"), cleaned.get("quantity")
        if product is not None and quantity is not None and product.unit == Unit.PCS and not is_whole(quantity):
            self.add_error("quantity", _("Для штук количество должно быть целым"))
        return cleaned


class ReceiveForm(MovementForm):
    operation_type = OperationType.IN


class IssueForm(MovementForm):
    operation_type = OperationType.OUT
    object_name = forms.CharField(
        label=gettext_lazy("Объект"),
        max_length=OBJECT_MAX_LENGTH,
        help_text=gettext_lazy("Куда выдаётся товар: объект, бригада или заказ"),
    )
    field_order = ["product", "quantity", "object_name", "comment"]


class ProductForm(forms.Form):
    name = forms.CharField(label=gettext_lazy("Название"), max_length=NAME_MAX_LENGTH)
    unit = forms.ChoiceField(label=gettext_lazy("Единица измерения"), choices=Unit.choices)
    price = DecimalTextField(
        label=gettext_lazy("Цена за единицу"), places=2, max_value=MAX_PRICE, allow_zero=True
    )
    currency = forms.ChoiceField(label=gettext_lazy("Валюта"), choices=Currency.choices)

    def __init__(self, *args, unit_locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        if unit_locked:
            self.fields["unit"].disabled = True
            self.fields["unit"].help_text = _("Нельзя изменить: по товару уже есть операции")


class HistoryProductField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return _("%(name)s (в архиве)") % {"name": obj.name} if obj.is_archived else obj.name


class HistoryFilterForm(forms.Form):
    product = HistoryProductField(
        label=gettext_lazy("Товар"),
        queryset=Product.objects.only("id", "name", "is_archived"),
        required=False,
        empty_label=gettext_lazy("Все товары"),
    )
    type = forms.ChoiceField(
        label=gettext_lazy("Тип"),
        required=False,
        choices=[("", gettext_lazy("Все операции")), *OperationType.choices],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["aria-label"] = field.label
