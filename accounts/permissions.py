"""The single source of truth for who may do what. Anything not listed is denied."""

from enum import StrEnum
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from .models import Role


class Perm(StrEnum):
    VIEW_STOCK = "view_stock"
    VIEW_PRICES = "view_prices"
    MANAGE_PRODUCTS = "manage_products"
    MOVE_STOCK = "move_stock"
    CANCEL_OPERATION = "cancel_operation"
    VIEW_HISTORY = "view_history"
    EXPORT_STOCK = "export_stock"


MATRIX: dict[str, frozenset[Perm]] = {
    Role.DIRECTOR.value: frozenset(Perm),
    Role.STOREKEEPER.value: frozenset({Perm.VIEW_STOCK, Perm.MOVE_STOCK, Perm.EXPORT_STOCK}),
    Role.MANAGER.value: frozenset(
        {Perm.VIEW_STOCK, Perm.VIEW_PRICES, Perm.VIEW_HISTORY, Perm.EXPORT_STOCK}
    ),
}


def has_perm(user, perm: Perm) -> bool:
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return perm in MATRIX.get(user.role, frozenset())


def require(user, perm: Perm) -> None:
    if not has_perm(user, perm):
        raise PermissionDenied


def role_required(*perms: Perm):
    """Anonymous -> login redirect; authenticated without every perm -> 403."""
    if not perms:
        raise ValueError("role_required needs at least one permission")

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            for perm in perms:
                require(request.user, perm)
            return view(request, *args, **kwargs)

        wrapper.required_perms = perms
        return wrapper

    return decorator
