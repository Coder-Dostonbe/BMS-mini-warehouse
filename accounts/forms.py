from django import forms
from django.utils.translation import gettext_lazy as _


class LoginForm(forms.Form):
    username = forms.CharField(
        label=_("Логин"),
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "username", "autocapitalize": "none"}),
    )
    password = forms.CharField(
        label=_("Пароль"),
        max_length=256,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
