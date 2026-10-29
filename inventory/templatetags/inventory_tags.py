from django import template

from inventory import numbers

register = template.Library()


@register.filter
def qty(value, unit):
    return numbers.format_quantity(value, unit)


@register.filter
def money(value, currency):
    return numbers.format_money(value, currency)


@register.filter
def unit_label(unit):
    return numbers.unit_label(unit)
