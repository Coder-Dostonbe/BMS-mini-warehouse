from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.shortcuts import redirect, render, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from . import throttle
from .forms import LoginForm


def _safe_next(request) -> str:
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return ""


def _canonical_username(typed: str) -> str:
    """Case-insensitive login name; an exact match wins if two names differ only by case."""
    matches = list(
        get_user_model().objects.filter(username__iexact=typed).values_list("username", flat=True)[:2]
    )
    if typed in matches or len(matches) != 1:
        return typed
    return matches[0]


def _authenticate(request, username: str, password: str):
    # Passwords stay case-sensitive; stray spaces at the ends (copy-paste) are forgiven.
    username = _canonical_username(username)
    user = authenticate(request, username=username, password=password)
    if user is None and password.strip() != password:
        user = authenticate(request, username=username, password=password.strip())
    return user


@sensitive_post_parameters("password")
@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request):
    next_url = _safe_next(request)
    if request.user.is_authenticated:
        return redirect(next_url or resolve_url(settings.LOGIN_REDIRECT_URL))

    error = ""
    form = LoginForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        username = form.cleaned_data["username"]
        ip = request.META.get("REMOTE_ADDR", "")
        if throttle.is_blocked(username, ip):
            error = _("Слишком много неудачных попыток. Повторите через 5 минут.")
        else:
            user = _authenticate(request, username, form.cleaned_data["password"])
            if user is None:
                throttle.register_failure(username, ip)
                error = _("Неверный логин или пароль")
            else:
                throttle.reset(username, ip)
                login(request, user)
                return redirect(next_url or resolve_url(settings.LOGIN_REDIRECT_URL))

    return render(
        request, "accounts/login.html", {"form": form, "error": error, "next": next_url}
    )


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")
